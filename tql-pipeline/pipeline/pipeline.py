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


def _save_scored(conn, dom, verdict, p, reasons, features, model_version):
    conn.execute("UPDATE prospects SET verdict=?, score=?, reasons=?, features=?, model_version=? WHERE domain=?",
                 (verdict, p, json.dumps(reasons), json.dumps(features), model_version, dom))


def research_one(conn, dom, icp, policy, fetcher):
    row = get_prospect(conn, dom)
    if domain_matches(dom, policy.get("blocked_domains", [])):
        conn.execute("UPDATE prospects SET status='suppressed', filter_reason=? WHERE domain=?",
                     ("blocked domain — research by hand", dom))
        conn.commit()
        return "blocked"
    try:
        pages = fetcher.crawl(row["url"])
    except Exception as e:  # dead site, DNS, robots — record it and move on
        conn.execute("UPDATE prospects SET status='error', filter_reason=?, researched_at=? WHERE domain=?",
                     (f"fetch failed: {type(e).__name__}: {str(e)[:150]}", now_iso(), dom))
        conn.commit()
        return "error"
    research, features, text = extract(pages, icp["signals"], active_terms(conn))
    model = current_model(conn, icp)
    verdict, p, reasons = score(features, research, model, icp, policy)
    conn.execute("UPDATE prospects SET research=?, page_text=?, researched_at=?, status='researched', name=COALESCE(name, ?)"
                 " WHERE domain=?", (json.dumps(research), text[:60000], now_iso(), research.get("name") or None, dom))
    _save_scored(conn, dom, verdict, p, reasons, features, model["version"])
    conn.commit()
    return verdict


def research_pending(conn, icp, policy=None, fetcher=None, limit=None, retry_errors=False, log=print):
    policy = policy or load_policy()
    fetcher = fetcher or Fetcher(policy)
    statuses = ("new", "error") if retry_errors else ("new",)
    q = f"SELECT domain FROM prospects WHERE status IN ({','.join('?' * len(statuses))}) ORDER BY added_at"
    doms = [r["domain"] for r in conn.execute(q, statuses)]
    if limit:
        doms = doms[:limit]
    tally = {}
    for i, dom in enumerate(doms, 1):
        result = research_one(conn, dom, icp, policy, fetcher)
        tally[result] = tally.get(result, 0) + 1
        log(f"  [{i}/{len(doms)}] {dom}: {result}")
    return tally


def rescore_all(conn, icp, policy=None, quiet=False):
    """Re-run every researched prospect through the newest model + learned terms. No re-crawl."""
    policy = policy or load_policy()
    model = current_model(conn, icp)
    terms = active_terms(conn)
    n = 0
    for r in conn.execute("SELECT * FROM prospects WHERE page_text IS NOT NULL").fetchall():
        features = {k: v for k, v in jload(r["features"], {}).items() if not k.startswith("term:")}
        fresh, _ = pattern_features(r["page_text"], icp["signals"], terms)
        features.update(fresh)
        verdict, p, reasons = score(features, jload(r["research"], {}), model, icp, policy)
        _save_scored(conn, r["domain"], verdict, p, reasons, features, model["version"])
        n += 1
    conn.commit()
    if not quiet:
        print(f"Rescored {n} prospects with model v{model['version']}.")
    return n


def apply_filters(conn, policy=None):
    """Move researched prospects into queued / skipped / suppressed. Runs every cycle."""
    policy = policy or load_policy()
    suppression = load_suppression()
    counts = {}
    rows = conn.execute("SELECT * FROM prospects WHERE status NOT IN ('new','error')").fetchall()
    for r in rows:
        reason = filter_reason(r, policy, suppression)
        if reason:
            status = "won" if r["last_outcome"] == "load_booked" else \
                     "contacted" if (r["last_outcome"] in ACTIVE or reason.startswith("cooldown")) else "suppressed"
        elif r["verdict"] == "SKIP":
            status, reason = "skipped", "scored SKIP"
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
