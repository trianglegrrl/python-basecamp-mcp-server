"""Unit tests for the _matches_scope helper in basecamp_client.

This helper is load-bearing for the L2 weekly-report workflow:
get_assignments_for_person feeds it the `due_on` of every Todo it walks,
filtered by one of the 6 valid scopes. A single off-by-one in the date
arithmetic propagates straight into "Show me Jill's tasks due this week"
and quietly truncates or over-includes results.

We pin every scope, every boundary case (today is Sunday, today is Monday,
None due_on, unknown scope), and the constant tuple itself.

All tests pass `today` explicitly — no freezegun, no clock dependency.
"""
from __future__ import annotations

import pytest


# ----------------------------------------------------------------------------
# Constant exposure: _matches_scope and VALID_ASSIGNMENT_SCOPES must be
# importable as module-level names from basecamp_client (not class members).
# ----------------------------------------------------------------------------

def test_valid_assignment_scopes_tuple_exact_contents_and_order():
    """The Node ref defined exactly these 6 scope strings in this order. The
    tool wrapper for get_my_due_assignments validates user-supplied scope
    against this tuple, so its contents are part of the public contract."""
    from basecamp_client import VALID_ASSIGNMENT_SCOPES

    assert isinstance(VALID_ASSIGNMENT_SCOPES, tuple), (
        "VALID_ASSIGNMENT_SCOPES must be a tuple (immutable, hashable); "
        f"got {type(VALID_ASSIGNMENT_SCOPES).__name__}"
    )
    assert VALID_ASSIGNMENT_SCOPES == (
        'overdue',
        'due_today',
        'due_tomorrow',
        'due_later_this_week',
        'due_next_week',
        'due_later',
    )


def test_matches_scope_is_module_level_function():
    """_matches_scope is a module-level helper, NOT a method. The
    get_assignments_for_person walk imports it from module scope."""
    import basecamp_client

    assert callable(getattr(basecamp_client, '_matches_scope', None)), (
        "basecamp_client._matches_scope must exist as a module-level callable"
    )


# ----------------------------------------------------------------------------
# Per-scope happy paths. today = 2026-05-27 (a Wednesday) chosen so every
# scope has a clearly distinguishable interior date.
#
#   2026-05-25 Mon
#   2026-05-26 Tue
#   2026-05-27 Wed  <- today
#   2026-05-28 Thu  <- tomorrow
#   2026-05-29 Fri
#   2026-05-30 Sat
#   2026-05-31 Sun  <- this_sun
#   2026-06-01 Mon  <- next_mon
#   2026-06-02 Tue
#   ...
#   2026-06-07 Sun  <- next_sun
#   2026-06-08 Mon  <- "due_later" starts here
# ----------------------------------------------------------------------------

TODAY_WED = '2026-05-27'


@pytest.mark.parametrize('due_on,expected', [
    ('2026-05-01', True),    # last month
    ('2026-05-26', True),    # yesterday
    ('2026-05-27', False),   # == today
    ('2026-05-28', False),   # tomorrow
    ('2030-01-01', False),   # far future
])
def test_overdue_window(due_on, expected):
    from basecamp_client import _matches_scope
    assert _matches_scope(due_on, 'overdue', TODAY_WED) is expected


@pytest.mark.parametrize('due_on,expected', [
    ('2026-05-27', True),
    ('2026-05-26', False),
    ('2026-05-28', False),
])
def test_due_today_window(due_on, expected):
    from basecamp_client import _matches_scope
    assert _matches_scope(due_on, 'due_today', TODAY_WED) is expected


@pytest.mark.parametrize('due_on,expected', [
    ('2026-05-28', True),    # tomorrow
    ('2026-05-27', False),   # today
    ('2026-05-29', False),   # day after tomorrow
])
def test_due_tomorrow_window(due_on, expected):
    from basecamp_client import _matches_scope
    assert _matches_scope(due_on, 'due_tomorrow', TODAY_WED) is expected


@pytest.mark.parametrize('due_on,expected', [
    ('2026-05-28', False),   # tomorrow — NOT included (window opens after)
    ('2026-05-29', True),    # Fri — interior
    ('2026-05-30', True),    # Sat — interior
    ('2026-05-31', True),    # Sun — closes the window (inclusive)
    ('2026-06-01', False),   # next Mon — already next week
    ('2026-05-27', False),   # today — not later this week
])
def test_due_later_this_week_window(due_on, expected):
    from basecamp_client import _matches_scope
    assert _matches_scope(due_on, 'due_later_this_week', TODAY_WED) is expected


@pytest.mark.parametrize('due_on,expected', [
    ('2026-05-31', False),   # this Sun
    ('2026-06-01', True),    # next Mon — interior
    ('2026-06-04', True),    # next Thu — interior
    ('2026-06-07', True),    # next Sun — closes the window (inclusive)
    ('2026-06-08', False),   # week after next
])
def test_due_next_week_window(due_on, expected):
    from basecamp_client import _matches_scope
    assert _matches_scope(due_on, 'due_next_week', TODAY_WED) is expected


@pytest.mark.parametrize('due_on,expected', [
    ('2026-06-07', False),   # next Sun — not yet "later"
    ('2026-06-08', True),    # Mon after next — first day of "later"
    ('2030-01-01', True),    # far future
])
def test_due_later_window(due_on, expected):
    from basecamp_client import _matches_scope
    assert _matches_scope(due_on, 'due_later', TODAY_WED) is expected


# ----------------------------------------------------------------------------
# Defensive cases.
# ----------------------------------------------------------------------------

@pytest.mark.parametrize('scope', [
    'overdue', 'due_today', 'due_tomorrow',
    'due_later_this_week', 'due_next_week', 'due_later',
])
def test_none_due_on_never_matches_any_scope(scope):
    """A todo without a due_on can never match a scope. BC3 returns due_on
    as null/missing for undated todos; the assignment-by-person walk must
    drop those silently rather than crash on a None comparison."""
    from basecamp_client import _matches_scope
    assert _matches_scope(None, scope, TODAY_WED) is False


@pytest.mark.parametrize('scope', [
    'overdue', 'due_today', 'due_tomorrow',
    'due_later_this_week', 'due_next_week', 'due_later',
])
def test_empty_string_due_on_never_matches_any_scope(scope):
    from basecamp_client import _matches_scope
    assert _matches_scope('', scope, TODAY_WED) is False


def test_unknown_scope_returns_false_defensively():
    """Unknown scope falls through. Callers (the client method) are
    expected to validate scope before this is reached, but the helper
    itself doesn't raise — it returns False so a stray scope string can
    never silently match every todo."""
    from basecamp_client import _matches_scope
    assert _matches_scope('2026-05-27', 'someday_maybe', TODAY_WED) is False


# ----------------------------------------------------------------------------
# Week-boundary regressions. Mon-start ISO weeks (Python weekday(): Mon=0).
# ----------------------------------------------------------------------------

def test_today_is_monday_week_start_is_today_itself():
    """When today is a Monday, this week's Monday is today; this_sun is +6;
    next_mon is +7. So a due_on on the next Sunday (+13) is the last day of
    due_next_week; +14 falls into due_later."""
    from basecamp_client import _matches_scope

    today_mon = '2026-05-25'  # Monday

    # due_today
    assert _matches_scope('2026-05-25', 'due_today', today_mon) is True
    # due_later_this_week opens after tomorrow (Tue=05-26), closes Sun (05-31)
    assert _matches_scope('2026-05-26', 'due_later_this_week', today_mon) is False  # tomorrow excluded
    assert _matches_scope('2026-05-27', 'due_later_this_week', today_mon) is True
    assert _matches_scope('2026-05-31', 'due_later_this_week', today_mon) is True   # this Sun, inclusive
    assert _matches_scope('2026-06-01', 'due_later_this_week', today_mon) is False  # next Mon, excluded
    # due_next_week: 06-01 .. 06-07
    assert _matches_scope('2026-06-01', 'due_next_week', today_mon) is True
    assert _matches_scope('2026-06-07', 'due_next_week', today_mon) is True
    assert _matches_scope('2026-06-08', 'due_next_week', today_mon) is False
    # due_later opens on 06-08
    assert _matches_scope('2026-06-08', 'due_later', today_mon) is True


def test_today_is_sunday_week_start_wraps_correctly():
    """When today is a Sunday, weekday()==6 so offset_to_mon is -6: this
    week's Monday is 6 days ago. So this_sun is today itself, next_mon is
    tomorrow. The trickiest case for any "Mon-start week" implementation
    because it lives at the end-of-week boundary."""
    from basecamp_client import _matches_scope

    today_sun = '2026-05-31'  # Sunday
    # weekday check (sanity): date.fromisoformat(today_sun).weekday() == 6

    # this_mon = 2026-05-25; this_sun = 2026-05-31 = today
    # tomorrow = 2026-06-01 (= next_mon)
    # next_mon = 2026-06-01; next_sun = 2026-06-07

    # due_today
    assert _matches_scope('2026-05-31', 'due_today', today_sun) is True
    # due_tomorrow == 2026-06-01
    assert _matches_scope('2026-06-01', 'due_tomorrow', today_sun) is True
    # due_later_this_week: > tomorrow (06-01) AND <= this_sun (05-31).
    # That window is EMPTY when today is Sunday — no date can be both after
    # 06-01 and on/before 05-31. Anything we throw at it must be False.
    assert _matches_scope('2026-05-31', 'due_later_this_week', today_sun) is False
    assert _matches_scope('2026-06-01', 'due_later_this_week', today_sun) is False
    assert _matches_scope('2026-06-02', 'due_later_this_week', today_sun) is False
    # due_next_week: 06-01 .. 06-07
    assert _matches_scope('2026-06-01', 'due_next_week', today_sun) is True
    assert _matches_scope('2026-06-07', 'due_next_week', today_sun) is True
    assert _matches_scope('2026-06-08', 'due_next_week', today_sun) is False
    # overdue: anything < 2026-05-31
    assert _matches_scope('2026-05-30', 'overdue', today_sun) is True
    assert _matches_scope('2026-05-31', 'overdue', today_sun) is False


def test_today_is_saturday_this_sun_is_tomorrow():
    """When today is Saturday: this_sun is tomorrow. So due_later_this_week
    opens after tomorrow (= after this_sun = after Sun) which means the
    window is empty (no date both > Sun AND <= Sun)."""
    from basecamp_client import _matches_scope

    today_sat = '2026-05-30'  # Saturday

    # tomorrow = 2026-05-31 (Sun) = this_sun
    # window: > tomorrow AND <= this_sun → empty
    assert _matches_scope('2026-05-31', 'due_tomorrow', today_sat) is True
    assert _matches_scope('2026-05-31', 'due_later_this_week', today_sat) is False
    assert _matches_scope('2026-06-01', 'due_later_this_week', today_sat) is False  # next Mon: out
    # next week still 06-01..06-07
    assert _matches_scope('2026-06-01', 'due_next_week', today_sat) is True
    assert _matches_scope('2026-06-07', 'due_next_week', today_sat) is True


def test_month_boundary_does_not_break_arithmetic():
    """Cross-month and cross-year arithmetic must use real date math, not
    string slicing. Pin Dec 31 → Jan 1 transition."""
    from basecamp_client import _matches_scope

    today = '2026-12-31'  # Thursday
    tomorrow = '2027-01-01'

    assert _matches_scope(tomorrow, 'due_tomorrow', today) is True
    assert _matches_scope('2026-12-31', 'due_today', today) is True
    assert _matches_scope('2026-12-30', 'overdue', today) is True
