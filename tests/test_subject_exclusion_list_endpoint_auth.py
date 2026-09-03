"""
Endpoint authentication / authorization tests (subject-exclusion-list spec).

Validates: Requirements 7.1, 7.3, 7.4, 8.1, 8.5, 8.6
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


@pytest.fixture
def client(tmp_path, monkeypatch):
    _redirect(tmp_path, monkeypatch)
    import app as flask_app
    return flask_app.app.test_client()


def _stamp(client, *, authenticated, is_admin):
    with client.session_transaction() as sess:
        if authenticated is None:
            return
        if authenticated:
            sess["authenticated"] = True
            sess["is_admin"] = bool(is_admin)
            sess["code_hash"] = "test_hash"
            sess["user_label"] = "tester"


def test_unauthenticated_get_401(client):
    """Validates: Requirements 7.3, 8.5."""
    res = client.get("/api/admin/subject-exclusion-list")
    assert res.status_code == 401
    assert res.get_json() == {"error": "Unauthorized"}


def test_unauthenticated_post_401(client):
    res = client.post("/api/admin/subject-exclusion-list", json={"patterns": ["x"]})
    assert res.status_code == 401
    assert res.get_json() == {"error": "Unauthorized"}


def test_non_admin_get_403(client):
    """Validates: Requirements 7.4, 8.6."""
    _stamp(client, authenticated=True, is_admin=False)
    res = client.get("/api/admin/subject-exclusion-list")
    assert res.status_code == 403
    assert res.get_json() == {"error": "Admin only"}


def test_non_admin_post_403(client):
    _stamp(client, authenticated=True, is_admin=False)
    res = client.post("/api/admin/subject-exclusion-list", json={"patterns": ["x"]})
    assert res.status_code == 403
    assert res.get_json() == {"error": "Admin only"}


def test_admin_get_200_returns_seeded_default(client):
    """Validates: Requirements 7.1 — first GET seeds + returns canonical."""
    _stamp(client, authenticated=True, is_admin=True)
    res = client.get("/api/admin/subject-exclusion-list")
    assert res.status_code == 200
    body = res.get_json()
    assert body == {"patterns": ["is your verification code"]}


def test_admin_post_200_returns_normalized(client):
    """Validates: Requirements 8.1."""
    _stamp(client, authenticated=True, is_admin=True)
    res = client.post(
        "/api/admin/subject-exclusion-list",
        json={"patterns": ["IS YOUR VERIFICATION CODE", "  Newsletter ", "promo"]},
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body == {
        "success": True,
        "patterns": ["is your verification code", "newsletter", "promo"],
    }
