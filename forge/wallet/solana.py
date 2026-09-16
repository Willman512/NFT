"""The office wallet.

Watch-only by default, and that is the recommended way to run it: you keep
the keys in Phantom or wherever you already keep them, paste the public
address into the config, and the office can see the balance without ever
being able to move it.

A hot wallet is only created if you ask for one explicitly. It exists so
the trader can place real orders, it holds only what you send it, and the
key file is written in the same format as ``solana-keygen`` so you can
import it into a normal wallet and take the money out at any time.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from ..core.log import get_logger

log = get_logger("wallet")

LAMPORTS_PER_SOL = 1_000_000_000
DEFAULT_RPC = "https://api.mainnet-beta.solana.com"
PRICE_URL = "https://lite-api.jup.ag/price/v3?ids=So11111111111111111111111111111111111111112"
SOL_MINT = "So11111111111111111111111111111111111111112"

#: Balances and prices are cached this long so a tight UI poll does not
#: hammer a public RPC endpoint and get itself rate limited.
CACHE_SECONDS = 30.0


class WalletError(RuntimeError):
    pass


@dataclass
class Balance:
    sol: float
    usd: float | None
    price_usd: float | None
    fetched_at: float
    online: bool
    error: str | None = None
    #: True once any read has succeeded, so a stale figure can be labelled
    #: as such instead of being shown as a confident zero.
    fetched_ok: bool = False


class Wallet:
    """A Solana wallet the office can look at, and sometimes spend from."""

    def __init__(
        self,
        address: str | None = None,
        keyfile: str | Path | None = None,
        rpc_url: str = DEFAULT_RPC,
        *,
        allow_spend: bool = False,
    ):
        self.rpc_url = rpc_url or DEFAULT_RPC
        self.keyfile = Path(keyfile).expanduser() if keyfile else None
        self._explicit_address = address or None
        self.allow_spend = allow_spend
        self._cache: Balance | None = None

    # ------------------------------------------------------------------
    @property
    def mode(self) -> str:
        if self.keyfile and self.keyfile.exists():
            return "hot" if self.allow_spend else "hot-locked"
        if self.address:
            return "watch"
        return "none"

    @property
    def address(self) -> str | None:
        if self._explicit_address:
            return self._explicit_address
        if self.keyfile and self.keyfile.exists():
            try:
                return self._pubkey_from_keyfile()
            except WalletError as exc:
                log.warning("%s", exc)
        return None

    @property
    def explorer_url(self) -> str | None:
        return f"https://solscan.io/account/{self.address}" if self.address else None

    @property
    def can_spend(self) -> bool:
        return self.allow_spend and bool(self.keyfile and self.keyfile.exists())

    # ------------------------------------------------------------------
    def _pubkey_from_keyfile(self) -> str:
        try:
            from solders.keypair import Keypair  # type: ignore
        except ImportError as exc:
            raise WalletError(
                "reading a key file needs solders: pip install -r requirements-live.txt"
            ) from exc
        raw = json.loads(self.keyfile.read_text())  # type: ignore[union-attr]
        return str(Keypair.from_bytes(bytes(raw)).pubkey())

    def load_keypair(self):
        """Return the signing keypair. Only the trader's live mode calls this."""
        if not self.can_spend:
            raise WalletError("this wallet is not allowed to spend")
        from solders.keypair import Keypair  # type: ignore

        raw = json.loads(self.keyfile.read_text())  # type: ignore[union-attr]
        return Keypair.from_bytes(bytes(raw))

    def create_hot_wallet(self, path: str | Path, *, overwrite: bool = False) -> str:
        """Create a new hot wallet key file and return its address."""
        try:
            from solders.keypair import Keypair  # type: ignore
        except ImportError as exc:
            raise WalletError(
                "creating a wallet needs solders: pip install -r requirements-live.txt"
            ) from exc
        p = Path(path).expanduser()
        if p.exists() and not overwrite:
            raise WalletError(f"{p} already exists; refusing to overwrite a key file")
        kp = Keypair()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(list(bytes(kp))))
        os.chmod(p, 0o600)
        self.keyfile = p
        self._explicit_address = None
        self._cache = None
        log.info("created hot wallet %s at %s", kp.pubkey(), p)
        return str(kp.pubkey())

    # ------------------------------------------------------------------
    def balance(self, *, force: bool = False) -> Balance:
        """Current balance, cached briefly. Never raises: returns offline instead."""
        now = time.time()
        if not force and self._cache and (now - self._cache.fetched_at) < CACHE_SECONDS:
            return self._cache
        if not self.address:
            self._cache = Balance(0.0, None, None, now, False, "no wallet configured")
            return self._cache

        sol, price, error = 0.0, None, None
        try:
            sol = self._fetch_sol()
            price = self._fetch_price()
        except Exception as exc:  # noqa: BLE001 - the office must survive a dead RPC
            error = str(exc)[:200]
            log.debug("wallet refresh failed: %s", error)
            if self._cache and self._cache.online:
                # Keep showing the last good number rather than flashing zero.
                stale = self._cache
                self._cache = Balance(
                    stale.sol, stale.usd, stale.price_usd, now, False, error, fetched_ok=True
                )
                return self._cache

        self._cache = Balance(
            sol=sol,
            usd=(sol * price) if price is not None else None,
            price_usd=price,
            fetched_at=now,
            online=error is None,
            error=error,
            fetched_ok=error is None,
        )
        return self._cache

    def _fetch_sol(self) -> float:
        import requests

        resp = requests.post(
            self.rpc_url,
            json={"jsonrpc": "2.0", "id": 1, "method": "getBalance", "params": [self.address]},
            timeout=12,
        )
        resp.raise_for_status()
        body = resp.json()
        if "error" in body:
            raise WalletError(str(body["error"]))
        return int(body["result"]["value"]) / LAMPORTS_PER_SOL

    def _fetch_price(self) -> float | None:
        import requests

        try:
            resp = requests.get(PRICE_URL, timeout=12)
            resp.raise_for_status()
            data = resp.json()
            entry = (data.get("data") or data).get(SOL_MINT) or {}
            price = entry.get("usdPrice", entry.get("price"))
            return float(price) if price is not None else None
        except Exception as exc:  # noqa: BLE001 - a missing price is not fatal
            log.debug("price lookup failed: %s", exc)
            return None

    def snapshot(self) -> dict:
        bal = self.balance()
        return {
            "mode": self.mode,
            "address": self.address,
            "explorer_url": self.explorer_url,
            "can_spend": self.can_spend,
            "sol": round(bal.sol, 6),
            "usd": round(bal.usd, 2) if bal.usd is not None else None,
            "sol_price_usd": round(bal.price_usd, 2) if bal.price_usd is not None else None,
            "online": bal.online,
            "fetched_ok": bal.fetched_ok,
            "error": bal.error,
            "fetched_at": bal.fetched_at,
        }
