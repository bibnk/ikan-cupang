"""
FileLock timeout example tests (subject-exclusion-list spec).

Three sub-scenarios:
1. Constructor fallback (3.4(b))
2. GET endpoint 503 (3.4(a) / 7.5)
3. POST endpoint 503 (3.4(a) / 8.7)

Validates: Requirements 3.4, 7.5, 8.7
"""
from __future__ import annotations

import os
import sys

from filelock import Timeout

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


def _raise_timeout(*args, **kwargs):
    raise Timeout("simulated subject-exclusion lock timeout")


def test_constructor_falls_back_to_default(tmp_path, monkeypatch, capsys):
    """Validates: Requirement 3.4(b)."""
    _redirect(tmp_path, monkeypatch)
    _isolate_master(tmp_path, monkeypatch)
    _isolate_skip(tmp_path, monkeypatch)

    monkeypatch.setattr(imap_engine, "_load_subject_exclusion_list", _raise_timeout)

    results_dir = tmp_path / "jobs" / "init_timeout"
    results_dir.mkdir(parents=True, exist_ok=True)

    checker = imap_engine.ImapChecker(
        job_id="init_timeout",
        results_dir=str(results_dir),
        accounts=[],
    )

    assert checker.subject_exclusion_patterns == list(
        imap_engine._DEFAULT_SUBJECT_EXCLUSION_PATTERNS
    )
    assert checker.subject_exclusion_patterns is not imap_engine._DEFAULT_SUBJECT_EXCLUSION_PATTERNS

    captured = capsys.readouterr()
    assert "subject-exclusion-list lock timeout" in captured.err


def test_get_endpoint_returns_503(tmp_path, monkeypatch):
    """Validates: Requirements 3.4(a), 7.5."""
    p = _redirect(tmp_path, monkeypatch)
    _isolate_master(tmp_path, monkeypatch)

    import app as flask_app
    monkeypatch.setattr(flask_app, "_load_subject_exclusion_list", _raise_timeout)

    client = flask_app.app.test_client()
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["is_admin"] = True
        sess["code_hash"] = "test_hash"
        sess["user_label"] = "tester"

    res = client.get("/api/admin/subject-exclusion-list")
    assert res.status_code == 503
    assert res.get_json() == {"error": "Subject-exclusion list is busy, please retry"}
    assert not os.path.exists(p)


def test_post_endpoint_returns_503(tmp_path, monkeypatch):
    """Validates: Requirements 3.4(a), 8.7."""
    p = _redirect(tmp_path, monkeypatch)
    _isolate_master(tmp_path, monkeypatch)

    import app as flask_app
    monkeypatch.setattr(flask_app, "_save_subject_exclusion_list", _raise_timeout)

    client = flask_app.app.test_client()
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["is_admin"] = True
        sess["code_hash"] = "test_hash"
        sess["user_label"] = "tester"

    res = client.post(
        "/api/admin/subject-exclusion-list",
        json={"patterns": ["new pattern"]},
    )
    assert res.status_code == 503
    assert res.get_json() == {"error": "Subject-exclusion list is busy, please retry"}
    assert not os.path.exists(p)
