"""The treasurer.

One object owns the money. Ventures propose; the orchestrator disposes. It
funds them, runs them when they are due, books what they report, harvests
their profits back into the treasury, and pulls the plug on the whole swarm
if the account bleeds past the drawdown limit.
"""

from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..ventures import registry
from .config import Config
from .humanqueue import HumanQueue
from .ledger import Ledger
from .log import get_logger
from .money import cents_to_str, usd
from .notify import Notifier
from .survival import ELIMINATED, SurvivalOfficer
from .venture import TickContext, TickResult, Venture

log = get_logger("orchestrator")

#: Do not shuffle capital around for trivial amounts.
REBALANCE_THRESHOLD_CENTS = 100
#: A venture that throws this many times in a row is benched.
MAX_CONSECUTIVE_FAILURES = 5
HALTED_KEY = "treasury:halted"
SEED_REF = "seed:initial"


@dataclass
class VentureRun:
    name: str
    ran: bool
    result: TickResult | None = None
    error: str | None = None
    skipped_reason: str | None = None


@dataclass
class CycleReport:
    ts: float
    halted: bool = False
    halt_reason: str | None = None
    runs: list[VentureRun] = field(default_factory=list)
    equity_cents: int = 0
    net_pnl_cents: int = 0

    @property
    def any_ran(self) -> bool:
        return any(r.ran for r in self.runs)


class Orchestrator:
    def __init__(
        self,
        cfg: Config,
        ledger: Ledger | None = None,
        notifier: Notifier | None = None,
        survival: SurvivalOfficer | None = None,
    ):
        self.cfg = cfg
        self.ledger = ledger or Ledger(cfg.ledger_path)
        self.queue = HumanQueue(self.ledger)
        self.notifier = notifier or Notifier(cfg.notify, cfg.workdir)
        # The treasurer has to know who is dead, or the next rebalance quietly
        # funds a venture that will never run again.
        self.survival = survival or SurvivalOfficer(self.ledger)
        self.ventures: dict[str, Venture] = {}
        self._load_ventures()
        self._ensure_seeded()

    # ------------------------------------------------------------------
    # setup
    # ------------------------------------------------------------------
    def _load_ventures(self) -> None:
        for vc in self.cfg.enabled_ventures():
            try:
                venture = registry.build(vc.name, vc.options)
            except KeyError as exc:
                log.error("%s", exc)
                continue
            venture.validate()
            self.ventures[vc.name] = venture
        if not self.ventures:
            log.warning("no ventures loaded; the swarm will do nothing")

    def _ensure_seeded(self) -> None:
        """Post the seed exactly once, no matter how often Forge restarts."""
        want = usd(self.cfg.treasury.seed_usd)
        if self.ledger.seed_total == 0:
            self.ledger.seed(want, memo=f"seed capital {cents_to_str(want)}", ref=SEED_REF)
            log.info("treasury seeded with %s", cents_to_str(want))
        elif self.ledger.seed_total != want:
            # The config was edited after the fact; top up or note the shortfall
            # rather than silently disagreeing with the books.
            delta = want - self.ledger.seed_total
            if delta > 0:
                self.ledger.seed(delta, memo="additional funding", ref=f"seed:topup:{want}")
                log.info("topped treasury up by %s", cents_to_str(delta))
            else:
                log.warning(
                    "config seed %s is below the %s already recorded; leaving the books alone",
                    cents_to_str(want),
                    cents_to_str(self.ledger.seed_total),
                )

    # ------------------------------------------------------------------
    # risk
    # ------------------------------------------------------------------
    @property
    def halted(self) -> bool:
        return self.ledger.get_state(HALTED_KEY, "0") == "1"

    def halt(self, reason: str) -> None:
        if self.halted:
            return
        self.ledger.set_state(HALTED_KEY, "1")
        self.ledger.set_state("treasury:halt_reason", reason)
        log.error("HALTED: %s", reason)
        self.notifier.send("Forge halted", reason)

    def resume(self) -> None:
        self.ledger.set_state(HALTED_KEY, "0")
        self.ledger.set_state("treasury:halt_reason", "")
        log.info("resumed")

    @property
    def floor_cents(self) -> int:
        """Equity below this and the swarm stops trading."""
        seed = self.ledger.seed_total
        return int(seed * (1 - self.cfg.treasury.max_drawdown_pct / 100.0))

    def _check_drawdown(self) -> bool:
        equity = self.ledger.equity
        if equity < self.floor_cents:
            self.halt(
                f"equity {cents_to_str(equity)} fell below the floor "
                f"{cents_to_str(self.floor_cents)} "
                f"({self.cfg.treasury.max_drawdown_pct:.0f}% drawdown limit)"
            )
            return True
        return False

    # ------------------------------------------------------------------
    # capital allocation
    # ------------------------------------------------------------------
    def is_eliminated(self, name: str) -> bool:
        return self.survival.status(name).state == ELIMINATED

    def _score(self, name: str) -> float:
        """How much this venture has earned the right to more capital.

        Unproven ventures sit at 1.0 so they get a fair first try; proven
        earners climb, money-losers shrink but never reach zero, so a
        venture that turns around can claw its way back.
        """
        books = self.ledger.books(name)
        if books.expense <= 0:
            return 1.0
        return max(0.1, min(3.0, books.payback_ratio))

    def target_allocations(self) -> dict[str, int]:
        """How much float each venture should be holding right now."""
        reserve = int(self.ledger.seed_total * self.cfg.treasury.reserve_pct / 100.0)
        investable = max(0, self.ledger.equity - reserve)

        caps = {
            name: usd(self.cfg.venture(name).max_capital_usd)
            for name in self.ventures
            if self.cfg.venture(name).max_capital_usd > 0 and not self.is_eliminated(name)
        }
        if not caps or investable <= 0:
            return {name: 0 for name in self.ventures}

        scores = {name: self._score(name) for name in caps}
        total_score = sum(scores.values()) or 1.0

        targets = {name: 0 for name in self.ventures}
        for name, cap in caps.items():
            share = int(investable * scores[name] / total_score)
            targets[name] = min(cap, share)
        return targets

    def rebalance(self) -> None:
        """Move capital toward the targets, and sweep profits home."""
        targets = self.target_allocations()
        # Recall first so the cash is there to fund the others.
        for name, target in targets.items():
            held = self.ledger.balance(f"asset:float:{name}")
            excess = held - target
            if excess >= REBALANCE_THRESHOLD_CENTS:
                self.ledger.deallocate(name, excess)
                log.info("recalled %s from %s", cents_to_str(excess), name)
        for name, target in targets.items():
            held = self.ledger.balance(f"asset:float:{name}")
            shortfall = min(target - held, self.ledger.cash)
            if shortfall >= REBALANCE_THRESHOLD_CENTS:
                self.ledger.allocate(name, shortfall)
                log.info("allocated %s to %s", cents_to_str(shortfall), name)

    # ------------------------------------------------------------------
    # running
    # ------------------------------------------------------------------
    def _due(self, name: str, now: float) -> bool:
        interval = self.cfg.venture(name).interval_minutes * 60.0
        last = float(self.ledger.get_state(f"venture:{name}:last_run", "0") or 0)
        return (now - last) >= interval

    def _ctx(self, name: str, now: float, dry_run: bool) -> TickContext:
        prefix = f"venture:{name}:"
        return TickContext(
            now=now,
            budget_cents=self.ledger.balance(f"asset:float:{name}"),
            float_cents=self.ledger.balance(f"asset:float:{name}"),
            workdir=self.cfg.workdir / name,
            out_dir=self.cfg.out_dir / name,
            dry_run=dry_run,
            log=get_logger(name),
            get_state=lambda k, d=None, _p=prefix: self.ledger.get_state(_p + k, d),
            set_state=lambda k, v, _p=prefix: self.ledger.set_state(_p + k, v),
        )

    def run_cycle(
        self,
        *,
        force: bool = False,
        dry_run: bool = False,
        only: str | set[str] | None = None,
    ) -> CycleReport:
        """One pass over every venture that is due.

        ``only`` restricts the pass to one venture or a set of them, which is
        how the office runs just the ventures the standup approved.
        """
        allowed = {only} if isinstance(only, str) else only
        now = time.time()
        report = CycleReport(ts=now)

        if self._check_drawdown() or self.halted:
            report.halted = True
            report.halt_reason = self.ledger.get_state("treasury:halt_reason", "halted")
            log.warning("swarm is halted: %s", report.halt_reason)
            report.equity_cents = self.ledger.equity
            report.net_pnl_cents = self.ledger.net_pnl
            return report

        self.rebalance()

        for name, venture in self.ventures.items():
            if allowed is not None and name not in allowed:
                continue
            if self.is_eliminated(name):
                report.runs.append(VentureRun(name, ran=False, skipped_reason="eliminated"))
                continue
            if self._benched(name):
                report.runs.append(
                    VentureRun(name, ran=False, skipped_reason="benched after repeated failures")
                )
                continue
            if not force and not self._due(name, now):
                report.runs.append(VentureRun(name, ran=False, skipped_reason="not due yet"))
                continue

            ctx = self._ctx(name, now, dry_run)
            ctx.workdir.mkdir(parents=True, exist_ok=True)
            ctx.out_dir.mkdir(parents=True, exist_ok=True)

            if ctx.float_cents < venture.min_capital_cents:
                report.runs.append(
                    VentureRun(
                        name,
                        ran=False,
                        skipped_reason=(
                            f"needs {cents_to_str(venture.min_capital_cents)} to act, "
                            f"holds {cents_to_str(ctx.float_cents)}"
                        ),
                    )
                )
                continue

            log.info("running %s", name)
            try:
                result = venture.tick(ctx)
            except Exception:  # noqa: BLE001 - a broken venture must not kill the swarm
                err = traceback.format_exc(limit=6)
                log.error("%s raised:\n%s", name, err)
                self._record_failure(name)
                report.runs.append(VentureRun(name, ran=True, error=err))
                continue

            self._record_success(name)
            self.ledger.set_state(f"venture:{name}:last_run", str(now))
            if not dry_run:
                self._book(name, result)
            report.runs.append(VentureRun(name, ran=True, result=result))

        self.ledger.check_invariant()
        self._check_drawdown()
        report.equity_cents = self.ledger.equity
        report.net_pnl_cents = self.ledger.net_pnl
        return report

    # ------------------------------------------------------------------
    def _book(self, name: str, result: TickResult) -> None:
        """Write a venture's reported costs, revenues and tasks to the books."""
        for entry in result.costs:
            ref = f"{name}:{entry.ref}" if entry.ref else None
            # A cost may exceed the float (e.g. a fee on a losing trade); the
            # float is allowed to go negative, the drawdown check catches it.
            self.ledger.record_cost(name, entry.cents, entry.memo, ref=ref)
        for entry in result.revenues:
            ref = f"{name}:{entry.ref}" if entry.ref else None
            self.ledger.record_revenue(name, entry.cents, entry.memo, ref=ref)

        for task in result.human_tasks:
            task.venture = task.venture or name
            key = f"{name}:{task.key}" if task.key else None
            task_id = self.queue.add(
                type(task)(
                    title=task.title,
                    body=task.body,
                    venture=task.venture,
                    artifact=task.artifact,
                    url=task.url,
                    payoff_cents=task.payoff_cents,
                    key=key,
                )
            )
            if task_id is not None:
                self.notifier.send(f"Forge needs you: {task.title}", task.body[:300])

        for note in result.notes:
            log.info("[%s] %s", name, note)

    def _benched(self, name: str) -> bool:
        return int(self.ledger.get_state(f"venture:{name}:failures", "0") or 0) >= MAX_CONSECUTIVE_FAILURES

    def _record_failure(self, name: str) -> None:
        n = int(self.ledger.get_state(f"venture:{name}:failures", "0") or 0) + 1
        self.ledger.set_state(f"venture:{name}:failures", str(n))
        if n >= MAX_CONSECUTIVE_FAILURES:
            self.notifier.send(
                f"Forge benched {name}",
                f"{name} failed {n} times in a row and has been stopped. "
                f"Check .forge/forge.log, then run: forge unbench {name}",
            )

    def _record_success(self, name: str) -> None:
        self.ledger.set_state(f"venture:{name}:failures", "0")

    def unbench(self, name: str) -> None:
        self.ledger.set_state(f"venture:{name}:failures", "0")

    # ------------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        """Everything `forge status` needs, in one dict."""
        ventures = []
        for name in sorted(set(self.ventures) | set(self.ledger.ventures())):
            books = self.ledger.books(name)
            vc = self.cfg.ventures.get(name)
            last = float(self.ledger.get_state(f"venture:{name}:last_run", "0") or 0)
            ventures.append(
                {
                    "name": name,
                    "enabled": bool(vc and vc.enabled),
                    "loaded": name in self.ventures,
                    "benched": self._benched(name),
                    "income_cents": books.income,
                    "expense_cents": books.expense,
                    "pnl_cents": books.pnl,
                    "float_cents": books.float_,
                    # None rather than infinity when nothing has been spent:
                    # infinity is not representable in JSON and silently breaks
                    # every client that parses this.
                    "payback_ratio": books.payback_ratio if books.expense > 0 else None,
                    "paid_for_itself": books.paid_for_itself,
                    "last_run": last,
                    "detail": self.ventures[name].status() if name in self.ventures else {},
                }
            )
        return {
            "seed_cents": self.ledger.seed_total,
            "cash_cents": self.ledger.cash,
            "equity_cents": self.ledger.equity,
            "net_pnl_cents": self.ledger.net_pnl,
            "floor_cents": self.floor_cents,
            "halted": self.halted,
            "halt_reason": self.ledger.get_state("treasury:halt_reason", "") or None,
            "open_tasks": self.queue.count_open(),
            "ventures": ventures,
        }

    def close(self) -> None:
        self.ledger.close()
