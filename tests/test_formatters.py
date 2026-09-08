from datetime import datetime, timezone, timedelta
from promux.formatters import (
    parse_iso_utc,
    format_relative_countdown,
    format_quota_cell,
    format_quota_detail,
)


def test_parse_iso_utc():
    assert parse_iso_utc(None) is None
    assert parse_iso_utc("") is None
    assert parse_iso_utc(123) is None  # type: ignore[arg-type]
    assert parse_iso_utc("invalid-date") is None

    # Naive ISO string gets UTC tzinfo
    dt_naive = parse_iso_utc("2026-09-09T03:15:00")
    assert dt_naive is not None
    assert dt_naive.tzinfo == timezone.utc

    # Timezone conversion to UTC
    dt_tz = parse_iso_utc("2026-09-09T08:15:00+05:00")
    assert dt_tz is not None
    assert dt_tz.tzinfo == timezone.utc
    assert dt_tz.hour == 3
    assert dt_tz.minute == 15

    dt = parse_iso_utc("2026-09-09T03:15:00Z")
    assert dt is not None
    assert dt.tzinfo == timezone.utc
    assert dt.year == 2026
    assert dt.hour == 3
    assert dt.minute == 15

    dt2 = parse_iso_utc("2026-09-09T03:15:00+00:00")
    assert dt2 is not None
    assert dt2 == dt


def test_format_relative_countdown():
    base_now = datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc)

    # Missing or invalid
    assert format_relative_countdown(None, now=base_now) == "-"
    assert format_relative_countdown("", now=base_now) == "-"

    # Expired / now
    past = (base_now - timedelta(minutes=5)).isoformat()
    assert format_relative_countdown(past, now=base_now) == "0m"

    # Minutes only
    t_15m = (base_now + timedelta(minutes=15)).isoformat()
    assert format_relative_countdown(t_15m, now=base_now) == "15m"

    # Hours and minutes
    t_2h_15m = (base_now + timedelta(hours=2, minutes=15)).isoformat()
    assert format_relative_countdown(t_2h_15m, now=base_now) == "2h 15m"

    # Days and hours
    t_3d_4h = (base_now + timedelta(days=3, hours=4, minutes=20)).isoformat()
    assert format_relative_countdown(t_3d_4h, now=base_now) == "3d 4h"

    # Default now parameter (future timestamp)
    future = (datetime.now(timezone.utc) + timedelta(days=2, hours=1)).isoformat()
    assert format_relative_countdown(future).startswith("2d")


def test_format_quota_cell():
    base_now = datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc)
    t_2h_15m = (base_now + timedelta(hours=2, minutes=15)).isoformat()

    assert format_quota_cell(None, None, now=base_now) == "-"
    assert format_quota_cell(1.0, None, now=base_now) == "100.0% (-)"
    assert format_quota_cell(0.98, t_2h_15m, now=base_now) == "98.0% (2h 15m)"


def test_format_quota_detail():
    base_now = datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc)
    t_2h_15m = "2026-09-09T14:15:00Z"
    t_3d_4h = "2026-09-12T16:00:00Z"

    assert format_quota_detail(None, None, now=base_now) == "-"
    assert format_quota_detail(1.0, None, now=base_now) == "100.0% (-)"
    assert (
        format_quota_detail(0.98, t_2h_15m, now=base_now)
        == "98.0% (2h 15m left - 14:15 UTC)"
    )
    assert (
        format_quota_detail(0.85, t_3d_4h, now=base_now)
        == "85.0% (3d 4h left - Sep 12 16:00 UTC)"
    )
