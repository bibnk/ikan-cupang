"""
Fix-checking property tests for the spec ``centralize-imap-success-master``.

These tests encode the post-fix invariants from ``design.md`` §"Fix Checking"
and §"Property-Based Tests". They MUST all PASS on the fixed code in
``imap_engine.py``.

The six properties cover:

1. **Path invariant** — ``_update_imap_config`` writes to the master at the
   project root (``MASTER_IMAP_SUCCESS_PATH``); no per-job
   ``imap_success.json`` is created.
2. **Atomicity invariant** — Between every two ``_atomic_update_master``
   operations the on-disk master parses cleanly and contains all completed
   entries; no leftover ``imap_success.json.tmp`` after the sequence.
3. **Crash safety** — When ``os.replace`` fails (simulated ``OSError``) after
   the tmp file is written, the master file is byte-equal to the pre-call
   state and ``_update_imap_config`` does not re-raise.
4. **Cross-process safety** — Four ``multiprocessing.Process`` workers each
   insert 25 unique domains; the final master contains exactly 100 distinct
   domains and is valid JSON.
5. **Escape correctness** — ``_escape_imap_string(s)``, when wrapped in outer
   ``"..."``, yields an RFC 3501–valid quoted string for every generated
   ``s``; for ``s`` containing ``"`` or ``\\`` the result differs from ``s``.
6. **Merge correctness** — Pre-seeding the master with ``before`` and running
   an updater that adds every entry from a disjoint ``delta`` produces a
   master equal to ``before | delta``.

All tests redirect ``imap_engine.MASTER_IMAP_SUCCESS_PATH`` and
``imap_engine.MASTER_IMAP_SUCCESS_LOCK_PATH`` to a ``tmp_path``-scoped
location so the real ``imap_success.json`` at the project root is never
touched.

Validates: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8
"""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import sys

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

# Make the project root importable for child processes too.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


# ---------------------------------------------------------------------------
# RFC 3501 §4.3 quoted-string oracle (copied verbatim from
# tests/test_centralize_imap_success_master_bug.py so the two test files stay
# in sync without taking a runtime dependency on each other).
# ---------------------------------------------------------------------------
def is_valid_rfc3501_quoted(s: str) -> bool:
    """Return True iff *s* is a syntactically valid RFC 3501 quoted string.

    A quoted string is ``"`` ... ``"`` where every literal ``"`` and ``\\``
    inside the outer quotes is escaped with a leading backslash, and CR / LF
    are forbidden.
    """
    if len(s) < 2 or s[0] != '"' or s[-1] != '"':
        return False
    inner = s[1:-1]
    i = 0
    while i < len(inner):
        c = inner[i]
        if c in ("\r", "\n"):
            return False
        if c == "\\":
            if i + 1 >= len(inner):
                return False
            nxt = inner[i + 1]
            if nxt not in ("\\", '"'):
                return False
            i += 2
        elif c == '"':
            return False
        else:
            i += 1
    return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _redirect_master(tmp_path, monkeypatch):
    """Point ``imap_engine`` master/lock constants at *tmp_path*.

    Returns ``(master_path, lock_path)`` as strings.
    """
    import imap_engine

    master = str(tmp_path / "imap_success.json")
    lock = master + ".lock"
    monkeypatch.setattr(imap_engine, "MASTER_IMAP_SUCCESS_PATH", master)
    monkeypatch.setattr(imap_engine, "MASTER_IMAP_SUCCESS_LOCK_PATH", lock)
    return master, lock


def _reset_master(master_path: str) -> None:
    """Delete master + tmp + lock so each Hypothesis example starts clean."""
    for p in (master_path, master_path + ".tmp"):
        try:
            os.remove(p)
        except FileNotFoundError:
            pass


# Strategies shared by several tests.
domain_strategy = st.from_regex(
    r"[a-z][a-z0-9-]{0,15}\.(?:com|net|org|io)", fullmatch=True
)
config_strategy = st.fixed_dictionaries(
    {
        "server": st.from_regex(
            r"imap\.[a-z][a-z0-9-]{0,15}\.(?:com|net|org)", fullmatch=True
        ),
        "port": st.sampled_from([143, 993]),
        "ssl": st.booleans(),
    }
)


# ---------------------------------------------------------------------------
# Test 1 — Path invariant
# ---------------------------------------------------------------------------
@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(domain=domain_strategy, config_data=config_strategy)
def test_fix1_update_writes_master_and_no_per_job_file(
    tmp_path, monkeypatch, domain, config_data
):
    """After ``_update_imap_config`` the master at ``MASTER_IMAP_SUCCESS_PATH``
    exists, contains the new entry, and no ``imap_success.json`` is created
    inside ``jobs/{job_id}/``.

    Validates: 2.1, 2.2, 2.3, 2.5
    """
    master, _lock = _redirect_master(tmp_path, monkeypatch)
    _reset_master(master)

    import imap_engine

    results_dir = tmp_path / "jobs" / "fix1_job"
    checker = imap_engine.ImapChecker(
        job_id="fix1_job",
        results_dir=str(results_dir),
        accounts=[],
    )

    checker._update_imap_config(domain, config_data)

    # (a) Master exists at the redirected path.
    assert os.path.exists(master), (
        f"master file missing after _update_imap_config: {master!r}"
    )

    # (b) Master contains the entry.
    parsed = json.loads(open(master, "r", encoding="utf-8").read())
    assert parsed.get(domain) == config_data, (
        f"expected master[{domain!r}] == {config_data!r}, "
        f"got master={parsed!r}"
    )

    # (c) No per-job imap_success.json was created.
    per_job_path = results_dir / "imap_success.json"
    assert not per_job_path.exists(), (
        f"per-job imap_success.json must not exist, but found: {per_job_path}"
    )


# ---------------------------------------------------------------------------
# Test 2 — Atomicity invariant
# ---------------------------------------------------------------------------
@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    pairs=st.lists(
        st.tuples(domain_strategy, config_strategy),
        min_size=2,
        max_size=8,
        unique_by=lambda t: t[0],
    ),
)
def test_fix2_atomicity_invariant(tmp_path, monkeypatch, pairs):
    """Between any two ``_atomic_update_master`` calls the master file
    parses as valid JSON and contains every entry from operations completed
    so far. After the whole sequence, no ``.tmp`` file remains.

    Validates: 2.2, 2.4, 2.6
    """
    master, _lock = _redirect_master(tmp_path, monkeypatch)
    _reset_master(master)

    import imap_engine

    completed: dict = {}
    for domain, cfg in pairs:
        def updater(current, d=domain, c=cfg):
            current[d] = c
            return True

        imap_engine._atomic_update_master(updater)
        completed[domain] = cfg

        # Snapshot raw bytes immediately after the operation.
        raw = open(master, "rb").read()

        # (a) Snapshot parses as valid JSON.
        try:
            snapshot = json.loads(raw.decode("utf-8"))
        except Exception as exc:  # pragma: no cover — assertion message path
            pytest.fail(
                f"master file is not valid JSON after inserting {domain!r}: "
                f"{type(exc).__name__}: {exc}; raw[:200]={raw[:200]!r}"
            )

        # (b) Snapshot contains every completed entry.
        for k, v in completed.items():
            assert snapshot.get(k) == v, (
                f"after inserting {domain!r}, snapshot lost entry {k!r}: "
                f"expected {v!r}, got {snapshot.get(k)!r}"
            )

    # (c) No leftover tmp file after the sequence.
    tmp_file = master + ".tmp"
    assert not os.path.exists(tmp_file), (
        f"leftover tmp file must not exist: {tmp_file}"
    )


# ---------------------------------------------------------------------------
# Test 3 — Crash safety (os.replace failure)
# ---------------------------------------------------------------------------
def test_fix3_crash_safety_when_os_replace_fails(tmp_path, monkeypatch):
    """If ``os.replace`` raises after the tmp file has been written, the
    master file is byte-equal to its pre-call state and
    ``_update_imap_config`` does not re-raise (Failure Handling table:
    ``OSError`` is caught silently).

    Validates: 2.6
    """
    master, _lock = _redirect_master(tmp_path, monkeypatch)

    # Pre-seed master with 20 known entries.
    seeded = {
        f"d{i:02d}.example.com": {
            "server": f"imap.d{i:02d}.example.com",
            "port": 993,
            "ssl": True,
        }
        for i in range(20)
    }
    seeded_bytes = json.dumps(seeded, indent=2).encode("utf-8")
    with open(master, "wb") as f:
        f.write(seeded_bytes)

    # Patch os.replace to raise OSError. The patch fires AFTER the tmp file
    # has already been written and fsync'd by _atomic_update_master.
    real_replace = os.replace
    captured = {"tmp_existed_at_replace_time": None}

    def crashing_replace(src, dst, *args, **kwargs):
        # Confirm the tmp file is on disk at the moment of the simulated crash.
        captured["tmp_existed_at_replace_time"] = os.path.exists(src)
        raise OSError("simulated os.replace failure")

    monkeypatch.setattr(os, "replace", crashing_replace)

    import imap_engine

    results_dir = tmp_path / "jobs" / "crash_job"
    checker = imap_engine.ImapChecker(
        job_id="crash_job",
        results_dir=str(results_dir),
        accounts=[],
    )

    # Must NOT re-raise — Failure Handling says OSError is caught silently.
    checker._update_imap_config(
        "victim.com",
        {"server": "imap.victim.com", "port": 993, "ssl": True},
    )

    # The simulated crash should have fired with the tmp file present.
    assert captured["tmp_existed_at_replace_time"] is True, (
        "expected os.replace to be called with tmp_path on disk; "
        f"got tmp_existed_at_replace_time={captured['tmp_existed_at_replace_time']!r}"
    )

    # Restore os.replace before reading the file (defensive — read() would
    # not call replace, but we want the rest of the test to work normally).
    monkeypatch.setattr(os, "replace", real_replace)

    # Master must be byte-equal to pre-call state.
    after_bytes = open(master, "rb").read()
    assert after_bytes == seeded_bytes, (
        "master file changed despite os.replace failure.\n"
        f"  before bytes len: {len(seeded_bytes)}\n"
        f"  after  bytes len: {len(after_bytes)}\n"
        f"  before head: {seeded_bytes[:120]!r}\n"
        f"  after  head: {after_bytes[:120]!r}"
    )


# ---------------------------------------------------------------------------
# Test 4 — Cross-process safety
# ---------------------------------------------------------------------------
# Worker MUST be defined at module level so the spawn-based ``multiprocessing``
# protocol on Windows can import it from the test module.
def _fix4_child_worker(worker_id, master_path, lock_path, project_root):
    """Insert 25 unique domains via ``_update_imap_config`` from a child proc.

    The child rebinds ``imap_engine.MASTER_IMAP_SUCCESS_PATH`` and
    ``imap_engine.MASTER_IMAP_SUCCESS_LOCK_PATH`` so that the engine writes
    to the parent-supplied tmp_path, not the real master at the project root.
    """
    import sys as _sys

    if project_root not in _sys.path:
        _sys.path.insert(0, project_root)

    import imap_engine
    imap_engine.MASTER_IMAP_SUCCESS_PATH = master_path
    imap_engine.MASTER_IMAP_SUCCESS_LOCK_PATH = lock_path

    checker = imap_engine.ImapChecker(
        job_id=f"worker_{worker_id}",
        results_dir=os.path.dirname(master_path),
        accounts=[],
    )

    for i in range(25):
        domain = f"w{worker_id}-d{i:02d}.example.com"
        cfg = {
            "server": f"imap.{domain}",
            "port": 993,
            "ssl": True,
        }
        checker._update_imap_config(domain, cfg)


def test_fix4_cross_process_concurrent_updates(tmp_path):
    """4 worker processes × 25 unique domains each. After ``join()`` the
    master must contain exactly 100 distinct domains and parse as valid JSON
    (no lost updates).

    Validates: 2.4, 2.7
    """
    master = str(tmp_path / "imap_success.json")
    lock = master + ".lock"

    # Pre-seed master as empty dict.
    with open(master, "w", encoding="utf-8") as f:
        json.dump({}, f, indent=2)

    ctx = mp.get_context("spawn")
    procs = []
    N = 4
    for wid in range(N):
        p = ctx.Process(
            target=_fix4_child_worker,
            args=(wid, master, lock, PROJECT_ROOT),
        )
        p.start()
        procs.append(p)

    for p in procs:
        p.join(timeout=180)

    for wid, p in enumerate(procs):
        assert p.exitcode == 0, (
            f"worker {wid} exited with {p.exitcode}; "
            f"is_alive={p.is_alive()}"
        )

    raw = open(master, "rb").read()

    # Valid JSON.
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except Exception as exc:  # pragma: no cover
        pytest.fail(
            f"master is not valid JSON after concurrent updates: "
            f"{type(exc).__name__}: {exc}; raw[:200]={raw[:200]!r}"
        )

    # Exactly 100 distinct domains.
    assert isinstance(parsed, dict), (
        f"master must be a dict, got {type(parsed).__name__}"
    )
    assert len(parsed) == 100, (
        f"expected 100 entries (4 workers × 25), got {len(parsed)}: "
        f"sample keys={sorted(parsed.keys())[:5]}"
    )
    assert len(set(parsed.keys())) == 100, "expected 100 distinct keys"

    # Sanity: every (worker_id, i) pair is present.
    expected = {
        f"w{wid}-d{i:02d}.example.com" for wid in range(N) for i in range(25)
    }
    missing = expected - set(parsed.keys())
    assert not missing, f"missing {len(missing)} entries: {sorted(missing)[:5]}..."


# ---------------------------------------------------------------------------
# Test 5 — Escape correctness
# ---------------------------------------------------------------------------
@given(s=st.text(alphabet=st.characters(blacklist_characters="\r\n")))
@settings(max_examples=200, deadline=None)
def test_fix5_escape_yields_valid_rfc3501_quoted_string(s):
    """For every ``s`` (no CR/LF, since RFC 3501 quoted strings forbid them),
    ``_escape_imap_string(s)`` wrapped in outer ``"..."`` is a valid RFC 3501
    quoted string per the handwritten oracle.

    Validates: 2.8
    """
    from imap_engine import _escape_imap_string

    escaped = _escape_imap_string(s)
    quoted = f'"{escaped}"'
    assert is_valid_rfc3501_quoted(quoted), (
        f"escaped output is not a valid RFC 3501 quoted string.\n"
        f"  input s : {s!r}\n"
        f"  escaped : {escaped!r}\n"
        f"  quoted  : {quoted!r}"
    )


@given(
    s=st.text(
        alphabet=st.characters(blacklist_characters="\r\n"),
        min_size=1,
    ).filter(lambda x: ('"' in x) or ('\\' in x))
)
@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.filter_too_much],
)
def test_fix5_escape_modifies_special_chars(s):
    """Whenever ``s`` contains ``"`` or ``\\``, the escape function MUST
    change ``s`` (it is not a no-op).

    Validates: 2.8
    """
    from imap_engine import _escape_imap_string

    escaped = _escape_imap_string(s)
    assert escaped != s, (
        f"escape was a no-op for input containing special chars: s={s!r}"
    )


# ---------------------------------------------------------------------------
# Test 6 — Merge correctness
# ---------------------------------------------------------------------------
_before_key_strategy = st.from_regex(r"a-[a-z0-9]{1,8}\.com", fullmatch=True)
_delta_key_strategy = st.from_regex(r"b-[a-z0-9]{1,8}\.com", fullmatch=True)
_value_strategy = st.fixed_dictionaries(
    {
        "server": st.from_regex(
            r"imap\.[a-z][a-z0-9-]{0,10}\.(?:com|net)", fullmatch=True
        ),
        "port": st.sampled_from([143, 993]),
    }
)


@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    before=st.dictionaries(
        keys=_before_key_strategy,
        values=_value_strategy,
        min_size=1,
        max_size=6,
    ),
    delta=st.dictionaries(
        keys=_delta_key_strategy,
        values=_value_strategy,
        min_size=1,
        max_size=6,
    ),
)
def test_fix6_merge_preserves_before_and_adds_delta(
    tmp_path, monkeypatch, before, delta
):
    """Pre-seed master with ``before``, then run ``_atomic_update_master``
    with an updater that adds every entry from ``delta``. The resulting
    master must equal ``before | delta`` (key set union and per-key values).

    The two key-spaces are disjoint by construction (``a-...`` vs ``b-...``).

    Validates: 2.2, 2.4, 2.6
    """
    master, _lock = _redirect_master(tmp_path, monkeypatch)
    _reset_master(master)

    # Sanity: keys are disjoint.
    assert set(before.keys()).isdisjoint(set(delta.keys()))

    # Pre-seed master with `before`.
    with open(master, "w", encoding="utf-8") as f:
        json.dump(before, f, indent=2)

    import imap_engine

    def updater(current):
        changed = False
        for k, v in delta.items():
            if current.get(k) != v:
                current[k] = v
                changed = True
        return changed

    imap_engine._atomic_update_master(updater)

    parsed = json.loads(open(master, "r", encoding="utf-8").read())

    expected = {**before, **delta}

    # (a) Set-of-keys equality.
    assert set(parsed.keys()) == set(expected.keys()), (
        f"key-set mismatch.\n"
        f"  expected: {sorted(expected.keys())}\n"
        f"  got     : {sorted(parsed.keys())}"
    )

    # (b) Per-key value equality.
    for k, v in expected.items():
        assert parsed[k] == v, (
            f"value mismatch for key {k!r}: expected {v!r}, got {parsed[k]!r}"
        )
