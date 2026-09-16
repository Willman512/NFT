"""Double-entry ledger.

The whole point of Forge is the question "has this thing paid for itself
yet?", so the books are real double-entry rather than a running total that
drifts. Every transaction is a set of signed postings that must sum to
zero, and every transaction may carry a unique ``ref`` so that a bot which
crashes and retries cannot bill you twice for the same event.

Accounts
--------
``equity:seed``          the money you put in
``asset:cash``           unallocated capital sitting in the treasury
``asset:float:<v>``      capital handed to venture ``<v>`` to work with
``income:<v>``           gross revenue earned by ``<v>``
``expense:<v>``          gross costs burned by ``<v>``

Sign convention: assets and expenses are positive when they increase;
equity and income are negative when they increase (standard credit sign).
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

CASH = "asset:cash"
SEED = "equity:seed"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS txns (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL NOT NULL,
    kind    TEXT NOT NULL,
    venture TEXT,
    memo    TEXT NOT NULL DEFAULT '',
    ref     TEXT UNIQUE
);
CREATE TABLE IF NOT EXISTS postings (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    txn_id  INTEGER NOT NULL REFERENCES txns(id) ON DELETE CASCADE,
    account TEXT NOT NULL,
    amount  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_postings_account ON postings(account);
CREATE INDEX IF NOT EXISTS idx_txns_venture ON txns(venture);
CREATE TABLE IF NOT EXISTS state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class LedgerError(RuntimeError):
    pass


@dataclass(frozen=True)
class VentureBooks:
    """What a single venture has earned and burned, in cents."""

    venture: str
    income: int
    expense: int
    float_: int

    @property
    def pnl(self) -> int:
        return self.income - self.expense

    @property
    def payback_ratio(self) -> float:
        """Revenue per dollar of cost. >= 1.0 means it has paid for itself."""
        if self.expense <= 0:
            return float("inf") if self.income > 0 else 0.0
        return self.income / self.expense

    @property
    def paid_for_itself(self) -> bool:
        return self.expense > 0 and self.income >= self.expense


def float_account(venture: str) -> str:
    return f"asset:float:{venture}"


def income_account(venture: str) -> str:
    return f"income:{venture}"


def expense_account(venture: str) -> str:
    return f"expense:{venture}"


class Ledger:
    """A SQLite-backed double-entry ledger."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        # The web office serves requests on worker threads; the Office object
        # that owns this connection serialises every use behind its own lock.
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(_SCHEMA)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "Ledger":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # posting
    # ------------------------------------------------------------------
    def post(
        self,
        kind: str,
        postings: dict[str, int],
        *,
        venture: str | None = None,
        memo: str = "",
        ref: str | None = None,
        ts: float | None = None,
    ) -> int | None:
        """Record a transaction. Returns its id, or None if ``ref`` is a duplicate.

        A duplicate ``ref`` is not an error: it is how a retried tick stays
        harmless.
        """
        total = sum(postings.values())
        if total != 0:
            raise LedgerError(f"postings must sum to zero, got {total}: {postings}")
        if not postings:
            raise LedgerError("refusing to post a transaction with no postings")
        if ref is not None and self.has_ref(ref):
            return None
        cur = self.db.execute(
            "INSERT INTO txns (ts, kind, venture, memo, ref) VALUES (?,?,?,?,?)",
            (ts if ts is not None else time.time(), kind, venture, memo, ref),
        )
        txn_id = int(cur.lastrowid)
        self.db.executemany(
            "INSERT INTO postings (txn_id, account, amount) VALUES (?,?,?)",
            [(txn_id, acct, int(amt)) for acct, amt in postings.items()],
        )
        self.db.commit()
        return txn_id

    def has_ref(self, ref: str) -> bool:
        row = self.db.execute("SELECT 1 FROM txns WHERE ref = ?", (ref,)).fetchone()
        return row is not None

    # ------------------------------------------------------------------
    # the five things that actually happen
    # ------------------------------------------------------------------
    def seed(self, cents: int, *, memo: str = "initial funding", ref: str | None = None) -> int | None:
        if cents <= 0:
            raise LedgerError("seed must be positive")
        return self.post("seed", {CASH: cents, SEED: -cents}, memo=memo, ref=ref)

    def allocate(self, venture: str, cents: int, *, ref: str | None = None) -> int | None:
        """Move capital from the treasury to a venture's float."""
        if cents <= 0:
            raise LedgerError("allocation must be positive")
        if cents > self.balance(CASH):
            raise LedgerError(
                f"cannot allocate {cents}c to {venture}: only {self.balance(CASH)}c in cash"
            )
        return self.post(
            "allocate",
            {float_account(venture): cents, CASH: -cents},
            venture=venture,
            memo=f"allocate to {venture}",
            ref=ref,
        )

    def deallocate(self, venture: str, cents: int, *, ref: str | None = None) -> int | None:
        """Pull capital back from a venture's float into the treasury."""
        if cents <= 0:
            raise LedgerError("deallocation must be positive")
        return self.post(
            "deallocate",
            {CASH: cents, float_account(venture): -cents},
            venture=venture,
            memo=f"recall from {venture}",
            ref=ref,
        )

    def record_cost(self, venture: str, cents: int, memo: str, *, ref: str | None = None) -> int | None:
        if cents <= 0:
            raise LedgerError("cost must be positive")
        return self.post(
            "cost",
            {expense_account(venture): cents, float_account(venture): -cents},
            venture=venture,
            memo=memo,
            ref=ref,
        )

    def record_revenue(self, venture: str, cents: int, memo: str, *, ref: str | None = None) -> int | None:
        if cents <= 0:
            raise LedgerError("revenue must be positive")
        return self.post(
            "revenue",
            {float_account(venture): cents, income_account(venture): -cents},
            venture=venture,
            memo=memo,
            ref=ref,
        )

    def record_pnl(self, venture: str, cents: int, memo: str, *, ref: str | None = None) -> int | None:
        """Book a realised profit (positive) or loss (negative). Zero is a no-op."""
        if cents > 0:
            return self.record_revenue(venture, cents, memo, ref=ref)
        if cents < 0:
            return self.record_cost(venture, -cents, memo, ref=ref)
        return None

    # ------------------------------------------------------------------
    # reading the books
    # ------------------------------------------------------------------
    def balance(self, account: str) -> int:
        row = self.db.execute(
            "SELECT COALESCE(SUM(amount), 0) AS bal FROM postings WHERE account = ?", (account,)
        ).fetchone()
        return int(row["bal"])

    def books(self, venture: str) -> VentureBooks:
        return VentureBooks(
            venture=venture,
            income=-self.balance(income_account(venture)),
            expense=self.balance(expense_account(venture)),
            float_=self.balance(float_account(venture)),
        )

    def ventures(self) -> list[str]:
        rows = self.db.execute(
            "SELECT DISTINCT venture FROM txns WHERE venture IS NOT NULL ORDER BY venture"
        ).fetchall()
        return [r["venture"] for r in rows]

    @property
    def cash(self) -> int:
        return self.balance(CASH)

    @property
    def seed_total(self) -> int:
        return -self.balance(SEED)

    @property
    def equity(self) -> int:
        """Everything you own: treasury cash plus every venture's float."""
        row = self.db.execute(
            "SELECT COALESCE(SUM(amount), 0) AS bal FROM postings WHERE account LIKE 'asset:%'"
        ).fetchone()
        return int(row["bal"])

    @property
    def net_pnl(self) -> int:
        return self.equity - self.seed_total

    def check_invariant(self) -> None:
        """Assets must equal seed plus net profit, or the books are broken."""
        row = self.db.execute("SELECT COALESCE(SUM(amount), 0) AS bal FROM postings").fetchone()
        if int(row["bal"]) != 0:
            raise LedgerError(f"ledger does not balance: total postings = {row['bal']}")

    def recent(self, limit: int = 20, venture: str | None = None) -> list[sqlite3.Row]:
        if venture:
            return list(
                self.db.execute(
                    "SELECT * FROM txns WHERE venture = ? ORDER BY id DESC LIMIT ?", (venture, limit)
                ).fetchall()
            )
        return list(self.db.execute("SELECT * FROM txns ORDER BY id DESC LIMIT ?", (limit,)).fetchall())

    # ------------------------------------------------------------------
    # small persistent key/value store, used by the scheduler and ventures
    # ------------------------------------------------------------------
    def get_state(self, key: str, default: str | None = None) -> str | None:
        row = self.db.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_state(self, key: str, value: str) -> None:
        self.db.execute(
            "INSERT INTO state (key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )
        self.db.commit()
