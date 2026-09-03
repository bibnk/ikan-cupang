# Implementation Plan

## Overview

Feature workflow yang menggantikan `OTP_SUBJECT_REGEX` di
`imap_engine.py` dengan list substring patterns yang dipersist ke
`subject_exclusion_list.json` di project root, dimuat per-job di
`ImapChecker.__init__`, dan diekspos ke admin via dua endpoint Flask
+ satu section di `templates/admin.html`. Eksekusi mengikuti pola
layered yang sama dengan spec `custom-skip-domains` plus satu task
tambahan untuk migrasi referensi `OTP_SUBJECT_REGEX` di test files
prior-spec:

1. Pasang fondasi di `imap_engine.py` — hapus `OTP_SUBJECT_REGEX`,
   tambah konstanta module-level + 5 helper baru
   (`_normalize_exclusion_entries`, `_validate_exclusion_entries`,
   `_save_subject_exclusion_list`, `_load_subject_exclusion_list`,
   `_subject_excluded`) plus 1 sub-helper `_has_control_char`
   (`design.md` §"Architecture > `imap_engine.py` changes").
2. Wire `ImapChecker.__init__` (snapshot per-job) dan `_worker` (gate
   replacement) — `design.md` §"Architecture > `ImapChecker.__init__`
   change" + §"`_worker` change".
3. Migrasi referensi `OTP_SUBJECT_REGEX` di
   `tests/test_subject_keyword_match_shown_in_live_*.py` mengikuti
   enumerasi line-by-line di `design.md` §"Migration > Test-code
   migration". Tanpa task ini, prior suite akan fail dengan
   `ImportError` setelah Task 1.
4. Tambah dua endpoint admin baru di `app.py` (`design.md`
   §"Architecture > `app.py` changes").
5. Tambah satu section UI baru di `templates/admin.html` dengan inline
   JS yang memanggil endpoint pada mount + click Save (`design.md`
   §"Architecture > `templates/admin.html` changes").
6. Tulis property-based + example tests untuk 8 correctness properties
   plus skenario example/edge/smoke (`design.md` §"Correctness
   Properties" + §"Testing Strategy").
7. Validasi cross-suite — pastikan suite ketiga spec sebelumnya
   (`centralize-imap-success-master`, `custom-skip-domains`,
   `subject-keyword-match-shown-in-live`) tetap hijau, master
   `imap_success.json` dan `skip_domains.json` byte-identical
   sebelum/sesudah, dan smoke import simbol baru sukses.

## Task Dependency Graph

```json
{
  "waves": [
    {
      "wave": 1,
      "tasks": ["1"],
      "description": "Foundation — remove OTP_SUBJECT_REGEX, add constants + 5 helpers in imap_engine.py"
    },
    {
      "wave": 2,
      "tasks": ["2"],
      "description": "ImapChecker.__init__ snapshot + _worker gate replacement"
    },
    {
      "wave": 3,
      "tasks": ["3"],
      "description": "Migrate OTP_SUBJECT_REGEX references in subject-keyword-match-shown-in-live test files"
    },
    {
      "wave": 4,
      "tasks": ["4"],
      "description": "Flask endpoints GET + POST /api/admin/subject-exclusion-list in app.py"
    },
    {
      "wave": 5,
      "tasks": ["5"],
      "description": "Admin UI section + inline JS in templates/admin.html"
    },
    {
      "wave": 6,
      "tasks": ["6"],
      "description": "Property-based + example/edge tests (8 properties + example/edge/smoke scenarios)"
    },
    {
      "wave": 7,
      "tasks": ["7"],
      "description": "Cross-suite validation, smoke import, neighbor-files byte-identical assertion"
    }
  ]
}
```

Reasoning:
- Task 1 has no dependency — adds new symbols (and removes
  `OTP_SUBJECT_REGEX`, but since `_worker` still references it, the
  module is in a temporary broken state until Task 2 closes the gap;
  that's why Tasks 1+2 run sequentially in waves 1 and 2 with no
  intermediate test runs).
- Task 2 depends on Task 1: `__init__` calls `_load_subject_exclusion_list`
  and `_worker` calls `_subject_excluded` (both fed by Task 1 helpers).
- Task 3 depends on Task 1 (the import line that swap-replaces
  `OTP_SUBJECT_REGEX` requires the new `_subject_excluded` symbol to
  exist). It is serialized after Task 2 to maintain a clean
  per-wave commit-able state.
- Task 4 depends on Task 1 (it imports the four helpers).
- Task 5 depends on Task 4 (UI hits the endpoints).
- Task 6 depends on Tasks 1+2+3+4+5 — property tests touch all layers
  (helpers, endpoints, integration with `_worker`).
- Task 7 depends on Task 6 — final cross-spec sign-off.

## Tasks

- [x] 1. Add module-level constants and seed/load/save helpers in `imap_engine.py`
  - File: `imap_engine.py`
  - Edits, in source order:
    1. **Remove `OTP_SUBJECT_REGEX`**. Delete the line
       `OTP_SUBJECT_REGEX = re.compile(r'^Booking\.com – \w+ is your verification code$')`
       at `imap_engine.py:27`. The constant is no longer needed
       (Requirement 5.2). `import re` stays — `EMAIL_REGEX` still uses it.
    2. **Add new module-level constants** in the constants region
       (just below the skip-domains constants block from spec
       `custom-skip-domains`):
       ```python
       # Subject-exclusion file (project root) — user-editable list of
       # substring patterns matched (case-insensitively) against decoded
       # email Subject. Loaded per-job in ImapChecker.__init__.
       SUBJECT_EXCLUSION_PATH = os.path.join(
           os.path.dirname(os.path.abspath(__file__)),
           "subject_exclusion_list.json",
       )
       SUBJECT_EXCLUSION_LOCK_PATH = SUBJECT_EXCLUSION_PATH + ".lock"

       # First-run seed only. NOT referenced from _worker (Requirement 5.2).
       # This single substring is the invariant trailing phrase of every
       # subject the previous OTP_SUBJECT_REGEX matched, so seeding with it
       # preserves the prior exclusion semantics by default
       # (Requirement 2.4, 12.1).
       _DEFAULT_SUBJECT_EXCLUSION_PATTERNS = ["is your verification code"]

       # Control-char detector for Requirement 9.4. Defined alongside
       # subject-exclusion module state, NOT shared with _WHITESPACE_RE
       # (custom-skip-domains).
       _EXCLUSION_CONTROL_CHARS = {"\r", "\n", "\t", "\v", "\f", "\x00"}
       ```
       — `_DEFAULT_SUBJECT_EXCLUSION_PATTERNS` MUST be a `list`, not a
       set/tuple, and MUST contain exactly one element in this order
       (Requirement 12.4).
    3. **Add `_has_control_char(s)` sub-helper** (Requirement 9.4):
       ```python
       def _has_control_char(s):
           """True iff s contains a control character per Requirement 9.4.

           Definition: c in {"\\r", "\\n", "\\t", "\\v", "\\f", "\\x00"} OR
           (ord(c) < 0x20 AND c != " "). Internal ASCII space U+0020 is
           explicitly allowed.
           """
           for c in s:
               if c in _EXCLUSION_CONTROL_CHARS:
                   return True
               if ord(c) < 0x20 and c != " ":
                   return True
           return False
       ```
    4. **Add `_normalize_exclusion_entries(raw)` pure helper**
       (Requirement 4.1, 4.2, 4.3, 4.4):
       ```python
       def _normalize_exclusion_entries(raw):
           """Normalize per Requirement 4.1: strip → lower → drop-empty →
           dedupe (preserve first-occurrence order). Internal whitespace
           inside an entry is preserved intact (Requirement 4.3).
           """
           seen = set()
           out = []
           for entry in raw:
               norm = entry.strip().lower()
               if not norm:
                   continue
               if norm in seen:
                   continue
               seen.add(norm)
               out.append(norm)
           return out
       ```
       Order: strip → lower → drop-empty → dedupe-preserving-order.
    5. **Add `_validate_exclusion_entries(raw)` pure helper**
       (Requirement 9.1–9.6):
       ```python
       def _validate_exclusion_entries(raw):
           """Validate per Requirement 9.1 with short-circuit; all-or-nothing
           per Requirement 9.6.

           Returns ``(valid_normalized, rejected_raw)``. ``rejected_raw``
           entries are reported as-sent (no strip/lower), preserving
           first-occurrence order, including duplicates (Requirement 9.5).
           """
           rejected = []
           valid_pre_dedupe = []
           for entry in raw:
               stripped = entry.strip()
               if stripped == "":
                   rejected.append(entry)
                   continue
               if len(stripped) > 500:
                   rejected.append(entry)
                   continue
               if _has_control_char(stripped):
                   rejected.append(entry)
                   continue
               valid_pre_dedupe.append(entry)
           if rejected:
               return [], rejected
           return _normalize_exclusion_entries(valid_pre_dedupe), []
       ```
       Threshold 500 (vs 255 in `_validate_skip_entries`) per
       Requirement 9.3.
    6. **Add `_save_subject_exclusion_list(value)` helper**
       (Requirement 3.1, 3.5):
       ```python
       def _save_subject_exclusion_list(value):
           """Atomically write ``list[str]`` to ``SUBJECT_EXCLUSION_PATH``
           under ``FileLock``.

           Caller is responsible for normalization + validation. Propagates
           ``filelock.Timeout`` and ``OSError`` to the caller.
           """
           lock = FileLock(SUBJECT_EXCLUSION_LOCK_PATH, timeout=MASTER_LOCK_TIMEOUT_SECONDS)
           with lock:
               tmp_path = SUBJECT_EXCLUSION_PATH + ".tmp"
               with open(tmp_path, "w", encoding="utf-8") as f:
                   json.dump(value, f, indent=2)
                   f.flush()
                   os.fsync(f.fileno())
               os.replace(tmp_path, SUBJECT_EXCLUSION_PATH)
       ```
       Call sequence: open(tmp,"w") → json.dump(indent=2) → flush →
       fsync → close → os.replace. Reuses `MASTER_LOCK_TIMEOUT_SECONDS`.
    7. **Add `_load_subject_exclusion_list()` helper** (Requirement
       2.1, 2.2, 2.3, 3.4):
       ```python
       def _load_subject_exclusion_list():
           """Load subject-exclusion list, seeding _DEFAULT_SUBJECT_EXCLUSION_PATTERNS
           on first run.

           - Missing file → write _DEFAULT_SUBJECT_EXCLUSION_PATTERNS via
             tmp + fsync + os.replace and return list(_DEFAULT_SUBJECT_EXCLUSION_PATTERNS).
           - Corrupt file (JSON error or not list[str]) → return [] WITHOUT
             rewriting (Requirement 2.3).
           - filelock.Timeout propagates to caller.
           """
           lock = FileLock(SUBJECT_EXCLUSION_LOCK_PATH, timeout=MASTER_LOCK_TIMEOUT_SECONDS)
           with lock:
               if not os.path.exists(SUBJECT_EXCLUSION_PATH):
                   tmp_path = SUBJECT_EXCLUSION_PATH + ".tmp"
                   with open(tmp_path, "w", encoding="utf-8") as f:
                       json.dump(list(_DEFAULT_SUBJECT_EXCLUSION_PATTERNS), f, indent=2)
                       f.flush()
                       os.fsync(f.fileno())
                   os.replace(tmp_path, SUBJECT_EXCLUSION_PATH)
                   return list(_DEFAULT_SUBJECT_EXCLUSION_PATTERNS)
               try:
                   with open(SUBJECT_EXCLUSION_PATH, "r", encoding="utf-8") as f:
                       data = json.load(f)
               except Exception:
                   return []
               if not isinstance(data, list) or not all(isinstance(x, str) for x in data):
                   return []
               return data
       ```
    8. **Add `_subject_excluded(subject, patterns)` pure helper**
       (Requirement 6.1, 6.2, 6.5):
       ```python
       def _subject_excluded(subject, patterns):
           """Return True iff any pattern is a substring of subject.lower().

           Assumes ``patterns`` has already been lowercased by the loader
           (Requirement 4.4) — does NOT lowercase ``patterns`` again.
           Lowercases ``subject`` exactly once.

           Requirement 6.2: empty patterns list ⇒ False (any over [] is False).
           """
           s = subject.lower()
           return any(p in s for p in patterns)
       ```
  - Place all helpers adjacent to the existing skip-domains helpers
    (after `_load_skip_domains`, before `_escape_imap_string` /
    `class ImapChecker`).
  - Smoke checks after the edit:
    ```
    python -c "from imap_engine import SUBJECT_EXCLUSION_PATH, SUBJECT_EXCLUSION_LOCK_PATH, _DEFAULT_SUBJECT_EXCLUSION_PATTERNS, _normalize_exclusion_entries, _validate_exclusion_entries, _save_subject_exclusion_list, _load_subject_exclusion_list, _subject_excluded; print('OK')"
    python -c "from imap_engine import _DEFAULT_SUBJECT_EXCLUSION_PATTERNS; assert _DEFAULT_SUBJECT_EXCLUSION_PATTERNS == ['is your verification code']; print('seed OK')"
    python -c "import imap_engine; assert not hasattr(imap_engine, 'OTP_SUBJECT_REGEX'); print('regex removed OK')"
    ```
  - **NOTE**: After this task, `imap_engine.py` is in a temporary
    broken state — `_worker` still has `if not OTP_SUBJECT_REGEX.match(subject.strip()):`
    referring to the removed constant. Task 2 closes the gap.
    Module imports cleanly because the reference is only evaluated at
    `_worker` runtime; do NOT run pytest until Task 2 lands.
  - _Validates: 1.1, 1.2, 1.3, 2.1, 2.2, 2.3, 2.4, 3.1, 3.2, 3.3, 3.5, 4.1, 4.2, 4.3, 4.4, 5.2, 6.1, 6.2, 6.5, 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 12.4, 13.5_

- [x] 2. Wire `ImapChecker.__init__` and `_worker` to use `self.subject_exclusion_patterns`
  - File: `imap_engine.py`
  - Edits, in source order:
    1. **`__init__` snapshot load**. Locate the existing
       `self.skip_domain_keywords = ...` block (around line 393–405,
       added by `custom-skip-domains` spec) and append a parallel
       block directly below it:
       ```python
       # Subject-exclusion snapshot for this job (Requirement 5.1, 5.3).
       # Loaded BEFORE any worker thread is spawned in run() so the
       # snapshot is fixed for the lifetime of the job (non-retroactive
       # to admin edits — Requirement 12.3).
       try:
           self.subject_exclusion_patterns = _load_subject_exclusion_list()
       except Timeout:
           # Requirement 3.4(b): fallback to default in-memory; do NOT
           # raise from constructor.
           print(
               "[imap_engine] subject-exclusion-list lock timeout, "
               "falling back to default in-memory",
               file=sys.stderr,
           )
           self.subject_exclusion_patterns = list(_DEFAULT_SUBJECT_EXCLUSION_PATTERNS)
       ```
       Place BEFORE `# Progress tracking`. The `list(...)` copy ensures
       fallback cannot mutate the module constant.
    2. **`_worker` gate replacement**. Locate the line at
       `imap_engine.py:748` (or wherever `OTP_SUBJECT_REGEX.match`
       still appears after Task 1):
       ```python
       if not OTP_SUBJECT_REGEX.match(subject.strip()):
       ```
       Replace with:
       ```python
       if not _subject_excluded(subject, self.subject_exclusion_patterns):
       ```
       — pass `subject` un-stripped (Requirement 6.4 — the legacy
       `.strip()` was needed for the regex's `^...$` anchors;
       substring matching doesn't need it). The if-body and surrounding
       lines (including the three-arm sender/keyword OR gate from
       `subject-keyword-match-shown-in-live`) stay byte-identical
       (Requirement 6.3).
    3. **Verify zero stray references**. Grep `imap_engine.py` for
       `OTP_SUBJECT_REGEX` — expected: 0 hits. Grep for
       `_DEFAULT_SUBJECT_EXCLUSION_PATTERNS` — expected: 3 references
       (constant definition, `_load_subject_exclusion_list` seed
       branch, `__init__` Timeout fallback). Zero hits inside `_worker`
       (Requirement 5.2).
  - Smoke checks after the edits:
    ```
    python -c "import imap_engine; print('imports OK')"
    python -c "import imap_engine; ImapChecker = imap_engine.ImapChecker; print('class OK')"
    ```
    One-off integration check (exercises seed pipeline via mocked path):
    ```
    python -c "import os, tempfile, imap_engine; td = tempfile.mkdtemp(); imap_engine.SUBJECT_EXCLUSION_PATH = os.path.join(td, 'subject_exclusion_list.json'); imap_engine.SUBJECT_EXCLUSION_LOCK_PATH = imap_engine.SUBJECT_EXCLUSION_PATH + '.lock'; print(imap_engine._load_subject_exclusion_list())"
    ```
    Expected output: `['is your verification code']`.
  - After this task, the module is in a coherent state but the
    `subject-keyword-match-shown-in-live` test suite will still fail
    with `ImportError: cannot import name 'OTP_SUBJECT_REGEX'`. Task 3
    fixes that.
  - _Validates: 5.1, 5.3, 5.4, 6.3, 6.4, 12.3_
  - _Depends on: 1_

- [x] 3. Migrate `OTP_SUBJECT_REGEX` references in prior-spec test files
  - Files (all under `tests/`):
    - `tests/test_subject_keyword_match_shown_in_live_preservation.py`
    - `tests/test_subject_keyword_match_shown_in_live_fix.py`
    - `tests/test_subject_keyword_match_shown_in_live_bug.py` (no
      code-level edits — only docstring/function-name references that
      don't affect runtime).
  - This task closes the temporary broken state for the prior-spec
    test suite. Without it, `pytest tests/test_subject_keyword_match_shown_in_live_*.py`
    fails at collection time with `ImportError`.
  - Edits, per `design.md` §"Migration > Test-code migration":
    1. **`test_subject_keyword_match_shown_in_live_preservation.py`**:
       - Line 51–55 (import block): replace
         `OTP_SUBJECT_REGEX,` with `_subject_excluded,` in the
         `from imap_engine import (...)` import list.
       - Line 173 (subject generator filter): replace
         `).filter(lambda s: not OTP_SUBJECT_REGEX.match(s.strip()))`
         with
         `).filter(lambda s: not _subject_excluded(s, ["is your verification code"]))`.
       - Line 300 (per-UID OTP check): replace
         `if OTP_SUBJECT_REGEX.match(subj_for_uid.strip()):`
         with
         `if _subject_excluded(subj_for_uid, ["is your verification code"]):`.
       - Line 413 (test-internal sanity check): replace
         `if not OTP_SUBJECT_REGEX.match(otp_subject.strip()):`
         with
         `if not _subject_excluded(otp_subject, ["is your verification code"]):`.
    2. **`test_subject_keyword_match_shown_in_live_fix.py`**:
       - Line 55 (import): replace
         `from imap_engine import OTP_SUBJECT_REGEX, sender_matches  # noqa: E402`
         with
         `from imap_engine import _subject_excluded, sender_matches  # noqa: E402`.
    3. **`test_subject_keyword_match_shown_in_live_bug.py`**:
       - No code-level edits. The four `OTP_SUBJECT_REGEX` references
         in this file are inside docstrings, function names, and
         assertion messages only — they do not affect test execution.
       - Optional cosmetic rename of
         `test_otp_subject_regex_remains_outermost_gate` →
         `test_default_subject_exclusion_remains_outermost_gate` for
         accuracy. This is OPTIONAL; skip if it adds noise.
  - After applying the edits, run:
    ```
    pytest tests/test_subject_keyword_match_shown_in_live_*.py -v
    ```
    Expected: 17 tests PASS (4 bug + 7 fix + 8 preservation, including
    the parametrized split). The semantic content of every assertion
    is preserved because the seeded default `["is your verification code"]`
    excludes exactly the subjects matched by the legacy regex
    (Property 6 in `design.md` guarantees this).
  - **Constraints**:
    - Do NOT modify any other tests beyond these line-level swaps.
    - Do NOT change test logic, assertion targets, or fixture data.
    - The semantic content of each migrated assertion is preserved.
  - _Validates: 14.3, 14.5_
  - _Depends on: 1, 2_

- [x] 4. Add Flask endpoints `GET` and `POST /api/admin/subject-exclusion-list` in `app.py`
  - File: `app.py`
  - Edits, in source order:
    1. **Imports**. Augment the existing
       `from imap_engine import (...)` block at the top of `app.py` to
       include three new helpers:
       ```python
       from imap_engine import (
           # ... existing imports retained verbatim ...
           _load_skip_domains,
           _save_skip_domains,
           _validate_skip_entries,
           # New for subject-exclusion-list:
           _load_subject_exclusion_list,
           _save_subject_exclusion_list,
           _validate_exclusion_entries,
       )
       ```
       `Timeout` from filelock is already imported by `custom-skip-domains`.
    2. **GET handler** placed directly below `post_skip_domains`
       (which `custom-skip-domains` added) and BEFORE `start_check`:
       ```python
       @app.route("/api/admin/subject-exclusion-list", methods=["GET"])
       @login_required
       def get_subject_exclusion_list():
           if not session.get("is_admin"):
               return jsonify({"error": "Admin only"}), 403
           try:
               patterns = _load_subject_exclusion_list()
           except Timeout:
               return jsonify({"error": "Subject-exclusion list is busy, please retry"}), 503
           return jsonify({"patterns": patterns}), 200
       ```
    3. **POST handler** placed immediately after `get_subject_exclusion_list`:
       ```python
       @app.route("/api/admin/subject-exclusion-list", methods=["POST"])
       @login_required
       def post_subject_exclusion_list():
           if not session.get("is_admin"):
               return jsonify({"error": "Admin only"}), 403

           # Requirement 8.4 + 9.7: structural validation BEFORE per-entry validation.
           data = request.get_json(silent=True)
           if not isinstance(data, dict):
               return jsonify({"error": "Invalid payload"}), 400
           patterns_raw = data.get("patterns")
           if not isinstance(patterns_raw, list):
               return jsonify({"error": "Invalid payload"}), 400
           if not all(isinstance(x, str) for x in patterns_raw):
               return jsonify({"error": "Invalid payload"}), 400

           # Requirement 9.1-9.6: per-entry validation, all-or-nothing short-circuit.
           valid, rejected = _validate_exclusion_entries(patterns_raw)
           if rejected:
               return jsonify({"error": "Invalid entries", "rejected": rejected}), 400

           # Requirement 3.1, 8.2, 8.3: full-replace atomic write under FileLock.
           try:
               _save_subject_exclusion_list(valid)
           except Timeout:
               return jsonify({"error": "Subject-exclusion list is busy, please retry"}), 503

           return jsonify({"success": True, "patterns": valid}), 200
       ```
    4. **No removal of any existing route**. Diff vs HEAD before
       commit; expected diff is exactly two new route registrations +
       three new import lines.
  - Smoke check after the edits:
    ```
    python -c "import app; print('OK')"
    python -c "import app; rules = sorted(set((rule.rule, tuple(sorted(m for m in rule.methods if m in ('GET','POST')))) for rule in app.app.url_map.iter_rules() if 'subject-exclusion-list' in rule.rule)); print(rules)"
    ```
    Expected: rules contain both GET and POST methods bound to
    `/api/admin/subject-exclusion-list`.
  - _Validates: 7.1, 7.2, 7.3, 7.4, 7.5, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 9.4, 9.5, 9.6, 9.7, 11.1, 11.2_
  - _Depends on: 1_

- [x] 5. Add Subject Exclusion section to `templates/admin.html` with inline JS
  - File: `templates/admin.html`
  - Edits, in source order:
    1. **HTML section**. Locate the closing `</div>` of the existing
       "Skip Domains" section (around line 117 — added by
       `custom-skip-domains`). Insert the new section directly after
       that closing `</div>` and BEFORE `</main>`:
       ```html
       <!-- Subject Exclusion List -->
       <div class="form-section">
           <h2 style="margin-bottom:20px;">🛑 Subject Exclusion List</h2>
           <p style="color:#94a3b8;margin-bottom:12px;font-size:14px;">
               Satu pattern per baris. Email yang Subject-nya memuat salah
               satu pattern (substring, case-insensitive) akan di-exclude
               dari hasil <code>live.txt</code>. Default: pattern OTP
               Booking.com.
           </p>
           <div class="form-group">
               <label for="subject-exclusion-textarea"><span class="label-icon">📝</span> Daftar Subject-Exclusion</label>
               <textarea id="subject-exclusion-textarea" rows="10"
                   style="width:100%;font-family:'Fira Code',monospace;font-size:14px;"
                   placeholder="is your verification code&#10;newsletter&#10;notification only"></textarea>
           </div>
           <button id="save-subject-exclusion-btn" class="btn-primary"
               style="margin-top:16px;background:linear-gradient(135deg,#f59e0b,#d97706);box-shadow:0 4px 20px rgba(245,158,11,0.3);">
               <span class="btn-icon">💾</span>
               <span class="btn-text">Simpan</span>
           </button>
           <div id="subject-exclusion-msg" class="admin-msg hidden"></div>
       </div>
       ```
    2. **Inline JS** appended to the existing `<script>` block at the
       bottom of `templates/admin.html`, AFTER the IIFE for skip-domains
       (which `custom-skip-domains` added):
       ```javascript
       // Subject Exclusion List: load on page mount + save on click
       (function () {
           const exclTextarea = document.getElementById("subject-exclusion-textarea");
           const exclSaveBtn = document.getElementById("save-subject-exclusion-btn");
           const exclMsgDiv = document.getElementById("subject-exclusion-msg");

           function showExclMsg(text, isError) {
               exclMsgDiv.textContent = text;
               exclMsgDiv.className = "admin-msg " + (isError ? "error" : "success");
               exclMsgDiv.classList.remove("hidden");
               setTimeout(function () { exclMsgDiv.classList.add("hidden"); }, 3000);
           }

           (async function loadSubjectExclusion() {
               try {
                   const res = await fetch("/api/admin/subject-exclusion-list");
                   if (!res.ok) {
                       showExclMsg("Gagal memuat: HTTP " + res.status, true);
                       return;
                   }
                   const data = await res.json();
                   exclTextarea.value = (data.patterns || []).join("\n");
               } catch (e) {
                   showExclMsg("Error: " + e.message, true);
               }
           })();

           exclSaveBtn.addEventListener("click", async function () {
               const lines = exclTextarea.value.split("\n");
               try {
                   const res = await fetch("/api/admin/subject-exclusion-list", {
                       method: "POST",
                       headers: { "Content-Type": "application/json" },
                       body: JSON.stringify({ patterns: lines })
                   });
                   const data = await res.json();
                   if (res.ok && data.success) {
                       exclTextarea.value = (data.patterns || []).join("\n");
                       showExclMsg("Tersimpan (" + (data.patterns || []).length + " entries)", false);
                   } else if (res.status === 400 && data.rejected) {
                       showExclMsg("Entry ditolak: " + data.rejected.join(", "), true);
                   } else {
                       showExclMsg(data.error || "HTTP " + res.status, true);
                   }
               } catch (e) {
                   showExclMsg("Error: " + e.message, true);
               }
           });
       })();
       ```
       Constraints:
       - Use `exclTextarea.value = ...` (text content), NOT `innerHTML` — XSS-safe.
       - On Save: split by `"\n"` only. Server normalizes.
       - On 200 success: REPLACE textarea contents with `data.patterns`.
       - On 400 with `rejected`: display rejected entries joined by `", "`.
  - Smoke check:
    ```
    python -c "from app import app; client = app.test_client(); r = client.get('/admin'); print(r.status_code)"
    ```
    Expected: 302 (redirect to /login because no session). Endpoint-auth
    tests in Task 6 cover the 200 path.
  - _Validates: 10.1, 10.2, 10.3, 10.4, 10.5_
  - _Depends on: 4_

- [x] 6. Write unit + property tests in `tests/`
  - Files (all new, flat under `tests/`):
    - `tests/test_subject_exclusion_list_normalize.py` (Property 1)
    - `tests/test_subject_exclusion_list_round_trip.py` (Property 2)
    - `tests/test_subject_exclusion_list_post_persist.py` (Property 3)
    - `tests/test_subject_exclusion_list_validate_reject.py` (Property 4)
    - `tests/test_subject_exclusion_list_subject_excluded.py` (Property 5)
    - `tests/test_subject_exclusion_list_default_seed_otp.py` (Property 6)
    - `tests/test_subject_exclusion_list_worker_gate.py` (Property 7)
    - `tests/test_subject_exclusion_list_neighbors_untouched.py` (Property 8)
    - `tests/test_subject_exclusion_list_first_run_seed.py` (EXAMPLE 2.1, 12.4)
    - `tests/test_subject_exclusion_list_corrupt_file.py` (EDGE_CASE 2.3, 5.4)
    - `tests/test_subject_exclusion_list_filelock_timeout.py` (EXAMPLE 3.4)
    - `tests/test_subject_exclusion_list_snapshot_semantics.py` (EXAMPLE 5.3, 12.3)
    - `tests/test_subject_exclusion_list_endpoint_auth.py` (EXAMPLE auth matrix)
    - `tests/test_subject_exclusion_list_invalid_payload.py` (EDGE_CASE 8.4, 9.7)
    - `tests/test_subject_exclusion_list_no_module_constant.py` (SMOKE 5.2, 14.5)
  - All Hypothesis tests use `@settings(max_examples=50, deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture])`.
    All fixtures monkeypatch `imap_engine.SUBJECT_EXCLUSION_PATH` and
    `imap_engine.SUBJECT_EXCLUSION_LOCK_PATH` to `tmp_path`-scoped
    locations. The real project-root files MUST NEVER be touched.
  - Test scenarios per property and per example/edge — full
    enumeration in `design.md` §"Testing Strategy". Key points:
    - **Property 4 control-char strategy**: include all of `\r`, `\n`,
      `\t`, `\v`, `\f`, `\x00` plus randomly-chosen `chr(c) for c <
      0x20 and c != 0x20`, and EXPLICITLY assert that ASCII space
      `" "` is NOT a control char (carve-out in Requirement 9.4).
    - **Property 6**: generate `t` matching `\w+` regex; assert
      `_subject_excluded(f"Booking.com \u2013 {t} is your verification code", ["is your verification code"])` returns True.
    - **Property 7**: drive `_worker` end-to-end with mocked
      `mail_conn`; assert email is dropped iff
      `_subject_excluded(subject, patterns) is True`.
    - **Property 8**: pre-create `imap_success.json` AND
      `skip_domains.json` with known content; capture
      bytes+mtime+sha256 for each plus their lock files; run
      `_save_subject_exclusion_list` and `_load_subject_exclusion_list`
      across generated values; assert all four neighbor files unchanged.
    - **No-module-constant smoke**: assert `not hasattr(imap_engine,
      "OTP_SUBJECT_REGEX")`; assert `inspect.getsource(ImapChecker._worker)`
      contains `"self.subject_exclusion_patterns"` and does NOT contain
      `"OTP_SUBJECT_REGEX"` and does NOT contain
      `"_DEFAULT_SUBJECT_EXCLUSION_PATTERNS"`; assert
      `_subject_excluded` and `_DEFAULT_SUBJECT_EXCLUSION_PATTERNS` are
      importable.
  - Run after writing:
    ```
    pytest tests/test_subject_exclusion_list_*.py -v
    ```
    Expected: ALL PASS on the fixed code.
  - _Validates: 1.2, 2.1, 2.2, 2.3, 2.4, 3.4, 3.5, 4.1, 4.2, 4.3, 4.4, 5.2, 5.3, 5.4, 6.1, 6.2, 6.3, 6.4, 6.5, 7.1, 7.2, 7.3, 7.4, 7.5, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 12.1, 12.3, 12.4, 13.1, 13.2, 13.3, 13.4, 13.5, 14.5_
  - _Depends on: 1, 2, 3, 4, 5_

- [x] 7. Final cross-suite validation, smoke import, neighbor-files byte-identical assertion
  - Steps in order:
    1. **Capture pre-suite digests**: SHA-256 of project-root
       `imap_success.json` and `skip_domains.json` (if they exist).
    2. **Run the new feature suite**:
       ```
       pytest tests/test_subject_exclusion_list_*.py -v
       ```
    3. **Run all prior-spec suites**:
       ```
       pytest tests/test_centralize_imap_success_master_*.py tests/test_custom_skip_domains_*.py tests/test_subject_keyword_match_shown_in_live_*.py -v
       ```
       Expected: all 72 prior tests PASS unchanged. The
       `subject-keyword-match-shown-in-live` suite passes because
       Task 3 migrated the OTP_SUBJECT_REGEX references.
    4. **Smoke import** all 8 new public surfaces:
       ```
       python -c "from imap_engine import _load_subject_exclusion_list, _save_subject_exclusion_list, _normalize_exclusion_entries, _validate_exclusion_entries, _subject_excluded, SUBJECT_EXCLUSION_PATH, SUBJECT_EXCLUSION_LOCK_PATH, _DEFAULT_SUBJECT_EXCLUSION_PATTERNS; print('OK')"
       ```
    5. **Confirm neighbor files byte-identical**: SHA-256 of
       `imap_success.json` and `skip_domains.json` after the suite
       MUST equal pre-suite digests.
    6. **Confirm route surface**: exactly 2 new path entries
       (`/api/admin/subject-exclusion-list` with both GET and POST).
       Existing 27 routes from prior specs unchanged.
    7. **Confirm no new dependency**: `git diff --stat -- requirements.txt`
       returns empty (Requirement 14.4).
  - **Workflow completion notice**: this is the final task across all
    four specs (`centralize-imap-success-master`,
    `subject-keyword-match-shown-in-live`, `custom-skip-domains`,
    `subject-exclusion-list`). Once Task 7 passes, the chain is done.
  - _Validates: all of 1.1–14.5 (final integration sign-off)_
  - _Depends on: 6_

## Notes

- **Layered execution**: Tasks 1 → 2 → 3 → 4 → 5 → 6 → 7 form a strict
  chain. Tasks 1+2 must run consecutively without intermediate test
  runs (Task 1 leaves the module in a temporary broken state for the
  test suites; Task 2 closes the gap). After Task 2, the
  `subject-keyword-match-shown-in-live` suite still fails with
  `ImportError` until Task 3 migrates the references.
- **Cross-spec preservation is structural**: this feature uses a
  separate `FileLock` path (`subject_exclusion_list.json.lock` vs
  `imap_success.json.lock` and `skip_domains.json.lock`), so the prior
  two storage specs are unaffected by FileLock contention. Property 8
  empirically validates neighbor-file byte-equivalence.
- **No new dependencies**: `filelock` was added by
  `centralize-imap-success-master`. `re` and `os`/`json`/`sys` are
  stdlib. `hypothesis` and `pytest` are already pinned. Requirement
  14.4 holds without any `requirements.txt` edit.
- **Test count after this spec lands**:
  - centralize-imap-success-master: 19 tests
  - custom-skip-domains: 36 tests
  - subject-keyword-match-shown-in-live: 17 tests
  - subject-exclusion-list: ~30 new tests (8 properties + 7
    example/edge/smoke)
  - **Total: ~102 tests across four spec suites.**
- **Workflow completion**: implementation is OUT of scope for this
  workflow document; open `tasks.md` and click "Start task" next to
  each item to execute via the orchestrator pattern used by the prior
  three specs.
