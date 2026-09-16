"""Configuration loading.

Secrets never live in the config file. Anything sensitive is named by the
environment variable that holds it, so the YAML stays safe to commit.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_NAME = "forge.yaml"


class ConfigError(RuntimeError):
    pass


@dataclass
class TreasuryConfig:
    seed_usd: float = 50.0
    #: Halt every venture if equity falls this far below the seed.
    max_drawdown_pct: float = 40.0
    #: Fraction of the seed never handed to a venture.
    reserve_pct: float = 20.0

    def validate(self) -> None:
        if self.seed_usd <= 0:
            raise ConfigError("treasury.seed_usd must be positive")
        if not 0 <= self.max_drawdown_pct <= 100:
            raise ConfigError("treasury.max_drawdown_pct must be between 0 and 100")
        if not 0 <= self.reserve_pct < 100:
            raise ConfigError("treasury.reserve_pct must be between 0 and 100")


@dataclass
class VentureConfig:
    name: str
    enabled: bool = True
    interval_minutes: float = 60.0
    max_capital_usd: float = 0.0
    options: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if self.interval_minutes <= 0:
            raise ConfigError(f"ventures.{self.name}.interval_minutes must be positive")
        if self.max_capital_usd < 0:
            raise ConfigError(f"ventures.{self.name}.max_capital_usd cannot be negative")


@dataclass
class TelegramConfig:
    enabled: bool = False
    token_env: str = "FORGE_TELEGRAM_TOKEN"
    chat_id_env: str = "FORGE_TELEGRAM_CHAT_ID"

    @property
    def token(self) -> str | None:
        return os.environ.get(self.token_env) or None

    @property
    def chat_id(self) -> str | None:
        return os.environ.get(self.chat_id_env) or None


@dataclass
class OfficeConfig:
    """Settings for the staff, as opposed to any one venture."""

    #: "auto" uses Claude when ANTHROPIC_API_KEY is set, else the scripted brain.
    brain: str = "auto"
    model: str = "claude-opus-5"
    #: How often the office holds a standup, in minutes.
    standup_interval_minutes: float = 60.0
    #: Survival rule.
    grace_ticks: int = 8
    grace_hours: float = 24.0
    min_profit_usd: float = 1.0
    max_strikes: int = 3
    #: Finished work waiting on a human before the salesman stops production.
    backlog_limit: int = 3
    #: The wallet shown along the bottom of the office. Watch-only.
    wallet_address: str = ""
    wallet_keyfile: str = ""
    rpc_url: str = "https://api.mainnet-beta.solana.com"
    #: Local web UI.
    host: str = "127.0.0.1"
    port: int = 8765

    def as_options(self) -> dict[str, Any]:
        return {
            "brain": self.brain,
            "model": self.model,
            "grace_ticks": self.grace_ticks,
            "grace_hours": self.grace_hours,
            "min_profit_usd": self.min_profit_usd,
            "max_strikes": self.max_strikes,
            "backlog_limit": self.backlog_limit,
            "wallet_address": self.wallet_address,
            "wallet_keyfile": self.wallet_keyfile,
            "rpc_url": self.rpc_url,
        }

    def validate(self) -> None:
        if self.brain not in {"auto", "claude", "scripted"}:
            raise ConfigError(f"office.brain must be auto, claude or scripted, got {self.brain!r}")
        if self.standup_interval_minutes <= 0:
            raise ConfigError("office.standup_interval_minutes must be positive")
        if not 1 <= self.port <= 65535:
            raise ConfigError(f"office.port is out of range: {self.port}")
        if self.max_strikes < 1:
            raise ConfigError("office.max_strikes must be at least 1")


@dataclass
class NotifyConfig:
    desktop: bool = True
    telegram: TelegramConfig = field(default_factory=TelegramConfig)


@dataclass
class Config:
    treasury: TreasuryConfig = field(default_factory=TreasuryConfig)
    office: OfficeConfig = field(default_factory=OfficeConfig)
    ventures: dict[str, VentureConfig] = field(default_factory=dict)
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    #: Everything Forge writes lives under here.
    workdir: Path = Path(".forge")
    out_dir: Path = Path("out")
    source_path: Path | None = None

    @property
    def ledger_path(self) -> Path:
        return self.workdir / "forge.db"

    def venture(self, name: str) -> VentureConfig:
        if name not in self.ventures:
            raise ConfigError(f"no configuration for venture {name!r}")
        return self.ventures[name]

    def enabled_ventures(self) -> list[VentureConfig]:
        return [v for v in self.ventures.values() if v.enabled]

    def validate(self) -> None:
        self.treasury.validate()
        self.office.validate()
        for v in self.ventures.values():
            v.validate()
        if not self.ventures:
            raise ConfigError("no ventures configured; nothing would ever run")


def _as_dict(value: Any, where: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{where} must be a mapping, got {type(value).__name__}")
    return value


def load_config(path: str | Path | None = None) -> Config:
    """Load a Forge config, falling back to built-in defaults."""
    if path is None:
        candidate = Path(DEFAULT_CONFIG_NAME)
        path = candidate if candidate.exists() else None
    if path is None:
        cfg = Config()
        cfg.validate_or_default()
        return cfg

    p = Path(path)
    if not p.exists():
        raise ConfigError(f"config file not found: {p}")
    raw = _as_dict(yaml.safe_load(p.read_text()), "config root")

    treasury = TreasuryConfig(**_as_dict(raw.get("treasury"), "treasury"))
    office = OfficeConfig(**_as_dict(raw.get("office"), "office"))

    ventures: dict[str, VentureConfig] = {}
    for name, body in _as_dict(raw.get("ventures"), "ventures").items():
        body = _as_dict(body, f"ventures.{name}")
        ventures[name] = VentureConfig(
            name=name,
            enabled=bool(body.get("enabled", True)),
            interval_minutes=float(body.get("interval_minutes", 60)),
            max_capital_usd=float(body.get("max_capital_usd", 0)),
            options=_as_dict(body.get("options"), f"ventures.{name}.options"),
        )

    nraw = _as_dict(raw.get("notify"), "notify")
    notify = NotifyConfig(
        desktop=bool(nraw.get("desktop", True)),
        telegram=TelegramConfig(**_as_dict(nraw.get("telegram"), "notify.telegram")),
    )

    cfg = Config(
        treasury=treasury,
        office=office,
        ventures=ventures,
        notify=notify,
        workdir=Path(raw.get("workdir", ".forge")).expanduser(),
        out_dir=Path(raw.get("out_dir", "out")).expanduser(),
        source_path=p,
    )
    cfg.validate()
    return cfg


def _validate_or_default(self: Config) -> None:
    """Defaults carry no ventures, so seed the config with the safe pair."""
    if not self.ventures:
        self.ventures = {
            "coloringbook": VentureConfig(name="coloringbook", interval_minutes=720),
            "memecoin": VentureConfig(
                name="memecoin", interval_minutes=15, options={"mode": "paper"}
            ),
        }
    self.validate()


Config.validate_or_default = _validate_or_default  # type: ignore[attr-defined]
