"""
Endpoint authentication / authorization tests (custom-skip-domains spec).

Asserts the auth matrix from ``tasks.md`` 5.11:

  | Context                | GET status / body                  | POST status / body                                   |
  |------------------------|------------------------------------|------------------------------------------------------|
  | Unauthenticated        | 401 / {"error":"Unauthorized"}     | 401 / {"error":"Unauthorized"}                       |
  | Authenticated non-admin| 403 / {"error":"Admin only"}       | 403 / {"error":"Admin only"}                         |
  | Authenticated admin    | 200 / {"domains":[...]}            | 200 / {"success":true,"domains":[...]}               |

The 401 path is delivered by the existing ``@login_required`` decorator
in ``app.py``; this test confirms Task 3 did NOT accidentally
short-circuit it.

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


def _redirect_skip_paths(tmp_path, monkeypatch):
    skip_path = str(tmp_path / "skip_domains.json")
    monkeypatch.setattr(imap_engine, "SKIP_DOMAINS_PATH", skip_path)
    monkeypatch.setattr(imap_engine, "SKIP_DOMAINS_LOCK_PATH", skip_path + ".lock")
    return skip_path


def _stamp_session(client, *, authenticated, is_admin):
    """Stamp the test client's session per the desired auth context."""
    with client.session_transaction() as sess:
        if authenticated is None:
            # Leave session blank → unauthenticated.
            return
        if authenticated:
            sess["authenticated"] = True
            sess["is_admin"] = bool(is_admin)
            sess["code_hash"] = "test_hash"
            sess["user_label"] = "tester"


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Flask test client with skip-domains paths redirected to tmp_path."""
    _redirect_skip_paths(tmp_path, monkeypatch)
    import app as flask_app

    return flask_app.app.test_client()


# ---------------------------------------------------------------------------
# Unauthenticated → 401
# ---------------------------------------------------------------------------
def test_unauthenticated_get_returns_401(client):
    """Validates: Requirements 7.3, 8.5 (401 from @login_required)."""
    res = client.get("/api/admin/skip-domains")
    assert res.status_code == 401, (
        f"GET (unauth) expected 401, got {res.status_code}; "
        f"body={res.get_data(as_text=True)!r}"
    )
    assert res.get_json() == {"error": "Unauthorized"}, (
        f"GET (unauth) body must equal {{'error': 'Unauthorized'}}; "
        f"got {res.get_json()!r}"
    )


def test_unauthenticated_post_returns_401(client):
    """Validates: Requirements 7.3, 8.5 (401 from @login_required)."""
    res = client.post(
        "/api/admin/skip-domains", json={"domains": ["hotmail"]}
    )
    assert res.status_code == 401, (
        f"POST (unauth) expected 401, got {res.status_code}; "
        f"body={res.get_data(as_text=True)!r}"
    )
    assert res.get_json() == {"error": "Unauthorized"}, (
        f"POST (unauth) body must equal {{'error': 'Unauthorized'}}; "
        f"got {res.get_json()!r}"
    )


# ---------------------------------------------------------------------------
# Authenticated non-admin → 403
# ---------------------------------------------------------------------------
def test_non_admin_get_returns_403(client):
    """Validates: Requirements 7.4, 8.6 (403 'Admin only')."""
    _stamp_session(client, authenticated=True, is_admin=False)
    res = client.get("/api/admin/skip-domains")
    assert res.status_code == 403, (
        f"GET (non-admin) expected 403, got {res.status_code}; "
        f"body={res.get_data(as_text=True)!r}"
    )
    assert res.get_json() == {"error": "Admin only"}, (
        f"GET (non-admin) body must equal {{'error': 'Admin only'}}; "
        f"got {res.get_json()!r}"
    )


def test_non_admin_post_returns_403(client):
    """Validates: Requirements 7.4, 8.6 (403 'Admin only')."""
    _stamp_session(client, authenticated=True, is_admin=False)
    res = client.post(
        "/api/admin/skip-domains", json={"domains": ["hotmail"]}
    )
    assert res.status_code == 403, (
        f"POST (non-admin) expected 403, got {res.status_code}; "
        f"body={res.get_data(as_text=True)!r}"
    )
    assert res.get_json() == {"error": "Admin only"}, (
        f"POST (non-admin) body must equal {{'error': 'Admin only'}}; "
        f"got {res.get_json()!r}"
    )


# ---------------------------------------------------------------------------
# Authenticated admin → 200
# ---------------------------------------------------------------------------
def test_admin_get_returns_200_with_domains(client):
    """Validates: Requirements 7.1, 8.1 (admin success path).

    First-run on the redirected path means the seed will fire and the
    canonical default list is returned.
    """
    _stamp_session(client, authenticated=True, is_admin=True)
    res = client.get("/api/admin/skip-domains")
    assert res.status_code == 200, (
        f"GET (admin) expected 200, got {res.status_code}; "
        f"body={res.get_data(as_text=True)!r}"
    )
    body = res.get_json()
    assert isinstance(body, dict) and "domains" in body, (
        f"GET (admin) body must be a dict with 'domains' key; got {body!r}"
    )
    assert body["domains"] == [
        "hotmail",
        "live",
        "msn",
        "outlook",
        "yahoo",
        "interia",
        "poczta.fm",
    ], (
        f"GET (admin) on a fresh path must return seeded default; "
        f"got {body['domains']!r}"
    )


def test_admin_post_returns_200_with_normalized_domains(client):
    """Validates: Requirements 7.1, 8.1 (admin POST success path)."""
    _stamp_session(client, authenticated=True, is_admin=True)
    res = client.post(
        "/api/admin/skip-domains",
        json={"domains": ["Hotmail", " gmail ", "outlook"]},
    )
    assert res.status_code == 200, (
        f"POST (admin) expected 200, got {res.status_code}; "
        f"body={res.get_data(as_text=True)!r}"
    )
    body = res.get_json()
    assert body == {
        "success": True,
        "domains": ["hotmail", "gmail", "outlook"],
    }, (
        f"POST (admin) body mismatch.\n"
        f"  expected: {{'success': True, 'domains': ['hotmail', 'gmail', "
        f"'outlook']}}\n"
        f"  actual  : {body!r}"
    )
