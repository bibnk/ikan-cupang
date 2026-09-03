"""
Round-trip and read-preservation property tests (subject-exclusion-list spec).

Encodes Property 2 (round trip + byte format equivalence + GET endpoint) and
the read-preservation aspect from ``design.md`` §"Correctness Properties".

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


def _redirect_subject_exclusion_paths(tmp_path, monkeypatch):
    p = str(tmp_path / "subject_exclusion_list.json")
    monkeypatch.setattr(imap_engine, "SUBJECT_EXCLUSION_PATH", p)
    monkeypatch.setattr(imap_engine, "SUBJECT_EXCLUSION_LOCK_PATH", p + ".lock")
    return p


def _reset(p):
    for x in (p, p + ".tmp", p + ".lock"):
        try:
            os.remove(x)
        except FileNotFoundError:
            pass


# Strategy: entries that survive normalization unchanged.
# - Already lowercase
# - No leading/trailing whitespace
# - No control chars (per Requirement 9.4)
# - Length 1..500
# - Internal ASCII space allowed
valid_entry = st.from_regex(r"[a-z0-9][a-z0-9 .\-]{0,30}", fullmatch=True).filter(
    lambda s: s.strip() == s and 1 <= len(s) <= 500 and s == s.lower()
)
valid_entry_list = st.lists(valid_entry, min_size=0, max_size=8, unique=True)


@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(value=valid_entry_list)
def test_property2_round_trip_byte_format_and_get(tmp_path, monkeypatch, value):
    """Validates: Requirements 1.2, 3.5, 7.2."""
    p = _redirect_subject_exclusion_paths(tmp_path, monkeypatch)
    _reset(p)

    # 1. Round trip
    imap_engine._save_subject_exclusion_list(value)
    loaded = imap_engine._load_subject_exclusion_list()
    assert loaded == value, f"round-trip mismatch: saved={value!r}, loaded={loaded!r}"

    # 2. Byte format identical to text-mode json.dump(value, f, indent=2)
    expected_path = str(tmp_path / "_expected.json")
    with open(expected_path, "w", encoding="utf-8") as f:
        json.dump(value, f, indent=2)
    with open(expected_path, "rb") as f:
        expected_bytes = f.read()
    raw = open(p, "rb").read()
    assert raw == expected_bytes

    # 3. GET endpoint returns same value
    import app as flask_app

    client = flask_app.app.test_client()
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["is_admin"] = True
        sess["code_hash"] = "test_hash"
        sess["user_label"] = "tester"

    res = client.get("/api/admin/subject-exclusion-list")
    assert res.status_code == 200
    body = res.get_json()
    assert body == {"patterns": value}


@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(value=valid_entry_list)
def test_existing_file_preserved_on_read(tmp_path, monkeypatch, value):
    """Validates: Requirement 2.2 — read path must NOT rewrite valid file."""
    p = _redirect_subject_exclusion_paths(tmp_path, monkeypatch)
    _reset(p)

    imap_engine._save_subject_exclusion_list(value)
    pre_bytes = open(p, "rb").read()
    pre_mtime = os.path.getmtime(p)

    time.sleep(0.01)

    loaded = imap_engine._load_subject_exclusion_list()
    assert loaded == value

    post_bytes = open(p, "rb").read()
    post_mtime = os.path.getmtime(p)
    assert post_bytes == pre_bytes
    assert post_mtime == pre_mtime
