"""Human-readable output: research cards, the queue, exports, and the scoreboard."""
import csv
import json

from .config import OUTPUT_DIR
from .db import jload
from .util import today

FREIGHT_ROLES = ("logistics", "shipping", "traffic", "transportation", "supply chain", "distribution", "warehouse",
                 "operations", "dispatch")


NOT_FREIGHT_ROLES = ("hr", "human resources", "marketing", "finance", "accounting", "communications", "it ", "technology",
                     "legal", "counsel", "people", "talent", "recruit")


def ranked_contacts(contacts):
    """Freight/logistics roles first, then operations, then owner/president/CEO. Drops HR, marketing, finance…"""
    def rank(c):
        title = " " + c.partition(" — ")[2].lower() + " "
        if any(k in title for k in ("logistics", "shipping", "traffic", "transportation", "dispatch", "supply chain",
                                    "distribution", "warehouse")):
            return 0
        if "operations" in title or "plant" in title or "purchasing" in title:
            return 1
        if any(k in title for k in ("owner", "president", "ceo", "coo", "general manager")):
            return 2
        return 3
    keep = [c for c in contacts if not any(k in " " + c.partition(" — ")[2].lower() + " " for k in NOT_FREIGHT_ROLES)]
    return sorted(keep, key=rank)


def card(row):
    """Research card in the same shape as the tql-prospect-research skill."""
    r = jload(row["research"], {})
    reasons = jload(row["reasons"], [])
    feats = jload(row["features"], {})
    out = [f"# {row['name'] or row['domain']}  ({row['domain']})",
           f"**Verdict:** {row['verdict'] or 'not researched'}  (fit score {row['score'] or 0:.2f}, "
           f"status: {row['status']}{', ' + row['filter_reason'] if row['filter_reason'] else ''})"]
    out += [f"  {x}" for x in reasons]

    g = jload(row["gates"], {}) if "gates" in row.keys() else {}
    if g:
        out.append("\n**Requirements:** " + ("all met" if g.get("qualified") else "NOT met: held"))
        out += [f"- {x}" for x in g.get("reasons", [])]
    out.append("\n**Company snapshot:**")
    hits = r.get("signal_hits", {})
    profile = [k for k in ("flatbed_oversize", "reefer", "palletized", "hazmat") if feats.get(k)]
    what = [k for k in ("manufacturer", "distributor", "food_producer") if feats.get(k)]
    out.append(f"- Type: {', '.join(what) or 'unclear from site'}; freight profile: {', '.join(profile) or 'unclear'}")
    locs = r.get("us_locations", []) + r.get("ca_locations", [])
    out.append(f"- Ships from: {'; '.join(locs[:5]) or 'no address found'}"
               + (f" (+{len(locs) - 5} more)" if len(locs) > 5 else ""))
    reach = [x for x in ("ships_national", "cross_border", "retail_dealers") if feats.get(x)]
    out.append(f"- Reach: {', '.join(reach) or 'regional/unclear'}" + (f"; MX mentions: {', '.join(r['mx_mentions'])}" if r.get("mx_mentions") else ""))
    if r.get("detail"):
        out.append(f"- Distinctive detail: \"{r['detail']}\"")
    if r.get("copyright_year"):
        out.append(f"- Site freshness: © {r['copyright_year']}")
    if hits:
        out.append("- Evidence: " + "; ".join(f"{k}: {', '.join(v)}" for k, v in list(hits.items())[:6]))

    out.append("\n**Who to contact:**")
    contacts = ranked_contacts(r.get("contacts", []))
    if contacts:
        out += [f"- {c}" for c in contacts]
    else:
        out.append(f"- No named contact on site. LinkedIn: \"{row['name'] or row['domain']} logistics\", \"shipping\", "
                   "\"traffic manager\". Or call the main line and ask who handles transportation.")
    if r.get("phones"):
        out.append(f"- Phone: {', '.join(r['phones'][:3])}")
    if r.get("emails"):
        out.append(f"- Email: {', '.join(r['emails'][:4])}" + ("  (generic inbox only)" if feats.get("generic_inbox_only") else ""))

    out.append("\n**Sources:**")
    out += [f"- {p['kind']}: {p['url']}" for p in r.get("pages", [])]
    from .email import call_opener
    out.append(f"\n**Call opener:** {call_opener(row)}")
    out.append(f"\nWant the intro email? Run: python prospect.py email {row['domain']}")
    return "\n".join(out)


def queue_table(rows):
    if not rows:
        return "Queue is empty. Add leads (`add`) and run a cycle (`cycle`)."
    lines = [f"{'#':>3}  {'verdict':7} {'score':>5}  {'domain':34} top reason"]
    for i, r in enumerate(rows, 1):
        reasons = jload(r["reasons"], [])
        top = next((x for x in reasons if x.startswith("+")), reasons[0] if reasons else "")
        flag = " ⚑" if any("RED FLAG" in x or "stale" in x for x in reasons) else ""
        lines.append(f"{i:>3}  {r['verdict']:7} {r['score']:>5.2f}  {r['domain'][:34]:34} {top[:60]}{flag}")
    return "\n".join(lines)


def export(conn, fmt="csv"):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = conn.execute("SELECT * FROM prospects WHERE status='queued' "
                        "ORDER BY CASE verdict WHEN 'GO' THEN 0 ELSE 1 END, score DESC").fetchall()
    stamp = today().isoformat()
    if fmt == "md":
        path = OUTPUT_DIR / f"queue-{stamp}.md"
        path.write_text("\n\n---\n\n".join(card(r) for r in rows) or "Queue empty.", encoding="utf-8")
        return path, len(rows)
    path = OUTPUT_DIR / f"queue-{stamp}.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["verdict", "score", "domain", "name", "url", "phones", "emails", "contacts", "locations", "flags", "reasons"])
        for r in rows:
            res = jload(r["research"], {})
            reasons = jload(r["reasons"], [])
            w.writerow([r["verdict"], r["score"], r["domain"], r["name"], r["url"],
                        "; ".join(res.get("phones", [])), "; ".join(res.get("emails", [])),
                        "; ".join(res.get("contacts", [])), "; ".join(res.get("us_locations", []) + res.get("ca_locations", [])),
                        " | ".join(x for x in reasons if not x.startswith(("+", "-"))),
                        " | ".join(x for x in reasons if x.startswith(("+", "-")))])
    return path, len(rows)


def stats(conn):
    from .learn import hit_rates
    from .db import latest_model
    by_status = dict(conn.execute("SELECT status, COUNT(*) FROM prospects GROUP BY status").fetchall())
    by_verdict = dict(conn.execute("SELECT verdict, COUNT(*) FROM prospects WHERE verdict IS NOT NULL GROUP BY verdict").fetchall())
    m = latest_model(conn)
    lines = ["Pipeline: " + ", ".join(f"{k}={v}" for k, v in sorted(by_status.items())),
             "Verdicts: " + ", ".join(f"{k}={v}" for k, v in sorted(by_verdict.items())),
             f"Model: v{m['version']} ({m['created_at']}, {m['n_pos']} wins / {m['n_neg']} losses, GO≥{m['go']}, SKIP<{m['skip']})"
             if m else "Model: v0 (starting weights from config/icp.json — log outcomes, then run `learn`)"]
    hr = hit_rates(conn)
    if hr:
        lines.append("Win rate by the verdict it had when you logged the outcome (is the filter working?):")
        for v in ("GO", "MAYBE", "SKIP"):
            if v in hr:
                wins, n = hr[v]
                lines.append(f"  {v:5} {wins}/{n} = {wins / n:.0%}")
    terms = [r[0] for r in conn.execute("SELECT term FROM learned_terms WHERE active=1")]
    if terms:
        lines.append("Learned terms in use: " + ", ".join(terms))
    return "\n".join(lines)


def suggest_searches(conn, icp, n=10):
    """Lookalike search ideas from what's winning, to feed new leads back in."""
    from .score import current_model
    model = current_model(conn, icp)
    words = {
        "manufacturer": "manufacturer", "distributor": "distributor", "food_producer": "food producer",
        "flatbed_oversize": "steel fabricator", "reefer": "frozen food", "retail_dealers": "dealer network",
        "hazmat": "chemical manufacturer", "palletized": "palletized", "cross_border": "Mexico Canada",
    }
    top = [f for w, f in sorted(((w, f) for f, w in model["weights"].items()), reverse=True) if w > 0][:8]
    cities = {}
    for r in conn.execute("SELECT research FROM prospects WHERE last_outcome IN "
                          "('replied','meeting','quote_requested','load_booked')"):
        for loc in jload(r["research"], {}).get("us_locations", [])[:2]:
            st = loc.split(",")[1].split()[0] if "," in loc else ""
            cities[st] = cities.get(st, 0) + 1
    states = [s for s, _ in sorted(cities.items(), key=lambda kv: -kv[1])][:3] or ["[your territory]"]
    out = []
    for f in top:
        term = f[5:] if f.startswith("term:") else words.get(f)
        if not term:
            continue
        for st in states:
            out.append(f'"{term}" {st} -broker -logistics -"freight forwarder"')
    return out[:n]
