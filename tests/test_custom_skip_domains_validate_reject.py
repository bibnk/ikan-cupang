"""
Property test for validation rejection semantics (custom-skip-domains spec).

Encodes Property 5 from ``design.md`` §"Correctness Properties":

  Validation rejection semantics. For every input list ``R`` containing at
  least one entry violating empty / >255 / whitespace rules,
  ``_validate_skip_entries(R)`` returns ``([], rejected)`` where
  ``rejected`` lists exactly the offending entries in first-occurrence
  order with duplicates preserved (no strip/lower applied), the POST
  endpoint returns 400 with body
  ``{"error": "Invalid entries", "rejected": rejected}``, and the on-disk
  file is unchanged after the failed POST (Requirement 9.5
  all-or-nothing).

Validates: Requirements 9.2, 9.3, 9.4, 9.5
"""
from __future__ import annotations

import os
import sys

from hypothesis import HealthCheck, assume, given, settings, strategies as st

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import imap_engine  # noqa: E402
from imap_engine import _WHITESPACE_RE, _validate_skip_entries  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _redirect_skip_paths(tmp_path, monkeypatch):
    skip_path = str(tmp_path / "skip_domains.json")
    monkeypatch.setattr(imap_engine, "SKIP_DOMAINS_PATH", skip_path)
    monkeypatch.setattr(imap_engine, "SKIP_DOMAINS_LOCK_PATH", skip_path + ".lock")
    return skip_path


def _entry_violates(entry: str) -> bool:
    """Return True iff *entry* fails at least one of the three validator
    rules (empty after strip, length >255 after strip, contains \\s after
    strip)."""
    stripped = entry.strip()
    if stripped == "":
        return True
    if len(stripped) > 255:
        return True
    if _WHITESPACE_RE.search(stripped):
        return True
    return False


# ---------------------------------------------------------------------------
# Generators — mix of valid and invalid entries with a guaranteed bad one.
# Whitespace pool covers ASCII space, \t, \n, \r, \v, \f, NBSP, ideographic.
# ---------------------------------------------------------------------------
WHITESPACE_CHARS = ["\t", "\n", "\r", "\v", "\f", "\u00a0", "\u3000", " "]


@st.composite
def whitespace_entry(draw):
    """An entry that fails the whitespace check (contains internal whitespace)."""
    left = draw(st.from_regex(r"[a-z]{1,5}", fullmatch=True))
    ws = draw(st.sampled_from(WHITESPACE_CHARS))
    right = draw(st.from_regex(r"[a-z]{1,5}", fullmatch=True))
    return left + ws + right


@st.composite
def empty_entry(draw):
    """An entry that fails the empty check (empty after strip)."""
    return draw(st.sampled_from(["", " ", "\t", "  \n  ", "\u00a0"]))


@st.composite
def too_long_entry(draw):
    """An entry whose stripped length exceeds 255 chars."""
    n = draw(st.integers(min_value=256, max_value=320))
    return "x" * n


@st.composite
def invalid_entry(draw):
    """An entry guaranteed to violate at least one rule."""
    kind = draw(st.sampled_from(["empty", "long", "whitespace"]))
    if kind == "empty":
        return draw(empty_entry())
    if kind == "long":
        return draw(too_long_entry())
    return draw(whitespace_entry())


@st.composite
def valid_entry(draw):
    """An entry that passes all three rules."""
    return draw(
        st.from_regex(r"[a-z0-9][a-z0-9.\-]{0,30}", fullmatch=True).filter(
            lambda s: s.strip() == s
            and not _WHITESPACE_RE.search(s)
            and 1 <= len(s) <= 255
        )
    )


@st.composite
def mixed_payload_with_at_least_one_invalid(draw):
    """A list of entries containing at least one invalid one."""
    parts = draw(
        st.lists(
            st.one_of(valid_entry(), invalid_entry()),
            min_size=1,
            max_size=10,
        )
    )
    # Force at least one invalid entry.
    if not any(_entry_violates(e) for e in parts):
        parts.append(draw(invalid_entry()))
    return parts


# ---------------------------------------------------------------------------
# Property 5 — Validation rejection semantics
# ---------------------------------------------------------------------------
@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(payload=mixed_payload_with_at_least_one_invalid())
def test_property5_validation_rejection_semantics(tmp_path, monkeypatch, payload):
    """Validates: Requirements 9.2, 9.3, 9.4, 9.5.

    Asserts:

    1. ``_validate_skip_entries(R)`` returns ``([], rejected)`` where
       ``rejected`` is exactly the sublist of ``R`` that fails at least
       one rule, in first-occurrence order, duplicates preserved, entries
       reported as-sent (no strip/lower).
    2. ``POST /api/admin/skip-domains`` with ``{"domains": R}`` returns
       400 ``{"error": "Invalid entries", "rejected": rejected}``.
    3. ``_load_skip_domains()`` after the failed POST returns the same
       value as before the POST (file unchanged — Requirement 9.5
       all-or-nothing).
    """
    assume(any(_entry_violates(e) for e in payload))

    skip_path = _redirect_skip_paths(tmp_path, monkeypatch)
    # Pre-seed file with a known good baseline so we can verify the failed
    # POST does not mutate it.
    baseline = ["seed-a", "seed-b"]
    imap_engine._save_skip_domains(baseline)
    pre_bytes = open(skip_path, "rb").read()

    # 1. Validator returns ([], expected_rejected).
    expected_rejected = [e for e in payload if _entry_violates(e)]
    valid, rejected = _validate_skip_entries(payload)
    assert valid == [], (
        f"validator must return [] for valid_normalized when any entry "
        f"fails (all-or-nothing).\n  payload : {payload!r}\n  valid   : {valid!r}"
    )
    assert rejected == expected_rejected, (
        f"validator rejected list mismatch.\n"
        f"  expected: {expected_rejected!r}\n"
        f"  actual  : {rejected!r}\n"
        f"  payload : {payload!r}"
    )


    # 2. POST returns 400 with body {"error": "Invalid entries", "rejected": ...}.
    import app as flask_app

    client = flask_app.app.test_client()
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["is_admin"] = True
        sess["code_hash"] = "test_hash"
        sess["user_label"] = "tester"

    res = client.post("/api/admin/skip-domains", json={"domains": list(payload)})
    assert res.status_code == 400, (
        f"POST with invalid payload expected 400, got {res.status_code}; "
        f"body={res.get_data(as_text=True)!r}\n  payload: {payload!r}"
    )
    body = res.get_json()
    assert body == {"error": "Invalid entries", "rejected": expected_rejected}, (
        f"POST response body mismatch.\n"
        f"  expected: {{'error': 'Invalid entries', 'rejected': "
        f"{expected_rejected!r}}}\n"
        f"  actual  : {body!r}\n"
        f"  payload : {payload!r}"
    )

    # 3. File unchanged after failed POST (all-or-nothing).
    post_bytes = open(skip_path, "rb").read()
    assert post_bytes == pre_bytes, (
        f"file changed after failed POST (must be unchanged per "
        f"Requirement 9.5 all-or-nothing).\n"
        f"  pre : {pre_bytes!r}\n  post: {post_bytes!r}"
    )
    assert imap_engine._load_skip_domains() == baseline, (
        f"_load_skip_domains() must return baseline after failed POST; "
        f"baseline={baseline!r}"
    )


# ---------------------------------------------------------------------------
# Example test — Requirement 9.1 short-circuit order: empty → length →
# whitespace, no rule label leaked in the rejected list.
# ---------------------------------------------------------------------------
def test_validate_short_circuit_order_and_no_rule_label():
    """Validates: Requirement 9.1 (short-circuit order) + 9.4(d) (no rule
    name in the rejected list).

    Feed three entries each violating a different rule. Each must
    short-circuit on its first failed rule and appear in the rejected list
    in original order, with no rule label appended.
    """
    payload = ["", "x" * 300, "ya hoo"]
    valid, rejected = _validate_skip_entries(payload)
    assert valid == [], f"valid must be [] when any entry fails, got {valid!r}"
    assert rejected == payload, (
        f"rejected list must contain entries as-sent in original order, "
        f"with no rule label.\n"
        f"  expected: {payload!r}\n  actual  : {rejected!r}"
    )
