import time

from forge.core.ledger import Ledger
from forge.core.money import usd
from forge.core.survival import ELIMINATED, PROBATION, SurvivalOfficer, SurvivalRules, Verdict


def officer(ledger, **kw):
    rules = SurvivalRules(grace_ticks=kw.pop("grace_ticks", 2), grace_hours=0, **kw)
    return SurvivalOfficer(ledger, rules)


def test_grace_period_protects_a_new_venture(ledger):
    ledger.seed(usd(50))
    ledger.allocate("v", usd(10))
    ledger.record_cost("v", usd(5), "spent")
    off = officer(ledger)
    verdict, reason = off.review("v")
    assert verdict is Verdict.KEEP_GOING
    assert "grace" in reason


def test_strikes_then_elimination_recalls_capital(ledger):
    ledger.seed(usd(50))
    ledger.allocate("v", usd(20))
    ledger.record_cost("v", usd(5), "spent")
    off = officer(ledger, grace_ticks=1, max_strikes=3)
    off.note_tick("v")

    states = []
    for _ in range(3):
        verdict, reason = off.review("v")
        off.apply("v", verdict, reason)
        states.append(verdict)

    assert states == [Verdict.STRIKE, Verdict.STRIKE, Verdict.ELIMINATE]
    assert off.status("v").state == ELIMINATED
    # Its float went home rather than being stranded with a dead venture.
    assert ledger.balance("asset:float:v") == 0
    assert ledger.cash == usd(45)
    ledger.check_invariant()


def test_a_venture_that_pays_is_promoted(ledger):
    ledger.seed(usd(50))
    ledger.allocate("v", usd(10))
    ledger.record_cost("v", usd(2), "spent")
    ledger.record_revenue("v", usd(9), "earned")
    off = officer(ledger, grace_ticks=1)
    off.note_tick("v")
    verdict, _ = off.review("v")
    assert verdict is Verdict.PROMOTE


def test_profit_must_clear_the_bar_not_just_break_even(ledger):
    ledger.seed(usd(50))
    ledger.allocate("v", usd(10))
    ledger.record_cost("v", usd(5), "spent")
    ledger.record_revenue("v", usd(5), "earned")  # exactly break even
    off = officer(ledger, grace_ticks=1, min_profit_cents=usd(1))
    off.note_tick("v")
    verdict, _ = off.review("v")
    assert verdict is Verdict.STRIKE


def test_costless_venture_is_never_struck(ledger):
    ledger.seed(usd(50))
    off = officer(ledger, grace_ticks=1)
    off.note_tick("free")
    ledger.allocate("free", usd(1))
    verdict, _ = off.review("free")
    assert verdict is Verdict.KEEP_GOING


def test_eliminated_stays_dead_until_revived(ledger):
    ledger.seed(usd(50))
    ledger.allocate("v", usd(10))
    ledger.record_cost("v", usd(5), "spent")
    off = officer(ledger, grace_ticks=1, max_strikes=1)
    off.note_tick("v")
    verdict, reason = off.review("v")
    off.apply("v", verdict, reason)
    assert off.review("v")[0] is Verdict.ALREADY_DEAD
    assert off.eliminated() == ["v"]

    off.revive("v")
    assert off.status("v").state == PROBATION
    assert off.status("v").strikes == 0
    assert off.review("v")[0] is Verdict.KEEP_GOING


def test_grace_can_expire_on_time_as_well_as_ticks(ledger):
    ledger.seed(usd(50))
    ledger.allocate("v", usd(10))
    ledger.record_cost("v", usd(5), "spent")
    rules = SurvivalRules(grace_ticks=0, grace_hours=1)
    off = SurvivalOfficer(ledger, rules)
    now = time.time()
    off.note_tick("v", now)
    assert off.in_grace("v", now)
    assert not off.in_grace("v", now + 3601)
