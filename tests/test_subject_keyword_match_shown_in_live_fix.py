"""
Fix-checking property tests for spec ``subject-keyword-match-shown-in-live``.

These six Hypothesis-driven tests encode Property 1 from
``design.md`` §"Correctness Properties": every fetched email satisfying
``isBugCondition`` (Case A keyword-only or Case B both-filled
SUBJECT-only) is included in the captured ``emails_data`` exactly once
and surfaces through ``_write_live`` + ``_update_progress("live")``,
without ever firing the ``noemail`` path. Every test MUST PASS on the
FIXED ``imap_engine.py``.

Property roster (one test each, 1:1 with the task spec):

1. ``test_property1_case_a_keyword_only_inclusion`` — Case A keep
2. ``test_property2_case_b_both_filled_subject_only_inclusion`` — Case B keep
3. ``test_property3_exactly_once_inclusion`` — UID returned by both branches
   is appended to ``emails_data`` exactly once
4. ``test_property4_raw_from_rendering_for_case_a`` — raw ``From`` column
   in ``live.txt`` for Case A (no ``<from_addr>`` highlight form)
5. ``test_property5_live_counter_incremented_once`` — ``_update_progress("live")``
   fires exactly once per surviving account
6. ``test_property6_noemail_counter_not_incremented`` — neither
   ``_update_progress("noemail")`` nor ``_write_noemail`` ever fire when at
   least one email survives the post-fetch gate

Mocking strategy mirrors the bug-exploration suite
(``tests/test_subject_keyword_match_shown_in_live_bug.py``): the helper
``_setup_mock_imap`` patches ``imap_engine.imaplib.IMAP4_SSL`` so the
returned ``MagicMock`` mail connection routes ``search(...)`` by FROM/SUBJECT
substring on the criteria and returns generated UID lists, and
``fetch(eid, "(RFC822)")`` returns generated RFC822 bytes keyed by UID.
``MASTER_IMAP_SUCCESS_PATH`` and ``MASTER_IMAP_SUCCESS_LOCK_PATH`` are
redirected to ``tmp_path`` so the real master is never touched.

Validates: 2.1, 2.1.1, 2.2, 2.3
"""
from __future__ import annotations

import os
import sys
import uuid
from collections import Counter
from email.header import Header
from email.message import Message
from queue import Queue
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import HealthCheck, assume, given, settings, strategies as st

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from imap_engine import _subject_excluded, sender_matches  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers — mirror tests/test_subject_keyword_match_shown_in_live_bug.py
# ---------------------------------------------------------------------------
def _build_rfc822(from_header: str, subject: str, date_header: str) -> bytes:
    """Build a minimal RFC822 message with MIME-encoded headers (utf-8)."""
    msg = Message()
    msg["From"] = from_header
    msg["Subject"] = str(Header(subject, "utf-8"))
    msg["Date"] = date_header
    msg.set_payload("Body of message\r\n")
    return msg.as_bytes()


def _isolate_master(tmp_path, monkeypatch):
    """Redirect MASTER_IMAP_SUCCESS_PATH to a tmp_path-scoped file so the
    real master at the project root is never touched by ``_update_imap_config``.

    Also isolates skip_domains and subject_exclusion_list state files so
    production state on the host project root cannot leak into engine
    behavior during tests.
    """
    import imap_engine

    master_path = tmp_path / "imap_success.json"
    monkeypatch.setattr(
        imap_engine, "MASTER_IMAP_SUCCESS_PATH", str(master_path)
    )
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
    """Force example.com into DEFAULT_IMAP_CONFIG so ``_worker`` skips
    ``_try_imap_variants`` auto-discovery."""
    import imap_engine

    monkeypatch.setattr(
        imap_engine,
        "DEFAULT_IMAP_CONFIG",
        {"example.com": {"server": "imap.example.com", "port": 993}},
    )


def _make_mock_imap_factory(mock_conn):
    def factory(*args, **kwargs):
        return mock_conn

    return factory


def _build_fetch_return(uid: bytes, rfc822_bytes: bytes):
    """imaplib fetch return shape: ``("OK", [(b"<uid> (RFC822 {N}", rfc822_bytes), b")"])``."""
    header = uid + b" (RFC822 {%d}" % len(rfc822_bytes)
    return ("OK", [(header, rfc822_bytes), b")"])


def _run_worker_for_account(checker, account):
    """Drain a single account through ``_worker`` synchronously."""
    q = Queue()
    q.put(account)
    checker._worker(q)


def _make_unique_results_dir(tmp_path):
    """Per-example unique results dir to avoid Hypothesis cross-pollution
    when ``tmp_path`` is reused across examples (function-scoped fixture)."""
    sub = tmp_path / f"jobs_{uuid.uuid4().hex[:12]}"
    sub.mkdir(parents=True, exist_ok=True)
    return sub


def _build_search_responses(emails):
    """Compute (from_uids_bytes, subject_uids_bytes) responses for the mock
    SEARCH calls. Each is the imaplib wire-format space-separated bytes
    string (or ``b""`` if no UIDs).

    A label of ``"from_only"`` or ``"both"`` means the email's UID is
    returned by FROM SEARCH; ``"subject_only"`` or ``"both"`` means it is
    returned by SUBJECT SEARCH.
    """
    from_uids = sorted({e["uid"] for e in emails if e["label"] in ("from_only", "both")})
    subj_uids = sorted({e["uid"] for e in emails if e["label"] in ("subject_only", "both")})
    from_bytes = b" ".join(str(u).encode() for u in from_uids)
    subj_bytes = b" ".join(str(u).encode() for u in subj_uids)
    return from_bytes, subj_bytes


def _build_search_side_effect(from_bytes, subj_bytes):
    """Route ``mail_conn.search(charset, criteria)`` by criteria substring."""

    def side_effect(charset, criteria):
        if 'FROM "' in criteria:
            return ("OK", [from_bytes])
        if 'SUBJECT "' in criteria:
            return ("OK", [subj_bytes])
        return ("OK", [b""])

    return side_effect


def _build_fetch_side_effect(emails):
    """Map UID-as-str (matching ``eid_bytes.decode()`` in ``_worker``) to a
    fetch return value built from the generated email."""
    by_uid = {}
    for e in emails:
        rfc = _build_rfc822(
            from_header=e["from"],
            subject=e["subject"],
            date_header="Sat, 04 Jan 2025 10:00:00 +0000",
        )
        by_uid[str(e["uid"])] = (e["uid"], rfc)

    def side_effect(eid, what):
        # _worker passes ``eid_bytes.decode()`` so eid is a Python str here.
        uid_int, rfc = by_uid[eid]
        return _build_fetch_return(str(uid_int).encode(), rfc)

    return side_effect


def _setup_mock_imap(monkeypatch, scenario):
    """Patch imap_engine.imaplib.IMAP4_SSL with a mock connection wired up
    from ``scenario``. Returns the ``MagicMock`` so callers can inspect
    ``search``/``fetch`` call counts."""
    import imap_engine

    from_bytes, subj_bytes = _build_search_responses(scenario["emails"])
    mock_conn = MagicMock()
    mock_conn.search.side_effect = _build_search_side_effect(from_bytes, subj_bytes)
    mock_conn.fetch.side_effect = _build_fetch_side_effect(scenario["emails"])
    monkeypatch.setattr(
        imap_engine.imaplib, "IMAP4_SSL", _make_mock_imap_factory(mock_conn)
    )
    return mock_conn


def _build_checker(tmp_path, scenario):
    """Build an ``ImapChecker`` for the single ``alice@example.com`` account
    using the scenario's ``target_senders``/``keywords``. Master is assumed
    to be redirected by ``_isolate_master`` in the caller."""
    from imap_engine import ImapChecker

    results_dir = _make_unique_results_dir(tmp_path)
    job_id = os.path.basename(str(results_dir))
    checker = ImapChecker(
        job_id=job_id,
        results_dir=str(results_dir),
        accounts=[{"email": "alice@example.com", "password": "hunter2"}],
        target_senders=list(scenario["target_senders"]),
        keywords=list(scenario["keywords"]),
        search_days=7,
    )
    return checker, results_dir


def _expected_kept_emails(scenario):
    """Return the list of generated emails that the FIXED ``_worker`` MUST
    keep in ``emails_data``: non-OTP emails whose UID is in either the FROM
    or SUBJECT branch return set, AND which satisfy the three-arm OR gate.

    For Case A (target_senders=[]), the gate's first arm
    ``not self.target_senders`` is True for every email, so every non-OTP
    fetched email is kept.

    For Case B (target_senders non-empty), the gate keeps an email iff
    ``sender_matches(from_addr, target_senders)`` OR ``label in
    ("subject_only", "both")``. Equivalent to: ``label in ("from_only",
    "subject_only", "both")`` because ``label == "from_only"`` implies
    ``sender_matches`` is True (by construction of the from header).
    """
    target_senders = scenario["target_senders"]
    kept = []
    for e in scenario["emails"]:
        if e["is_otp"]:
            continue
        # Only emails whose UID is in email_ids (i.e. label != "neither")
        # ever reach the post-fetch gate.
        if e["label"] == "neither":
            continue
        if not target_senders:
            # Case A: gate's first arm is True → keep every fetched non-OTP.
            kept.append(e)
            continue
        # Case B: keep iff sender_matches OR UID in subject_matched_ids.
        from_addr = e["from"]
        if sender_matches(from_addr, target_senders):
            kept.append(e)
        elif e["label"] in ("subject_only", "both"):
            kept.append(e)
    return kept


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------
# Lowercase ASCII letters/digits keyword (per task hint: "ASCII letters +
# digits, lowercase"). 3..10 chars keeps subjects readable and avoids the
# FETCH cap of 20 newest emails interfering with property checks.
keyword_strategy = st.from_regex(r"[a-z]{3,10}", fullmatch=True)
sender_domain_strategy = st.from_regex(r"[a-z]{4,8}\.com", fullmatch=True)
local_part_strategy = st.from_regex(r"[a-z]{3,6}", fullmatch=True)

# OTP-shaped subject matches OTP_SUBJECT_REGEX = ``^Booking\.com – \w+ is
# your verification code$`` (en-dash U+2013).
_OTP_SUBJECT = "Booking.com \u2013 ABC123 is your verification code"


def _build_email(uid, label, target_senders, keywords, is_otp, kw_choice, salt):
    """Construct one synthetic email dict from the chosen labels.

    Construction guarantees:
    - ``label == "from_only"`` or ``"both"`` ⟹ from_header ends with one of
      ``target_senders`` (so ``sender_matches`` returns True).
    - ``label == "subject_only"`` or ``"neither"`` (with target_senders
      non-empty) ⟹ from_header ends with a non-target ``.net`` domain
      (so ``sender_matches`` returns False).
    - ``label in ("subject_only", "both")`` ⟹ subject contains a chosen
      keyword as substring (when ``is_otp`` is False).
    - ``label in ("from_only", "neither")`` ⟹ subject is uppercase+digits
      so no lowercase keyword can appear as substring (when ``is_otp``
      is False).
    - ``is_otp`` ⟹ subject matches ``OTP_SUBJECT_REGEX``.
    """
    local = f"user{salt}"
    if label in ("from_only", "both") and target_senders:
        # Pick first target sender domain (peel "@").
        domain = target_senders[0][1:]
        from_header = f"{local}@{domain}"
    else:
        # Use a .net domain so it never ends with a target's "@<x>.com".
        from_header = f"{local}@neutral{salt}.net"

    if is_otp:
        subject = _OTP_SUBJECT
    elif label in ("subject_only", "both"):
        kw = keywords[kw_choice % len(keywords)]
        subject = f"prefix-{kw}-tail-{salt}"
    else:
        subject = f"INNOCUOUS_{salt}_TEXT"  # uppercase only — no lowercase kw

    return {
        "uid": uid,
        "from": from_header,
        "subject": subject,
        "label": label,
        "is_otp": is_otp,
    }


@st.composite
def case_a_scenario(draw):
    """Case A: ``target_senders=[]`` and at least one non-OTP, SUBJECT-matched
    email so the gate's first arm (``not self.target_senders``) keeps it."""
    keywords = draw(
        st.lists(keyword_strategy, min_size=1, max_size=4, unique=True)
    )
    n = draw(st.integers(min_value=1, max_value=5))
    emails = []
    for i in range(n):
        # In Case A the FROM SEARCH loop is skipped (target_senders=[]),
        # so the only meaningful labels are "subject_only" (UID returned
        # by SUBJECT SEARCH) and "neither" (no SEARCH return — never
        # reaches FETCH).
        label = draw(st.sampled_from(["subject_only", "neither"]))
        is_otp = draw(st.booleans()) if label == "subject_only" else False
        kw_choice = draw(st.integers(min_value=0, max_value=10))
        emails.append(
            _build_email(
                uid=i + 1,
                label=label,
                target_senders=[],
                keywords=keywords,
                is_otp=is_otp,
                kw_choice=kw_choice,
                salt=i + 1,
            )
        )
    # Need at least one email that the FIXED gate must keep.
    assume(any(e["label"] == "subject_only" and not e["is_otp"] for e in emails))
    return {
        "target_senders": [],
        "keywords": keywords,
        "emails": emails,
        "regime": "case_a",
    }


@st.composite
def case_b_scenario(draw):
    """Case B: ``target_senders`` non-empty and ``keywords`` non-empty, with
    at least one non-OTP email labeled ``"subject_only"`` whose ``from_addr``
    does NOT match any sender — exercising the new third arm
    ``eid_bytes in subject_matched_ids``."""
    sender_domain = draw(sender_domain_strategy)
    target_senders = [f"@{sender_domain}"]
    keywords = draw(
        st.lists(keyword_strategy, min_size=1, max_size=4, unique=True)
    )
    n = draw(st.integers(min_value=1, max_value=5))
    emails = []
    for i in range(n):
        label = draw(
            st.sampled_from(["from_only", "subject_only", "both", "neither"])
        )
        is_otp = (
            draw(st.booleans())
            if label in ("subject_only", "both", "from_only")
            else False
        )
        kw_choice = draw(st.integers(min_value=0, max_value=10))
        emails.append(
            _build_email(
                uid=i + 1,
                label=label,
                target_senders=target_senders,
                keywords=keywords,
                is_otp=is_otp,
                kw_choice=kw_choice,
                salt=i + 1,
            )
        )
    # Need at least one SUBJECT-only non-OTP — that's the new keep path.
    assume(any(e["label"] == "subject_only" and not e["is_otp"] for e in emails))
    return {
        "target_senders": target_senders,
        "keywords": keywords,
        "emails": emails,
        "regime": "case_b",
    }


@st.composite
def case_a_or_b_scenario(draw):
    """Either Case A or Case B. Used by properties 5 and 6 which apply to
    both regimes."""
    if draw(st.booleans()):
        return draw(case_a_scenario())
    return draw(case_b_scenario())


@st.composite
def both_branches_scenario(draw):
    """Case B variant where at least one email is labeled ``"both"`` — its
    UID is returned by FROM SEARCH AND SUBJECT SEARCH, so it enters
    ``email_ids`` via two paths. Used by property 3 to verify exactly-once
    inclusion in ``emails_data``."""
    sender_domain = draw(sender_domain_strategy)
    target_senders = [f"@{sender_domain}"]
    keywords = draw(
        st.lists(keyword_strategy, min_size=1, max_size=3, unique=True)
    )
    n = draw(st.integers(min_value=1, max_value=4))
    emails = []
    for i in range(n):
        label = draw(st.sampled_from(["both", "from_only", "subject_only"]))
        # Force is_otp=False here so every "both" email is kept and
        # observable in emails_data — the property is about exactly-once,
        # not about OTP filtering.
        is_otp = False
        kw_choice = draw(st.integers(min_value=0, max_value=10))
        emails.append(
            _build_email(
                uid=i + 1,
                label=label,
                target_senders=target_senders,
                keywords=keywords,
                is_otp=is_otp,
                kw_choice=kw_choice,
                salt=i + 1,
            )
        )
    assume(any(e["label"] == "both" for e in emails))
    return {
        "target_senders": target_senders,
        "keywords": keywords,
        "emails": emails,
        "regime": "both_branches",
    }


# ---------------------------------------------------------------------------
# Common Hypothesis settings: capped per task spec, with deadline disabled
# (file-locked master writes per example can be slow on Windows) and the
# function-scoped-fixture health check suppressed (tmp_path / monkeypatch).
# ---------------------------------------------------------------------------
PBT_SETTINGS = settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


# ---------------------------------------------------------------------------
# Property 1 — Case A keep: keyword-only inclusion
# ---------------------------------------------------------------------------
@PBT_SETTINGS
@given(scenario=case_a_scenario())
def test_property1_case_a_keyword_only_inclusion(tmp_path, monkeypatch, scenario):
    """``target_senders=[]`` and ``keywords`` non-empty: every non-OTP email
    whose UID is returned by SUBJECT SEARCH MUST appear exactly once in the
    captured ``emails_data`` passed to ``_write_live``.

    Validates: Requirements 2.1, 2.2 (Property 1 from design.md).
    """
    import imap_engine

    _isolate_master(tmp_path, monkeypatch)
    _force_known_domain(monkeypatch)
    _setup_mock_imap(monkeypatch, scenario)

    checker, _ = _build_checker(tmp_path, scenario)

    with patch.object(imap_engine.ImapChecker, "_write_live", autospec=True) as spy_live, \
         patch.object(imap_engine.ImapChecker, "_write_noemail", autospec=True), \
         patch.object(imap_engine.ImapChecker, "_update_progress", autospec=True):
        _run_worker_for_account(checker, checker.accounts[0])

    expected = _expected_kept_emails(scenario)

    # _write_live must have been called exactly once with the surviving emails.
    assert len(spy_live.call_args_list) == 1, (
        f"Property 1 — _write_live must be called exactly once when at least "
        f"one Case A email survives. scenario={scenario!r}, "
        f"call_count={len(spy_live.call_args_list)}"
    )
    _, _email_addr, _password, emails_data = spy_live.call_args_list[0].args

    expected_subjects = Counter(e["subject"] for e in expected)
    actual_subjects = Counter(d["subject"] for d in emails_data)
    assert actual_subjects == expected_subjects, (
        f"Property 1 — captured emails_data subjects must match the set of "
        f"non-OTP SUBJECT-matched emails exactly once each.\n"
        f"  expected: {dict(expected_subjects)}\n"
        f"  actual  : {dict(actual_subjects)}\n"
        f"  scenario: {scenario!r}"
    )


# ---------------------------------------------------------------------------
# Property 2 — Case B keep: both-filled SUBJECT-only inclusion
# ---------------------------------------------------------------------------
@PBT_SETTINGS
@given(scenario=case_b_scenario())
def test_property2_case_b_both_filled_subject_only_inclusion(
    tmp_path, monkeypatch, scenario
):
    """``target_senders`` non-empty AND ``keywords`` non-empty: every
    SUBJECT-only non-OTP email (``from_addr`` does not satisfy
    ``sender_matches`` AND UID is in SUBJECT branch) MUST appear exactly
    once in ``emails_data``.

    Validates: Requirements 2.3 (Property 1 from design.md).
    """
    import imap_engine

    _isolate_master(tmp_path, monkeypatch)
    _force_known_domain(monkeypatch)
    _setup_mock_imap(monkeypatch, scenario)

    checker, _ = _build_checker(tmp_path, scenario)

    with patch.object(imap_engine.ImapChecker, "_write_live", autospec=True) as spy_live, \
         patch.object(imap_engine.ImapChecker, "_write_noemail", autospec=True), \
         patch.object(imap_engine.ImapChecker, "_update_progress", autospec=True):
        _run_worker_for_account(checker, checker.accounts[0])

    # Predicate from the task spec: SUBJECT-only ⟺
    # not sender_matches(from_addr, target_senders) AND label in
    # ("subject_only",) — i.e. UID is solely in subject_matched_ids.
    target_senders = scenario["target_senders"]
    expected_subject_only = [
        e
        for e in scenario["emails"]
        if (
            not e["is_otp"]
            and e["label"] == "subject_only"
            and not sender_matches(e["from"], target_senders)
        )
    ]

    assert len(spy_live.call_args_list) == 1, (
        f"Property 2 — _write_live must be called exactly once when at least "
        f"one Case B SUBJECT-only email survives. scenario={scenario!r}, "
        f"call_count={len(spy_live.call_args_list)}"
    )
    _, _email_addr, _password, emails_data = spy_live.call_args_list[0].args

    actual_subjects = {d["subject"] for d in emails_data}
    for e in expected_subject_only:
        assert e["subject"] in actual_subjects, (
            f"Property 2 — SUBJECT-only non-OTP email missing from emails_data.\n"
            f"  missing email   : uid={e['uid']!r}, from={e['from']!r}, "
            f"subject={e['subject']!r}, label={e['label']!r}\n"
            f"  emails_data subj: {sorted(actual_subjects)!r}\n"
            f"  scenario        : {scenario!r}"
        )

    # Each subject-only email appears exactly once (no duplicate appends).
    counts = Counter(d["subject"] for d in emails_data)
    for e in expected_subject_only:
        assert counts[e["subject"]] == 1, (
            f"Property 2 — SUBJECT-only email appended more than once. "
            f"subject={e['subject']!r}, count={counts[e['subject']]}, "
            f"scenario={scenario!r}"
        )


# ---------------------------------------------------------------------------
# Property 3 — Exactly-once inclusion (UID in both FROM and SUBJECT branch)
# ---------------------------------------------------------------------------
@PBT_SETTINGS
@given(scenario=both_branches_scenario())
def test_property3_exactly_once_inclusion(tmp_path, monkeypatch, scenario):
    """When an email's UID is returned by BOTH FROM SEARCH and SUBJECT
    SEARCH, the ``email_ids`` set deduplicates it (set union semantics) and
    the FETCH loop appends it to ``emails_data`` at most once.

    Asserted via ``Counter`` over ``(subject, from, date)`` triples — every
    triple count must be exactly 1.

    Validates: Requirement 2.3 (single logical OR per email — design.md
    §"Design Decisions > Single logical OR per email").
    """
    import imap_engine

    _isolate_master(tmp_path, monkeypatch)
    _force_known_domain(monkeypatch)
    _setup_mock_imap(monkeypatch, scenario)

    checker, _ = _build_checker(tmp_path, scenario)

    with patch.object(imap_engine.ImapChecker, "_write_live", autospec=True) as spy_live, \
         patch.object(imap_engine.ImapChecker, "_write_noemail", autospec=True), \
         patch.object(imap_engine.ImapChecker, "_update_progress", autospec=True):
        _run_worker_for_account(checker, checker.accounts[0])

    assert len(spy_live.call_args_list) == 1, (
        f"Property 3 — _write_live must be called exactly once. "
        f"scenario={scenario!r}, call_count={len(spy_live.call_args_list)}"
    )
    _, _email_addr, _password, emails_data = spy_live.call_args_list[0].args

    triples = Counter((d["subject"], d["from"], d["date"]) for d in emails_data)
    duplicates = [t for t, c in triples.items() if c != 1]
    assert not duplicates, (
        f"Property 3 — every (subject, from, date) triple in emails_data "
        f"must occur exactly once.\n"
        f"  duplicates: {duplicates!r}\n"
        f"  scenario  : {scenario!r}"
    )

    # Sanity: at least one "both"-labeled email made it into emails_data.
    both_subjects = {
        e["subject"] for e in scenario["emails"] if e["label"] == "both"
    }
    actual_subjects = {d["subject"] for d in emails_data}
    assert both_subjects & actual_subjects, (
        f"Property 3 — at least one 'both'-labeled email must appear in "
        f"emails_data (otherwise the test isn't exercising the dual-branch "
        f"path).\n  both_subjects: {both_subjects!r}\n"
        f"  actual_subjects: {actual_subjects!r}\n"
        f"  scenario: {scenario!r}"
    )


# ---------------------------------------------------------------------------
# Property 4 — Raw From rendering for Case A
# ---------------------------------------------------------------------------
def test_property4_raw_from_rendering_for_case_a(tmp_path, monkeypatch):
    """Construct a fixed Case A scenario (``target_senders=[]``, one keyword,
    one non-OTP SUBJECT-matched email with display-name ``From`` header) and
    let ``_write_live`` run for real. Read the generated ``live.txt`` and
    assert that the From column contains the raw ``From`` header (display
    name + ``<addr>``) — NOT the bare ``<from_addr>`` form that the
    ``sender_matches``-True branch at ``imap_engine.py``:385 produces.

    Per ``design.md`` §"Why Not Modify ``sender_matches``": for Case A,
    ``sender_matches(from_addr, [])`` returns False, so the ``_write_live``
    rendering at line 385–387 is naturally raw.

    Validates: Requirement 2.1.1.
    """
    import imap_engine
    from imap_engine import ImapChecker

    _isolate_master(tmp_path, monkeypatch)
    _force_known_domain(monkeypatch)

    raw_from_header = "Hotel Chain <reservations@hotelchain.com>"
    bare_addr_form = "<reservations@hotelchain.com>"
    subject = "Booking confirmation #12345 keyword tail"

    rfc822 = _build_rfc822(
        from_header=raw_from_header,
        subject=subject,
        date_header="Sat, 04 Jan 2025 10:00:00 +0000",
    )
    fetch_return = _build_fetch_return(b"42", rfc822)

    mock_conn = MagicMock()
    mock_conn.search.side_effect = _build_search_side_effect(
        from_bytes=b"", subj_bytes=b"42"
    )
    mock_conn.fetch.return_value = fetch_return
    monkeypatch.setattr(
        imap_engine.imaplib, "IMAP4_SSL", _make_mock_imap_factory(mock_conn)
    )

    results_dir = _make_unique_results_dir(tmp_path)
    job_id = os.path.basename(str(results_dir))
    checker = ImapChecker(
        job_id=job_id,
        results_dir=str(results_dir),
        accounts=[{"email": "alice@example.com", "password": "hunter2"}],
        target_senders=[],
        keywords=["keyword"],
        search_days=7,
    )

    # Do NOT spy on _write_live — let it run and write to live.txt for real.
    _run_worker_for_account(checker, checker.accounts[0])

    live_path = os.path.join(str(results_dir), "live.txt")
    assert os.path.exists(live_path), (
        f"Property 4 — live.txt must exist after Case A worker run. "
        f"path={live_path!r}"
    )
    with open(live_path, "r", encoding="utf-8") as f:
        contents = f.read()

    # Raw From header value must appear verbatim in the file.
    assert raw_from_header in contents, (
        f"Property 4 — raw From header {raw_from_header!r} must appear in "
        f"live.txt (Case A renders raw From, not <from_addr>). "
        f"contents=\n{contents}"
    )

    # The bare-addr form must NOT appear standalone. Because the raw form
    # contains the bare-addr form as a substring (``Hotel Chain
    # <reservations@hotelchain.com>`` ends with ``<reservations@hotelchain.com>``)
    # we instead assert that the ``From`` column does NOT begin with the
    # bare-addr form by scanning each row for the column boundary.
    #
    # The row format is:
    #   ``    | <No.> | <Date> | <From> | <Subject> |``
    # so the From column starts after the third ``| `` separator. We split
    # the row on ``|`` and inspect column index 3 (1-based: 1=No., 2=Date,
    # 3=From, 4=Subject).
    found_data_row = False
    for line in contents.splitlines():
        if line.startswith("    | ") and "|" in line[6:]:
            cells = [c.strip() for c in line.split("|")]
            # Padding cell at the start (empty string before the first ``|``)
            # and at the end (after the last ``|``). Real columns at
            # indices 1..4: No., Date, From, Subject.
            if len(cells) >= 5 and cells[1].isdigit():
                found_data_row = True
                from_cell = cells[3]
                assert from_cell == raw_from_header, (
                    f"Property 4 — From column for Case A must be the raw "
                    f"From header, NOT the <from_addr> form.\n"
                    f"  expected: {raw_from_header!r}\n"
                    f"  actual  : {from_cell!r}\n"
                    f"  bare    : {bare_addr_form!r} (must NOT be the cell)\n"
                    f"  row     : {line!r}"
                )
                assert from_cell != bare_addr_form, (
                    f"Property 4 — From column collapsed to <from_addr> "
                    f"form; sender_matches highlight branch fired for "
                    f"target_senders=[]. row={line!r}"
                )

    assert found_data_row, (
        f"Property 4 — no data row found in live.txt (expected at least one "
        f"row for the surviving Case A email). contents=\n{contents}"
    )


# ---------------------------------------------------------------------------
# Property 5 — Live counter incremented exactly once
# ---------------------------------------------------------------------------
@PBT_SETTINGS
@given(scenario=case_a_or_b_scenario())
def test_property5_live_counter_incremented_once(tmp_path, monkeypatch, scenario):
    """For every Case A or Case B scenario where ``emails_data`` ends
    non-empty, ``self._update_progress`` MUST be called exactly once with
    ``"live"`` for the account.

    Validates: Requirement 2.2.
    """
    import imap_engine

    _isolate_master(tmp_path, monkeypatch)
    _force_known_domain(monkeypatch)
    _setup_mock_imap(monkeypatch, scenario)

    checker, _ = _build_checker(tmp_path, scenario)

    with patch.object(imap_engine.ImapChecker, "_write_live", autospec=True) as spy_live, \
         patch.object(imap_engine.ImapChecker, "_write_noemail", autospec=True), \
         patch.object(imap_engine.ImapChecker, "_update_progress", autospec=True) as spy_progress:
        _run_worker_for_account(checker, checker.accounts[0])

    # Sanity: the scenario was constructed so that at least one email
    # survives and emails_data is non-empty.
    assert len(spy_live.call_args_list) == 1, (
        f"Property 5 prerequisite — _write_live must be called once per "
        f"keep-eligible scenario. scenario={scenario!r}"
    )

    # Each call's args = (self, category) under autospec=True.
    categories = [c.args[1] for c in spy_progress.call_args_list]
    live_count = sum(1 for c in categories if c == "live")
    assert live_count == 1, (
        f"Property 5 — _update_progress('live') must fire exactly once.\n"
        f"  categories: {categories!r}\n"
        f"  scenario  : {scenario!r}"
    )


# ---------------------------------------------------------------------------
# Property 6 — noemail counter and _write_noemail are NOT invoked
# ---------------------------------------------------------------------------
@PBT_SETTINGS
@given(scenario=case_a_or_b_scenario())
def test_property6_noemail_counter_not_incremented(tmp_path, monkeypatch, scenario):
    """For the same Case A and Case B scenarios as property 5,
    ``_update_progress("noemail")`` MUST NEVER fire AND ``_write_noemail``
    MUST NEVER be called for the account ``(email_addr, password)``.

    Validates: Requirement 2.2.
    """
    import imap_engine

    _isolate_master(tmp_path, monkeypatch)
    _force_known_domain(monkeypatch)
    _setup_mock_imap(monkeypatch, scenario)

    checker, _ = _build_checker(tmp_path, scenario)

    with patch.object(imap_engine.ImapChecker, "_write_live", autospec=True) as spy_live, \
         patch.object(imap_engine.ImapChecker, "_write_noemail", autospec=True) as spy_noemail, \
         patch.object(imap_engine.ImapChecker, "_update_progress", autospec=True) as spy_progress:
        _run_worker_for_account(checker, checker.accounts[0])

    assert len(spy_live.call_args_list) == 1, (
        f"Property 6 prerequisite — _write_live must be called once. "
        f"scenario={scenario!r}"
    )

    categories = [c.args[1] for c in spy_progress.call_args_list]
    assert "noemail" not in categories, (
        f"Property 6 — _update_progress('noemail') must NEVER fire when at "
        f"least one Case A or Case B email survives.\n"
        f"  categories: {categories!r}\n"
        f"  scenario  : {scenario!r}"
    )

    # _write_noemail must not be called for ``(alice@example.com, hunter2)``.
    bad_calls = [
        c
        for c in spy_noemail.call_args_list
        if len(c.args) >= 3
        and c.args[1] == "alice@example.com"
        and c.args[2] == "hunter2"
    ]
    assert not bad_calls, (
        f"Property 6 — _write_noemail must NEVER be called for the surviving "
        f"account.\n  bad_calls: {bad_calls!r}\n"
        f"  scenario  : {scenario!r}"
    )
