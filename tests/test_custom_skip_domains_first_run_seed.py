"""
First-run seed example test (custom-skip-domains spec).

Validates that ``_load_skip_domains()`` seeds ``_DEFAULT_SKIP_SET`` byte-for-byte
on a missing file, returns the canonical default order, and does not rewrite
on subsequent reads (bridges to Property 4 / Requirement 2.2).

Validates: Requirements 2.1, 12.3
"""
from __future__ import annotations

import json
import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import imap_engine  # noqa: E402
from imap_engine import _DEFAULT_SKIP_SET, _load_skip_domains  # noqa: E402


EXPECTED_DEFAULT = ["hotmail", "live", "msn", "outlook", "yahoo", "interia", "poczta.fm"]


def _redirect_skip_paths(tmp_path, monkeypatch):
    skip_path = str(tmp_path / "skip_domains.json")
    monkeypatch.setattr(imap_engine, "SKIP_DOMAINS_PATH", skip_path)
    monkeypatch.setattr(imap_engine, "SKIP_DOMAINS_LOCK_PATH", skip_path + ".lock")
    return skip_path


def test_first_run_seeds_default_in_canonical_order(tmp_path, monkeypatch):
    """Validates: Requirements 2.1, 12.3.

    Asserts:
    1. ``_DEFAULT_SKIP_SET`` itself equals the canonical 7-entry list in
       documented order (Requirement 12.3 byte-level guarantee).
    2. Before the call, ``SKIP_DOMAINS_PATH`` does not exist.
    3. ``_load_skip_domains()`` returns the canonical list.
    4. After the call, the file exists at the redirected path.
    5. The file's raw bytes equal
       ``json.dumps(_DEFAULT_SKIP_SET, indent=2).encode("utf-8")``
       (Requirement 3.5 byte-format cross-check with the master spec).
    6. A second ``_load_skip_domains()`` returns the same list and does
       NOT rewrite the file (mtime unchanged) — bridges to Property 4.
    """
    skip_path = _redirect_skip_paths(tmp_path, monkeypatch)

    # (1) Sanity on the seed constant itself.
    assert list(_DEFAULT_SKIP_SET) == EXPECTED_DEFAULT, (
        f"_DEFAULT_SKIP_SET drifted from canonical order.\n"
        f"  expected: {EXPECTED_DEFAULT!r}\n"
        f"  actual  : {list(_DEFAULT_SKIP_SET)!r}"
    )
    assert isinstance(_DEFAULT_SKIP_SET, list), (
        f"_DEFAULT_SKIP_SET must be a list (for deterministic seed order); "
        f"got {type(_DEFAULT_SKIP_SET).__name__}"
    )


    # (2) File does not exist before the call.
    assert not os.path.exists(skip_path), (
        f"redirected skip-domains file must not exist pre-call: {skip_path}"
    )

    # (3) First call seeds default and returns it.
    first = _load_skip_domains()
    assert first == EXPECTED_DEFAULT, (
        f"_load_skip_domains() on missing file must return canonical default.\n"
        f"  expected: {EXPECTED_DEFAULT!r}\n  actual  : {first!r}"
    )

    # (4) File now exists.
    assert os.path.exists(skip_path), (
        f"first-run seed must create file at: {skip_path}"
    )

    # (5) Byte-format equivalence with json.dump(_DEFAULT_SKIP_SET, f,
    # indent=2) written in text mode (matches _load_skip_domains'
    # first-run seed pipeline). Compute expected bytes via the same
    # write pipeline so the comparison is platform-correct.
    expected_path = str(tmp_path / "_expected_seed.json")
    with open(expected_path, "w", encoding="utf-8") as f:
        json.dump(EXPECTED_DEFAULT, f, indent=2)
    with open(expected_path, "rb") as f:
        expected_bytes = f.read()

    raw = open(skip_path, "rb").read()
    assert raw == expected_bytes, (
        f"seeded file bytes do not match json.dump(_DEFAULT_SKIP_SET, "
        f"f, indent=2) text-mode write.\n"
        f"  expected: {expected_bytes!r}\n  actual  : {raw!r}"
    )

    # (6) Second call returns the same list and does NOT rewrite the file.
    pre_mtime = os.path.getmtime(skip_path)
    time.sleep(0.01)
    second = _load_skip_domains()
    post_mtime = os.path.getmtime(skip_path)
    assert second == EXPECTED_DEFAULT, (
        f"second _load_skip_domains() must return same list; got {second!r}"
    )
    assert post_mtime == pre_mtime, (
        f"second call must NOT rewrite the file (Property 4 bridge); "
        f"mtime changed {pre_mtime} → {post_mtime}"
    )
