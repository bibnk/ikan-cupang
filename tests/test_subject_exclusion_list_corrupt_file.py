"""
Edge-case: corrupt subject_exclusion_list.json fallback (subject-exclusion-list spec).

Validates: Requirements 2.3, 5.4
"""
from __future__ import annotations

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import imap_engine  # noqa: E402


def _redirect(tmp_path, monkeypatch):
    p = str(tmp_path / "subject_exclusion_list.json")
    monkeypatch.setattr(imap_engine, "SUBJECT_EXCLUSION_PATH", p)
    monkeypatch.setattr(imap_engine, "SUBJECT_EXCLUSION_LOCK_PATH", p + ".lock")
    return p


def _isolate_master(tmp_path, monkeypatch):
    m = str(tmp_path / "imap_success.json")
    monkeypatch.setattr(imap_engine, "MASTER_IMAP_SUCCESS_PATH", m)
    monkeypatch.setattr(imap_engine, "MASTER_IMAP_SUCCESS_LOCK_PATH", m + ".lock")


def _isolate_skip(tmp_path, monkeypatch):
    s = str(tmp_path / "skip_domains.json")
    monkeypatch.setattr(imap_engine, "SKIP_DOMAINS_PATH", s)
    monkeypatch.setattr(imap_engine, "SKIP_DOMAINS_LOCK_PATH", s + ".lock")


def _check_corrupt(tmp_path, monkeypatch, corrupt_bytes):
    p = _redirect(tmp_path, monkeypatch)
    _isolate_master(tmp_path, monkeypatch)
    _isolate_skip(tmp_path, monkeypatch)

    with open(p, "wb") as f:
        f.write(corrupt_bytes)
    pre = open(p, "rb").read()

    # _load returns []
    assert imap_engine._load_subject_exclusion_list() == []

    # File unchanged
    assert open(p, "rb").read() == pre

    # ImapChecker init succeeds with empty patterns
    results_dir = tmp_path / "jobs" / "test"
    results_dir.mkdir(parents=True, exist_ok=True)
    checker = imap_engine.ImapChecker(
        job_id="test",
        results_dir=str(results_dir),
        accounts=[],
    )
    assert checker.subject_exclusion_patterns == []


def test_corrupt_invalid_json(tmp_path, monkeypatch):
    """Validates: Requirements 2.3, 5.4 (invalid JSON)."""
    _check_corrupt(tmp_path, monkeypatch, b"{not json}")


def test_corrupt_non_list_root(tmp_path, monkeypatch):
    """Validates: Requirements 2.3, 5.4 (non-list root)."""
    _check_corrupt(tmp_path, monkeypatch, b'{"foo": "bar"}')


def test_corrupt_list_with_non_string(tmp_path, monkeypatch):
    """Validates: Requirements 2.3, 5.4 (list with non-string element)."""
    _check_corrupt(tmp_path, monkeypatch, b'["pattern", 42, "other"]')
