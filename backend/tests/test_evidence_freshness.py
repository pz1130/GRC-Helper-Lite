from datetime import UTC, datetime, timedelta, timezone

from app.evidence.freshness import display_status

NOW = datetime(2026, 9, 10, 12, tzinfo=UTC)


def test_non_collected_statuses_are_not_converted_to_expired():
    assert display_status("missing", NOW - timedelta(days=1), NOW) == "missing"
    assert display_status("planned", None, NOW) == "planned"


def test_collected_without_expiry_is_valid_and_expiry_is_inclusive():
    assert display_status("collected", None, NOW) == "valid"
    assert display_status("collected", NOW, NOW) == "valid"
    assert display_status("collected", NOW - timedelta(microseconds=1), NOW) == "expired"


def test_aware_timezones_are_compared_by_instant():
    shanghai = timezone(timedelta(hours=8))
    assert display_status("collected", datetime(2026, 9, 10, 20, tzinfo=shanghai), NOW) == "valid"


def test_naive_fixture_values_are_supported():
    assert display_status("collected", datetime(2026, 9, 10, 11), NOW) == "expired"
