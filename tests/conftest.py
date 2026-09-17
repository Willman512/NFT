import pytest

from forge.core.config import Config, OfficeConfig, TreasuryConfig, VentureConfig
from forge.core.ledger import Ledger


@pytest.fixture
def ledger() -> Ledger:
    with Ledger(":memory:") as led:
        yield led


def make_config(tmp_path, **overrides) -> Config:
    """A config wired for tests: no network, tiny grace periods, fast ticks."""
    cfg = Config(
        treasury=TreasuryConfig(seed_usd=50.0, max_drawdown_pct=40, reserve_pct=20),
        office=OfficeConfig(
            brain="scripted",
            grace_ticks=overrides.pop("grace_ticks", 1),
            grace_hours=0,
            max_strikes=overrides.pop("max_strikes", 3),
            min_profit_usd=overrides.pop("min_profit_usd", 1.0),
            standup_interval_minutes=1,
        ),
        ventures={
            "coloringbook": VentureConfig(
                "coloringbook", interval_minutes=0.01, options={"art_pages": 12}
            ),
            "memecoin": VentureConfig(
                "memecoin",
                interval_minutes=0.01,
                max_capital_usd=15.0,
                options={
                    "source": "synthetic",
                    "synthetic_seed": 5,
                    "synthetic_steps_per_tick": 3,
                    "min_age_minutes": 0,
                    "min_liquidity_usd": 5000,
                },
            ),
        },
        workdir=tmp_path / ".forge",
        out_dir=tmp_path / "out",
    )
    for key, value in overrides.items():
        setattr(cfg, key, value)
    cfg.validate()
    return cfg


@pytest.fixture
def client(make_cfg):
    """A Flask test client for an office bound to loopback, so no token."""
    from forge.staff.office import Office
    from forge.web.server import OfficeService, create_app

    cfg = make_cfg()
    office = Office(cfg)
    service = OfficeService(cfg, office=office, autostart=False)
    app = create_app(cfg, service)
    app.config.update(TESTING=True)
    with app.test_client() as c:
        c.service = service
        yield c
    service.close()


@pytest.fixture
def empty_office(tmp_path):
    """An office with no ventures at all — the normal starting state."""
    from forge.staff.office import Office

    off = Office(make_config(tmp_path, ventures={}))
    yield off
    off.close()


@pytest.fixture
def make_cfg(tmp_path):
    """Factory so a test can tweak the office rules it is exercising."""

    def _make(**overrides) -> Config:
        return make_config(tmp_path, **overrides)

    return _make


@pytest.fixture
def config(tmp_path) -> Config:
    return make_config(tmp_path)


@pytest.fixture
def office(config):
    from forge.staff.office import Office

    off = Office(config)
    yield off
    off.close()
