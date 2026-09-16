"""Risk rules, the paper book, and the entry/exit strategy.

The order of these three matters. Risk comes first because on this kind of
market it is the only part with a reliable effect: position caps and stops
change the distribution of outcomes, while entry signals mostly do not.
The strategy is deliberately plain for the same reason — a complicated one
would only be overfitted to noise.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field

from .sources import Candidate


@dataclass
class RiskLimits:
    """Every one of these is a hard gate, not a suggestion."""

    # -- what may be bought at all
    min_liquidity_usd: float = 15_000.0
    max_fdv_usd: float = 5_000_000.0
    min_age_minutes: float = 20.0
    max_age_minutes: float = 2_880.0
    min_volume_5m_usd: float = 2_000.0
    min_buy_pressure: float = 0.55
    min_change_5m_pct: float = 1.5
    max_change_5m_pct: float = 60.0

    # -- how much
    max_position_usd: float = 3.0
    max_positions: int = 3
    max_exposure_usd: float = 9.0

    # -- when to get out
    stop_loss_pct: float = 22.0
    take_profit_pct: float = 45.0
    trailing_stop_pct: float = 18.0
    max_hold_minutes: float = 180.0

    # -- when to stop entirely
    daily_loss_limit_usd: float = 5.0
    cooldown_minutes: float = 10.0
    blacklist: list[str] = field(default_factory=list)

    @classmethod
    def from_options(cls, options: dict) -> "RiskLimits":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in options.items() if k in known})


@dataclass
class Position:
    mint: str
    symbol: str
    entry_price: float
    size_usd: float
    opened_at: float
    high_price: float = 0.0
    reason: str = ""

    def __post_init__(self) -> None:
        self.high_price = max(self.high_price, self.entry_price)

    def value_at(self, price: float) -> float:
        return self.size_usd * (price / self.entry_price) if self.entry_price > 0 else 0.0

    def pnl_at(self, price: float) -> float:
        return self.value_at(price) - self.size_usd

    def pct_at(self, price: float) -> float:
        if self.entry_price <= 0:
            return 0.0
        return (price / self.entry_price - 1) * 100


@dataclass
class ClosedTrade:
    mint: str
    symbol: str
    entry_price: float
    exit_price: float
    size_usd: float
    pnl_usd: float
    opened_at: float
    closed_at: float
    reason: str

    @property
    def won(self) -> bool:
        return self.pnl_usd > 0

    @property
    def held_minutes(self) -> float:
        return (self.closed_at - self.opened_at) / 60.0


class PaperBook:
    """Positions and history. Identical bookkeeping in paper and live mode."""

    def __init__(self) -> None:
        self.positions: dict[str, Position] = {}
        self.closed: list[ClosedTrade] = []
        self.last_entry_at: float = 0.0

    # -- state ---------------------------------------------------------
    def to_json(self) -> str:
        return json.dumps(
            {
                "positions": {k: asdict(v) for k, v in self.positions.items()},
                "closed": [asdict(t) for t in self.closed[-500:]],
                "last_entry_at": self.last_entry_at,
            }
        )

    @classmethod
    def from_json(cls, blob: str | None) -> "PaperBook":
        book = cls()
        if not blob:
            return book
        try:
            data = json.loads(blob)
        except (ValueError, TypeError):
            return book
        for k, v in (data.get("positions") or {}).items():
            book.positions[k] = Position(**v)
        for t in data.get("closed") or []:
            book.closed.append(ClosedTrade(**t))
        book.last_entry_at = float(data.get("last_entry_at") or 0.0)
        return book

    # -- actions -------------------------------------------------------
    def open(self, candidate: Candidate, size_usd: float, now: float, reason: str) -> Position:
        pos = Position(
            mint=candidate.mint,
            symbol=candidate.symbol,
            entry_price=candidate.price_usd,
            size_usd=size_usd,
            opened_at=now,
            reason=reason,
        )
        self.positions[candidate.mint] = pos
        self.last_entry_at = now
        return pos

    def close(self, mint: str, price: float, now: float, reason: str) -> ClosedTrade:
        pos = self.positions.pop(mint)
        trade = ClosedTrade(
            mint=pos.mint,
            symbol=pos.symbol,
            entry_price=pos.entry_price,
            exit_price=price,
            size_usd=pos.size_usd,
            pnl_usd=pos.pnl_at(price),
            opened_at=pos.opened_at,
            closed_at=now,
            reason=reason,
        )
        self.closed.append(trade)
        return trade

    def mark(self, prices: dict[str, float]) -> None:
        """Update trailing-stop high-water marks."""
        for mint, pos in self.positions.items():
            price = prices.get(mint)
            if price and price > pos.high_price:
                pos.high_price = price

    # -- reading -------------------------------------------------------
    @property
    def exposure_usd(self) -> float:
        return sum(p.size_usd for p in self.positions.values())

    @property
    def realised_pnl_usd(self) -> float:
        return sum(t.pnl_usd for t in self.closed)

    def loss_since(self, since: float) -> float:
        """Realised losses since a timestamp, as a positive number."""
        recent = sum(t.pnl_usd for t in self.closed if t.closed_at >= since)
        return max(0.0, -recent)

    def stats(self) -> dict:
        n = len(self.closed)
        wins = [t for t in self.closed if t.won]
        losses = [t for t in self.closed if not t.won]
        return {
            "closed_trades": n,
            "open_positions": len(self.positions),
            "exposure_usd": round(self.exposure_usd, 2),
            "realised_pnl_usd": round(self.realised_pnl_usd, 4),
            "win_rate": round(len(wins) / n, 3) if n else 0.0,
            "avg_win_usd": round(sum(t.pnl_usd for t in wins) / len(wins), 4) if wins else 0.0,
            "avg_loss_usd": round(sum(t.pnl_usd for t in losses) / len(losses), 4) if losses else 0.0,
            "expectancy_usd": round(self.realised_pnl_usd / n, 4) if n else 0.0,
        }


# ----------------------------------------------------------------------
# strategy
# ----------------------------------------------------------------------
def passes_filters(c: Candidate, limits: RiskLimits) -> tuple[bool, str]:
    """Would we even look at this token? Returns (ok, why not)."""
    if c.mint in limits.blacklist:
        return False, "blacklisted"
    if c.liquidity_usd < limits.min_liquidity_usd:
        return False, f"liquidity ${c.liquidity_usd:,.0f} below floor"
    if c.fdv_usd > limits.max_fdv_usd:
        return False, f"FDV ${c.fdv_usd:,.0f} above ceiling"
    if c.age_minutes < limits.min_age_minutes:
        return False, f"only {c.age_minutes:.0f}m old"
    if c.age_minutes > limits.max_age_minutes:
        return False, "too old to be running"
    if c.volume_5m_usd < limits.min_volume_5m_usd:
        return False, "not enough volume"
    if c.buy_pressure < limits.min_buy_pressure:
        return False, f"buy pressure {c.buy_pressure:.0%} too weak"
    if c.change_5m_pct < limits.min_change_5m_pct:
        return False, "no momentum"
    if c.change_5m_pct > limits.max_change_5m_pct:
        return False, f"already up {c.change_5m_pct:.0f}% in 5m, too late"
    return True, ""


def score(c: Candidate) -> float:
    """Rank the survivors. Higher is more interesting."""
    liquidity_term = min(c.liquidity_usd / 100_000.0, 1.0)
    momentum_term = min(c.change_5m_pct / 25.0, 1.0)
    pressure_term = (c.buy_pressure - 0.5) * 2
    turnover_term = min(c.volume_5m_usd / max(c.liquidity_usd, 1.0), 1.0)
    return 0.35 * momentum_term + 0.25 * pressure_term + 0.2 * liquidity_term + 0.2 * turnover_term


def choose_entries(
    candidates: list[Candidate], book: PaperBook, limits: RiskLimits, budget_usd: float, now: float
) -> list[tuple[Candidate, float, str]]:
    """Pick what to buy. Returns (candidate, size_usd, reason) triples."""
    if now - book.last_entry_at < limits.cooldown_minutes * 60:
        return []

    room = min(limits.max_positions - len(book.positions), 99)
    if room <= 0:
        return []
    spare = min(budget_usd, limits.max_exposure_usd - book.exposure_usd)
    if spare < 0.5:
        return []

    ranked = []
    for c in candidates:
        if c.mint in book.positions:
            continue
        ok, _ = passes_filters(c, limits)
        if ok:
            ranked.append((score(c), c))
    ranked.sort(key=lambda pair: pair[0], reverse=True)

    picks: list[tuple[Candidate, float, str]] = []
    for s, c in ranked[:room]:
        size = min(limits.max_position_usd, spare)
        if size < 0.5:
            break
        spare -= size
        picks.append(
            (
                c,
                size,
                f"score {s:.2f}: +{c.change_5m_pct:.1f}% in 5m, "
                f"{c.buy_pressure:.0%} buys, ${c.liquidity_usd:,.0f} liquidity",
            )
        )
    return picks


def choose_exits(
    book: PaperBook, prices: dict[str, float], limits: RiskLimits, now: float
) -> list[tuple[str, float, str]]:
    """Decide what to sell. Returns (mint, price, reason) triples."""
    out = []
    for mint, pos in list(book.positions.items()):
        price = prices.get(mint)
        if price is None or price <= 0:
            # A price that vanishes is usually a rug. Treat it as a total loss.
            out.append((mint, 0.0, "price disappeared"))
            continue
        pct = pos.pct_at(price)
        if pct <= -limits.stop_loss_pct:
            out.append((mint, price, f"stop loss at {pct:.1f}%"))
        elif pct >= limits.take_profit_pct:
            out.append((mint, price, f"take profit at {pct:.1f}%"))
        elif pos.high_price > 0 and price <= pos.high_price * (1 - limits.trailing_stop_pct / 100):
            drop = (price / pos.high_price - 1) * 100
            out.append((mint, price, f"trailing stop, {drop:.1f}% off the high"))
        elif (now - pos.opened_at) / 60 >= limits.max_hold_minutes:
            out.append((mint, price, f"held {limits.max_hold_minutes:.0f}m without resolving"))
    return out


# ----------------------------------------------------------------------
# the gate between paper and real money
# ----------------------------------------------------------------------
@dataclass
class Graduation:
    min_trades: int = 60
    min_days: float = 14.0
    min_expectancy_usd: float = 0.0
    min_win_rate: float = 0.0


def graduation_report(book: PaperBook, rules: Graduation, now: float | None = None) -> dict:
    """Has paper trading earned the right to touch real money?

    Nothing here says the strategy is good. It says only that it has not
    already proven itself bad, over a sample big enough to mean something.
    """
    now = now or time.time()
    stats = book.stats()
    first = min((t.closed_at for t in book.closed), default=now)
    days = (now - first) / 86400.0

    checks = {
        "trades": (stats["closed_trades"] >= rules.min_trades,
                   f"{stats['closed_trades']}/{rules.min_trades} closed trades"),
        "duration": (days >= rules.min_days, f"{days:.1f}/{rules.min_days:.0f} days of record"),
        "expectancy": (stats["expectancy_usd"] > rules.min_expectancy_usd,
                       f"${stats['expectancy_usd']:.4f} per trade"),
        "win_rate": (stats["win_rate"] >= rules.min_win_rate,
                     f"{stats['win_rate']:.0%} win rate"),
    }
    return {
        "passed": all(ok for ok, _ in checks.values()),
        "checks": {k: {"ok": ok, "detail": detail} for k, (ok, detail) in checks.items()},
        "stats": stats,
    }
