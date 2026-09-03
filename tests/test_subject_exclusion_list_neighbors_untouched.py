"""
Property: master imap_success.json AND skip_domains.json untouched
(subject-exclusion-list spec).

Encodes Property 8 from ``design.md`` §"Correctness Properties".

Validates: Requirements 13.1, 13.2, 13.3, 13.4, 13.5
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


def _redirect_subject(tmp_path, monkeypatch):
    p = str(tmp_path / "subject_exclusion_list.json")
    monkeypatch.setattr(imap_engine, "SUBJECT_EXCLUSION_PATH", p)
    monkeypatch.setattr(imap_engine, "SUBJECT_EXCLUSION_LOCK_PATH", p + ".lock")
    return p


def _redirect_master(tmp_path, monkeypatch):
    m = str(tmp_path / "imap_success.json")
    monkeypatch.setattr(imap_engine, "MASTER_IMAP_SUCCESS_PATH", m)
    monkeypatch.setattr(imap_engine, "MASTER_IMAP_SUCCESS_LOCK_PATH", m + ".lock")
    return m


def _redirect_skip(tmp_path, monkeypatch):
    s = str(tmp_path / "skip_domains.json")
    monkeypatch.setattr(imap_engine, "SKIP_DOMAINS_PATH", s)
    monkeypatch.setattr(imap_engine, "SKIP_DOMAINS_LOCK_PATH", s + ".lock")
    return s


def _sha(b):
    return hashlib.sha256(b).hexdigest()


valid_entry = st.from_regex(r"[a-z0-9][a-z0-9 .\-]{0,15}", fullmatch=True).filter(
    lambda s: s.strip() == s and 1 <= len(s) <= 100 and s == s.lower()
)


@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(value=st.lists(valid_entry, min_size=0, max_size=5, unique=True))
def test_property8_neighbors_untouched(tmp_path, monkeypatch, value):
    """Validates: Requirements 13.1, 13.2, 13.3, 13.4, 13.5."""
    _redirect_subject(tmp_path, monkeypatch)
    master = _redirect_master(tmp_path, monkeypatch)
    skip = _redirect_skip(tmp_path, monkeypatch)

    # Pre-create master with known content via the master's own helper.
    def _seed_master(d):
        d["test.com"] = {"server": "imap.test.com", "port": 993}
        return True

    imap_engine._atomic_update_master(_seed_master)
    pre_master_bytes = open(master, "rb").read()
    pre_master_mtime = os.path.getmtime(master)
    pre_master_sha = _sha(pre_master_bytes)

    # Pre-create skip-domains with known content.
    imap_engine._save_skip_domains(["hotmail", "live"])
    pre_skip_bytes = open(skip, "rb").read()
    pre_skip_mtime = os.path.getmtime(skip)
    pre_skip_sha = _sha(pre_skip_bytes)

    time.sleep(0.01)

    # Run subject-exclusion operations.
    imap_engine._save_subject_exclusion_list(value)
    loaded = imap_engine._load_subject_exclusion_list()
    assert loaded == value

    # Master unchanged
    post_master_bytes = open(master, "rb").read()
    assert post_master_bytes == pre_master_bytes
    assert os.path.getmtime(master) == pre_master_mtime
    assert _sha(post_master_bytes) == pre_master_sha

    # Skip-domains unchanged
    post_skip_bytes = open(skip, "rb").read()
    assert post_skip_bytes == pre_skip_bytes
    assert os.path.getmtime(skip) == pre_skip_mtime
    assert _sha(post_skip_bytes) == pre_skip_sha
