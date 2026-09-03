# Implementation Plan

## Overview

Bugfix workflow yang memperbaiki post-fetch sender filter di
`ImapChecker._worker` (`imap_engine.py`) sehingga email yang masuk ke `email_ids`
via SUBJECT branch tidak lagi dibuang saat user mengisi keyword-only atau
both-filled. Eksekusi mengikuti pola bug-condition-driven:

1. Tulis test eksplorasi yang **harus gagal** pada kode unfixed untuk
   mengonfirmasi dua counterexample (Case A: keyword-only; Case B: both-filled
   subject-only) dari `bugfix.md` §"Counterexample" dan §"Bug Condition".
2. Implementasikan fix surgical 3-edit di method `ImapChecker._worker` sesuai
   `design.md` §"Architecture > Surgical Change to `_worker`" dan §"Fix
   Implementation > Changes Required" — tanpa menyentuh helper modul-level,
   `sender_matches`, `OTP_SUBJECT_REGEX`, `_write_live`, atau master
   `imap_success.json` flow.
3. Tulis property-based fix-checking tests yang **harus lulus** pada kode fixed
   untuk Property 1 dari `design.md` §"Correctness Properties".
4. Tulis property-based preservation tests yang **harus lulus** pada kode fixed
   untuk Property 2 dari `design.md` §"Correctness Properties" — termasuk
   konfirmasi bahwa fix `centralize-imap-success-master` tetap utuh.
5. Jalankan seluruh suite ditambah suite `centralize-imap-success-master` untuk
   memastikan zero regression.

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
      "description": "Foundation: introduce subject_matched_ids local set"
    },
    {
      "wave": 3,
      "tasks": ["2.2"],
      "description": "Populate subject_matched_ids inside SUBJECT search loop"
    },
    {
      "wave": 4,
      "tasks": ["2.3"],
      "description": "Replace single-arm post-fetch gate with three-arm OR"
    },
    {
      "wave": 5,
      "tasks": ["3", "4"],
      "description": "Fix-checking and preservation property tests"
    },
    {
      "wave": 6,
      "tasks": ["5"],
      "description": "Final integration validation across both spec suites"
    }
  ]
}
```

Reasoning:
- Task 1 (bug exploration) is independent of any implementation.
- Task 2.1 introduces the new local set `subject_matched_ids`; Task 2.2 needs
  that set to populate; Task 2.3 needs both 2.1 and 2.2 to consume the set in
  the gate. The three edits all live in the same method (`_worker`) and must be
  applied sequentially to keep the file in a compilable state.
- Tasks 3 and 4 (PBT) need the full implementation in place (2.1 + 2.2 + 2.3).
- Task 5 is the final cross-spec validation sign-off.

## Tasks

- [x] 1. Write bug condition exploration test
  - File: `tests/test_subject_keyword_match_shown_in_live_bug.py` (new)
  - Implement three unit tests (using `pytest`) that drive `ImapChecker._worker`
    against the **current unfixed** `imap_engine.py` with `mail_conn` mocked via
    `unittest.mock.MagicMock`. Tests 1 and 2 MUST fail on the unfixed code; each
    failure MUST surface a concrete counterexample matching the cases in
    `bugfix.md` §"Counterexample" and `design.md` §"Testing Strategy >
    Exploratory Bug Condition Checking". Test 3 MUST pass on both unfixed and
    fixed code (sanity gate for the OTP regex).
    1. **Case A — Keyword-only drop** (will FAIL on unfixed code, per
       `bugfix.md` §1.1 / §1.2 and `design.md` §"Bug Details > Examples > Case
       A"). Construct `ImapChecker(job_id="explore_a", results_dir=tmp_path,
       accounts=[("alice@example.com", "hunter2")], target_senders=[],
       keywords=["confirmation"], search_days=7)` inside a `tmp_path`-scoped
       working directory. Patch `imaplib.IMAP4_SSL` so `_worker`'s
       `mail_conn.search` returns `("OK", [b"42"])` for the SUBJECT criterion
       and is never called for FROM (because `target_senders=[]`). Mock
       `mail_conn.fetch(b"42", "(RFC822)")` to return raw RFC822 bytes for an
       email with `From: reservations@hotelchain.com`, `Subject: Booking
       confirmation #12345 — see you in Paris`, `Date: Sat, 04 Jan 2025
       10:00:00 +0000`. Spy on `_write_live` and `_write_noemail` via
       `unittest.mock.patch.object`. Run `_worker` for the single account.
       Assert `_write_live` was called exactly once with `emails_data` of
       length 1 whose single entry has `subject == "Booking confirmation #12345
       — see you in Paris"`, AND assert `_write_noemail` was NOT called. On
       unfixed code the `if sender_matches(from_addr, self.target_senders):`
       gate at `imap_engine.py`:630 evaluates False (because
       `sender_matches(_, [])` always returns False — `bugfix.md` §"Bug
       Analysis > Current Behavior" 1.1), so `_write_noemail` is called and
       `_write_live` is not — the assertion fails. **Failure message MUST
       include**: the literal account `"alice@example.com:hunter2"`, the
       subject string, and a one-line summary `"Case A counterexample:
       sender_matches(from_addr, []) returned False, dropping a SUBJECT-matched
       non-OTP email"`.
    2. **Case B — Both-filled, SUBJECT-only drop** (will FAIL on unfixed code,
       per `bugfix.md` §1.3 and `design.md` §"Bug Details > Examples > Case
       B"). Construct `ImapChecker(..., target_senders=["@booking.com"],
       keywords=["invoice"], ...)`. Mock `mail_conn.search` so the FROM
       criterion returns `("OK", [b""])` (no match) and the SUBJECT criterion
       returns `("OK", [b"77"])`. Mock `mail_conn.fetch(b"77", "(RFC822)")` to
       return raw RFC822 for an email with `From: billing@stripe.com`,
       `Subject: Your invoice #998`. Spy on `_write_live` / `_write_noemail`.
       Assert `_write_live` was called once with `emails_data` of length 1
       containing the invoice email. On unfixed code the post-fetch gate
       evaluates `sender_matches("billing@stripe.com", ["@booking.com"])` →
       False, so the email is dropped despite UID `b"77"` being a member of
       `email_ids` via the SUBJECT branch — the assertion fails. **Failure
       message MUST include**: the literal `from_addr`, the subject, and the
       string `"Case B counterexample: SUBJECT-branch UID b'77' dropped because
       single-arm gate ignored email_ids union semantics"`.
    3. **OTP-stays-outermost sanity** (will PASS on unfixed AND fixed code, per
       `bugfix.md` §3.2 and `design.md` §"Bug Details > Examples >
       OTP-filtered"). Construct `ImapChecker(..., target_senders=[],
       keywords=["Booking.com"], ...)`. Mock `mail_conn.search` SUBJECT to
       return `("OK", [b"99"])`; mock `mail_conn.fetch` to return RFC822 with
       `From: noreply@booking.com`, `Subject: Booking.com – ABC123 is your
       verification code`. Assert `_write_live` is NOT called and that
       `emails_data` (captured via spy on `emails_data.append`) is empty —
       confirming OTP regex match at `imap_engine.py`:627 fires before any
       post-fetch gate logic. This is the sanity boundary: the OTP gate is
       outermost in both pre-fix and post-fix code paths.
  - Each assertion failure message in tests 1 and 2 MUST be detailed enough that
    a reader of pytest output can reconstruct the exact `(target_senders,
    keywords, from_addr, subject, UID)` counterexample without re-running the
    test.
  - **Expected outcome on unfixed code: tests 1 and 2 FAIL (deliverable —
    confirms Case A and Case B exist); test 3 PASSES (deliverable — confirms
    OTP gate is unaffected).**
  - _Validates: 1.1, 1.2, 1.3_

- [x] 2. Implement the surgical fix in `imap_engine.py`

- [x] 2.1 Initialize `subject_matched_ids` local set in `_worker`
  - File: `imap_engine.py`
  - Inside `ImapChecker._worker` (definition at line 518), locate the line
    `email_ids = set()` (around line 600, immediately after
    `mail_conn.select("INBOX")`) and insert a new line directly below it:
    `subject_matched_ids = set()  # bytes UIDs from SUBJECT branch — consumed by post-fetch gate`.
  - Match the existing 4-space indentation of `email_ids = set()`.
  - This set is scoped to a single `_worker` invocation (one account per call)
    and is garbage-collected on return — per `design.md` §"Design Decisions >
    Lokal set, bukan instance attribute" — to prevent cross-account state leak
    between worker threads.
  - Element type is `bytes`, identical to `email_ids` and `eid_bytes`, so
    membership testing in Edit 3 is type-safe with no encode/decode trip
    (`design.md` §"Design Decisions > `subject_matched_ids` menyimpan `bytes`").
  - Do NOT touch the FROM search loop (around lines 601–605) — it must NOT
    populate `subject_matched_ids` (`design.md` §"Fix Implementation > Changes
    Required" item 2 note).
  - Verify the file still parses by running `python -c "import imap_engine"`
    after the edit.
  - _Validates: 2.3, 3.4_
  - _Depends on: 1_

- [x] 2.2 Populate `subject_matched_ids` inside the SUBJECT search loop
  - File: `imap_engine.py`
  - Locate the per-keyword SUBJECT search block in `_worker` (around lines
    607–611) which currently reads:
    ```python
    for kw in self.keywords:
        criteria = f'(SINCE "{self.search_since_date}" SUBJECT "{_escape_imap_string(kw)}")'
        status, msgs = mail_conn.search(None, criteria)
        if status == "OK" and msgs and msgs[0]:
            email_ids.update(msgs[0].split())
    ```
  - Refactor to extract `msgs[0].split()` into a local `ids` so both sets are
    populated from the same evaluation:
    ```python
    for kw in self.keywords:
        criteria = f'(SINCE "{self.search_since_date}" SUBJECT "{_escape_imap_string(kw)}")'
        status, msgs = mail_conn.search(None, criteria)
        if status == "OK" and msgs and msgs[0]:
            ids = msgs[0].split()
            email_ids.update(ids)
            subject_matched_ids.update(ids)
    ```
  - Constraints carried from `design.md` §"Architecture > Edit 2" and §"Fix
    Implementation > Changes Required" item 2:
    - The set-union semantics of `email_ids` MUST remain unchanged
      byte-for-byte — `email_ids.update(ids)` produces the same result as the
      previous `email_ids.update(msgs[0].split())` (Requirement 3.4).
    - `_escape_imap_string(kw)` interpolation MUST stay intact (Requirement
      3.6) — do NOT remove it during the refactor.
    - The `if status == "OK" and msgs and msgs[0]:` guard MUST be preserved so
      a non-OK search or empty `msgs[0]` leaves both sets unchanged (`design.md`
      §"Failure Handling" rows 1 and 2).
  - Run `python -c "import imap_engine"` to confirm the file still parses.
  - _Validates: 2.3, 3.4, 3.6_
  - _Depends on: 2.1_

- [x] 2.3 Replace single-arm post-fetch gate with three-arm OR
  - File: `imap_engine.py`
  - Locate the post-fetch sender filter in `_worker` (around line 630). Current
    unfixed form:
    ```python
    if sender_matches(from_addr, self.target_senders):
        date_str = decode_mime_words(msg.get("Date", ""))
        emails_data.append({
            "subject": subject,
            "from": from_,
            "date": date_str
        })
    ```
  - Replace ONLY the `if` condition with the three-arm OR specified in
    `design.md` §"Architecture > Edit 3" and §"Fix Implementation > Changes
    Required" item 3:
    ```python
    if (
        (not self.target_senders)
        or sender_matches(from_addr, self.target_senders)
        or eid_bytes in subject_matched_ids
    ):
        date_str = decode_mime_words(msg.get("Date", ""))
        emails_data.append({
            "subject": subject,
            "from": from_,
            "date": date_str
        })
    ```
  - The `if` body (the `date_str = ...` line and the `emails_data.append({...})`
    block) MUST be left byte-identical to the pre-fix code — Requirements 2.2
    and 3.5 depend on the existing append shape and on `_write_live`'s downstream
    rendering.
  - Branch ordering rationale (`design.md` §"Architecture > Edit 3"
    short-circuit table, §"Design Decisions > Urutan cabang OR"):
    - `not self.target_senders` (cheap O(1) list-truthy check) covers Case A
      (keyword-only) and the both-empty regime via natural short-circuit.
    - `sender_matches(from_addr, self.target_senders)` covers the existing
      sender-only and both-filled FROM-match path — this is where the
      pre-fix gate collapses to so sender-only callers see byte-identical
      behavior (Requirement 3.1).
    - `eid_bytes in subject_matched_ids` (O(1) set membership on `bytes`)
      covers Case B (both-filled, SUBJECT-only) — the new path.
  - Do NOT modify `sender_matches` itself (`design.md` §"Architecture > Why Not
    Modify `sender_matches`"): a global change to its empty-list semantics
    would break the `_write_live` `<from_addr>` rendering at line 385 and
    violate Requirement 2.1.1.
  - Do NOT touch the OTP regex gate at line 627 (`if not OTP_SUBJECT_REGEX.match(subject.strip()):`)
    — it must remain outermost (Requirement 3.2 / `design.md` §"Risks" row 4).
  - Run `python -c "import imap_engine"` to confirm the file still parses.
  - _Bug_Condition: isBugCondition(X) per `bugfix.md` §"Bug Condition" — Case A
    (target_senders=[] AND keywords non-empty AND SUBJECT in matched_via) and
    Case B (target_senders non-empty AND keywords non-empty AND SUBJECT in
    matched_via AND NOT sender_matches(from_addr, target_senders))_
  - _Expected_Behavior: Property 1 from `design.md` §"Correctness Properties" —
    every email satisfying isBugCondition is included in `emails_data` exactly
    once and surfaces through `_write_live` + `_update_progress("live")`_
  - _Preservation: Property 2 from `design.md` §"Correctness Properties" —
    sender-only, OTP, both-empty, FROM-match, and master `imap_success.json`
    paths byte-identical to F_
  - _Validates: 2.1, 2.1.1, 2.2, 2.3, 3.1, 3.2, 3.3, 3.5_
  - _Depends on: 2.1, 2.2_

- [x] 3. Write fix-checking property tests
  - File: `tests/test_subject_keyword_match_shown_in_live_fix.py` (new)
  - Implement Hypothesis-driven property tests that encode Property 1 from
    `design.md` §"Correctness Properties" and §"Testing Strategy > Fix Checking"
    against the FIXED `imap_engine.py`. All tests MUST pass on fixed code.
    1. **Case A keep — keyword-only inclusion** (`bugfix.md` §2.1, `design.md`
       §"Testing Strategy > Property-Based Tests > keyword-only"). `@given` a
       non-empty list of keywords (`st.lists(st.text(min_size=1, max_size=20),
       min_size=1, max_size=4)`) and a list of synthetic fetched emails where
       at least one has a Subject containing one of the keywords as a substring
       AND a Subject that does NOT match `OTP_SUBJECT_REGEX`. Drive `_worker`
       with `target_senders=[]` and `keywords=<generated>`, mock
       `mail_conn.search` for SUBJECT branch to return UIDs corresponding to
       those subject-matching emails (FROM branch returns empty), mock
       `mail_conn.fetch` to return generated RFC822 bytes. Assert: for every
       non-OTP email in the generated set whose UID is returned by SUBJECT
       SEARCH, the email appears exactly once in the captured `emails_data`
       passed to `_write_live`.
    2. **Case B keep — both-filled SUBJECT-only inclusion** (`bugfix.md` §2.3,
       `design.md` §"Testing Strategy > Property-Based Tests > both-filled
       subject-only"). `@given` non-empty `target_senders` and non-empty
       `keywords`, plus a generated fetched-email set partitioned into
       FROM-only / SUBJECT-only / both / no-match. Drive `_worker` with the
       generated values; mock SEARCH to return UID partitions matching the
       generation labels. Assert: every SUBJECT-only non-OTP email
       (`from_addr` does not satisfy `sender_matches(target_senders)` AND UID
       is in the SUBJECT branch return set) appears exactly once in
       `emails_data`.
    3. **Exactly-once inclusion** (`bugfix.md` §2.3, `design.md` §"Design
       Decisions > Single logical OR per email"). For inputs where an email's
       UID is returned by BOTH FROM and SUBJECT SEARCH (the email is in
       `email_ids` via two paths), assert it appears in `emails_data` exactly
       ONCE. Use `collections.Counter(id(d) for d in emails_data)` (or compare
       by `(subject, from, date)` triple) to verify no duplicate append. The
       `if` body executes at most once per `eid_bytes` because the FETCH loop
       iterates over `sorted_ids` (which is built from `email_ids`, a set —
       UIDs are unique).
    4. **Raw From rendering for Case A** (`bugfix.md` §2.1.1, `design.md`
       §"Testing Strategy > Test Cases" item 4). Construct a Case A scenario
       (`target_senders=[]`, one keyword, one matching non-OTP email). Patch
       `builtins.open` in `_write_live`'s call site so the file write to
       `live_file` is captured. Parse the captured bytes and assert the From
       column is the raw `From` header value (e.g.
       `"Hotel Chain <reservations@hotelchain.com>"`) and NOT the
       `<reservations@hotelchain.com>` form produced by the
       `sender_matches`-True branch at `imap_engine.py`:385. Validates that
       `_write_live` rendering is naturally correct for Case A because
       `sender_matches(_, [])` is False (`design.md` §"Why Not Modify
       `sender_matches`").
    5. **Live counter increment** (`bugfix.md` §2.2, `design.md` §"Testing
       Strategy > Fix Checking" pseudocode). For every Case A or Case B
       scenario where `emails_data` ends non-empty, spy on
       `self._update_progress` and assert it is called exactly once with the
       string `"live"` for that account.
    6. **noemail counter NOT incremented** (`bugfix.md` §2.2). For the same
       Case A and Case B scenarios in property 5, assert
       `self._update_progress("noemail")` is NEVER called for that account
       AND `_write_noemail` is NEVER called for the account
       `(email_addr, password)` tuple.
  - Use `pytest.fixture` with `monkeypatch` to inject the mocked
    `imaplib.IMAP4_SSL`, and `unittest.mock.patch.object(ImapChecker,
    '_update_progress' / '_write_live' / '_write_noemail')` for spies. Cap
    Hypothesis examples at `@settings(max_examples=50)` to keep CI fast.
  - **Expected outcome on FIXED code: ALL TESTS PASS.**
  - _Validates: 2.1, 2.1.1, 2.2, 2.3_
  - _Depends on: 2.1, 2.2, 2.3_

- [x] 4. Write preservation property tests
  - File: `tests/test_subject_keyword_match_shown_in_live_preservation.py` (new)
  - Implement Hypothesis-driven property tests that encode Property 2 from
    `design.md` §"Correctness Properties" and §"Testing Strategy > Preservation
    Checking" against the FIXED `imap_engine.py`. All tests MUST pass on fixed
    code.
    1. **Sender-only byte-identical output** (`bugfix.md` §3.1, `design.md`
       §"Testing Strategy > Test Cases" item 1). `@given` non-empty
       `target_senders` (`st.lists(st.text(min_size=1, max_size=20),
       min_size=1, max_size=3)`), empty `keywords`, and a generated set of
       fetched emails. Run two harnesses against identical inputs: one
       importing the FIXED `_worker`, one re-running with the gate temporarily
       reverted to single-arm `if sender_matches(...)` (use
       `unittest.mock.patch` on the gate behavior, OR snapshot bytes via a
       golden file captured earlier from the unfixed commit). Assert
       `live.txt`, `noemail.txt`, `die.txt`, `unreg.txt`, `domain_skipped.txt`
       contents are byte-equal between the two runs (open each file in `"rb"`
       mode and compare). For sender-only callers the three-arm OR collapses
       to `sender_matches(...)` (because `target_senders` is non-empty AND
       `subject_matched_ids` is empty since `keywords=[]` — `design.md` §"Risks"
       row 1).
    2. **OTP outermost gate** (`bugfix.md` §3.2, `design.md` §"Testing Strategy
       > Property-Based Tests > OTP gate stays outermost"). `@given` random
       `target_senders` and `keywords` regimes (sender-only, keyword-only,
       both-filled, both-empty) and emails whose Subject is generated to match
       `OTP_SUBJECT_REGEX` (e.g. `"Booking.com – {token} is your verification
       code"` with `st.text(alphabet=st.characters(whitelist_categories=("Lu",
       "Ll", "Nd")), min_size=1, max_size=10)` for the token). Assert: across
       all four regimes, no OTP-matching email ever appears in the captured
       `emails_data`. Confirms the OTP gate at `imap_engine.py`:627 fires
       BEFORE the new three-arm OR for every regime.
    3. **Both-empty zero-SEARCH** (`bugfix.md` §3.3, `design.md` §"Testing
       Strategy > Test Cases" item 3). Drive `_worker` with `target_senders=[]`
       AND `keywords=[]`. Spy on `mail_conn.search` (set `search_count`
       counter). Assert `search_count == 0`, `_write_noemail` is called
       exactly once for the account, `self._update_progress("noemail")` is
       called exactly once, `_write_live` is NEVER called, and
       `self._update_progress("live")` is NEVER called.
    4. **`email_ids` union invariance** (`bugfix.md` §3.4, `design.md`
       §"Testing Strategy > Property-Based Tests > email_ids union
       unchanged"). `@given` random `target_senders`, `keywords`, and
       generated SEARCH return values. Instrument `_worker` (via subclass or
       monkey-patch) to capture the final `email_ids` set just before the
       `sorted_ids = sorted(...)` line. Run the same input through a
       reference path that re-implements only the SEARCH stage (the union
       loop, byte-for-byte). Assert the two `email_ids` sets are equal.
       Confirms Edit 2's refactor (`ids = msgs[0].split(); email_ids.update(ids)`)
       preserves union semantics relative to the pre-fix
       `email_ids.update(msgs[0].split())`.
    5. **`_write_live` rendering preservation** (`bugfix.md` §3.5,
       `design.md` §"Testing Strategy > Test Cases" item 4). For sender-only
       and both-filled regimes, assert `_write_live` renders `<from_addr>` for
       any email whose `from_addr` satisfies `sender_matches` (line 385) and
       raw `From` header otherwise. Capture bytes via `monkeypatch` on
       `builtins.open` for `live_file` and parse rows. The Case A raw
       rendering (already covered by Task 3 property 4) must coexist here
       without altering the sender-matched branch.
    6. **Master `imap_success.json` untouched** (`bugfix.md` §3.6, `design.md`
       §"Testing Strategy > Test Cases" item 5, §"Risks" row 8). Spy on
       `_atomic_update_master` (via `unittest.mock.patch.object(imap_engine,
       "_atomic_update_master")`). Drive `_worker` end-to-end across all four
       regimes. Assert `_atomic_update_master` is called only via
       `_update_imap_config` flow (the existing call sites that the
       `centralize-imap-success-master` fix introduced) and NEVER from any
       new code path introduced by this fix. Additionally assert that
       `_escape_imap_string` is still invoked once per keyword in the SUBJECT
       loop (Requirement 3.6 / `design.md` §"Glossary" entry for
       `_escape_imap_string`).
    7. **No API surface changes** (`bugfix.md` §3.7, `bugfix.md` §3.8). Use
       Flask's test client (or assert by importing `app` and inspecting
       `app.url_map`) to confirm that `/api/check`, `/api/status/<job_id>`,
       `/api/jobs` exist with the same methods and that the JSON response
       schema for `/api/status` matches a pre-fix snapshot (keys only, not
       values). Assert the per-job file names produced by `_worker` are
       exactly `live.txt`, `noemail.txt`, `die.txt`, `unreg.txt`,
       `domain_skipped.txt` (no new file, no rename).
  - Use `pytest.fixture` to construct an `ImapChecker` with mocked
    `imaplib.IMAP4_SSL`. Cap `@settings(max_examples=50)`.
  - **Expected outcome on FIXED code: ALL TESTS PASS.**
  - _Validates: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8_
  - _Depends on: 2.1, 2.2, 2.3_

- [x] 5. Run the full test suite and confirm zero regression
  - Run the new bugfix suite in one invocation:
    ```
    pytest tests/test_subject_keyword_match_shown_in_live_bug.py \
           tests/test_subject_keyword_match_shown_in_live_fix.py \
           tests/test_subject_keyword_match_shown_in_live_preservation.py -v
    ```
    Confirm:
    - Task 1 tests 1 and 2 now PASS on fixed code (the documented
      counterexamples no longer reproduce because the three-arm OR keeps the
      previously-dropped emails). Task 1 test 3 (OTP sanity) PASSES (same as
      it did on unfixed code).
    - All Task 3 fix-checking properties pass.
    - All Task 4 preservation properties pass.
  - Run the existing `centralize-imap-success-master` suite to guarantee zero
    regression on the prior fix:
    ```
    pytest tests/test_centralize_imap_success_master_bug.py \
           tests/test_centralize_imap_success_master_fix.py \
           tests/test_centralize_imap_success_master_preservation.py -v
    ```
    Confirm every test still passes — the surgical fix in `_worker` does not
    touch `_atomic_update_master`, `MASTER_IMAP_SUCCESS_PATH`, `FileLock`, or
    `_escape_imap_string`, so this suite must stay green (`design.md` §"Risks"
    row 8 / Requirement 3.6).
  - Smoke import: run
    `python -c "from imap_engine import ImapChecker, _atomic_update_master, _escape_imap_string, MASTER_IMAP_SUCCESS_PATH"`
    to confirm the module still imports cleanly and that no symbol from the
    prior spec was accidentally removed.
  - Manual sanity substitution (without starting `app.py`): capture
    `os.path.getsize` and the SHA-256 of the project-root `imap_success.json`
    (`c:\Users\Administrator\Desktop\imap-checker-vps\imap_success.json`)
    BEFORE running the full pytest suite above. Re-capture AFTER the suite
    completes. Assert the two SHA-256 digests are equal (the master file is
    byte-identical) — confirms the test suite never accidentally writes to the
    real master via a leaked path, and that the new fix introduces no new
    write to the master (`bugfix.md` §3.6).
  - _Validates: all of 1.1, 1.2, 1.3, 2.1, 2.1.1, 2.2, 2.3, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8 (final integration sign-off)_
  - _Depends on: 3, 4_

## Notes

- **Bug exploration semantics (Task 1)**: Tests 1 and 2 are written so they
  FAIL on the unfixed code — that failure is the deliverable, per the bugfix
  workflow's bug-condition methodology. Each failure message must surface a
  concrete `(target_senders, keywords, from_addr, subject, UID)`
  counterexample so the orchestrator can record it. After the fix in Task 2 is
  applied, the same tests must PASS without modification — they encode both
  the bug condition AND the expected behavior.
- **Surgical scope**: All three edits (2.1, 2.2, 2.3) live in a single method
  (`ImapChecker._worker`), within ~30 lines of each other (lines ~600, ~607–611,
  ~630). They MUST be applied in order to keep the file in a parseable state
  between waves; running `python -c "import imap_engine"` after each edit is
  the cheap fast-check.
- **No helper changes**: `sender_matches`, `OTP_SUBJECT_REGEX`,
  `_atomic_update_master`, `_escape_imap_string`, `MASTER_IMAP_SUCCESS_PATH`,
  `_write_live`, `_write_noemail`, `_write_die`, `_write_unreg`,
  `_write_domain_skip` are all explicitly out of scope (`design.md` §"Fix
  Implementation > Files NOT Touched"). If a sub-task tempts modification to
  any of these, stop and re-read `design.md` §"Architecture > Why Not Modify
  `sender_matches`" — call-site fix is intentional.
- **Cross-spec preservation (Task 5)**: This fix lives in a different method
  than the `centralize-imap-success-master` fix (`_worker` vs
  `_update_imap_config` and module-level helpers), so the two suites must
  both stay green simultaneously. Task 5 enforces that explicitly.
- **PBT example caps**: `@settings(max_examples=50)` is the suggested cap for
  Hypothesis tests in Tasks 3 and 4. If CI runtime budget is tight, lower to
  `25`; the property structures are deliberately narrow (single account, single
  inbox per example) so even 25 examples cover all four regime branches.
- **Hypothesis cache reuse**: The repo already has a `.hypothesis/` directory
  populated by the `centralize-imap-success-master` suite — Hypothesis will
  reuse and extend that cache automatically; no extra configuration needed.
