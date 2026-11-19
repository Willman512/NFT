"""What do the companies TQL already ships for have in common?

Model customers = companies you marked in Prospecting Desk as "Active customer (other rep)" (plus any customers of
your own: load_booked, existing_customer). They can't be called, but they're proof of what a TQL customer looks
like. We compare their traits with the average lead (website signals, EPA industry codes, freight profile, number of
locations, forwarder or not) and keep the traits that are much more common among customers.

Every lead then gets a lookalike score: the sum of those traits' log-lift. Leads that score like the model
customers get the `lookalike` feature (a scoring signal), the shared traits show on their card, and `discover
--like-customers` searches the industries and states the customers cluster in. No negatives needed: the
contrast is the general lead pool, so this works from the first few customers on.
"""
import json
import math
from collections import Counter

from .db import jload
from .util import now_iso

MODEL_OUTCOMES = ("other_rep_customer", "existing_customer", "load_booked")
MIN_MODELS = 3
IGNORE = {"lookalike", "stale_site", "thin_site", "contact_info_missing", "generic_inbox_only", "french_site",
          "named_contact", "no_na_presence"}  # website quality, not company traits


def traits(row):
    """Company traits used for comparison: website signals + EPA industry codes + size."""
    f = jload(row["features"], {})
    meta = jload(row["meta"], {})
    research = jload(row["research"], {})
    t = {k for k, v in f.items() if v and k not in IGNORE and not k.startswith("term:")}
    t |= {f"naics:{n}" for n in meta.get("naics", [])}
    if research.get("source") != "Prospecting Desk research":  # Desk records list one location: size unknown
        locs = len(research.get("us_locations", [])) + len(research.get("ca_locations", []))
        t.add("size:5+ locations" if locs >= 5 else "size:2-4 locations" if locs >= 2 else "size:1 location")
    return t


def build(conn):
    rows = conn.execute("SELECT * FROM prospects WHERE features IS NOT NULL").fetchall()
    models = [r for r in rows if r["last_outcome"] in MODEL_OUTCOMES]
    base = [r for r in rows if r["last_outcome"] not in MODEL_OUTCOMES]
    if len(models) < MIN_MODELS or not base:
        prof = {"n_models": len(models), "active": False, "built_at": now_iso()}
        _save(conn, prof)
        return prof
    mc = Counter(t for r in models for t in traits(r))
    bc = Counter(t for r in base for t in traits(r))
    nm, nb = len(models), len(base)
    lift = {}
    for t in set(mc) | set(bc):
        pm, pb = (mc[t] + 0.5) / (nm + 1), (bc[t] + 0.5) / (nb + 1)
        lift[t] = max(-1.5, min(1.5, math.log(pm / pb)))
    prof = {"n_models": nm, "n_base": nb, "active": True, "built_at": now_iso(), "lift": lift,
            "common": sorted(((mc[t] / nm, t) for t in mc), reverse=True)[:15],
            "distinctive": sorted(((lift[t], t, mc[t], bc[t]) for t in mc if mc[t] >= 2 and lift[t] > 0.3), reverse=True)[:15],
            "states": Counter(_state(loc) for r in models for loc in jload(r["research"], {}).get("us_locations", [])[:3]
                              if _state(loc)).most_common(8),
            "naics": Counter(n for r in models for n in jload(r["meta"], {}).get("naics", [])).most_common(8),
            "models": [r["name"] or r["domain"] for r in models]}
    scores = sorted(score(prof, traits(r))[0] for r in models)
    prof["threshold"] = scores[len(scores) // 4]  # a lead scoring like the lower quartile of real customers qualifies
    _save(conn, prof)
    return prof


def _state(loc):
    _, _, rest = loc.partition(",")
    return rest.split()[0] if rest.split() else ""


def _save(conn, prof):
    conn.execute("CREATE TABLE IF NOT EXISTS customer_profile (id INTEGER PRIMARY KEY CHECK (id = 1), data TEXT)")
    conn.execute("INSERT OR REPLACE INTO customer_profile(id, data) VALUES (1, ?)", (json.dumps(prof),))
    conn.commit()


def load(conn):
    try:
        row = conn.execute("SELECT data FROM customer_profile WHERE id=1").fetchone()
    except Exception:
        return None
    return json.loads(row["data"]) if row else None


def score(prof, ts):
    """(lookalike score, top shared traits)"""
    lift = prof.get("lift", {})
    contrib = sorted(((lift.get(t, 0.0), t) for t in ts), reverse=True)
    return sum(v for v, _ in contrib), [t for v, t in contrib if v > 0.3][:3]


def label(t, icp):
    if t.startswith("naics:"):
        from .discover import ALL_NAICS
        code = t[6:]
        return f"industry {ALL_NAICS.get(code, code)}"
    if t.startswith("size:"):
        return t[5:]
    return icp["signals"].get(t, {}).get("label", t).split(" (")[0].lower()


def render(prof, icp):
    if not prof or not prof.get("active"):
        n = (prof or {}).get("n_models", 0)
        return (f"Customer profile: {n} model customer(s); needs {MIN_MODELS}+. In Prospecting Desk, mark CRM-checked "
                "companies 'Active customer (other rep)', back up, then run `desk-import`.")
    out = [f"## What {prof['n_models']} active customers have in common (vs {prof['n_base']} other leads)", "",
           "Most common traits:"]
    out += [f"- {share:.0%} {label(t, icp)}" for share, t in prof["common"][:8]]
    out += ["", "Much more common among customers than other leads (these drive the lookalike score):"]
    out += [f"- {label(t, icp)}: {m}/{prof['n_models']} customers vs {b}/{prof['n_base']} leads ({math.exp(l):.1f}x)"
            for l, t, m, b in prof["distinctive"][:8]] or ["- nothing stands out yet (add more model customers)"]
    if prof.get("states"):
        out += ["", "Where they are: " + ", ".join(f"{s} ({n})" for s, n in prof["states"])]
    if prof.get("naics"):
        from .discover import ALL_NAICS
        out += ["Industries (EPA codes): " + ", ".join(f"{ALL_NAICS.get(c, c)} ({n})" for c, n in prof["naics"])]
    return "\n".join(out)


def discovery_targets(prof, top=4):
    """States and NAICS codes to discover next, from where the customers cluster."""
    if not prof or not prof.get("active"):
        return [], []
    return [s for s, _ in prof.get("states", [])[:top]], [c for c, _ in prof.get("naics", [])[:top]]
