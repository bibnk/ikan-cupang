"""
Property test for POST normalize-and-persist with full replace
(custom-skip-domains spec).

Encodes Property 3 from ``design.md`` §"Correctness Properties":

  POST normalize-and-persist with full replace. For pre-existing on-disk
  content ``O`` and a new payload ``R``, ``POST /api/admin/skip-domains``
  with ``{"domains": R}`` returns 200 with body
  ``{"success": True, "domains": _normalize_skip_entries(R)}``,
  ``_load_skip_domains()`` returns ``_normalize_skip_entries(R)``, and
  any element of ``O`` not in ``_normalize_skip_entries(R)`` is absent
  from the post-write file.

Validates: Requirements 8.2, 8.3
"""
from __future__ import annotations

import os
import sys

from hypothesis import HealthCheck, given, settings, strategies as st

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import imap_engine  # noqa: E402
from imap_engine import _WHITESPACE_RE, _normalize_skip_entries  # noqa: E402


def _redirect_skip_paths(tmp_path, monkeypatch):
    skip_path = str(tmp_path / "skip_domains.json")
    monkeypatch.setattr(imap_engine, "SKIP_DOMAINS_PATH", skip_path)
    monkeypatch.setattr(imap_engine, "SKIP_DOMAINS_LOCK_PATH", skip_path + ".lock")
    return skip_path


def _reset_skip_files(skip_path: str) -> None:
    for p in (skip_path, skip_path + ".tmp", skip_path + ".lock"):
        try:
            os.remove(p)
        except FileNotFoundError:
            pass


valid_entry = st.from_regex(r"[a-z0-9][a-z0-9.\-]{0,30}", fullmatch=True).filter(
    lambda s: s.strip() == s
    and s == s.lower()
    and not _WHITESPACE_RE.search(s)
    and 1 <= len(s) <= 255
)


@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    pre_existing=st.lists(valid_entry, min_size=0, max_size=10, unique=True),
    new_payload=st.lists(valid_entry, min_size=0, max_size=10),
)
def test_property3_post_normalize_persist_full_replace(
    tmp_path, monkeypatch, pre_existing, new_payload
):
    """Validates: Requirements 8.2, 8.3.

    1. Pre-write ``pre_existing`` (``O``) on disk.
    2. POST ``{"domains": new_payload}`` (``R``) with admin session.
    3. Response: 200, body == ``{"success": True, "domains":
       _normalize_skip_entries(R)}``.
    4. ``_load_skip_domains()`` returns ``_normalize_skip_entries(R)`` —
       confirms full replace, not merge.
    5. Any entry of ``O`` that is not in ``_normalize_skip_entries(R)`` is
       absent from the post-write file (no leftover residue).
    """
    skip_path = _redirect_skip_paths(tmp_path, monkeypatch)
    _reset_skip_files(skip_path)

    # 1. Pre-write O.
    imap_engine._save_skip_domains(pre_existing)

    expected_normalized = _normalize_skip_entries(new_payload)

    # 2. POST R via Flask test client with admin session.
    import app as flask_app

    client = flask_app.app.test_client()
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["is_admin"] = True
        sess["code_hash"] = "test_hash"
        sess["user_label"] = "tester"

    res = client.post(
        "/api/admin/skip-domains",
        json={"domains": list(new_payload)},
    )

    # 3. Response status + body.
    assert res.status_code == 200, (
        f"POST expected 200, got {res.status_code}; "
        f"body={res.get_data(as_text=True)!r}\n"
        f"  pre_existing: {pre_existing!r}\n  new_payload : {new_payload!r}"
    )
    body = res.get_json()
    assert body == {"success": True, "domains": expected_normalized}, (
        f"POST response body must equal "
        f"{{'success': True, 'domains': _normalize_skip_entries(R)}}.\n"
        f"  expected: {{'success': True, 'domains': {expected_normalized!r}}}\n"
        f"  actual  : {body!r}"
    )

    # 4. _load_skip_domains() returns the normalized R (full replace).
    loaded = imap_engine._load_skip_domains()
    assert loaded == expected_normalized, (
        f"_load_skip_domains() must return _normalize_skip_entries(R) after POST.\n"
        f"  expected: {expected_normalized!r}\n  actual  : {loaded!r}"
    )

    # 5. Any element of O not in normalized R is absent from disk.
    residue = [o for o in pre_existing if o not in expected_normalized]
    for r in residue:
        assert r not in loaded, (
            f"pre-existing entry {r!r} was not in normalized payload "
            f"{expected_normalized!r} but is still present after POST "
            f"(full-replace violated). loaded={loaded!r}, "
            f"pre_existing={pre_existing!r}"
        )
