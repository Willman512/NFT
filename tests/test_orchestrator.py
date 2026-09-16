from forge.core.money import usd
from forge.core.orchestrator import Orchestrator
from forge.core.venture import TickResult, Venture


class Dummy(Venture):
    name = "dummy"
    description = "test venture"

    def __init__(self, options=None):
        super().__init__(options)
        self.calls = 0

    def tick(self, ctx):
        self.calls += 1
        result = TickResult()
        result.earn(usd(1), "sold something", ref=f"sale-{self.calls}")
        return result


class Exploder(Venture):
    name = "exploder"

    def tick(self, ctx):
        raise RuntimeError("nope")


def _orch(make_cfg, tmp_path, ventures):
    cfg = make_cfg()
    orch = Orchestrator.__new__(Orchestrator)
    from forge.core.humanqueue import HumanQueue
    from forge.core.ledger import Ledger
    from forge.core.notify import Notifier

    orch.cfg = cfg
    orch.ledger = Ledger(":memory:")
    orch.queue = HumanQueue(orch.ledger)
    orch.notifier = Notifier(cfg.notify, tmp_path)
    orch.ventures = ventures
    orch._ensure_seeded()
    return orch


def test_seed_is_posted_exactly_once(make_cfg):
    cfg = make_cfg()
    orch = Orchestrator(cfg)
    try:
        assert orch.ledger.seed_total == usd(50)
        orch._ensure_seeded()
        orch._ensure_seeded()
        assert orch.ledger.seed_total == usd(50)
    finally:
        orch.close()


def test_seed_tops_up_when_the_config_grows(make_cfg):
    cfg = make_cfg()
    orch = Orchestrator(cfg)
    try:
        orch.cfg.treasury.seed_usd = 80.0
        orch._ensure_seeded()
        assert orch.ledger.seed_total == usd(80)
    finally:
        orch.close()


def test_allocation_respects_cap_and_reserve(make_cfg):
    cfg = make_cfg()
    orch = Orchestrator(cfg)
    try:
        targets = orch.target_allocations()
        # coloringbook asks for nothing; memecoin is capped at $15.
        assert targets["coloringbook"] == 0
        assert 0 < targets["memecoin"] <= usd(15)
        orch.rebalance()
        assert orch.ledger.balance("asset:float:memecoin") == targets["memecoin"]
        assert orch.ledger.cash + orch.ledger.balance("asset:float:memecoin") == usd(50)
    finally:
        orch.close()


def test_results_are_booked_and_refs_deduplicated(make_cfg, tmp_path):
    dummy = Dummy()
    orch = _orch(make_cfg, tmp_path, {"dummy": dummy})
    result = TickResult()
    result.earn(usd(2), "sale", ref="s1")
    result.spend(usd(1), "fee", ref="f1")
    orch._book("dummy", result)
    orch._book("dummy", result)  # a retried tick must not double-count
    books = orch.ledger.books("dummy")
    assert (books.income, books.expense) == (usd(2), usd(1))
    orch.ledger.check_invariant()


def test_a_broken_venture_is_benched_not_fatal(make_cfg):
    cfg = make_cfg()
    orch = Orchestrator(cfg)
    try:
        orch.ventures = {"exploder": Exploder()}
        orch.cfg.ventures["exploder"] = cfg.ventures["coloringbook"]
        orch.cfg.ventures["exploder"].name = "exploder"
        for _ in range(5):
            report = orch.run_cycle(force=True)
        assert orch._benched("exploder")
        assert any(r.error for r in report.runs)
        orch.unbench("exploder")
        assert not orch._benched("exploder")
    finally:
        orch.close()


def test_drawdown_halts_everything(make_cfg):
    cfg = make_cfg()
    orch = Orchestrator(cfg)
    try:
        orch.ledger.allocate("memecoin", usd(30))
        orch.ledger.record_cost("memecoin", usd(25), "catastrophe")
        report = orch.run_cycle(force=True)
        assert report.halted or orch.halted
        assert orch.halted
        orch.resume()
        assert not orch.halted
    finally:
        orch.close()


def test_only_restricts_the_pass(make_cfg):
    cfg = make_cfg()
    orch = Orchestrator(cfg)
    try:
        report = orch.run_cycle(force=True, only={"coloringbook"})
        assert [r.name for r in report.runs] == ["coloringbook"]
    finally:
        orch.close()
