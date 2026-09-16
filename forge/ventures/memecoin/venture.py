"""The memecoin trading venture.

Paper by default, and it stays on paper until its own recorded results say
otherwise. The honest summary, which the researcher will repeat to you in
the office: a momentum bot reading public endpoints has no known edge on
this market, and the fees, the spread and the rug rate all run against you.
It is in the swarm so the banker has something to compare against, and so
that if it does turn out to lose money, it gets eliminated on evidence
rather than on opinion.
"""

from __future__ import annotations

import json
import time
from typing import Any

from ...core.money import usd
from ...core.venture import TickContext, TickResult, Venture
from ...wallet import Wallet
from . import live as live_mod
from .sources import MarketSource, build_source
from .trading import (
    Graduation,
    PaperBook,
    RiskLimits,
    choose_entries,
    choose_exits,
    graduation_report,
)

BOOK_KEY = "book"
DAY_START_KEY = "day_start"


class MemecoinVenture(Venture):
    name = "memecoin"
    description = "Trades Solana memecoins on momentum, on paper until proven"
    min_capital_cents = 100

    def __init__(self, options: dict[str, Any] | None = None):
        super().__init__(options)
        self.limits = RiskLimits.from_options(self.options)
        self.graduation = Graduation(
            min_trades=int(self.opt("graduation_min_trades", 60)),
            min_days=float(self.opt("graduation_min_days", 14)),
        )
        self._source: MarketSource | None = None
        self._wallet: Wallet | None = None

    # ------------------------------------------------------------------
    def validate(self) -> None:
        mode = self.opt("mode", "paper")
        if mode not in {"paper", "live"}:
            raise ValueError(f"mode must be 'paper' or 'live', got {mode!r}")
        build_source(self.options)
        if self.limits.max_position_usd <= 0:
            raise ValueError("max_position_usd must be positive")
        if self.limits.max_exposure_usd < self.limits.max_position_usd:
            raise ValueError("max_exposure_usd cannot be smaller than max_position_usd")

    @property
    def source(self) -> MarketSource:
        if self._source is None:
            self._source = build_source(self.options)
        return self._source

    @property
    def wallet(self) -> Wallet:
        if self._wallet is None:
            self._wallet = Wallet(
                address=self.opt("wallet_address"),
                keyfile=self.opt("wallet_keyfile"),
                rpc_url=self.opt("rpc_url", "https://api.mainnet-beta.solana.com"),
                allow_spend=bool(self.opt("allow_spend", False)),
            )
        return self._wallet

    # ------------------------------------------------------------------
    def live_blockers(self, book: PaperBook, now: float | None = None) -> list[str]:
        """Everything standing between this venture and real money."""
        blockers = []
        if self.opt("mode", "paper") != "live":
            blockers.append("mode is set to paper")
        if not self.opt("i_understand_the_risk", False):
            blockers.append("i_understand_the_risk is not set in the config")
        report = graduation_report(book, self.graduation, now)
        if not report["passed"]:
            failed = [d["detail"] for d in report["checks"].values() if not d["ok"]]
            blockers.append("paper record has not graduated: " + "; ".join(failed))
        if not self.wallet.can_spend:
            blockers.append("no spendable wallet is configured")
        return blockers

    # ------------------------------------------------------------------
    def tick(self, ctx: TickContext) -> TickResult:
        result = TickResult()
        now = ctx.now
        book = PaperBook.from_json(ctx.get_state(BOOK_KEY, None))

        # A synthetic market has to be nudged forward; a real one moves itself.
        if hasattr(self.source, "advance"):
            self.source.advance(int(self.opt("synthetic_steps_per_tick", 1)))

        try:
            candidates = self.source.candidates()
        except Exception as exc:  # noqa: BLE001 - a dead endpoint is not a crash
            result.ok = False
            result.note(f"could not read the market: {str(exc)[:160]}")
            return result

        prices = {c.mint: c.price_usd for c in candidates}
        for mint in book.positions:
            if mint not in prices:
                fetched = self.source.price(mint)
                if fetched:
                    prices[mint] = fetched
        book.mark(prices)

        blockers = self.live_blockers(book, now)
        going_live = not blockers
        if self.opt("mode", "paper") == "live" and blockers:
            result.note("live mode requested but blocked: " + "; ".join(blockers))

        # -- daily loss limit ------------------------------------------
        day_start = float(ctx.get_state(DAY_START_KEY, "0") or 0)
        if now - day_start > 86400:
            day_start = now
            ctx.set_state(DAY_START_KEY, str(now))
        lost_today = book.loss_since(day_start)
        halted_today = lost_today >= self.limits.daily_loss_limit_usd

        # -- exits first: always allowed, even when entries are halted ---
        for mint, price, why in choose_exits(book, prices, self.limits, now):
            pos = book.positions[mint]
            trade = book.close(mint, price, now, why)
            ref = f"exit:{mint}:{int(trade.closed_at)}"
            label = "live" if going_live else "paper"
            memo = f"[{label}] sold {trade.symbol}: {why}"
            if trade.pnl_usd >= 0:
                result.earn(usd(trade.pnl_usd), memo, ref=ref)
            else:
                result.spend(usd(-trade.pnl_usd), memo, ref=ref)
            ctx.log.info("closed %s at %.1f%% (%s)", trade.symbol, pos.pct_at(price), why)
            result.note(f"sold {trade.symbol} for {trade.pnl_usd:+.2f} USD — {why}")

        # -- entries ----------------------------------------------------
        if halted_today:
            result.note(
                f"daily loss limit hit (${lost_today:.2f} of "
                f"${self.limits.daily_loss_limit_usd:.2f}); no new positions today"
            )
        elif ctx.dry_run:
            result.note("dry run: no positions opened")
        else:
            budget = ctx.budget_cents / 100.0
            for candidate, size, why in choose_entries(candidates, book, self.limits, budget, now):
                book.open(candidate, size, now, why)
                label = "live" if going_live else "paper"
                result.note(f"[{label}] bought ${size:.2f} of {candidate.symbol} — {why}")
                ctx.log.info("opened %s at %g", candidate.symbol, candidate.price_usd)

        ctx.set_state(BOOK_KEY, book.to_json())
        ctx.set_state("stats", json.dumps(book.stats()))
        ctx.set_state("graduated", "1" if not self.live_blockers(book, now) else "0")

        stats = book.stats()
        result.note(
            f"{stats['open_positions']} open, {stats['closed_trades']} closed, "
            f"expectancy ${stats['expectancy_usd']:.4f}/trade"
        )
        return result

    # ------------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        return {
            "mode": self.opt("mode", "paper"),
            "source": self.opt("source", "dexscreener"),
            "max_position_usd": self.limits.max_position_usd,
            "max_exposure_usd": self.limits.max_exposure_usd,
            "daily_loss_limit_usd": self.limits.daily_loss_limit_usd,
            "wallet_mode": self.wallet.mode,
        }

    def selftest(self) -> dict:
        """Check the live plumbing without spending anything."""
        return live_mod.selftest(self.opt("rpc_url", "https://api.mainnet-beta.solana.com"))
