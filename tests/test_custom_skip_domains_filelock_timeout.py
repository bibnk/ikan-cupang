"""
FileLock timeout example tests (custom-skip-domains spec).

Three sub-scenarios encode Requirements 3.4(a) and 3.4(b):

1. **Constructor fallback (3.4(b))** — When ``_load_skip_domains`` raises
   ``filelock.Timeout`` during ``ImapChecker.__init__``, the constructor
   must NOT raise; ``self.skip_domain_keywords`` must equal
   ``list(_DEFAULT_SKIP_SET)``; a stderr warning containing
   ``"skip-domains lock timeout"`` must be emitted.
2. **GET endpoint (3.4(a))** — When the loader times out under an admin
   session, the GET endpoint returns 503 with body
   ``{"error": "Skip-domains file is busy, please retry"}`` and the file
   is NOT written.
3. **POST endpoint (3.4(a))** — When the saver times out (after passing
   structural+per-entry validation), the POST endpoint returns 503 with
   the same body and the file is NOT written.

Validates: Requirements 3.4(a), 3.4(b)
"""
from __future__ import annotations

import os
import sys

from filelock import Timeout

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import imap_engine  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _redirect_skip_paths(tmp_path, monkeypatch):
    skip_path = str(tmp_path / "skip_domains.json")
    monkeypatch.setattr(imap_engine, "SKIP_DOMAINS_PATH", skip_path)
    monkeypatch.setattr(imap_engine, "SKIP_DOMAINS_LOCK_PATH", skip_path + ".lock")
    return skip_path


def _isolate_master(tmp_path, monkeypatch):
    master = str(tmp_path / "imap_success.json")
    monkeypatch.setattr(imap_engine, "MASTER_IMAP_SUCCESS_PATH", master)
    monkeypatch.setattr(imap_engine, "MASTER_IMAP_SUCCESS_LOCK_PATH", master + ".lock")


def _raise_timeout(*args, **kwargs):
    """Drop-in replacement for ``_load_skip_domains`` / ``_save_skip_domains``
    that raises ``filelock.Timeout`` immediately."""
    raise Timeout("simulated skip-domains lock timeout")


# ---------------------------------------------------------------------------
# Sub-scenario 1 — Constructor fallback (Requirement 3.4(b))
# ---------------------------------------------------------------------------
def test_filelock_timeout_constructor_falls_back_to_default(
    tmp_path, monkeypatch, capsys
):
    """Validates: Requirement 3.4(b).

    With ``_load_skip_domains`` raising ``filelock.Timeout``,
    ``ImapChecker(...)`` must NOT raise; instead it falls back to
    ``list(_DEFAULT_SKIP_SET)`` in-memory and emits a stderr warning
    containing ``"skip-domains lock timeout"``.
    """
    _redirect_skip_paths(tmp_path, monkeypatch)
    _isolate_master(tmp_path, monkeypatch)

    # Patch _load_skip_domains in the imap_engine module so __init__'s
    # call site sees the timeout-raising version.
    monkeypatch.setattr(imap_engine, "_load_skip_domains", _raise_timeout)

    results_dir = tmp_path / "jobs" / "lock_timeout_init"
    results_dir.mkdir(parents=True, exist_ok=True)

    # Constructor must not raise.
    checker = imap_engine.ImapChecker(
        job_id="lock_timeout_init",
        results_dir=str(results_dir),
        accounts=[],
    )

    # Snapshot equals list(_DEFAULT_SKIP_SET), and is a fresh copy
    # (mutating the snapshot must not affect the module constant).
    assert checker.skip_domain_keywords == list(imap_engine._DEFAULT_SKIP_SET), (
        f"fallback snapshot must equal _DEFAULT_SKIP_SET; got "
        f"{checker.skip_domain_keywords!r}"
    )
    assert checker.skip_domain_keywords is not imap_engine._DEFAULT_SKIP_SET, (
        "fallback snapshot must be a separate list (not the module constant)"
    )

    # Stderr warning emitted.
    captured = capsys.readouterr()
    assert "skip-domains lock timeout" in captured.err, (
        f"expected stderr warning containing 'skip-domains lock timeout'.\n"
        f"  stderr: {captured.err!r}"
    )


# ---------------------------------------------------------------------------
# Sub-scenario 2 — GET endpoint 503 (Requirement 3.4(a))
# ---------------------------------------------------------------------------
def test_filelock_timeout_get_endpoint_returns_503(tmp_path, monkeypatch):
    """Validates: Requirement 3.4(a) for GET.

    With ``_load_skip_domains`` raising ``filelock.Timeout``,
    ``GET /api/admin/skip-domains`` (admin session) returns 503 with body
    ``{"error": "Skip-domains file is busy, please retry"}`` and the file
    is NOT written.
    """
    skip_path = _redirect_skip_paths(tmp_path, monkeypatch)
    _isolate_master(tmp_path, monkeypatch)

    # The endpoint imports _load_skip_domains from imap_engine into the
    # app module's namespace. Patch it where the endpoint looks it up.
    import app as flask_app

    monkeypatch.setattr(flask_app, "_load_skip_domains", _raise_timeout)

    client = flask_app.app.test_client()
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["is_admin"] = True
        sess["code_hash"] = "test_hash"
        sess["user_label"] = "tester"

    res = client.get("/api/admin/skip-domains")
    assert res.status_code == 503, (
        f"GET expected 503, got {res.status_code}; "
        f"body={res.get_data(as_text=True)!r}"
    )
    body = res.get_json()
    assert body == {"error": "Skip-domains file is busy, please retry"}, (
        f"GET 503 body mismatch.\n"
        f"  expected: {{'error': 'Skip-domains file is busy, please retry'}}\n"
        f"  actual  : {body!r}"
    )
    assert not os.path.exists(skip_path), (
        f"GET timeout must not create the skip-domains file: {skip_path}"
    )


# ---------------------------------------------------------------------------
# Sub-scenario 3 — POST endpoint 503 (Requirement 3.4(a))
# ---------------------------------------------------------------------------
def test_filelock_timeout_post_endpoint_returns_503(tmp_path, monkeypatch):
    """Validates: Requirement 3.4(a) for POST.

    With ``_save_skip_domains`` raising ``filelock.Timeout`` (after
    structural+per-entry validation passes for a valid payload), the
    POST endpoint returns 503 with body
    ``{"error": "Skip-domains file is busy, please retry"}`` and the
    file is NOT written.
    """
    skip_path = _redirect_skip_paths(tmp_path, monkeypatch)
    _isolate_master(tmp_path, monkeypatch)

    import app as flask_app

    monkeypatch.setattr(flask_app, "_save_skip_domains", _raise_timeout)

    client = flask_app.app.test_client()
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["is_admin"] = True
        sess["code_hash"] = "test_hash"
        sess["user_label"] = "tester"

    # Valid payload so we get past structural + per-entry validation.
    res = client.post(
        "/api/admin/skip-domains",
        json={"domains": ["hotmail", "gmail"]},
    )
    assert res.status_code == 503, (
        f"POST expected 503, got {res.status_code}; "
        f"body={res.get_data(as_text=True)!r}"
    )
    body = res.get_json()
    assert body == {"error": "Skip-domains file is busy, please retry"}, (
        f"POST 503 body mismatch.\n"
        f"  expected: {{'error': 'Skip-domains file is busy, please retry'}}\n"
        f"  actual  : {body!r}"
    )
    assert not os.path.exists(skip_path), (
        f"POST timeout must not write the skip-domains file: {skip_path}"
    )
