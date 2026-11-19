"""Does this shipper run its own trucks? Ask the federal government.

Any company that operates its own trucks in interstate commerce must hold a USDOT number and report its fleet to
FMCSA. Shippers hauling their own goods register as "PRIVATE PROPERTY" carriers; companies with their own trucking
authority register as "AUTHORIZED FOR HIRE". The FMCSA Company Census (public, data.transportation.gov) lists them
with power-unit counts. We pull active registrations with trucks for the states we prospect in (a few bulk requests),
cache them, and match prospects locally by normalized name + state.
"""
import json
import re
import time
import urllib.parse
import urllib.request

from .util import now_iso

CENSUS = "https://data.transportation.gov/resource/az4n-8mr2.json"
FIELDS = "dot_number,legal_name,dba_name,phy_city,phy_state,classdef,carrier_operation,power_units,status_code"
LEGAL = {"inc", "llc", "co", "corp", "corporation", "company", "ltd", "the", "group", "and", "incorporated", "lp",
         "llp", "of", "dba", "a", "usa", "us", "na", "div", "division"}


def key(name):
    name = re.sub(r"['’]s\b", "", str(name or "").lower())  # Herr's -> herr
    words = [w for w in re.findall(r"[a-z0-9]+", name.replace("&", " and ")) if w not in LEGAL]
    return " ".join(words)


def _get(url, tries=6):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ProspectResearch/1.0", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=180) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and i < tries - 1:  # throttled or busy: back off
                time.sleep(30 * (i + 1))
                continue
            raise
        except (urllib.error.URLError, ConnectionResetError, TimeoutError) as e:
            if i < tries - 1:  # dropped connection on a big download: retry
                time.sleep(15 * (i + 1))
                continue
            raise


def pull_state(state, get=_get, page=50000):
    """Active private fleets (a shipper's own trucks, incl. ones that also hold for-hire authority) in `state`."""
    rows, offset = [], 0
    while True:
        qs = urllib.parse.urlencode({
            "$select": FIELDS, "$limit": page, "$offset": offset, "$order": "dot_number",
            "$where": f"phy_state='{state}' AND status_code='A' AND power_units not in ('0','') "
                      "AND classdef like '%PRIVATE PROPERTY%'"})  # shippers hauling their own goods
        batch = get(CENSUS + "?" + qs)
        rows += batch
        if len(batch) < page:
            return rows
        offset += page


def ensure_states(conn, states, get=_get, max_age_days=30, log=print):
    """Cache each state's fleet list (refreshed monthly)."""
    import datetime as dt
    for st in sorted(set(s for s in states if s)):
        row = conn.execute("SELECT fetched_at FROM fmcsa_states WHERE state=?", (st,)).fetchone()
        if row and (dt.datetime.now() - dt.datetime.fromisoformat(row["fetched_at"])).days < max_age_days:
            continue
        log(f"  FMCSA: downloading registered fleets in {st}…")
        rows = pull_state(st, get)
        conn.execute("DELETE FROM fmcsa WHERE state=?", (st,))
        conn.executemany("INSERT INTO fmcsa(state, name_key, dba_key, city, dot, classdef, power_units, interstate) "
                         "VALUES (?,?,?,?,?,?,?,?)",
                         [(st, key(r.get("legal_name")), key(r.get("dba_name")), (r.get("phy_city") or "").upper(),
                           r.get("dot_number"), r.get("classdef"), int(r.get("power_units") or 0),
                           1 if r.get("carrier_operation") == "A" else 0) for r in rows])
        conn.execute("INSERT OR REPLACE INTO fmcsa_states(state, fetched_at, n) VALUES (?,?,?)", (st, now_iso(), len(rows)))
        conn.commit()
        log(f"  FMCSA: {len(rows)} active fleets in {st}")


def match(conn, name, states, cities=()):
    """Registered fleets that are this company. Exact normalized name, or the first two name words + same city."""
    k = key(name)
    if not k or not states:
        return []
    marks = ",".join("?" * len(states))
    rows = conn.execute(f"SELECT * FROM fmcsa WHERE state IN ({marks}) AND (name_key=? OR dba_key=?)",
                        (*states, k, k)).fetchall()
    two = " ".join(k.split()[:2])
    from .discover import GENERIC, STATE_FULL
    weak = GENERIC | {w for s in STATE_FULL.values() for w in s.lower().split()} | {
        "great", "lakes", "valley", "river", "mountain", "coastal", "central", "eastern", "western", "north", "south",
        "east", "west", "first", "new", "standard", "quality", "premier", "superior", "best", "general", "global",
        "allied", "advanced", "precision", "custom", "royal", "golden", "star", "eagle", "liberty", "patriot"}
    first = k.split()[0]
    if not rows and cities and first not in weak and len(first) >= 4:
        # "Herr" + same city as one of their plants -> "HERR FOODS INC" in Nottingham
        cm = ",".join("?" * len(cities))
        rows = conn.execute(f"SELECT * FROM fmcsa WHERE state IN ({marks}) AND city IN ({cm}) AND "
                            "(name_key = ? OR name_key LIKE ? OR dba_key = ? OR dba_key LIKE ?)",
                            (*states, *[c.upper() for c in cities], first, first + " %", first, first + " %")).fetchall()
    if not rows and first not in weak and len(first) >= 3:
        # Trucking subsidiary in the same state: "UTZTRAN", "Smith Trucking", "Acme Transport LLC"
        rows = [r for r in conn.execute(f"SELECT * FROM fmcsa WHERE state IN ({marks}) AND (name_key LIKE ? OR dba_key LIKE ?)",
                                        (*states, first + "%", first + "%")).fetchall()
                if re.match(re.escape(first) + r" ?(tran|truck|transport|logistic|fleet|deliver|freight|express|carrier|distribut)",
                            r["name_key"] or "") or re.match(re.escape(first) + r" ?(tran|truck|transport|logistic|fleet|deliver)",
                                                             r["dba_key"] or "")]
    if not rows and first not in weak and len(first) >= 6:
        # Distinctive name anywhere in the US (head office state may not be on their website): "UNVERFERTH MFG".
        # Only the exact name, or the name + a corporate word; "Mikesell Sales Service" is not "Mikesell's".
        suffixes = ("mfg", "manufacturing", "industries", "products", "foods", "brands", "enterprises", "holdings")
        rows = conn.execute("SELECT * FROM fmcsa WHERE name_key = ? OR dba_key = ? OR name_key IN (%s)"
                            % ",".join("?" * len(suffixes)), (k, k, *[f"{k} {s}" for s in suffixes])).fetchall()
    if not rows and len(k.split()) >= 2 and cities:
        cm = ",".join("?" * len(cities))
        rows = conn.execute(f"SELECT * FROM fmcsa WHERE state IN ({marks}) AND city IN ({cm}) AND "
                            "(name_key LIKE ? OR dba_key LIKE ?)",
                            (*states, *[c.upper() for c in cities], two + "%", two + "%")).fetchall()
    return rows


def states_checked(conn):
    return {r["state"] for r in conn.execute("SELECT state FROM fmcsa_states")}
