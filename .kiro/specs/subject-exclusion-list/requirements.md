# Requirements Document

## Introduction

Today, `imap_engine.py` excludes one specific class of fetched email
from `live.txt` via a hardcoded module-level regex
`OTP_SUBJECT_REGEX = re.compile(r'^Booking\.com – \w+ is your verification code$')`,
applied inside `ImapChecker._worker` as
`if not OTP_SUBJECT_REGEX.match(subject.strip()):`. The pattern can only
be changed by editing source and redeploying, so admins cannot add new
subject-level exclusions (for example notification-only mailings that
should never count as a "live" hit) or relax the existing one without a
code change.

This feature replaces that single hardcoded regex with a globally
persisted, admin-editable list of substring patterns. For each fetched
email the engine builds the decoded `Subject`, lowercases it, and
excludes the email from `emails_data` if any pattern (also lowercased)
appears as a substring of the lowercased subject. The list is stored as
JSON on the server filesystem at the project root, read atomically and
cross-process safely via `filelock.FileLock` + `tmp` + `os.replace`
(reusing the pattern shared with `centralize-imap-success-master` and
`custom-skip-domains`), seeded on first read with a single substring
`"is your verification code"` so that subjects matching the previous
`OTP_SUBJECT_REGEX` continue to be excluded out of the box.

The list is exposed through the existing admin page
(`templates/admin.html`) with a textarea (one pattern per line) and a
pair of admin-gated endpoints (`GET` / `POST
/api/admin/subject-exclusion-list`). The semantics flip relative to the
old gate: the previous code kept emails whose subject did NOT match the
regex; the new code drops emails whose subject DOES match any pattern.
With the seeded default, end-to-end behavior is preserved for admins
who never edit the list. After this feature ships, the constant
`OTP_SUBJECT_REGEX` is removed from `imap_engine.py` and the exclusion
list is the only Subject-level filter.

Out of scope: per-user / per-job exclusion lists, regex or wildcard
semantics (substring only, Unicode-friendly via lowercasing on both
sides), and any From / sender-side filtering.

## Glossary

- **Subject_Exclusion_File**: The single JSON file at the project root,
  `subject_exclusion_list.json`, in the same directory as
  `imap_engine.py`, `app.py`, the master `imap_success.json`, and
  `skip_domains.json`. Format: a JSON array of strings (e.g.
  `["is your verification code"]`).
- **Subject_Exclusion_Lock_Path**: The cross-process lock file path
  used by `filelock.FileLock` to coordinate read/write of
  `Subject_Exclusion_File`. Equal to
  `Subject_Exclusion_File + ".lock"`.
- **Default_Subject_Exclusion_Patterns**: The seed written on first
  read when `Subject_Exclusion_File` does not exist — exactly
  `["is your verification code"]`. This single substring is the
  invariant trailing phrase of every subject the previous
  `OTP_SUBJECT_REGEX` (`^Booking\.com – \w+ is your verification code$`)
  matched, so seeding with it preserves the prior exclusion semantics
  by default.
- **Engine**: The `ImapChecker` class in `imap_engine.py`. Its
  constructor loads the active list into the instance attribute
  `subject_exclusion_patterns` (a `list[str]` of already-lowercased
  patterns), which `_worker` consults via `_subject_excluded` to decide
  whether to drop a fetched email.
- **Admin_UI**: The `/admin` page (`templates/admin.html`),
  admin-gated by `@login_required` plus `session.get("is_admin")`. A
  new section is added to this template; no other template is touched.
- **Admin_API**: The pair of new Flask endpoints,
  `GET /api/admin/subject-exclusion-list` and
  `POST /api/admin/subject-exclusion-list`. Both go through the
  existing `@login_required` decorator plus a `session.get("is_admin")`
  check (the same mechanism used by `/api/admin/add-code`,
  `/api/admin/delete-code`, and `/api/admin/skip-domains`).
- **Subject_Exclusion_Entry**: One pattern stored in
  `Subject_Exclusion_File`. After normalization for storage the entry
  is `strip()`ped, `lower()`ed, deduplicated (preserving first
  occurrence order), and checked for empty / oversize / control-char
  content (Requirement 9). Internal ASCII spaces and other non-control
  whitespace inside the pattern are kept.
- **`_subject_excluded`**: A new pure helper at module level in
  `imap_engine.py`, signature `_subject_excluded(subject: str, patterns:
  list[str]) -> bool`. Returns `True` iff
  `any(p in subject.lower() for p in patterns)` is `True`, where
  `patterns` is assumed to be already lowercased by the loader.
- **OTP_SUBJECT_REGEX (legacy)**: The compiled regex
  `^Booking\.com – \w+ is your verification code$` previously defined
  at module level in `imap_engine.py` and used inside `_worker`. After
  this feature ships, this constant is **removed** from
  `imap_engine.py`; the exclusion list is the sole Subject-level
  filter.

## Requirements

### Storage and Defaults

#### Requirement 1: File location and on-disk format

**User Story:** As an admin operating the server, I want the
subject-exclusion list stored in a single global file at the project
root, so every job and worker process reads the same configuration and
I always know where to find it.

#### Acceptance Criteria

1. THE Engine SHALL define `Subject_Exclusion_File` as a module-level constant in `imap_engine.py` evaluated to `os.path.join(os.path.dirname(os.path.abspath(__file__)), "subject_exclusion_list.json")`, so the path always points to the directory of `imap_engine.py` (project root) regardless of the process current working directory.
2. THE Engine SHALL store the contents of `Subject_Exclusion_File` as a UTF-8 JSON document whose top-level value is a JSON array of strings, written with `json.dump(value, f, indent=2)` (e.g. `["is your verification code"]`), byte-equivalent to the on-disk format used by `custom-skip-domains` and `centralize-imap-success-master`.
3. THE Engine SHALL define `Subject_Exclusion_Lock_Path` as a module-level constant equal to `Subject_Exclusion_File + ".lock"` and use it as the sentinel passed to `filelock.FileLock`.

#### Requirement 2: First-run seeding preserves prior OTP exclusion

**User Story:** As an admin upgrading to the new version, I want the
exclusion file to be created automatically with a default that is
behaviorally equivalent to the previous `OTP_SUBJECT_REGEX`, so that
subjects which used to be excluded continue to be excluded without me
touching anything.

#### Acceptance Criteria

1. WHEN `Subject_Exclusion_File` does not exist on disk and the Engine or Admin_API attempts to read the list, THE Engine SHALL initialize the file with `Default_Subject_Exclusion_Patterns` (exactly `["is your verification code"]`) using the atomic write path of Requirement 3.
2. WHEN `Subject_Exclusion_File` already exists on disk, THE Engine SHALL read its contents as-is and SHALL NOT overwrite it with `Default_Subject_Exclusion_Patterns`, so admin edits (including deleting the seeded entry) are preserved permanently.
3. IF `Subject_Exclusion_File` exists but cannot be parsed as valid JSON, or the parsed value is not a JSON array whose elements are all strings, THEN THE Engine SHALL treat the active exclusion list for the running job as the empty list (`[]`) and SHALL NOT auto-rewrite the corrupt file as a side effect of init.
4. THE Engine SHALL choose `Default_Subject_Exclusion_Patterns = ["is your verification code"]` such that, for every string `S` matching the previous regex `^Booking\.com – \w+ is your verification code$`, `_subject_excluded(S, ["is your verification code"])` evaluates to `True`, so the seeded default preserves the OTP exclusion semantics of `OTP_SUBJECT_REGEX` for those subjects.

#### Requirement 3: Atomic write and cross-process safety

**User Story:** As an admin running the application behind a
multi-worker WSGI server, I want changes to the exclusion list to be
safe under crashes and inter-process races, so the file is never
corrupted and updates are not lost.

#### Acceptance Criteria

1. WHEN Admin_API or Engine writes a change to `Subject_Exclusion_File`, THE Engine SHALL execute the following filesystem operations in order, observable to a tester: (a) `open(Subject_Exclusion_File + ".tmp", "w", encoding="utf-8")` creating or truncating a tmp file in the same directory as the target, (b) `json.dump(value, f, indent=2)` where `value` is the normalized `list[str]` so that on-disk byte equivalence with `_atomic_update_master` and `_save_skip_domains` holds, (c) `f.flush()`, (d) `os.fsync(f.fileno())`, (e) close the file (end of the `with` block), (f) `os.replace(tmp_path, Subject_Exclusion_File)` for an atomic rename (POSIX and Windows, provided source and target share a filesystem).
2. WHEN Admin_API or Engine performs a read-modify-write cycle on `Subject_Exclusion_File`, THE Engine SHALL wrap the entire cycle in `filelock.FileLock(Subject_Exclusion_Lock_Path, timeout=MASTER_LOCK_TIMEOUT_SECONDS)` (reusing the existing `MASTER_LOCK_TIMEOUT_SECONDS` constant), so that at most one process across all workers can read-modify-write at a time; `Subject_Exclusion_Lock_Path` MAY persist on disk between runs because `filelock` releases via the OS file handle, not file existence, and Engine SHALL NOT delete or treat any leftover `.lock` file as stale during startup.
3. IF the process crashes, is killed, or runs out of disk space after `Subject_Exclusion_File + ".tmp"` is partially written but before `os.replace` completes, THEN THE Engine SHALL leave `Subject_Exclusion_File` in its previous valid state — the file is never observed truncated, partially written, or containing a mix of old and new bytes — an invariant guaranteed by the tmp + `os.replace` sequence; if a leftover `.tmp` file remains on disk from a prior failure, Engine SHALL NOT treat it as a source of truth on the next run (only the `Subject_Exclusion_File` path renamed by `os.replace` is authoritative).
4. IF `filelock.FileLock` cannot be acquired within `MASTER_LOCK_TIMEOUT_SECONDS` seconds, THEN THE Engine SHALL raise `filelock.Timeout` to the caller, and the caller SHALL handle it as follows: (a) for Admin_API handlers (`GET` / `POST /api/admin/subject-exclusion-list`), return HTTP 503 with body JSON `{"error": "Subject-exclusion list is busy, please retry"}` and SHALL NOT write to `Subject_Exclusion_File`; (b) for `ImapChecker.__init__` during first-run seeding, fall back to `Default_Subject_Exclusion_Patterns` in memory as `self.subject_exclusion_patterns` (already lowercased) without marking the file as seeded and without raising from the constructor (worker process does not crash).
5. THE Engine SHALL produce on-disk bytes byte-identical to the output of `json.dumps(value, indent=2).encode("utf-8")` for the same `value` (on platforms without newline translation), matching the format produced by `_atomic_update_master` (master `imap_success.json`) and `_save_skip_domains` (`skip_domains.json`); a tester can verify byte equivalence via a byte-level diff between the file written by this feature and the reference `json.dumps` output.

#### Requirement 4: Storage normalization

**User Story:** As an admin typing patterns into the textarea, I want
entries stored in a consistent canonical form, so the substring match
in the engine is always deterministic.

#### Acceptance Criteria

1. WHEN Admin_API receives a list of patterns from the client to save, THE Engine SHALL apply this transformation sequence in exactly this order: (a) `strip()` each entry, (b) `lower()` each entry, (c) drop entries that are empty after `strip()`, (d) deduplicate while preserving the first-occurrence order from the input.
2. WHILE normalization is running, THE Engine SHALL preserve the first-occurrence order of the input as the order written into `Subject_Exclusion_File`.
3. THE Engine SHALL NOT apply transformations beyond the steps in 4.1 — internal ASCII spaces and other non-control whitespace inside an entry are NOT removed, NOT collapsed, and NOT replaced (e.g. `"is your verification code"` is stored verbatim after `strip()`/`lower()`, with its three internal spaces intact).
4. THE Engine SHALL persist the result of step 4.1 to `Subject_Exclusion_File`, so that any subsequent reader (the Engine on the next job, the Admin_API on a `GET`) observes already-lowercased patterns and does not need to lowercase them again before passing them to `_subject_excluded`.

### Engine Integration

#### Requirement 5: ImapChecker reads the exclusion list at init

**User Story:** As a user running an IMAP check, I want each job to use
the latest exclusion list at the moment it starts, so admin edits take
effect for the next job without restarting the application.

#### Acceptance Criteria

1. WHEN `ImapChecker.__init__` is invoked for a new job, THE Engine SHALL read `Subject_Exclusion_File` (seeding `Default_Subject_Exclusion_Patterns` on first run per Requirement 2.1) inside `filelock.FileLock(Subject_Exclusion_Lock_Path, timeout=MASTER_LOCK_TIMEOUT_SECONDS)` — read-only acquire is accepted because the same lock coordinates the whole read-modify-write per Requirement 3.2 — and store the result as the instance attribute `self.subject_exclusion_patterns` typed as `list[str]`, with every element already lowercased; this read SHALL complete BEFORE the worker threads (`self._worker`) are spawned in `run()`, so the per-job snapshot is in place before parallel execution begins.
2. THE Engine SHALL remove the module-level constant `OTP_SUBJECT_REGEX` from `imap_engine.py`, making the loader of `Subject_Exclusion_File` the single source of truth for Subject-level exclusion; `Default_Subject_Exclusion_Patterns` SHALL remain as a private module-level constant used only for first-run seeding (Requirement 2.1) and for the `__init__` fallback path of Requirement 3.4(b), and SHALL NOT be referenced from `_worker` or from the per-email exclusion evaluation.
3. WHEN two or more jobs run concurrently in the same worker process or in separate WSGI worker processes, THE Engine SHALL give each job the snapshot of the exclusion list that was loaded when that job's `__init__` ran, so that an edit submitted via `POST /api/admin/subject-exclusion-list` while another job is mid-run SHALL NOT mutate `self.subject_exclusion_patterns` for the running job and SHALL NOT change which emails are dropped from `emails_data` for that job (snapshot semantics per job, non-retroactive).
4. IF `Subject_Exclusion_File` exists on disk but cannot be parsed as JSON or is not an array of strings (the case handled by Requirement 2.3), THEN `ImapChecker.__init__` SHALL set `self.subject_exclusion_patterns = []` for that job, `_worker` SHALL evaluate `_subject_excluded(subject, [])` as `False` for every email (no email is dropped purely because the list is empty, per Requirement 6.2), and the Engine SHALL NOT auto-rewrite `Subject_Exclusion_File` as a side effect of init (the corrupt file stays as-is until the admin replaces it via `POST /api/admin/subject-exclusion-list`).

#### Requirement 6: `_subject_excluded` helper and `_worker` integration

**User Story:** As a developer maintaining `_worker`, I want a single
named helper to decide whether a subject is excluded, so the call site
in `_worker` is small, the semantics are explicit, and the empty-list
case is unambiguous.

#### Acceptance Criteria

1. THE Engine SHALL define a module-level pure helper `_subject_excluded(subject: str, patterns: list[str]) -> bool` in `imap_engine.py` that returns `True` if and only if `any(p in subject.lower() for p in patterns)` is `True`; the helper SHALL assume `patterns` is already lowercased by the loader and SHALL NOT lowercase elements of `patterns` again.
2. WHEN `patterns` is the empty list `[]`, THE Engine SHALL evaluate `_subject_excluded(subject, [])` as `False` for every `subject` (because Python's `any` over an empty iterable is `False`), so an empty list excludes nothing.
3. WHEN `_worker` reaches the per-email exclusion gate inside the fetch loop, THE Engine SHALL replace the previous line `if not OTP_SUBJECT_REGEX.match(subject.strip()):` with a guard that excludes the email when `_subject_excluded(subject, self.subject_exclusion_patterns)` returns `True`, equivalently kept by `if not _subject_excluded(subject, self.subject_exclusion_patterns):`, preserving the surrounding control flow (sender / SUBJECT-branch union check, `emails_data.append(...)`, etc.) byte-for-byte except for this single boolean expression and any call-site reformat.
4. WHEN `_worker` calls `_subject_excluded`, THE Engine SHALL pass the decoded subject `subject = decode_mime_words(msg.get("Subject", ""))` (the same decoded value used elsewhere in the loop) WITHOUT a preceding `.strip()` — the legacy `subject.strip()` was needed because the regex was anchored with `^...$`, but substring matching is insensitive to leading/trailing whitespace in `subject`, and lowercasing inside the helper handles Unicode case folding consistently.
5. THE Engine SHALL evaluate the substring check as case-insensitive on BOTH sides: the loader has already lowercased every element of `self.subject_exclusion_patterns` (Requirement 4.4), and `_subject_excluded` lowercases `subject` exactly once via `subject.lower()` before the `in` check, so a pattern stored as `"is your verification code"` matches a subject `"Booking.com – ABC123 IS YOUR VERIFICATION CODE"`.

### Admin UI and Endpoints

#### Requirement 7: GET endpoint to read the current list

**User Story:** As an admin opening the admin page, I want to see the
current exclusion list, so I can edit from the up-to-date state.

#### Acceptance Criteria

1. THE Admin_API SHALL expose `GET /api/admin/subject-exclusion-list`, protected by the `@login_required` decorator and requiring `session.get("is_admin")` to be truthy.
2. WHEN a `GET /api/admin/subject-exclusion-list` request arrives from a user authenticated as admin, THE Admin_API SHALL read `Subject_Exclusion_File` (seeding the default per Requirement 2.1 if missing) and return JSON `{"patterns": [<entry>, ...]}` with HTTP status 200, where the order of entries is the order present in the on-disk file (already normalized per Requirement 4).
3. IF a `GET /api/admin/subject-exclusion-list` request arrives from a user that is not authenticated (no `session["authenticated"]`), THEN THE Admin_API SHALL return HTTP 401 with body JSON `{"error": "Unauthorized"}`, matching the existing `@login_required` behavior for paths beginning with `/api/`.
4. IF a `GET /api/admin/subject-exclusion-list` request arrives from a user that is authenticated but not admin, THEN THE Admin_API SHALL return HTTP 403 with body JSON `{"error": "Admin only"}`, matching the existing pattern of `/api/admin/add-code` and `/api/admin/skip-domains`.
5. IF `filelock.FileLock` for `Subject_Exclusion_Lock_Path` cannot be acquired within `MASTER_LOCK_TIMEOUT_SECONDS` while serving a `GET /api/admin/subject-exclusion-list` request, THEN THE Admin_API SHALL return HTTP 503 with body JSON `{"error": "Subject-exclusion list is busy, please retry"}` per Requirement 3.4(a).

#### Requirement 8: POST endpoint to replace the entire list

**User Story:** As an admin, I want to replace the exclusion list with
a single submit, so the semantics are unambiguous (PUT-like) and I do
not have to call separate add/remove endpoints per entry.

#### Acceptance Criteria

1. THE Admin_API SHALL expose `POST /api/admin/subject-exclusion-list`, protected by the `@login_required` decorator and requiring `session.get("is_admin")` to be truthy.
2. WHEN `POST /api/admin/subject-exclusion-list` arrives from a valid admin with body JSON `{"patterns": [<string>, ...]}`, THE Admin_API SHALL validate every entry per Requirement 9, normalize the surviving entries per Requirement 4, then — if all entries pass validation — write the normalized list to `Subject_Exclusion_File` via the atomic path of Requirement 3 and return HTTP 200 with body JSON `{"success": true, "patterns": [<string>, ...]}` where `patterns` is the list actually persisted.
3. WHEN the write of Requirement 8.2 succeeds, THE Admin_API SHALL fully replace the contents of `Subject_Exclusion_File` (PUT-like): entries previously present but absent from the normalized payload SHALL be removed from the final file.
4. IF the request body is not valid JSON, OR does not contain a key `patterns` whose value is a JSON array, OR any element of that array is not a string, THEN THE Admin_API SHALL return HTTP 400 with body JSON `{"error": "Invalid payload"}` and SHALL NOT write to `Subject_Exclusion_File`.
5. IF the request arrives from an unauthenticated user, THEN THE Admin_API SHALL return HTTP 401 with body JSON `{"error": "Unauthorized"}`.
6. IF the request arrives from an authenticated non-admin user, THEN THE Admin_API SHALL return HTTP 403 with body JSON `{"error": "Admin only"}`.
7. IF `filelock.FileLock` for `Subject_Exclusion_Lock_Path` cannot be acquired within `MASTER_LOCK_TIMEOUT_SECONDS` while serving a `POST /api/admin/subject-exclusion-list` request, THEN THE Admin_API SHALL return HTTP 503 with body JSON `{"error": "Subject-exclusion list is busy, please retry"}` per Requirement 3.4(a) and SHALL NOT write to `Subject_Exclusion_File`.

#### Requirement 9: Per-entry validation on save

**User Story:** As an admin, I want the system to reject malformed
entries before they are saved, so the list the engine eventually uses
always makes sense as a substring pattern against an email Subject.

#### Acceptance Criteria

1. THE Admin_API SHALL apply the per-entry validation checks in this DETERMINISTIC order, short-circuiting on the first failure for a given entry: FIRST check that `entry.strip()` is non-empty (Requirement 9.2), SECOND check that `len(entry.strip()) <= 500` (Requirement 9.3), THIRD check that `entry.strip()` contains no character `c` such that `c in {"\r", "\n", "\t", "\v", "\f", "\x00"}` or `ord(c) < 0x20` AND `c != " "` (Requirement 9.4); the first failing check stops further checks for that entry, and the rejection list reports the entry verbatim, never which rule failed.
2. THE Admin_API SHALL reject any entry whose value is empty after `.strip()` as invalid (FIRST check).
3. THE Admin_API SHALL reject any entry whose `.strip()`ed length exceeds 500 characters as invalid (SECOND check). 500 is chosen larger than the 255-character limit of `custom-skip-domains` because subject patterns are natural-language phrases that may legitimately be longer than a domain keyword.
4. THE Admin_API SHALL reject any entry that, after `.strip()`, contains at least one control character — defined as: any character `c` with `c in {"\r", "\n", "\t", "\v", "\f", "\x00"}`, OR `ord(c) < 0x20 and c != " "` — as invalid (THIRD check). Internal ASCII space `" "` (`U+0020`) is explicitly allowed (e.g. `"is your verification code"` passes), and other non-control whitespace (NBSP `U+00A0`, ideographic space `U+3000`, etc.) is also allowed because subject patterns naturally contain them.
5. IF one or more entries in a `POST /api/admin/subject-exclusion-list` payload violate Requirement 9.2, 9.3, or 9.4, THEN THE Admin_API SHALL return HTTP 400 with body JSON `{"error": "Invalid entries", "rejected": [<entry_as_sent>, ...]}` where (a) `rejected` reports each offending entry exactly as the user submitted it (before any `.strip()`/`.lower()`), (b) entries appear in `rejected` in their first-occurrence order in the payload, (c) duplicates are reported as-sent if duplicated invalid entries were submitted, and (d) the response SHALL NOT expose which rule failed for which entry; the Admin_API SHALL NOT write to `Subject_Exclusion_File`.
6. WHEN validation fails (Requirement 9.5), THE Admin_API SHALL leave `Subject_Exclusion_File` exactly as it was — no partial write, no persistence of the entries that did pass validation in the same payload (all-or-nothing semantics).
7. THE Admin_API SHALL assume each element of the `patterns` array is a string when running checks 9.2–9.6; IF an element is not a string (integer, null, dict, list, etc.), THEN handling SHALL be delegated to Requirement 8.4 — the payload is structurally invalid and the response is HTTP 400 with body `{"error": "Invalid payload"}`, with no per-entry validation executed for that array.

#### Requirement 10: New section in the admin page

**User Story:** As an admin, I want to manage the exclusion list from
the same web page as the other admin sections, so I do not need an
external tool.

#### Acceptance Criteria

1. THE Admin_UI SHALL add one new section to `templates/admin.html` containing a heading, one textarea for subject-exclusion patterns (one pattern per line), one "Simpan" / "Save" button, and a status message element, styled consistently with the existing skip-domains section.
2. WHEN `/admin` is rendered for an admin user, THE Admin_UI SHALL load the current list via a client-side `GET /api/admin/subject-exclusion-list` call (or the equivalent Jinja context) and populate the textarea with one entry per line in the order returned by the endpoint.
3. WHEN the admin clicks "Save", THE Admin_UI SHALL parse the textarea content with `split("\n")`, send the payload `{"patterns": [<line>, ...]}` (preserving line order, including blank lines so the server-side `strip()`+empty-drop applies) to `POST /api/admin/subject-exclusion-list`, and display a success message on HTTP 200 or an error message on HTTP 400/401/403/5xx based on the endpoint response.
4. WHERE the user is not admin (`session.get("is_admin")` is falsy), THE Admin_UI SHALL NOT render the subject-exclusion section in any template (the `/admin` route already redirects non-admins to `/`, so the guard is enforced at the route level and the template is only reachable for admins).
5. THE Admin_UI SHALL use the existing authentication mechanism (`@login_required` plus `session.get("is_admin")`) for every interaction with the subject-exclusion section, and SHALL NOT introduce an additional auth layer.

### Backward Compatibility / Preservation

#### Requirement 11: No regressions on existing API surface

**User Story:** As a user or integrator of the existing endpoints, I
want every current Flask route to behave identically, so existing
clients are not broken.

#### Acceptance Criteria

1. THE Engine SHALL NOT modify the contract (path, method, request body, response body, status code) of any of these endpoints: `/login`, `/logout`, `/`, `/get-email`, `/admin`, `/api/admin/add-code`, `/api/admin/delete-code`, `/api/admin/skip-domains` (GET and POST), `/api/check`, `/api/stop/<job_id>`, `/api/status/<job_id>`, `/api/download/<job_id>/<file_type>`, `/api/get-email`, `/api/delete-email`, `/history`, `/api/jobs`, `/api/jobs/<job_id>/extend`, `/api/jobs/<job_id>/delete`, `/api/jobs/<job_id>/live`, `/loop-delete`, `/api/loop-delete/start`, `/api/loop-delete/stop/<job_id>`, `/api/loop-delete/restart/<job_id>`, `/api/loop-delete/delete/<job_id>`, `/api/loop-delete/status/<job_id>`, `/api/loop-delete/list`.
2. THE Engine SHALL add only the new endpoints `GET /api/admin/subject-exclusion-list` and `POST /api/admin/subject-exclusion-list`, without touching any other Flask path.

#### Requirement 12: Default-seeded exclusion preserves OTP semantics

**User Story:** As an admin who never edits the list after upgrading,
I want subjects matching the previous `OTP_SUBJECT_REGEX` to still be
excluded, so the upgrade is transparent for my users.

#### Acceptance Criteria

1. WHEN the admin has never called `POST /api/admin/subject-exclusion-list` after deploying the new version — covering both (a) `Subject_Exclusion_File` does not exist at deploy time and is then seeded by the Engine on the first read in `ImapChecker.__init__`, and (b) the admin's first interaction is a `GET /api/admin/subject-exclusion-list` (read-only, also triggers first-run seeding via Requirement 2.1) — so that `Subject_Exclusion_File` contains `Default_Subject_Exclusion_Patterns` verbatim, THE Engine SHALL exclude every fetched email whose decoded `Subject` would have matched the previous compiled pattern `^Booking\.com – \w+ is your verification code$` from `emails_data`, because for any such subject `S` the substring `"is your verification code"` is contained in `S.lower()`.
2. WHEN the admin has never called `POST /api/admin/subject-exclusion-list` and the file holds `Default_Subject_Exclusion_Patterns`, THE Engine SHALL produce `live.txt`, `noemail.txt`, `die.txt`, `unreg.txt`, and `domain_skipped.txt` for a job whose only candidate emails are subjects matching `^Booking\.com – \w+ is your verification code$` byte-identical to the output produced by the implementation prior to this feature for the same job inputs and the same deterministic IMAP fixture; "byte-identical" means the SHA-256 digest of each per-job file matches the reference.
3. WHEN the admin has edited the list via `POST /api/admin/subject-exclusion-list` and the write of Requirement 3.1 has completed (`os.replace` finished), THE Engine SHALL treat the edited list as the new source of truth on the NEXT `ImapChecker.__init__` invocation, so the new job's `emails_data` reflects the edited list, while any job already running at the moment of the write SHALL continue to use the snapshot loaded at its own `__init__` (non-retroactive, per Requirement 5.3).
4. WHEN the Engine performs first-run seeding of `Subject_Exclusion_File` (Requirement 2.1), THE Engine SHALL write the file as a JSON array with exactly one element in this order: `["is your verification code"]`.

#### Requirement 13: `imap_success.json` and `skip_domains.json` untouched

**User Story:** As an admin who already relies on the master
`imap_success.json` (from `centralize-imap-success-master`) and the
custom skip-domains list (from `custom-skip-domains`), I want this
feature to leave both files entirely alone, so my existing
configuration is safe.

#### Acceptance Criteria

1. THE Engine SHALL NOT perform any write to `imap_success.json` at the project root as part of handling `Subject_Exclusion_File`.
2. THE Engine SHALL NOT modify the schema, format, or key/value ordering of `imap_success.json` as a side effect of loading or saving `Subject_Exclusion_File`.
3. THE Engine SHALL NOT perform any write to `skip_domains.json` at the project root as part of handling `Subject_Exclusion_File`.
4. THE Engine SHALL NOT modify the schema, format, or element ordering of `skip_domains.json` as a side effect of loading or saving `Subject_Exclusion_File`.
5. THE Engine SHALL use a file lock path (`Subject_Exclusion_File + ".lock"`) that is DISTINCT from `MASTER_IMAP_SUCCESS_LOCK_PATH` and from `SKIP_DOMAINS_LOCK_PATH`, so operations on any one of the three files do not block operations on the others.
6. THE Engine SHALL NOT change the on-disk format or contract of any per-job file under `jobs/{job_id}/` (`live.txt`, `noemail.txt`, `die.txt`, `unreg.txt`, `domain_skipped.txt`, the per-job `imap_success.json`) — column layout, headers, separators, ordering, and trailing newlines remain as defined by the prior specs.

#### Requirement 14: Prior-spec test suites remain green (with minimal migration of `OTP_SUBJECT_REGEX` references)

**User Story:** As a developer maintaining the regression suite, I want
the test suites of the three earlier specs to keep passing after this
feature ships, with the smallest possible migration where they reach
into the legacy `OTP_SUBJECT_REGEX` symbol, so the upgrade does not
produce out-of-feature test breakage.

#### Acceptance Criteria

1. WHEN the test suite of `centralize-imap-success-master` runs after this feature is implemented, THE Engine SHALL cause every test in that suite to PASS without any modification to test code, fixtures, or runner configuration.
2. WHEN the test suite of `custom-skip-domains` runs after this feature is implemented, THE Engine SHALL cause every test in that suite to PASS without any modification to test code, fixtures, or runner configuration.
3. WHEN the test suite of `subject-keyword-match-shown-in-live` runs after this feature is implemented, THE Engine SHALL cause every test in that suite to PASS, allowing minimal migration of test code that imports or references the now-deleted `OTP_SUBJECT_REGEX` symbol; permitted migrations are limited to: (a) replacing `OTP_SUBJECT_REGEX.match(s.strip()) is None` with `not _subject_excluded(s, ["is your verification code"])`, (b) replacing `OTP_SUBJECT_REGEX.match(s.strip()) is not None` with `_subject_excluded(s, ["is your verification code"])`, (c) updating any synthetic-subject generator that previously asserted "matches `OTP_SUBJECT_REGEX`" to instead assert "is excluded by `_subject_excluded` against `Default_Subject_Exclusion_Patterns`", and (d) replacing `from imap_engine import OTP_SUBJECT_REGEX` with `from imap_engine import _subject_excluded` (or the equivalent module-level access). The semantic content of every assertion in that suite SHALL be preserved: tests that previously asserted "subject S is excluded from `live.txt`" SHALL continue to assert that, and tests that previously asserted "subject S is included in `live.txt`" SHALL continue to assert that, after the migration.
4. THE Engine SHALL NOT add any new dependency to `requirements.txt` — `filelock` was already added by `centralize-imap-success-master` and is reused by this feature without an upgrade across major versions.
5. WHEN any test in the three prior suites references the removed module-level `OTP_SUBJECT_REGEX`, THE Engine SHALL provide `_subject_excluded` and `_DEFAULT_SUBJECT_EXCLUSION_PATTERNS` (or the equivalent module-level seed constant chosen by the implementation phase) as importable symbols from `imap_engine`, so the migrations enumerated in 14.3 are mechanical and do not require restructuring the affected test files.
