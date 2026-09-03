"""
Validation rejection semantics (subject-exclusion-list spec).

Encodes Property 4 from ``design.md`` §"Correctness Properties".

Validates: Requirements 9.1, 9.2, 9.3, 9.4, 9.5, 9.6
"""
from __future__ import annotations

import os
import sys

from hypothesis import HealthCheck, assume, given, settings, strategies as st

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import imap_engine  # noqa: E402
from imap_engine import _has_control_char, _validate_exclusion_entries  # noqa: E402


def _redirect(tmp_path, monkeypatch):
    p = str(tmp_path / "subject_exclusion_list.json")
    monkeypatch.setattr(imap_engine, "SUBJECT_EXCLUSION_PATH", p)
    monkeypatch.setattr(imap_engine, "SUBJECT_EXCLUSION_LOCK_PATH", p + ".lock")
    return p


def _violates(entry: str) -> bool:
    s = entry.strip()
    if s == "":
        return True
    if len(s) > 500:
        return True
    if _has_control_char(s):
        return True
    return False


# Control chars include the explicit set + randomly chosen low-codepoint chars.
EXPLICIT_CTRL = ["\r", "\n", "\t", "\v", "\f", "\x00"]


@st.composite
def empty_entry(draw):
    return draw(st.sampled_from(["", " ", "\t", "  \n  "]))


@st.composite
def too_long_entry(draw):
    n = draw(st.integers(min_value=501, max_value=600))
    return "a" * n


@st.composite
def control_entry(draw):
    left = draw(st.from_regex(r"[a-z]{1,5}", fullmatch=True))
    ctrl = draw(st.sampled_from(EXPLICIT_CTRL + [chr(c) for c in range(0x20) if c not in (0x20,)]))
    right = draw(st.from_regex(r"[a-z]{1,5}", fullmatch=True))
    return left + ctrl + right


@st.composite
def invalid_entry(draw):
    return draw(st.one_of(empty_entry(), too_long_entry(), control_entry()))


@st.composite
def valid_entry(draw):
    # Allow internal ASCII spaces (Requirement 4.3, 9.4 carve-out)
    return draw(
        st.from_regex(r"[a-z0-9][a-z0-9 .\-]{0,30}", fullmatch=True).filter(
            lambda s: s.strip() == s and 1 <= len(s) <= 500 and not _has_control_char(s)
        )
    )


@st.composite
def mixed_payload_with_one_invalid(draw):
    parts = draw(
        st.lists(st.one_of(valid_entry(), invalid_entry()), min_size=1, max_size=8)
    )
    if not any(_violates(p) for p in parts):
        parts.append(draw(invalid_entry()))
    return parts


@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(payload=mixed_payload_with_one_invalid())
def test_property4_validation_rejection_semantics(tmp_path, monkeypatch, payload):
    """Validates: Requirements 9.1–9.6."""
    assume(any(_violates(e) for e in payload))

    p = _redirect(tmp_path, monkeypatch)
    baseline = ["seed-a", "seed-b"]
    imap_engine._save_subject_exclusion_list(baseline)
    pre_bytes = open(p, "rb").read()

    expected_rejected = [e for e in payload if _violates(e)]
    valid, rejected = _validate_exclusion_entries(payload)
    assert valid == []
    assert rejected == expected_rejected

    import app as flask_app

    client = flask_app.app.test_client()
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["is_admin"] = True
        sess["code_hash"] = "test_hash"
        sess["user_label"] = "tester"

    res = client.post(
        "/api/admin/subject-exclusion-list",
        json={"patterns": list(payload)},
    )
    assert res.status_code == 400
    body = res.get_json()
    assert body == {"error": "Invalid entries", "rejected": expected_rejected}
    # Body must NOT expose which rule failed (Requirement 9.5(d))
    assert "rule" not in body and "violation" not in body

    # File unchanged (all-or-nothing, Requirement 9.6).
    post_bytes = open(p, "rb").read()
    assert post_bytes == pre_bytes
    assert imap_engine._load_subject_exclusion_list() == baseline


def test_validate_short_circuit_order_no_rule_label():
    """Validates: Requirement 9.1 (short-circuit order) + 9.5(d)."""
    payload = ["", "x" * 600, "bad\nentry"]
    valid, rejected = _validate_exclusion_entries(payload)
    assert valid == []
    assert rejected == payload


def test_validate_ascii_space_allowed():
    """Validates: Requirement 9.4 carve-out — ASCII U+0020 is allowed."""
    payload = ["is your verification code", "promo alert", "newsletter only"]
    valid, rejected = _validate_exclusion_entries(payload)
    assert rejected == []
    assert valid == payload  # already normalized
