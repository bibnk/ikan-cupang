"""
Invalid-payload edge-case tests for POST /api/admin/subject-exclusion-list.

Validates: Requirements 8.4, 9.7
"""
from __future__ import annotations

import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import imap_engine  # noqa: E402


def _redirect(tmp_path, monkeypatch):
    p = str(tmp_path / "subject_exclusion_list.json")
    monkeypatch.setattr(imap_engine, "SUBJECT_EXCLUSION_PATH", p)
    monkeypatch.setattr(imap_engine, "SUBJECT_EXCLUSION_LOCK_PATH", p + ".lock")
    return p


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    p = _redirect(tmp_path, monkeypatch)
    baseline = ["is your verification code"]
    imap_engine._save_subject_exclusion_list(baseline)

    import app as flask_app
    client = flask_app.app.test_client()
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["is_admin"] = True
        sess["code_hash"] = "test_hash"
        sess["user_label"] = "tester"
    return client, p, baseline


def _assert_invalid_payload(res):
    assert res.status_code == 400
    assert res.get_json() == {"error": "Invalid payload"}


def _assert_baseline(p, baseline):
    assert imap_engine._load_subject_exclusion_list() == baseline


def test_plain_text_body(admin_client):
    """Validates: Requirement 8.4."""
    client, p, baseline = admin_client
    res = client.post(
        "/api/admin/subject-exclusion-list",
        data="hello",
        content_type="text/plain",
    )
    _assert_invalid_payload(res)
    _assert_baseline(p, baseline)


@pytest.mark.parametrize("payload", [[1, 2, 3], "hi", None])
def test_non_dict_root(admin_client, payload):
    """Validates: Requirement 8.4."""
    client, p, baseline = admin_client
    res = client.post("/api/admin/subject-exclusion-list", json=payload)
    _assert_invalid_payload(res)
    _assert_baseline(p, baseline)


@pytest.mark.parametrize("payload", [{}, {"foo": "bar"}])
def test_missing_patterns_key(admin_client, payload):
    """Validates: Requirement 8.4."""
    client, p, baseline = admin_client
    res = client.post("/api/admin/subject-exclusion-list", json=payload)
    _assert_invalid_payload(res)
    _assert_baseline(p, baseline)


@pytest.mark.parametrize("payload", [
    {"patterns": "string-not-list"},
    {"patterns": {"a": 1}},
])
def test_patterns_not_list(admin_client, payload):
    """Validates: Requirement 8.4."""
    client, p, baseline = admin_client
    res = client.post("/api/admin/subject-exclusion-list", json=payload)
    _assert_invalid_payload(res)
    _assert_baseline(p, baseline)


@pytest.mark.parametrize("payload", [
    {"patterns": ["valid", 42, "other"]},
    {"patterns": [None]},
    {"patterns": [{"x": 1}]},
])
def test_non_string_element_routes_to_invalid_payload(admin_client, payload):
    """Validates: Requirements 8.4, 9.7.

    Non-string elements MUST return 'Invalid payload', NOT 'Invalid entries'.
    """
    client, p, baseline = admin_client
    res = client.post("/api/admin/subject-exclusion-list", json=payload)
    _assert_invalid_payload(res)
    body = res.get_json()
    assert body.get("error") != "Invalid entries"
    _assert_baseline(p, baseline)
