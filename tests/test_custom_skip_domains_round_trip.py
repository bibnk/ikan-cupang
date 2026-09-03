"""
Round-trip and read-preservation property tests (custom-skip-domains spec).

Encodes Property 2 ("on-disk round trip and byte-format equivalence") and
Property 4 ("existing file preserved on read") from
``design.md`` §"Correctness Properties".

Validates: Requirements 1.2, 2.2, 3.5, 7.2
"""
from __future__ import annotations

import json
import os
import sys
import time

from hypothesis import HealthCheck, given, settings, strategies as st

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import imap_engine  # noqa: E402
from imap_engine import _WHITESPACE_RE  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _redirect_skip_paths(tmp_path, monkeypatch):
    """Point ``imap_engine.SKIP_DOMAINS_PATH`` and the lock at *tmp_path*."""
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


# ---------------------------------------------------------------------------
# Strategy — entries that survive normalization unchanged so the file's
# stored value equals the input verbatim. Lowercase ASCII letters, digits,
# hyphen, dot. No leading/trailing whitespace (already pre-stripped). No
# internal whitespace (would be rejected by validator). Length 1..255.
# ---------------------------------------------------------------------------
valid_entry = st.from_regex(r"[a-z0-9][a-z0-9.\-]{0,30}", fullmatch=True).filter(
    lambda s: s.strip() == s
    and s == s.lower()
    and not _WHITESPACE_RE.search(s)
    and 1 <= len(s) <= 255
)

valid_entry_list = st.lists(valid_entry, min_size=0, max_size=10, unique=True)


# ---------------------------------------------------------------------------
# Property 2 — On-disk round trip and byte-format equivalence
# ---------------------------------------------------------------------------
@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(value=valid_entry_list)
def test_property2_round_trip_byte_format_and_get_endpoint(
    tmp_path, monkeypatch, value
):
    """Validates: Requirements 1.2, 3.5, 7.2.

    Asserts three sub-claims about the on-disk format:

    1. ``_save_skip_domains(value)`` followed by ``_load_skip_domains()``
       returns ``value`` (round-trip equality).
    2. The file's raw bytes equal
       ``json.dumps(value, indent=2).encode("utf-8")`` — same encoder
       config as ``_atomic_update_master`` (Requirement 3.5).
    3. ``GET /api/admin/skip-domains`` (admin session) returns
       ``200 {"domains": value}`` (Requirement 7.2 — server returns the
       on-disk content verbatim).
    """
    skip_path = _redirect_skip_paths(tmp_path, monkeypatch)
    _reset_skip_files(skip_path)

    # 1. Save → load round trip.
    imap_engine._save_skip_domains(value)
    loaded = imap_engine._load_skip_domains()
    assert loaded == value, (
        f"round trip mismatch.\n  saved : {value!r}\n  loaded: {loaded!r}"
    )

    # 2. Byte-format equivalence with json.dump(value, f, indent=2) written
    # in text mode (UTF-8). Compute expected bytes via the same write
    # pipeline so the comparison is platform-correct (Windows emits \r\n
    # in text mode; this matches what _save_skip_domains writes).
    expected_path = str(tmp_path / "_expected.json")
    with open(expected_path, "w", encoding="utf-8") as f:
        json.dump(value, f, indent=2)
    with open(expected_path, "rb") as f:
        expected_bytes = f.read()

    with open(skip_path, "rb") as f:
        raw = f.read()
    assert raw == expected_bytes, (
        f"on-disk bytes do not match the json.dump(...,indent=2) text-mode "
        f"write encoding.\n"
        f"  expected len={len(expected_bytes)}: {expected_bytes!r}\n"
        f"  actual   len={len(raw)}: {raw!r}"
    )

    # 3. GET endpoint returns the same value.
    import app as flask_app

    client = flask_app.app.test_client()
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["is_admin"] = True
        sess["code_hash"] = "test_hash"
        sess["user_label"] = "tester"

    res = client.get("/api/admin/skip-domains")
    assert res.status_code == 200, (
        f"GET /api/admin/skip-domains expected 200, got {res.status_code}; "
        f"body={res.get_data(as_text=True)!r}"
    )
    body = res.get_json()
    assert body == {"domains": value}, (
        f"GET response body must equal {{'domains': value}}.\n"
        f"  expected: {{'domains': {value!r}}}\n"
        f"  actual  : {body!r}"
    )


# ---------------------------------------------------------------------------
# Property 4 — Existing file preserved on read
# ---------------------------------------------------------------------------
@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(value=valid_entry_list)
def test_property4_existing_file_preserved_on_read(tmp_path, monkeypatch, value):
    """Validates: Requirement 2.2.

    Pre-write a valid file, capture its bytes and mtime, then call
    ``_load_skip_domains()``. The file's raw bytes and mtime MUST be
    unchanged after the read — the read path must NOT rewrite an
    already-valid file.
    """
    skip_path = _redirect_skip_paths(tmp_path, monkeypatch)
    _reset_skip_files(skip_path)

    # Pre-write the file via the public save helper to get the canonical
    # byte format that _load_skip_domains will see.
    imap_engine._save_skip_domains(value)

    pre_bytes = open(skip_path, "rb").read()
    pre_mtime = os.path.getmtime(skip_path)

    # Sleep a tiny bit so a hypothetical rewrite would change mtime.
    time.sleep(0.01)

    loaded = imap_engine._load_skip_domains()
    assert loaded == value, (
        f"_load_skip_domains must return the on-disk value unchanged.\n"
        f"  expected: {value!r}\n  actual  : {loaded!r}"
    )

    post_bytes = open(skip_path, "rb").read()
    post_mtime = os.path.getmtime(skip_path)

    assert post_bytes == pre_bytes, (
        f"_load_skip_domains rewrote the file (bytes changed).\n"
        f"  pre : {pre_bytes!r}\n  post: {post_bytes!r}"
    )
    assert post_mtime == pre_mtime, (
        f"_load_skip_domains rewrote the file (mtime changed: "
        f"{pre_mtime} → {post_mtime})."
    )
