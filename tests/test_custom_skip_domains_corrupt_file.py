"""
Edge-case test for corrupt skip-domains file fallback (custom-skip-domains spec).

Three sub-scenarios encode Requirements 2.3 + 5.4 directly:

1. Invalid JSON              → ``_load_skip_domains() == []`` and file
                                bytes unchanged (no auto-rewrite).
2. Non-list root             → same as (1).
3. List with non-string elem → same as (1).

In each case ``ImapChecker`` constructed with the corrupt file MUST end
up with ``self.skip_domain_keywords == []``, and driving ``_worker`` past
an account whose domain would have been skipped under defaults must NOT
classify the account as ``domain_skipped`` (because the empty list makes
the predicate False for every domain — Requirement 6.2 corollary).

Validates: Requirements 2.3, 5.4
"""
from __future__ import annotations

import os
import sys
from queue import Queue

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


def _force_known_domain(domain, monkeypatch):
    monkeypatch.setattr(
        imap_engine,
        "DEFAULT_IMAP_CONFIG",
        {domain: {"server": f"imap.{domain}", "port": 993}},
    )


def _no_real_imap(*args, **kwargs):
    raise OSError("mocked: no network in corrupt-file tests")


def _drive_worker_with_corrupt_file(
    tmp_path, monkeypatch, corrupt_bytes, account_domain
):
    """Common harness: pre-write *corrupt_bytes* to SKIP_DOMAINS_PATH,
    construct ImapChecker, drive _worker for one synthetic account whose
    domain WOULD be skipped under defaults (e.g. ``hotmail.com``), and
    return ``(checker, results_dir, pre_bytes, post_bytes)``.
    """
    skip_path = _redirect_skip_paths(tmp_path, monkeypatch)
    _isolate_master(tmp_path, monkeypatch)
    _force_known_domain(account_domain, monkeypatch)
    monkeypatch.setattr(imap_engine.imaplib, "IMAP4_SSL", _no_real_imap)
    monkeypatch.setattr(imap_engine.imaplib, "IMAP4", _no_real_imap)


    with open(skip_path, "wb") as f:
        f.write(corrupt_bytes)
    pre_bytes = open(skip_path, "rb").read()
    assert pre_bytes == corrupt_bytes, "pre-write sanity"

    # Direct loader returns [].
    loaded = imap_engine._load_skip_domains()
    assert loaded == [], (
        f"_load_skip_domains() on corrupt file must return []; got {loaded!r}; "
        f"corrupt_bytes={corrupt_bytes!r}"
    )

    # File must not be auto-rewritten.
    post_bytes_after_load = open(skip_path, "rb").read()
    assert post_bytes_after_load == pre_bytes, (
        f"corrupt file was auto-rewritten by _load_skip_domains.\n"
        f"  pre : {pre_bytes!r}\n  post: {post_bytes_after_load!r}"
    )

    # Build ImapChecker — its constructor calls _load_skip_domains(), which
    # must yield self.skip_domain_keywords == [].
    results_dir = tmp_path / "jobs" / "corrupt_test"
    results_dir.mkdir(parents=True, exist_ok=True)
    checker = imap_engine.ImapChecker(
        job_id="corrupt_test",
        results_dir=str(results_dir),
        accounts=[{"email": f"alice@{account_domain}", "password": "hunter2"}],
        target_senders=[],
        keywords=[],
        search_days=1,
    )
    assert checker.skip_domain_keywords == [], (
        f"ImapChecker.skip_domain_keywords must be [] after corrupt file load; "
        f"got {checker.skip_domain_keywords!r}"
    )

    # Drive _worker for the account.
    q = Queue()
    q.put(checker.accounts[0])
    checker._worker(q)

    return checker, results_dir, pre_bytes


def _assert_account_not_skipped(checker, results_dir, account_email):
    """Assert that *account_email* was NOT classified as domain_skipped."""
    domain_skip_file = os.path.join(str(results_dir), "domain_skipped.txt")
    if os.path.exists(domain_skip_file):
        contents = open(domain_skip_file, "r", encoding="utf-8").read()
        assert account_email not in contents, (
            f"corrupt file → empty skip list → predicate False, but "
            f"account was written to domain_skipped.txt.\n"
            f"  contents: {contents!r}\n  account : {account_email!r}"
        )
    assert checker.skipped_count == 0, (
        f"skipped_count must be 0 for empty skip list; got {checker.skipped_count}"
    )


# ---------------------------------------------------------------------------
# Sub-scenario 1 — Invalid JSON
# ---------------------------------------------------------------------------
def test_corrupt_invalid_json_returns_empty_no_rewrite(tmp_path, monkeypatch):
    """Validates: Requirements 2.3, 5.4 (invalid JSON branch)."""
    checker, results_dir, _ = _drive_worker_with_corrupt_file(
        tmp_path,
        monkeypatch,
        corrupt_bytes=b"{not json}",
        account_domain="hotmail.com",
    )
    _assert_account_not_skipped(checker, results_dir, "alice@hotmail.com:hunter2")


# ---------------------------------------------------------------------------
# Sub-scenario 2 — Non-list root
# ---------------------------------------------------------------------------
def test_corrupt_non_list_root_returns_empty_no_rewrite(tmp_path, monkeypatch):
    """Validates: Requirements 2.3, 5.4 (root is dict, not list)."""
    checker, results_dir, _ = _drive_worker_with_corrupt_file(
        tmp_path,
        monkeypatch,
        corrupt_bytes=b'{"foo": "bar"}',
        account_domain="hotmail.com",
    )
    _assert_account_not_skipped(checker, results_dir, "alice@hotmail.com:hunter2")


# ---------------------------------------------------------------------------
# Sub-scenario 3 — List with non-string element
# ---------------------------------------------------------------------------
def test_corrupt_list_non_string_element_returns_empty_no_rewrite(
    tmp_path, monkeypatch
):
    """Validates: Requirements 2.3, 5.4 (list contains a non-str element)."""
    checker, results_dir, _ = _drive_worker_with_corrupt_file(
        tmp_path,
        monkeypatch,
        corrupt_bytes=b'["hotmail", 42, "outlook"]',
        account_domain="hotmail.com",
    )
    _assert_account_not_skipped(checker, results_dir, "alice@hotmail.com:hunter2")
