"""SQLite persistence: Rally's durable source of truth (PRD section 6).

Streamlit reruns the script on every click, so nothing that matters lives in session state.
The `events` table is append-only (enforced by triggers). Uniqueness constraints back the hard
invariants: one live campaign per opportunity, one seat per player per campaign, and at most one
invite / booking-link send per invite.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS opportunities (
    opportunity_id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    payload TEXT NOT NULL,
    first_detected_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS campaigns (
    campaign_id TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL,
    type TEXT NOT NULL,
    state TEXT NOT NULL,
    strategy TEXT NOT NULL,
    approval TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    state_reason TEXT,
    hold_deadline TEXT,
    last_check TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_live_campaign ON campaigns(opportunity_id)
    WHERE state NOT IN ('CONFIRMED', 'EXPIRED', 'CANCELLED');

CREATE TABLE IF NOT EXISTS invites (
    invite_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL,
    player_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    status TEXT NOT NULL,
    channel TEXT,
    sent_at TEXT,
    reply_deadline TEXT,
    replied_at TEXT,
    last_intent TEXT,
    seat_no INTEGER,
    UNIQUE (campaign_id, player_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_seat ON invites(campaign_id, seat_no) WHERE seat_no IS NOT NULL;

CREATE TABLE IF NOT EXISTS messages (
    message_id TEXT PRIMARY KEY,
    campaign_id TEXT,
    invite_id TEXT,
    player_id TEXT NOT NULL,
    direction TEXT NOT NULL,
    kind TEXT NOT NULL,
    channel TEXT,
    body TEXT NOT NULL,
    sim_ts TEXT NOT NULL,
    simulated INTEGER NOT NULL DEFAULT 1
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_one_send ON messages(invite_id, kind)
    WHERE direction = 'out' AND kind IN ('invite', 'booking_link');

CREATE TABLE IF NOT EXISTS events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    sim_ts TEXT NOT NULL,
    real_ts TEXT NOT NULL,
    campaign_id TEXT,
    opportunity_id TEXT,
    actor TEXT NOT NULL,
    tool TEXT NOT NULL,
    event_type TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT,
    policy_version TEXT NOT NULL,
    detail TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
    BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
    BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;

CREATE TABLE IF NOT EXISTS consent_overrides (
    player_id TEXT PRIMARY KEY,
    opt_in_status TEXT NOT NULL,
    changed_at TEXT NOT NULL,
    source TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS prior_invites (player_id TEXT NOT NULL, sent_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS drafts (
    opportunity_id TEXT PRIMARY KEY,
    template TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    note TEXT
);

CREATE TABLE IF NOT EXISTS llm_calls (
    call_id INTEGER PRIMARY KEY AUTOINCREMENT,
    sim_ts TEXT,
    real_ts TEXT NOT NULL,
    purpose TEXT NOT NULL,
    mode TEXT NOT NULL,
    model TEXT,
    latency_ms INTEGER,
    input_tokens INTEGER,
    output_tokens INTEGER,
    ok INTEGER NOT NULL,
    error TEXT,
    request TEXT,
    response TEXT,
    campaign_id TEXT,
    invite_id TEXT
);
"""


def _iso(value: datetime) -> str:
    return value.isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, default=str, ensure_ascii=False)


class AuditWriteError(RuntimeError):
    """Raised when an event cannot be written. High-risk actions must not proceed without one."""


class Storage:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ---- meta / clock -------------------------------------------------------------------------
    def get_meta(self, key: str) -> Optional[str]:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute("INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                         (key, value))

    # ---- events (append-only) -----------------------------------------------------------------
    @staticmethod
    def log_event(conn: sqlite3.Connection, *, sim_ts: datetime, actor: str, tool: str, event_type: str,
                  policy_version: str, detail: dict | None = None, campaign_id: str | None = None,
                  opportunity_id: str | None = None, from_state: str | None = None,
                  to_state: str | None = None) -> int:
        try:
            cur = conn.execute(
                "INSERT INTO events (sim_ts, real_ts, campaign_id, opportunity_id, actor, tool, event_type, "
                "from_state, to_state, policy_version, detail) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (_iso(sim_ts), datetime.now(timezone.utc).isoformat(timespec="seconds"), campaign_id, opportunity_id,
                 actor, tool, event_type, from_state, to_state, policy_version, _json(detail or {})))
        except sqlite3.Error as exc:  # pragma: no cover - exercised only when the DB is broken
            raise AuditWriteError(f"could not write audit event {event_type}: {exc}") from exc
        return int(cur.lastrowid)

    def events(self, campaign_id: str | None = None, opportunity_id: str | None = None, limit: int = 2000) -> list[dict]:
        sql = "SELECT * FROM events"
        args: list[Any] = []
        clauses = []
        if campaign_id:
            clauses.append("campaign_id = ?")
            args.append(campaign_id)
        if opportunity_id:
            clauses.append("opportunity_id = ?")
            args.append(opportunity_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY event_id DESC LIMIT ?"
        args.append(limit)
        with self.connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["detail"] = json.loads(item["detail"])
            out.append(item)
        return out

    # ---- opportunities ------------------------------------------------------------------------
    def upsert_opportunity(self, conn: sqlite3.Connection, opp_id: str, opp_type: str, payload: dict,
                           now: datetime) -> bool:
        """Insert or refresh an opportunity. Returns True if it was new."""
        row = conn.execute("SELECT opportunity_id FROM opportunities WHERE opportunity_id = ?", (opp_id,)).fetchone()
        if row:
            conn.execute("UPDATE opportunities SET payload = ?, last_seen_at = ?, active = 1 WHERE opportunity_id = ?",
                         (_json(payload), _iso(now), opp_id))
            return False
        conn.execute("INSERT INTO opportunities VALUES (?,?,?,?,?,1)",
                     (opp_id, opp_type, _json(payload), _iso(now), _iso(now)))
        return True

    def deactivate_opportunity(self, conn: sqlite3.Connection, opp_id: str) -> None:
        conn.execute("UPDATE opportunities SET active = 0 WHERE opportunity_id = ?", (opp_id,))

    def opportunities(self, active_only: bool = False) -> list[dict]:
        sql = "SELECT * FROM opportunities" + (" WHERE active = 1" if active_only else "")
        with self.connect() as conn:
            rows = conn.execute(sql).fetchall()
        return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]

    def get_opportunity(self, opp_id: str) -> Optional[dict]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM opportunities WHERE opportunity_id = ?", (opp_id,)).fetchone()
        return {**dict(row), "payload": json.loads(row["payload"])} if row else None

    # ---- campaigns ----------------------------------------------------------------------------
    def insert_campaign(self, conn: sqlite3.Connection, *, campaign_id: str, opportunity_id: str, opp_type: str,
                        state: str, strategy: str, approval: dict, now: datetime) -> None:
        conn.execute("INSERT INTO campaigns (campaign_id, opportunity_id, type, state, strategy, approval, created_at, "
                     "updated_at) VALUES (?,?,?,?,?,?,?,?)",
                     (campaign_id, opportunity_id, opp_type, state, strategy, _json(approval), _iso(now), _iso(now)))

    def update_campaign(self, conn: sqlite3.Connection, campaign_id: str, now: datetime, **fields: Any) -> None:
        fields["updated_at"] = _iso(now)
        cols = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE campaigns SET {cols} WHERE campaign_id = ?", (*fields.values(), campaign_id))

    @staticmethod
    def _campaign_row(row: sqlite3.Row | None) -> Optional[dict]:
        if row is None:
            return None
        item = dict(row)
        item["approval"] = json.loads(item["approval"])
        return item

    def get_campaign(self, campaign_id: str, conn: sqlite3.Connection | None = None) -> Optional[dict]:
        if conn is not None:
            return self._campaign_row(conn.execute("SELECT * FROM campaigns WHERE campaign_id = ?",
                                                   (campaign_id,)).fetchone())
        with self.connect() as c:
            return self._campaign_row(c.execute("SELECT * FROM campaigns WHERE campaign_id = ?",
                                                (campaign_id,)).fetchone())

    def campaigns(self, opportunity_id: str | None = None) -> list[dict]:
        with self.connect() as conn:
            if opportunity_id:
                rows = conn.execute("SELECT * FROM campaigns WHERE opportunity_id = ? ORDER BY created_at",
                                    (opportunity_id,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM campaigns ORDER BY created_at").fetchall()
        return [self._campaign_row(r) for r in rows]

    # ---- invites ------------------------------------------------------------------------------
    def insert_invite(self, conn: sqlite3.Connection, *, invite_id: str, campaign_id: str, player_id: str, seq: int,
                      channel: str) -> None:
        conn.execute("INSERT INTO invites (invite_id, campaign_id, player_id, seq, status, channel) "
                     "VALUES (?,?,?,?, 'QUEUED', ?)", (invite_id, campaign_id, player_id, seq, channel))

    def update_invite(self, conn: sqlite3.Connection, invite_id: str, **fields: Any) -> None:
        cols = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE invites SET {cols} WHERE invite_id = ?", (*fields.values(), invite_id))

    def invites(self, campaign_id: str, conn: sqlite3.Connection | None = None) -> list[dict]:
        sql = "SELECT * FROM invites WHERE campaign_id = ? ORDER BY seq"
        if conn is not None:
            return [dict(r) for r in conn.execute(sql, (campaign_id,)).fetchall()]
        with self.connect() as c:
            return [dict(r) for r in c.execute(sql, (campaign_id,)).fetchall()]

    def get_invite(self, invite_id: str, conn: sqlite3.Connection | None = None) -> Optional[dict]:
        sql = "SELECT * FROM invites WHERE invite_id = ?"
        if conn is not None:
            row = conn.execute(sql, (invite_id,)).fetchone()
        else:
            with self.connect() as c:
                row = c.execute(sql, (invite_id,)).fetchone()
        return dict(row) if row else None

    def all_invites(self) -> list[dict]:
        with self.connect() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM invites").fetchall()]

    # ---- messages -----------------------------------------------------------------------------
    def insert_message(self, conn: sqlite3.Connection, *, message_id: str, campaign_id: str | None,
                       invite_id: str | None, player_id: str, direction: str, kind: str, channel: str | None,
                       body: str, now: datetime) -> None:
        conn.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,1)",
                     (message_id, campaign_id, invite_id, player_id, direction, kind, channel, body, _iso(now)))

    def messages(self, campaign_id: str | None = None, player_id: str | None = None) -> list[dict]:
        sql, args = "SELECT * FROM messages", []
        clauses = []
        if campaign_id:
            clauses.append("campaign_id = ?")
            args.append(campaign_id)
        if player_id:
            clauses.append("player_id = ?")
            args.append(player_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY sim_ts, rowid"
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]

    def count_messages(self, direction: str | None = None) -> int:
        with self.connect() as conn:
            if direction:
                return conn.execute("SELECT COUNT(*) FROM messages WHERE direction = ?", (direction,)).fetchone()[0]
            return conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]

    # ---- consent and contact history ----------------------------------------------------------
    def set_consent(self, conn: sqlite3.Connection, player_id: str, status: str, now: datetime, source: str) -> None:
        conn.execute("INSERT INTO consent_overrides VALUES (?,?,?,?) ON CONFLICT(player_id) DO UPDATE SET "
                     "opt_in_status = excluded.opt_in_status, changed_at = excluded.changed_at, source = excluded.source",
                     (player_id, status, _iso(now), source))

    def consent_overrides(self) -> dict[str, dict]:
        with self.connect() as conn:
            return {r["player_id"]: dict(r) for r in conn.execute("SELECT * FROM consent_overrides").fetchall()}

    def seed_prior_invites(self, rows: list[tuple[str, datetime]]) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM prior_invites")
            conn.executemany("INSERT INTO prior_invites VALUES (?, ?)", [(p, _iso(t)) for p, t in rows])

    def invite_times(self) -> dict[str, list[datetime]]:
        """All invitation send times per player: seeded history plus Rally sends."""
        out: dict[str, list[datetime]] = {}
        with self.connect() as conn:
            for r in conn.execute("SELECT player_id, sent_at FROM prior_invites").fetchall():
                out.setdefault(r["player_id"], []).append(datetime.fromisoformat(r["sent_at"]))
            for r in conn.execute("SELECT player_id, sent_at FROM invites WHERE sent_at IS NOT NULL").fetchall():
                out.setdefault(r["player_id"], []).append(datetime.fromisoformat(r["sent_at"]))
        return out

    # ---- drafts and LLM calls -----------------------------------------------------------------
    def get_draft(self, opp_id: str) -> Optional[dict]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM drafts WHERE opportunity_id = ?", (opp_id,)).fetchone()
        return dict(row) if row else None

    def set_draft(self, opp_id: str, template: str, source: str, now: datetime, note: str = "") -> None:
        with self.connect() as conn:
            conn.execute("INSERT INTO drafts VALUES (?,?,?,?,?) ON CONFLICT(opportunity_id) DO UPDATE SET "
                         "template = excluded.template, source = excluded.source, created_at = excluded.created_at, "
                         "note = excluded.note", (opp_id, template, source, _iso(now), note))

    def log_llm_call(self, record: dict) -> None:
        cols = ["sim_ts", "real_ts", "purpose", "mode", "model", "latency_ms", "input_tokens", "output_tokens", "ok",
                "error", "request", "response", "campaign_id", "invite_id"]
        values = [record.get(c) for c in cols]
        values[1] = values[1] or datetime.now(timezone.utc).isoformat(timespec="seconds")
        values[10] = _json(values[10]) if values[10] is not None and not isinstance(values[10], str) else values[10]
        values[11] = _json(values[11]) if values[11] is not None and not isinstance(values[11], str) else values[11]
        with self.connect() as conn:
            conn.execute(f"INSERT INTO llm_calls ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", values)

    def llm_calls(self) -> list[dict]:
        with self.connect() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM llm_calls ORDER BY call_id DESC").fetchall()]
