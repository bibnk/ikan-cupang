"""
Property test that the default seed preserves OTP_SUBJECT_REGEX exclusion
semantics (subject-exclusion-list spec).

Encodes Property 6 from ``design.md`` §"Correctness Properties".

Validates: Requirements 2.4, 12.1, 12.4
"""
from __future__ import annotations

import os
import re
import sys

from hypothesis import given, settings, strategies as st

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import imap_engine  # noqa: E402
from imap_engine import (  # noqa: E402
    _DEFAULT_SUBJECT_EXCLUSION_PATTERNS,
    _subject_excluded,
    _load_subject_exclusion_list,
)


# Exact legacy regex for reference.
_LEGACY_REGEX = re.compile(r"^Booking\.com – \w+ is your verification code$")


def _redirect(tmp_path, monkeypatch):
    p = str(tmp_path / "subject_exclusion_list.json")
    monkeypatch.setattr(imap_engine, "SUBJECT_EXCLUSION_PATH", p)
    monkeypatch.setattr(imap_engine, "SUBJECT_EXCLUSION_LOCK_PATH", p + ".lock")
    return p


@settings(max_examples=100, deadline=None)
@given(
    token=st.text(
        alphabet=st.characters(
            whitelist_categories=("Lu", "Ll", "Nd"),
            whitelist_characters="_",
        ),
        min_size=1,
        max_size=20,
    )
)
def test_property6_default_seed_excludes_legacy_otp_subjects(token):
    """Validates: Requirements 2.4, 12.1.

    For every token matching ``\\w+``, the subject
    ``Booking.com – {token} is your verification code`` (en-dash) is
    excluded by both ``["is your verification code"]`` and
    ``_DEFAULT_SUBJECT_EXCLUSION_PATTERNS``.
    """
    subject = f"Booking.com \u2013 {token} is your verification code"

    # Sanity: the subject does match the legacy regex.
    assert _LEGACY_REGEX.match(subject) is not None, (
        f"generated subject does not match legacy regex: {subject!r}"
    )

    # Property 6.1
    assert _subject_excluded(subject, ["is your verification code"]) is True

    # Property 6.2
    assert _subject_excluded(
        subject, list(_DEFAULT_SUBJECT_EXCLUSION_PATTERNS)
    ) is True


def test_property6_first_run_seed_returns_canonical_default(tmp_path, monkeypatch):
    """Validates: Requirement 12.4 — seed exactly ``["is your verification code"]``."""
    p = _redirect(tmp_path, monkeypatch)
    assert not os.path.exists(p)

    loaded = _load_subject_exclusion_list()
    assert loaded == ["is your verification code"]
    assert os.path.exists(p)


def test_default_constant_is_canonical():
    """Validates: Requirement 12.4 — the constant itself."""
    assert _DEFAULT_SUBJECT_EXCLUSION_PATTERNS == ["is your verification code"]
    assert isinstance(_DEFAULT_SUBJECT_EXCLUSION_PATTERNS, list)
