#!/usr/bin/env python3
"""TQL prospecting pipeline — ever filtering, ever improving.

  python prospect.py add https://acme-steel.com           # or: add leads.csv
  python prospect.py cycle                                # research new leads, rescore, filter
  python prospect.py queue                                # who to call today
  python prospect.py card acme-steel.com                  # full research card
  python prospect.py today --drafts                       # the day's call/email plan
  python prospect.py email acme-steel.com                 # next email in the sequence (you send it)
  python prospect.py outcome acme-steel.com contacted     # log what happened
  python prospect.py learn                                # retrain on outcomes
  python prospect.py stats | suggest | export
"""
import argparse
import json
import os
import sys

from pipeline import db
from pipeline.config import load_icp, load_policy
from pipeline.email import draft
from pipeline.learn import learn
from pipeline.pipeline import (OUTCOMES, add_from_csv, add_lead, apply_filters, gate_all, queue, record_outcome,
                               rescore_all, research_pending)
from pipeline.report import card, export, queue_table, stats, suggest_searches
from pipeline.today import next_touch, plan, write_plan
from pipeline import discover as disc
from pipeline import forecast
from pipeline.util import normalize_domain


def cmd_add(conn, a):
    for item in a.items:
        if item.lower().endswith(".csv"):
            added, dupes = add_from_csv(conn, item, a.source)
            print(f"{item}: added {added}, skipped {dupes} duplicates")
        else:
            dom, status = add_lead(conn, item, a.name or "", a.source or "manual", a.notes or "")
            print(f"{dom}: {status}")


def cmd_cycle(conn, a):
    icp, policy = load_icp(), load_policy()
    print("1/4 Researching new leads…")
    t = research_pending(conn, icp, policy, limit=a.limit, retry_errors=a.retry_errors)
    print(f"    {t or 'nothing new'}")
    if a.learn:
        print("2/4 Learning from outcomes…")
        _print_learn(learn(conn, icp, note="auto (cycle)"))
    else:
        print("2/4 Learning skipped (pass --learn to retrain each cycle)")
    print("3/4 Rescoring everything with the latest model…")
    rescore_all(conn, icp, policy)
    print("    Checking requirements: no own fleet (FMCSA), ships interstate, uses outside carriers…")
    print(f"    {gate_all(conn, policy)}")
    print("4/4 Filtering…")
    print(f"    {apply_filters(conn, policy)}")
    print()
    print(queue_table(queue(conn, limit=a.top)))


def cmd_research(conn, a):
    icp, policy = load_icp(), load_policy()
    if a.domains:
        from pipeline.fetch import Fetcher
        from pipeline.pipeline import research_one
        f = Fetcher(policy)
        for d in a.domains:
            dom = normalize_domain(d)
            if not db.get_prospect(conn, dom):
                add_lead(conn, d)
            print(f"{dom}: {research_one(conn, dom, icp, policy, f)}")
    else:
        if a.all:  # re-crawl everyone not yet contacted (e.g. after improving extraction)
            conn.execute("UPDATE prospects SET status='new' WHERE touches=0 AND last_outcome IS NULL "
                         "AND status IN ('researched','queued','skipped','error')")
            conn.commit()
        print(research_pending(conn, icp, policy, limit=a.limit, retry_errors=a.retry_errors))
    gate_all(conn, policy, log=lambda *_: None)  # downloads FMCSA data for any new states, then checks requirements
    apply_filters(conn, policy)


def cmd_queue(conn, a):
    print(queue_table(queue(conn, a.verdict, a.top)))


def _row(conn, d):
    r = db.get_prospect(conn, normalize_domain(d))
    if not r:
        sys.exit(f"{d} not found. Add it with: python prospect.py add {d}")
    return r


def cmd_card(conn, a):
    for d in a.domains:
        print(card(_row(conn, d)))
        print()


def cmd_email(conn, a):
    r = _row(conn, a.domain)
    if not r["research"]:
        sys.exit("Research it first: python prospect.py research " + a.domain)
    if r["status"] == "suppressed":
        sys.exit(f"Not drafting: {r['domain']} is suppressed ({r['filter_reason']}).")
    touch = a.touch or next_touch(r, load_policy())
    text, warnings = draft(r, a.phone, touch)
    print(text)
    if warnings:
        print("\n# Check before sending:\n" + "\n".join(f"#  - {w}" for w in warnings))
    print("\n# After sending from your TQL email, log it: python prospect.py outcome", r["domain"], "contacted")


def cmd_today(conn, a):
    text = plan(conn, load_policy(), a.top, a.drafts, a.phone)
    print(text)
    print(f"\n(saved to {write_plan(text)})")


def cmd_discover(conn, a):
    if a.unmatched:
        import csv
        from pipeline.config import OUTPUT_DIR
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        path = OUTPUT_DIR / "discovered-no-website.csv"
        rows = disc.unmatched(conn)
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["company", "plants", "plant_locations", "naics", "google_this"])
            for r in rows:
                plants = json.loads(r["plants"])
                w.writerow([r["name"], len(plants), "; ".join(plants), " ".join(json.loads(r["naics"])),
                            f"{r['name']} {plants[0] if plants else ''}"])
        print(f"{len(rows)} companies with no website found yet -> {path}")
        print("Google each one; if it's a fit, `python prospect.py add <their site>`.")
        return
    states = [s.strip().upper() for s in a.states.split(",") if s.strip()]
    if a.from_csv:
        cos = disc.companies_from_csv(a.from_csv)
        sts = sorted({p.split(", ")[1][:2] for c in cos for p in c["plants"] if ", " in p}) or states
        print(f"{a.from_csv}: {len(cos)} companies. Checking fleets, finding websites…")
        res = disc.process_companies(conn, cos, sts, find_sites=not a.no_websites, limit=a.limit)
    elif a.forwarders:
        print(f"Pulling active FMCSA freight forwarders in {', '.join(states)}…")
        cos = disc.forwarder_companies(disc.fmcsa_forwarders(states))
        print(f"  {len(cos)} forwarders (big national 3PLs left out). Checking fleets, finding websites…")
        res = disc.process_companies(conn, cos, states, find_sites=not a.no_websites, limit=a.limit)
    else:
        naics = a.naics.split(",") if a.naics else [c for p in a.profile.split(",") for c in disc.PROFILES[p.strip()]]
        print(f"Searching EPA plant data: {len(states)} state(s) x {len(naics)} industries…")
        res = disc.discover(conn, states, naics, find_sites=not a.no_websites, limit=a.limit)
    print(f"\n{res['facilities']} active plants -> {res['companies']} companies "
          f"({res['known']} already seen, {res.get('fleet_dropped', 0)} dropped for their own FMCSA fleet). "
          f"New this run: {res['new']} — "
          f"{res['matched']} with a verified website (added to the pipeline), {res['unmatched']} without.")
    if res["matched"] and not a.no_research:
        print("Researching the new leads…")
        cmd_cycle(conn, argparse.Namespace(limit=None, retry_errors=False, learn=False, top=a.top))


def cmd_desk_export(conn, a):
    from pipeline import desk
    from pipeline.config import OUTPUT_DIR
    existing = []
    for src in a.desk or []:
        existing += desk.load_desk(src)
    backup, skipped = desk.export(conn, existing, tuple(v.upper() for v in a.verdict.split(",")), a.limit)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"prospecting-desk-import-{backup['at'][:10]}.json"
    path.write_text(json.dumps(backup, indent=1), encoding="utf-8")
    if not a.dry_run:
        desk.mark_exported(conn, backup)
    print(f"Wrote {len(backup['companies'])} researched prospects -> {path}")
    print(f"Skipped {skipped} already in your Desk." if existing else
          "Tip: pass --desk <your Desk backup.json> so companies already in the Desk are skipped.")
    print("In Prospecting Desk: Settings > Restore from backup > pick this file. It adds them; nothing else changes.")


def cmd_desk_import(conn, a):
    from pipeline import desk
    res = desk.import_desk(conn, desk.load_desk(a.backup))
    apply_filters(conn)
    print(f"{res['companies']} Desk companies: {res['matched']} matched pipeline leads, {res['added']} added as Desk-only.")
    print(f"New touches: {res['touches']} · new outcomes: {res['outcomes'] or 'none'}")
    print(forecast.render(forecast.plan(conn, load_policy())))


def cmd_held(conn, a):
    """Leads held back by the requirements, with the reason. Export: outputs/held.csv."""
    import csv
    from pipeline.config import OUTPUT_DIR
    rows = conn.execute("SELECT * FROM prospects WHERE status='held' ORDER BY score DESC").fetchall()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / "held.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["domain", "name", "verdict", "score", "own fleet", "interstate", "outside carriers"])
        for r in rows:
            g = json.loads(r["gates"] or "{}")
            w.writerow([r["domain"], r["name"], r["verdict"], r["score"]] +
                       [f"{g.get(k, ['?', ''])[0]}: {g.get(k, ['', ''])[1]}" for k in ("fleet", "interstate", "carriers")])
    by = {}
    for r in rows:
        g = json.loads(r["gates"] or "{}")
        for k in ("fleet", "interstate", "carriers"):
            st = g.get(k, ["unknown"])[0]
            if st != "pass":
                by[f"{k} {st}"] = by.get(f"{k} {st}", 0) + 1
    print(f"{len(rows)} leads held back. Why: {by}")
    for r in rows[:a.top]:
        print(f"  {r['domain']:32} {r['filter_reason'][:110]}")
    print(f"Full list: {path}")


def cmd_plan(conn, a):
    print(forecast.render(forecast.plan(conn, load_policy())))


def cmd_outcome(conn, a):
    record_outcome(conn, a.domain, a.outcome, a.note or "")
    apply_filters(conn)
    print(f"Logged {a.outcome} for {normalize_domain(a.domain)}.")


def _print_learn(rep):
    print(f"    Labeled: {rep['n_pos']} wins / {rep['n_neg']} losses")
    if not rep["trained"]:
        print("    " + rep.get("reason", "Not trained."))
    else:
        print(f"    New model saved. GO ≥ {rep['old_go']} → {rep['go']}, SKIP < {rep['old_skip']} → {rep['skip']}")
        for delta, f, old, new in rep["changes"]:
            print(f"      {f:28} {old:+.2f} → {new:+.2f}")
    if rep["terms"]:
        print("    Words on winners' sites, rare on losers' (candidates for new signals):")
        for lo, t, pos, neg in rep["terms"]:
            print(f"      {t:28} log-odds {lo:+.2f}  (wins {pos}, losses {neg})")
    if rep["adopted"]:
        print(f"    Adopted as signals: {', '.join(rep['adopted'])}")


def cmd_learn(conn, a):
    icp = load_icp()
    rep = learn(conn, icp, adopt_terms=a.adopt_terms, note=a.note or "manual")
    _print_learn(rep)
    rescore_all(conn, icp)
    print(apply_filters(conn))


def cmd_remove(conn, a):
    """Delete a lead entered by mistake (wrong domain, typo). Use `outcome … bad_fit` for real bad fits."""
    for d in a.domains:
        dom = normalize_domain(d)
        n = conn.execute("DELETE FROM prospects WHERE domain=?", (dom,)).rowcount
        conn.execute("DELETE FROM outcomes WHERE domain=?", (dom,))
        print(f"{dom}: {'removed' if n else 'not found'}")
    conn.commit()


def cmd_terms(conn, a):
    if a.disable:
        conn.execute("UPDATE learned_terms SET active=0 WHERE term=?", (a.disable,))
        conn.commit()
        rescore_all(conn, load_icp())
    for r in conn.execute("SELECT * FROM learned_terms ORDER BY active DESC, log_odds DESC"):
        print(f"{'on ' if r['active'] else 'off'}  {r['term']:30} log-odds {r['log_odds']:+.2f}  added {r['added_at'][:10]}")


def cmd_stats(conn, a):
    print(stats(conn))


def cmd_suggest(conn, a):
    print("Search ideas based on what's scoring and winning (paste into Google/Maps):")
    for s in suggest_searches(conn, load_icp(), a.n):
        print("  " + s)


def cmd_export(conn, a):
    path, n = export(conn, a.format)
    print(f"Wrote {n} prospects to {path}")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", help="path to sqlite db (default data/prospects.db)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("add", help="add lead URLs or a CSV of leads")
    s.add_argument("items", nargs="+")
    s.add_argument("--name"), s.add_argument("--source"), s.add_argument("--notes")
    s.set_defaults(fn=cmd_add)

    s = sub.add_parser("cycle", help="research new → (learn) → rescore → filter → show queue")
    s.add_argument("--limit", type=int), s.add_argument("--top", type=int, default=25)
    s.add_argument("--learn", action="store_true"), s.add_argument("--retry-errors", action="store_true")
    s.set_defaults(fn=cmd_cycle)

    s = sub.add_parser("research", help="research pending leads, or specific domains (re-research)")
    s.add_argument("domains", nargs="*"), s.add_argument("--limit", type=int)
    s.add_argument("--all", action="store_true", help="re-crawl every lead you haven't contacted yet")
    s.add_argument("--retry-errors", action="store_true")
    s.set_defaults(fn=cmd_research)

    s = sub.add_parser("queue", help="ranked call/email list")
    s.add_argument("--verdict", choices=["GO", "MAYBE", "go", "maybe"]), s.add_argument("--top", type=int, default=50)
    s.set_defaults(fn=cmd_queue)

    s = sub.add_parser("card", help="full research card(s)")
    s.add_argument("domains", nargs="+")
    s.set_defaults(fn=cmd_card)

    s = sub.add_parser("email", help="draft an intro email (never sends)")
    s.add_argument("domain"), s.add_argument("--phone", default=os.environ.get("TQL_PHONE", "[Phone]"))
    s.add_argument("--touch", type=int, choices=[1, 2, 3], help="default: next touch in the sequence")
    s.set_defaults(fn=cmd_email)

    s = sub.add_parser("today", help="daily plan: follow-ups due, new GOs with call openers, MAYBEs")
    s.add_argument("--top", type=int, help="override the quota from the customer plan")
    s.add_argument("--drafts", action="store_true")
    s.add_argument("--phone", default=os.environ.get("TQL_PHONE", "[Phone]"))
    s.set_defaults(fn=cmd_today)

    s = sub.add_parser("outcome", help="log what happened: " + ", ".join(OUTCOMES))
    s.add_argument("domain"), s.add_argument("outcome", choices=OUTCOMES), s.add_argument("--note")
    s.set_defaults(fn=cmd_outcome)

    s = sub.add_parser("learn", help="retrain weights from outcomes, mine new terms")
    s.add_argument("--adopt-terms", type=int, default=0, help="turn the top N mined terms into scoring signals")
    s.add_argument("--note")
    s.set_defaults(fn=cmd_learn)

    s = sub.add_parser("remove", help="delete leads entered by mistake (not for bad fits: use outcome)")
    s.add_argument("domains", nargs="+")
    s.set_defaults(fn=cmd_remove)

    s = sub.add_parser("discover", help="find new shippers from EPA plant data (by state + industry)")
    s.add_argument("--states", default="OH,KY,IN", help="comma list, e.g. GA,AL,FL")
    s.add_argument("--profile", default="flatbed,reefer,dryvan", help="flatbed, reefer, dryvan (comma list)")
    s.add_argument("--naics", help="exact NAICS codes instead of a profile, e.g. 332312,327390")
    s.add_argument("--limit", type=int, help="max new companies this run")
    s.add_argument("--top", type=int, default=25)
    s.add_argument("--no-websites", action="store_true", help="just list companies, skip website lookup")
    s.add_argument("--no-research", action="store_true")
    s.add_argument("--unmatched", action="store_true", help="export companies with no website found (call list)")
    s.add_argument("--forwarders", action="store_true", help="find freight forwarders (active FMCSA FF authority)")
    s.add_argument("--from-csv", help="find websites for any list: columns name, city, state (phone, website optional)")
    s.set_defaults(fn=cmd_discover)

    s = sub.add_parser("desk-export", help="researched leads as a Prospecting Desk backup file (Restore from backup)")
    s.add_argument("--desk", action="append", help="your Desk backup .json (or the app .zip) to skip companies you have")
    s.add_argument("--verdict", default="GO,MAYBE"), s.add_argument("--limit", type=int)
    s.add_argument("--dry-run", action="store_true", help="write the file but don't mark leads as sent")
    s.set_defaults(fn=cmd_desk_export)

    s = sub.add_parser("desk-import", help="read a Prospecting Desk backup: your calls and stages become outcomes")
    s.add_argument("backup")
    s.set_defaults(fn=cmd_desk_import)

    s = sub.add_parser("held", help="leads held back by the fleet / interstate / outside-carrier requirements")
    s.add_argument("--top", type=int, default=25)
    s.set_defaults(fn=cmd_held)

    sub.add_parser("plan", help="customer plan: prospects needed for ~95% odds of a new customer, daily quota"
                   ).set_defaults(fn=cmd_plan)

    s = sub.add_parser("terms", help="list learned terms (or --disable one)")
    s.add_argument("--disable")
    s.set_defaults(fn=cmd_terms)

    sub.add_parser("stats", help="pipeline + model scoreboard").set_defaults(fn=cmd_stats)
    s = sub.add_parser("suggest", help="lookalike search queries for new leads")
    s.add_argument("-n", type=int, default=10)
    s.set_defaults(fn=cmd_suggest)
    s = sub.add_parser("export", help="write the queue to outputs/ (csv or md)")
    s.add_argument("--format", choices=["csv", "md"], default="csv")
    s.set_defaults(fn=cmd_export)

    a = p.parse_args(argv)
    conn = db.connect(a.db)
    a.fn(conn, a)


if __name__ == "__main__":
    main()
