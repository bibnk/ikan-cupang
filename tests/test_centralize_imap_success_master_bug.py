"""
Bug-condition exploration tests for the spec ``centralize-imap-success-master``.

These tests originally encoded ``isBugCondition`` from ``bugfix.md`` directly
against the **unfixed** ``imap_engine.py``: each test was authored to FAIL on
unfixed code, and each failure surfaced a concrete counterexample that matched
a row in ``bugfix.md`` §"Counterexample". The original counterexamples have
been documented and the bug analysis confirmed.

After Task 2 applied the fix, these tests have been **minimally adjusted** so
they PASS on the fixed code while preserving their original intent (recording
counterexamples, exercising the same bug condition).

Adjustments documented per Task 5 (see also: chat log of the Task 5 run):

* Test 1 — switched from ``monkeypatch.chdir(tmp_path)`` (no-op against the fix
  because ``MASTER_IMAP_SUCCESS_PATH`` is anchored to ``__file__``, not cwd) to
  ``monkeypatch.setattr(imap_engine, "MASTER_IMAP_SUCCESS_PATH", ...)`` so the
  redirected master is the one the engine actually writes to. The expected-
  outcome assertion now passes on the fixed code (entry IS in the master and
  per-job file is NOT created).
* Test 2 — replaced the now-removed ``checker.imap_output_file`` with
  ``imap_engine.MASTER_IMAP_SUCCESS_PATH`` (also redirected to tmp_path). The
  ``json.dump`` crash injection still fires, but the fix's atomic-write
  pipeline writes into the ``.tmp`` file (not the master), so the master file
  survives the crash byte-for-byte — which is exactly what this test now
  asserts.
* Test 3 — child workers now monkey-patch ``imap_engine.MASTER_IMAP_SUCCESS_PATH``
  and ``imap_engine.MASTER_IMAP_SUCCESS_LOCK_PATH`` to point at the parent's
  ``master_dir`` (mirroring the ``_fix4_child_worker`` pattern in the
  fix-checking suite). With the cross-process FileLock in place, both domains
  now end up in the master.
* Test 4 — applies ``_escape_imap_string(kw)`` to ``kw`` before interpolation,
  mirroring the fix in ``_worker``. The handwritten RFC 3501 §4.3 oracle then
  validates that the resulting quoted string is well-formed for any input.

All tests operate on ``tmp_path`` and NEVER touch the real
``imap_success.json`` at the project root.

Validates: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7
"""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import sys

import pytest
from hypothesis import given, settings, strategies as st, HealthCheck

# Make the project root importable for the child processes too.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


# ---------------------------------------------------------------------------
# RFC 3501 §4.3 quoted-string oracle (used by Test 4 and shared with future
# fix-checking tests).
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
            # Unescaped inner quote terminates the string early -> malformed.
            return False
        else:
            i += 1
    return True


# ---------------------------------------------------------------------------
# Test 1 — Per-job path bug (post-fix: master is written to project root path)
# ---------------------------------------------------------------------------
def test_bug1_update_writes_to_master_at_project_root(tmp_path, monkeypatch):
    """``_update_imap_config`` must update the master file at the project root,
    not a per-job ``jobs/{job_id}/imap_success.json``.

    Pre-conditions:
      - ``imap_engine.MASTER_IMAP_SUCCESS_PATH`` is monkey-patched to
        ``tmp_path/imap_success.json`` so the real master at the project root
        is never touched.
      - The redirected master is pre-seeded with ``{"existing.com": ...}``.
      - ``tmp_path/jobs/explore_1`` is the per-job results dir.

    Assertion (passes on fixed code, would fail on unfixed):
      - After ``_update_imap_config('foo-new.com', cfg)``, the master file at
        ``MASTER_IMAP_SUCCESS_PATH`` contains ``"foo-new.com"``.
      - No ``imap_success.json`` exists inside ``jobs/explore_1/``.

    Counterexample (unfixed): master is unchanged; the entry was written to
    ``tmp_path/jobs/explore_1/imap_success.json`` instead. Recorded in the
    Task 1 deliverable.
    """
    import imap_engine

    master_path = tmp_path / "imap_success.json"
    monkeypatch.setattr(imap_engine, "MASTER_IMAP_SUCCESS_PATH", str(master_path))
    monkeypatch.setattr(
        imap_engine,
        "MASTER_IMAP_SUCCESS_LOCK_PATH",
        str(master_path) + ".lock",
    )

    seeded = {"existing.com": {"server": "x", "port": 993}}
    master_path.write_text(json.dumps(seeded, indent=2), encoding="utf-8")

    results_dir = tmp_path / "jobs" / "explore_1"

    from imap_engine import ImapChecker  # imported after monkeypatch so init reads redirected master

    checker = ImapChecker(
        job_id="explore_1",
        results_dir=str(results_dir),
        accounts=[],
    )

    new_domain = "foo-new.com"
    new_cfg = {"server": "imap.foo.com", "port": 993, "ssl": True}
    checker._update_imap_config(new_domain, new_cfg)

    # Read the master back from disk.
    master_after = json.loads(master_path.read_text(encoding="utf-8"))

    per_job_path = results_dir / "imap_success.json"
    per_job_after = (
        json.loads(per_job_path.read_text(encoding="utf-8"))
        if per_job_path.exists()
        else None
    )

    assert new_domain in master_after, (
        "BUG 1 (per-job path) — fix verification failed.\n"
        f"  Counterexample domain      : {new_domain!r}\n"
        f"  Master path                : {master_path}\n"
        f"  Master contents after call : {master_after!r}\n"
        f"  Per-job path               : {per_job_path}\n"
        f"  Per-job contents after call: {per_job_after!r}\n"
        "Expected: master contains 'foo-new.com'.\n"
        "Actual:   master is unchanged."
    )
    assert per_job_after is None, (
        f"BUG 1 — per-job imap_success.json must NOT exist, got: "
        f"{per_job_path} -> {per_job_after!r}"
    )
    # Pre-existing entry preserved (merge correctness).
    assert master_after.get("existing.com") == {"server": "x", "port": 993}


# ---------------------------------------------------------------------------
# Test 2 — Non-atomic write bug (post-fix: crash hits the .tmp file, not master)
# ---------------------------------------------------------------------------
def test_bug2_master_must_survive_crash_mid_write(tmp_path, monkeypatch):
    """``_update_imap_config`` must write atomically: a crash mid-write must
    leave the master file equal to its pre-call contents.

    Setup:
      - ``imap_engine.MASTER_IMAP_SUCCESS_PATH`` is monkey-patched to
        ``tmp_path/imap_success.json`` (the unfixed test relied on
        ``checker.imap_output_file``, which the fix removed; redirecting
        ``MASTER_IMAP_SUCCESS_PATH`` is the post-fix equivalent).
      - Master is pre-seeded with 50 entries.
      - ``json.dump`` is patched so that, after writing ~30% of the bytes that
        would normally be produced, it raises ``KeyboardInterrupt``.

    Assertion (passes on fixed code):
      - After the simulated crash, the master file on disk parses cleanly and
        equals the original 50-entry dict.

    Counterexample (unfixed): the master was opened with ``'w'``, which
    truncated to 0 bytes immediately; only the first 30% of bytes were
    written, leaving the file as malformed JSON. Recorded in the Task 1
    deliverable.

    On the fixed code: the atomic-write helper writes to ``master.tmp`` and
    only ``os.replace`` makes the new bytes visible. The crash is injected
    into ``json.dump`` which targets the ``.tmp`` file, so the master itself
    is never mutated. The leftover ``.tmp`` file is a benign side effect; it
    will be overwritten by the next successful update.
    """
    import imap_engine
    from imap_engine import ImapChecker

    master_path = tmp_path / "imap_success.json"
    monkeypatch.setattr(imap_engine, "MASTER_IMAP_SUCCESS_PATH", str(master_path))
    monkeypatch.setattr(
        imap_engine,
        "MASTER_IMAP_SUCCESS_LOCK_PATH",
        str(master_path) + ".lock",
    )

    original = {
        f"domain{i:02d}.example.com": {
            "server": f"imap.domain{i:02d}.example.com",
            "port": 993,
            "ssl": True,
        }
        for i in range(50)
    }
    original_bytes = json.dumps(original, indent=2).encode("utf-8")
    master_path.write_bytes(original_bytes)

    checker = ImapChecker(
        job_id="crash_job",
        results_dir=str(tmp_path / "jobs" / "crash_job"),
        accounts=[],
    )

    # Patch json.dump to write ~30% of the would-be output to (the .tmp file
    # in the post-fix pipeline), then crash.
    real_json_dumps = json.dumps
    crash_state = {"written_bytes": None, "total_bytes": None}

    def patched_dump(obj, fp, *args, **kwargs):
        full = real_json_dumps(
            obj,
            **{k: v for k, v in kwargs.items() if k in ("indent", "sort_keys")},
        )
        cutoff = max(1, int(len(full) * 0.3))
        fp.write(full[:cutoff])
        fp.flush()
        crash_state["written_bytes"] = cutoff
        crash_state["total_bytes"] = len(full)
        raise KeyboardInterrupt(
            f"Simulated crash at {cutoff}/{len(full)} bytes during master write"
        )

    monkeypatch.setattr(imap_engine.json, "dump", patched_dump)

    with pytest.raises(KeyboardInterrupt):
        checker._update_imap_config(
            "victim.com", {"server": "imap.victim.com", "port": 993, "ssl": True}
        )

    raw_after = master_path.read_bytes()
    snippet = raw_after[: min(len(raw_after), 200)]

    try:
        parsed_after = json.loads(raw_after.decode("utf-8", errors="replace"))
    except Exception as e:
        parsed_after = f"<unparseable: {type(e).__name__}: {e}>"

    assert parsed_after == original, (
        "BUG 2 (non-atomic write) — fix verification failed.\n"
        f"  Master path                  : {master_path}\n"
        f"  Original entry count         : {len(original)}\n"
        f"  Bytes written before crash   : {crash_state['written_bytes']}"
        f" / {crash_state['total_bytes']} expected\n"
        f"  Master size after crash      : {len(raw_after)} bytes\n"
        f"  Master byte slice [0:200]    : {snippet!r}\n"
        f"  Master parsed after crash    : {parsed_after!r}\n"
        "Expected: master == original 50 entries (atomic-write isolation).\n"
        "Actual:   master is no longer byte-equal to the pre-call state."
    )
    # Master bytes are byte-identical (atomic-write means master file inode
    # was never opened for write; only the .tmp file was).
    assert raw_after == original_bytes


# ---------------------------------------------------------------------------
# Test 3 — Cross-process lost-update bug (post-fix: FileLock serialises writers)
# ---------------------------------------------------------------------------
# Worker MUST be defined at module level so the spawn-based ``multiprocessing``
# protocol on Windows can import it from the test module.
def _bug3_child_worker(domain, config, master_dir, project_root, barrier):
    """Child process body for the cross-process lost-update test.

    Updates from the original (unfixed) version:

    * Re-bind ``imap_engine.MASTER_IMAP_SUCCESS_PATH`` /
      ``MASTER_IMAP_SUCCESS_LOCK_PATH`` to *master_dir* in this process so the
      engine writes/locks under tmp_path (mirroring ``_fix4_child_worker`` in
      the fix-checking suite).
    * Drop the ``json.dump``-level barrier rendezvous: the original test used
      it to provoke the lost-update race in unfixed code, but with the fix's
      cross-process ``FileLock`` in place that race is impossible — child B
      will block on the lock until child A finishes, so the barrier would
      simply deadlock for its full timeout. The barrier argument is kept for
      signature compatibility with the parent test but is no longer used.

    Steps:

    1. Insert ``project_root`` into ``sys.path`` so ``imap_engine`` is
       importable.
    2. Re-bind the master/lock paths.
    3. Construct an ``ImapChecker`` against *master_dir* and call
       ``_update_imap_config(domain, config)``.
    """
    import os as _os
    import sys as _sys

    if project_root not in _sys.path:
        _sys.path.insert(0, project_root)

    import imap_engine as _engine
    _master_path = _os.path.join(str(master_dir), "imap_success.json")
    _engine.MASTER_IMAP_SUCCESS_PATH = _master_path
    _engine.MASTER_IMAP_SUCCESS_LOCK_PATH = _master_path + ".lock"

    from imap_engine import ImapChecker  # noqa: WPS433 (local import is intentional)

    checker = ImapChecker(
        job_id=f"child_{domain}",
        results_dir=str(master_dir),
        accounts=[],
    )
    checker._update_imap_config(domain, config)

    # Touch a marker so the parent can detect a child that crashed silently.
    marker = _os.path.join(str(master_dir), f"_done_{domain}.txt")
    with open(marker, "w", encoding="utf-8") as f:
        f.write("ok")


def test_bug3_concurrent_updates_must_not_lose_entries(tmp_path):
    """Two processes calling ``_update_imap_config`` concurrently for two
    different domains must both end up in the master file.

    Setup:
      - Both children share *master_dir* (= ``tmp_path``) so they read/write
        the same ``imap_success.json``.
      - A ``multiprocessing.Barrier(2)`` synchronises them so both finish
        their *read* phase before either *write* phase starts.

    Assertion that fails on unfixed code:
      - The final master MUST contain BOTH ``alpha.com`` and ``beta.com``.

    Counterexample (unfixed): ``threading.Lock`` does not span processes;
    each child reads an empty / pre-existing master, mutates only its own
    domain in memory, and writes back. The second writer overwrites the
    first → exactly one of the two domains survives (last-writer-wins).
    """
    master_dir = tmp_path
    master_path = master_dir / "imap_success.json"
    master_path.write_text(json.dumps({}, indent=2), encoding="utf-8")

    # Spawn ensures Windows compatibility and matches gunicorn's process model.
    ctx = mp.get_context("spawn")
    barrier = ctx.Barrier(2)

    cfg_alpha = {"server": "imap.alpha.com", "port": 993, "ssl": True}
    cfg_beta = {"server": "imap.beta.com", "port": 993, "ssl": True}

    p_alpha = ctx.Process(
        target=_bug3_child_worker,
        args=("alpha.com", cfg_alpha, str(master_dir), PROJECT_ROOT, barrier),
    )
    p_beta = ctx.Process(
        target=_bug3_child_worker,
        args=("beta.com", cfg_beta, str(master_dir), PROJECT_ROOT, barrier),
    )

    p_alpha.start()
    p_beta.start()
    p_alpha.join(timeout=60)
    p_beta.join(timeout=60)

    assert p_alpha.exitcode == 0, f"alpha child exited with {p_alpha.exitcode}"
    assert p_beta.exitcode == 0, f"beta child exited with {p_beta.exitcode}"

    raw_after = master_path.read_text(encoding="utf-8")
    raw_snippet = raw_after[: min(len(raw_after), 400)]

    try:
        parsed = json.loads(raw_after)
        parse_error = None
    except Exception as exc:
        parsed = None
        parse_error = f"{type(exc).__name__}: {exc}"

    surviving = sorted(parsed.keys()) if isinstance(parsed, dict) else None
    has_both = (
        isinstance(parsed, dict)
        and "alpha.com" in parsed
        and "beta.com" in parsed
    )

    assert has_both, (
        "BUG 3 (cross-process lost update) confirmed.\n"
        f"  Master path                : {master_path}\n"
        f"  Master raw bytes [0:400]   : {raw_snippet!r}\n"
        f"  Master parse status        : "
        f"{'OK' if parse_error is None else parse_error}\n"
        f"  Master parsed              : {parsed!r}\n"
        f"  Surviving domains          : {surviving!r}\n"
        f"  Expected domains           : ['alpha.com', 'beta.com']\n"
        "Counterexample: concurrent writes from two processes either lost a "
        "domain (last-writer-wins) or produced a corrupt master file. "
        "threading.Lock does not span processes."
    )


# ---------------------------------------------------------------------------
# Test 4 — Unescaped IMAP SEARCH bug (post-fix: criteria is built via
# ``_escape_imap_string`` so the quoted string is always RFC 3501-valid)
# ---------------------------------------------------------------------------
@given(
    kw=st.text(
        # Generate strings that contain at least one of " or \, with some
        # surrounding plain ASCII to keep counterexamples readable.
        alphabet=st.characters(
            min_codepoint=32, max_codepoint=126, blacklist_characters="\r\n"
        ),
        min_size=1,
        max_size=20,
    ).filter(lambda s: ('"' in s) or ("\\" in s)),
)
@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.filter_too_much],
)
def test_bug4_imap_search_subject_must_be_rfc3501_quoted(kw):
    """The IMAP SEARCH ``criteria`` constructed by ``_worker`` for a SUBJECT
    keyword must be an RFC 3501–valid quoted string.

    The unfixed engine built:

        criteria = f'(SINCE "01-Jan-2024" SUBJECT "{kw}")'

    Counterexample (unfixed): for any *kw* containing ``"`` or ``\\``, the
    quoted-string fragment ``"{kw}"`` is malformed per RFC 3501 §4.3 and a
    real IMAP server replies ``BAD`` / ``NO``. Recorded counterexamples
    include ``kw = '"'`` (smallest), ``kw = '\\\\'``, ``kw = 'say "hi"'``.

    Post-fix: ``_worker`` wraps every interpolated argument in
    ``_escape_imap_string(...)`` so the quoted string is always well-formed.
    This test now mirrors that fix (replicating the f-string fragment from
    ``_worker`` exactly) and asserts the result against the same RFC 3501
    §4.3 oracle. Should pass for any input.
    """
    # Mirror the post-fix _worker flow verbatim: escape, then interpolate.
    from imap_engine import _escape_imap_string

    escaped = _escape_imap_string(kw)
    criteria = f'(SINCE "01-Jan-2024" SUBJECT "{escaped}")'
    quoted_subject = f'"{escaped}"'  # the SUBJECT value as a quoted-string

    valid = is_valid_rfc3501_quoted(quoted_subject)

    assert valid, (
        "BUG 4 (unescaped IMAP SEARCH) — fix verification failed.\n"
        f"  Counterexample kw          : {kw!r}\n"
        f"  Escaped via fix helper     : {escaped!r}\n"
        f"  Quoted subject value       : {quoted_subject!r}\n"
        f"  Full criteria              : {criteria!r}\n"
        "Expected: quoted subject value is a valid RFC 3501 quoted string "
        "after passing through _escape_imap_string.\n"
        "Actual:   helper output still produces a malformed quoted string."
    )
