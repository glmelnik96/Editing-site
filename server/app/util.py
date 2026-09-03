"""Время и идентификаторы. Время везде: ISO-8601 UTC с миллисекундами и суффиксом Z."""
from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError("iso() требует datetime с часовым поясом")
    return dt.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def now_iso() -> str:
    return iso(utcnow())


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
