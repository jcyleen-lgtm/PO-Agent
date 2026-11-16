"""A partly uploaded last week must not feed the forecast or the 4-week average."""
from datetime import date

from src.repositories.sales import last_complete_week


def test_week_complete_on_saturday():
    assert last_complete_week(date(2026, 11, 7)) == date(2026, 11, 2)      # Sat: week of 2 Nov complete


def test_partial_week_falls_back_one_week():
    assert last_complete_week(date(2026, 11, 13)) == date(2026, 11, 2)     # Fri: week of 9 Nov partial
    assert last_complete_week(date(2026, 11, 9)) == date(2026, 11, 2)      # Mon


def test_sunday_counts_as_complete():
    assert last_complete_week(date(2026, 11, 15)) == date(2026, 11, 9)


def test_no_sales():
    assert last_complete_week(None) is None
