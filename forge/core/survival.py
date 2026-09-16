"""The rule that keeps the swarm honest: earn your keep or be shut down.

Every venture is on probation when it starts. It gets a grace period to
find its feet, and after that it has to show the only thing that matters:
it has brought in more than it has cost, plus a little. A venture that
cannot do that collects strikes, and a venture that collects enough
strikes is eliminated — disabled for good, its capital pulled back into
the treasury, and a tombstone written so the office remembers why.

Nothing here has an opinion. It reads the ledger and applies the rule.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum

from .ledger import Ledger
from .money import cents_to_str

PROBATION = "probation"
TRIAL = "trial"
TENURED = "tenured"
ELIMINATED = "eliminated"


class Verdict(str, Enum):
    KEEP_GOING = "keep_going"
    PROMOTE = "promote"
    STRIKE = "strike"
    ELIMINATE = "eliminate"
    ALREADY_DEAD = "already_dead"


@dataclass(frozen=True)
class SurvivalRules:
    #: Runs a venture gets before its numbers start counting against it.
    grace_ticks: int = 8
    #: ...and it must have had this long, so a fast-ticking bot is not judged early.
    grace_hours: float = 24.0
    #: Profit required on top of breaking even, in cents.
    min_profit_cents: int = 100
    #: Strikes tolerated before elimination.
    max_strikes: int = 3
    #: A venture that has never spent anything cannot fail a payback test.
    ignore_costless: bool = True


@dataclass
class SurvivalStatus:
    venture: str
    state: str
    strikes: int
    ticks: int
    first_seen: float
    payback_ratio: float
    pnl_cents: int
    reason: str = ""

    @property
    def alive(self) -> bool:
        return self.state != ELIMINATED


class SurvivalOfficer:
    """Applies the survival rules against the ledger."""

    def __init__(self, ledger: Ledger, rules: SurvivalRules | None = None):
        self.ledger = ledger
        self.rules = rules or SurvivalRules()

    # -- stored state ---------------------------------------------------
    def _key(self, venture: str, field: str) -> str:
        return f"survival:{venture}:{field}"

    def status(self, venture: str) -> SurvivalStatus:
        books = self.ledger.books(venture)
        first = float(self.ledger.get_state(self._key(venture, "first_seen"), "0") or 0)
        return SurvivalStatus(
            venture=venture,
            state=self.ledger.get_state(self._key(venture, "state"), PROBATION) or PROBATION,
            strikes=int(self.ledger.get_state(self._key(venture, "strikes"), "0") or 0),
            ticks=int(self.ledger.get_state(self._key(venture, "ticks"), "0") or 0),
            first_seen=first,
            payback_ratio=books.payback_ratio,
            pnl_cents=books.pnl,
            reason=self.ledger.get_state(self._key(venture, "reason"), "") or "",
        )

    def note_tick(self, venture: str, now: float | None = None) -> None:
        """Record that a venture took a turn. Drives the grace period."""
        now = now if now is not None else time.time()
        if not self.ledger.get_state(self._key(venture, "first_seen")):
            self.ledger.set_state(self._key(venture, "first_seen"), str(now))
        ticks = int(self.ledger.get_state(self._key(venture, "ticks"), "0") or 0)
        self.ledger.set_state(self._key(venture, "ticks"), str(ticks + 1))

    def in_grace(self, venture: str, now: float | None = None) -> bool:
        now = now if now is not None else time.time()
        st = self.status(venture)
        if st.ticks < self.rules.grace_ticks:
            return True
        if st.first_seen and (now - st.first_seen) < self.rules.grace_hours * 3600:
            return True
        return False

    # -- the judgement ---------------------------------------------------
    def review(self, venture: str, now: float | None = None) -> tuple[Verdict, str]:
        """Decide a venture's fate. Pure of side effects; call ``apply`` to enact."""
        now = now if now is not None else time.time()
        st = self.status(venture)
        books = self.ledger.books(venture)

        if st.state == ELIMINATED:
            return Verdict.ALREADY_DEAD, st.reason or "already eliminated"

        if self.in_grace(venture, now):
            remaining = max(0, self.rules.grace_ticks - st.ticks)
            return (
                Verdict.KEEP_GOING,
                f"still in its grace period ({remaining} runs to go); "
                f"so far {cents_to_str(books.income)} in, {cents_to_str(books.expense)} out",
            )

        if self.rules.ignore_costless and books.expense == 0:
            if books.income > 0:
                return Verdict.PROMOTE, (
                    f"costs nothing to run and has brought in {cents_to_str(books.income)}"
                )
            return Verdict.KEEP_GOING, "costs nothing, so it cannot be losing money yet"

        clears_bar = books.income >= books.expense + self.rules.min_profit_cents
        if clears_bar:
            return Verdict.PROMOTE, (
                f"pays for itself: {cents_to_str(books.income)} earned against "
                f"{cents_to_str(books.expense)} spent ({books.payback_ratio:.2f}x)"
            )

        shortfall = books.expense + self.rules.min_profit_cents - books.income
        if st.strikes + 1 >= self.rules.max_strikes:
            return Verdict.ELIMINATE, (
                f"failed the payback test {st.strikes + 1} times: {cents_to_str(books.income)} "
                f"earned against {cents_to_str(books.expense)} spent, "
                f"{cents_to_str(shortfall)} short of the bar"
            )
        return Verdict.STRIKE, (
            f"has not paid for itself: {cents_to_str(books.income)} earned against "
            f"{cents_to_str(books.expense)} spent, {cents_to_str(shortfall)} short "
            f"(strike {st.strikes + 1} of {self.rules.max_strikes})"
        )

    def apply(self, venture: str, verdict: Verdict, reason: str, now: float | None = None) -> SurvivalStatus:
        """Write a verdict to the books."""
        now = now if now is not None else time.time()
        if verdict == Verdict.PROMOTE:
            self.ledger.set_state(self._key(venture, "state"), TENURED)
            self.ledger.set_state(self._key(venture, "strikes"), "0")
        elif verdict == Verdict.STRIKE:
            strikes = int(self.ledger.get_state(self._key(venture, "strikes"), "0") or 0) + 1
            self.ledger.set_state(self._key(venture, "strikes"), str(strikes))
            self.ledger.set_state(self._key(venture, "state"), TRIAL)
        elif verdict == Verdict.ELIMINATE:
            strikes = int(self.ledger.get_state(self._key(venture, "strikes"), "0") or 0) + 1
            self.ledger.set_state(self._key(venture, "strikes"), str(strikes))
            self.ledger.set_state(self._key(venture, "state"), ELIMINATED)
            self.ledger.set_state(self._key(venture, "died_at"), str(now))
            self._recall_capital(venture)
        self.ledger.set_state(self._key(venture, "reason"), reason)
        return self.status(venture)

    def _recall_capital(self, venture: str) -> None:
        """Pull an eliminated venture's float back into the treasury."""
        held = self.ledger.balance(f"asset:float:{venture}")
        if held > 0:
            self.ledger.deallocate(venture, held, ref=f"eliminate:{venture}:{int(time.time())}")

    def revive(self, venture: str) -> None:
        """Give an eliminated venture another life. Only a human does this."""
        self.ledger.set_state(self._key(venture, "state"), PROBATION)
        self.ledger.set_state(self._key(venture, "strikes"), "0")
        self.ledger.set_state(self._key(venture, "ticks"), "0")
        self.ledger.set_state(self._key(venture, "first_seen"), str(time.time()))
        self.ledger.set_state(self._key(venture, "reason"), "revived by hand")

    def eliminated(self) -> list[str]:
        return [v for v in self.ledger.ventures() if self.status(v).state == ELIMINATED]
