"""Real orders, through Jupiter.

Read this before you enable it. This module signs transactions and spends
real money. It is written against Jupiter's documented swap API, but it has
never been run against mainnet by its author, and there is no amount of
code review that substitutes for your own small-size test. The venture will
not call any of it until the paper record passes the graduation gate and
you have separately said yes in the config.

The failure modes that cost money here are not bugs in the arithmetic: they
are slippage on thin pairs, transactions that land late, and tokens that
cannot be sold at all. Position caps are the defence, not cleverness.
"""

from __future__ import annotations

import base64
import time

from ...core.log import get_logger

log = get_logger("memecoin.live")

QUOTE_URL = "https://lite-api.jup.ag/swap/v1/quote"
SWAP_URL = "https://lite-api.jup.ag/swap/v1/swap"
SOL_MINT = "So11111111111111111111111111111111111111112"
LAMPORTS_PER_SOL = 1_000_000_000


class LiveTradeError(RuntimeError):
    pass


def get_quote(input_mint: str, output_mint: str, amount: int, slippage_bps: int = 300) -> dict:
    """A swap quote. Needs no key, so this is also the connectivity self-test."""
    import requests

    resp = requests.get(
        QUOTE_URL,
        params={
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(int(amount)),
            "slippageBps": str(int(slippage_bps)),
            "restrictIntermediateTokens": "true",
        },
        timeout=20,
        headers={"accept": "application/json"},
    )
    resp.raise_for_status()
    quote = resp.json()
    if "error" in quote:
        raise LiveTradeError(f"quote failed: {quote['error']}")
    if not quote.get("outAmount"):
        raise LiveTradeError("quote came back with no route")
    return quote


def price_impact_pct(quote: dict) -> float:
    try:
        return abs(float(quote.get("priceImpactPct") or 0.0)) * 100
    except (TypeError, ValueError):
        return 0.0


def execute_swap(
    wallet,
    quote: dict,
    rpc_url: str,
    *,
    max_price_impact_pct: float = 5.0,
    priority_fee_lamports: int = 50_000,
    confirm_timeout: float = 90.0,
) -> str:
    """Sign and send a swap. Returns the transaction signature.

    Refuses outright if the quote's price impact is worse than the cap: a
    bad fill is the most common way small trades lose money on thin pairs.
    """
    impact = price_impact_pct(quote)
    if impact > max_price_impact_pct:
        raise LiveTradeError(f"price impact {impact:.2f}% exceeds the {max_price_impact_pct:.2f}% cap")
    if not wallet.can_spend:
        raise LiveTradeError("wallet is not allowed to spend")

    import requests
    from solders.transaction import VersionedTransaction  # type: ignore

    keypair = wallet.load_keypair()
    resp = requests.post(
        SWAP_URL,
        json={
            "quoteResponse": quote,
            "userPublicKey": str(keypair.pubkey()),
            "wrapAndUnwrapSol": True,
            "dynamicComputeUnitLimit": True,
            "prioritizationFeeLamports": {"priorityLevelWithMaxLamports": {
                "maxLamports": int(priority_fee_lamports),
                "priorityLevel": "high",
            }},
        },
        timeout=30,
        headers={"accept": "application/json", "content-type": "application/json"},
    )
    resp.raise_for_status()
    body = resp.json()
    if "swapTransaction" not in body:
        raise LiveTradeError(f"swap build failed: {str(body)[:300]}")

    raw = VersionedTransaction.from_bytes(base64.b64decode(body["swapTransaction"]))
    signed = VersionedTransaction(raw.message, [keypair])
    encoded = base64.b64encode(bytes(signed)).decode()

    send = requests.post(
        rpc_url,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sendTransaction",
            "params": [encoded, {"encoding": "base64", "skipPreflight": False, "maxRetries": 3}],
        },
        timeout=30,
    ).json()
    if "error" in send:
        raise LiveTradeError(f"send failed: {send['error']}")
    signature = send["result"]
    log.info("submitted swap %s", signature)

    _confirm(rpc_url, signature, confirm_timeout)
    return signature


def _confirm(rpc_url: str, signature: str, timeout: float) -> None:
    """Poll until the network accepts the transaction, or give up loudly."""
    import requests

    deadline = time.time() + timeout
    while time.time() < deadline:
        body = requests.post(
            rpc_url,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSignatureStatuses",
                "params": [[signature], {"searchTransactionHistory": True}],
            },
            timeout=20,
        ).json()
        value = ((body.get("result") or {}).get("value") or [None])[0]
        if value:
            if value.get("err"):
                raise LiveTradeError(f"transaction {signature} failed on chain: {value['err']}")
            if value.get("confirmationStatus") in {"confirmed", "finalized"}:
                return
        time.sleep(2)
    raise LiveTradeError(
        f"transaction {signature} was not confirmed within {timeout:.0f}s; "
        "check the explorer before assuming it did not land"
    )


def selftest(rpc_url: str) -> dict:
    """Quote 0.01 SOL into USDC. Touches no key and spends nothing."""
    usdc = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    started = time.time()
    quote = get_quote(SOL_MINT, usdc, int(0.01 * LAMPORTS_PER_SOL))
    return {
        "ok": True,
        "route_hops": len(quote.get("routePlan") or []),
        "out_amount": quote.get("outAmount"),
        "price_impact_pct": round(price_impact_pct(quote), 4),
        "latency_s": round(time.time() - started, 2),
    }
