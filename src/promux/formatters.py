from datetime import datetime, timezone, timedelta


def parse_iso_utc(iso_str: str | None) -> datetime | None:
    """Parse ISO8601 string into a timezone-aware UTC datetime."""
    if not iso_str or not isinstance(iso_str, str):
        return None
    try:
        clean = iso_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def format_relative_countdown(
    reset_iso: str | None, now: datetime | None = None
) -> str:
    """Calculate compact relative countdown (e.g. '2h 15m', '3d 4h', '18m', or '-')."""
    reset_dt = parse_iso_utc(reset_iso)
    if reset_dt is None:
        return "-"

    current = now or datetime.now(timezone.utc)
    delta = reset_dt - current

    if delta <= timedelta(0):
        return "0m"

    days = delta.days
    total_seconds = delta.seconds
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60

    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def format_quota_cell(
    val: float | None, reset_iso: str | None, now: datetime | None = None
) -> str:
    """Format overview table cell: e.g. '98.0% (2h 15m)', '85.0% (3d 4h)', '100.0% (-)'."""
    if val is None:
        return "-"
    pct = f"{val * 100:.1f}%"
    countdown = format_relative_countdown(reset_iso, now=now)
    if countdown == "-":
        return f"{pct} (-)"
    return f"{pct} ({countdown})"


def format_quota_detail(
    val: float | None, reset_iso: str | None, now: datetime | None = None
) -> str:
    """Format detail mode: e.g. '98.0% (2h 15m left - 03:15 UTC)'."""
    if val is None:
        return "-"
    pct = f"{val * 100:.1f}%"
    reset_dt = parse_iso_utc(reset_iso)
    if reset_dt is None:
        return f"{pct} (-)"

    countdown = format_relative_countdown(reset_iso, now=now)
    current = now or datetime.now(timezone.utc)

    # Format absolute timestamp
    if reset_dt.date() == current.date():
        abs_str = reset_dt.strftime("%H:%M UTC")
    else:
        abs_str = reset_dt.strftime("%b %d %H:%M UTC")

    return f"{pct} ({countdown} left - {abs_str})"
