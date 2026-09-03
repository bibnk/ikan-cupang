"""
Invalid-payload edge-case tests for POST /api/admin/skip-domains
(custom-skip-domains spec).

Five sub-scenarios, each MUST return 400 with body
``{"error": "Invalid payload"}`` AND leave ``_load_skip_domains()``
unchanged after the failed POST. Crucially, payloads with non-string
elements MUST return ``"Invalid payload"`` (Requirement 9.6 routes
through 8.4), NOT ``"Invalid entries"``.

Validates: Requirements 8.4, 9.6
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


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    """Flask test client with admin session and skip-domains paths
    redirected to tmp_path. Pre-seeds a known baseline list so we can
    verify failed POSTs do not mutate it."""
    skip_path = _redirect_skip_paths(tmp_path, monkeypatch)

    # Pre-seed a known baseline.
    baseline = ["seed-a", "seed-b"]
    imap_engine._save_skip_domains(baseline)

    import app as flask_app

    client = flask_app.app.test_client()
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["is_admin"] = True
        sess["code_hash"] = "test_hash"
        sess["user_label"] = "tester"

    return client, skip_path, baseline


def _assert_invalid_payload_400(res, scenario_label):
    assert res.status_code == 400, (
        f"[{scenario_label}] expected 400 Invalid payload, got "
        f"{res.status_code}; body={res.get_data(as_text=True)!r}"
    )
    body = res.get_json()
    assert body == {"error": "Invalid payload"}, (
        f"[{scenario_label}] body must equal {{'error': 'Invalid payload'}}; "
        f"got {body!r}"
    )


def _assert_baseline_unchanged(skip_path, baseline, scenario_label):
    loaded = imap_engine._load_skip_domains()
    assert loaded == baseline, (
        f"[{scenario_label}] failed POST must not mutate the file.\n"
        f"  baseline: {baseline!r}\n  loaded  : {loaded!r}"
    )


# ---------------------------------------------------------------------------
# Scenario 1 — Plain text body (request.get_json(silent=True) returns None)
# ---------------------------------------------------------------------------
def test_invalid_payload_plain_text_body(admin_client):
    """Validates: Requirement 8.4 (non-JSON body)."""
    client, skip_path, baseline = admin_client
    res = client.post(
        "/api/admin/skip-domains",
        data="hello",
        content_type="text/plain",
    )
    _assert_invalid_payload_400(res, "plain text body")
    _assert_baseline_unchanged(skip_path, baseline, "plain text body")


# ---------------------------------------------------------------------------
# Scenario 2 — JSON body that is not a dict
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "payload,label",
    [
        ([1, 2, 3], "json list root"),
        ("hi", "json string root"),
        (None, "json null root"),
    ],
)
def test_invalid_payload_non_dict_root(admin_client, payload, label):
    """Validates: Requirement 8.4 (root must be a dict)."""
    client, skip_path, baseline = admin_client
    res = client.post("/api/admin/skip-domains", json=payload)
    _assert_invalid_payload_400(res, label)
    _assert_baseline_unchanged(skip_path, baseline, label)


# ---------------------------------------------------------------------------
# Scenario 3 — Dict body without "domains" key
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "payload,label",
    [
        ({}, "empty dict"),
        ({"foo": "bar"}, "dict without 'domains' key"),
    ],
)
def test_invalid_payload_missing_domains_key(admin_client, payload, label):
    """Validates: Requirement 8.4 ('domains' key required, must be a list).

    ``data.get('domains')`` returns ``None`` when absent; ``None`` is not a
    list ⇒ 400 Invalid payload.
    """
    client, skip_path, baseline = admin_client
    res = client.post("/api/admin/skip-domains", json=payload)
    _assert_invalid_payload_400(res, label)
    _assert_baseline_unchanged(skip_path, baseline, label)


# ---------------------------------------------------------------------------
# Scenario 4 — Dict body with "domains" not a list
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "payload,label",
    [
        ({"domains": "hotmail"}, "domains is a string"),
        ({"domains": {"a": 1}}, "domains is a dict"),
    ],
)
def test_invalid_payload_domains_not_list(admin_client, payload, label):
    """Validates: Requirement 8.4 ('domains' must be a list)."""
    client, skip_path, baseline = admin_client
    res = client.post("/api/admin/skip-domains", json=payload)
    _assert_invalid_payload_400(res, label)
    _assert_baseline_unchanged(skip_path, baseline, label)


# ---------------------------------------------------------------------------
# Scenario 5 — Dict body with "domains" containing non-string elements.
# CRITICAL: must return "Invalid payload", NOT "Invalid entries"
# (Requirement 9.6 routes through 8.4).
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "payload,label",
    [
        ({"domains": ["hotmail", 42, "outlook"]}, "list contains int"),
        ({"domains": [None]}, "list contains null"),
        ({"domains": [{"x": 1}]}, "list contains dict"),
    ],
)
def test_invalid_payload_non_string_element_routes_to_invalid_payload(
    admin_client, payload, label
):
    """Validates: Requirements 8.4, 9.6.

    Non-string element ⇒ ``"Invalid payload"`` (NOT ``"Invalid entries"``).
    Confirms the structural-validation gate is evaluated BEFORE the
    per-entry validator.
    """
    client, skip_path, baseline = admin_client
    res = client.post("/api/admin/skip-domains", json=payload)
    _assert_invalid_payload_400(res, label)
    body = res.get_json()
    assert body.get("error") != "Invalid entries", (
        f"[{label}] non-string element must produce 'Invalid payload' "
        f"(structural), NOT 'Invalid entries' (per-entry); "
        f"body={body!r}"
    )
    _assert_baseline_unchanged(skip_path, baseline, label)
