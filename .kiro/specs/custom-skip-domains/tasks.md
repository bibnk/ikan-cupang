# Implementation Plan

## Overview

Feature-workflow yang memindahkan `DOMAINS_TO_SKIP_KEYWORDS` dari konstanta
hardcoded di `imap_engine.py` ke state on-disk yang user-editable
(`skip_domains.json` di project root), dimuat per-job di
`ImapChecker.__init__` lewat dua helper module-level baru, dan diekspos
ke admin lewat dua endpoint Flask + satu section di
`templates/admin.html`. Eksekusi mengikuti urutan layered:

1. Pasang fondasi di `imap_engine.py` — konstanta module-level baru,
   pure helper untuk normalize/validate, dan dua helper FileLock-aware
   untuk load/save (`design.md` §"Architecture > `imap_engine.py`
   changes").
2. Wire `ImapChecker.__init__` dan `_worker` agar memakai snapshot
   per-job lewat atribut instance `self.skip_domain_keywords`, sambil
   menghapus simbol `DOMAINS_TO_SKIP_KEYWORDS` (`design.md`
   §"Architecture > `ImapChecker.__init__` change" dan §"_worker
   change").
3. Tambah dua endpoint admin-gated baru di `app.py` (`design.md`
   §"Architecture > `app.py` changes").
4. Tambah satu section UI baru di `templates/admin.html` lengkap
   dengan inline JS yang memanggil endpoint pada mount + click Save
   (`design.md` §"Architecture > `templates/admin.html` changes").
5. Tulis property-based + example tests untuk 7 correctness properties
   plus skenario example/edge case (`design.md` §"Correctness
   Properties" + §"Testing Strategy").
6. Validasi cross-suite — pastikan suite `centralize-imap-success-master`
   dan `subject-keyword-match-shown-in-live` tetap hijau, file master
   `imap_success.json` byte-identical sebelum/sesudah, dan smoke import
   simbol baru sukses.

## Task Dependency Graph

```json
{
  "waves": [
    {
      "wave": 1,
      "tasks": ["1"],
      "description": "Foundation — module-level constants + pure helpers + load/save helpers in imap_engine.py"
    },
    {
      "wave": 2,
      "tasks": ["2"],
      "description": "ImapChecker.__init__ snapshot + _worker rewrite + DOMAINS_TO_SKIP_KEYWORDS removal"
    },
    {
      "wave": 3,
      "tasks": ["3"],
      "description": "Two new admin endpoints in app.py (GET + POST /api/admin/skip-domains)"
    },
    {
      "wave": 4,
      "tasks": ["4"],
      "description": "Admin UI section + inline JS in templates/admin.html"
    },
    {
      "wave": 5,
      "tasks": ["5"],
      "description": "Property-based + example/edge tests (7 properties + 6 example/edge scenarios)"
    },
    {
      "wave": 6,
      "tasks": ["6"],
      "description": "Cross-suite validation, smoke import, master file byte-identical assertion"
    }
  ]
}
```

Reasoning:
- Task 1 has no dependency — it only adds new symbols and helpers; it
  doesn't break anything because nothing references them yet.
- Task 2 depends on Task 1: `__init__` calls `_load_skip_domains` and
  `_worker` reads `self.skip_domain_keywords` (which is fed by the
  loader). The removal of `DOMAINS_TO_SKIP_KEYWORDS` happens here, so
  Task 1's `_DEFAULT_SKIP_SET` must already exist as the new seed
  source.
- Task 3 depends on Task 1 (it imports the four helpers). It does NOT
  depend on Task 2 in principle, but is serialized after Task 2 to
  keep `imap_engine.py` in a single coherent commit-able state per
  wave (the reference suite `subject-keyword-match-shown-in-live`
  uses the same conservative ordering).
- Task 4 depends on Task 3 (UI hits the endpoints; for safety the user
  asked to serialize after Task 3 even though the textarea+button
  markup itself could be authored in parallel).
- Task 5 depends on Tasks 1 + 2 + 3 + 4 — the property tests touch all
  three layers (helpers, endpoints, integration with `_worker`
  classification).
- Task 6 depends on Task 5 — it's the final cross-spec sign-off.

## Tasks

- [x] 1. Add module-level constants and seed/load/save helpers in `imap_engine.py`
  - File: `imap_engine.py`
  - This task wires the foundation only — no caller is changed yet,
    `DOMAINS_TO_SKIP_KEYWORDS` is still in place. Removal of that
    constant happens in Task 2 once a replacement reader exists.
  - Edits, in source order:
    1. **Remove hardcoded set**. Delete the line
       `DOMAINS_TO_SKIP_KEYWORDS = {"hotmail", "live", "msn", "outlook", "yahoo", "interia", "poczta.fm"}`
       at `imap_engine.py:30` (`design.md` §"Architecture >
       Module-level constants"). Leave any blank lines around it
       intact so the diff stays minimal.
    2. **Add new module-level constants** in the same region (just
       below `MASTER_LOCK_TIMEOUT_SECONDS` so the project-root path
       constants live next to each other):
       ```python
       SKIP_DOMAINS_PATH = os.path.join(
           os.path.dirname(os.path.abspath(__file__)),
           "skip_domains.json",
       )
       SKIP_DOMAINS_LOCK_PATH = SKIP_DOMAINS_PATH + ".lock"

       # First-run seed only. NOT referenced from _worker (Requirement 5.2).
       _DEFAULT_SKIP_SET = ["hotmail", "live", "msn", "outlook", "yahoo", "interia", "poczta.fm"]
       ```
       — `_DEFAULT_SKIP_SET` MUST be a **list**, not a set, with
       elements in **exactly** the order shown so Requirement 12.3 is
       satisfied byte-for-byte by `json.dump(_DEFAULT_SKIP_SET,
       f, indent=2)`.
    3. **Import `re` at the top of the file** if not already imported
       (the validator uses `re.compile(r"\s")`). Check first via grep;
       if `import re` already exists at the top, skip this edit.
    4. **Add module-level compiled regex** for the whitespace check
       (`design.md` §"Architecture > `_validate_skip_entries`"):
       ```python
       _WHITESPACE_RE = re.compile(r"\s")
       ```
       Place it adjacent to the new constants so all skip-domains
       module state lives in one block.
    5. **Add `_normalize_skip_entries(raw)` pure helper** (`design.md`
       §"Architecture > `_normalize_skip_entries(raw)` helper"):
       ```python
       def _normalize_skip_entries(raw):
           """Normalize per Requirement 4.1: strip → lower → drop-empty → dedupe (preserve first-occurrence order)."""
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
       — order-preservation MUST be by first occurrence (Requirement
       4.2). Do NOT add any other transform (Requirement 4.3).
    6. **Add `_validate_skip_entries(raw)` pure helper** (`design.md`
       §"Architecture > `_validate_skip_entries(raw)` helper"):
       ```python
       def _validate_skip_entries(raw):
           """Validate per Requirement 9.1-9.3 with short-circuit; all-or-nothing per 9.5.

           Returns (valid_normalized, rejected_raw). rejected_raw entries are
           reported as-sent (no strip/lower), preserving first-occurrence
           order, including duplicates (Requirement 9.4).
           """
           rejected = []
           valid_pre_dedupe = []
           for entry in raw:
               stripped = entry.strip()
               if stripped == "":
                   rejected.append(entry)
                   continue
               if len(stripped) > 255:
                   rejected.append(entry)
                   continue
               if _WHITESPACE_RE.search(stripped):
                   rejected.append(entry)
                   continue
               valid_pre_dedupe.append(entry)
           if rejected:
               return [], rejected
           return _normalize_skip_entries(valid_pre_dedupe), []
       ```
       — checks MUST be evaluated in the exact order empty → length →
       whitespace per Requirement 9.1 short-circuit. Do NOT report
       which rule failed in `rejected` (Requirement 9.4(d)).
    7. **Add `_save_skip_domains(value)` helper** (`design.md`
       §"Architecture > `_save_skip_domains(value)` helper"):
       ```python
       def _save_skip_domains(value):
           """Atomically write a list[str] to SKIP_DOMAINS_PATH under FileLock.

           Caller is responsible for normalization + validation. This helper
           only does the atomic tmp + fsync + os.replace under
           FileLock(SKIP_DOMAINS_LOCK_PATH, timeout=MASTER_LOCK_TIMEOUT_SECONDS).
           Propagates filelock.Timeout and OSError to the caller.
           """
           lock = FileLock(SKIP_DOMAINS_LOCK_PATH, timeout=MASTER_LOCK_TIMEOUT_SECONDS)
           with lock:
               tmp_path = SKIP_DOMAINS_PATH + ".tmp"
               with open(tmp_path, "w", encoding="utf-8") as f:
                   json.dump(value, f, indent=2)
                   f.flush()
                   os.fsync(f.fileno())
               os.replace(tmp_path, SKIP_DOMAINS_PATH)
       ```
       — call sequence MUST be open(tmp,"w") → json.dump(indent=2) →
       flush → fsync → close (end of `with`) → `os.replace` per
       Requirement 3.1. Use `MASTER_LOCK_TIMEOUT_SECONDS` reused from
       the master spec — do NOT introduce a new timeout constant
       (Requirement 13.3 implies separate lock paths but shared
       timeout is fine; `design.md` §"Architecture > Module-level
       constants" pinpoints reuse).
    8. **Add `_load_skip_domains()` helper** (`design.md`
       §"Architecture > `_load_skip_domains()` helper"):
       ```python
       def _load_skip_domains():
           """Load the skip-domains list, seeding _DEFAULT_SKIP_SET on first run.

           Acquires FileLock(SKIP_DOMAINS_LOCK_PATH, timeout=MASTER_LOCK_TIMEOUT_SECONDS).
           - Missing file → write _DEFAULT_SKIP_SET via _save_skip_domains-style
             atomic path and return list(_DEFAULT_SKIP_SET).
           - Corrupt file (JSON error or not list[str]) → return [] WITHOUT
             rewriting (Requirement 2.3).
           - filelock.Timeout propagates to caller.
           """
           lock = FileLock(SKIP_DOMAINS_LOCK_PATH, timeout=MASTER_LOCK_TIMEOUT_SECONDS)
           with lock:
               if not os.path.exists(SKIP_DOMAINS_PATH):
                   tmp_path = SKIP_DOMAINS_PATH + ".tmp"
                   with open(tmp_path, "w", encoding="utf-8") as f:
                       json.dump(list(_DEFAULT_SKIP_SET), f, indent=2)
                       f.flush()
                       os.fsync(f.fileno())
                   os.replace(tmp_path, SKIP_DOMAINS_PATH)
                   return list(_DEFAULT_SKIP_SET)
               try:
                   with open(SKIP_DOMAINS_PATH, "r", encoding="utf-8") as f:
                       data = json.load(f)
               except Exception:
                   return []
               if not isinstance(data, list) or not all(isinstance(x, str) for x in data):
                   return []
               return data
       ```
       — first-run seed branch MUST use the same tmp + fsync +
       `os.replace` sequence as `_save_skip_domains` so byte-format is
       identical (Requirement 3.5). Read branch on existing valid
       file MUST NOT touch disk (Requirement 2.2 / Property 4) — no
       `_save_skip_domains` call when `os.path.exists` is True. The
       `try/except Exception` guard MUST be broad to catch
       `json.JSONDecodeError`, `UnicodeDecodeError`, and any
       `OSError` during read; corrupt or unreadable file all map to
       returning `[]` without rewrite (Requirement 2.3).
    9. **Verify imports**. Confirm at the top of `imap_engine.py` that
       `os`, `json`, `sys`, and `from filelock import FileLock,
       Timeout` are all already imported (the
       `centralize-imap-success-master` spec added these). If
       `Timeout` is not yet imported, add it to the existing
       `from filelock import ...` line — Task 2 will reference it.
  - Smoke check after the edit:
    ```
    python -c "from imap_engine import SKIP_DOMAINS_PATH, SKIP_DOMAINS_LOCK_PATH, _DEFAULT_SKIP_SET, _normalize_skip_entries, _validate_skip_entries, _load_skip_domains, _save_skip_domains; print('OK')"
    ```
    The expected output is `OK`. If `_DEFAULT_SKIP_SET` was created as
    a `set` instead of a `list`, this still imports — but Task 2 and
    Property 2 will catch the type mismatch at runtime; verify type
    here as defensive measure: `python -c "from imap_engine import
    _DEFAULT_SKIP_SET; assert isinstance(_DEFAULT_SKIP_SET, list);
    print(_DEFAULT_SKIP_SET)"`.
  - _Validates: 1.1, 1.2, 1.3, 2.1, 2.2, 2.3, 3.1, 3.2, 3.3, 3.5, 4.1, 4.2, 4.3, 9.1, 9.2, 9.3, 9.4, 9.5, 12.3, 13.3_

- [x] 2. Wire `ImapChecker.__init__` and `_worker` to use `self.skip_domain_keywords`
  - File: `imap_engine.py`
  - This task replaces the module-level skip set with a per-job
    snapshot loaded once in the constructor and consumed read-only by
    `_worker`. Edits, in source order:
    1. **`__init__` snapshot load**. Locate the end of
       `ImapChecker.__init__` (around line 220, immediately after
       `self.imap_success_config = self._load_imap_success_config()`)
       and append:
       ```python
       # Skip-domains snapshot for this job (Requirement 5.1, 5.3).
       # Loaded BEFORE any worker thread is spawned in run() so the snapshot
       # is fixed for the lifetime of the job (non-retroactive to admin edits).
       try:
           self.skip_domain_keywords = _load_skip_domains()
       except Timeout:
           # Requirement 3.4(b): fallback to default in-memory; do NOT raise.
           print(
               "[imap_engine] skip-domains lock timeout, falling back to default in-memory",
               file=sys.stderr,
           )
           self.skip_domain_keywords = list(_DEFAULT_SKIP_SET)
       ```
       — the `list(_DEFAULT_SKIP_SET)` copy ensures the fallback
       cannot accidentally mutate the module-level constant
       (Requirement 5.2 keeps it as a private seed only). The placement
       at the end of `__init__` guarantees Requirement 5.1 because
       `run()` (`imap_engine.py:484`) only spawns workers AFTER
       `__init__` returns.
    2. **`_worker` predicate rewrite**. Locate the line
       `if any(kw in domain for kw in DOMAINS_TO_SKIP_KEYWORDS):`
       at `imap_engine.py:410` (inside `_worker`) and change ONLY
       the iterable on the right of `for kw in`:
       ```python
       if any(kw in domain for kw in self.skip_domain_keywords):
       ```
       — the rest of the line (`if any(kw in domain for kw in ...)`)
       and the body (`self._write_domain_skip(...)`,
       `self._update_progress("skipped")`,
       `queue.task_done()`, `continue`) MUST be left byte-identical
       so Requirement 6.1, 6.3, and 12.1 hold (`design.md`
       §"Architecture > `_worker` change").
    3. **Confirm `domain` lowercasing remains**. Verify the line
       directly above the predicate still computes
       `domain = email_addr.split("@")[-1].lower()`. Do NOT touch
       it; this is the case-insensitive guarantee in Requirement 6.1.
    4. **Confirm zero stray references**. After the rewrites above,
       grep `imap_engine.py` for `DOMAINS_TO_SKIP_KEYWORDS` —
       expected: zero hits (Task 1 already removed the definition,
       and this task removed the only consumer). Grep for
       `_DEFAULT_SKIP_SET` — expected: only two hits, both inside
       `_load_skip_domains` (the seed write and the seed return) and
       one inside the new `__init__` Timeout fallback. No hit inside
       `_worker` (Requirement 5.2).
  - Smoke checks after the edits:
    ```
    python -c "import imap_engine; print('imports OK')"
    python -c "from imap_engine import ImapChecker; c = ImapChecker.__init__; print('init OK')"
    ```
    The first command catches syntax errors; the second confirms the
    class symbol is still exposed. Then run a one-off:
    ```
    python -c "import os, tempfile, imap_engine; td = tempfile.mkdtemp(); imap_engine.SKIP_DOMAINS_PATH = os.path.join(td, 'skip_domains.json'); imap_engine.SKIP_DOMAINS_LOCK_PATH = imap_engine.SKIP_DOMAINS_PATH + '.lock'; print(imap_engine._load_skip_domains())"
    ```
    Expected output: `['hotmail', 'live', 'msn', 'outlook', 'yahoo',
    'interia', 'poczta.fm']` — confirms the full first-run-seed
    pipeline works end-to-end (this is the EXAMPLE behavior validated
    by Task 5's `test_first_run_seed.py`).
  - _Validates: 5.1, 5.2, 5.3, 5.4, 6.1, 6.2, 6.3, 12.1, 12.2_
  - _Depends on: 1_

- [x] 3. Add Flask endpoints `GET` and `POST /api/admin/skip-domains` in `app.py`
  - File: `app.py`
  - This task adds exactly two new routes — no other route is touched
    (Requirement 11.1, 11.2). Edits, in source order:
    1. **Imports**. At the top of `app.py`, locate the existing import
       block (around the line that imports from `imap_engine`). Add
       the four helper symbols:
       ```python
       from imap_engine import (
           # ... existing imports retained verbatim ...
           _load_skip_domains,
           _save_skip_domains,
           _validate_skip_entries,
       )
       from filelock import Timeout
       ```
       Note: `_normalize_skip_entries` is reachable transitively
       through `_validate_skip_entries` and is NOT imported into
       `app.py` (the validator already calls it on the success path
       — see `design.md` §"Architecture > `_validate_skip_entries`").
       Tests in Task 5 may import `_normalize_skip_entries` directly
       from `imap_engine`.
    2. **GET handler** placed between `delete_code` (`app.py:171`) and
       `start_check` (`app.py:189`) so admin endpoints stay grouped:
       ```python
       @app.route("/api/admin/skip-domains", methods=["GET"])
       @login_required
       def get_skip_domains():
           if not session.get("is_admin"):
               return jsonify({"error": "Admin only"}), 403
           try:
               domains = _load_skip_domains()
           except Timeout:
               return jsonify({"error": "Skip-domains file is busy, please retry"}), 503
           return jsonify({"domains": domains}), 200
       ```
       — auth pattern matches `add_code` (`app.py:148`) and
       `delete_code` (`app.py:171`) verbatim (`design.md`
       §"Architecture > `app.py` changes"). The 401 path is delegated
       to the `@login_required` decorator (Requirement 7.3 / 8.5);
       this handler only needs to handle 403, 200, and 503.
    3. **POST handler** placed immediately after `get_skip_domains`:
       ```python
       @app.route("/api/admin/skip-domains", methods=["POST"])
       @login_required
       def post_skip_domains():
           if not session.get("is_admin"):
               return jsonify({"error": "Admin only"}), 403

           # Requirement 8.4 + 9.6: structural validation BEFORE per-entry validation.
           data = request.get_json(silent=True)
           if not isinstance(data, dict):
               return jsonify({"error": "Invalid payload"}), 400
           domains_raw = data.get("domains")
           if not isinstance(domains_raw, list):
               return jsonify({"error": "Invalid payload"}), 400
           if not all(isinstance(x, str) for x in domains_raw):
               return jsonify({"error": "Invalid payload"}), 400

           # Requirement 9.1-9.5: per-entry validation, all-or-nothing short-circuit.
           valid, rejected = _validate_skip_entries(domains_raw)
           if rejected:
               return jsonify({"error": "Invalid entries", "rejected": rejected}), 400

           # Requirement 3.1, 8.2, 8.3: full-replace atomic write under FileLock.
           try:
               _save_skip_domains(valid)
           except Timeout:
               return jsonify({"error": "Skip-domains file is busy, please retry"}), 503

           return jsonify({"success": True, "domains": valid}), 200
       ```
       — order of validation gates MUST be exactly: structural (dict)
       → list → all-string → per-entry. This ensures non-string
       elements at the list level produce `"Invalid payload"`, not
       `"Invalid entries"` (Requirement 9.6 → 8.4 routing per
       `design.md` §"Architecture > `app.py` changes" note on 9.6).
       The `_save_skip_domains(valid)` call passes the **normalized**
       list returned by `_validate_skip_entries`, NOT the raw
       `domains_raw`. The 200 response body's `domains` is the
       same `valid` list (Requirement 8.2 — matches what is on disk).
    4. **No removal of any existing route**. Run a grep on
       `app.url_map`-equivalent symbols to ensure the path list in
       Requirement 11.1 is unchanged (`/login`, `/logout`, `/`,
       `/get-email`, `/admin`, `/api/admin/add-code`,
       `/api/admin/delete-code`, `/api/check`, `/api/stop/<job_id>`,
       `/api/status/<job_id>`, `/api/download/<job_id>/<file_type>`,
       `/api/get-email`, `/api/delete-email`, `/history`,
       `/api/jobs`, `/api/jobs/<job_id>/extend`,
       `/api/jobs/<job_id>/delete`, `/api/jobs/<job_id>/live`,
       `/loop-delete`, `/api/loop-delete/start`,
       `/api/loop-delete/stop/<job_id>`,
       `/api/loop-delete/restart/<job_id>`,
       `/api/loop-delete/delete/<job_id>`,
       `/api/loop-delete/status/<job_id>`,
       `/api/loop-delete/list`). Diff vs HEAD before commit; expected
       diff is exactly two new route registrations + one updated
       import block.
  - Smoke check after the edits:
    ```
    python -c "import app; r = sorted(str(rule) for rule in app.app.url_map.iter_rules() if 'skip-domains' in str(rule)); print(r)"
    ```
    Expected output: `['/api/admin/skip-domains']` (Flask collapses
    GET+POST under one rule when they share the same path), or two
    entries if registered separately. Either is acceptable so long
    as `app.app.url_map` shows BOTH `GET` and `POST` methods bound
    to that path — verify via:
    ```
    python -c "import app; [print(rule.rule, sorted(rule.methods)) for rule in app.app.url_map.iter_rules() if 'skip-domains' in rule.rule]"
    ```
    Expected: `GET` and `POST` (plus `OPTIONS` and `HEAD` which Flask
    adds automatically).
  - _Validates: 7.1, 7.2, 7.3, 7.4, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 9.4, 9.5, 9.6, 11.1, 11.2_
  - _Depends on: 1_

- [x] 4. Add Skip Domains section to `templates/admin.html` with inline JS
  - File: `templates/admin.html`
  - This task adds one self-contained section + one inline JS block.
    The page is already admin-only (`/admin` redirects non-admin to
    `/` per `app.py:140`), so no Jinja-level guard is needed
    (Requirement 10.4 — `design.md` §"Architecture > `templates/admin.html`
    changes" final paragraph). Edits, in source order:
    1. **HTML section**. Locate the closing `</div>` of the existing
       "Daftar Kode Akses" section (around `templates/admin.html:81`).
       Insert the new section directly after that closing `</div>`
       and BEFORE `</main>`:
       ```html
       <!-- Skip Domains -->
       <div class="form-section">
           <h2 style="margin-bottom:20px;">🚫 Skip Domains</h2>
           <p style="color:#94a3b8;margin-bottom:12px;font-size:14px;">
               Satu pattern per baris. Akun dengan domain yang memuat salah
               satu pattern (substring, case-insensitive) akan di-skip oleh
               job IMAP check.
           </p>
           <div class="form-group">
               <label for="skip-domains-textarea"><span class="label-icon">📝</span> Daftar Skip-Domains</label>
               <textarea id="skip-domains-textarea" rows="10"
                   style="width:100%;font-family:'Fira Code',monospace;font-size:14px;"
                   placeholder="hotmail&#10;live&#10;outlook"></textarea>
           </div>
           <button id="save-skip-domains-btn" class="btn-primary"
               style="margin-top:16px;background:linear-gradient(135deg,#f59e0b,#d97706);box-shadow:0 4px 20px rgba(245,158,11,0.3);">
               <span class="btn-icon">💾</span>
               <span class="btn-text">Simpan</span>
           </button>
           <div id="skip-domains-msg" class="admin-msg hidden"></div>
       </div>
       ```
       — the section sits below "Daftar Kode Akses" per Requirement
       10.1. The textarea has `rows="10"` (sufficient for the default
       7-entry seed plus expansion) and uses
       `placeholder="hotmail&#10;live&#10;outlook"` so first-time
       admins see the one-pattern-per-line convention without needing
       to load the API.
    2. **Inline JS** placed inside the existing `<script>` block at
       the bottom of the template (after the existing add-code /
       delete-code handlers — which the spec uses as the reference
       pattern). Append:
       ```javascript
       // Skip Domains: load on page mount + save on click
       const skipTextarea = document.getElementById("skip-domains-textarea");
       const skipSaveBtn = document.getElementById("save-skip-domains-btn");
       const skipMsgDiv = document.getElementById("skip-domains-msg");

       function showSkipMsg(text, isError) {
           skipMsgDiv.textContent = text;
           skipMsgDiv.className = "admin-msg " + (isError ? "error" : "success");
           skipMsgDiv.classList.remove("hidden");
           setTimeout(function () { skipMsgDiv.classList.add("hidden"); }, 3000);
       }

       (async function loadSkipDomains() {
           try {
               const res = await fetch("/api/admin/skip-domains");
               if (!res.ok) {
                   showSkipMsg("Gagal memuat: HTTP " + res.status, true);
                   return;
               }
               const data = await res.json();
               skipTextarea.value = (data.domains || []).join("\n");
           } catch (e) {
               showSkipMsg("Error: " + e.message, true);
           }
       })();

       skipSaveBtn.addEventListener("click", async function () {
           const lines = skipTextarea.value.split("\n");
           try {
               const res = await fetch("/api/admin/skip-domains", {
                   method: "POST",
                   headers: { "Content-Type": "application/json" },
                   body: JSON.stringify({ domains: lines })
               });
               const data = await res.json();
               if (res.ok && data.success) {
                   skipTextarea.value = (data.domains || []).join("\n");
                   showSkipMsg("Tersimpan (" + (data.domains || []).length + " entries)", false);
               } else if (res.status === 400 && data.rejected) {
                   showSkipMsg("Entry ditolak: " + data.rejected.join(", "), true);
               } else {
                   showSkipMsg(data.error || "HTTP " + res.status, true);
               }
           } catch (e) {
               showSkipMsg("Error: " + e.message, true);
           }
       });
       ```
       Constraints from `design.md` §"Architecture >
       `templates/admin.html` changes" + §"Risks" row "Frontend
       XSS":
       - The script MUST use `skipTextarea.value = ...` (text-only),
         NOT `innerHTML`. This neutralizes the XSS risk noted in
         `design.md` §"Risks" — admin-supplied entries are rendered
         as text, not HTML.
       - On Save: split by `"\n"` only (Requirement 10.3) — do NOT
         pre-trim or pre-deduplicate on the client. Server-side
         normalization (`_normalize_skip_entries`) is the single
         source of truth.
       - On 200 success: REPLACE textarea contents with
         `data.domains` (the normalized list the server stored), so
         the user sees post-normalization state immediately.
       - On 400 with `rejected` key: display the raw rejected entries
         joined by `", "`. Do NOT include the rule name; server
         already conforms to Requirement 9.4(d) ("rejection list
         hanya berisi entry-nya").
    3. **No CSS additions**. The classes `form-section`,
       `form-group`, `label-icon`, `btn-primary`, `btn-icon`,
       `btn-text`, `admin-msg`, and `hidden` are already defined in
       the existing `templates/admin.html` and its referenced CSS.
       Verify by grep before assuming. If any class is missing,
       reuse the inline `style=` already shown above for the section
       — do NOT add new global CSS rules.
  - Smoke check after the edit:
    ```
    python -c "from app import app; client = app.test_client(); r = client.get('/admin'); print(r.status_code)"
    ```
    Expected output: `302` (redirect to `/login` because the test
    client has no session) — the page renders for an authenticated
    admin only, and Task 5's endpoint-auth tests cover the 200 path
    with a session.
  - _Validates: 10.1, 10.2, 10.3, 10.4, 10.5_
  - _Depends on: 3_

- [x] 5. Write unit + property tests in `tests/custom-skip-domains/`
  - Files (all new, under `tests/custom-skip-domains/`):
    - `test_normalize.py`
    - `test_load_save_round_trip.py`
    - `test_post_persist.py`
    - `test_validate_reject.py`
    - `test_skip_predicate.py`
    - `test_master_untouched.py`
    - `test_first_run_seed.py`
    - `test_corrupt_file.py`
    - `test_filelock_timeout.py`
    - `test_snapshot_semantics.py`
    - `test_endpoint_auth.py`
    - `test_invalid_payload.py`
  - All Hypothesis tests use `@settings(max_examples=50)` (matching
    the cap used by `subject-keyword-match-shown-in-live`). All
    fixtures monkeypatch `imap_engine.SKIP_DOMAINS_PATH` and
    `imap_engine.SKIP_DOMAINS_LOCK_PATH` to `tmp_path`-scoped paths
    so no test ever touches the real project-root file (`design.md`
    §"Risks" row "Hypothesis flakiness in concurrency tests").

  - [x]* 5.1 Property test for normalization
    - File: `tests/custom-skip-domains/test_normalize.py`
    - **Property 1: Normalization correctness and idempotency**
    - **Validates: Requirements 4.1, 4.2**
    - Generator: `lists(text(min_size=0, max_size=300), min_size=0,
      max_size=20)` so duplicates, casing variations, leading/trailing
      whitespace, and empties are exercised.
    - Assertions per `design.md` §"Correctness Properties > Property 1":
      1. Every output element equals `s.strip().lower()` for some
         input `s`.
      2. No output element is empty.
      3. No output element appears twice.
      4. Output order matches first-occurrence order of inputs whose
         normalized form is non-empty.
      5. `_normalize_skip_entries(_normalize_skip_entries(raw)) ==
         _normalize_skip_entries(raw)` (idempotency).
    - Add a separate non-Hypothesis example test asserting that the
      transform applies ONLY strip+lower+drop-empty+dedupe — feed
      `["a-b.com", "A-B.COM", "  a-b.com  "]` and assert output is
      `["a-b.com"]`, confirming Requirement 4.3 (no other transforms
      like internal-character stripping).

  - [x]* 5.2 Property test for round trip and byte format
    - File: `tests/custom-skip-domains/test_load_save_round_trip.py`
    - **Property 2: On-disk round trip and byte-format equivalence**
    - **Property 4: Existing file preserved on read**
    - **Validates: Requirements 1.2, 2.2, 3.5, 7.2**
    - Strategy `valid_entry`: `text(min_size=1, max_size=255).filter(
      lambda s: s.strip() == s and s == s.lower() and not
      _WHITESPACE_RE.search(s))` (entries that survive normalization
      unchanged so the file content equals the input verbatim).
    - Property 2 assertions per `design.md`:
      1. After `_save_skip_domains(value)` → `_load_skip_domains() ==
         value`.
      2. Raw bytes of the file (read in `"rb"` mode) equal
         `json.dumps(value, indent=2).encode("utf-8")` — the
         byte-equivalence check from Requirement 3.5.
      3. A `GET /api/admin/skip-domains` (via Flask test_client with
         an admin session) returns `200 {"domains": value}`.
    - Property 4 assertions per `design.md`:
      1. Pre-write the file with a valid `value`. Capture
         `os.path.getmtime` and the raw bytes.
      2. Call `_load_skip_domains()` and assert return == `value`.
      3. Re-capture mtime and bytes; assert both unchanged. (Read
         path MUST NOT rewrite — Requirement 2.2.)

  - [x]* 5.3 Property test for POST normalize-and-persist with full replace
    - File: `tests/custom-skip-domains/test_post_persist.py`
    - **Property 3: POST normalize-and-persist with full replace**
    - **Validates: Requirements 8.2, 8.3**
    - Use Flask `test_client` with a `session_transaction` helper that
      sets `session["authenticated"] = True` and
      `session["is_admin"] = True`.
    - Strategy: tuple of `O = lists(valid_entry, max_size=10)`
      (pre-existing on-disk content) and `R = lists(valid_entry,
      max_size=10)` (new payload). Pre-write `O` via
      `_save_skip_domains(O)` before each example.
    - Assertions per `design.md` §"Correctness Properties > Property 3":
      1. `POST /api/admin/skip-domains` with `{"domains": R}` returns
         `200` with body `{"success": true, "domains":
         _normalize_skip_entries(R)}`.
      2. `_load_skip_domains()` returns `_normalize_skip_entries(R)`.
      3. Any element of `O` not in `_normalize_skip_entries(R)` is
         absent from the post-write file (full-replace / PUT-like).

  - [x]* 5.4 Property test for validation rejection semantics
    - File: `tests/custom-skip-domains/test_validate_reject.py`
    - **Property 5: Validation rejection semantics**
    - **Validates: Requirements 9.2, 9.3, 9.4, 9.5**
    - Strategy: `lists(text(min_size=0, max_size=300), min_size=1,
      max_size=10)` filtered to include AT LEAST one entry that
      fails empty / >255 / `\s` (use `assume(...)` to enforce). The
      whitespace pool MUST include ASCII space, `\t`, `\n`, `\r`,
      `\v`, `\f`, NBSP `U+00A0`, ideographic space `U+3000` so
      Requirement 9.3's "ASCII + Unicode whitespace" claim is
      directly exercised.
    - Assertions per `design.md`:
      1. `_validate_skip_entries(R)` returns `([], rejected)` where
         `rejected` is exactly the sublist of `R` that fails at least
         one rule, in first-occurrence order, with duplicates
         preserved and entries reported as-sent (no strip/lower).
      2. `POST /api/admin/skip-domains` with `{"domains": R}` returns
         `400 {"error": "Invalid entries", "rejected": rejected}`.
      3. `_load_skip_domains()` after the failed POST returns the
         same value as before the POST (file unchanged —
         Requirement 9.5 all-or-nothing).
    - Add a non-Hypothesis short-circuit example:
      `_validate_skip_entries(["", "x"*300, "ya hoo"])` MUST process
      each entry's checks in empty → length → whitespace order, and
      each entry MUST short-circuit on the first failed rule
      (Requirement 9.1). Assert `rejected == ["", "x"*300, "ya hoo"]`
      — all three appear in original order, no rule label leaked.

  - [x]* 5.5 Property test for skip predicate equivalence
    - File: `tests/custom-skip-domains/test_skip_predicate.py`
    - **Property 6: Skip predicate equivalence**
    - **Validates: Requirements 6.1, 6.2**
    - Strategy: `domain = text(min_size=0, max_size=50)`,
      `keyword_list = lists(valid_entry, min_size=0, max_size=10)`
      (so the empty-list case 6.2 is naturally covered).
    - Assertion per `design.md`:
      `any(kw in domain for kw in keyword_list) == any(kw in
      domain.lower() for kw in keyword_list)` — because each `kw`
      is already lowercase by the `valid_entry` strategy and the
      `_worker` evaluates against `domain.lower()`. Special-case
      `keyword_list == []` ⇒ both sides evaluate to `False`
      (Requirement 6.2).
    - Embed the predicate inside an `ImapChecker` instance for
      end-to-end fidelity: monkeypatch `_load_skip_domains` to return
      the generated `keyword_list`, instantiate
      `ImapChecker(...)` with mocked `mail_conn`, and drive
      `_worker` past one synthetic account whose `email_addr.split("@")[-1]`
      equals the generated `domain`. Assert that the account is
      classified `domain_skipped` IFF the bare predicate evaluates
      `True`.

  - [x]* 5.6 Property test for master imap_success.json untouched
    - File: `tests/custom-skip-domains/test_master_untouched.py`
    - **Property 7: Master imap_success.json untouched**
    - **Validates: Requirements 13.1, 13.2**
    - Strategy: `lists(valid_entry, min_size=0, max_size=10)`.
    - Setup: pre-create a fixture `imap_success.json` with known
      content (e.g. `{"hotmail.com": {"server": "imap-mail.outlook.com",
      "port": 993}}`) at the master-spec's `MASTER_IMAP_SUCCESS_PATH`
      (monkeypatched into `tmp_path`). Snapshot the bytes and mtime.
    - Assertions per `design.md`:
      1. After `_save_skip_domains(value)`, the bytes and mtime of
         `imap_success.json` are unchanged (Requirement 13.1).
      2. After `_load_skip_domains()`, the bytes and mtime of
         `imap_success.json` are unchanged (Requirement 13.2 — load
         path must not write to the master either).
      3. Assert `imap_success.json.lock` (the master spec's lock)
         is also unchanged (mtime + bytes), because the skip-domains
         path uses a SEPARATE `FileLock` instance (Requirement 13.3
         is structurally proven by the path constants but
         operationally proven here).

  - [x]* 5.7 Example test for first-run seed
    - File: `tests/custom-skip-domains/test_first_run_seed.py`
    - **Validates: Requirements 2.1, 12.3**
    - Setup: `monkeypatch` `SKIP_DOMAINS_PATH` and
      `SKIP_DOMAINS_LOCK_PATH` to a fresh `tmp_path`. Confirm
      `os.path.exists(SKIP_DOMAINS_PATH)` is `False` before the call.
    - Action: call `_load_skip_domains()`.
    - Assertions:
      1. Return value equals `["hotmail", "live", "msn", "outlook",
         "yahoo", "interia", "poczta.fm"]` (exact order — Requirement
         12.3).
      2. `os.path.exists(SKIP_DOMAINS_PATH)` is `True` after the
         call.
      3. `open(SKIP_DOMAINS_PATH, "rb").read() ==
         json.dumps(_DEFAULT_SKIP_SET, indent=2).encode("utf-8")`
         (byte-format equivalence with the master spec — Requirement
         3.5 cross-check).
      4. A second call to `_load_skip_domains()` returns the same
         list and does NOT rewrite the file (assert mtime unchanged
         after the second call) — bridges to Property 4.

  - [x]* 5.8 Edge-case test for corrupt file fallback
    - File: `tests/custom-skip-domains/test_corrupt_file.py`
    - **Validates: Requirements 2.3, 5.4**
    - Three sub-scenarios, each in its own test function:
      1. **Invalid JSON**: pre-write `b"{not json}"` to
         `SKIP_DOMAINS_PATH`. Assert `_load_skip_domains() == []`
         and that the file bytes are unchanged after the call (no
         auto-rewrite — Requirement 2.3).
      2. **Non-list root**: pre-write `b'{"foo": "bar"}'`. Assert
         `_load_skip_domains() == []`, file unchanged.
      3. **List with non-string element**: pre-write
         `b'["hotmail", 42, "outlook"]'`. Assert
         `_load_skip_domains() == []`, file unchanged.
    - For each: instantiate `ImapChecker(...)` (with the corrupt file
      pre-staged) and assert `self.skip_domain_keywords == []`
      (Requirement 5.4). Drive `_worker` past one synthetic account
      whose domain would have been skipped under defaults; assert the
      account is NOT classified `domain_skipped` because the empty
      list makes the `any(...)` predicate `False` for every account
      (Requirement 6.2).

  - [x]* 5.9 Example test for FileLock timeout
    - File: `tests/custom-skip-domains/test_filelock_timeout.py`
    - **Validates: Requirements 3.4(a), 3.4(b)**
    - Mock `filelock.FileLock.__enter__` (or equivalently the
      acquire path) to raise `filelock.Timeout`. Three sub-scenarios:
      1. **Constructor fallback (3.4(b))**: instantiate
         `ImapChecker(...)`. Assert no exception is raised, and
         `self.skip_domain_keywords == list(_DEFAULT_SKIP_SET)`. Capture
         stderr (via `capsys`) and assert the warning string
         `"skip-domains lock timeout"` appears.
      2. **GET endpoint (3.4(a))**: with mocked Timeout active,
         `client.get("/api/admin/skip-domains")` (admin session)
         returns `503` with body
         `{"error": "Skip-domains file is busy, please retry"}`. The
         file MUST NOT be written.
      3. **POST endpoint (3.4(a))**: with mocked Timeout active on
         `_save_skip_domains` only (passing structural+per-entry
         validation first), `client.post(...)` returns `503` with
         body `{"error": "Skip-domains file is busy, please retry"}`.
         The file MUST NOT be written.

  - [x]* 5.10 Example test for snapshot semantics non-retroactive
    - File: `tests/custom-skip-domains/test_snapshot_semantics.py`
    - **Validates: Requirements 5.3, 12.2**
    - Scenario per `design.md` §"Sequence Diagrams > Diagram 2":
      1. Pre-write `SKIP_DOMAINS_PATH` with `["hotmail"]`.
      2. Instantiate `job_a = ImapChecker(...)`. Assert
         `job_a.skip_domain_keywords == ["hotmail"]`.
      3. Mid-test, call `_save_skip_domains(["gmail"])` (simulating
         an admin POST during job_a's execution).
      4. Drive `job_a._worker` past two synthetic accounts:
         `alice@hotmail.com` (would skip under V1, NOT under V2) and
         `bob@gmail.com` (NOT skip under V1, would skip under V2).
         Assert: `alice` is classified `domain_skipped` (V1 still
         applied — Requirement 5.3 non-retroactive); `bob` is NOT
         classified `domain_skipped` (V2 not applied to running job).
      5. Instantiate `job_b = ImapChecker(...)` AFTER the save.
         Assert `job_b.skip_domain_keywords == ["gmail"]` —
         next-job snapshot reflects the new content (Requirement
         12.2).
    - This test directly mirrors the lock-serialization sequence in
      `design.md` Diagram 2, minus the lock contention timing (which
      is exercised separately by 5.9).

  - [x]* 5.11 Example test for endpoint auth (401 / 403 / 200)
    - File: `tests/custom-skip-domains/test_endpoint_auth.py`
    - **Validates: Requirements 7.1, 7.3, 7.4, 8.1, 8.5, 8.6**
    - Use Flask `test_client` with three session contexts:
      unauthenticated (no `session["authenticated"]`),
      authenticated non-admin (`session["authenticated"] = True`,
      `session["is_admin"] = False`), and authenticated admin (both
      True).
    - For each session context, hit BOTH `GET` and `POST`
      `/api/admin/skip-domains`. Assertions:
      | Context | GET status | GET body | POST status | POST body |
      |---|---|---|---|---|
      | Unauthenticated | 401 | `{"error": "Unauthorized"}` | 401 | `{"error": "Unauthorized"}` |
      | Authenticated non-admin | 403 | `{"error": "Admin only"}` | 403 | `{"error": "Admin only"}` |
      | Authenticated admin | 200 | `{"domains": [...]}` | 200 | `{"success": true, "domains": [...]}` (with valid payload) |
    - The 401 path is delivered by the existing `@login_required`
      decorator (`app.py:65`) — assert that Task 3 did NOT
      accidentally short-circuit it.

  - [x]* 5.12 Edge-case test for invalid payload (400 Invalid payload)
    - File: `tests/custom-skip-domains/test_invalid_payload.py`
    - **Validates: Requirements 8.4, 9.6**
    - With an admin session, hit `POST /api/admin/skip-domains` with
      each of the following bodies. Assert each returns `400` with
      body `{"error": "Invalid payload"}` and that
      `_load_skip_domains()` is unchanged after each call:
      1. Plain text body (`Content-Type: text/plain`, body `"hello"`)
         — `request.get_json(silent=True)` returns `None`.
      2. JSON body that is not a dict: `[1, 2, 3]` and `"hi"` and
         `null`.
      3. Dict body without `domains` key: `{}` and `{"foo": "bar"}`.
      4. Dict body with `domains` not a list: `{"domains": "hotmail"}`
         and `{"domains": {"a": 1}}`.
      5. Dict body with `domains` containing non-string elements:
         `{"domains": ["hotmail", 42, "outlook"]}`,
         `{"domains": [null]}`, `{"domains": [{"x": 1}]}`. These MUST
         return `"Invalid payload"`, NOT `"Invalid entries"` —
         confirms Requirement 9.6's routing through Requirement 8.4
         (per `design.md` §"Architecture > `app.py` changes" note).

  - _Validates: 1.2, 2.1, 2.2, 2.3, 3.4, 3.5, 4.1, 4.2, 5.3, 5.4, 6.1, 6.2, 7.1, 7.2, 7.3, 7.4, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 9.2, 9.3, 9.4, 9.5, 9.6, 12.2, 12.3, 13.1, 13.2_
  - _Depends on: 1, 2, 3, 4_

- [x] 6. Final cross-suite validation, smoke import, and master byte-identical assertion
  - This is the workflow's integration sign-off. No new code or tests
    are written here — only command-line verification.
  - Steps, in order:
    1. **Run the new feature suite**:
       ```
       python -m pytest tests/custom-skip-domains/ -v
       ```
       Expected: every test under `tests/custom-skip-domains/` passes
       (the 12 sub-tasks in Task 5 contributed all the test files).
       Optional sub-tasks marked with `*` are still expected to pass
       when run; the `*` only governs whether the task is scheduled
       during implementation, not whether the test is valid.
    2. **Run the `centralize-imap-success-master` suite** (Requirement
       14.1):
       ```
       python -m pytest tests/centralize-imap-success-master/ -v
       ```
       Expected: every test passes without modification. This guards
       against accidental regression of the `_atomic_update_master`
       and master-file flow that this feature deliberately does NOT
       touch (`design.md` §"Architecture > `imap_engine.py` changes"
       and §"Risks" row "FileLock contention with master").
    3. **Run the `subject-keyword-match-shown-in-live` suite**
       (Requirement 14.2):
       ```
       python -m pytest tests/subject-keyword-match-shown-in-live/ -v
       ```
       Expected: every test passes without modification. The
       `_worker` predicate change in Task 2 is one line and only
       changes the iterable; the SUBJECT-branch fix introduced by
       that earlier spec is in different code (post-fetch sender
       gate), so the suites are orthogonal.
    4. **Smoke import** of the seven new public surfaces from
       `imap_engine`:
       ```
       python -c "from imap_engine import _load_skip_domains, _save_skip_domains, _normalize_skip_entries, _validate_skip_entries, SKIP_DOMAINS_PATH, SKIP_DOMAINS_LOCK_PATH, _DEFAULT_SKIP_SET; print('OK')"
       ```
       Expected output: `OK`. If any symbol fails to import, Task 1
       drift is the most likely cause — re-check the `_DEFAULT_SKIP_SET`
       type (must be `list`) and the helper function names exactly.
    5. **Confirm the project-root `imap_success.json` is byte-identical
       before and after the entire test suite run** (Requirement 13.1
       cross-check end-to-end):
       ```
       python -c "import hashlib, pathlib; p = pathlib.Path('imap_success.json'); print(hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else 'absent')"
       ```
       Capture this digest BEFORE running any of the test commands
       above, then re-capture AFTER all three pytest invocations
       complete. The two digests MUST be equal — the test suites only
       touch `tmp_path`-scoped files, never the real master. (If the
       file does not exist on this machine, both captures will print
       `absent` — that is also acceptable; the assertion is "no
       change", not "exists".)
    6. **Confirm `requirements.txt` is unchanged** (Requirement 14.3):
       run `git diff --stat -- requirements.txt`. Expected: empty
       output (no lines changed). The feature reuses `filelock`
       already pinned by `centralize-imap-success-master`.
    7. **Confirm the new route surface is exactly two new entries**
       (Requirement 11.2):
       ```
       python -c "import app; rules = sorted(rule.rule for rule in app.app.url_map.iter_rules()); skip_rules = [r for r in rules if 'skip-domains' in r]; print('skip-domains routes:', skip_rules); print('total routes:', len(rules))"
       ```
       Expected: `skip-domains routes:
       ['/api/admin/skip-domains']` (or two entries if Flask
       registered GET and POST as separate rules — both are
       acceptable). The pre-feature route count plus 1 (or 2) MUST
       equal the post-feature total — diff against a pre-feature
       snapshot if available.
  - **Workflow completion notice**: this is the final task. Per the
    feature-requirements-first workflow, implementation of subsequent
    bug fixes or enhancements should be triggered via new specs, not
    appended here. Once all six tasks (and their non-optional
    sub-tasks 5.x) pass, the spec is done.
  - _Validates: all of 1.1, 1.2, 1.3, 2.1, 2.2, 2.3, 3.1, 3.2, 3.3, 3.4, 3.5, 4.1, 4.2, 4.3, 5.1, 5.2, 5.3, 5.4, 6.1, 6.2, 6.3, 7.1, 7.2, 7.3, 7.4, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 10.1, 10.2, 10.3, 10.4, 10.5, 11.1, 11.2, 12.1, 12.2, 12.3, 13.1, 13.2, 13.3, 14.1, 14.2, 14.3 (final integration sign-off)_
  - _Depends on: 5_

## Notes

- **Layered execution order**. Tasks 1 → 2 → 3 → 4 → 5 → 6 form a
  strict dependency chain. The user-supplied wave plan was chosen for
  safety: even where two layers (e.g. Task 3 endpoints + Task 4 UI)
  could be authored in parallel, serializing them keeps each wave's
  diff small and reviewable, mirroring the `subject-keyword-match-shown-in-live`
  pattern.
- **Foundation isolation in Task 1**. Task 1 deliberately does NOT
  remove `DOMAINS_TO_SKIP_KEYWORDS` — that removal is paired with the
  `_worker` rewrite in Task 2 so the file is never in a state where
  `_worker` references an undefined symbol. Between waves the module
  must always be `import`-able.
- **Optional test sub-tasks under Task 5**. Each property test is a
  separate sub-task marked with `*` so MVP-mode implementation can
  land Tasks 1–4 + Task 6 without writing the property suite.
  However, **Task 6 step 1 will fail** if Task 5 is fully skipped
  (because `tests/custom-skip-domains/` will be empty). Skip
  individual sub-tasks but plan to land at least Properties 2, 3, 5,
  6, 7 plus the auth + invalid-payload examples to satisfy
  Requirements 14.1 + 14.2's spirit (existing suites green +
  feature properly tested).
- **Cross-spec preservation is structural**. The
  `centralize-imap-success-master` and
  `subject-keyword-match-shown-in-live` suites are guaranteed green
  by construction:
  - This feature's `FileLock` uses a separate path
    (`skip_domains.json.lock` vs `imap_success.json.lock`), so the
    master spec's atomic-write tests are unaffected.
  - The `_worker` edit is one line, changing only the iterable on
    the right of `for kw in`, leaving the post-fetch sender gate
    (the subject-keyword spec's domain) byte-identical.
  Task 6 steps 2 and 3 verify both invariants empirically.
- **No new dependencies**. `filelock` was added by
  `centralize-imap-success-master`. `re`, `os`, `json`, `sys` are
  stdlib. `hypothesis` and `pytest` are already pinned by the test
  suite of the prior specs. Requirement 14.3 holds without any
  `requirements.txt` edit.
- **Hypothesis cache reuse**. The `.hypothesis/` directory at the
  repo root is shared across spec suites; the new property tests
  will populate it under their own example IDs without colliding
  with prior specs.
- **Workflow completion**. After Task 6 passes, the feature is done.
  Implementation of the tasks themselves is OUT of scope for this
  workflow — open `tasks.md` and click "Start task" next to each
  item to execute.
