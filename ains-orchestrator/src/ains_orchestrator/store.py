"""I5: SQLite session store on the /work volume. One row per story, an event per transition."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ains_orchestrator.errors import StoreError

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    key TEXT PRIMARY KEY,
    target TEXT NOT NULL,
    repo TEXT NOT NULL,
    flow TEXT NOT NULL,
    stage TEXT NOT NULL,
    gate_state TEXT NOT NULL DEFAULT '{}',
    branch TEXT,
    pr_number INTEGER,
    head_sha TEXT,
    reviewed_sha TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created TEXT NOT NULL,
    updated TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS events_key ON events(key, id);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Session:
    key: str
    target: str
    repo: str
    flow: str
    stage: str
    gate_state: dict[str, Any]
    branch: str | None
    pr_number: int | None
    head_sha: str | None
    reviewed_sha: str | None
    attempts: int
    last_error: str | None
    created: str
    updated: str


@dataclass
class Event:
    id: int
    key: str
    ts: str
    kind: str
    detail: dict[str, Any]


class Store:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(self.path, isolation_level=None)
            self._db.row_factory = sqlite3.Row
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(SCHEMA)
        except (OSError, sqlite3.Error) as e:
            raise StoreError(f"cannot open session store at {self.path}: {e}") from e

    # ------------------------------------------------------------------ sessions
    def create_session(self, key: str, target: str, repo: str, flow: str, stage: str) -> Session:
        now = _now()
        self._db.execute(
            "INSERT INTO sessions(key,target,repo,flow,stage,created,updated) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET updated=excluded.updated",
            (key, target, repo, flow, stage, now, now),
        )
        self.add_event(key, "session.created", {"target": target, "repo": repo, "flow": flow, "stage": stage})
        return self.get_session(key)  # type: ignore[return-value]

    def get_session(self, key: str) -> Session | None:
        row = self._db.execute("SELECT * FROM sessions WHERE key=?", (key,)).fetchone()
        return self._to_session(row) if row else None

    def update_session(self, key: str, **fields: Any) -> Session:
        allowed = {"stage", "gate_state", "branch", "pr_number", "head_sha", "reviewed_sha", "attempts", "last_error"}
        bad = set(fields) - allowed
        if bad:
            raise StoreError(f"unknown session fields: {sorted(bad)}")
        if "gate_state" in fields and not isinstance(fields["gate_state"], str):
            fields["gate_state"] = json.dumps(fields["gate_state"])
        sets = ", ".join(f"{k}=?" for k in fields) + ", updated=?"
        cur = self._db.execute(f"UPDATE sessions SET {sets} WHERE key=?", (*fields.values(), _now(), key))
        if cur.rowcount == 0:
            raise StoreError(f"no session {key}")
        return self.get_session(key)  # type: ignore[return-value]

    def list_sessions(self, *, stage: str | None = None, target: str | None = None) -> list[Session]:
        q, args = "SELECT * FROM sessions WHERE 1=1", []
        if stage:
            q, args = q + " AND stage=?", [*args, stage]
        if target:
            q, args = q + " AND target=?", [*args, target]
        return [self._to_session(r) for r in self._db.execute(q + " ORDER BY created", args).fetchall()]

    def active_keys(self) -> set[str]:
        rows = self._db.execute(
            "SELECT key FROM sessions WHERE stage NOT IN ('released','abandoned','pr_reviewed')"
        ).fetchall()
        return {r["key"] for r in rows}

    # ------------------------------------------------------------------ events
    def add_event(self, key: str, kind: str, detail: dict[str, Any] | None = None) -> int:
        cur = self._db.execute(
            "INSERT INTO events(key,ts,kind,detail) VALUES (?,?,?,?)",
            (key, _now(), kind, json.dumps(detail or {}, default=str)),
        )
        return int(cur.lastrowid or 0)

    def events(self, key: str) -> list[Event]:
        rows = self._db.execute("SELECT * FROM events WHERE key=? ORDER BY id", (key,)).fetchall()
        return [Event(r["id"], r["key"], r["ts"], r["kind"], json.loads(r["detail"])) for r in rows]

    def export(self, key: str) -> dict[str, Any]:
        s = self.get_session(key)
        if not s:
            raise StoreError(f"no session {key}")
        return {"session": s.__dict__, "events": [e.__dict__ for e in self.events(key)]}

    # ------------------------------------------------------------------ misc
    @staticmethod
    def _to_session(r: sqlite3.Row) -> Session:
        d = dict(r)
        d["gate_state"] = json.loads(d["gate_state"] or "{}")
        return Session(**d)

    def close(self) -> None:
        self._db.close()
