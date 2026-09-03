"""
Property test that the master ``imap_success.json`` is never touched
by skip-domains operations (custom-skip-domains spec).

Encodes Property 7 from ``design.md`` §"Correctness Properties":

  Master imap_success.json untouched. After any ``_save_skip_domains`` or
  ``_load_skip_domains`` call, the master file's bytes and mtime are
  unchanged, and the master's lock file is also unchanged (the two specs
  use SEPARATE FileLock paths — Requirement 13.3).

Validates: Requirements 13.1, 13.2
"""
from __future__ import annotations

import hashlib
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
    skip_path = str(tmp_path / "skip_domains.json")
    monkeypatch.setattr(imap_engine, "SKIP_DOMAINS_PATH", skip_path)
    monkeypatch.setattr(imap_engine, "SKIP_DOMAINS_LOCK_PATH", skip_path + ".lock")
    return skip_path


def _redirect_master_paths(tmp_path, monkeypatch):
    master = str(tmp_path / "imap_success.json")
    monkeypatch.setattr(imap_engine, "MASTER_IMAP_SUCCESS_PATH", master)
    monkeypatch.setattr(imap_engine, "MASTER_IMAP_SUCCESS_LOCK_PATH", master + ".lock")
    return master


def _seed_master(master_path: str) -> bytes:
    """Pre-create a known master file. Returns its initial bytes."""
    contents = (
        b'{\n  "hotmail.com": {\n    "server": "imap-mail.outlook.com",\n'
        b'    "port": 993\n  }\n}'
    )
    with open(master_path, "wb") as f:
        f.write(contents)
    return contents


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


valid_entry = st.from_regex(r"[a-z0-9][a-z0-9.\-]{0,15}", fullmatch=True).filter(
    lambda s: s.strip() == s
    and s == s.lower()
    and not _WHITESPACE_RE.search(s)
    and 1 <= len(s) <= 30
)


# ---------------------------------------------------------------------------
# Property 7 — Master imap_success.json untouched
# ---------------------------------------------------------------------------
@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(value=st.lists(valid_entry, min_size=0, max_size=10, unique=True))
def test_property7_master_imap_success_untouched(tmp_path, monkeypatch, value):
    """Validates: Requirements 13.1, 13.2.

    1. Pre-create a fixture ``imap_success.json`` at
       ``MASTER_IMAP_SUCCESS_PATH`` (redirected to tmp_path) with known
       content. Snapshot bytes + mtime + sha256.
    2. Pre-create the master's lock file (touch) and snapshot its bytes
       + mtime as well.
    3. Run ``_save_skip_domains(value)`` then ``_load_skip_domains()``.
    4. Re-snapshot master bytes/mtime/sha and master-lock bytes/mtime;
       assert all unchanged. The skip-domains spec uses a SEPARATE lock
       (``skip_domains.json.lock``), so the master's lock must be
       untouched too (Requirement 13.3 operationally proven).
    """
    skip_path = _redirect_skip_paths(tmp_path, monkeypatch)
    master_path = _redirect_master_paths(tmp_path, monkeypatch)

    # 1. Seed master file.
    pre_master_bytes = _seed_master(master_path)
    pre_master_mtime = os.path.getmtime(master_path)
    pre_master_sha = _sha256(pre_master_bytes)

    # 2. Pre-create the master's lock file with known content.
    master_lock_path = master_path + ".lock"
    with open(master_lock_path, "wb") as f:
        f.write(b"sentinel-master-lock-content")
    pre_lock_bytes = open(master_lock_path, "rb").read()
    pre_lock_mtime = os.path.getmtime(master_lock_path)

    # Ensure mtime resolution is observable.
    time.sleep(0.01)

    # 3. Run skip-domains operations.
    imap_engine._save_skip_domains(value)
    loaded = imap_engine._load_skip_domains()
    assert loaded == value, (
        f"sanity: skip-domains round trip should still work; "
        f"saved={value!r}, loaded={loaded!r}"
    )


    # 4a. Master file unchanged (bytes + mtime + sha256).
    post_master_bytes = open(master_path, "rb").read()
    post_master_mtime = os.path.getmtime(master_path)
    post_master_sha = _sha256(post_master_bytes)

    assert post_master_bytes == pre_master_bytes, (
        f"master bytes changed after skip-domains operations.\n"
        f"  pre : {pre_master_bytes!r}\n  post: {post_master_bytes!r}"
    )
    assert post_master_mtime == pre_master_mtime, (
        f"master mtime changed: {pre_master_mtime} → {post_master_mtime}"
    )
    assert post_master_sha == pre_master_sha, (
        f"master sha256 changed: {pre_master_sha} → {post_master_sha}"
    )

    # 4b. Master lock unchanged (bytes + mtime).
    post_lock_bytes = open(master_lock_path, "rb").read()
    post_lock_mtime = os.path.getmtime(master_lock_path)

    assert post_lock_bytes == pre_lock_bytes, (
        f"master lock bytes changed (skip-domains spec must use a "
        f"separate lock).\n  pre : {pre_lock_bytes!r}\n"
        f"  post: {post_lock_bytes!r}"
    )
    assert post_lock_mtime == pre_lock_mtime, (
        f"master lock mtime changed: {pre_lock_mtime} → {post_lock_mtime}; "
        f"skip-domains lock path must be separate from master lock."
    )

    # Sanity: skip-domains files exist at their own path.
    assert os.path.exists(skip_path), (
        f"skip-domains file should exist after operations: {skip_path}"
    )
