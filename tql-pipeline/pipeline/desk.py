"""Bridge to Prospecting Desk (Will's browser app).

Pipeline -> Desk: researched leads as a Desk backup file ({"app": "prospecting-desk", "companies": [...]}) that
Settings > "Restore from backup" merges in. Every lead arrives with rubric ratings, trigger, freight profile,
contacts, opener, questions and sources. Companies already in the Desk (by id, name, phone or website) and
leads exported before are never sent again, so a re-import can't overwrite your logged calls.

Desk -> Pipeline: read a Desk backup and turn its stages and logged calls into pipeline outcomes, so `learn`
and the customer plan run on your real results.
"""
import json
import re
import zipfile
from pathlib import Path

from .db import jload
from .email import call_opener, context
from .report import ranked_contacts
from .util import normalize_domain, now_iso, today

# ---- Desk-compatible normalizers (same rules as PC.nName / nPhone / nDomain in core.js)
_NAME_STOP = {"inc", "llc", "co", "corp", "company", "corporation", "ltd", "the", "group", "and", "incorporated"}


def n_name(s):
    s = re.sub(r"[^a-z0-9 ]", " ", str(s or "").lower().replace("&", " and "))
    return " ".join(w for w in s.split() if w not in _NAME_STOP)


def n_phone(s):
    return re.sub(r"\D", "", str(s or ""))[-10:]


def n_domain(s):
    s = re.sub(r"^https?://", "", str(s or "").lower())
    return re.split(r"[/?#]", re.sub(r"^www\.", "", s))[0].split("@")[-1]


REGIONS = {"Southeast": "FL GA AL MS TN KY NC SC VA WV", "Northeast": "ME NH VT MA RI CT NY NJ PA DE MD DC",
           "Midwest": "OH MI IN IL WI MN IA MO ND SD NE KS", "South Central": "TX OK AR LA",
           "West": "WA OR CA NV AZ NM CO UT ID MT WY AK HI"}


def region_of(st):
    return next((r for r, sts in REGIONS.items() if st and st.upper() in sts.split()), "")


# ---- reading Desk data
def load_desk(path):
    """Desk companies from a backup .json, the app's seed.js, or the app's .zip."""
    path = Path(path)
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            seed = next((n for n in names if n.endswith("seed.js")), None)
            return _from_seed(z.read(seed).decode("utf-8")) if seed else []
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".js":
        return _from_seed(text)
    data = json.loads(text)
    if isinstance(data, dict) and data.get("app") == "prospecting-desk":
        return [c for c in data.get("companies", []) if c.get("type", "company") == "company"]
    raise ValueError("Not a Prospecting Desk backup (expected app: prospecting-desk).")


def _from_seed(text):
    dec, out = json.JSONDecoder(), []
    for m in re.finditer(r"window\.(PC_SEED|PC_LEGACY)\s*=\s*", text):
        obj, _ = dec.raw_decode(text, m.end())
        out += [c for c in obj if c.get("type") == "company"]
    return out


class DeskIndex:
    def __init__(self, companies):
        self.ids = {c.get("id") for c in companies}
        self.names = {n_name(c.get("name")) for c in companies} - {""}
        self.phones = {n_phone(c.get("phone")) for c in companies if len(n_phone(c.get("phone"))) == 10}
        self.domains = {n_domain(c.get("website")) for c in companies} - {""}

    def has(self, id_, name, phones, website):
        return (id_ in self.ids or n_name(name) in self.names or n_domain(website) in self.domains
                or any(n_phone(p) in self.phones for p in phones if len(n_phone(p)) == 10))


# ---- pipeline -> Desk
def _clamp(x):
    return max(1, min(5, x))


def ratings(f, r, contacts):
    """Pipeline evidence -> the Desk's 1-5 rubric. Industry growth is left unrated (not guessed)."""
    locs = len(r.get("us_locations", [])) + len(r.get("ca_locations", []))
    makes = f.get("manufacturer") or f.get("distributor") or f.get("food_producer")
    hard = f.get("flatbed_oversize") or f.get("reefer") or f.get("hazmat")
    freight_role = contacts and any(k in contacts[0].lower() for k in ("logistics", "shipping", "traffic",
                                                                       "transportation", "dispatch", "supply chain",
                                                                       "distribution", "warehouse", "operations"))
    out = {
        "demand": _clamp(2 + bool(makes) + bool(f.get("multi_location")) + bool(f.get("many_locations") or f.get("ships_national"))),
        "timing": 4 if f.get("growth") else 3 if hard else 2,
        "control": 4 if f.get("controls_delivery") else 2 if (f.get("customer_pickup") or f.get("own_fleet")) else 3,
        "footprint": _clamp((2 if locs <= 1 else 3 if locs < 5 else 4) + bool(f.get("ships_national") or f.get("cross_border"))),
        "expansion": 4 if f.get("growth") else 2,
        "complexity": 4 if hard else 2,
        "access": 4 if freight_role else 3 if contacts else 2 if r.get("phones") else 1,
        "ltv": 4 if (f.get("longevity") and makes) else 3,
        "research": 3 if (len(r.get("pages", [])) >= 4 and r.get("phones")) else 2,
    }
    return out


def _modes(f, r):
    if f.get("forwarder") or f.get("naics_forwarder"):
        return ["Drayage and ocean", "Van", "LTL and partials"] + (["Cross-border"] if f.get("cross_border") else [])
    modes = []
    naics = any(k.startswith("naics_") and v for k, v in f.items())
    if f.get("flatbed_oversize") or f.get("naics_flatbed"):
        modes.append("Flatbed")
    if f.get("naics_reefer") or (f.get("reefer") and not naics):  # trust the industry code over site words
        modes.append("Reefer")
    if not modes or f.get("naics_dryvan") or (not naics and (f.get("palletized") or f.get("retail_dealers"))):
        modes.insert(0, "Van")
    if any(re.search(r"oversiz|heavy haul|heavy (equipment|machinery)", h, re.I)
           for h in r.get("signal_hits", {}).get("flatbed_oversize", [])):
        modes.append("Heavy haul and specialized")
    return list(dict.fromkeys(modes))


QUESTIONS = {
    "Flatbed": ["Who books your outbound flatbeds, and which lanes get turned down most?",
                "Any loads that need tarps, permits or a step deck?", "Do you ship delivered, or do customers pick up?"],
    "Reefer": ["Dry, refrigerated or frozen?", "Which retail DCs do you deliver to, and which lanes get rejected most?",
               "Do you book the trucks or do customers pick up?"],
    "Forwarder": ["Which ports and airports do you move the most out of, and who covers your drayage?",
                  "When an inland truckload or LTL leg falls through, who's your next call?",
                  "Any lanes into Canada or Mexico?"],
    "Van": ["Do you ship delivered, or do customers pick up?", "Who books the trucks: you, a 3PL or the customer?",
            "Which lane gave you the most trouble this month?"],
}


def to_desk(row, stamp=None):
    stamp = stamp or today().isoformat()
    r, f = jload(row["research"], {}), jload(row["features"], {})
    meta, reasons = jload(row["meta"], {}), jload(row["reasons"], [])
    contacts = ranked_contacts(r.get("contacts", []))
    loc = (meta.get("plants") or r.get("us_locations") or r.get("ca_locations") or [""])[0]
    city, _, rest = loc.partition(",")
    state = rest.split()[0] if rest.split() else ""
    modes = _modes(f, r)
    cid = "pl_" + re.sub(r"[^a-z0-9]", "_", row["domain"])
    researched = (row["researched_at"] or now_iso())[:10]
    findings = [{"id": f"f{i}", "text": f"Company website: {p['kind']} page", "url": p["url"], "pub": researched,
                 "basis": "Public research"} for i, p in enumerate(r.get("pages", []))]
    if meta.get("plants"):
        findings.append({"id": f"f{len(findings)}", "url": "https://echo.epa.gov/",
                         "text": f"EPA ECHO: {len(meta['plants'])} active plant(s) ({'; '.join(meta['plants'][:5])}), "
                                 f"NAICS {' '.join(meta.get('naics', []))}", "pub": researched, "basis": "Public research"})
    gates = jload(row["gates"], {})
    if gates.get("qualified"):
        findings.append({"id": f"f{len(findings)}", "url": "https://data.transportation.gov/d/az4n-8mr2",
                         "text": "Requirements check: " + " | ".join(gates.get("reasons", [])),
                         "pub": researched, "basis": "Public research"})
    growth = r.get("signal_hits", {}).get("growth", [])
    why = "; ".join(x[2:] for x in reasons if x.startswith("+"))
    flags = [x for x in reasons if not x.startswith(("+", "-")) and "CRM" not in x]
    from .discover import ALL_NAICS
    industry = ("Freight forwarder" if (f.get("forwarder") or f.get("naics_forwarder")) else "") or \
        ", ".join(ALL_NAICS[n] for n in meta.get("naics", []) if n in ALL_NAICS) or ", ".join(
        k for k in ("manufacturer", "distributor", "food_producer") if f.get(k)).replace("_", " ")
    phones = r.get("phones", [])
    return {
        "k": "co_" + cid, "type": "company", "id": cid, "name": row["name"] or row["domain"],
        "phone": phones[0] if phones else "", "website": row["url"], "address": "", "city": city.strip(),
        "state": state, "region": region_of(state), "industry": industry,
        "description": (f"{len(meta['plants'])} EPA-registered plant(s). " if meta.get("plants") else "")
                       + (f"Ships from {'; '.join((r.get('us_locations') or [])[:3])}." if r.get("us_locations") else ""),
        "stage": "qualified" if row["verdict"] == "GO" else "researching",
        "priority": "A" if row["verdict"] == "GO" and (row["score"] or 0) >= 0.9 else "B" if row["verdict"] == "GO" else "C",
        "tags": [f"Pipeline {stamp}", row["verdict"]] + [p.title() for p in meta.get("profile", [])],
        "tag": {"status": "Unchecked", "checkedAt": "", "crmNames": row["name"] or row["domain"]},
        "tenders": "Unknown", "fleet": "None" if gates.get("qualified") else "Unknown",
        "trigger": {"text": f"Site mentions: \"{growth[0]}\"" if growth else "", "date": researched if growth else "",
                    "url": row["url"] if growth else ""},
        "freight": {"modes": modes,
                    "commodities": {"v": industry or "Ask what they ship", "b": "Public research" if industry else "Hypothesis"},
                    "frequency": {"v": "Unknown: ask how many loads a week leave each plant", "b": "Hypothesis"}},
        "research": {"why": (f"Pipeline fit {row['score']:.2f} ({row['verdict']}). " if row["score"] is not None else "") + why,
                     "findings": findings, "lastResearched": researched},
        "ratings": ratings(f, r, contacts),
        "ratingsNote": "Estimated by the pipeline from the company website" + (" and EPA plant data" if meta else "")
                       + ". Industry growth left for you to rate.",
        "opener": call_opener(row).strip('"'),
        "questions": QUESTIONS["Forwarder" if "Drayage and ocean" in modes else
                               next((m for m in ("Flatbed", "Reefer") if m in modes), "Van")],
        "caution": " ".join(flags),
        "contacts": [{"id": f"{cid}_c{i}", "name": c.partition(" — ")[0], "title": c.partition(" — ")[2], "dept": "",
                      "email": "", "phone": "", "linkedin": "", "location": "",
                      "role": "Primary" if i == 0 else "Secondary", "dm": i == 0 and bool(context(row)["first"]),
                      "source": "Company website", "verified": researched, "basis": "Listed on the company website",
                      "consent": "", "prefs": {"call": "Unknown", "email": "Unknown", "linkedin": "Unknown", "text": "Unknown"},
                      "optOut": False, "optOutAt": "", "notes": "", "createdAt": now_iso()} for i, c in enumerate(contacts[:3])],
        "phoneVerified": {"by": "Company website", "at": researched} if phones else None,
        "interactions": [], "next": None, "fuHist": [], "events": [{"ts": now_iso(), "k": "created"}],
        "source": f"Pipeline research, {stamp}", "createdAt": now_iso(), "updatedAt": now_iso(), "dnc": False,
    }


def export(conn, desk_companies, verdicts=("GO", "MAYBE"), limit=None):
    idx = DeskIndex(desk_companies)
    # status='queued' already means all three requirements passed (held leads never reach here)
    q = ("SELECT * FROM prospects WHERE status='queued' AND touches=0 AND desk_exported_at IS NULL AND verdict IN (%s) "
         "ORDER BY CASE verdict WHEN 'GO' THEN 0 ELSE 1 END, score DESC" % ",".join("?" * len(verdicts)))
    out, skipped = [], 0
    for row in conn.execute(q, verdicts).fetchall():
        rec = to_desk(row)
        if idx.has(rec["id"], rec["name"], jload(row["research"], {}).get("phones", []), rec["website"]):
            skipped += 1
            continue
        out.append(rec)
        if limit and len(out) >= limit:
            break
    return {"app": "prospecting-desk", "at": now_iso(), "companies": out}, skipped


def mark_exported(conn, backup):
    for c in backup["companies"]:
        conn.execute("UPDATE prospects SET desk_exported_at=? WHERE url=? OR name=?", (now_iso(), c["website"], c["name"]))
    conn.commit()


# ---- Desk -> pipeline
OTHER_CUST = "Active customer (other rep)"   # PC.OTHER_CUST in the Desk
POSITIVE = {"Load booked": "load_booked", "Quote sent": "quote_requested", "Meeting scheduled": "meeting",
            "Interested": "replied", "Decision-maker conversation": "replied", "Reply received": "replied",
            "Requested information": "replied", "Follow-up requested": "replied"}
RANK = ["replied", "meeting", "quote_requested", "load_booked"]
ATTEMPTS = {"No answer", "Voicemail", "Gatekeeper conversation", "Sent"}


def desk_outcome(c, today_=None):
    """(outcome or None, touches) for one Desk company."""
    import datetime as dt
    today_ = today_ or dt.date.today()
    its = c.get("interactions") or []
    touches = sum(1 for i in its if i.get("kind") in ("Call", "Email", "LinkedIn", "Meeting"))
    if c.get("dnc"):
        return "do_not_contact", touches
    if (c.get("tag") or {}).get("status") == OTHER_CUST:
        return "other_rep_customer", touches
    if (c.get("tag") or {}).get("status") == "Tagged":
        return "owned_by_other_rep", touches
    if c.get("stage") == "customer":
        return "load_booked", touches
    best = None
    for i in its:
        o = POSITIVE.get(i.get("outcome"))
        if o and (best is None or RANK.index(o) > RANK.index(best)):
            best = o
    if best:
        return best, touches
    if c.get("stage") == "disqualified" or any(i.get("outcome") == "Disqualified" for i in its):
        return "bad_fit", touches
    if c.get("stage") in ("lost", "nurture") or any(i.get("outcome") == "Not interested" for i in its):
        return "not_interested", touches
    attempts = [i for i in its if i.get("outcome") in ATTEMPTS]
    if len(attempts) >= 3:
        last = max(i.get("ts", "")[:10] for i in attempts)
        try:
            if (today_ - dt.date.fromisoformat(last)).days >= 7:
                return "no_response", touches
        except ValueError:
            pass
    return None, touches


def import_desk(conn, desk_companies):
    """Write Desk results into the pipeline. Idempotent: only changes what's new."""
    from .pipeline import record_outcome
    summary = {"companies": len(desk_companies), "matched": 0, "added": 0, "outcomes": {}, "touches": 0}
    by_url = {n_domain(r["url"]): r["domain"] for r in conn.execute("SELECT domain, url FROM prospects WHERE url != ''")}
    by_name = {n_name(r["name"]): r["domain"] for r in conn.execute("SELECT domain, name FROM prospects WHERE name IS NOT NULL")}
    for c in desk_companies:
        site = n_domain(c.get("website"))
        dom = (by_url.get(site) if site else None) or by_name.get(n_name(c.get("name")))
        if not dom and not site and desk_outcome(c)[0] == "other_rep_customer":
            found = _find_site(c)  # a model customer is only useful to learn from with a website to research
            if found:
                c = dict(c, website=found)
                site = n_domain(found)
                dom = by_url.get(site)
        if dom:
            summary["matched"] += 1
        else:
            # Desk-only company (often no website): keep it so discovery skips it and its results count.
            dom = normalize_domain(c.get("website")) if c.get("website") else f"desk-{c.get('id')}.desk"
            conn.execute("INSERT OR IGNORE INTO prospects(domain, url, name, source, added_at, status, desk_exported_at) "
                         "VALUES (?,?,?,?,?, 'suppressed', ?)",
                         (dom, c.get("website") or "", c.get("name"), "prospecting-desk", now_iso(), now_iso()))
            conn.execute("UPDATE prospects SET filter_reason='in Prospecting Desk' WHERE domain=? AND filter_reason IS NULL",
                         (dom,))
            summary["added"] += 1
            by_name[n_name(c.get("name"))] = dom
        outcome, touches = desk_outcome(c)
        if outcome == "other_rep_customer":
            dom = _upgrade_model(conn, dom, c) or dom
            _keep_desk_research(conn, dom, c)
        row = conn.execute("SELECT touches, last_outcome FROM prospects WHERE domain=?", (dom,)).fetchone()
        if touches > (row["touches"] or 0):
            conn.execute("UPDATE prospects SET touches=?, contacted_at=COALESCE(contacted_at, ?), desk_exported_at="
                         "COALESCE(desk_exported_at, ?) WHERE domain=?", (touches, now_iso(), now_iso(), dom))
            summary["touches"] += touches - (row["touches"] or 0)
        if outcome and outcome != row["last_outcome"]:
            conn.commit()
            record_outcome(conn, dom, outcome, "from Prospecting Desk")
            summary["outcomes"][outcome] = summary["outcomes"].get(outcome, 0) + 1
    conn.commit()
    return summary


def _find_site(c):
    from .discover import find_website
    from .fetch import Fetcher
    from .config import load_policy
    get = Fetcher(dict(load_policy(), min_seconds_between_requests=0, request_timeout_seconds=6), retries=0).get
    try:
        return find_website(c.get("name", ""), (c.get("state") or "").upper(), get, [c.get("city")] if c.get("city") else [])
    except Exception:
        return None


def research_models(conn, icp, policy, log=print):
    """Crawl model customers that have a website but no research yet (they stay off every call list)."""
    from .fetch import Fetcher
    from .pipeline import research_one
    rows = conn.execute("SELECT domain FROM prospects WHERE last_outcome IN ('other_rep_customer','existing_customer',"
                        "'load_booked') AND page_text IS NULL AND url != ''").fetchall()
    f = Fetcher(policy)
    for r in rows:
        log(f"  researching model customer {r['domain']}: {research_one(conn, r['domain'], icp, policy, f)}")
    return len(rows)


def _upgrade_model(conn, dom, c):
    """A Desk-only record (no website) that's now a model customer: find its site and switch the record to it."""
    row = conn.execute("SELECT url, meta FROM prospects WHERE domain=?", (dom,)).fetchone()
    if not row or row["url"]:
        return None
    found = c.get("website") or _find_site(c)
    if not found:
        return None
    new = normalize_domain(found)
    if conn.execute("SELECT 1 FROM prospects WHERE domain=?", (new,)).fetchone():
        return new  # already researched under its real domain
    meta = jload(row["meta"], {})
    if c.get("city") and c.get("state"):
        meta.setdefault("plants", [f"{c['city']}, {c['state'].upper()}"])
    conn.execute("UPDATE prospects SET domain=?, url=?, meta=? WHERE domain=?", (new, found, json.dumps(meta), dom))
    conn.execute("UPDATE outcomes SET domain=? WHERE domain=?", (new, dom))
    conn.commit()
    return new


def _desk_text(c):
    fr = c.get("freight") or {}
    parts = [c.get("name"), c.get("industry"), c.get("description"), (fr.get("commodities") or {}).get("v"),
             (fr.get("seasonality") or {}).get("v"), (fr.get("frequency") or {}).get("v"), (c.get("research") or {}).get("why"),
             (c.get("trigger") or {}).get("text"), " ".join(f.get("text", "") for f in (c.get("research") or {}).get("findings", []))]
    return "\n".join(p for p in parts if p)


def _keep_desk_research(conn, dom, c):
    """Save the Desk's own research on a model customer, used when its website can't be found or read."""
    row = conn.execute("SELECT meta FROM prospects WHERE domain=?", (dom,)).fetchone()
    if not row:
        return
    meta = jload(row["meta"], {})
    meta["desk"] = {"text": _desk_text(c), "modes": (c.get("freight") or {}).get("modes", []),
                    "city": c.get("city", ""), "state": (c.get("state") or "").upper()}
    conn.execute("UPDATE prospects SET meta=? WHERE domain=?", (json.dumps(meta), dom))
    conn.commit()


MODE_FEATURES = {"Flatbed": "flatbed_oversize", "Heavy haul and specialized": "flatbed_oversize", "Reefer": "reefer",
                 "Van": "palletized", "LTL and partials": "palletized", "Cross-border": "cross_border",
                 "Drayage and ocean": "forwarder"}


def profile_from_desk(conn, icp):
    """Model customers with no readable website: build their traits from the Desk's research."""
    from .extract import pattern_features
    n = 0
    for r in conn.execute("SELECT * FROM prospects WHERE last_outcome IN ('other_rep_customer','existing_customer',"
                          "'load_booked') AND page_text IS NULL").fetchall():
        d = jload(r["meta"], {}).get("desk")
        if not d or not d.get("text"):
            continue
        features, hits = pattern_features(d["text"], icp["signals"])
        for m in d.get("modes", []):
            if m in MODE_FEATURES:
                features[MODE_FEATURES[m]] = 1
        loc = f"{d['city']}, {d['state']}" if d.get("city") and d.get("state") else ""
        research = {"name": r["name"], "pages": [], "us_locations": [loc] if loc else [], "signal_hits": hits,
                    "source": "Prospecting Desk research"}
        conn.execute("UPDATE prospects SET page_text=?, features=?, research=?, researched_at=? WHERE domain=?",
                     (d["text"], json.dumps(features), json.dumps(research), now_iso(), r["domain"]))
        n += 1
    conn.commit()
    return n
