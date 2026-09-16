"""Where candidate tokens come from.

The live source is Dexscreener, which is free and needs no key. A synthetic
source stands in for it offline, so the strategy, the risk rules and the
paper book can all be tested without a network and without waiting for a
real market to do something interesting.
"""

from __future__ import annotations

import math
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ...core.log import get_logger

log = get_logger("memecoin.source")

DEXSCREENER_SEARCH = "https://api.dexscreener.com/latest/dex/search"


@dataclass
class Candidate:
    """One tradeable token, normalised across sources."""

    mint: str
    symbol: str
    name: str
    price_usd: float
    liquidity_usd: float
    fdv_usd: float
    volume_5m_usd: float
    change_5m_pct: float
    change_1h_pct: float
    age_minutes: float
    buys_5m: int = 0
    sells_5m: int = 0
    url: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def buy_pressure(self) -> float:
        """Share of recent trades that were buys, 0.5 when there is no data."""
        total = self.buys_5m + self.sells_5m
        return 0.5 if total <= 0 else self.buys_5m / total


class MarketSource(ABC):
    name = "source"

    @abstractmethod
    def candidates(self) -> list[Candidate]:
        """Tokens worth considering right now."""

    def price(self, mint: str) -> float | None:
        """Current price for an open position. None if it cannot be read."""
        for c in self.candidates():
            if c.mint == mint:
                return c.price_usd
        return None


class DexscreenerSource(MarketSource):
    """Live pairs from Dexscreener's public search endpoint."""

    name = "dexscreener"

    def __init__(self, query: str = "SOL", chain: str = "solana", timeout: float = 15.0):
        self.query = query
        self.chain = chain
        self.timeout = timeout
        self._last: dict[str, float] = {}

    def candidates(self) -> list[Candidate]:
        import requests

        resp = requests.get(
            DEXSCREENER_SEARCH,
            params={"q": self.query},
            timeout=self.timeout,
            headers={"accept": "application/json"},
        )
        resp.raise_for_status()
        pairs = resp.json().get("pairs") or []

        out: list[Candidate] = []
        now_ms = time.time() * 1000
        for p in pairs:
            if p.get("chainId") != self.chain:
                continue
            base = p.get("baseToken") or {}
            mint = base.get("address")
            price = _f(p.get("priceUsd"))
            if not mint or price <= 0:
                continue
            created = p.get("pairCreatedAt")
            age = (now_ms - created) / 60000 if created else 1e9
            txns5 = (p.get("txns") or {}).get("m5") or {}
            out.append(
                Candidate(
                    mint=mint,
                    symbol=base.get("symbol", "?"),
                    name=base.get("name", "?"),
                    price_usd=price,
                    liquidity_usd=_f((p.get("liquidity") or {}).get("usd")),
                    fdv_usd=_f(p.get("fdv")),
                    volume_5m_usd=_f((p.get("volume") or {}).get("m5")),
                    change_5m_pct=_f((p.get("priceChange") or {}).get("m5")),
                    change_1h_pct=_f((p.get("priceChange") or {}).get("h1")),
                    age_minutes=age,
                    buys_5m=int(txns5.get("buys") or 0),
                    sells_5m=int(txns5.get("sells") or 0),
                    url=p.get("url", ""),
                )
            )
        self._last = {c.mint: c.price_usd for c in out}
        log.debug("dexscreener returned %d candidates", len(out))
        return out

    def price(self, mint: str) -> float | None:
        if mint in self._last:
            return self._last[mint]
        return super().price(mint)


class SyntheticSource(MarketSource):
    """A deterministic fake market.

    Tokens are born, pump, and mostly die, which is roughly what the real
    thing does. Useful for demos and for tests that need the strategy to
    actually take and close positions.
    """

    name = "synthetic"

    def __init__(self, seed: int = 0, size: int = 24, volatility: float = 0.18):
        self.rng = random.Random(seed)
        self.size = size
        self.volatility = volatility
        self.step = 0
        self._tokens: list[dict] = [self._spawn(i) for i in range(size)]

    def _spawn(self, i: int) -> dict:
        return {
            "mint": f"SYN{i:03d}{self.rng.randrange(16**6):06x}",
            "symbol": f"SYN{i:03d}",
            "price": self.rng.uniform(1e-6, 5e-4),
            "liq": self.rng.uniform(2_000, 120_000),
            "fdv": self.rng.uniform(20_000, 3_000_000),
            "born": self.step,
            # A minority of tokens genuinely trend; the rest bleed out.
            "drift": self.rng.choice([0.06, 0.02, -0.01, -0.03, -0.05]),
        }

    def advance(self, steps: int = 1) -> None:
        for _ in range(steps):
            self.step += 1
            for t in self._tokens:
                shock = self.rng.gauss(t["drift"], self.volatility)
                t["prev"] = t["price"]
                t["price"] = max(1e-9, t["price"] * math.exp(shock))
                t["liq"] = max(500.0, t["liq"] * math.exp(self.rng.gauss(0, 0.05)))
            # Churn: retire one token, list a new one.
            if self.rng.random() < 0.3:
                self._tokens[self.rng.randrange(len(self._tokens))] = self._spawn(self.rng.randrange(999))

    def candidates(self) -> list[Candidate]:
        out = []
        for t in self._tokens:
            prev = t.get("prev", t["price"])
            change = (t["price"] / prev - 1) * 100 if prev else 0.0
            buys = self.rng.randint(0, 40)
            out.append(
                Candidate(
                    mint=t["mint"],
                    symbol=t["symbol"],
                    name=t["symbol"],
                    price_usd=t["price"],
                    liquidity_usd=t["liq"],
                    fdv_usd=t["fdv"],
                    volume_5m_usd=t["liq"] * self.rng.uniform(0.05, 0.9),
                    change_5m_pct=change,
                    change_1h_pct=change * self.rng.uniform(1.0, 3.0),
                    age_minutes=(self.step - t["born"]) * 5.0 + 5.0,
                    buys_5m=buys,
                    sells_5m=self.rng.randint(0, 40),
                    url="",
                )
            )
        return out

    def price(self, mint: str) -> float | None:
        for t in self._tokens:
            if t["mint"] == mint:
                return t["price"]
        return None


def _f(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def build_source(options: dict) -> MarketSource:
    kind = options.get("source", "dexscreener")
    if kind == "synthetic":
        return SyntheticSource(
            seed=int(options.get("synthetic_seed", 7)),
            size=int(options.get("synthetic_size", 24)),
        )
    if kind == "dexscreener":
        return DexscreenerSource(query=options.get("query", "SOL"))
    raise ValueError(f"unknown market source {kind!r}")
