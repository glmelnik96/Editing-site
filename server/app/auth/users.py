"""Whitelist и пользователи."""
from __future__ import annotations

import sqlite3

from server.app.util import new_id, now_iso


def normalize_email(email: str) -> str:
    return email.strip().lower()


def is_whitelisted(conn: sqlite3.Connection, email: str, admin_email: str) -> bool:
    e = normalize_email(email)
    if not e:
        return False
    if admin_email and e == normalize_email(admin_email):
        return True
    return conn.execute("SELECT 1 FROM whitelist WHERE email = ?", (e,)).fetchone() is not None


def upsert_user(conn: sqlite3.Connection, *, email: str, name: str, admin_email: str) -> sqlite3.Row:
    e = normalize_email(email)
    role = "admin" if admin_email and e == normalize_email(admin_email) else "user"
    row = conn.execute("SELECT id FROM users WHERE email = ?", (e,)).fetchone()
    if row is None:
        uid = new_id("usr")
        conn.execute(
            "INSERT INTO users (id, email, name, role, disabled, created_at) VALUES (?, ?, ?, ?, 0, ?)",
            (uid, e, name[:100], role, now_iso()),
        )
    else:
        uid = row["id"]
        conn.execute("UPDATE users SET name = ?, role = ? WHERE id = ?", (name[:100], role, uid))
    return conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
