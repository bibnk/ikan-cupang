"""
Property-based tests for ``_normalize_skip_entries`` (custom-skip-domains spec).

Encodes Property 1 from ``design.md`` §"Correctness Properties":

  Normalization correctness and idempotency. For every input list of strings,
  ``_normalize_skip_entries(raw)`` returns a list whose elements are
  ``s.strip().lower()`` for some non-empty ``s in raw``, with no duplicates,
  in first-occurrence order, and applying the helper twice gives the same
  result as applying it once.

Validates: Requirements 4.1, 4.2, 4.3
"""
from __future__ import annotations

import os
import sys

from hypothesis import given, settings, strategies as st

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from imap_engine import _normalize_skip_entries  # noqa: E402


# ---------------------------------------------------------------------------
# Property 1 — Normalization correctness and idempotency
# ---------------------------------------------------------------------------
@settings(max_examples=50, deadline=None)
@given(
    raw=st.lists(
        st.text(min_size=0, max_size=300),
        min_size=0,
        max_size=20,
    )
)
def test_property1_normalize_correct_and_idempotent(raw):
    """Validates: Requirements 4.1, 4.2.

    Asserts five sub-claims about ``_normalize_skip_entries``:

    1. Every output element is ``s.strip().lower()`` for some input ``s``
       that has a non-empty stripped form.
    2. No output element is empty.
    3. No output element appears twice (deduplication).
    4. Output order matches first-occurrence order of inputs whose
       normalized form is non-empty (Requirement 4.2).
    5. ``_normalize_skip_entries(_normalize_skip_entries(raw)) ==
       _normalize_skip_entries(raw)`` (idempotency).
    """
    out = _normalize_skip_entries(raw)

    # (1) Every output element is strip()+lower() of some input.
    valid_norms = {s.strip().lower() for s in raw if s.strip() != ""}
    for el in out:
        assert el in valid_norms, (
            f"output element {el!r} not produced by strip+lower of any input.\n"
            f"  raw    : {raw!r}\n"
            f"  output : {out!r}"
        )
        # Also check it equals itself stripped+lowered (already-normalized form).
        assert el == el.strip().lower(), (
            f"output element {el!r} is not in normalized form (strip+lower)."
        )

    # (2) No empty element.
    assert "" not in out, (
        f"normalize must drop empties; got '' in output. raw={raw!r}, out={out!r}"
    )

    # (3) No duplicates.
    assert len(out) == len(set(out)), (
        f"normalize must dedupe; got duplicates in output. "
        f"raw={raw!r}, out={out!r}"
    )

    # (4) First-occurrence order preserved.
    expected_order = []
    seen = set()
    for s in raw:
        norm = s.strip().lower()
        if norm == "" or norm in seen:
            continue
        seen.add(norm)
        expected_order.append(norm)
    assert out == expected_order, (
        f"normalize must preserve first-occurrence order.\n"
        f"  raw      : {raw!r}\n"
        f"  expected : {expected_order!r}\n"
        f"  actual   : {out!r}"
    )

    # (5) Idempotent: applying twice equals applying once.
    twice = _normalize_skip_entries(out)
    assert twice == out, (
        f"normalize must be idempotent.\n"
        f"  once  : {out!r}\n"
        f"  twice : {twice!r}\n"
        f"  raw   : {raw!r}"
    )


# ---------------------------------------------------------------------------
# Example test — Requirement 4.3: only strip+lower+drop-empty+dedupe is
# applied; no other transform (e.g., punctuation stripping) leaks in.
# ---------------------------------------------------------------------------
def test_normalize_applies_only_strip_lower_dropempty_dedupe():
    """Validates: Requirement 4.3 (no other transforms apply).

    Feeding three forms of the same domain (one with extra whitespace, one
    with mixed case, one already canonical) must collapse to the single
    normalized form ``"a-b.com"`` — internal hyphen and dot are preserved
    because no character-stripping rule applies beyond ``.strip()`` (which
    only removes leading/trailing whitespace).
    """
    raw = ["a-b.com", "A-B.COM", "  a-b.com  "]
    out = _normalize_skip_entries(raw)
    assert out == ["a-b.com"], (
        f"normalize must apply only strip+lower+drop-empty+dedupe; "
        f"internal characters '-' and '.' must be preserved.\n"
        f"  raw    : {raw!r}\n"
        f"  expected: ['a-b.com']\n"
        f"  actual : {out!r}"
    )


def test_normalize_handles_empty_and_whitespace_only_inputs():
    """Sanity example: empty-string and whitespace-only entries are dropped
    (Requirement 4.1 'drop-empty' clause covers post-strip empties)."""
    raw = ["", "   ", "\t", "hotmail", " hotmail ", "HOTMAIL"]
    out = _normalize_skip_entries(raw)
    assert out == ["hotmail"], (
        f"normalize must drop empty/whitespace-only entries and dedupe "
        f"case/whitespace variants. raw={raw!r}, out={out!r}"
    )


def test_normalize_returns_empty_list_for_empty_input():
    """Sanity: empty input ⇒ empty output."""
    assert _normalize_skip_entries([]) == []
