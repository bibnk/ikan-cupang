"""
Property test that ``_worker`` drops emails iff ``_subject_excluded`` returns True
(subject-exclusion-list spec).

Encodes Property 7 from ``design.md`` §"Correctness Properties".

Validates: Requirement 6.3
"""
from __future__ import annotations

import os
import sys
import uuid
from email.header import Header
from email.message import Message
from queue import Queue
from unittest.mock import MagicMock

from hypothesis import HealthCheck, given, settings, strategies as st

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import imap_engine  # noqa: E402
from imap_engine import _subject_excluded  # noqa: E402


def _redirect_subject(tmp_path, monkeypatch):
    p = str(tmp_path / "subject_exclusion_list.json")
    monkeypatch.setattr(imap_engine, "SUBJECT_EXCLUSION_PATH", p)
    monkeypatch.setattr(imap_engine, "SUBJECT_EXCLUSION_LOCK_PATH", p + ".lock")


def _isolate_master(tmp_path, monkeypatch):
    m = str(tmp_path / "imap_success.json")
    monkeypatch.setattr(imap_engine, "MASTER_IMAP_SUCCESS_PATH", m)
    monkeypatch.setattr(imap_engine, "MASTER_IMAP_SUCCESS_LOCK_PATH", m + ".lock")


def _isolate_skip(tmp_path, monkeypatch):
    s = str(tmp_path / "skip_domains.json")
    monkeypatch.setattr(imap_engine, "SKIP_DOMAINS_PATH", s)
    monkeypatch.setattr(imap_engine, "SKIP_DOMAINS_LOCK_PATH", s + ".lock")


def _force_known_domain(monkeypatch, domain="example.com"):
    monkeypatch.setattr(
        imap_engine,
        "DEFAULT_IMAP_CONFIG",
        {domain: {"server": f"imap.{domain}", "port": 993}},
    )


def _build_rfc822(from_header, subject, date_header):
    msg = Message()
    msg["From"] = from_header
    msg["Subject"] = str(Header(subject, "utf-8"))
    msg["Date"] = date_header
    msg.set_payload("Body\r\n")
    return msg.as_bytes()


def _build_fetch_return(uid, rfc822_bytes):
    header = uid + b" (RFC822 {%d}" % len(rfc822_bytes)
    return ("OK", [(header, rfc822_bytes), b")"])


def _build_search_side_effect(from_resp, subject_resp):
    def se(charset, criteria):
        if 'FROM "' in criteria:
            return from_resp
        if 'SUBJECT "' in criteria:
            return subject_resp
        return ("OK", [b""])
    return se


valid_pattern = st.from_regex(r"[a-z][a-z0-9 .\-]{0,10}", fullmatch=True).filter(
    lambda s: s.strip() == s and 1 <= len(s) <= 30 and s == s.lower()
)

# Subjects: ASCII letters/digits/spaces, length 1-50.
subject_strategy = st.text(
    alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Nd"),
        whitelist_characters=" .-",
    ),
    min_size=1,
    max_size=50,
)


@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    subject=subject_strategy,
    patterns=st.lists(valid_pattern, min_size=0, max_size=5, unique=True),
)
def test_property7_worker_drops_iff_excluded(
    tmp_path, monkeypatch, subject, patterns
):
    """Validates: Requirement 6.3."""
    _redirect_subject(tmp_path, monkeypatch)
    _isolate_master(tmp_path, monkeypatch)
    _isolate_skip(tmp_path, monkeypatch)
    _force_known_domain(monkeypatch)

    # Force loader to return the generated patterns.
    monkeypatch.setattr(
        imap_engine, "_load_subject_exclusion_list", lambda: list(patterns)
    )

    # Stub IMAP — return one email with the generated subject.
    rfc = _build_rfc822(
        from_header="alice@example.com",
        subject=subject,
        date_header="Sat, 04 Jan 2025 10:00:00 +0000",
    )

    mock_conn = MagicMock()
    # FROM-search returns UID 1 (so the three-arm gate's first arm via
    # target_senders=[] → True OR sender_matches True OR subject set
    # — easiest with target_senders=[] so first arm True).
    mock_conn.search.side_effect = _build_search_side_effect(
        from_resp=("OK", [b""]),
        subject_resp=("OK", [b"1"]),
    )
    mock_conn.fetch.return_value = _build_fetch_return(b"1", rfc)

    monkeypatch.setattr(
        imap_engine.imaplib,
        "IMAP4_SSL",
        lambda *a, **k: mock_conn,
    )

    # Per-example fresh results dir to avoid Hypothesis cross-pollution.
    results_dir = tmp_path / f"jobs_{uuid.uuid4().hex[:12]}"
    results_dir.mkdir(parents=True, exist_ok=True)

    account = {"email": "alice@example.com", "password": "pw"}
    checker = imap_engine.ImapChecker(
        job_id="prop7",
        results_dir=str(results_dir),
        accounts=[account],
        target_senders=[],
        keywords=["x"],  # any non-empty so SUBJECT search runs
        search_days=1,
    )
    assert checker.subject_exclusion_patterns == list(patterns)

    q = Queue()
    q.put(account)
    checker._worker(q)

    expected_excluded = _subject_excluded(subject, patterns)

    if expected_excluded:
        # Email dropped → noemail or no live entry.
        assert checker.live_count == 0, (
            f"expected drop but live_count={checker.live_count}; "
            f"subject={subject!r}, patterns={patterns!r}"
        )
    else:
        # Email kept → live_count == 1.
        assert checker.live_count == 1, (
            f"expected keep but live_count={checker.live_count}; "
            f"subject={subject!r}, patterns={patterns!r}"
        )
