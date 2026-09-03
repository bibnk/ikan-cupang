"""
First-run seed example test (subject-exclusion-list spec).

Validates: Requirements 2.1, 12.4
"""
from __future__ import annotations

import json
import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import imap_engine  # noqa: E402
from imap_engine import _DEFAULT_SUBJECT_EXCLUSION_PATTERNS, _load_subject_exclusion_list  # noqa: E402


EXPECTED = ["is your verification code"]


def _redirect(tmp_path, monkeypatch):
    p = str(tmp_path / "subject_exclusion_list.json")
    monkeypatch.setattr(imap_engine, "SUBJECT_EXCLUSION_PATH", p)
    monkeypatch.setattr(imap_engine, "SUBJECT_EXCLUSION_LOCK_PATH", p + ".lock")
    return p


def test_first_run_seeds_canonical_default(tmp_path, monkeypatch):
    """Validates: Requirements 2.1, 12.4."""
    p = _redirect(tmp_path, monkeypatch)

    # 1. Constant itself.
    assert list(_DEFAULT_SUBJECT_EXCLUSION_PATTERNS) == EXPECTED

    # 2. Pre-call file does not exist.
    assert not os.path.exists(p)

    # 3. First load seeds and returns default.
    first = _load_subject_exclusion_list()
    assert first == EXPECTED

    # 4. File exists with byte-equivalent format.
    assert os.path.exists(p)
    expected_path = str(tmp_path / "_expected.json")
    with open(expected_path, "w", encoding="utf-8") as f:
        json.dump(EXPECTED, f, indent=2)
    with open(expected_path, "rb") as ef, open(p, "rb") as af:
        assert af.read() == ef.read()

    # 5. Second load is no-op (no rewrite).
    pre_mtime = os.path.getmtime(p)
    time.sleep(0.01)
    second = _load_subject_exclusion_list()
    assert second == EXPECTED
    assert os.path.getmtime(p) == pre_mtime
