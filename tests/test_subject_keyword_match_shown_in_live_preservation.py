"""
Preservation property tests for the spec ``subject-keyword-match-shown-in-live``.

Encodes Property 2 from ``design.md`` §"Correctness Properties" — for every
input outside ``isBugCondition``, the FIXED ``_worker`` MUST behave identically
to the original. All seven tests MUST PASS on the fixed code.

Coverage map (one test per Requirement 3.x):

1. ``test_sender_only_byte_identical_output``           — Req 3.1
2. ``test_otp_subject_regex_outermost_across_regimes``  — Req 3.2
3. ``test_both_empty_issues_zero_search_calls``         — Req 3.3
4. ``test_email_ids_union_invariance``                  — Req 3.4
5. ``test_write_live_rendering_preservation``           — Req 3.5
6. ``test_master_imap_success_json_untouched``          — Req 3.6
7. ``test_api_surface_and_per_job_file_names_unchanged``— Req 3.7, 3.8

Helpers (``_isolate_master``, ``_force_known_domain``, ``_make_mock_imap_factory``,
``_build_search_side_effect``, ``_build_fetch_return``, ``_build_rfc822``,
``_run_worker_for_account``) are defined locally — re-implemented from the
companion bug exploration suite at
``tests/test_subject_keyword_match_shown_in_live_bug.py`` so the two suites
remain decoupled and can be reasoned about in isolation.

All tests monkey-patch ``imap_engine.MASTER_IMAP_SUCCESS_PATH`` and
``imap_engine.MASTER_IMAP_SUCCESS_LOCK_PATH`` to ``tmp_path``-scoped paths so
the real master ``imap_success.json`` at the project root is NEVER touched.

Validates: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8
"""
from __future__ import annotations

import os
import re
import sys
import builtins
from email.header import Header
from email.message import Message
from queue import Queue
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given, settings, HealthCheck, strategies as st

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import imap_engine  # noqa: E402  (after sys.path setup)
from imap_engine import (  # noqa: E402
    ImapChecker,
    _subject_excluded,
    sender_matches,
)


# ---------------------------------------------------------------------------
# Local helpers (re-implemented from the bug suite for isolation)
# ---------------------------------------------------------------------------
def _build_rfc822(from_header: str, subject: str, date_header: str) -> bytes:
    msg = Message()
    msg["From"] = from_header
    msg["Subject"] = str(Header(subject, "utf-8"))
    msg["Date"] = date_header
    msg.set_payload("Body of message\r\n")
    return msg.as_bytes()


def _isolate_master(tmp_path, monkeypatch):
    master_path = tmp_path / "imap_success.json"
    monkeypatch.setattr(imap_engine, "MASTER_IMAP_SUCCESS_PATH", str(master_path))
    monkeypatch.setattr(
        imap_engine, "MASTER_IMAP_SUCCESS_LOCK_PATH", str(master_path) + ".lock"
    )
    # Also isolate skip_domains and subject_exclusion_list state files so
    # production state on the host project root cannot leak into engine
    # behavior during tests (added when subject-exclusion-list spec shipped).
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


def _force_known_domain(monkeypatch, domain="example.com"):
    monkeypatch.setattr(
        imap_engine,
        "DEFAULT_IMAP_CONFIG",
        {domain: {"server": f"imap.{domain}", "port": 993}},
    )


def _make_mock_imap_factory(mock_conn):
    def factory(*args, **kwargs):
        return mock_conn

    return factory


def _build_search_side_effect(from_response, subject_response):
    """Route ``mail_conn.search(charset, criteria)`` calls by criteria substring.

    ``from_response`` and ``subject_response`` may also be callables receiving
    the raw criteria string and returning ``("OK", [<bytes>])``.
    """

    def side_effect(charset, criteria):
        if 'FROM "' in criteria:
            return from_response(criteria) if callable(from_response) else from_response
        if 'SUBJECT "' in criteria:
            return subject_response(criteria) if callable(subject_response) else subject_response
        return ("OK", [b""])

    return side_effect


def _build_fetch_return(uid: bytes, rfc822_bytes: bytes):
    header = uid + b" (RFC822 {%d}" % len(rfc822_bytes)
    return ("OK", [(header, rfc822_bytes), b")"])


def _run_worker_for_account(checker, account):
    q = Queue()
    q.put(account)
    checker._worker(q)


def _no_op_master(monkeypatch):
    """Replace ``_atomic_update_master`` with a no-op so per-Hypothesis-example
    runs don't contend on the file lock and don't fan out disk writes.

    Tests that specifically observe ``_atomic_update_master`` (Test 6) override
    this with their own spy.
    """
    monkeypatch.setattr(
        imap_engine, "_atomic_update_master", lambda updater_fn: False
    )


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------
# Domain-side characters: ASCII letters/digits + a few separators commonly seen
# in From headers. Avoid characters that would break the email module's
# parseaddr / decode_header round-trip (control chars, quoted strings, etc.)
# and avoid the en-dash so non-OTP subjects don't accidentally match
# OTP_SUBJECT_REGEX.
_LOCAL_PART = st.text(
    alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Nd"),
        whitelist_characters="._-+",
    ),
    min_size=1,
    max_size=12,
)
_DOMAIN_PART = st.text(
    alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Nd"),
        whitelist_characters=".-",
    ),
    min_size=3,
    max_size=20,
)


@st.composite
def _from_addr(draw):
    return f"{draw(_LOCAL_PART)}@{draw(_DOMAIN_PART)}.test"


# Subjects MUST NOT match OTP_SUBJECT_REGEX. The pattern is
# ``^Booking\.com – \w+ is your verification code$`` (en-dash). Generating
# without the literal ``Booking.com – `` prefix is sufficient.
_NON_OTP_SUBJECT = st.text(
    alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Nd"),
        whitelist_characters=" .-_#",
    ),
    min_size=1,
    max_size=40,
).filter(lambda s: not _subject_excluded(s, ["is your verification code"]))


# Target sender entries: domain pattern (``@foo.test``) or full email match.
_TARGET_DOMAIN = st.text(
    alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters=".-"),
    min_size=3,
    max_size=15,
).map(lambda d: f"@{d}.test")


def _build_email_record(uid_int: int, from_addr: str, subject: str):
    """Convenience builder: returns ``(uid_bytes, from_addr, subject, rfc822_bytes)``."""
    uid_bytes = str(uid_int).encode("ascii")
    rfc822 = _build_rfc822(
        from_header=from_addr,
        subject=subject,
        date_header="Sat, 04 Jan 2025 10:00:00 +0000",
    )
    return uid_bytes, from_addr, subject, rfc822


# ---------------------------------------------------------------------------
# Test 1 — Sender-only byte-identical output (Requirement 3.1)
# ---------------------------------------------------------------------------
@given(
    target_senders=st.lists(_TARGET_DOMAIN, min_size=1, max_size=3, unique=True),
    emails=st.lists(
        st.tuples(
            st.integers(min_value=1, max_value=999),
            _from_addr(),
            _NON_OTP_SUBJECT,
        ),
        min_size=1,
        max_size=10,
        unique_by=lambda t: t[0],
    ),
)
@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_sender_only_byte_identical_output(tmp_path_factory, target_senders, emails):
    """Sender-only callers (``keywords=[]``) MUST produce ``emails_data``
    byte-identical to what the pre-fix single-arm gate would have produced.

    The fixed three-arm OR collapses to ``sender_matches(...)`` in this
    regime because ``target_senders`` is non-empty AND ``subject_matched_ids``
    is empty (the SUBJECT loop is zero-iteration for ``keywords=[]``). So the
    expected ``emails_data`` is exactly the subset of generated emails whose
    ``from_addr`` satisfies ``sender_matches(from_addr, target_senders)``.

    Validates: Requirement 3.1
    """
    tmp_path = tmp_path_factory.mktemp("sender_only_byte_identical")

    saved_master = imap_engine.MASTER_IMAP_SUCCESS_PATH
    saved_lock = imap_engine.MASTER_IMAP_SUCCESS_LOCK_PATH
    saved_default = imap_engine.DEFAULT_IMAP_CONFIG
    saved_atomic = imap_engine._atomic_update_master
    try:
        imap_engine.MASTER_IMAP_SUCCESS_PATH = str(tmp_path / "imap_success.json")
        imap_engine.MASTER_IMAP_SUCCESS_LOCK_PATH = str(tmp_path / "imap_success.json.lock")
        imap_engine.DEFAULT_IMAP_CONFIG = {
            "example.com": {"server": "imap.example.com", "port": 993}
        }
        imap_engine._atomic_update_master = lambda updater_fn: False

        # Build per-UID RFC822 lookup so fetch can answer for any UID.
        records = {
            str(uid).encode("ascii"): _build_rfc822(from_addr, subject,
                                                   "Sat, 04 Jan 2025 10:00:00 +0000")
            for uid, from_addr, subject in emails
        }
        all_uids_bytes = b" ".join(records.keys())

        mock_conn = MagicMock()
        # FROM search: return every UID. The post-fetch gate is what filters
        # by from_addr — that is the contract under test.
        mock_conn.search.side_effect = _build_search_side_effect(
            from_response=("OK", [all_uids_bytes]),
            subject_response=("OK", [b""]),
        )

        def fetch_side_effect(uid_str, _spec):
            uid_bytes = uid_str.encode("ascii") if isinstance(uid_str, str) else uid_str
            payload = records[uid_bytes]
            return _build_fetch_return(uid_bytes, payload)

        mock_conn.fetch.side_effect = fetch_side_effect

        # Patch IMAP4_SSL on the module's imaplib reference.
        original_ssl = imap_engine.imaplib.IMAP4_SSL
        imap_engine.imaplib.IMAP4_SSL = _make_mock_imap_factory(mock_conn)

        results_dir = tmp_path / "jobs" / "sender_only"
        results_dir.mkdir(parents=True, exist_ok=True)

        account = {"email": "alice@example.com", "password": "hunter2"}
        checker = ImapChecker(
            job_id="sender_only",
            results_dir=str(results_dir),
            accounts=[account],
            target_senders=list(target_senders),
            keywords=[],
            search_days=7,
        )

        try:
            with patch.object(ImapChecker, "_write_live", autospec=True) as spy_live, \
                 patch.object(ImapChecker, "_write_noemail", autospec=True) as spy_noemail:
                _run_worker_for_account(checker, account)
        finally:
            imap_engine.imaplib.IMAP4_SSL = original_ssl

        # Compute the pre-fix expected emails_data: exactly those generated
        # emails whose from_addr satisfies sender_matches(target_senders).
        # _worker only fetches the most-recent 20 by integer UID, so apply
        # the same cap to the expected list to mirror sorted_ids slicing.
        sorted_uids = sorted(records.keys(), key=lambda b: int(b))[-20:]
        sorted_uids.reverse()  # newest first, matches _worker
        expected_emails_data = []
        for uid_bytes in sorted_uids:
            uid_int = int(uid_bytes)
            from_addr_for_uid = next(fa for u, fa, _ in emails if u == uid_int)
            subj_for_uid = next(s for u, _, s in emails if u == uid_int)
            if _subject_excluded(subj_for_uid, ["is your verification code"]):
                continue
            if sender_matches(from_addr_for_uid, list(target_senders)):
                expected_emails_data.append(
                    {
                        "from": from_addr_for_uid,
                        "subject": subj_for_uid,
                    }
                )

        if expected_emails_data:
            assert spy_live.call_count == 1, (
                f"sender-only regime: expected exactly one _write_live call, "
                f"got {spy_live.call_count}; target_senders={target_senders!r}, "
                f"emails={emails!r}"
            )
            _, _, _, captured_emails = spy_live.call_args.args
            # Compare projection (from + subject) — date is set from the
            # generated header and is identical across both sides anyway.
            captured_proj = [
                {"from": e["from"], "subject": e["subject"]} for e in captured_emails
            ]
            assert captured_proj == expected_emails_data, (
                f"sender-only emails_data diverged from pre-fix expectation:\n"
                f"  target_senders : {target_senders!r}\n"
                f"  expected       : {expected_emails_data!r}\n"
                f"  got            : {captured_proj!r}"
            )
            assert spy_noemail.call_count == 0, (
                f"sender-only regime: _write_noemail must NOT be called when "
                f"emails_data is non-empty; got {spy_noemail.call_count} calls"
            )
        else:
            assert spy_live.call_count == 0, (
                "sender-only regime: _write_live must NOT be called when no "
                "generated email satisfies sender_matches"
            )
            assert spy_noemail.call_count == 1, (
                f"sender-only regime: _write_noemail must be called exactly once "
                f"when emails_data is empty; got {spy_noemail.call_count} calls"
            )
    finally:
        imap_engine.MASTER_IMAP_SUCCESS_PATH = saved_master
        imap_engine.MASTER_IMAP_SUCCESS_LOCK_PATH = saved_lock
        imap_engine.DEFAULT_IMAP_CONFIG = saved_default
        imap_engine._atomic_update_master = saved_atomic


# ---------------------------------------------------------------------------
# Test 2 — OTP regex stays outermost across all four regimes (Req 3.2)
# ---------------------------------------------------------------------------
_OTP_TOKEN = st.text(
    alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd")),
    min_size=1,
    max_size=10,
)


@given(
    regime=st.sampled_from(["sender_only", "keyword_only", "both_filled", "both_empty"]),
    token=_OTP_TOKEN,
    from_local=_LOCAL_PART,
)
@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_otp_subject_regex_outermost_across_regimes(
    tmp_path_factory, regime, token, from_local
):
    """An email whose decoded ``Subject`` matches ``OTP_SUBJECT_REGEX`` MUST
    be excluded from ``emails_data`` regardless of which regime is active.

    Confirms the OTP gate at ``imap_engine.py``:627
    (``if not OTP_SUBJECT_REGEX.match(subject.strip()):``) fires BEFORE the
    new three-arm OR for every (``target_senders``, ``keywords``) combination.

    Validates: Requirement 3.2
    """
    tmp_path = tmp_path_factory.mktemp(f"otp_outermost_{regime}")

    saved_master = imap_engine.MASTER_IMAP_SUCCESS_PATH
    saved_lock = imap_engine.MASTER_IMAP_SUCCESS_LOCK_PATH
    saved_default = imap_engine.DEFAULT_IMAP_CONFIG
    saved_atomic = imap_engine._atomic_update_master
    try:
        imap_engine.MASTER_IMAP_SUCCESS_PATH = str(tmp_path / "imap_success.json")
        imap_engine.MASTER_IMAP_SUCCESS_LOCK_PATH = str(tmp_path / "imap_success.json.lock")
        imap_engine.DEFAULT_IMAP_CONFIG = {
            "example.com": {"server": "imap.example.com", "port": 993}
        }
        imap_engine._atomic_update_master = lambda updater_fn: False

        if regime == "sender_only":
            target_senders = ["@booking.com"]
            keywords = []
        elif regime == "keyword_only":
            target_senders = []
            keywords = ["verification"]
        elif regime == "both_filled":
            target_senders = ["@booking.com"]
            keywords = ["verification"]
        else:  # both_empty
            target_senders = []
            keywords = []

        # OTP-matching subject (en-dash U+2013 — must be the literal that the
        # compiled regex expects).
        otp_subject = f"Booking.com \u2013 {token} is your verification code"
        # Sanity precondition: the generated subject MUST match OTP regex.
        # If the strategy happens to emit a token that breaks \w+ matching,
        # skip the example.
        if not _subject_excluded(otp_subject, ["is your verification code"]):
            return  # generator gave us a token outside \w+ (shouldn't happen)

        from_addr = f"{from_local}@booking.com"
        rfc822 = _build_rfc822(
            from_header=from_addr,
            subject=otp_subject,
            date_header="Wed, 22 Jan 2025 12:00:00 +0000",
        )
        fetch_return = _build_fetch_return(b"99", rfc822)

        mock_conn = MagicMock()
        # Both branches return the same UID so any non-empty regime fetches it.
        mock_conn.search.side_effect = _build_search_side_effect(
            from_response=("OK", [b"99"]),
            subject_response=("OK", [b"99"]),
        )
        mock_conn.fetch.return_value = fetch_return

        original_ssl = imap_engine.imaplib.IMAP4_SSL
        imap_engine.imaplib.IMAP4_SSL = _make_mock_imap_factory(mock_conn)

        results_dir = tmp_path / "jobs" / f"otp_{regime}"
        results_dir.mkdir(parents=True, exist_ok=True)

        account = {"email": "alice@example.com", "password": "hunter2"}
        checker = ImapChecker(
            job_id=f"otp_{regime}",
            results_dir=str(results_dir),
            accounts=[account],
            target_senders=target_senders,
            keywords=keywords,
            search_days=7,
        )

        try:
            with patch.object(ImapChecker, "_write_live", autospec=True) as spy_live, \
                 patch.object(ImapChecker, "_write_noemail", autospec=True) as spy_noemail:
                _run_worker_for_account(checker, account)
        finally:
            imap_engine.imaplib.IMAP4_SSL = original_ssl

        # OTP must be excluded — _write_live must not see this OTP email.
        for call in spy_live.call_args_list:
            _, _, _, captured = call.args
            for entry in captured:
                assert entry["subject"] != otp_subject, (
                    f"OTP regime={regime!r}: OTP-matching email leaked into "
                    f"emails_data despite outermost gate.\n"
                    f"  target_senders : {target_senders!r}\n"
                    f"  keywords       : {keywords!r}\n"
                    f"  subject        : {otp_subject!r}\n"
                )

        # In all four regimes, the only fetched email is OTP-filtered, so
        # emails_data ends empty → _write_noemail is the surviving call.
        # Exception: both_empty regime never reaches FETCH at all, so
        # emails_data is also empty there.
        assert spy_live.call_count == 0, (
            f"OTP regime={regime!r}: _write_live unexpectedly called "
            f"{spy_live.call_count}x; the only email is OTP-filtered."
        )
    finally:
        imap_engine.MASTER_IMAP_SUCCESS_PATH = saved_master
        imap_engine.MASTER_IMAP_SUCCESS_LOCK_PATH = saved_lock
        imap_engine.DEFAULT_IMAP_CONFIG = saved_default
        imap_engine._atomic_update_master = saved_atomic


# ---------------------------------------------------------------------------
# Test 3 — Both-empty regime issues zero SEARCH calls (Req 3.3)
# ---------------------------------------------------------------------------
def test_both_empty_issues_zero_search_calls(tmp_path, monkeypatch):
    """``target_senders=[] AND keywords=[]`` MUST produce zero ``mail_conn.search``
    invocations, exactly one ``_write_noemail`` call, exactly one
    ``_update_progress("noemail")`` increment, and zero ``_write_live`` /
    ``_update_progress("live")`` calls.

    Validates: Requirement 3.3
    """
    _isolate_master(tmp_path, monkeypatch)
    _force_known_domain(monkeypatch)
    _no_op_master(monkeypatch)

    mock_conn = MagicMock()
    # If anything calls search/fetch, default returns are inert.
    mock_conn.search.return_value = ("OK", [b""])
    monkeypatch.setattr(
        imap_engine.imaplib, "IMAP4_SSL", _make_mock_imap_factory(mock_conn)
    )

    results_dir = tmp_path / "jobs" / "both_empty"
    results_dir.mkdir(parents=True, exist_ok=True)

    account = {"email": "alice@example.com", "password": "hunter2"}
    checker = ImapChecker(
        job_id="both_empty",
        results_dir=str(results_dir),
        accounts=[account],
        target_senders=[],
        keywords=[],
        search_days=7,
    )

    with patch.object(ImapChecker, "_write_live", autospec=True) as spy_live, \
         patch.object(ImapChecker, "_write_noemail", autospec=True) as spy_noemail, \
         patch.object(ImapChecker, "_update_progress", autospec=True) as spy_progress:
        _run_worker_for_account(checker, account)

    assert mock_conn.search.call_count == 0, (
        f"both-empty regime: mail_conn.search must NEVER be called; "
        f"got {mock_conn.search.call_count} call(s): "
        f"{mock_conn.search.call_args_list!r}"
    )
    assert spy_live.call_count == 0, (
        f"both-empty regime: _write_live must NEVER be called; "
        f"got {spy_live.call_count} call(s)"
    )
    assert spy_noemail.call_count == 1, (
        f"both-empty regime: _write_noemail must be called exactly once; "
        f"got {spy_noemail.call_count} call(s)"
    )

    progress_categories = [c.args[1] for c in spy_progress.call_args_list]
    assert progress_categories.count("noemail") == 1, (
        f"both-empty regime: _update_progress('noemail') must be called "
        f"exactly once; got categories {progress_categories!r}"
    )
    assert progress_categories.count("live") == 0, (
        f"both-empty regime: _update_progress('live') must NEVER be called; "
        f"got categories {progress_categories!r}"
    )


# ---------------------------------------------------------------------------
# Test 4 — email_ids union invariance (Req 3.4)
# ---------------------------------------------------------------------------
@st.composite
def _union_inputs(draw):
    """Generate (target_senders, keywords, from_uids, subject_uids) with the
    invariant that |from_uids ∪ subject_uids| ≤ 20 so the worker's
    ``sorted_ids[-20:]`` slicing covers the full ``email_ids`` set — making
    it possible to capture ``email_ids`` indirectly via the FETCH UID list.
    """
    target_senders = draw(
        st.lists(_TARGET_DOMAIN, min_size=0, max_size=2, unique=True)
    )
    keywords = draw(
        st.lists(
            st.text(
                alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd")),
                min_size=1,
                max_size=8,
            ),
            min_size=0,
            max_size=2,
            unique=True,
        )
    )
    from_uids = draw(
        st.lists(
            st.integers(min_value=1, max_value=999),
            min_size=0,
            max_size=10,
            unique=True,
        )
    )
    subject_uids = draw(
        st.lists(
            st.integers(min_value=1, max_value=999),
            min_size=0,
            max_size=10,
            unique=True,
        )
    )
    union_size = len(set(from_uids) | set(subject_uids))
    if union_size > 20:
        # discard so the slicing invariant holds; Hypothesis handles it.
        from hypothesis import assume
        assume(False)
    return target_senders, keywords, from_uids, subject_uids


@given(inputs=_union_inputs())
@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_email_ids_union_invariance(tmp_path_factory, inputs):
    """The set of UIDs the worker fetches MUST equal the union of UIDs
    returned by the FROM SEARCH calls and the SUBJECT SEARCH calls — i.e.
    Edit 2's refactor preserves the pre-fix
    ``email_ids.update(msgs[0].split())`` set-union semantics.

    Indirect observation: ``_worker`` builds ``email_ids`` then computes
    ``sorted_ids = sorted(list(email_ids), key=int)[-20:]`` and FETCHes each
    one. With ``|email_ids| ≤ 20`` (enforced by the strategy) the captured
    FETCH UID set is exactly ``email_ids``.

    Validates: Requirement 3.4
    """
    target_senders, keywords, from_uids, subject_uids = inputs
    tmp_path = tmp_path_factory.mktemp("email_ids_union")

    saved_master = imap_engine.MASTER_IMAP_SUCCESS_PATH
    saved_lock = imap_engine.MASTER_IMAP_SUCCESS_LOCK_PATH
    saved_default = imap_engine.DEFAULT_IMAP_CONFIG
    saved_atomic = imap_engine._atomic_update_master
    try:
        imap_engine.MASTER_IMAP_SUCCESS_PATH = str(tmp_path / "imap_success.json")
        imap_engine.MASTER_IMAP_SUCCESS_LOCK_PATH = str(tmp_path / "imap_success.json.lock")
        imap_engine.DEFAULT_IMAP_CONFIG = {
            "example.com": {"server": "imap.example.com", "port": 993}
        }
        imap_engine._atomic_update_master = lambda updater_fn: False

        from_bytes = b" ".join(str(u).encode("ascii") for u in from_uids) or b""
        subject_bytes = b" ".join(str(u).encode("ascii") for u in subject_uids) or b""

        # Each FROM call returns the same UID list; same for SUBJECT (matches
        # the per-criterion mocking strategy already used in the bug suite).
        mock_conn = MagicMock()
        mock_conn.search.side_effect = _build_search_side_effect(
            from_response=("OK", [from_bytes]),
            subject_response=("OK", [subject_bytes]),
        )

        # FETCH returns a generic non-OTP RFC822 for any UID so the worker
        # doesn't blow up. The content is irrelevant for this test — only
        # the call list of UIDs matters.
        generic_rfc822 = _build_rfc822(
            from_header="x@example.test",
            subject="generic subject",
            date_header="Sat, 04 Jan 2025 10:00:00 +0000",
        )

        def fetch_side_effect(uid_str, _spec):
            uid_bytes = uid_str.encode("ascii") if isinstance(uid_str, str) else uid_str
            return _build_fetch_return(uid_bytes, generic_rfc822)

        mock_conn.fetch.side_effect = fetch_side_effect

        original_ssl = imap_engine.imaplib.IMAP4_SSL
        imap_engine.imaplib.IMAP4_SSL = _make_mock_imap_factory(mock_conn)

        results_dir = tmp_path / "jobs" / "email_ids_union"
        results_dir.mkdir(parents=True, exist_ok=True)

        account = {"email": "alice@example.com", "password": "hunter2"}
        checker = ImapChecker(
            job_id="email_ids_union",
            results_dir=str(results_dir),
            accounts=[account],
            target_senders=list(target_senders),
            keywords=list(keywords),
            search_days=7,
        )

        try:
            _run_worker_for_account(checker, account)
        finally:
            imap_engine.imaplib.IMAP4_SSL = original_ssl

        fetched_uids = set()
        for call in mock_conn.fetch.call_args_list:
            uid_arg = call.args[0]
            uid_int = int(uid_arg)
            fetched_uids.add(uid_int)

        # The expected email_ids set: union of FROM and SUBJECT search returns
        # — but only when the corresponding criterion list is non-empty (the
        # for-loop is otherwise zero-iteration). Empty bytes ("") do not
        # add UIDs because the engine's guard ``if status == "OK" and msgs
        # and msgs[0]:`` rejects empty msgs[0].
        expected_from = set(from_uids) if target_senders else set()
        expected_subject = set(subject_uids) if keywords else set()
        # Empty list of UIDs from a non-empty branch (the search returned
        # empty bytes for that criterion) also contributes nothing.
        if not from_bytes:
            expected_from = set()
        if not subject_bytes:
            expected_subject = set()
        expected_email_ids = expected_from | expected_subject

        assert fetched_uids == expected_email_ids, (
            f"email_ids set-union semantics diverged from expected union:\n"
            f"  target_senders : {target_senders!r}\n"
            f"  keywords       : {keywords!r}\n"
            f"  from_uids      : {sorted(from_uids)!r}\n"
            f"  subject_uids   : {sorted(subject_uids)!r}\n"
            f"  expected union : {sorted(expected_email_ids)!r}\n"
            f"  fetched UIDs   : {sorted(fetched_uids)!r}\n"
        )
    finally:
        imap_engine.MASTER_IMAP_SUCCESS_PATH = saved_master
        imap_engine.MASTER_IMAP_SUCCESS_LOCK_PATH = saved_lock
        imap_engine.DEFAULT_IMAP_CONFIG = saved_default
        imap_engine._atomic_update_master = saved_atomic


# ---------------------------------------------------------------------------
# Test 5 — _write_live rendering preservation (Req 3.5)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("regime", ["sender_only", "both_filled"])
def test_write_live_rendering_preservation(tmp_path, monkeypatch, regime):
    """``_write_live`` MUST render ``<from_addr>`` for emails whose
    ``from_addr`` satisfies ``sender_matches(from_addr, self.target_senders)``
    (line 385) and the raw ``From`` header otherwise.

    Drives ``_worker`` end-to-end across two regimes (sender-only and
    both-filled) with two seed emails per run — one whose ``from_addr``
    matches a target sender (must be rendered as ``<from_addr>``) and one
    whose ``from_addr`` does not match (must be rendered as raw).

    Validates: Requirement 3.5
    """
    _isolate_master(tmp_path, monkeypatch)
    _force_known_domain(monkeypatch)
    _no_op_master(monkeypatch)

    target_senders = ["@booking.com"]
    keywords = ["invoice"] if regime == "both_filled" else []

    matching_from_raw = "Booking Reservations <reservations@booking.com>"
    matching_from_addr = "reservations@booking.com"
    nonmatching_from_raw = "Stripe Billing <billing@stripe.com>"
    nonmatching_from_addr = "billing@stripe.com"

    rfc_match = _build_rfc822(
        from_header=matching_from_raw,
        subject="Your booking confirmation",
        date_header="Sat, 04 Jan 2025 10:00:00 +0000",
    )
    rfc_nomatch = _build_rfc822(
        from_header=nonmatching_from_raw,
        subject="Your invoice #998",
        date_header="Tue, 14 Jan 2025 09:00:00 +0000",
    )

    fetch_lookup = {b"1": rfc_match, b"2": rfc_nomatch}

    mock_conn = MagicMock()
    # In sender-only regime we drop UID 2 (since SUBJECT loop is empty there's
    # no path for it), but the gate would still drop it anyway because its
    # from_addr doesn't satisfy sender_matches. To keep the test scenario
    # parallel across both regimes, we have FROM SEARCH return only UID 1
    # (matches @booking.com) and SUBJECT SEARCH return UID 2 ("invoice").
    mock_conn.search.side_effect = _build_search_side_effect(
        from_response=("OK", [b"1"]),
        subject_response=("OK", [b"2"]),
    )

    def fetch_side_effect(uid_str, _spec):
        uid_bytes = uid_str.encode("ascii") if isinstance(uid_str, str) else uid_str
        return _build_fetch_return(uid_bytes, fetch_lookup[uid_bytes])

    mock_conn.fetch.side_effect = fetch_side_effect

    monkeypatch.setattr(
        imap_engine.imaplib, "IMAP4_SSL", _make_mock_imap_factory(mock_conn)
    )

    results_dir = tmp_path / "jobs" / f"render_{regime}"
    results_dir.mkdir(parents=True, exist_ok=True)
    live_file_path = str(results_dir / "live.txt")

    account = {"email": "alice@example.com", "password": "hunter2"}
    checker = ImapChecker(
        job_id=f"render_{regime}",
        results_dir=str(results_dir),
        accounts=[account],
        target_senders=target_senders,
        keywords=keywords,
        search_days=7,
    )

    # Capture bytes via monkeypatch on builtins.open for the live_file path.
    captured_writes = []
    real_open = builtins.open

    def spying_open(path, mode="r", *args, **kwargs):
        f = real_open(path, mode, *args, **kwargs)
        if str(path) == live_file_path and ("w" in mode or "a" in mode):
            class TeeFile:
                def __init__(self, inner):
                    self._inner = inner

                def write(self, data):
                    captured_writes.append(data)
                    return self._inner.write(data)

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb):
                    return self._inner.__exit__(exc_type, exc, tb)

                def __getattr__(self, item):
                    return getattr(self._inner, item)

            return TeeFile(f)
        return f

    monkeypatch.setattr(builtins, "open", spying_open)

    _run_worker_for_account(checker, account)

    captured_text = "".join(
        c if isinstance(c, str) else c.decode("utf-8") for c in captured_writes
    )

    if regime == "sender_only":
        # Only UID 1 (matches @booking.com) survives. Its From is rendered
        # as <reservations@booking.com>.
        assert "<reservations@booking.com>" in captured_text, (
            f"sender_only: matching email's From must render as "
            f"<from_addr>; live.txt content:\n{captured_text}"
        )
        # UID 2 was never fetched (no SUBJECT search), so its raw From must
        # NOT appear in the captured bytes.
        assert nonmatching_from_raw not in captured_text, (
            f"sender_only: non-matching email must NOT appear in live.txt; "
            f"content:\n{captured_text}"
        )
    else:
        # both_filled: UID 1 kept via sender_matches=True (renders <addr>);
        # UID 2 kept via subject_matched_ids (renders raw From).
        assert "<reservations@booking.com>" in captured_text, (
            f"both_filled: sender-matched email's From must render as "
            f"<from_addr>; live.txt content:\n{captured_text}"
        )
        assert nonmatching_from_raw in captured_text, (
            f"both_filled: subject-only email's From must render as raw "
            f"header (got escape-form?); live.txt content:\n{captured_text}"
        )
        # Distinguishing feature for raw rendering: the display name
        # ("Stripe Billing") MUST be present. The matched-form rendering
        # would drop the display name and write only ``<billing@stripe.com>``.
        # The raw From header itself contains ``<billing@stripe.com>`` as
        # the address part, so we validate the *display name* presence
        # rather than the absence of angle-bracketed addr.
        assert "Stripe Billing" in captured_text, (
            f"both_filled: subject-only email must render the raw From "
            f"header (display name 'Stripe Billing' missing — looks like "
            f"the matched-form <addr> branch fired by mistake); "
            f"content:\n{captured_text}"
        )


# ---------------------------------------------------------------------------
# Test 6 — Master imap_success.json untouched (Req 3.6)
# ---------------------------------------------------------------------------
def test_master_imap_success_json_untouched(tmp_path, monkeypatch):
    """The fix MUST NOT introduce any new call to ``_atomic_update_master``
    outside the existing ``_update_imap_config`` flow. Additionally,
    ``_escape_imap_string`` MUST be invoked exactly once per keyword in the
    SUBJECT loop (preserving the centralize-imap-success-master fix).

    Strategy:
      - Spy on ``imap_engine._atomic_update_master`` (return False to stay
        idempotent — no actual disk writes).
      - Spy on ``ImapChecker._update_imap_config`` to count its invocations.
      - Spy on ``imap_engine._escape_imap_string``.
      - Drive ``_worker`` for an account that successfully connects.
      - Assert ``_atomic_update_master.call_count <= _update_imap_config.call_count``
        (every ``_atomic_update_master`` call passes through
        ``_update_imap_config``; the converse holds because
        ``_update_imap_config`` always invokes ``_atomic_update_master``
        exactly once unless a Timeout/OSError short-circuits it).
      - Assert ``_escape_imap_string`` was called once per keyword (plus
        once per target sender — the sender SEARCH loop invokes the same
        helper, but the spec narrows assertion to the SUBJECT loop count).

    Validates: Requirement 3.6
    """
    _isolate_master(tmp_path, monkeypatch)
    _force_known_domain(monkeypatch)

    target_senders = ["@booking.com"]
    keywords = ["invoice", "receipt"]

    rfc822 = _build_rfc822(
        from_header="reservations@booking.com",
        subject="Your invoice #998",
        date_header="Sat, 04 Jan 2025 10:00:00 +0000",
    )
    fetch_return = _build_fetch_return(b"1", rfc822)

    mock_conn = MagicMock()
    mock_conn.search.side_effect = _build_search_side_effect(
        from_response=("OK", [b"1"]),
        subject_response=("OK", [b""]),
    )
    mock_conn.fetch.return_value = fetch_return

    monkeypatch.setattr(
        imap_engine.imaplib, "IMAP4_SSL", _make_mock_imap_factory(mock_conn)
    )

    # Spy on _atomic_update_master at the module level.
    atomic_spy = MagicMock(return_value=False)
    monkeypatch.setattr(imap_engine, "_atomic_update_master", atomic_spy)

    # Spy on _escape_imap_string. The engine module references it via the
    # bare name inside _worker, so patch on the imap_engine namespace.
    escape_calls = []
    real_escape = imap_engine._escape_imap_string

    def spying_escape(s):
        escape_calls.append(s)
        return real_escape(s)

    monkeypatch.setattr(imap_engine, "_escape_imap_string", spying_escape)

    results_dir = tmp_path / "jobs" / "master_untouched"
    results_dir.mkdir(parents=True, exist_ok=True)

    account = {"email": "alice@example.com", "password": "hunter2"}
    checker = ImapChecker(
        job_id="master_untouched",
        results_dir=str(results_dir),
        accounts=[account],
        target_senders=target_senders,
        keywords=keywords,
        search_days=7,
    )

    with patch.object(ImapChecker, "_update_imap_config", autospec=True) as spy_update:
        # Replicate the behavior _update_imap_config would have on the
        # spy: invoke the spied _atomic_update_master so the call-site
        # equivalence is preserved end-to-end.
        def fake_update(self, domain, config_data):
            def updater(current):
                if current.get(domain) == config_data:
                    return False
                current[domain] = config_data
                return True

            try:
                imap_engine._atomic_update_master(updater)
            except Exception:
                pass

        spy_update.side_effect = fake_update

        _run_worker_for_account(checker, account)

    # Assert all _atomic_update_master calls were funneled through
    # _update_imap_config (no NEW direct call sites introduced by the fix).
    assert atomic_spy.call_count <= spy_update.call_count, (
        f"_atomic_update_master was called {atomic_spy.call_count}x but "
        f"_update_imap_config only {spy_update.call_count}x — a NEW call "
        f"site was introduced outside _update_imap_config. "
        f"_atomic_update_master calls: {atomic_spy.call_args_list!r}"
    )

    # Assert _escape_imap_string was called once per keyword in the SUBJECT
    # loop. The FROM loop also invokes it once per target sender, so the
    # SUBJECT-loop slice equals total - len(target_senders).
    expected_subject_invocations = len(keywords)
    expected_from_invocations = len(target_senders)
    expected_total = expected_subject_invocations + expected_from_invocations
    assert len(escape_calls) == expected_total, (
        f"_escape_imap_string call count diverged from expected total: "
        f"got {len(escape_calls)}, expected {expected_total} "
        f"(= {expected_from_invocations} FROM + {expected_subject_invocations} SUBJECT). "
        f"Calls: {escape_calls!r}"
    )
    # Tail-slice the escape_calls list: FROM loop runs first, then SUBJECT.
    subject_slice = escape_calls[expected_from_invocations:]
    assert subject_slice == keywords, (
        f"_escape_imap_string SUBJECT-loop invocation order diverged: "
        f"got {subject_slice!r}, expected {keywords!r}"
    )


# ---------------------------------------------------------------------------
# Test 7 — API surface and per-job file names unchanged (Req 3.7, 3.8)
# ---------------------------------------------------------------------------
def test_api_surface_and_per_job_file_names_unchanged(tmp_path, monkeypatch):
    """The Flask app MUST still expose ``/api/check`` (POST), ``/api/jobs``
    (GET), and ``/api/status/<job_id>`` (GET) with the pre-fix JSON key
    schema. The per-job result files produced by ``_worker`` MUST be named
    exactly ``live.txt``, ``noemail.txt``, ``die.txt``, ``unreg.txt``,
    ``domain_skipped.txt`` — no new file, no rename.

    Validates: Requirements 3.7, 3.8
    """
    _isolate_master(tmp_path, monkeypatch)

    import app as app_module  # imported lazily so the test can monkeypatch first

    monkeypatch.setattr(app_module, "JOBS_DIR", str(tmp_path / "jobs"))
    os.makedirs(str(tmp_path / "jobs"), exist_ok=True)

    saved_jobs = dict(app_module.jobs)
    app_module.jobs.clear()

    # Stub ImapChecker.run so no background work happens; the job's status
    # immediately becomes 'done' so the SSE generator yields once and exits.
    def fake_run(self):
        self.status = "done"

    monkeypatch.setattr(imap_engine.ImapChecker, "run", fake_run)

    # ---- 1. Routes exist with expected methods ----
    url_map = app_module.app.url_map
    rules_by_endpoint = {
        rule.rule: {"methods": rule.methods, "endpoint": rule.endpoint}
        for rule in url_map.iter_rules()
    }

    assert "/api/check" in rules_by_endpoint, (
        f"/api/check route missing; got rules: {sorted(rules_by_endpoint)!r}"
    )
    assert "POST" in rules_by_endpoint["/api/check"]["methods"], (
        "/api/check must accept POST"
    )

    assert "/api/jobs" in rules_by_endpoint, (
        f"/api/jobs route missing; got rules: {sorted(rules_by_endpoint)!r}"
    )
    assert "GET" in rules_by_endpoint["/api/jobs"]["methods"], (
        "/api/jobs must accept GET"
    )

    assert "/api/status/<job_id>" in rules_by_endpoint, (
        f"/api/status/<job_id> route missing; got rules: "
        f"{sorted(rules_by_endpoint)!r}"
    )
    assert "GET" in rules_by_endpoint["/api/status/<job_id>"]["methods"], (
        "/api/status/<job_id> must accept GET"
    )

    try:
        client = app_module.app.test_client()
        with client.session_transaction() as sess:
            sess["authenticated"] = True
            sess["is_admin"] = True
            sess["code_hash"] = "test_hash"
            sess["user_label"] = "tester"

        # ---- 2. /api/check JSON response key schema ----
        resp = client.post(
            "/api/check",
            json={
                "accounts": "user@foo.com:pass123",
                "senders": "@booking.com",
                "keywords": "",
                "search_days": 30,
                "max_threads": 1,
            },
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        expected_check_keys = {
            "job_id", "total_accounts", "total_senders", "total_keywords",
            "duplicates_removed",
        }
        assert set(body.keys()) == expected_check_keys, (
            f"/api/check key schema diverged: got {set(body.keys())!r}, "
            f"expected {expected_check_keys!r}"
        )
        job_id = body["job_id"]

        # ---- 3. /api/jobs JSON response key schema ----
        resp = client.get("/api/jobs")
        assert resp.status_code == 200
        jobs_list = resp.get_json()
        assert isinstance(jobs_list, list) and len(jobs_list) >= 1
        entry = next(j for j in jobs_list if j["job_id"] == job_id)
        expected_job_keys = {
            "job_id", "status", "percentage", "checked", "total",
            "live", "die", "noemail", "unreg", "skipped",
            "created_at", "expires_at", "meta",
        }
        missing = expected_job_keys - set(entry.keys())
        assert not missing, (
            f"/api/jobs entry missing keys: {missing!r}; "
            f"got {set(entry.keys())!r}"
        )

        # ---- 4. /api/status/<job_id> SSE first-frame key schema ----
        resp = client.get(f"/api/status/{job_id}")
        assert resp.status_code == 200
        assert resp.mimetype == "text/event-stream"
        sse_body = resp.get_data(as_text=True)
        assert sse_body.startswith("data: "), sse_body
        first_line = sse_body.split("\n\n", 1)[0]
        import json as _json
        progress = _json.loads(first_line[len("data: "):])
        expected_progress_keys = {
            "status", "total", "checked", "percentage",
            "live", "noemail", "die", "unreg", "skipped",
            "live_accounts",
        }
        missing = expected_progress_keys - set(progress.keys())
        assert not missing, (
            f"/api/status payload missing keys: {missing!r}; "
            f"got {set(progress.keys())!r}"
        )
    finally:
        app_module.jobs.clear()
        app_module.jobs.update(saved_jobs)

    # ---- 5. Per-job file names produced by _worker are unchanged ----
    # Drive _worker for a tiny scenario and assert the EXACT file name set
    # under jobs/{job_id}/ is a subset of the canonical 5 (no new file, no
    # rename). We construct one of each outcome category.
    _force_known_domain(monkeypatch, domain="ok.test")
    monkeypatch.setattr(
        imap_engine,
        "DEFAULT_IMAP_CONFIG",
        {
            "ok.test": {"server": "imap.ok.test", "port": 993},
            "nomail.test": {"server": "imap.nomail.test", "port": 993},
            "bad.test": {"server": "imap.bad.test", "port": 993},
        },
    )
    _no_op_master(monkeypatch)

    rfc_ok = _build_rfc822(
        from_header="reservations@booking.com",
        subject="Your booking confirmation",
        date_header="Sat, 04 Jan 2025 10:00:00 +0000",
    )

    def imap_factory(host, port, *a, **k):
        if host == "imap.ok.test":
            conn = MagicMock()
            conn.search.side_effect = _build_search_side_effect(
                from_response=("OK", [b"1"]),
                subject_response=("OK", [b""]),
            )
            conn.fetch.return_value = _build_fetch_return(b"1", rfc_ok)
            return conn
        if host == "imap.nomail.test":
            conn = MagicMock()
            conn.search.return_value = ("OK", [b""])
            return conn
        if host == "imap.bad.test":
            conn = MagicMock()
            conn.login.side_effect = Exception("login failed")
            return conn
        raise Exception(f"unexpected host: {host}")

    monkeypatch.setattr(imap_engine.imaplib, "IMAP4_SSL", imap_factory)

    accounts = [
        {"email": "a@ok.test", "password": "p"},                # → live
        {"email": "b@nomail.test", "password": "p"},            # → noemail
        {"email": "c@bad.test", "password": "p"},               # → die
        {"email": "d@unknown-isp.test", "password": "p"},       # → unreg
        {"email": "e@hotmail.com", "password": "p"},            # → domain_skipped
    ]

    results_dir = tmp_path / "jobs" / "filenames_check"
    results_dir.mkdir(parents=True, exist_ok=True)

    checker = ImapChecker(
        job_id="filenames_check",
        results_dir=str(results_dir),
        accounts=accounts,
        target_senders=["@booking.com"],
        keywords=[],
        max_threads=1,
    )
    checker._try_imap_variants = lambda *a, **k: None  # force unreg for unknown

    q = Queue()
    for acc in accounts:
        q.put(acc)
    checker._worker(q)

    expected_filenames = {
        "live.txt",
        "noemail.txt",
        "die.txt",
        "unreg.txt",
        "domain_skipped.txt",
    }
    actual_filenames = {f for f in os.listdir(results_dir) if not f.startswith(".")}

    # All produced files must be members of the canonical set — no new file,
    # no rename.
    extra = actual_filenames - expected_filenames
    assert not extra, (
        f"Per-job directory contains UNEXPECTED file(s): {extra!r}. "
        f"Canonical set: {expected_filenames!r}"
    )

    # And every canonical file should be present (this scenario hits all 5
    # outcome buckets by construction).
    missing_files = expected_filenames - actual_filenames
    assert not missing_files, (
        f"Per-job directory MISSING canonical file(s): {missing_files!r}. "
        f"Found: {actual_filenames!r}"
    )
