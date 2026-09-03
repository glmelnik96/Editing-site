from datetime import UTC, datetime, timedelta, timezone

import pytest

from server.app.util import iso, new_id, now_iso, parse_iso, utcnow


def test_iso_roundtrip_is_utc_with_milliseconds():
    dt = datetime(2026, 9, 3, 10, 0, 0, 123456, tzinfo=UTC)
    s = iso(dt)
    assert s == "2026-09-03T10:00:00.123Z"
    assert parse_iso(s) == datetime(2026, 9, 3, 10, 0, 0, 123000, tzinfo=UTC)


def test_iso_strings_compare_chronologically():
    a = utcnow()
    assert iso(a) < iso(a + timedelta(seconds=1))
    assert now_iso().endswith("Z")


def test_new_id_has_prefix_and_is_unique():
    a, b = new_id("usr"), new_id("usr")
    assert a.startswith("usr_") and len(a) == 4 + 12
    assert a != b


def test_parse_iso_accepts_offset_form():
    assert parse_iso("2026-09-03T10:00:00.123+00:00") == datetime(2026, 9, 3, 10, 0, 0, 123000, tzinfo=UTC)


def test_iso_converts_other_timezones_to_utc():
    plus3 = timezone(timedelta(hours=3))
    assert iso(datetime(2026, 9, 3, 13, 0, 0, 123000, tzinfo=plus3)) == "2026-09-03T10:00:00.123Z"


def test_iso_rejects_naive_datetime():
    with pytest.raises(ValueError):
        iso(datetime(2026, 9, 3, 10, 0, 0))  # noqa: DTZ001
