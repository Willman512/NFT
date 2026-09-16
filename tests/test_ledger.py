import pytest

from forge.core.ledger import CASH, Ledger, LedgerError
from forge.core.money import cents_to_str, usd


def test_postings_must_balance(ledger):
    with pytest.raises(LedgerError):
        ledger.post("bogus", {CASH: 100})


def test_empty_transaction_refused(ledger):
    with pytest.raises(LedgerError):
        ledger.post("bogus", {})


def test_seed_allocate_spend_earn(ledger):
    ledger.seed(usd(50))
    ledger.allocate("books", usd(20))
    ledger.record_cost("books", usd(3), "stock")
    ledger.record_revenue("books", usd(9), "royalty")

    books = ledger.books("books")
    assert books.income == 900
    assert books.expense == 300
    assert books.pnl == 600
    assert books.payback_ratio == pytest.approx(3.0)
    assert books.paid_for_itself
    assert ledger.cash == usd(30)
    assert ledger.equity == usd(56)
    assert ledger.net_pnl == usd(6)
    ledger.check_invariant()


def test_cannot_allocate_more_than_cash(ledger):
    ledger.seed(usd(10))
    with pytest.raises(LedgerError):
        ledger.allocate("books", usd(11))


def test_ref_makes_a_retry_harmless(ledger):
    ledger.seed(usd(10))
    ledger.allocate("books", usd(5))
    first = ledger.record_cost("books", usd(1), "api call", ref="tick-7")
    second = ledger.record_cost("books", usd(1), "api call", ref="tick-7")
    assert first is not None
    assert second is None
    assert ledger.books("books").expense == usd(1)


def test_record_pnl_routes_both_ways(ledger):
    ledger.seed(usd(10))
    ledger.allocate("t", usd(5))
    ledger.record_pnl("t", usd(2), "win")
    ledger.record_pnl("t", usd(-1), "loss")
    assert ledger.record_pnl("t", 0, "flat") is None
    books = ledger.books("t")
    assert (books.income, books.expense) == (usd(2), usd(1))


def test_costless_venture_reports_no_payback_but_no_crash(ledger):
    ledger.seed(usd(10))
    ledger.allocate("free", usd(1))
    ledger.record_revenue("free", usd(4), "pure profit")
    books = ledger.books("free")
    assert books.expense == 0
    assert books.payback_ratio == float("inf")
    assert not books.paid_for_itself  # nothing was spent, so nothing was paid back


def test_state_roundtrip(ledger):
    assert ledger.get_state("missing", "fallback") == "fallback"
    ledger.set_state("k", "v")
    ledger.set_state("k", "v2")
    assert ledger.get_state("k") == "v2"


def test_money_formatting():
    assert cents_to_str(-1234) == "-$12.34"
    assert cents_to_str(0) == "$0.00"
    assert cents_to_str(123456) == "$1,234.56"
    assert usd("7.995") == 800
