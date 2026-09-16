"""The plugin contract every earner bot implements.

A venture is a small autonomous business. It is handed a budget and a
scratch directory, it is told to take a turn, and it reports back what it
spent, what it earned, what it produced, and anything a human has to do.
It never touches the ledger itself: the orchestrator books the results, so
one misbehaving venture cannot corrupt the accounts.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class Entry:
    """A cost or a revenue the venture wants booked."""

    cents: int
    memo: str
    #: A stable id for this exact event. Lets a retried tick stay harmless.
    ref: str | None = None


@dataclass
class HumanTask:
    """Work that genuinely cannot be automated, packaged to take 2 minutes.

    Forge is honest about this: uploading to KDP, funding a wallet and
    verifying an account all require a person. The bots do everything up to
    that line and then hand over a task with the artifacts attached.
    """

    title: str
    body: str
    venture: str = ""
    artifact: str | None = None
    url: str | None = None
    #: What you plausibly earn by doing it, for prioritising the queue.
    payoff_cents: int = 0
    #: Stable id so the same task is not queued twice.
    key: str | None = None


@dataclass
class TickResult:
    """What one turn of a venture produced."""

    costs: list[Entry] = field(default_factory=list)
    revenues: list[Entry] = field(default_factory=list)
    artifacts: list[Path] = field(default_factory=list)
    human_tasks: list[HumanTask] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    ok: bool = True

    def spend(self, cents: int, memo: str, ref: str | None = None) -> None:
        if cents > 0:
            self.costs.append(Entry(cents, memo, ref))

    def earn(self, cents: int, memo: str, ref: str | None = None) -> None:
        if cents > 0:
            self.revenues.append(Entry(cents, memo, ref))

    def note(self, text: str) -> None:
        self.notes.append(text)

    @property
    def net_cents(self) -> int:
        return sum(e.cents for e in self.revenues) - sum(e.cents for e in self.costs)


@dataclass
class TickContext:
    """Everything a venture is allowed to know about the outside world."""

    now: float
    #: Capital the venture may put at risk or spend this tick, in cents.
    budget_cents: int
    #: Total capital currently parked with this venture, in cents.
    float_cents: int
    workdir: Path
    out_dir: Path
    dry_run: bool
    log: logging.Logger
    #: Namespaced persistent state, so ventures survive restarts.
    get_state: Callable[[str, str | None], str | None]
    set_state: Callable[[str, str], None]


class Venture(ABC):
    """Base class for an earner bot."""

    #: Short machine name, must match the key in the config file.
    name: str = ""
    #: One line shown in `forge status`.
    description: str = ""
    #: Minimum capital below which the venture cannot usefully act.
    min_capital_cents: int = 0

    def __init__(self, options: dict[str, Any] | None = None):
        self.options = dict(options or {})

    # -- lifecycle -----------------------------------------------------
    def validate(self) -> None:
        """Raise if the configured options make no sense. Called at startup."""

    @abstractmethod
    def tick(self, ctx: TickContext) -> TickResult:
        """Take one turn. Must not raise for ordinary failures; return ok=False."""

    def status(self) -> dict[str, Any]:
        """Extra detail for `forge status`."""
        return {}

    # -- helpers -------------------------------------------------------
    def opt(self, key: str, default: Any = None) -> Any:
        return self.options.get(key, default)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} {self.name}>"
