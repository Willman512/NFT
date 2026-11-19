"""Orchestration: ingest -> research -> score -> filter -> queue, plus outcome logging."""
import csv
import json
import sys

from .config import load_policy, load_suppression
from .db import active_terms, get_prospect, jload
from .extract import extract, pattern_features
from .fetch import Fetcher
from .filters import ACTIVE, PERMANENT, filter_reason
from .learn import LABELS
from .score import current_model, score
from .util import domain_matches, normalize_domain, normalize_url, now_iso

OUTCOMES = sorted(set(LABELS) | set(PERMANENT) | ACTIVE | {"contacted"})
CONTACT_IMPLIED = ACTIVE | {"no_response", "not_interested", "bad_fit", "do_not_contact"}


def add_lead(conn, url, name="", source="manual", notes=""):
    url = normalize_url(url)
    dom = normalize_domain(url)
    if not dom:
        return None, "empty"
    if get_prospect(conn, dom):
        return dom, "duplicate"
    conn.execute("INSERT INTO prospects(domain, url, name, source, notes, added_at, status) VALUES (?,?,?,?,?,?, 'new')",
                 (dom, url, name or None, source, notes or None, now_iso()))
    conn.commit()
    return dom, "added"


def add_from_csv(conn, path, source=None):
    """CSV needs a url/website/domain column; name/company, source, notes optional."""
    added = dupes = 0
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            url = row.get("url") or row.get("website") or row.get("domain") or row.get("site")
            if not url:
                continue
            _, status = add_lead(conn, url, row.get("name") or row.get("company", ""),
                                 source or row.get("source") or path, row.get("notes", ""))
            added += status == "added"
            dupes += status == "duplicate"
    return added, dupes


def lookalike(conn, prof, features, research, meta):
    """Set the `lookalike` feature from the customer profile; return a reason line (or None)."""
    from . import profile
    if not prof or not prof.get("active"):
        features.pop("lookalike", None)
        return None
    row = {"features": json.dumps(features), "meta": json.dumps(meta), "research": json.dumps(research)}
    s, top = profile.score(prof, profile.traits(row))
    features["lookalike"] = 1 if s >= prof["threshold"] else 0
    if features["lookalike"]:
        from .config import load_icp
        icp = load_icp()
        return (f"+ Looks like other reps' active customers ({prof['n_models']} models): "
                + ", ".join(profile.label(t, icp) for t in top))
    return None


def _save_scored(conn, dom, verdict, p, reasons, features, model_version):
    conn.execute("UPDATE prospects SET verdict=?, score=?, reasons=?, features=?, model_version=? WHERE domain=?",
                 (verdict, p, json.dumps(reasons), json.dumps(features), model_version, dom))


def merge_meta(research, features, meta):
    """Government data beats website guesswork: EPA plant list and industry code."""
    if not meta:
        return
    plants = meta.get("plants", [])
    for p in plants:
        city = p.split(",")[0]
        if not any(loc.startswith(city + ",") for loc in research.get("us_locations", [])):
            research.setdefault("us_locations", []).append(p)
    research["epa_plants"] = len(plants)
    if meta.get("phone") and meta["phone"] not in research.setdefault("phones", []):
        research["phones"].insert(0, meta["phone"])
    research["naics"] = meta.get("naics", [])
    n = len(research.get("us_locations", [])) + len(research.get("ca_locations", []))
    if n >= 2:
        features["multi_location"] = 1
    features["many_locations"] = 1 if n >= 5 else features.get("many_locations", 0)
    for prof in meta.get("profile", []):
        features[f"naics_{prof}"] = 1
    if meta.get("profile"):
        features["no_na_presence"] = 0  # EPA-registered US plant
        features["contact_info_missing"] = 0 if research.get("phones") else features.get("contact_info_missing", 0)


def _store_research(conn, dom, pages, error, icp, policy):
    if error is not None:  # dead site, DNS, robots — record it and move on
        conn.execute("UPDATE prospects SET status='error', filter_reason=?, researched_at=? WHERE domain=?",
                     (f"fetch failed: {type(error).__name__}: {str(error)[:150]}", now_iso(), dom))
        conn.commit()
        return "error"
    research, features, text = extract(pages, icp["signals"], active_terms(conn))
    meta = jload(get_prospect(conn, dom)["meta"], {})
    merge_meta(research, features, meta)
    from . import profile
    why = lookalike(conn, profile.load(conn), features, research, meta)
    model = current_model(conn, icp)
    verdict, p, reasons = score(features, research, model, icp, policy)
    if why:
        reasons.insert(0, why)
    conn.execute("UPDATE prospects SET research=?, page_text=?, researched_at=?, status='researched', filter_reason=NULL,"
                 " name=COALESCE(name, ?) WHERE domain=?",
                 (json.dumps(research), text[:60000], now_iso(), research.get("name") or None, dom))
    _save_scored(conn, dom, verdict, p, reasons, features, model["version"])
    gate_one(conn, get_prospect(conn, dom), policy=policy)
    conn.commit()
    apply_filters(conn, policy, dom)
    return verdict


def _crawl(url, fetcher):
    try:
        return fetcher.crawl(url), None
    except Exception as e:
        return None, e


def _blocked(conn, dom, policy):
    if domain_matches(dom, policy.get("blocked_domains", [])):
        conn.execute("UPDATE prospects SET status='suppressed', filter_reason=? WHERE domain=?",
                     ("blocked domain — research by hand", dom))
        conn.commit()
        return True
    return False


def research_one(conn, dom, icp, policy, fetcher):
    if _blocked(conn, dom, policy):
        return "blocked"
    pages, err = _crawl(get_prospect(conn, dom)["url"], fetcher)
    return _store_research(conn, dom, pages, err, icp, policy)


def research_pending(conn, icp, policy=None, fetcher=None, limit=None, retry_errors=False, log=print, workers=None):
    """Crawl many sites in parallel (each site is still crawled politely, one page at a time)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    policy = policy or load_policy()
    workers = workers or int(policy.get("parallel_sites", 6))
    statuses = ("new", "error") if retry_errors else ("new",)
    q = f"SELECT domain, url FROM prospects WHERE status IN ({','.join('?' * len(statuses))}) ORDER BY added_at"
    rows = [(r["domain"], r["url"]) for r in conn.execute(q, statuses)]
    if limit:
        rows = rows[:limit]
    tally = {}
    todo = []
    for dom, url in rows:
        if _blocked(conn, dom, policy):
            tally["blocked"] = tally.get("blocked", 0) + 1
        else:
            todo.append((dom, url))
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futs = {pool.submit(_crawl, url, fetcher or Fetcher(policy)): dom for dom, url in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            dom = futs[fut]
            pages, err = fut.result()
            result = _store_research(conn, dom, pages, err, icp, policy)
            tally[result] = tally.get(result, 0) + 1
            log(f"  [{i}/{len(todo)}] {dom}: {result}")
    return tally


def rescore_all(conn, icp, policy=None, quiet=False):
    """Re-run every researched prospect through the newest model + learned terms. No re-crawl."""
    policy = policy or load_policy()
    model = current_model(conn, icp)
    terms = active_terms(conn)
    from . import profile
    prof = profile.load(conn)
    n = 0
    for r in conn.execute("SELECT * FROM prospects WHERE page_text IS NOT NULL").fetchall():
        features = {k: v for k, v in jload(r["features"], {}).items() if not k.startswith("term:")}
        fresh, _ = pattern_features(r["page_text"], icp["signals"], terms)
        features.update(fresh)
        why = lookalike(conn, prof, features, jload(r["research"], {}), jload(r["meta"], {}))
        verdict, p, reasons = score(features, jload(r["research"], {}), model, icp, policy)
        if why:
            reasons.insert(0, why)
        _save_scored(conn, r["domain"], verdict, p, reasons, features, model["version"])
        n += 1
    conn.commit()
    if not quiet:
        print(f"Rescored {n} prospects with model v{model['version']}.")
    return n


def gate_one(conn, row, icp=None, policy=None):
    """Run the three hard requirements (own fleet / interstate / outside carriers) on one researched lead."""
    from .qualify import evaluate
    policy = policy or load_policy()
    g = evaluate(conn, row, row["page_text"] or "", jload(row["research"], {}), jload(row["features"], {}),
                 jload(row["meta"], {}), policy)
    conn.execute("UPDATE prospects SET gates=? WHERE domain=?", (json.dumps(g), row["domain"]))
    return g


def gate_all(conn, policy=None, fetch_fmcsa=True, log=print):
    """Make sure FMCSA fleet data covers every lead's states, then evaluate every researched lead."""
    from .qualify import _states
    policy = policy or load_policy()
    rows = conn.execute("SELECT * FROM prospects WHERE page_text IS NOT NULL AND source != 'prospecting-desk'").fetchall()
    if fetch_fmcsa:
        from . import fmcsa
        states = set()
        for r in rows:
            states |= _states(jload(r["research"], {}))
            states |= {p.split(", ")[1][:2] for p in jload(r["meta"], {}).get("plants", []) if ", " in p}
        try:
            dots = [jload(r["meta"], {}).get("dot") for r in rows]
            if any(dots):
                fmcsa.trucks_by_dot(conn, dots)
            fmcsa.ensure_states(conn, states, log=log)
        except Exception as e:  # network trouble: gates stay "unknown" and those leads are held, never let through
            log(f"  FMCSA download failed ({type(e).__name__}: {e}). Unchecked leads stay held.")
    tally = {"qualified": 0, "held": 0}
    for r in rows:
        g = gate_one(conn, r, policy=policy)
        tally["qualified" if g["qualified"] else "held"] += 1
    conn.commit()
    return tally


def apply_filters(conn, policy=None, domain=None):
    """Move researched prospects into queued / skipped / suppressed. Runs every cycle, and on each lead
    as soon as its research finishes (so an interrupted run never strands finished work)."""
    policy = policy or load_policy()
    suppression = load_suppression()
    counts = {}
    q = "SELECT * FROM prospects WHERE status NOT IN ('new','error')" + (" AND domain=?" if domain else "")
    rows = conn.execute(q, (domain,) if domain else ()).fetchall()
    for r in rows:
        reason = filter_reason(r, policy, suppression)
        if reason:
            status = "won" if r["last_outcome"] == "load_booked" else \
                     "contacted" if (r["last_outcome"] in ACTIVE or reason.startswith("cooldown")) else "suppressed"
        elif r["verdict"] == "SKIP":
            status, reason = "skipped", "scored SKIP"
        elif policy.get("require_gates", True) and r["source"] != "prospecting-desk" \
                and not jload(r["gates"], {}).get("qualified"):
            g = jload(r["gates"], {})
            fails = [x for x in g.get("reasons", []) if ": PASS" not in x]
            status, reason = "held", ("; ".join(fails) if fails else "requirements not checked yet")
        elif r["verdict"] in ("GO", "MAYBE"):
            status = "queued"
        else:
            status = r["status"]
        conn.execute("UPDATE prospects SET status=?, filter_reason=? WHERE domain=?", (status, reason, r["domain"]))
        counts[status] = counts.get(status, 0) + 1
    conn.commit()
    return counts


def record_outcome(conn, dom, outcome, note=""):
    dom = normalize_domain(dom)
    if outcome not in OUTCOMES:
        sys.exit(f"Unknown outcome '{outcome}'. Use one of: {', '.join(OUTCOMES)}")
    row = get_prospect(conn, dom)
    if not row:
        sys.exit(f"{dom} is not in the pipeline. Add it first.")
    at = now_iso()
    conn.execute("INSERT INTO outcomes(domain, outcome, at, note, verdict, score, model_version) VALUES (?,?,?,?,?,?,?)",
                 (dom, outcome, at, note, row["verdict"], row["score"], row["model_version"]))
    if outcome == "contacted":
        conn.execute("UPDATE prospects SET contacted_at=?, touches=touches+1 WHERE domain=?", (at, dom))
    else:
        conn.execute("UPDATE prospects SET last_outcome=?, last_outcome_at=?, contacted_at=COALESCE(contacted_at, ?)"
                     " WHERE domain=?", (outcome, at, at if outcome in CONTACT_IMPLIED else None, dom))
    if outcome == "do_not_contact":
        _append_suppression(dom, note or "asked not to be contacted")
    conn.commit()
    apply_filters(conn, None, dom)  # off the call list immediately, whichever path logged it


def _append_suppression(dom, reason):
    from .config import CONFIG_DIR
    path = CONFIG_DIR / "suppression.csv"
    existing = load_suppression()
    if dom in existing:
        return
    with open(path, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([dom, reason, now_iso()[:10]])


def queue(conn, verdict=None, limit=50):
    q = "SELECT * FROM prospects WHERE status='queued'"
    args = []
    if verdict:
        q += " AND verdict=?"
        args.append(verdict.upper())
    q += " ORDER BY CASE verdict WHEN 'GO' THEN 0 ELSE 1 END, score DESC LIMIT ?"
    args.append(limit)
    return conn.execute(q, args).fetchall()
