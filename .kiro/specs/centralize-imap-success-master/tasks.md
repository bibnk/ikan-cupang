# Implementation Plan

## Overview

Bugfix workflow yang memperbaiki tiga cacat saling-terkait di `imap_engine.py`:
sentralisasi `imap_success.json` ke project root, penulisan atomik + cross-process
lock untuk master, dan escaping argumen IMAP `SEARCH` per RFC 3501. Eksekusi
mengikuti pola bug-condition-driven:

1. Tulis test eksplorasi yang **harus gagal** pada kode unfixed untuk
   mengonfirmasi keempat counterexample di `bugfix.md`.
2. Implementasikan fix di `imap_engine.py` (helper baru, refactor method, escape
   call site) sesuai `design.md` §"Fix Implementation".
3. Tulis property-based fix-checking tests yang **harus lulus** pada kode fixed.
4. Tulis property-based preservation tests yang **harus lulus** pada kode fixed.
5. Jalankan seluruh suite untuk validasi akhir.

## Task Dependency Graph

```json
{
  "waves": [
    {
      "wave": 1,
      "tasks": ["1"],
      "description": "Bug exploration tests against unfixed code"
    },
    {
      "wave": 2,
      "tasks": ["2.1"],
      "description": "Foundation: constants + filelock import"
    },
    {
      "wave": 3,
      "tasks": ["2.2", "2.3", "2.4", "2.8"],
      "description": "Independent helpers and cleanup that only depend on 2.1"
    },
    {
      "wave": 4,
      "tasks": ["2.5", "2.7"],
      "description": "Method redirect (needs 2.4) and SEARCH escape call sites (needs 2.3)"
    },
    {
      "wave": 5,
      "tasks": ["2.6"],
      "description": "Refactor _update_imap_config (needs 2.2 + 2.5)"
    },
    {
      "wave": 6,
      "tasks": ["3", "4"],
      "description": "Fix-checking and preservation property tests"
    },
    {
      "wave": 7,
      "tasks": ["5"],
      "description": "Final integration validation"
    }
  ]
}
```

Reasoning:
- Task 1 (bug exploration) is independent of any implementation.
- Task 2.1 is the foundation (constants + import) for every 2.x subtask.
- Tasks 2.2 / 2.3 / 2.4 / 2.8 only depend on 2.1 and can run in parallel.
- Task 2.5 needs 2.4 (path constant + cleaned `__init__`); Task 2.7 needs 2.3 (escape helper).
- Task 2.6 (refactor `_update_imap_config`) needs the atomic helper (2.2) and the load redirect (2.5).
- Tasks 3 and 4 (PBT) need the full implementation in place.
- Task 5 is the final validation sign-off.

## Tasks

- [x] 1. Write bug condition exploration property test
  - File: `tests/test_centralize_imap_success_master_bug.py` (new)
  - Implement four property-based tests (using `pytest` + `hypothesis`) that encode `isBugCondition` from `bugfix.md` directly against the **current unfixed** `imap_engine.py`. Each test MUST fail on the unfixed code, and each failure MUST surface a concrete counterexample matching the cases in `bugfix.md` §"Counterexample".
    1. **Per-job path bug** — Construct `ImapChecker(job_id="explore_1", results_dir="jobs/explore_1", accounts=[...])` in a `tmp_path`-scoped working directory that contains a pre-seeded master `imap_success.json` (e.g. `{"existing.com": {"server": "x", "port": 993}}`). Call `checker._update_imap_config("foo-new.com", {"server": "imap.foo.com", "port": 993, "ssl": True})`. Assert that the master `imap_success.json` in the project root **does** contain `"foo-new.com"`. (Will FAIL on unfixed because the entry was written to `jobs/explore_1/imap_success.json` instead.)
    2. **Non-atomic write bug** — Pre-seed a master with 50 entries. Monkey-patch `json.dump` so that after writing approximately 30% of the bytes it raises `KeyboardInterrupt`. Invoke `_update_imap_config("victim.com", {...})` inside `pytest.raises(KeyboardInterrupt)`. After the simulated crash, read the master file from disk and assert it parses as valid JSON whose contents equal the original 50 entries (i.e. master untouched). (Will FAIL on unfixed because direct `open(..., 'w')` truncated the file before `json.dump` finished.)
    3. **Cross-process lost-update bug** — Use `multiprocessing.Process` to spawn 2 child processes (each constructs its own `ImapChecker` against the same master path) that simultaneously call `_update_imap_config` for two different domains (`"alpha.com"`, `"beta.com"`) with a `multiprocessing.Barrier(2)` so both reach the read step before either writes. After both `join()`, assert the master file contains BOTH domains. (Will FAIL on unfixed because `threading.Lock` does not span processes — last writer wins.)
    4. **Unescaped IMAP SEARCH bug** — Use Hypothesis to generate strings that contain at least one of `"` or `\`. For each generated `kw`, build `criteria = f'(SINCE "01-Jan-2024" SUBJECT "{kw}")'` exactly the way `_worker` does today, then assert the resulting string is a syntactically valid IMAP quoted string per RFC 3501 §4.3 (i.e. inside the outer quotes every literal `\` and `"` is escaped with `\`). Use a small handwritten validator as the oracle. (Will FAIL on unfixed because raw `kw` is interpolated without escaping.)
  - Each assertion failure message MUST include the counterexample (domain name, byte slice, generated string) so the orchestrator can record it.
  - **Expected outcome on unfixed code: TEST FAILS — failure confirms all four bugs exist and matches the analysis in `bugfix.md`.**
  - _Validates: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7_

- [x] 2. Implement the fix in `imap_engine.py`

- [x] 2.1 Add module-level constants and `filelock` import
  - File: `imap_engine.py`
  - At the top of the file (alongside existing imports), add `from filelock import FileLock, Timeout`.
  - Below the existing constants block (after `DOMAINS_TO_SKIP_KEYWORDS`), add:
    - `MASTER_IMAP_SUCCESS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "imap_success.json")`
    - `MASTER_IMAP_SUCCESS_LOCK_PATH = MASTER_IMAP_SUCCESS_PATH + ".lock"`
    - `MASTER_LOCK_TIMEOUT_SECONDS = 30`
  - These names match the design glossary so other modules / tests can import them.
  - _Validates: 2.1, 2.3_
  - _Depends on: 1_

- [x] 2.2 Add `_atomic_update_master(updater_fn)` module-level helper
  - File: `imap_engine.py`
  - Implement the helper exactly as specified in `design.md` §Architecture > "Atomic Update Helper":
    - Acquire `FileLock(MASTER_IMAP_SUCCESS_LOCK_PATH, timeout=MASTER_LOCK_TIMEOUT_SECONDS)`.
    - Load current master with the same `try/except → {}` fallback used by `_load_imap_success_config`.
    - Call `updater_fn(current_dict)` which mutates in place and returns `True` iff something changed; if it returns `False`, return `False` immediately (idempotent skip per 3.3).
    - Write the dict to `MASTER_IMAP_SUCCESS_PATH + ".tmp"` with `json.dump(current, f, indent=2)`, then `f.flush()` + `os.fsync(f.fileno())`.
    - Call `os.replace(tmp_path, MASTER_IMAP_SUCCESS_PATH)` for the atomic rename.
    - Return `True`.
  - Use UTF-8 encoding everywhere. Do not catch `Timeout` or `OSError` here — propagate them so callers can decide. Place the helper above the `ImapChecker` class definition.
  - _Validates: 2.2, 2.4, 2.6, 2.7, 3.4, 3.11_
  - _Depends on: 2.1_

- [x] 2.3 Add `_escape_imap_string(s)` module-level helper
  - File: `imap_engine.py`
  - Implement `def _escape_imap_string(s: str) -> str: return s.replace("\\", "\\\\").replace('"', '\\"')`. The `\\` replacement MUST come before the `"` replacement so that an input `\"` is encoded as `\\\"` and not `\\\\\"`. Place the helper next to `_atomic_update_master`.
  - _Validates: 2.8, 3.9, 3.10_
  - _Depends on: 2.1_

- [x] 2.4 Remove per-job `imap_output_file` from `ImapChecker.__init__`
  - File: `imap_engine.py`
  - Delete the line `self.imap_output_file = os.path.join(self.results_dir, "imap_success.json")` (currently around line 181).
  - Keep `self.imap_config_lock = threading.Lock()` and the call `self.imap_success_config = self._load_imap_success_config()` exactly as they are.
  - All other per-job file paths (`live_file`, `noemail_file`, `die_file`, `unreg_file`, `domain_skip_file`) stay unchanged (3.1).
  - _Validates: 2.1, 3.1_
  - _Depends on: 2.1_

- [x] 2.5 Redirect `_load_imap_success_config` to the master path
  - File: `imap_engine.py`
  - In `ImapChecker._load_imap_success_config`, replace every reference to `self.imap_output_file` with `MASTER_IMAP_SUCCESS_PATH`. Keep the existing `try/except → return {}` fallback unchanged so a missing or corrupt master file still yields `{}` (3.5 normal case + corrupt edge case from Failure Handling).
  - _Validates: 2.3, 3.5_
  - _Depends on: 2.1, 2.4_

- [x] 2.6 Refactor `_update_imap_config` to use atomic + cross-process safe write
  - File: `imap_engine.py`
  - Inside `with self.imap_config_lock:`, define a local `updater(current)` that returns `False` when `current.get(domain) == config_data` (idempotent skip per 3.3) and otherwise sets `current[domain] = config_data` and returns `True`.
  - Call `changed = _atomic_update_master(updater)` wrapped in `try / except Timeout / except OSError`. On either exception, write a one-line message to `stderr` (using `print(..., file=sys.stderr)` to match the existing module style) and `return` — do not re-raise (Failure Handling table).
  - When `changed` is `True`, update the in-memory cache via `self.imap_success_config[domain] = config_data` so other threads in the same process see the new entry without re-reading the disk.
  - Remove every reference to `self.imap_output_file` from this method.
  - _Validates: 2.2, 2.4, 2.6, 2.7, 3.3, 3.7, 3.12_
  - _Depends on: 2.1, 2.2, 2.4_

- [x] 2.7 Escape `FROM` and `SUBJECT` arguments in `_worker`
  - File: `imap_engine.py`
  - In `ImapChecker._worker`, modify the two existing call sites (currently around lines 498 and 504):
    - `criteria = f'(SINCE "{self.search_since_date}" FROM "{_escape_imap_string(sender)}")'`
    - `criteria = f'(SINCE "{self.search_since_date}" SUBJECT "{_escape_imap_string(kw)}")'`
  - Do not change `self.search_since_date` interpolation (format `%d-%b-%Y` is already safe). Do not introduce additional searches.
  - _Validates: 2.8, 3.9, 3.10_
  - _Depends on: 2.3_

- [x] 2.8 Add `filelock` to `requirements.txt`
  - File: `requirements.txt`
  - Append a line `filelock>=3.0` (preserving existing pins). Re-run `pip install -r requirements.txt` in the dev environment so subsequent test tasks can import the library.
  - _Validates: 2.6, 2.7_
  - _Depends on: 2.1_

- [x] 3. Write fix-checking property tests
  - File: `tests/test_centralize_imap_success_master_fix.py` (new)
  - Implement Hypothesis-driven property tests that encode the post-fix invariants from `design.md` §"Fix Checking" and §"Property-Based Tests":
    1. **Path invariant** — For any randomly generated `domain` / `config_data`, after `_update_imap_config` returns successfully, `os.path.exists(MASTER_IMAP_SUCCESS_PATH)` is true and the entry is present in the master. No file named `imap_success.json` exists inside `jobs/{job_id}/`.
    2. **Atomicity invariant** — Generate a random sequence of `(domain, config)` pairs. Run them sequentially through `_atomic_update_master`. Between every two operations, snapshot the raw bytes of the master file and assert: (a) `json.loads(snapshot)` succeeds, (b) the parsed dict contains every entry from operations completed so far. After the whole sequence, assert no `imap_success.json.tmp` file remains in the master directory.
    3. **Crash safety** — Use `monkeypatch` on `os.replace` to raise `OSError` after `tmp_path` is written but before rename. Confirm the master file on disk still equals the pre-call state (byte-equal) and that `_update_imap_config` returns without re-raising.
    4. **Cross-process safety** — Spawn N=4 `multiprocessing.Process` workers. Each one constructs its own `ImapChecker` and inserts 25 unique domains via `_update_imap_config`. After `join()`, assert the master contains exactly 100 distinct domains and is valid JSON.
    5. **Escape correctness** — `@given(st.text())` assert that for every generated `s`, `_escape_imap_string(s)` produces a string that, when wrapped in outer `"..."`, is a valid RFC 3501 quoted string per the small handwritten validator from Task 1. Additionally for every `s` containing `"` or `\`, assert the result differs from `s`.
    6. **Merge correctness** — Generate two disjoint dicts `before` and `delta`. Pre-seed master with `before`, run `_atomic_update_master` with an updater that adds every entry from `delta`. Assert resulting master equals `before | delta` (set-of-keys equality and per-key value equality).
  - **Expected outcome: ALL TESTS PASS on the fixed code.**
  - _Validates: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8_
  - _Depends on: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8_

- [x] 4. Write preservation property tests
  - File: `tests/test_centralize_imap_success_master_preservation.py` (new)
  - Encode the invariants from `design.md` §"Preservation Checking":
    1. **JSON format identical** — Generate a random `dict[str, dict]` with keys/values matching the master schema (`{server, port, ssl?}`). Write it via `_atomic_update_master` and read the raw bytes back. Assert the bytes equal `json.dumps(d, indent=2).encode("utf-8")` (preserving 3.4, 3.11).
    2. **Idempotent skip** — Pre-seed master with `{"d.com": cfg}`. Capture `os.stat(MASTER_IMAP_SUCCESS_PATH).st_mtime_ns`. Call `_update_imap_config("d.com", cfg)` again. Assert the file's mtime is unchanged AND `_atomic_update_master` returned `False` for the inner updater (use a spy to confirm `os.replace` was never invoked) (3.3).
    3. **Per-job files unchanged** — Drive a small in-process job through `ImapChecker.run()` with mocked IMAP connections that return canned responses, and assert `live.txt`, `noemail.txt`, `die.txt`, `unreg.txt`, `domain_skipped.txt` exist in `jobs/{job_id}/` with the same byte format as a baseline captured from the pre-fix code. Also assert `jobs/{job_id}/imap_success.json` does NOT exist (3.1).
    4. **API contract unchanged** — Use Flask's test client to hit `/api/check`, `/api/status/<job_id>`, `/api/jobs`. Assert the JSON response keys are identical to a snapshot captured pre-fix (3.6).
    5. **SEARCH no-op for safe input** — `@given(st.text(alphabet=st.characters(blacklist_characters='"\\\\')))` assert `_escape_imap_string(s) == s` (3.9).
    6. **Lookup order unchanged** — With monkey-patched `DEFAULT_IMAP_CONFIG`, cached `imap_success_config`, and `_try_imap_variants`, drive `_worker` for a fixed domain set and assert the lookup sequence (recorded via spies) is identical to the pre-fix order: `DEFAULT_IMAP_CONFIG → cached → wildcard *.rr.com → auto-discovery` (3.2).
    7. **Empty target_senders / keywords** — Assert that running a job with empty `target_senders` and empty `keywords` issues exactly the same number of `mail_conn.search` calls as the pre-fix code (recorded count == 0 extra) (3.10).
    8. **Master corrupt fallback** — Pre-seed master with truncated JSON bytes. Construct a fresh `ImapChecker`. Assert `self.imap_success_config == {}` and that the next `_update_imap_config` succeeds (3.5 corrupt edge case from Failure Handling).
  - **Expected outcome: ALL TESTS PASS on the fixed code.**
  - _Validates: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.11, 3.12, 3.13_
  - _Depends on: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8_

- [x] 5. Run the full test suite and confirm all checks pass
  - Run `pytest tests/test_centralize_imap_success_master_bug.py tests/test_centralize_imap_success_master_fix.py tests/test_centralize_imap_success_master_preservation.py -v`. Confirm:
    - The bug exploration test from Task 1 now PASSES on the fixed code (its assertions assumed the bug; flip / re-check semantics are described inside that file — for fix-checking we instead rely on Tasks 3 and 4 and simply confirm Task 1 still surfaces the documented counterexamples when run against a temporarily reverted helper).
    - All Task 3 and Task 4 tests pass.
  - Then run the existing project test suite (if any) plus a smoke `python -c "from imap_engine import ImapChecker, MASTER_IMAP_SUCCESS_PATH, _atomic_update_master, _escape_imap_string"` to confirm the module still imports cleanly.
  - Finally, execute a manual sanity check: start `app.py`, submit a tiny job through `/api/check`, and verify the project-root `imap_success.json` is the only `imap_success.json` written (no `jobs/{job_id}/imap_success.json` is created).
  - _Validates: all of 2.1–2.8 and 3.1–3.13 (final integration sign-off)_
  - _Depends on: 3, 4_

## Notes

- **Bug exploration semantics (Task 1)**: Tests are written so that on the **current
  unfixed** code, every assertion fails and surfaces a concrete counterexample. Per
  the bugfix workflow, an exploration test that fails on unfixed code is the SUCCESS
  signal — it confirms the bug exists and matches `bugfix.md` analysis. Do not "fix"
  the test to make it pass on unfixed code; the failures are the deliverable.
- **Lock file in version control**: Add `imap_success.json.lock` to `.gitignore` if a
  repo-level `.gitignore` exists (out of scope for this spec; mention only if such a
  file is encountered during implementation).
- **`jobs/*/imap_success.json` migration**: Out of scope per `design.md` §Migration —
  expired jobs are cleaned up naturally by `cleanup_expired_jobs`. No migration
  script is written here.
- **Test framework discovery**: If `tests/` does not yet exist, the agent creating
  Task 1 also creates `tests/__init__.py` and a minimal `pytest` configuration in
  `pyproject.toml` or `pytest.ini` matching the project's existing toolchain. If
  Hypothesis is not yet a dependency, add it under a `[dev]` extra or to a separate
  `requirements-dev.txt` to keep the production `requirements.txt` clean (only
  `filelock>=3.0` is added there per Task 2.8).
- **Cross-platform sanity**: All atomicity / locking tests must work on both Linux
  and Windows (the project ships a `setup.sh` for Linux but `os.replace` and
  `filelock` are cross-platform; design.md §Risks calls this out).
