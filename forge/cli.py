"""Command line entry point.

``forge office`` is the one most people want; everything else here exists
so the same office can be driven from a terminal, a cron job, or a service
manager without the web UI running.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

from .core.config import Config, ConfigError, load_config
from .core.log import get_logger, setup_logging
from .core.money import cents_to_str
from .core.survival import SurvivalOfficer
from .staff import roles
from .staff.office import Office

log = get_logger("cli")


# ----------------------------------------------------------------------
# small presentation helpers; no third-party table library needed
# ----------------------------------------------------------------------
def table(rows: list[list[str]], headers: list[str]) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    line = "  ".join(h.upper().ljust(widths[i]) for i, h in enumerate(headers))
    out = [line, "  ".join("-" * w for w in widths)]
    for row in rows:
        out.append("  ".join(str(c).ljust(widths[i]) for i, c in enumerate(row)))
    return "\n".join(out)


def _office(args: argparse.Namespace) -> Office:
    cfg = _config(args)
    setup_logging(cfg.workdir, verbose=getattr(args, "verbose", False))
    return Office(cfg)


def _config(args: argparse.Namespace) -> Config:
    try:
        return load_config(getattr(args, "config", None))
    except ConfigError as exc:
        print(f"config problem: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


# ----------------------------------------------------------------------
# commands
# ----------------------------------------------------------------------
def cmd_init(args: argparse.Namespace) -> int:
    target = Path(args.path or "forge.yaml")
    if target.exists() and not args.force:
        print(f"{target} already exists. Use --force to overwrite it.")
        return 1
    example = Path(__file__).parent.parent / "forge.example.yaml"
    if not example.exists():
        print("forge.example.yaml is missing from the installation", file=sys.stderr)
        return 1
    shutil.copy(example, target)
    print(f"Wrote {target}.")
    print("Edit the seed, paste your wallet address, then run:  forge office")
    return 0


def cmd_office(args: argparse.Namespace) -> int:
    from .core.ledger import Ledger
    from .web import auth, serve

    cfg = _config(args)
    setup_logging(cfg.workdir, verbose=args.verbose)
    if args.port:
        cfg.office.port = args.port
    if args.host:
        cfg.office.host = args.host
    elif args.lan:
        cfg.office.host = "0.0.0.0"

    exposed = not auth.is_loopback(cfg.office.host)

    if args.show_link:
        with Ledger(cfg.ledger_path) as ledger:
            token = auth.resolve_token(cfg, ledger)
        print(auth.link(cfg.office.host, cfg.office.port, token))
        return 0

    if exposed:
        with Ledger(cfg.ledger_path) as ledger:
            token = auth.resolve_token(cfg, ledger)
        url = auth.link(cfg.office.host, cfg.office.port, token)
        print()
        print("  The office is open to your network.")
        print(f"  {url}")
        print()
        print("  Open that on your phone. The link carries the key, so treat it")
        print("  like one: anyone who has it can talk to your staff and see your")
        print("  wallet. It is plain HTTP, so it is only as private as the network")
        print("  you are on — for anywhere else, put it behind a tunnel instead.")
        print("  See the README, 'Getting to it from anywhere'.")
        print()
    else:
        print(f"  The office is at http://localhost:{cfg.office.port}/")
        print("  Only this machine can reach it. For your phone: forge office --lan")
        print()

    try:
        serve(cfg, open_browser=not args.no_browser and not exposed)
    except KeyboardInterrupt:
        print("\nClosed.")
    return 0


def cmd_idea(args: argparse.Namespace) -> int:
    """Put something in front of the staff and let them chew on it."""
    office = _office(args)
    handed_off = False
    try:
        words = list(args.text)
        if words and words[0] == "show":
            handed_off = True
            office.close()
            if len(words) != 2 or not words[1].isdigit():
                print('Usage: forge idea show <id>', file=sys.stderr)
                return 1
            return cmd_idea_show(argparse.Namespace(**{**vars(args), "id": int(words[1])}))

        body = " ".join(words).strip()
        if not body:
            print("Say what the idea is.", file=sys.stderr)
            return 1
        idea = office.propose(body)
        print(f"\nIdea {idea.id}: {idea.title}\n")
        result = office.vet(idea, office.memory.cycle)
        print(f"  {result['status'].upper()} — {result['reason']}\n")

        for review in office.ideas.reviews(idea.id):
            person = roles.PERSONAS.get(review.persona)
            name = person.name if person else review.persona
            print(f"  {person.avatar if person else '-'} {name:<14} {review.stance:<8} {review.headline}")
            for point in review.points:
                print(f"       - {point}")
            print()

        assignments = office.ideas.assignments(idea_id=idea.id)
        if assignments:
            print("  Who has what:")
            for a in assignments:
                mark = {"done": "x", "doing": "~", "blocked": "!", "todo": " "}[a.status]
                print(f"    [{mark}] {a.owner:<11} {a.title}")
            print(f"\n  They will work it at the next standup. Watch with:  forge idea show {idea.id}\n")
        return 0
    finally:
        if not handed_off:
            office.close()


def cmd_ideas(args: argparse.Namespace) -> int:
    office = _office(args)
    try:
        ideas = office.ideas.ideas(limit=args.limit)
        if not ideas:
            print("Nothing on the board. Add one with:  forge idea \"...\"")
            return 0
        rows = []
        for idea in ideas:
            tally = office.ideas.tally(idea.id)
            progress = office.ideas.progress(idea.id)
            total = sum(progress.values())
            rows.append([
                str(idea.id),
                idea.title[:52],
                idea.status,
                f"{tally['for']}/{tally['against']}/{tally['unsure']}",
                f"{progress['done']}/{total}" if total else "—",
                str(progress["blocked"]) if progress["blocked"] else "",
            ])
        print()
        print(table(rows, ["id", "idea", "status", "for/agst/uns", "done", "on you"]))
        print("\nOpen one with:  forge idea show <id>\n")
        return 0
    finally:
        office.close()


def cmd_idea_show(args: argparse.Namespace) -> int:
    office = _office(args)
    try:
        idea = office.ideas.idea(args.id)
        if idea is None:
            print(f"No idea {args.id}.", file=sys.stderr)
            return 1
        print(f"\n{idea.title}")
        print(f"  {idea.status.replace('_', ' ')} · raised by {idea.source}\n")
        for line in idea.body.splitlines():
            print(f"  > {line}")
        if idea.decision:
            print(f"\n  Decision: {idea.decision}")

        reviews = office.ideas.reviews(idea.id)
        if reviews:
            print("\n  What they said:")
            for review in reviews:
                person = roles.PERSONAS.get(review.persona)
                name = person.name if person else review.persona
                print(f"    {name:<14} {review.stance:<8} {review.headline}")

        assignments = office.ideas.assignments(idea_id=idea.id)
        if assignments:
            print("\n  The work:")
            for a in assignments:
                mark = {"done": "x", "doing": "~", "blocked": "!", "todo": " "}[a.status]
                waiting = f"  (waiting on {a.blocked_on})" if a.blocked_on else ""
                print(f"    [{mark}] {a.id:>3} {a.owner:<11} {a.title}{waiting}")
                for note in a.notes:
                    for line in note.splitlines():
                        print(f"            {line}")
            waiting = [a for a in assignments if a.status == "blocked"]
            if waiting:
                print(f"\n  Answer one with:  forge answer {waiting[0].id} \"...\"")
        print()
        return 0
    finally:
        office.close()


def cmd_answer(args: argparse.Namespace) -> int:
    office = _office(args)
    try:
        text = " ".join(args.text).strip()
        try:
            office.answer(args.id, text)
        except KeyError:
            print(f"No piece of work numbered {args.id}.", file=sys.stderr)
            return 1
        except ValueError as exc:
            print(f"{exc}", file=sys.stderr)
            return 1
        print(f"Noted against work {args.id}. They will pick it up from there.")
        return 0
    finally:
        office.close()


def cmd_standup(args: argparse.Namespace) -> int:
    office = _office(args)
    try:
        report = office.standup(force=True, dry_run=args.dry_run)
        print(f"\nStandup {report.cycle}\n")
        for remark in report.remarks:
            person = roles.PERSONAS.get(remark["persona"])
            name = person.name if person else remark["persona"]
            print(f"  {person.avatar if person else '-'} {name:<14} {remark['text']}")
        if report.blocked:
            print("\nHeld back:")
            for name, why in report.blocked.items():
                print(f"  {name}: {why}")
        print()
        return 0
    finally:
        office.close()


def cmd_run(args: argparse.Namespace) -> int:
    """Headless perpetual mode, for a service manager or a cron job."""
    office = _office(args)
    interval = (args.interval or office.cfg.office.standup_interval_minutes) * 60
    try:
        while True:
            report = office.standup(force=True)
            log.info(
                "standup %d: ran %s, held back %s",
                report.cycle,
                ", ".join(report.ran) or "nothing",
                ", ".join(report.blocked) or "nothing",
            )
            if args.once:
                return 0
            time.sleep(interval)
    except KeyboardInterrupt:
        return 0
    finally:
        office.close()


def cmd_status(args: argparse.Namespace) -> int:
    office = _office(args)
    try:
        ctx = office.context()
        t = ctx["treasury"]
        if args.json:
            print(json.dumps(ctx, indent=2, default=str))
            return 0

        print()
        print(f"  Seed        {cents_to_str(t['seed_cents'])}")
        print(f"  Equity      {cents_to_str(t['equity_cents'])}   "
              f"({cents_to_str(t['net_pnl_cents'])} net)")
        print(f"  Uncommitted {cents_to_str(t['cash_cents'])}")
        print(f"  Floor       {cents_to_str(t['floor_cents'])}"
              + ("   HALTED: " + (t["halt_reason"] or "") if t["halted"] else ""))
        print()

        rows = []
        for v in ctx["ventures"]:
            ratio = "—" if not v["expense_cents"] else f"{v['income_cents'] / v['expense_cents']:.2f}x"
            rows.append([
                v["name"],
                v["survival_state"] + (" (grace)" if v["in_grace"] and v["survival_state"] != "eliminated" else ""),
                cents_to_str(v["income_cents"]),
                cents_to_str(v["expense_cents"]),
                ratio,
                cents_to_str(v["float_cents"]),
            ])
        print(table(rows, ["venture", "standing", "earned", "spent", "payback", "float"]))

        print(f"\n  {ctx['open_tasks']} task(s) waiting on you.  Staff brain: {ctx['brain']}")
        w = ctx["wallet"]
        if w.get("address"):
            where = f"{w['sol']:.4f} SOL" if w.get("fetched_ok") else "balance unavailable"
            print(f"  Wallet ({w['mode']}): {w['address']}  {where}")
        print()
        return 0
    finally:
        office.close()


def cmd_tasks(args: argparse.Namespace) -> int:
    office = _office(args)
    try:
        if args.done or args.skip:
            task_id = args.done or args.skip
            status = "done" if args.done else "skipped"
            if office.queue.resolve(task_id, status):
                print(f"Task {task_id} marked {status}.")
                return 0
            print(f"No task {task_id}.", file=sys.stderr)
            return 1

        tasks = office.queue.open_tasks()
        if not tasks:
            print("Nothing is waiting on you.")
            return 0
        for t in tasks:
            print(f"\n[{t.id}] {t.title}")
            for line in t.body.splitlines():
                print(f"     {line}")
            if t.url:
                print(f"     {t.url}")
        print(f"\nMark one done with:  forge tasks --done {tasks[0].id}\n")
        return 0
    finally:
        office.close()


def cmd_earned(args: argparse.Namespace) -> int:
    office = _office(args)
    try:
        target = office.resolve_income_target(args.venture)
        if target is None:
            options = office.income_targets()
            print(f"Nothing here called {args.venture!r}. "
                  f"Try one of: {', '.join(options) or 'nothing yet'}", file=sys.stderr)
            return 1
        if args.amount <= 0:
            print("Amount must be positive.", file=sys.stderr)
            return 1
        cents = office.record_income(target, args.amount, args.memo)
        books = office.ledger.books(target)
        print(f"Booked {cents_to_str(cents)} to {target}.")
        print(f"It is now at {cents_to_str(books.income)} earned against "
              f"{cents_to_str(books.expense)} spent.")
        return 0
    finally:
        office.close()


def cmd_ask(args: argparse.Namespace) -> int:
    office = _office(args)
    try:
        if args.who not in roles.PERSONAS:
            print(f"No such member of staff. Try: {', '.join(roles.PERSONAS)}", file=sys.stderr)
            return 1
        person = roles.get(args.who)
        answer = office.ask(args.who, " ".join(args.question))
        print(f"\n{person.avatar} {person.name}, {person.title}\n")
        for line in answer["answer"].splitlines():
            print(f"  {line}")
        print()
        return 0
    finally:
        office.close()


def cmd_staff(args: argparse.Namespace) -> int:
    rows = [[p.avatar, p.key, p.name, p.title] for p in
            (roles.PERSONAS[k] for k in roles.STANDUP_ORDER)]
    print()
    print(table(rows, ["", "key", "name", "role"]))
    print("\nTalk to one with:  forge ask manager \"how are we doing?\"\n")
    return 0


def cmd_venture(args: argparse.Namespace) -> int:
    office = _office(args)
    try:
        officer = SurvivalOfficer(office.ledger, office.survival.rules)
        if args.action == "revive":
            officer.revive(args.name)
            print(f"{args.name} is back on probation with a clean sheet.")
        elif args.action == "unbench":
            office.orch.unbench(args.name)
            print(f"{args.name} will be tried again.")
        elif args.action == "resume":
            office.orch.resume()
            print("The office is no longer halted.")
        return 0
    finally:
        office.close()


def cmd_wallet(args: argparse.Namespace) -> int:
    office = _office(args)
    try:
        if args.create_hot:
            from .wallet import WalletError

            try:
                address = office.wallet.create_hot_wallet(args.create_hot)
            except WalletError as exc:
                print(f"{exc}", file=sys.stderr)
                return 1
            print(f"Created a hot wallet: {address}")
            print(f"Key file: {Path(args.create_hot).expanduser()} (mode 600 — back it up)")
            print("Put that path in office.wallet_keyfile. Only send it what you can lose.")
            return 0

        snap = office.wallet.snapshot()
        if not snap["address"]:
            print("No wallet configured. Put your public address in forge.yaml under "
                  "office.wallet_address.")
            return 0
        print(f"\n  Address   {snap['address']}")
        print(f"  Mode      {snap['mode']}"
              + ("  (this office cannot move the money)" if snap["mode"] == "watch" else ""))
        if snap["fetched_ok"]:
            usd = f"  (${snap['usd']:,.2f})" if snap["usd"] is not None else ""
            print(f"  Balance   {snap['sol']:.6f} SOL{usd}")
        else:
            print(f"  Balance   unavailable — {snap['error']}")
        print(f"  Explorer  {snap['explorer_url']}\n")
        return 0
    finally:
        office.close()


def cmd_preview(args: argparse.Namespace) -> int:
    import random

    from .ventures.coloringbook.art import GENERATORS
    from .ventures.coloringbook.preview import render_png

    kinds = [args.kind] if args.kind else list(GENERATORS)
    unknown = [k for k in kinds if k not in GENERATORS]
    if unknown:
        print(f"Unknown page type {unknown[0]!r}. Try: {', '.join(GENERATORS)}", file=sys.stderr)
        return 1

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    try:
        for kind in kinds:
            for i in range(args.count):
                seed = args.seed + i
                drawing = GENERATORS[kind](random.Random(seed), args.difficulty)
                path = render_png(drawing, out / f"{kind}-{seed}.png")
                print(f"  {path}")
    except RuntimeError as exc:
        print(f"{exc}", file=sys.stderr)
        return 1
    return 0


def cmd_selftest(args: argparse.Namespace) -> int:
    office = _office(args)
    try:
        venture = office.orch.ventures.get("memecoin")
        if venture is None:
            print("The trader is not enabled in this config.", file=sys.stderr)
            return 1
        try:
            result = venture.selftest()  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - this command exists to report failures
            print(f"Live plumbing is NOT reachable: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        print(f"Quote round trip fine: {json.dumps(result)}")
        print("That only proves the endpoint answers. It signed nothing and spent nothing.")
        return 0
    finally:
        office.close()


# ----------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="forge",
        description="A local office of bots that has to pay for itself.",
    )
    p.add_argument("-c", "--config", help="path to forge.yaml")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command")

    q = sub.add_parser("init", help="write a starter forge.yaml")
    q.add_argument("path", nargs="?")
    q.add_argument("--force", action="store_true")
    q.set_defaults(func=cmd_init)

    q = sub.add_parser("office", help="open the visual office (this is the main one)")
    q.add_argument("--port", type=int)
    q.add_argument("--lan", action="store_true",
                   help="let your other devices reach it, with a key in the link")
    q.add_argument("--host", help="bind to a specific address")
    q.add_argument("--show-link", action="store_true",
                   help="print the link with its key and exit")
    q.add_argument("--no-browser", action="store_true")
    q.set_defaults(func=cmd_office)

    # `forge idea show 3` reads better than a separate verb, but argparse
    # cannot have a subcommand and a greedy positional on the same parser,
    # so the split happens in cmd_idea instead.
    q = sub.add_parser("idea", help='put an idea to the staff, or "show <id>"')
    q.add_argument("text", nargs="+", help="the idea in your own words, or: show <id>")
    q.set_defaults(func=cmd_idea)

    q = sub.add_parser("ideas", help="what is on the board")
    q.add_argument("--limit", type=int, default=25)
    q.set_defaults(func=cmd_ideas)

    q = sub.add_parser("answer", help="answer a question the office put to you")
    q.add_argument("id", type=int)
    q.add_argument("text", nargs="+")
    q.set_defaults(func=cmd_answer)

    q = sub.add_parser("standup", help="hold one standup in the terminal")
    q.add_argument("--dry-run", action="store_true", help="decide but do not execute")
    q.set_defaults(func=cmd_standup)

    q = sub.add_parser("run", help="run standups forever, with no web UI")
    q.add_argument("--once", action="store_true")
    q.add_argument("--interval", type=float, help="minutes between standups")
    q.set_defaults(func=cmd_run)

    q = sub.add_parser("status", help="what the books say")
    q.add_argument("--json", action="store_true")
    q.set_defaults(func=cmd_status)

    q = sub.add_parser("tasks", help="what is waiting on you")
    q.add_argument("--done", type=int, metavar="ID")
    q.add_argument("--skip", type=int, metavar="ID")
    q.set_defaults(func=cmd_tasks)

    q = sub.add_parser("earned", help="book money that actually arrived")
    q.add_argument("venture", metavar="what", help="a venture name, or an idea id")
    q.add_argument("amount", type=float)
    q.add_argument("--memo", default="payment received")
    q.set_defaults(func=cmd_earned)

    q = sub.add_parser("ask", help="put a question to one of the staff")
    q.add_argument("who")
    q.add_argument("question", nargs="+")
    q.set_defaults(func=cmd_ask)

    q = sub.add_parser("staff", help="who works here")
    q.set_defaults(func=cmd_staff)

    q = sub.add_parser("venture", help="revive, unbench, or resume")
    q.add_argument("action", choices=["revive", "unbench", "resume"])
    q.add_argument("name", nargs="?", default="")
    q.set_defaults(func=cmd_venture)

    q = sub.add_parser("wallet", help="show the wallet, or create a hot one")
    q.add_argument("--create-hot", metavar="PATH")
    q.set_defaults(func=cmd_wallet)

    q = sub.add_parser("preview", help="render sample colouring pages as PNGs")
    q.add_argument("--kind")
    q.add_argument("--count", type=int, default=2)
    q.add_argument("--seed", type=int, default=1)
    q.add_argument("--difficulty", type=int, default=2, choices=[1, 2, 3])
    q.add_argument("--out", default="out/preview")
    q.set_defaults(func=cmd_preview)

    q = sub.add_parser("selftest", help="check the live trading endpoints without spending")
    q.set_defaults(func=cmd_selftest)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
