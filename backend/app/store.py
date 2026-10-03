"""Lightweight persistence for user-saved plans + notes.

Uses the Python standard-library ``sqlite3`` so there are no extra dependencies
and the app still runs with zero configuration. Each saved plan belongs to a
user identified by an opaque token the client generates and stores locally
(sent as the ``X-User-Token`` header). That gives per-user separation without a
login system; the same token used on another device syncs the same plans.

All functions are synchronous and cheap; FastAPI runs the route handlers that
call them in a threadpool, which is fine for this low volume.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from typing import Optional

from app.config import get_settings

_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None


def _connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        settings = get_settings()
        _conn = sqlite3.connect(
            settings.db_path, check_same_thread=False
        )
        _conn.row_factory = sqlite3.Row
    return _conn


def init_db() -> None:
    """Create the table if it doesn't exist. Safe to call on every startup."""
    with _lock:
        conn = _connect()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS saved_plans (
                id          TEXT PRIMARY KEY,
                user_token  TEXT NOT NULL,
                title       TEXT NOT NULL,
                city        TEXT NOT NULL,
                notes       TEXT NOT NULL DEFAULT '',
                plan_json   TEXT NOT NULL,
                request_json TEXT,
                created_at  REAL NOT NULL,
                updated_at  REAL NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_saved_user ON saved_plans(user_token)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id         TEXT PRIMARY KEY,
                email      TEXT NOT NULL UNIQUE,
                pw_hash    TEXT NOT NULL,
                pw_salt    TEXT NOT NULL,
                created_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                token      TEXT PRIMARY KEY,
                user_id    TEXT NOT NULL,
                created_at REAL NOT NULL
            )
            """
        )
        # One row per trip generation, for per-user rate limiting.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trip_events (
                id         TEXT PRIMARY KEY,
                user_token TEXT NOT NULL,
                created_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_trip_events_user ON trip_events(user_token, created_at)"
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Rate limiting (trip generations per user, rolling window)
# ---------------------------------------------------------------------------


def count_recent_trips(user_token: str, since_ts: float) -> tuple[int, Optional[float]]:
    """How many trips this user generated since ``since_ts`` (a rolling window),
    plus the oldest timestamp in that window (so the caller can tell the user
    when they can plan again). Returns (count, oldest_ts|None)."""
    with _lock:
        conn = _connect()
        row = conn.execute(
            "SELECT COUNT(*) AS n, MIN(created_at) AS oldest FROM trip_events "
            "WHERE user_token = ? AND created_at >= ?",
            (user_token, since_ts),
        ).fetchone()
    return (int(row["n"] or 0), row["oldest"])


def record_trip_event(user_token: str) -> None:
    """Record that this user generated a trip (counts toward the rate limit)."""
    with _lock:
        conn = _connect()
        conn.execute(
            "INSERT INTO trip_events (id, user_token, created_at) VALUES (?, ?, ?)",
            (uuid.uuid4().hex, user_token, time.time()),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Authentication (lightweight: pbkdf2 password hashing, opaque session tokens)
# ---------------------------------------------------------------------------


def _hash_pw(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), 200_000
    ).hex()


def create_user(email: str, password: str) -> Optional[dict]:
    """Returns the new user dict, or None if the email already exists."""
    email = email.strip().lower()
    uid = uuid.uuid4().hex
    salt = os.urandom(16).hex()
    pw_hash = _hash_pw(password, salt)
    now = time.time()
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO users (id, email, pw_hash, pw_salt, created_at) VALUES (?, ?, ?, ?, ?)",
                (uid, email, pw_hash, salt, now),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            return None
    return {"id": uid, "email": email}


def verify_user(email: str, password: str) -> Optional[dict]:
    email = email.strip().lower()
    with _lock:
        conn = _connect()
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if not row:
        return None
    if _hash_pw(password, row["pw_salt"]) != row["pw_hash"]:
        return None
    return {"id": row["id"], "email": row["email"]}


def create_session(user_id: str) -> str:
    token = uuid.uuid4().hex + uuid.uuid4().hex
    with _lock:
        conn = _connect()
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at) VALUES (?, ?, ?)",
            (token, user_id, time.time()),
        )
        conn.commit()
    return token


def user_for_token(token: str) -> Optional[dict]:
    """Resolve a session token to {id, email}, or None."""
    if not token:
        return None
    with _lock:
        conn = _connect()
        row = conn.execute(
            "SELECT u.id AS id, u.email AS email FROM sessions s "
            "JOIN users u ON u.id = s.user_id WHERE s.token = ?",
            (token,),
        ).fetchone()
    return {"id": row["id"], "email": row["email"]} if row else None


def delete_session(token: str) -> None:
    with _lock:
        conn = _connect()
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "city": row["city"],
        "notes": row["notes"],
        "plan": json.loads(row["plan_json"]),
        "request": json.loads(row["request_json"]) if row["request_json"] else None,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def create_plan(
    user_token: str,
    title: str,
    city: str,
    plan: dict,
    notes: str = "",
    request: Optional[dict] = None,
) -> dict:
    now = time.time()
    plan_id = uuid.uuid4().hex
    with _lock:
        conn = _connect()
        conn.execute(
            """
            INSERT INTO saved_plans
                (id, user_token, title, city, notes, plan_json, request_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                plan_id,
                user_token,
                title,
                city,
                notes or "",
                json.dumps(plan),
                json.dumps(request) if request is not None else None,
                now,
                now,
            ),
        )
        conn.commit()
    return get_plan(user_token, plan_id)  # type: ignore[return-value]


def list_plans(user_token: str) -> list[dict]:
    with _lock:
        conn = _connect()
        rows = conn.execute(
            "SELECT * FROM saved_plans WHERE user_token = ? ORDER BY updated_at DESC",
            (user_token,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_plan(user_token: str, plan_id: str) -> Optional[dict]:
    with _lock:
        conn = _connect()
        row = conn.execute(
            "SELECT * FROM saved_plans WHERE id = ? AND user_token = ?",
            (plan_id, user_token),
        ).fetchone()
    return _row_to_dict(row) if row else None


def update_plan(
    user_token: str,
    plan_id: str,
    *,
    title: Optional[str] = None,
    notes: Optional[str] = None,
) -> Optional[dict]:
    sets = []
    params: list = []
    if title is not None:
        sets.append("title = ?")
        params.append(title)
    if notes is not None:
        sets.append("notes = ?")
        params.append(notes)
    if not sets:
        return get_plan(user_token, plan_id)
    sets.append("updated_at = ?")
    params.append(time.time())
    params.extend([plan_id, user_token])
    with _lock:
        conn = _connect()
        cur = conn.execute(
            f"UPDATE saved_plans SET {', '.join(sets)} WHERE id = ? AND user_token = ?",
            params,
        )
        conn.commit()
        if cur.rowcount == 0:
            return None
    return get_plan(user_token, plan_id)


def delete_plan(user_token: str, plan_id: str) -> bool:
    with _lock:
        conn = _connect()
        cur = conn.execute(
            "DELETE FROM saved_plans WHERE id = ? AND user_token = ?",
            (plan_id, user_token),
        )
        conn.commit()
        return cur.rowcount > 0
