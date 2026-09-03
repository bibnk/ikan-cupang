"""
Snapshot semantics example test (subject-exclusion-list spec).

Validates: Requirements 5.3, 12.3
"""
from __future__ import annotations

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import imap_engine  # noqa: E402


def _redirect_subject(tmp_path, monkeypatch):
    p = str(tmp_path / "subject_exclusion_list.json")
    monkeypatch.setattr(imap_engine, "SUBJECT_EXCLUSION_PATH", p)
    monkeypatch.setattr(imap_engine, "SUBJECT_EXCLUSION_LOCK_PATH", p + ".lock")


def _isolate_master(tmp_path, monkeypatch):
    m = str(tmp_path / "imap_success.json")
    monkeypatch.setattr(imap_engine, "MASTER_IMAP_SUCCESS_PATH", m)
    monkeypatch.setattr(imap_engine, "MASTER_IMAP_SUCCESS_LOCK_PATH", m + ".lock")


def _isolate_skip(tmp_path, monkeypatch):
    s = str(tmp_path / "skip_domains.json")
    monkeypatch.setattr(imap_engine, "SKIP_DOMAINS_PATH", s)
    monkeypatch.setattr(imap_engine, "SKIP_DOMAINS_LOCK_PATH", s + ".lock")


def test_snapshot_non_retroactive(tmp_path, monkeypatch):
    """Validates: Requirements 5.3, 12.3."""
    _redirect_subject(tmp_path, monkeypatch)
    _isolate_master(tmp_path, monkeypatch)
    _isolate_skip(tmp_path, monkeypatch)

    # Pre-write V1.
    imap_engine._save_subject_exclusion_list(["pattern v1"])

    # Construct job_a with snapshot V1.
    job_a_dir = tmp_path / "jobs" / "job_a"
    job_a_dir.mkdir(parents=True, exist_ok=True)
    job_a = imap_engine.ImapChecker(
        job_id="job_a",
        results_dir=str(job_a_dir),
        accounts=[],
    )
    assert job_a.subject_exclusion_patterns == ["pattern v1"]

    # Mid-test, save V2.
    imap_engine._save_subject_exclusion_list(["pattern v2 newer"])
    assert imap_engine._load_subject_exclusion_list() == ["pattern v2 newer"]

    # job_a snapshot unchanged (non-retroactive).
    assert job_a.subject_exclusion_patterns == ["pattern v1"]

    # New job_b sees V2.
    job_b_dir = tmp_path / "jobs" / "job_b"
    job_b_dir.mkdir(parents=True, exist_ok=True)
    job_b = imap_engine.ImapChecker(
        job_id="job_b",
        results_dir=str(job_b_dir),
        accounts=[],
    )
    assert job_b.subject_exclusion_patterns == ["pattern v2 newer"]
