"""Find new shippers automatically from public government data.

Source: EPA ECHO (echo.epa.gov), a free public API listing every EPA-regulated facility in the US with its
industry code (NAICS), street address and active status. Factories, sawmills, precast plants and food
plants are almost all in it. We pull active plants by NAICS code + state, group them by company (more plants
= more freight), find the company website, verify it, and drop it into the research pipeline.
"""
import json
import re
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from .util import normalize_domain, now_iso

ECHO = "https://echodata.epa.gov/echo/echo_rest_services."

# Freight profiles -> NAICS codes. Pick by what TQL covers best for you.
PROFILES = {
    "flatbed": {
        "332312": "structural steel fabrication", "332311": "prefab metal buildings", "332322": "sheet metal work",
        "327390": "precast concrete products", "327331": "concrete block & brick", "327332": "concrete pipe",
        "321113": "sawmills", "321214": "trusses", "321215": "engineered wood", "331210": "steel pipe & tube",
        "423510": "metal service centers", "333111": "farm machinery", "333120": "construction machinery",
        "336212": "truck trailers", "327991": "cut stone",
    },
    "reefer": {
        "311411": "frozen fruit & vegetables", "311412": "frozen specialty foods", "311611": "meat processing",
        "311612": "meat products", "311615": "poultry processing", "311511": "fluid milk", "311513": "cheese",
        "311520": "ice cream", "311991": "perishable prepared foods", "311710": "seafood", "424480": "fresh produce wholesale",
    },
    "dryvan": {
        "311919": "snack foods", "311812": "commercial bakeries", "312111": "soft drinks", "312120": "breweries",
        "311421": "canned fruit & vegetables", "311230": "breakfast cereal", "325314": "fertilizer mixing",
        "326199": "plastics products", "322211": "corrugated boxes", "337122": "furniture",
        "325510": "paint", "325611": "soap & detergents", "327310": "cement", "327420": "gypsum products",
    },
}
ALL_NAICS = {k: v for p in PROFILES.values() for k, v in p.items()}
NAICS_PROFILE = {code: prof for prof, codes in PROFILES.items() for code in codes}

STATE_FULL = {"AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
              "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
              "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
              "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
              "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
              "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
              "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
              "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
              "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
              "WI": "Wisconsin", "WY": "Wyoming"}

LEGAL = {"inc", "llc", "co", "corp", "corporation", "company", "ltd", "lp", "llp", "the", "of", "and", "dba", "plc",
         "incorporated", "limited", "&", "a", "div", "division", "plant", "facility", "mill", "usa", "us", "na"}
GENERIC = {"steel", "metal", "metals", "concrete", "precast", "products", "industries", "manufacturing", "mfg",
           "foods", "food", "farms", "lumber", "supply", "group", "enterprises", "fabrication", "fabricators",
           "fabricating", "services", "systems", "materials", "international", "american", "national", "united",
           "southern", "northern", "midwest", "pacific", "inc", "works", "company", "co", "sales", "solutions"}
NOT_SHIPPERS = re.compile(r"\b(county|city of|township|university|college|school|hospital|department|dept|"
                          r"state of|u\.?s\.? army|air force|navy|correctional|prison|landfill)\b", re.I)


def _get_json(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "ProspectResearch/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


def echo_facilities(state, naics, get_json=_get_json, max_pages=20):
    """All active facilities for one state + NAICS code."""
    qs = urllib.parse.urlencode({"output": "JSON", "p_st": state, "p_ncs": naics, "p_act": "Y"})
    res = get_json(ECHO + "get_facilities?" + qs)["Results"]
    qid = res.get("QueryID")
    if not qid or res.get("QueryRows") in (None, "0"):
        return []
    out = []
    for page in range(1, max_pages + 1):
        qs = urllib.parse.urlencode({"output": "JSON", "qid": qid, "pageno": page})
        rows = get_json(ECHO + "get_qid?" + qs)["Results"].get("Facilities") or []
        if not rows:
            break
        out += rows
    return out


def company_key(name):
    words = re.findall(r"[a-z0-9]+", (name or "").lower())
    words = [w for w in words if w not in LEGAL]
    return " ".join(words[:4])


def group_companies(facilities, naics_wanted):
    """Facilities -> companies with plant counts. Drops government, schools, etc."""
    companies = {}
    for f in facilities:
        name = (f.get("FacName") or "").strip()
        if not name or NOT_SHIPPERS.search(name):
            continue
        key = company_key(name)
        if not key:
            continue
        codes = set((f.get("FacNAICSCodes") or "").split()) | set((f.get("CAANAICS") or "").split())
        c = companies.setdefault(key, {"name": name.title(), "key": key, "plants": [], "naics": set()})
        plant = f"{(f.get('FacCity') or '').title()}, {f.get('FacState')} {f.get('FacZip') or ''}".strip()
        if plant not in c["plants"]:
            c["plants"].append(plant)
        c["naics"] |= codes & set(naics_wanted) or codes & set(ALL_NAICS)
    for c in companies.values():
        c["naics"] = sorted(c["naics"])
        c["profile"] = sorted({NAICS_PROFILE[n] for n in c["naics"] if n in NAICS_PROFILE})
    return sorted(companies.values(), key=lambda c: -len(c["plants"]))


def domain_guesses(name):
    """'Cooper Steel Fabricators Inc' -> coopersteelfabricators.com, coopersteel.com, cooper-steel.com, …"""
    words = [w for w in re.findall(r"[a-z0-9]+", name.lower()) if w not in LEGAL]
    if not words:
        return []
    cands = []
    for n in (len(words), 3, 2, 1):
        part = words[:n]
        if not part or (n == 1 and part[0] in GENERIC):
            continue
        cands += ["".join(part) + ".com", "-".join(part) + ".com"]
    distinct = [w for w in words if w not in GENERIC]
    if distinct:
        cands += [distinct[0] + w + ".com" for w in ("inc", "co", "usa", "mfg", "corp")]
    seen, out = set(), []
    for c in cands:
        if c not in seen and 6 <= len(c) <= 45:
            seen.add(c)
            out.append(c)
    return out[:8]


def site_matches(html, name, state, cities=()):
    """Is this homepage really this company? Needs the company's name phrase AND one of its plant cities
    (or its state spelled out / 'ST 12345'). Rejects parked domains."""
    raw = html or ""
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw)).lower().replace("-", " ")
    if re.search(r"domain (is )?for sale|buy this domain|parked free|hugedomains|this domain may be for sale", text):
        return False
    words = [w for w in re.findall(r"[a-z0-9]+", name.lower()) if w not in LEGAL]
    if not words:
        return False
    phrase = " ".join(words[:2])
    title = " ".join(re.findall(r"<title[^>]*>(.*?)</title>", raw, re.I | re.S)).lower()
    name_ok = phrase in text or (len(words) == 1 or words[1] in GENERIC) and words[0] in title
    if not name_ok:
        return False
    loc_ok = any(c and re.search(r"\b" + re.escape(c.lower()) + r"\b", text) for c in cities) \
        or STATE_FULL.get(state, "~").lower() in text \
        or re.search(r"\b" + state.lower() + r"\.?\s+\d{5}\b", text)
    return bool(loc_ok)


def _resolves(domain):
    import socket
    try:
        socket.getaddrinfo(domain, 443)
        return True
    except OSError:
        return False


def find_website(name, state, get, cities=()):
    """Try likely domains (DNS check first, it's 100x faster); return the first verified homepage or None."""
    for dom in domain_guesses(name):
        if not _resolves(dom):
            continue
        for url in (f"https://www.{dom}", f"https://{dom}"):
            try:
                final, html = get(url)
            except Exception:
                continue
            if site_matches(html, name, state, cities):
                return final
            break  # host answered but isn't them: next domain
    return None


def discover(conn, states, naics, get_json=_get_json, get_page=None, find_sites=True, limit=None,
             workers=16, log=print):
    """Pull, group, dedupe, find websites, add to prospects. Returns a summary dict."""
    from .fetch import Fetcher
    from .config import load_policy
    from .pipeline import add_lead

    policy = load_policy()
    # Guessing domains: most guesses don't exist, so fail fast (no retries, short timeout).
    get_page = get_page or Fetcher(dict(policy, min_seconds_between_requests=0, request_timeout_seconds=6), retries=0).get
    summary = {"facilities": 0, "companies": 0, "new": 0, "matched": 0, "unmatched": 0, "known": 0}
    facilities = []
    for st in states:
        for code in naics:
            try:
                rows = echo_facilities(st, code, get_json)
            except Exception as e:
                log(f"  {st} {code}: EPA lookup failed ({type(e).__name__})")
                continue
            log(f"  {st} {code} ({ALL_NAICS.get(code, '?')}): {len(rows)} active plants")
            facilities += rows
    summary["facilities"] = len(facilities)
    companies = group_companies(facilities, naics)
    summary["companies"] = len(companies)

    known = {r["key"] for r in conn.execute("SELECT key FROM discovered")}
    # Same company already in the pipeline under another domain (encoreprecast.com vs encoreprecastllc.com)?
    have = {company_key(r["name"]) for r in conn.execute("SELECT name FROM prospects WHERE name IS NOT NULL")}
    have |= {company_key(r["domain"].split(".")[0]) for r in conn.execute("SELECT domain FROM prospects")}
    known |= {c["key"] for c in companies if c["key"] in have or c["key"].replace(" ", "") in have
              or " ".join(c["key"].split()[:2]) in have}
    fresh = [c for c in companies if c["key"] not in known]
    summary["known"] = len(companies) - len(fresh)

    # Requirement: no own fleet. Drop companies with an FMCSA-registered fleet before any website work.
    from . import fmcsa
    min_units = int(policy.get("fleet_min_power_units", 2))
    try:
        fmcsa.ensure_states(conn, states, log=log)
    except Exception as e:
        log(f"  FMCSA download failed ({type(e).__name__}); fleet check will run at research time instead")
    summary["fleet_dropped"] = 0
    keep = []
    for c in fresh:
        sts = sorted({p.split(", ")[1][:2] for p in c["plants"] if ", " in p})
        regs = [r for r in fmcsa.match(conn, c["name"], sts, [p.split(",")[0] for p in c["plants"]])
                if r["power_units"] >= min_units]
        if regs:
            conn.execute("INSERT OR REPLACE INTO discovered(key, name, plants, naics, website, found_at) VALUES (?,?,?,?,?,?)",
                         (c["key"], c["name"], json.dumps(c["plants"]), json.dumps(c["naics"]),
                          f"fleet:USDOT {regs[0]['dot']} ({regs[0]['power_units']} trucks)", now_iso()))
            summary["fleet_dropped"] += 1
        else:
            keep.append(c)
    conn.commit()
    fresh = keep
    if limit:
        fresh = fresh[:limit]

    def work(c):
        state = c["plants"][0].split(", ")[1][:2] if ", " in c["plants"][0] else states[0]
        cities = [p.split(",")[0] for p in c["plants"]]
        return c, (find_website(c["name"], state, get_page, cities) if find_sites else None)

    from concurrent.futures import as_completed
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(work, c) for c in fresh]
        for fut in as_completed(futures):  # save each as it finishes; one slow site can't block the rest
            try:
                c, url = fut.result()
            except Exception:
                continue
            meta = {"plants": c["plants"], "naics": c["naics"], "profile": c["profile"], "source": "epa-echo"}
            conn.execute("INSERT OR REPLACE INTO discovered(key, name, plants, naics, website, found_at) "
                         "VALUES (?,?,?,?,?,?)", (c["key"], c["name"], json.dumps(c["plants"]), json.dumps(c["naics"]),
                                                  url, now_iso()))
            summary["new"] += 1
            conn.commit()
            if url:
                dom, status = add_lead(conn, url, c["name"], "epa-echo",
                                       f"{len(c['plants'])} plant(s): {'; '.join(c['plants'][:4])}; NAICS {' '.join(c['naics'])}")
                if dom:
                    conn.execute("UPDATE prospects SET meta=? WHERE domain=?", (json.dumps(meta), dom))
                summary["matched"] += 1
            else:
                summary["unmatched"] += 1
    conn.commit()
    return summary


def unmatched(conn, limit=500):
    return conn.execute("SELECT * FROM discovered WHERE website IS NULL ORDER BY json_array_length(plants) DESC, name "
                        "LIMIT ?", (limit,)).fetchall()


def domain_of(url):
    return normalize_domain(url)
