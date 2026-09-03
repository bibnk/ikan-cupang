"""
Property test for ``_normalize_exclusion_entries`` (subject-exclusion-list spec).

Encodes Property 1 from ``design.md`` §"Correctness Properties":

  Normalization correctness and idempotency. For every input list of strings,
  ``_normalize_exclusion_entries(raw)`` returns a list whose elements are
  ``s.strip().lower()`` for some non-empty ``s in raw``, with no duplicates,
  in first-occurrence order, all lowercased, internal whitespace preserved,
  and applying the helper twice gives the same result as applying it once.

Validates: Requirements 4.1, 4.2, 4.3, 4.4
"""
from __future__ import annotations

import os
import sys

from hypothesis import given, settings, strategies as st

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from imap_engine import _normalize_exclusion_entries  # noqa: E402


@settings(max_examples=50, deadline=None)
@given(raw=st.lists(st.text(min_size=0, max_size=300), min_size=0, max_size=20))
def test_property1_normalize_correct_and_idempotent(raw):
    """Validates: Requirements 4.1, 4.2, 4.3, 4.4."""
    out = _normalize_exclusion_entries(raw)

    # (1) Every output element is strip()+lower() of some input.
    valid_norms = {s.strip().lower() for s in raw if s.strip() != ""}
    for el in out:
        assert el in valid_norms, (
            f"output element {el!r} not produced by strip+lower of any input.\n"
            f"  raw    : {raw!r}\n  output : {out!r}"
        )

    # (2) No empty element.
    assert "" not in out

    # (3) No duplicates.
    assert len(out) == len(set(out))

    # (4) First-occurrence order preserved.
    expected_order = []
    seen = set()
    for s in raw:
        norm = s.strip().lower()
        if norm == "" or norm in seen:
            continue
        seen.add(norm)
        expected_order.append(norm)
    assert out == expected_order

    # (5) Output already-lowercased (Requirement 4.4).
    for el in out:
        assert el == el.lower(), f"output {el!r} not lowercased"

    # (6) Internal whitespace preserved (Requirement 4.3).
    for el in out:
        # Output element must equal its own stripped form (no leading/trailing ws).
        assert el == el.strip(), f"output {el!r} has leading/trailing whitespace"

    # (7) Idempotency.
    assert _normalize_exclusion_entries(out) == out


def test_normalize_internal_whitespace_preserved():
    """Validates: Requirement 4.3 — internal ASCII spaces preserved."""
    raw = ["is your verification code", "  Newsletter  ", "PROMO ALERT"]
    out = _normalize_exclusion_entries(raw)
    assert out == ["is your verification code", "newsletter", "promo alert"]


def test_normalize_empty_input():
    assert _normalize_exclusion_entries([]) == []


def test_normalize_drops_empty_and_whitespace_only():
    raw = ["", "   ", "\t", "hello", "  hello  ", "HELLO"]
    out = _normalize_exclusion_entries(raw)
    assert out == ["hello"]
