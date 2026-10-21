from datetime import timezone

from satprecip.dates import iter_hours, normalize_interval


def test_date_only_end_includes_full_day():
    start, end = normalize_interval("2025-03-01", "2025-03-01")
    hours = list(iter_hours(start, end))
    assert len(hours) == 24
    assert hours[0].hour == 0
    assert hours[-1].hour == 23
    assert start.tzinfo == timezone.utc


def test_exact_end_is_exclusive():
    start, end = normalize_interval("2025-03-01T00:30:00Z", "2025-03-01T02:00:00Z")
    assert [item.hour for item in iter_hours(start, end)] == [0, 1]
