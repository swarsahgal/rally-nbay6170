"""Booking provider boundary (PRD decision D04).

Rally only reads from a provider: it never writes bookings, prices or payments. `MockBookingProvider`
stands in for Playtomic (or a club spreadsheet) and keeps its own SQLite file so the boundary stays
visible. The `simulate_*` methods are demo fixtures a manager clicks to play the provider's part.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional, Protocol

from .schemas import Court, Match, RosterPlayer, Snapshot


class ProviderUnavailable(RuntimeError):
    """The provider could not be reached or returned stale data. Callers must stop or escalate."""


class BookingProvider(Protocol):
    name: str

    def health(self) -> str: ...

    def get_snapshot(self, club_id: str, start: datetime, end: datetime, now: datetime) -> Snapshot: ...

    def get_match(self, match_id: str) -> Optional[Match]: ...

    def bookings_at(self, court_id: str, start: datetime, end: datetime) -> list[Match]: ...


PROVIDER_SCHEMA = """
CREATE TABLE IF NOT EXISTS courts (court_id TEXT PRIMARY KEY, name TEXT NOT NULL, court_type TEXT);
CREATE TABLE IF NOT EXISTS roster (player_id TEXT PRIMARY KEY, name TEXT, first_name TEXT, level REAL);
CREATE TABLE IF NOT EXISTS matches (
    match_id TEXT PRIMARY KEY, court_id TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL, kind TEXT NOT NULL,
    level REAL, player_ids TEXT NOT NULL, players_known INTEGER NOT NULL, capacity INTEGER NOT NULL,
    status TEXT NOT NULL, payment_status TEXT NOT NULL, dropout_player_ids TEXT NOT NULL, created_by TEXT NOT NULL,
    note TEXT
);
CREATE TABLE IF NOT EXISTS flags (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def _split(value: str | None) -> list[str]:
    return [v for v in (value or "").split(";") if v]


class MockBookingProvider:
    """A read-only provider backed by data/*.csv, plus demo-only fixture controls."""

    name = "mock_provider"

    def __init__(self, db_path: str | Path, data_dir: str | Path):
        self.db_path = Path(db_path)
        self.data_dir = Path(data_dir)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        fresh = not self.db_path.exists()
        with self._conn() as conn:
            conn.executescript(PROVIDER_SCHEMA)
        if fresh:
            self._seed()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _seed(self) -> None:
        with self._conn() as conn:
            with open(self.data_dir / "courts.csv", newline="", encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    conn.execute("INSERT INTO courts VALUES (?,?,?)", (r["court_id"], r["name"], r["court_type"]))
            with open(self.data_dir / "players.csv", newline="", encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    conn.execute("INSERT INTO roster VALUES (?,?,?,?)",
                                 (r["player_id"], r["name"], r["first_name"], float(r["level"])))
            with open(self.data_dir / "matches.csv", newline="", encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    conn.execute(
                        "INSERT INTO matches VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (r["match_id"], r["court_id"], r["start"], r["end"], r["kind"],
                         float(r["level"]) if r["level"] else None, json.dumps(_split(r["player_ids"])),
                         1 if r["players_known"] == "True" else 0, int(r["capacity"]), r["status"],
                         r["payment_status"], json.dumps(_split(r["dropout_player_ids"])), r["created_by"], r["note"]))
            conn.execute("INSERT OR REPLACE INTO flags VALUES ('outage', '0')")

    @staticmethod
    def _match(row: sqlite3.Row) -> Match:
        return Match(
            match_id=row["match_id"], court_id=row["court_id"], start=datetime.fromisoformat(row["start"]),
            end=datetime.fromisoformat(row["end"]), kind=row["kind"], level=row["level"],
            player_ids=json.loads(row["player_ids"]), players_known=bool(row["players_known"]),
            capacity=row["capacity"], status=row["status"], payment_status=row["payment_status"],
            dropout_player_ids=json.loads(row["dropout_player_ids"]), created_by=row["created_by"],
            note=row["note"] or "")

    def _check_up(self) -> None:
        if self.health() != "ok":
            raise ProviderUnavailable("mock provider is in a simulated outage")

    # ---- read API (what a real Playtomic adapter would implement) -----------------------------
    def health(self) -> str:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM flags WHERE key = 'outage'").fetchone()
        return "unavailable" if row and row["value"] == "1" else "ok"

    def get_snapshot(self, club_id: str, start: datetime, end: datetime, now: datetime) -> Snapshot:
        self._check_up()
        with self._conn() as conn:
            courts = [Court(**dict(r)) for r in conn.execute("SELECT * FROM courts ORDER BY court_id")]
            roster = [RosterPlayer(**dict(r)) for r in conn.execute("SELECT * FROM roster ORDER BY player_id")]
            rows = conn.execute("SELECT * FROM matches WHERE status = 'confirmed' AND start < ? AND end > ?",
                                (end.isoformat(), start.isoformat())).fetchall()
        return Snapshot(club_id=club_id, taken_at=now, window_start=start, window_end=end, courts=courts,
                        matches=[self._match(r) for r in rows], roster=roster)

    def get_match(self, match_id: str) -> Optional[Match]:
        self._check_up()
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM matches WHERE match_id = ?", (match_id,)).fetchone()
        return self._match(row) if row else None

    def bookings_at(self, court_id: str, start: datetime, end: datetime) -> list[Match]:
        self._check_up()
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM matches WHERE court_id = ? AND status = 'confirmed' AND start < ? "
                                "AND end > ?", (court_id, end.isoformat(), start.isoformat())).fetchall()
        return [self._match(r) for r in rows]

    def all_matches(self) -> list[Match]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM matches WHERE status = 'confirmed' ORDER BY start, court_id").fetchall()
        return [self._match(r) for r in rows]

    # ---- demo fixtures: the manager plays the provider's part (all clearly SIMULATED) ---------
    def set_outage(self, on: bool) -> None:
        with self._conn() as conn:
            conn.execute("INSERT OR REPLACE INTO flags VALUES ('outage', ?)", ("1" if on else "0",))

    def simulate_seat_filled(self, match_id: str, player_id: str, payment_status: str = "paid") -> None:
        """The invited player completed the booking link, so the provider now lists them in the match."""
        with self._conn() as conn:
            row = conn.execute("SELECT player_ids, capacity FROM matches WHERE match_id = ?", (match_id,)).fetchone()
            if row is None:
                raise ValueError(f"unknown match {match_id}")
            players = json.loads(row["player_ids"])
            if player_id not in players:
                if len(players) >= row["capacity"]:
                    raise ValueError("match is already full")
                players.append(player_id)
            conn.execute("UPDATE matches SET player_ids = ?, payment_status = ? WHERE match_id = ?",
                         (json.dumps(players), payment_status, match_id))

    def simulate_seat_taken_elsewhere(self, match_id: str, outsider_id: str = "EXT-WALKIN") -> None:
        """Someone outside Rally (e.g. at the front desk) filled the open seat."""
        self.simulate_seat_filled(match_id, outsider_id, payment_status="paid")

    def simulate_group_booking(self, court_id: str, start: datetime, end: datetime, player_ids: list[str],
                               payment_status: str = "paid") -> str:
        """The group used the booking link: the provider now shows a booking for the court."""
        booking_id = f"B-RALLY-{start:%Y%m%d-%H%M}-{court_id}"
        with self._conn() as conn:
            conn.execute("INSERT OR REPLACE INTO matches VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (booking_id, court_id, start.isoformat(), end.isoformat(), "private", None,
                          json.dumps(player_ids), 1, 4, "confirmed", payment_status, "[]", "rally_handoff",
                          "Booked through Rally booking link (simulated)"))
        return booking_id

    def simulate_external_booking(self, court_id: str, start: datetime, end: datetime) -> str:
        """Another customer booked the court directly, so Rally's view is now stale."""
        booking_id = f"B-EXT-{start:%Y%m%d-%H%M}-{court_id}"
        with self._conn() as conn:
            conn.execute("INSERT OR REPLACE INTO matches VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (booking_id, court_id, start.isoformat(), end.isoformat(), "private", None, "[]", 0, 4,
                          "confirmed", "paid", "[]", "external", "Booked directly by another customer (simulated)"))
        return booking_id


class PlaytomicProvider:  # pragma: no cover - future adapter boundary
    """Placeholder for a real read-only Playtomic adapter.

    Per the Playtomic API guide referenced in the PRD, the API is read-only: bookings and some player
    details are exposed, and dedicated player-data endpoints are planned. Rally must never assume write
    access; club authorisation and platform terms apply before any live use.
    """

    name = "playtomic"

    def __init__(self, *_, **__):
        raise NotImplementedError("Live Playtomic access is out of scope for HW2 (PRD D04).")
