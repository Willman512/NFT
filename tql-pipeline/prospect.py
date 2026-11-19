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
import os
import sys

from pipeline import db
from pipeline.config import load_icp, load_policy
from pipeline.email import draft
from pipeline.learn import learn
from pipeline.pipeline import (OUTCOMES, add_from_csv, add_lead, apply_filters, queue, record_outcome,
                               rescore_all, research_pending)
from pipeline.report import card, export, queue_table, stats, suggest_searches
from pipeline.today import next_touch, plan, write_plan
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
    s.add_argument("--top", type=int, default=15), s.add_argument("--drafts", action="store_true")
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
