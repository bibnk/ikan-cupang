"""
Smoke tests for module-level constant invariants (subject-exclusion-list spec).

Validates: Requirements 5.2, 14.5
"""
from __future__ import annotations

import inspect
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import imap_engine  # noqa: E402


def test_otp_subject_regex_is_removed():
    """Validates: Requirement 5.2 — `OTP_SUBJECT_REGEX` removed from module scope."""
    assert not hasattr(imap_engine, "OTP_SUBJECT_REGEX"), (
        "imap_engine still defines OTP_SUBJECT_REGEX; this constant must be "
        "removed by the subject-exclusion-list spec (Requirement 5.2)."
    )


def test_worker_uses_subject_exclusion_patterns():
    """Validates: Requirement 5.2 — `_worker` reads from instance attribute."""
    src = inspect.getsource(imap_engine.ImapChecker._worker)
    assert "self.subject_exclusion_patterns" in src
    assert "OTP_SUBJECT_REGEX" not in src
    assert "_DEFAULT_SUBJECT_EXCLUSION_PATTERNS" not in src


def test_new_symbols_importable():
    """Validates: Requirement 14.5 — new symbols importable for tests/migration."""
    from imap_engine import (  # noqa: F401
        _subject_excluded,
        _DEFAULT_SUBJECT_EXCLUSION_PATTERNS,
        _load_subject_exclusion_list,
        _save_subject_exclusion_list,
        _normalize_exclusion_entries,
        _validate_exclusion_entries,
        SUBJECT_EXCLUSION_PATH,
        SUBJECT_EXCLUSION_LOCK_PATH,
    )


def test_lock_paths_distinct():
    """Validates: Requirement 13.5 — three lock paths pairwise distinct."""
    paths = {
        imap_engine.MASTER_IMAP_SUCCESS_LOCK_PATH,
        imap_engine.SKIP_DOMAINS_LOCK_PATH,
        imap_engine.SUBJECT_EXCLUSION_LOCK_PATH,
    }
    assert len(paths) == 3, f"lock paths must be pairwise distinct: {paths!r}"
