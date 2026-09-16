"""Ideas, the reviews they collect, and the work they get broken into.

This is the unit the office runs on. You bring an idea — any idea — and it
moves through the same pipeline every time:

    proposed -> vetting -> accepted | parked | declined
                             |
                             v
                        in_progress -> delivered | abandoned

Nothing here knows what an idea is *about*. That is the point: the staff
are meant to take whatever you hand them, not pick from a menu.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

from ..core.ledger import Ledger

# -- idea status -------------------------------------------------------
PROPOSED = "proposed"
VETTING = "vetting"
ACCEPTED = "accepted"
PARKED = "parked"
DECLINED = "declined"
IN_PROGRESS = "in_progress"
DELIVERED = "delivered"
ABANDONED = "abandoned"

OPEN_STATUSES = {PROPOSED, VETTING, ACCEPTED, IN_PROGRESS}
LIVE_STATUSES = {ACCEPTED, IN_PROGRESS}

# -- stances a reviewer can take ---------------------------------------
FOR = "for"
AGAINST = "against"
UNSURE = "unsure"

# -- assignment status -------------------------------------------------
TODO = "todo"
DOING = "doing"
BLOCKED = "blocked"
DONE = "done"

#: What kind of work an assignment is. Drives who gets it and what the
#: owner actually produces when they sit down to it.
KIND_RESEARCH = "research"
KIND_AUDIENCE = "audience"
KIND_BUDGET = "budget"
KIND_BUILD = "build"
KIND_DECIDE = "decide"
KIND_ASK = "ask_user"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ideas (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         REAL NOT NULL,
    title      TEXT NOT NULL,
    body       TEXT NOT NULL DEFAULT '',
    source     TEXT NOT NULL DEFAULT 'you',
    status     TEXT NOT NULL DEFAULT 'proposed',
    slug       TEXT NOT NULL,
    decision   TEXT NOT NULL DEFAULT '',
    decided_at REAL
);
CREATE TABLE IF NOT EXISTS reviews (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         REAL NOT NULL,
    idea_id    INTEGER NOT NULL REFERENCES ideas(id) ON DELETE CASCADE,
    persona    TEXT NOT NULL,
    stance     TEXT NOT NULL,
    headline   TEXT NOT NULL DEFAULT '',
    points     TEXT NOT NULL DEFAULT '[]',
    questions  TEXT NOT NULL DEFAULT '[]',
    UNIQUE (idea_id, persona)
);
CREATE TABLE IF NOT EXISTS assignments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         REAL NOT NULL,
    idea_id    INTEGER NOT NULL REFERENCES ideas(id) ON DELETE CASCADE,
    owner      TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'research',
    title      TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT '',
    status     TEXT NOT NULL DEFAULT 'todo',
    blocked_on TEXT NOT NULL DEFAULT '',
    notes      TEXT NOT NULL DEFAULT '[]',
    seq        INTEGER NOT NULL DEFAULT 0,
    updated    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reviews_idea ON reviews(idea_id);
CREATE INDEX IF NOT EXISTS idx_assignments_idea ON assignments(idea_id);
CREATE INDEX IF NOT EXISTS idx_assignments_owner ON assignments(owner, status);
"""


def slugify(text: str, fallback: str = "idea") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (slug or fallback)[:48]


def title_of(text: str) -> str:
    """First sentence or line, trimmed to something that fits on a card."""
    first = text.strip().splitlines()[0] if text.strip() else "Untitled idea"
    first = re.split(r"(?<=[.!?])\s", first)[0].strip()
    return (first[:97] + "…") if len(first) > 98 else first


@dataclass
class Idea:
    id: int
    ts: float
    title: str
    body: str
    source: str
    status: str
    slug: str
    decision: str = ""
    decided_at: float | None = None

    @property
    def text(self) -> str:
        """Everything the staff know about it, for reading and reasoning."""
        return f"{self.title}\n\n{self.body}".strip()

    @property
    def open(self) -> bool:
        return self.status in OPEN_STATUSES

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "ts": self.ts,
            "title": self.title,
            "body": self.body,
            "source": self.source,
            "status": self.status,
            "slug": self.slug,
            "decision": self.decision,
            "decided_at": self.decided_at,
        }


@dataclass
class Review:
    id: int
    ts: float
    idea_id: int
    persona: str
    stance: str
    headline: str
    points: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "ts": self.ts,
            "idea_id": self.idea_id,
            "persona": self.persona,
            "stance": self.stance,
            "headline": self.headline,
            "points": self.points,
            "questions": self.questions,
        }


@dataclass
class Assignment:
    id: int
    ts: float
    idea_id: int
    owner: str
    kind: str
    title: str
    detail: str
    status: str
    blocked_on: str
    notes: list[str] = field(default_factory=list)
    seq: int = 0
    updated: float = 0.0

    @property
    def finished(self) -> bool:
        return self.status == DONE

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "ts": self.ts,
            "idea_id": self.idea_id,
            "owner": self.owner,
            "kind": self.kind,
            "title": self.title,
            "detail": self.detail,
            "status": self.status,
            "blocked_on": self.blocked_on,
            "notes": self.notes,
            "seq": self.seq,
            "updated": self.updated,
        }


class IdeaBook:
    """Storage for ideas, reviews and assignments, in the office database."""

    def __init__(self, ledger: Ledger):
        self.db = ledger.db
        self.db.executescript(_SCHEMA)
        self.db.commit()

    # -- ideas ---------------------------------------------------------
    def propose(self, body: str, *, title: str = "", source: str = "you") -> Idea:
        body = body.strip()
        if not body:
            raise ValueError("an idea needs some words in it")
        title = (title or title_of(body)).strip()
        cur = self.db.execute(
            "INSERT INTO ideas (ts, title, body, source, status, slug) VALUES (?,?,?,?,?,?)",
            (time.time(), title, body, source, PROPOSED, slugify(title)),
        )
        self.db.commit()
        return self.idea(int(cur.lastrowid))  # type: ignore[return-value]

    def idea(self, idea_id: int) -> Idea | None:
        row = self.db.execute("SELECT * FROM ideas WHERE id = ?", (idea_id,)).fetchone()
        return _idea(row) if row else None

    def ideas(self, *, status: str | set[str] | None = None, limit: int = 100) -> list[Idea]:
        if status is None:
            rows = self.db.execute(
                "SELECT * FROM ideas ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        else:
            wanted = {status} if isinstance(status, str) else set(status)
            marks = ",".join("?" * len(wanted))
            rows = self.db.execute(
                f"SELECT * FROM ideas WHERE status IN ({marks}) ORDER BY id DESC LIMIT ?",
                (*wanted, limit),
            ).fetchall()
        return [_idea(r) for r in rows]

    def set_status(self, idea_id: int, status: str, decision: str = "") -> None:
        self.db.execute(
            "UPDATE ideas SET status = ?, decision = COALESCE(NULLIF(?,''), decision), "
            "decided_at = CASE WHEN ? IN ('accepted','parked','declined') THEN ? ELSE decided_at END "
            "WHERE id = ?",
            (status, decision, status, time.time(), idea_id),
        )
        self.db.commit()

    # -- reviews -------------------------------------------------------
    def add_review(
        self,
        idea_id: int,
        persona: str,
        stance: str,
        headline: str,
        points: list[str] | None = None,
        questions: list[str] | None = None,
    ) -> Review:
        if stance not in {FOR, AGAINST, UNSURE}:
            raise ValueError(f"stance must be for, against or unsure, got {stance!r}")
        self.db.execute(
            "INSERT INTO reviews (ts, idea_id, persona, stance, headline, points, questions) "
            "VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(idea_id, persona) DO UPDATE SET "
            "ts=excluded.ts, stance=excluded.stance, headline=excluded.headline, "
            "points=excluded.points, questions=excluded.questions",
            (
                time.time(), idea_id, persona, stance, headline,
                json.dumps(points or []), json.dumps(questions or []),
            ),
        )
        self.db.commit()
        row = self.db.execute(
            "SELECT * FROM reviews WHERE idea_id = ? AND persona = ?", (idea_id, persona)
        ).fetchone()
        return _review(row)

    def reviews(self, idea_id: int) -> list[Review]:
        rows = self.db.execute(
            "SELECT * FROM reviews WHERE idea_id = ? ORDER BY id ASC", (idea_id,)
        ).fetchall()
        return [_review(r) for r in rows]

    def tally(self, idea_id: int) -> dict[str, int]:
        counts = {FOR: 0, AGAINST: 0, UNSURE: 0}
        for review in self.reviews(idea_id):
            counts[review.stance] = counts.get(review.stance, 0) + 1
        return counts

    # -- assignments ---------------------------------------------------
    def assign(
        self,
        idea_id: int,
        owner: str,
        title: str,
        *,
        kind: str = KIND_RESEARCH,
        detail: str = "",
        seq: int = 0,
    ) -> Assignment:
        now = time.time()
        cur = self.db.execute(
            "INSERT INTO assignments (ts, idea_id, owner, kind, title, detail, seq, updated) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (now, idea_id, owner, kind, title, detail, seq, now),
        )
        self.db.commit()
        return self.assignment(int(cur.lastrowid))  # type: ignore[return-value]

    def assignment(self, assignment_id: int) -> Assignment | None:
        row = self.db.execute(
            "SELECT * FROM assignments WHERE id = ?", (assignment_id,)
        ).fetchone()
        return _assignment(row) if row else None

    def assignments(
        self,
        *,
        idea_id: int | None = None,
        owner: str | None = None,
        status: str | set[str] | None = None,
    ) -> list[Assignment]:
        clauses, params = [], []
        if idea_id is not None:
            clauses.append("idea_id = ?")
            params.append(idea_id)
        if owner is not None:
            clauses.append("owner = ?")
            params.append(owner)
        if status is not None:
            wanted = {status} if isinstance(status, str) else set(status)
            clauses.append(f"status IN ({','.join('?' * len(wanted))})")
            params.extend(wanted)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.db.execute(
            f"SELECT * FROM assignments {where} ORDER BY seq ASC, id ASC", params
        ).fetchall()
        return [_assignment(r) for r in rows]

    def update_assignment(
        self,
        assignment_id: int,
        *,
        status: str | None = None,
        blocked_on: str | None = None,
        note: str | None = None,
    ) -> Assignment | None:
        current = self.assignment(assignment_id)
        if current is None:
            return None
        if status is not None and status not in {TODO, DOING, BLOCKED, DONE}:
            raise ValueError(f"unknown assignment status {status!r}")
        notes = current.notes + ([note] if note else [])
        self.db.execute(
            "UPDATE assignments SET status = ?, blocked_on = ?, notes = ?, updated = ? WHERE id = ?",
            (
                status or current.status,
                current.blocked_on if blocked_on is None else blocked_on,
                json.dumps(notes[-40:]),
                time.time(),
                assignment_id,
            ),
        )
        self.db.commit()
        return self.assignment(assignment_id)

    def next_for(self, owner: str) -> Assignment | None:
        """What is on this person's desk right now.

        Blocked work counts: a desk holding three questions for you is not
        an idle desk, and showing it as one hides the thing you need to do.
        """
        for wanted in (DOING, TODO, BLOCKED):
            found = self.assignments(owner=owner, status=wanted)
            if found:
                return found[0]
        return None

    def progress(self, idea_id: int) -> dict[str, int]:
        counts = {TODO: 0, DOING: 0, BLOCKED: 0, DONE: 0}
        for a in self.assignments(idea_id=idea_id):
            counts[a.status] = counts.get(a.status, 0) + 1
        return counts


# ----------------------------------------------------------------------
def _idea(row) -> Idea:
    return Idea(
        id=int(row["id"]), ts=float(row["ts"]), title=row["title"], body=row["body"],
        source=row["source"], status=row["status"], slug=row["slug"],
        decision=row["decision"], decided_at=row["decided_at"],
    )


def _review(row) -> Review:
    return Review(
        id=int(row["id"]), ts=float(row["ts"]), idea_id=int(row["idea_id"]),
        persona=row["persona"], stance=row["stance"], headline=row["headline"],
        points=json.loads(row["points"] or "[]"),
        questions=json.loads(row["questions"] or "[]"),
    )


def _assignment(row) -> Assignment:
    return Assignment(
        id=int(row["id"]), ts=float(row["ts"]), idea_id=int(row["idea_id"]),
        owner=row["owner"], kind=row["kind"], title=row["title"], detail=row["detail"],
        status=row["status"], blocked_on=row["blocked_on"],
        notes=json.loads(row["notes"] or "[]"), seq=int(row["seq"]),
        updated=float(row["updated"]),
    )
