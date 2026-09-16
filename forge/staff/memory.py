"""What the office remembers.

Two streams, kept in the same database as the ledger so a single file is
the whole state of the business: the activity feed the staff post to during
a standup, and the chat threads you hold with them.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

from ..core.ledger import Ledger

_SCHEMA = """
CREATE TABLE IF NOT EXISTS remarks (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL NOT NULL,
    persona TEXT NOT NULL,
    kind    TEXT NOT NULL DEFAULT 'note',
    text    TEXT NOT NULL,
    meta    TEXT NOT NULL DEFAULT '{}',
    cycle   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_remarks_cycle ON remarks(cycle);
CREATE TABLE IF NOT EXISTS chat_messages (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL NOT NULL,
    persona TEXT NOT NULL,
    speaker TEXT NOT NULL,
    text    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_persona ON chat_messages(persona, id);
"""

#: Kinds carry meaning in the UI: decisions and rulings are highlighted.
KIND_NOTE = "note"
KIND_DECISION = "decision"
KIND_RULING = "ruling"
KIND_ALERT = "alert"
KIND_RESULT = "result"


@dataclass(frozen=True)
class Remark:
    id: int
    ts: float
    persona: str
    kind: str
    text: str
    meta: dict
    cycle: int

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "ts": self.ts,
            "persona": self.persona,
            "kind": self.kind,
            "text": self.text,
            "meta": self.meta,
            "cycle": self.cycle,
        }


@dataclass(frozen=True)
class ChatMessage:
    id: int
    ts: float
    persona: str
    speaker: str
    text: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "ts": self.ts,
            "persona": self.persona,
            "speaker": self.speaker,
            "text": self.text,
        }


class Memory:
    def __init__(self, ledger: Ledger):
        self.ledger = ledger
        self.db = ledger.db
        self.db.executescript(_SCHEMA)
        self.db.commit()

    # -- the feed -------------------------------------------------------
    def say(
        self,
        persona: str,
        text: str,
        *,
        kind: str = KIND_NOTE,
        meta: dict | None = None,
        cycle: int = 0,
    ) -> int:
        cur = self.db.execute(
            "INSERT INTO remarks (ts, persona, kind, text, meta, cycle) VALUES (?,?,?,?,?,?)",
            (time.time(), persona, kind, text, json.dumps(meta or {}), cycle),
        )
        self.db.commit()
        return int(cur.lastrowid)

    def feed(self, limit: int = 60, since_id: int = 0) -> list[Remark]:
        rows = self.db.execute(
            "SELECT * FROM remarks WHERE id > ? ORDER BY id DESC LIMIT ?", (since_id, limit)
        ).fetchall()
        return [
            Remark(
                id=int(r["id"]),
                ts=float(r["ts"]),
                persona=r["persona"],
                kind=r["kind"],
                text=r["text"],
                meta=json.loads(r["meta"] or "{}"),
                cycle=int(r["cycle"]),
            )
            for r in rows
        ]

    def next_cycle(self) -> int:
        n = int(self.ledger.get_state("office:cycle", "0") or 0) + 1
        self.ledger.set_state("office:cycle", str(n))
        return n

    @property
    def cycle(self) -> int:
        return int(self.ledger.get_state("office:cycle", "0") or 0)

    # -- chat -----------------------------------------------------------
    def add_chat(self, persona: str, speaker: str, text: str) -> int:
        cur = self.db.execute(
            "INSERT INTO chat_messages (ts, persona, speaker, text) VALUES (?,?,?,?)",
            (time.time(), persona, speaker, text),
        )
        self.db.commit()
        return int(cur.lastrowid)

    def thread(self, persona: str, limit: int = 40) -> list[ChatMessage]:
        rows = self.db.execute(
            "SELECT * FROM (SELECT * FROM chat_messages WHERE persona = ? "
            "ORDER BY id DESC LIMIT ?) ORDER BY id ASC",
            (persona, limit),
        ).fetchall()
        return [
            ChatMessage(
                id=int(r["id"]),
                ts=float(r["ts"]),
                persona=r["persona"],
                speaker=r["speaker"],
                text=r["text"],
            )
            for r in rows
        ]

    def clear_thread(self, persona: str) -> int:
        cur = self.db.execute("DELETE FROM chat_messages WHERE persona = ?", (persona,))
        self.db.commit()
        return cur.rowcount
