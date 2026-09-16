import time

import pytest

from forge.ventures.memecoin.sources import Candidate, SyntheticSource
from forge.ventures.memecoin.trading import (
    Graduation,
    PaperBook,
    RiskLimits,
    choose_entries,
    choose_exits,
    graduation_report,
    passes_filters,
)


def candidate(**kw) -> Candidate:
    base = dict(
        mint="M1", symbol="OK", name="OK", price_usd=0.001, liquidity_usd=50_000,
        fdv_usd=500_000, volume_5m_usd=10_000, change_5m_pct=8.0, change_1h_pct=20.0,
        age_minutes=90, buys_5m=70, sells_5m=30,
    )
    base.update(kw)
    return Candidate(**base)


def test_a_good_candidate_passes():
    ok, why = passes_filters(candidate(), RiskLimits())
    assert ok, why


@pytest.mark.parametrize("field,value,fragment", [
    ("liquidity_usd", 500, "liquidity"),
    ("fdv_usd", 50_000_000, "FDV"),
    ("age_minutes", 2, "old"),
    ("volume_5m_usd", 10, "volume"),
    ("change_5m_pct", 0.1, "momentum"),
    ("change_5m_pct", 200.0, "too late"),
])
def test_each_filter_rejects_for_its_own_reason(field, value, fragment):
    ok, why = passes_filters(candidate(**{field: value}), RiskLimits())
    assert not ok
    assert fragment in why


def test_weak_buy_pressure_is_rejected():
    ok, why = passes_filters(candidate(buys_5m=10, sells_5m=90), RiskLimits())
    assert not ok and "buy pressure" in why


def test_blacklist_is_honoured():
    ok, why = passes_filters(candidate(), RiskLimits(blacklist=["M1"]))
    assert not ok and why == "blacklisted"


def test_position_and_exposure_caps_bound_the_damage():
    limits = RiskLimits(max_position_usd=3, max_positions=3, max_exposure_usd=6, cooldown_minutes=0)
    book = PaperBook()
    cands = [candidate(mint=f"M{i}", symbol=f"S{i}") for i in range(5)]
    picks = choose_entries(cands, book, limits, budget_usd=100, now=time.time())
    assert len(picks) <= 3
    assert sum(size for _, size, _ in picks) <= 6


def test_cooldown_blocks_a_second_entry_immediately_after_the_first():
    limits = RiskLimits(cooldown_minutes=10)
    book = PaperBook()
    now = time.time()
    book.open(candidate(), 3.0, now, "first")
    assert choose_entries([candidate(mint="M2")], book, limits, 100, now + 60) == []
    assert choose_entries([candidate(mint="M2")], book, limits, 100, now + 700)


def test_budget_limits_the_size():
    limits = RiskLimits(max_position_usd=5, cooldown_minutes=0)
    picks = choose_entries([candidate()], PaperBook(), limits, budget_usd=2.0, now=time.time())
    assert picks and picks[0][1] == pytest.approx(2.0)


@pytest.mark.parametrize("price,fragment", [
    (0.00074, "stop loss"),
    (0.00150, "take profit"),
])
def test_stops_and_targets_fire(price, fragment):
    limits = RiskLimits(stop_loss_pct=22, take_profit_pct=45)
    book = PaperBook()
    now = time.time()
    book.open(candidate(price_usd=0.001), 3.0, now, "entry")
    exits = choose_exits(book, {"M1": price}, limits, now)
    assert exits and fragment in exits[0][2]


def test_trailing_stop_uses_the_high_water_mark():
    limits = RiskLimits(stop_loss_pct=90, take_profit_pct=900, trailing_stop_pct=18)
    book = PaperBook()
    now = time.time()
    book.open(candidate(price_usd=0.001), 3.0, now, "entry")
    book.mark({"M1": 0.002})           # it ran up
    exits = choose_exits(book, {"M1": 0.0016}, limits, now)  # then gave back 20%
    assert exits and "trailing" in exits[0][2]


def test_a_vanished_price_is_treated_as_a_total_loss():
    book = PaperBook()
    now = time.time()
    book.open(candidate(price_usd=0.001), 3.0, now, "entry")
    exits = choose_exits(book, {}, RiskLimits(), now)
    assert exits == [("M1", 0.0, "price disappeared")]
    trade = book.close("M1", 0.0, now, "rug")
    assert trade.pnl_usd == pytest.approx(-3.0)


def test_max_hold_closes_a_position_that_goes_nowhere():
    limits = RiskLimits(max_hold_minutes=60, stop_loss_pct=99, take_profit_pct=999, trailing_stop_pct=99)
    book = PaperBook()
    now = time.time()
    book.open(candidate(price_usd=0.001), 3.0, now, "entry")
    exits = choose_exits(book, {"M1": 0.001}, limits, now + 3700)
    assert exits and "without resolving" in exits[0][2]


def test_daily_loss_is_measured_from_the_window_start():
    book = PaperBook()
    now = time.time()
    book.open(candidate(price_usd=0.001), 3.0, now - 10_000, "old")
    book.close("M1", 0.0005, now - 9_000, "stop")
    assert book.loss_since(now - 20_000) == pytest.approx(1.5)
    assert book.loss_since(now) == 0.0


def test_book_survives_a_restart():
    book = PaperBook()
    now = time.time()
    book.open(candidate(), 3.0, now, "entry")
    book.close("M1", 0.0015, now + 60, "take profit")
    book.open(candidate(mint="M2", symbol="S2"), 2.0, now + 120, "entry")

    restored = PaperBook.from_json(book.to_json())
    assert restored.stats() == book.stats()
    assert set(restored.positions) == {"M2"}


def test_a_corrupt_state_blob_does_not_crash_the_venture():
    assert PaperBook.from_json("not json").stats()["closed_trades"] == 0
    assert PaperBook.from_json(None).positions == {}


def test_graduation_needs_a_real_sample_not_a_lucky_streak():
    book = PaperBook()
    now = time.time()
    for i in range(5):
        book.open(candidate(mint=f"M{i}"), 1.0, now, "entry")
        book.close(f"M{i}", 0.002, now + 60, "take profit")
    report = graduation_report(book, Graduation(min_trades=60, min_days=14), now + 120)
    assert not report["passed"]
    assert not report["checks"]["trades"]["ok"]
    assert not report["checks"]["duration"]["ok"]
    assert report["checks"]["expectancy"]["ok"]  # it did make money, just not enough of it


def test_synthetic_market_is_reproducible():
    a, b = SyntheticSource(3), SyntheticSource(3)
    a.advance(5)
    b.advance(5)
    assert [c.price_usd for c in a.candidates()] == [c.price_usd for c in b.candidates()]
