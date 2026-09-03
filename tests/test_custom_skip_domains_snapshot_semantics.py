"""
Snapshot-semantics example test (custom-skip-domains spec).

Encodes the non-retroactive snapshot behavior from
``design.md`` §"Sequence Diagrams > Diagram 2": jobs running before an
admin POST keep their original snapshot (Requirement 5.3); jobs created
AFTER the POST see the new content (Requirement 12.2).

Validates: Requirements 5.3, 12.2
"""
from __future__ import annotations

import os
import sys
from queue import Queue

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import imap_engine  # noqa: E402


def _redirect_skip_paths(tmp_path, monkeypatch):
    skip_path = str(tmp_path / "skip_domains.json")
    monkeypatch.setattr(imap_engine, "SKIP_DOMAINS_PATH", skip_path)
    monkeypatch.setattr(imap_engine, "SKIP_DOMAINS_LOCK_PATH", skip_path + ".lock")
    return skip_path


def _isolate_master(tmp_path, monkeypatch):
    master = str(tmp_path / "imap_success.json")
    monkeypatch.setattr(imap_engine, "MASTER_IMAP_SUCCESS_PATH", master)
    monkeypatch.setattr(imap_engine, "MASTER_IMAP_SUCCESS_LOCK_PATH", master + ".lock")


def _force_known_domains(monkeypatch, *domains):
    cfg = {d: {"server": f"imap.{d}", "port": 993} for d in domains}
    monkeypatch.setattr(imap_engine, "DEFAULT_IMAP_CONFIG", cfg)


def _no_real_imap(*args, **kwargs):
    raise OSError("mocked: no network in snapshot tests")


def _drive_one_account(checker, account):
    q = Queue()
    q.put(account)
    checker._worker(q)


def test_snapshot_non_retroactive_and_next_job_sees_new_content(
    tmp_path, monkeypatch
):
    """Validates: Requirements 5.3, 12.2.

    Scenario:
      1. Pre-write SKIP_DOMAINS_PATH with ``["hotmail"]`` (V1).
      2. Instantiate ``job_a = ImapChecker(...)`` — snapshot V1.
      3. Mid-test, call ``_save_skip_domains(["gmail"])`` (V2),
         simulating an admin POST during job_a's execution.
      4. Drive ``job_a._worker`` past two synthetic accounts:
         - ``alice@hotmail.com`` MUST be classified ``domain_skipped``
           (V1 snapshot still applied — Requirement 5.3 non-retroactive).
         - ``bob@gmail.com`` MUST NOT be classified ``domain_skipped``
           (V2 not applied to running job).
      5. Instantiate ``job_b = ImapChecker(...)`` AFTER the save.
         ``job_b.skip_domain_keywords`` MUST equal ``["gmail"]``
         (Requirement 12.2 — next job snapshot reflects new content).
    """
    _redirect_skip_paths(tmp_path, monkeypatch)
    _isolate_master(tmp_path, monkeypatch)
    _force_known_domains(monkeypatch, "hotmail.com", "gmail.com")
    monkeypatch.setattr(imap_engine.imaplib, "IMAP4_SSL", _no_real_imap)
    monkeypatch.setattr(imap_engine.imaplib, "IMAP4", _no_real_imap)

    # 1. Pre-write V1.
    imap_engine._save_skip_domains(["hotmail"])

    # 2. Construct job_a with snapshot V1.
    job_a_dir = tmp_path / "jobs" / "job_a"
    job_a_dir.mkdir(parents=True, exist_ok=True)
    job_a = imap_engine.ImapChecker(
        job_id="job_a",
        results_dir=str(job_a_dir),
        accounts=[
            {"email": "alice@hotmail.com", "password": "pw_a"},
            {"email": "bob@gmail.com", "password": "pw_b"},
        ],
        target_senders=[],
        keywords=[],
        search_days=1,
    )
    assert job_a.skip_domain_keywords == ["hotmail"], (
        f"job_a snapshot must capture V1=['hotmail']; got "
        f"{job_a.skip_domain_keywords!r}"
    )


    # 3. Admin POST mid-job: rewrite skip-domains to V2=["gmail"].
    imap_engine._save_skip_domains(["gmail"])
    assert imap_engine._load_skip_domains() == ["gmail"], (
        "sanity: V2 must now be on disk"
    )

    # 4. Drive job_a._worker past the two accounts. job_a uses V1 snapshot.
    _drive_one_account(job_a, job_a.accounts[0])  # alice@hotmail.com
    _drive_one_account(job_a, job_a.accounts[1])  # bob@gmail.com

    domain_skip_path = os.path.join(str(job_a_dir), "domain_skipped.txt")
    assert os.path.exists(domain_skip_path), (
        f"job_a must have written to domain_skipped.txt for alice@hotmail.com "
        f"under V1 snapshot. path={domain_skip_path}"
    )
    contents = open(domain_skip_path, "r", encoding="utf-8").read()
    assert "alice@hotmail.com:pw_a" in contents, (
        f"alice@hotmail.com must be classified domain_skipped under V1 "
        f"snapshot (Requirement 5.3 non-retroactive). contents={contents!r}"
    )
    assert "bob@gmail.com:pw_b" not in contents, (
        f"bob@gmail.com must NOT be classified domain_skipped under V1 "
        f"snapshot — V2 must NOT apply to running job_a. contents={contents!r}"
    )
    assert job_a.skipped_count == 1, (
        f"job_a must have exactly one skipped account (alice@hotmail.com); "
        f"got skipped_count={job_a.skipped_count}"
    )

    # 5. New job_b after the save sees V2=["gmail"].
    job_b_dir = tmp_path / "jobs" / "job_b"
    job_b_dir.mkdir(parents=True, exist_ok=True)
    job_b = imap_engine.ImapChecker(
        job_id="job_b",
        results_dir=str(job_b_dir),
        accounts=[],
    )
    assert job_b.skip_domain_keywords == ["gmail"], (
        f"job_b (instantiated AFTER POST) must capture V2=['gmail'] "
        f"(Requirement 12.2). got {job_b.skip_domain_keywords!r}"
    )
