"""
Property test for ``_subject_excluded`` predicate (subject-exclusion-list spec).

Encodes Property 5 from ``design.md`` §"Correctness Properties":

  Subject-exclusion predicate equivalence. For every (subject, patterns) pair
  where each pattern is non-empty and equals its own .lower(),
  ``_subject_excluded(subject, patterns)`` equals
  ``any(p in subject.lower() for p in patterns)``.

Validates: Requirements 6.1, 6.2, 6.5
"""
from __future__ import annotations

import os
import sys

from hypothesis import given, settings, strategies as st

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from imap_engine import _subject_excluded  # noqa: E402


valid_pattern = st.from_regex(r"[a-z][a-z0-9 .\-]{0,15}", fullmatch=True).filter(
    lambda s: s.strip() == s and 1 <= len(s) <= 30 and s == s.lower()
)


@settings(max_examples=100, deadline=None)
@given(
    subject=st.text(min_size=0, max_size=80),
    patterns=st.lists(valid_pattern, min_size=0, max_size=8),
)
def test_property5_predicate_equals_any_substring(subject, patterns):
    """Validates: Requirements 6.1, 6.2, 6.5."""
    expected = any(p in subject.lower() for p in patterns)
    actual = _subject_excluded(subject, patterns)
    assert actual == expected, (
        f"predicate mismatch.\n"
        f"  subject={subject!r}, patterns={patterns!r}\n"
        f"  expected={expected}, actual={actual}"
    )


def test_empty_patterns_always_false():
    """Validates: Requirement 6.2 — empty list ⇒ False for every subject."""
    for subject in ["", "anything", "Booking.com – ABC123 is your verification code"]:
        assert _subject_excluded(subject, []) is False


def test_case_insensitive_both_sides():
    """Validates: Requirement 6.5 — case-insensitive on both sides."""
    pattern = "is your verification code"
    subject_upper = "Booking.com \u2013 ABC123 IS YOUR VERIFICATION CODE"
    subject_mixed = "BOOKING.COM \u2013 xyz789 Is Your Verification Code"
    assert _subject_excluded(subject_upper, [pattern]) is True
    assert _subject_excluded(subject_mixed, [pattern]) is True


def test_does_not_lowercase_patterns_again():
    """Helper assumes patterns already lowercased by loader (Requirement 6.1
    contract)."""
    # Pass an already-lowercased pattern; helper must work without re-lowering.
    assert _subject_excluded("hello world", ["world"]) is True
    # An UPPERCASE pattern (would be from a non-loader call site) won't
    # match a lowercased subject — confirms helper does NOT lowercase
    # patterns.
    assert _subject_excluded("hello world", ["WORLD"]) is False
