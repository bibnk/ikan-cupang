"""
Bug-condition exploration tests for spec ``subject-keyword-match-shown-in-live``.

These tests encode ``isBugCondition`` from ``bugfix.md`` directly against the
**unfixed** ``imap_engine.py``. Tests 1 and 2 are authored to FAIL on unfixed
code — that failure is the success signal: it confirms Case A and Case B from
``bugfix.md`` §"Counterexample" / §"Bug Condition" exist. Test 3 is the OTP
sanity gate which MUST pass on both unfixed and fixed code, confirming that
``OTP_SUBJECT_REGEX.match`` is the outermost gate (Requirement 3.2).

After the surgical fix in Task 2 is applied, all three tests pass without
modification.

Mocking strategy:

* ``imap_engine.imaplib.IMAP4_SSL`` is replaced with a factory that returns a
  ``MagicMock`` ``mail_conn``. The mock's ``.login`` / ``.select`` / ``.search``
  / ``.fetch`` / ``.logout`` methods cover every call site in
  ``ImapChecker._worker``'s connect+search+fetch flow.
* ``mail_conn.search.side_effect`` routes by criteria substring so FROM and
  SUBJECT branches return the right per-case UID lists.
* ``mail_conn.fetch`` returns RFC822 bytes in imaplib's standard
  ``("OK", [(b"<uid> (RFC822 {N}", rfc822_bytes), b")"])`` shape.
* ``imap_engine.MASTER_IMAP_SUCCESS_PATH`` is redirected to ``tmp_path`` so the
  real project-root ``imap_success.json`` is never touched by the test (the
  ``_update_imap_config`` call inside ``_worker``'s connect flow writes to the
  redirected path).
* ``imap_engine.DEFAULT_IMAP_CONFIG`` is monkey-patched to contain ``example.com``
  so ``_worker`` skips the ``_try_imap_variants`` auto-discovery branch.
* ``ImapChecker._write_live`` / ``._write_noemail`` / ``._update_progress`` are
  spied via ``unittest.mock.patch.object`` with ``autospec=True`` so we can
  inspect the exact arguments the engine passed.

Validates: 1.1, 1.2, 1.3
"""
from __future__ import annotations

import os
import sys
from email.header import Header
from email.message import Message
from queue import Queue
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _build_rfc822(from_header: str, subject: str, date_header: str) -> bytes:
    """Build a minimal RFC822 message with MIME-encoded headers.

    Uses ``email.header.Header(..., "utf-8")`` so non-ASCII characters in the
    subject (e.g. en-dash ``\u2013`` for the OTP example, em-dash ``\u2014``
    for the Case A subject) round-trip correctly through
    ``email.message_from_bytes`` + ``decode_mime_words`` inside ``_worker``.
    """
    msg = Message()
    msg["From"] = from_header
    msg["Subject"] = str(Header(subject, "utf-8"))
    msg["Date"] = date_header
    msg.set_payload("Body of message\r\n")
    return msg.as_bytes()


def _isolate_master(tmp_path, monkeypatch):
    """Redirect MASTER_IMAP_SUCCESS_PATH to a tmp_path-scoped file so the real
    master at the project root is never touched by ``_update_imap_config``.

    Also isolates skip_domains and subject_exclusion_list state files so
    production state on the host project root cannot leak into engine
    behavior during tests.
    """
    import imap_engine

    master_path = tmp_path / "imap_success.json"
    monkeypatch.setattr(imap_engine, "MASTER_IMAP_SUCCESS_PATH", str(master_path))
    monkeypatch.setattr(
        imap_engine, "MASTER_IMAP_SUCCESS_LOCK_PATH", str(master_path) + ".lock"
    )
    skip_path = tmp_path / "skip_domains.json"
    monkeypatch.setattr(imap_engine, "SKIP_DOMAINS_PATH", str(skip_path))
    monkeypatch.setattr(
        imap_engine, "SKIP_DOMAINS_LOCK_PATH", str(skip_path) + ".lock"
    )
    excl_path = tmp_path / "subject_exclusion_list.json"
    monkeypatch.setattr(imap_engine, "SUBJECT_EXCLUSION_PATH", str(excl_path))
    monkeypatch.setattr(
        imap_engine, "SUBJECT_EXCLUSION_LOCK_PATH", str(excl_path) + ".lock"
    )


def _force_known_domain(monkeypatch):
    """Force example.com into DEFAULT_IMAP_CONFIG so ``_worker`` skips the
    ``_try_imap_variants`` auto-discovery branch."""
    import imap_engine

    monkeypatch.setattr(
        imap_engine,
        "DEFAULT_IMAP_CONFIG",
        {"example.com": {"server": "imap.example.com", "port": 993}},
    )


def _make_mock_imap_factory(mock_conn):
    """Return a factory matching ``imaplib.IMAP4_SSL(server, port, timeout=...)``
    that ignores all arguments and returns ``mock_conn``."""

    def factory(*args, **kwargs):
        return mock_conn

    return factory


def _build_search_side_effect(from_response, subject_response):
    """Route ``mail_conn.search(charset, criteria)`` calls by substring match
    on ``criteria`` to the right ``("OK", [<bytes>])`` tuple."""

    def side_effect(charset, criteria):
        if 'FROM "' in criteria:
            return from_response
        if 'SUBJECT "' in criteria:
            return subject_response
        return ("OK", [b""])

    return side_effect


def _build_fetch_return(uid: bytes, rfc822_bytes: bytes):
    """Build the imaplib ``fetch`` return value tuple shape:
    ``("OK", [(b"<uid> (RFC822 {N}", rfc822_bytes), b")"])``.
    The ``_worker`` loop iterates ``msg_data`` and only processes elements
    that are tuples — taking ``part[1]`` as the raw RFC822 bytes."""
    header = uid + b" (RFC822 {%d}" % len(rfc822_bytes)
    return ("OK", [(header, rfc822_bytes), b")"])


def _run_worker_for_account(checker, account):
    """Drain a single account through ``_worker`` synchronously."""
    q = Queue()
    q.put(account)
    checker._worker(q)


# ---------------------------------------------------------------------------
# Test 1 — Case A: keyword-only drop. MUST FAIL on unfixed code.
# ---------------------------------------------------------------------------
def test_case_a_keyword_only_subject_match_must_be_kept(tmp_path, monkeypatch):
    """target_senders=[], keywords=["confirmation"]: a SUBJECT-matched, non-OTP
    email MUST be appended to ``emails_data`` and surfaced via ``_write_live``.

    On UNFIXED code the post-fetch gate ``if sender_matches(from_addr, [])``
    evaluates False (because ``sender_matches(_, [])`` always returns False —
    its ``for target in target_senders:`` loop never executes). The email is
    dropped, ``emails_data`` is empty, and ``_write_noemail`` is invoked
    instead of ``_write_live``. This test's assertions therefore FAIL on
    unfixed code — and that failure is the deliverable, confirming Case A
    from ``bugfix.md`` §"Counterexample" / §1.1 / §1.2 / §"Bug Condition".

    On FIXED code the three-arm OR's first arm (``not self.target_senders``)
    short-circuits to True, the email is kept, and the assertions pass.
    """
    import imap_engine
    from imap_engine import ImapChecker

    _isolate_master(tmp_path, monkeypatch)
    _force_known_domain(monkeypatch)

    rfc822 = _build_rfc822(
        from_header="reservations@hotelchain.com",
        subject="Booking confirmation #12345 \u2014 see you in Paris",
        date_header="Sat, 04 Jan 2025 10:00:00 +0000",
    )
    fetch_return = _build_fetch_return(b"42", rfc822)

    mock_conn = MagicMock()
    mock_conn.search.side_effect = _build_search_side_effect(
        from_response=("OK", [b""]),
        subject_response=("OK", [b"42"]),
    )
    mock_conn.fetch.return_value = fetch_return

    monkeypatch.setattr(
        imap_engine.imaplib, "IMAP4_SSL", _make_mock_imap_factory(mock_conn)
    )

    results_dir = tmp_path / "jobs" / "explore_a"
    results_dir.mkdir(parents=True, exist_ok=True)

    account = {"email": "alice@example.com", "password": "hunter2"}
    checker = ImapChecker(
        job_id="explore_a",
        results_dir=str(results_dir),
        accounts=[account],
        target_senders=[],
        keywords=["confirmation"],
        search_days=7,
    )

    with patch.object(ImapChecker, "_write_live", autospec=True) as spy_live, \
         patch.object(ImapChecker, "_write_noemail", autospec=True) as spy_noemail, \
         patch.object(ImapChecker, "_update_progress", autospec=True) as spy_progress:
        _run_worker_for_account(checker, account)

    live_calls = spy_live.call_args_list
    noemail_calls = spy_noemail.call_args_list
    progress_calls = [c.args[1] for c in spy_progress.call_args_list]

    detail = "\n".join([
        "Case A counterexample: sender_matches(from_addr, []) returned False, "
        "dropping a SUBJECT-matched non-OTP email",
        "  account              : alice@example.com:hunter2",
        "  target_senders       : []",
        "  keywords             : ['confirmation']",
        "  from_addr            : reservations@hotelchain.com",
        "  subject              : Booking confirmation #12345 \u2014 see you in Paris",
        "  UID                  : b'42' (collected via SUBJECT branch)",
        f"  _write_live calls    : {len(live_calls)}",
        f"  _write_noemail calls : {len(noemail_calls)}",
        f"  progress categories  : {progress_calls}",
    ])

    assert len(live_calls) == 1, (
        "BUG Case A — _write_live MUST be called exactly once.\n" + detail
    )

    # autospec=True puts ``self`` first → args = (self, email_addr, password, emails_found)
    _, email_addr_arg, password_arg, emails_found_arg = live_calls[0].args
    assert email_addr_arg == "alice@example.com", (
        f"BUG Case A — wrong email_addr passed to _write_live: {email_addr_arg!r}\n"
        + detail
    )
    assert password_arg == "hunter2", (
        f"BUG Case A — wrong password passed to _write_live: {password_arg!r}\n"
        + detail
    )
    assert len(emails_found_arg) == 1, (
        f"BUG Case A — emails_found must contain exactly 1 entry, got "
        f"{len(emails_found_arg)}: {emails_found_arg!r}\n" + detail
    )
    assert (
        emails_found_arg[0]["subject"]
        == "Booking confirmation #12345 \u2014 see you in Paris"
    ), (
        f"BUG Case A — wrong subject in emails_found: "
        f"{emails_found_arg[0]['subject']!r}\n" + detail
    )
    assert len(noemail_calls) == 0, (
        "BUG Case A — _write_noemail must NOT be called when a SUBJECT-matched "
        "non-OTP email exists.\n" + detail
    )


# ---------------------------------------------------------------------------
# Test 2 — Case B: both-filled, SUBJECT-only drop. MUST FAIL on unfixed code.
# ---------------------------------------------------------------------------
def test_case_b_both_filled_subject_only_match_must_be_kept(tmp_path, monkeypatch):
    """target_senders=["@booking.com"], keywords=["invoice"]: an email whose UID
    is in ``email_ids`` solely via the SUBJECT branch, and whose ``from_addr``
    does NOT satisfy ``sender_matches``, MUST still be kept.

    On UNFIXED code the post-fetch gate evaluates
    ``sender_matches("billing@stripe.com", ["@booking.com"])`` → False. The
    UID ``b"77"`` is dropped despite being in ``email_ids`` via the SUBJECT
    branch — contradicting the union semantics that SEARCH stage already
    promised. This test's assertions therefore FAIL on unfixed code, and that
    failure is the deliverable confirming Case B from ``bugfix.md``
    §"Counterexample" / §1.3 / §"Bug Condition".

    On FIXED code the three-arm OR's third arm
    (``eid_bytes in subject_matched_ids``) is True, so the email is kept.
    """
    import imap_engine
    from imap_engine import ImapChecker

    _isolate_master(tmp_path, monkeypatch)
    _force_known_domain(monkeypatch)

    rfc822 = _build_rfc822(
        from_header="billing@stripe.com",
        subject="Your invoice #998",
        date_header="Tue, 14 Jan 2025 09:00:00 +0000",
    )
    fetch_return = _build_fetch_return(b"77", rfc822)

    mock_conn = MagicMock()
    mock_conn.search.side_effect = _build_search_side_effect(
        from_response=("OK", [b""]),
        subject_response=("OK", [b"77"]),
    )
    mock_conn.fetch.return_value = fetch_return

    monkeypatch.setattr(
        imap_engine.imaplib, "IMAP4_SSL", _make_mock_imap_factory(mock_conn)
    )

    results_dir = tmp_path / "jobs" / "explore_b"
    results_dir.mkdir(parents=True, exist_ok=True)

    account = {"email": "alice@example.com", "password": "hunter2"}
    checker = ImapChecker(
        job_id="explore_b",
        results_dir=str(results_dir),
        accounts=[account],
        target_senders=["@booking.com"],
        keywords=["invoice"],
        search_days=7,
    )

    with patch.object(ImapChecker, "_write_live", autospec=True) as spy_live, \
         patch.object(ImapChecker, "_write_noemail", autospec=True) as spy_noemail, \
         patch.object(ImapChecker, "_update_progress", autospec=True) as spy_progress:
        _run_worker_for_account(checker, account)

    live_calls = spy_live.call_args_list
    noemail_calls = spy_noemail.call_args_list
    progress_calls = [c.args[1] for c in spy_progress.call_args_list]

    detail = "\n".join([
        "Case B counterexample: SUBJECT-branch UID b'77' dropped because "
        "single-arm gate ignored email_ids union semantics",
        "  account              : alice@example.com:hunter2",
        "  target_senders       : ['@booking.com']",
        "  keywords             : ['invoice']",
        "  from_addr            : billing@stripe.com",
        "  subject              : Your invoice #998",
        "  UID                  : b'77' (in SUBJECT branch only — FROM SEARCH returned empty)",
        f"  _write_live calls    : {len(live_calls)}",
        f"  _write_noemail calls : {len(noemail_calls)}",
        f"  progress categories  : {progress_calls}",
    ])

    assert len(live_calls) == 1, (
        "BUG Case B — _write_live MUST be called exactly once.\n" + detail
    )

    _, email_addr_arg, password_arg, emails_found_arg = live_calls[0].args
    assert email_addr_arg == "alice@example.com", (
        f"BUG Case B — wrong email_addr passed to _write_live: {email_addr_arg!r}\n"
        + detail
    )
    assert password_arg == "hunter2", (
        f"BUG Case B — wrong password passed to _write_live: {password_arg!r}\n"
        + detail
    )
    assert len(emails_found_arg) == 1, (
        f"BUG Case B — emails_found must contain exactly 1 entry, got "
        f"{len(emails_found_arg)}: {emails_found_arg!r}\n" + detail
    )
    assert emails_found_arg[0]["subject"] == "Your invoice #998", (
        f"BUG Case B — wrong subject in emails_found: "
        f"{emails_found_arg[0]['subject']!r}\n" + detail
    )
    assert len(noemail_calls) == 0, (
        "BUG Case B — _write_noemail must NOT be called when a SUBJECT-branch "
        "UID resolves to a non-OTP email.\n" + detail
    )


# ---------------------------------------------------------------------------
# Test 3 — OTP regex remains the outermost gate. PASSES on unfixed AND fixed.
# ---------------------------------------------------------------------------
def test_otp_subject_regex_remains_outermost_gate(tmp_path, monkeypatch):
    """An email whose decoded ``Subject`` matches ``OTP_SUBJECT_REGEX`` MUST be
    dropped at the outermost gate — before any post-fetch sender/subject logic
    runs. Confirms Requirement 3.2 / ``bugfix.md`` §3.2 / ``design.md``
    §"Bug Details > Examples > OTP-filtered".

    Inputs: ``target_senders=[]``, ``keywords=["Booking.com"]``. SUBJECT search
    returns UID ``b"99"``; FETCH returns email with subject
    ``Booking.com \u2013 ABC123 is your verification code`` (en-dash matches
    the compiled OTP regex literal).

    Expected on BOTH unfixed and fixed code:
      - The OTP regex match at the outermost gate fires before the post-fetch
        sender/subject logic, so the email is excluded from ``emails_data``.
      - ``emails_data`` is therefore empty for the account.
      - ``_write_live`` is NOT called.
      - ``_write_noemail`` IS called exactly once (the empty-``emails_data``
        ``else`` branch).

    This is the sanity gate for the bugfix workflow: it must remain green
    across the unfixed → fixed transition to prove the fix did not relocate
    the OTP filter.
    """
    import imap_engine
    from imap_engine import ImapChecker

    _isolate_master(tmp_path, monkeypatch)
    _force_known_domain(monkeypatch)

    rfc822 = _build_rfc822(
        from_header="noreply@booking.com",
        subject="Booking.com \u2013 ABC123 is your verification code",
        date_header="Wed, 22 Jan 2025 12:00:00 +0000",
    )
    fetch_return = _build_fetch_return(b"99", rfc822)

    mock_conn = MagicMock()
    mock_conn.search.side_effect = _build_search_side_effect(
        from_response=("OK", [b""]),
        subject_response=("OK", [b"99"]),
    )
    mock_conn.fetch.return_value = fetch_return

    monkeypatch.setattr(
        imap_engine.imaplib, "IMAP4_SSL", _make_mock_imap_factory(mock_conn)
    )

    results_dir = tmp_path / "jobs" / "explore_otp"
    results_dir.mkdir(parents=True, exist_ok=True)

    account = {"email": "alice@example.com", "password": "hunter2"}
    checker = ImapChecker(
        job_id="explore_otp",
        results_dir=str(results_dir),
        accounts=[account],
        target_senders=[],
        keywords=["Booking.com"],
        search_days=7,
    )

    with patch.object(ImapChecker, "_write_live", autospec=True) as spy_live, \
         patch.object(ImapChecker, "_write_noemail", autospec=True) as spy_noemail, \
         patch.object(ImapChecker, "_update_progress", autospec=True) as spy_progress:
        _run_worker_for_account(checker, account)

    live_calls = spy_live.call_args_list
    noemail_calls = spy_noemail.call_args_list
    progress_calls = [c.args[1] for c in spy_progress.call_args_list]

    assert len(live_calls) == 0, (
        "OTP sanity — _write_live must NOT be called when the only fetched "
        "email matches OTP_SUBJECT_REGEX.\n"
        f"  _write_live call count: {len(live_calls)}\n"
        f"  _write_live call args : {live_calls!r}"
    )
    assert len(noemail_calls) == 1, (
        "OTP sanity — _write_noemail must be called exactly once "
        "(emails_data is empty after the OTP filter).\n"
        f"  _write_noemail call count: {len(noemail_calls)}\n"
        f"  progress categories      : {progress_calls}"
    )
