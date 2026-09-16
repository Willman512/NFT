"""The office itself.

This is where the personalities stop being decoration. A standup is a real
sequence of decisions, each owned by one member of staff and each wired to
something that actually happens:

    researcher  reads the ledger and says what it supports
    salesman    picks the next theme and price, and can refuse to ship
    banker      applies the survival rule and sets every budget
    manager     resolves the above into the list of ventures that run
    maker       runs them and reports what came out

The brain layer only narrates those decisions. Take the language model
away and the same things happen, described more tersely.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ..core.config import Config
from ..core.humanqueue import HumanQueue
from ..core.ledger import Ledger
from ..core.log import get_logger
from ..core.money import cents_to_str, usd
from ..core.orchestrator import Orchestrator
from ..core.survival import SurvivalOfficer, SurvivalRules, Verdict
from ..ventures.coloringbook import metadata as book_meta
from ..wallet import Wallet
from . import roles
from .brain import Brain, pick_brain
from .memory import KIND_ALERT, KIND_DECISION, KIND_NOTE, KIND_RESULT, KIND_RULING, Memory

log = get_logger("office")

#: Finished books waiting on the user before the salesman stops the presses.
DEFAULT_BACKLOG_LIMIT = 3


@dataclass
class StandupReport:
    cycle: int
    ran: list[str] = field(default_factory=list)
    blocked: dict[str, str] = field(default_factory=dict)
    verdicts: dict[str, str] = field(default_factory=dict)
    remarks: list[dict] = field(default_factory=list)
    halted: bool = False

    def to_dict(self) -> dict:
        return {
            "cycle": self.cycle,
            "ran": self.ran,
            "blocked": self.blocked,
            "verdicts": self.verdicts,
            "remarks": self.remarks,
            "halted": self.halted,
        }


class Office:
    def __init__(
        self,
        cfg: Config,
        *,
        ledger: Ledger | None = None,
        brain: Brain | None = None,
        orchestrator: Orchestrator | None = None,
    ):
        self.cfg = cfg
        self.ledger = ledger or Ledger(cfg.ledger_path)
        self.options = cfg.office.as_options()
        # One officer, shared with the treasurer, so the rules the banker
        # applies are the same ones the capital allocator respects.
        self.survival = SurvivalOfficer(self.ledger, _survival_rules(self.options))
        self.orch = orchestrator or Orchestrator(cfg, ledger=self.ledger, survival=self.survival)
        # Share one connection so the whole business is a single file.
        self.ledger = self.orch.ledger
        self.memory = Memory(self.ledger)
        self.queue = HumanQueue(self.ledger)
        self.brain = brain or pick_brain(self.options)
        self.wallet = Wallet(
            address=cfg.office.wallet_address or _venture_opt(cfg, "memecoin", "wallet_address"),
            keyfile=cfg.office.wallet_keyfile or _venture_opt(cfg, "memecoin", "wallet_keyfile"),
            rpc_url=cfg.office.rpc_url or "https://api.mainnet-beta.solana.com",
            allow_spend=False,  # the office view never needs spend rights
        )

    # ------------------------------------------------------------------
    # what everyone can see
    # ------------------------------------------------------------------
    def context(self, *, include_wallet: bool = True) -> dict[str, Any]:
        snap = self.orch.snapshot()
        ventures = []
        for v in snap["ventures"]:
            st = self.survival.status(v["name"])
            ventures.append(
                {
                    **v,
                    "survival_state": st.state,
                    "survival_strikes": st.strikes,
                    "survival_reason": st.reason,
                    "in_grace": self.survival.in_grace(v["name"]),
                }
            )

        return {
            "treasury": {
                "seed_cents": snap["seed_cents"],
                "cash_cents": snap["cash_cents"],
                "equity_cents": snap["equity_cents"],
                "net_pnl_cents": snap["net_pnl_cents"],
                "floor_cents": snap["floor_cents"],
                "halted": snap["halted"],
                "halt_reason": snap["halt_reason"],
            },
            "ventures": ventures,
            "open_tasks": snap["open_tasks"],
            "tasks": [
                {
                    "id": t.id,
                    "title": t.title,
                    "body": t.body,
                    "venture": t.venture,
                    "artifact": t.artifact,
                    "url": t.url,
                    "payoff_cents": t.payoff_cents,
                }
                for t in self.queue.open_tasks(limit=10)
            ],
            "wallet": self.wallet.snapshot() if include_wallet else {},
            "trader": self._trader_context(),
            "sales": self._sales_context(),
            "recent": [r.to_dict() for r in self.memory.feed(limit=8)],
            "cycle": self.memory.cycle,
            "brain": self.brain.label,
            "staff": [roles.PERSONAS[k].to_dict() for k in roles.STANDUP_ORDER],
        }

    def _trader_context(self) -> dict:
        import json

        raw = self.ledger.get_state("venture:memecoin:stats")
        stats = {}
        if raw:
            try:
                stats = json.loads(raw)
            except ValueError:
                stats = {}
        venture = self.orch.ventures.get("memecoin")
        blockers: list[str] = []
        if venture is not None:
            from ..ventures.memecoin.trading import PaperBook

            book = PaperBook.from_json(self.ledger.get_state("venture:memecoin:book"))
            blockers = venture.live_blockers(book)  # type: ignore[attr-defined]
        return {
            **stats,
            "mode": _venture_opt(self.cfg, "memecoin", "mode") or "paper",
            "live_blockers": blockers,
        }

    def _sales_context(self) -> dict:
        return {
            "theme": self.ledger.get_state("venture:coloringbook:theme_hint", "") or "unset",
            "price": float(self.ledger.get_state("venture:coloringbook:price_hint", "") or 7.99),
            "reason": self.ledger.get_state("office:sales_reason", "") or "",
            "backlog": self.queue.count_open(),
        }

    # ------------------------------------------------------------------
    # the standup
    # ------------------------------------------------------------------
    def standup(self, *, force: bool = False, dry_run: bool = False) -> StandupReport:
        cycle = self.memory.next_cycle()
        report = StandupReport(cycle=cycle)
        ctx = self.context()
        log.info("standup #%d", cycle)

        if ctx["treasury"]["halted"]:
            report.halted = True
            self._say(
                "manager",
                {"kind": "finding", "summary":
                    f"We are halted: {ctx['treasury']['halt_reason']}. "
                    f"Nothing runs until you resume us."},
                cycle,
                kind=KIND_ALERT,
            )
            return report

        # 1. the researcher reads the record ------------------------------
        proposals = self._researcher_pass(ctx, cycle)

        # 2. the salesman sets the offer ----------------------------------
        self._salesman_pass(ctx, cycle, proposals)

        # 3. the banker judges and funds ----------------------------------
        self._banker_pass(ctx, cycle, proposals, report)

        # 4. the manager resolves it ---------------------------------------
        approved = [name for name, p in proposals.items() if p["approved"]]
        for name, p in proposals.items():
            if not p["approved"]:
                report.blocked[name] = p["reason"]
        self._say(
            "manager",
            {
                "kind": "decision",
                "running": approved,
                "reason": "; ".join(f"{n} held back because {r}" for n, r in report.blocked.items()),
            },
            cycle,
            kind=KIND_DECISION,
        )

        # 5. the maker runs them -------------------------------------------
        if approved and not dry_run:
            result = self.orch.run_cycle(force=force, only=set(approved))
            for run in result.runs:
                if not run.ran:
                    if run.skipped_reason:
                        report.blocked.setdefault(run.name, run.skipped_reason)
                    continue
                report.ran.append(run.name)
                self.survival.note_tick(run.name)
                if run.error:
                    self._say(
                        "maker",
                        {"kind": "result", "summary": f"{run.name} fell over. Last line: "
                         f"{run.error.strip().splitlines()[-1][:160]}"},
                        cycle,
                        kind=KIND_ALERT,
                    )
                elif run.result is not None:
                    for note in run.result.notes:
                        self._say("maker", {"kind": "result", "summary": f"{run.name}: {note}"}, cycle,
                                  kind=KIND_RESULT)
                    if run.result.net_cents:
                        self._say(
                            "banker",
                            {"kind": "finding", "summary":
                                f"{run.name} moved {cents_to_str(run.result.net_cents)} this run."},
                            cycle,
                        )
        elif dry_run:
            self._say("maker", {"kind": "result", "summary": "Dry run: nothing was executed."}, cycle)

        report.remarks = [r.to_dict() for r in self.memory.feed(limit=40) if r.cycle == cycle][::-1]
        return report

    # -- individual passes ------------------------------------------------
    def _researcher_pass(self, ctx: dict, cycle: int) -> dict[str, dict]:
        proposals: dict[str, dict] = {}
        for v in ctx["ventures"]:
            name = v["name"]
            if not v["enabled"] or not v["loaded"]:
                continue
            if v["survival_state"] == "eliminated":
                proposals[name] = {"approved": False, "reason": "it was eliminated"}
                continue

            if v["expense_cents"] == 0 and v["income_cents"] == 0:
                summary = f"{name} has no record yet — nothing earned, nothing spent."
            else:
                summary = (
                    f"{name} has brought in {cents_to_str(v['income_cents'])} against "
                    f"{cents_to_str(v['expense_cents'])} spent"
                    + (f", {v['payback_ratio']:.2f}x back." if v["expense_cents"] else ".")
                )
            if v["in_grace"]:
                summary += " Still inside its grace period, so it proves nothing yet."
            self._say("researcher", {"kind": "finding", "summary": summary}, cycle)
            proposals[name] = {"approved": True, "reason": ""}

        trader = ctx.get("trader") or {}
        if trader.get("closed_trades", 0) >= 10:
            self._say(
                "researcher",
                {"kind": "finding", "summary":
                    f"The trader is {trader['closed_trades']} trades in at "
                    f"${trader.get('expectancy_usd', 0):.4f} expectancy a trade. "
                    + ("That is a real sample and it is negative."
                       if trader.get("expectancy_usd", 0) < 0 and trader["closed_trades"] >= 50
                       else "Too early to call.")},
                cycle,
            )
        return proposals

    def _salesman_pass(self, ctx: dict, cycle: int, proposals: dict[str, dict]) -> None:
        if "coloringbook" not in proposals:
            return
        backlog = ctx["open_tasks"]
        limit = int(self.options.get("backlog_limit", DEFAULT_BACKLOG_LIMIT))

        if backlog >= limit:
            reason = (
                f"there are already {backlog} finished books waiting on you to upload; "
                f"making a fourth does not sell the first three"
            )
            proposals["coloringbook"] = {"approved": False, "reason": reason}
            self._say(
                "salesman",
                {"kind": "finding", "summary":
                    f"Holding production. {backlog} books are sitting in the queue unpublished. "
                    f"Clear those and I'll start the presses again."},
                cycle,
                kind=KIND_ALERT,
            )
            return

        # Rotate themes so the catalogue is not five versions of one book.
        used = (self.ledger.get_state("office:themes_used", "") or "").split(",")
        used = [u for u in used if u]
        pool = [t for t in book_meta.THEMES if t not in used] or list(book_meta.THEMES)
        theme = pool[cycle % len(pool)]
        price = _price_for(theme)

        self.ledger.set_state("venture:coloringbook:theme_hint", theme)
        self.ledger.set_state("venture:coloringbook:price_hint", str(price))
        reason = (
            f"{book_meta.THEMES[theme].display} is untried in this catalogue."
            if theme not in used
            else f"Coming back round to {book_meta.THEMES[theme].display}."
        )
        self.ledger.set_state("office:sales_reason", reason)
        self.ledger.set_state("office:themes_used", ",".join(used + [theme])[-200:])
        self._say(
            "salesman",
            {"kind": "pitch", "theme": book_meta.THEMES[theme].display, "price": price, "reason": reason},
            cycle,
        )

    def _banker_pass(self, ctx: dict, cycle: int, proposals: dict[str, dict], report: StandupReport) -> None:
        for v in ctx["ventures"]:
            name = v["name"]
            if name not in proposals:
                continue
            verdict, reason = self.survival.review(name)
            report.verdicts[name] = verdict.value
            if verdict in {Verdict.STRIKE, Verdict.ELIMINATE, Verdict.PROMOTE}:
                self.survival.apply(name, verdict, reason)
                self._say(
                    "banker",
                    {"kind": "verdict", "verdict": verdict.value, "venture": name, "reason": reason},
                    cycle,
                    kind=KIND_RULING,
                )
            if verdict == Verdict.ELIMINATE:
                proposals[name] = {"approved": False, "reason": "the banker eliminated it"}

        targets = self.orch.target_allocations()
        for name, cents in targets.items():
            if name not in proposals or not proposals[name]["approved"]:
                continue
            venture = self.orch.ventures.get(name)
            if venture is not None and cents < venture.min_capital_cents:
                proposals[name] = {
                    "approved": False,
                    "reason": f"it needs {cents_to_str(venture.min_capital_cents)} and the "
                              f"treasury will only commit {cents_to_str(cents)}",
                }
                self._say(
                    "banker",
                    {"kind": "budget", "venture": name, "cents": 0,
                     "reason": f"it wants {cents_to_str(venture.min_capital_cents)} and I will not "
                               f"go past {cents_to_str(cents)}"},
                    cycle,
                )

    # ------------------------------------------------------------------
    # talking to people
    # ------------------------------------------------------------------
    def ask(self, persona_key: str, question: str) -> dict:
        persona = roles.get(persona_key)
        self.memory.add_chat(persona_key, "user", question)
        history = [m.to_dict() for m in self.memory.thread(persona_key)]
        answer = self.brain.reply(persona, question, self.context(), history)
        self.memory.add_chat(persona_key, "staff", answer)
        return {"persona": persona_key, "question": question, "answer": answer, "ts": time.time()}

    def _say(self, persona_key: str, substance: dict, cycle: int, kind: str = KIND_NOTE) -> None:
        persona = roles.get(persona_key)
        text = self.brain.render(persona, substance)
        self.memory.say(persona_key, text, kind=kind, meta=substance, cycle=cycle)

    # ------------------------------------------------------------------
    def record_income(self, venture: str, amount_usd: float, memo: str) -> int:
        """Book real money that arrived outside the office (a KDP payout)."""
        cents = usd(amount_usd)
        self.ledger.record_revenue(venture, cents, memo)
        self.memory.say(
            "banker",
            f"Booked {cents_to_str(cents)} against {venture}: {memo}.",
            kind=KIND_RULING,
            cycle=self.memory.cycle,
        )
        return cents

    def close(self) -> None:
        self.orch.close()


def _venture_opt(cfg: Config, venture: str, key: str) -> Any:
    vc = cfg.ventures.get(venture)
    return vc.options.get(key) if vc else None


def _survival_rules(options: dict) -> SurvivalRules:
    return SurvivalRules(
        grace_ticks=int(options.get("grace_ticks", 8)),
        grace_hours=float(options.get("grace_hours", 24)),
        min_profit_cents=usd(options.get("min_profit_usd", 1.0)),
        max_strikes=int(options.get("max_strikes", 3)),
    )


def _price_for(theme: str) -> float:
    """List price by theme. Puzzle books carry a little more than art books."""
    return {"mazes": 8.99, "botanical": 6.99}.get(theme, 7.99)
