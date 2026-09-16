"""The queue of things a human must do.

Forge does not pretend that everything can be automated. Amazon KDP has no
publishing API and forbids scripted uploads; exchanges need a verified
person. So those steps become queued tasks with the finished artifact
attached, and the ledger only counts the money once it actually arrives.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

from .ledger import Ledger
from .venture import HumanTask

_SCHEMA = """
CREATE TABLE IF NOT EXISTS human_tasks (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       REAL NOT NULL,
    venture  TEXT NOT NULL DEFAULT '',
    title    TEXT NOT NULL,
    body     TEXT NOT NULL DEFAULT '',
    artifact TEXT,
    url      TEXT,
    payoff   INTEGER NOT NULL DEFAULT 0,
    status   TEXT NOT NULL DEFAULT 'open',
    task_key TEXT UNIQUE,
    done_ts  REAL
);
"""


@dataclass(frozen=True)
class QueuedTask:
    id: int
    ts: float
    venture: str
    title: str
    body: str
    artifact: str | None
    url: str | None
    payoff_cents: int
    status: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "QueuedTask":
        return cls(
            id=int(row["id"]),
            ts=float(row["ts"]),
            venture=row["venture"],
            title=row["title"],
            body=row["body"],
            artifact=row["artifact"],
            url=row["url"],
            payoff_cents=int(row["payoff"]),
            status=row["status"],
        )


class HumanQueue:
    def __init__(self, ledger: Ledger):
        self.db = ledger.db
        self.db.executescript(_SCHEMA)
        self.db.commit()

    def add(self, task: HumanTask) -> int | None:
        """Queue a task. Returns its id, or None if the key is already queued."""
        if task.key and self._key_exists(task.key):
            return None
        cur = self.db.execute(
            "INSERT INTO human_tasks (ts, venture, title, body, artifact, url, payoff, task_key) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                time.time(),
                task.venture,
                task.title,
                task.body,
                task.artifact,
                task.url,
                int(task.payoff_cents),
                task.key,
            ),
        )
        self.db.commit()
        return int(cur.lastrowid)

    def _key_exists(self, key: str) -> bool:
        return self.db.execute("SELECT 1 FROM human_tasks WHERE task_key = ?", (key,)).fetchone() is not None

    def open_tasks(self, limit: int = 50) -> list[QueuedTask]:
        """Highest payoff first: do the tasks that actually pay."""
        rows = self.db.execute(
            "SELECT * FROM human_tasks WHERE status = 'open' ORDER BY payoff DESC, id ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [QueuedTask.from_row(r) for r in rows]

    def get(self, task_id: int) -> QueuedTask | None:
        row = self.db.execute("SELECT * FROM human_tasks WHERE id = ?", (task_id,)).fetchone()
        return QueuedTask.from_row(row) if row else None

    def resolve(self, task_id: int, status: str = "done") -> bool:
        if status not in {"done", "skipped", "open"}:
            raise ValueError(f"unknown status {status!r}")
        cur = self.db.execute(
            "UPDATE human_tasks SET status = ?, done_ts = ? WHERE id = ?",
            (status, time.time() if status != "open" else None, task_id),
        )
        self.db.commit()
        return cur.rowcount > 0

    def count_open(self) -> int:
        row = self.db.execute("SELECT COUNT(*) AS n FROM human_tasks WHERE status = 'open'").fetchone()
        return int(row["n"])
