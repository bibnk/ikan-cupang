# Design Document

## Overview

Saat ini `imap_engine.py` mendefinisikan satu compiled regex hardcoded
di module scope yang menjadi satu-satunya filter Subject:

```python
# imap_engine.py:27
OTP_SUBJECT_REGEX = re.compile(r'^Booking\.com – \w+ is your verification code$')
```

dan `_worker` mengevaluasi-nya di dalam fetch loop sebagai gate
post-fetch sebelum email dimasukkan ke `emails_data`
(`imap_engine.py:748`):

```python
# imap_engine.py:746-758
msg = email.message_from_bytes(part[1])
subject = decode_mime_words(msg.get("Subject", ""))
if not OTP_SUBJECT_REGEX.match(subject.strip()):
    from_ = decode_mime_words(msg.get("From", ""))
    _name, from_addr = email.utils.parseaddr(from_)
    if (
        (not self.target_senders)
        or sender_matches(from_addr, self.target_senders)
        or eid_bytes in subject_matched_ids
    ):
        ...
        emails_data.append({...})
```

Pattern ini hanya bisa diubah dengan editing source dan redeploy. Fitur
ini menggantikan-nya dengan list substring patterns yang dipersist ke
`subject_exclusion_list.json` di project root, di-load ulang per-job di
`ImapChecker.__init__`, dan diekspos ke admin via dua endpoint Flask
baru (`GET` dan `POST /api/admin/subject-exclusion-list`) plus satu
section di `templates/admin.html`.

Implementasi reuse pola filesystem yang sudah dibangun dua spec
sebelumnya:

- **`centralize-imap-success-master`** menyumbang konstanta
  `MASTER_LOCK_TIMEOUT_SECONDS = 30` (`imap_engine.py:33`) dan helper
  `_atomic_update_master` (`imap_engine.py:194`) yang men-define pola
  `tmp` + `flush` + `fsync` + `os.replace` di dalam `FileLock`.
- **`custom-skip-domains`** menyumbang implementasi konkret pola itu
  untuk payload `list[str]` lewat `_save_skip_domains`
  (`imap_engine.py:308`) dan `_load_skip_domains`
  (`imap_engine.py:326`), plus helper validasi/normalisasi
  `_normalize_skip_entries` (`imap_engine.py:251`) dan
  `_validate_skip_entries` (`imap_engine.py:266`). Fitur baru ini
  menyalin bentuk lima helper itu (dengan parameter berbeda untuk
  validation 9.3 / 9.4) — bukan extends, melainkan parallel — sehingga
  ketiga storage (master `imap_success.json`, `skip_domains.json`,
  `subject_exclusion_list.json`) sepenuhnya independent dengan FileLock
  paths terpisah (Requirement 13.5).

**Snapshot semantics** (Requirement 5.1, 5.3): setiap `ImapChecker`
memuat list ke `self.subject_exclusion_patterns` SEKALI di `__init__`
sebelum thread worker di-spawn di `run()` (`imap_engine.py:781`). Job
yang sedang berjalan memakai snapshot-nya sendiri; edit admin di
tengah eksekusi job lain tidak retroaktif.

**Semantics flip** (Requirement 6.3): legacy code keep email saat
`OTP_SUBJECT_REGEX.match(subject.strip())` returns `None` (regex tidak
match). Code baru drop email saat `_subject_excluded(subject,
patterns)` returns `True` (paling tidak satu pattern adalah substring
dari subject). Bentuk `if not _subject_excluded(...)` di call site
mempertahankan struktur control flow `_worker` byte-for-byte. Dengan
seed default `["is your verification code"]`, untuk setiap subject
`S` yang match regex lama, `"is your verification code" in S.lower()`
juga `True` — jadi default-seeded behavior identik dengan behavior
pre-feature (Requirement 12.1, 12.4).

Auth pakai pola persis sama dengan `/api/admin/skip-domains`
(`app.py:194`, `app.py:206`): dekorator `@login_required`
(`app.py:73`) plus pengecekan `session.get("is_admin")` di body
handler.

UI: satu section baru di `templates/admin.html` di bawah section
"Skip Domains" (`templates/admin.html:99`–~117) — `<textarea>` +
tombol Simpan + status `<div>`. JS inline mengikuti pola IIFE yang
sama dengan blok skip-domains existing (`templates/admin.html:181`
onwards).

Setelah feature ini ship, konstanta `OTP_SUBJECT_REGEX` di
`imap_engine.py:27` **dihapus seluruhnya** (Requirement 5.2). Helper
`_subject_excluded` adalah satu-satunya Subject-level filter, dan
`_DEFAULT_SUBJECT_EXCLUSION_PATTERNS` hanya hidup sebagai seed
first-run + fallback timeout di `__init__`.

Total perubahan: ~5 helper baru di `imap_engine.py` + 2 endpoint baru
di `app.py` + 1 section + 1 IIFE di `templates/admin.html` + 1 baris
init di `ImapChecker.__init__` + 1 baris ganti di `_worker` + hapus
satu baris konstanta lama, plus migrasi ~5 baris di
`tests/test_subject_keyword_match_shown_in_live_*.py`.

## Glossary

Term di "Direplikasi dari `requirements.md`" diambil verbatim dari
`requirements.md` agar desain dan requirement berbicara dalam
vocabulary yang sama. Term di "Design-time additions" hanya muncul di
desain — itu nama-nama simbol Python konkret.

### Direplikasi dari `requirements.md`

- **Subject_Exclusion_File**: File JSON tunggal di project root,
  `subject_exclusion_list.json`, di direktori yang sama dengan
  `imap_engine.py`, `app.py`, master `imap_success.json`, dan
  `skip_domains.json`. Format: array of strings (mis.
  `["is your verification code"]`).
- **Subject_Exclusion_Lock_Path**: Path file lock cross-process yang
  dipakai `filelock.FileLock` untuk koordinasi baca/tulis
  `Subject_Exclusion_File`. Bernilai `Subject_Exclusion_File + ".lock"`.
- **Default_Subject_Exclusion_Patterns**: Seed yang ditulis pada first
  run bila `Subject_Exclusion_File` belum ada — persis
  `["is your verification code"]`. Substring tunggal ini adalah
  trailing phrase invarian dari setiap subject yang dulu di-match
  `OTP_SUBJECT_REGEX`, sehingga seed ini mempertahankan semantics
  exclusion lama secara default.
- **Engine**: Class `ImapChecker` di `imap_engine.py`. Constructor-nya
  memuat list aktif ke instance attribute
  `subject_exclusion_patterns` (sebuah `list[str]` dengan elemen
  sudah lowercased), yang di-konsultasi `_worker` via
  `_subject_excluded` untuk memutuskan drop atau keep email.
- **Admin_UI**: Halaman `/admin` (`templates/admin.html`).
- **Admin_API**: Pasangan endpoint Flask
  `GET /api/admin/subject-exclusion-list` dan
  `POST /api/admin/subject-exclusion-list`.
- **Subject_Exclusion_Entry**: Satu pattern setelah normalisasi
  storage (strip → lower → drop-empty → dedupe-preserve-order),
  divalidasi terhadap empty / >500 chars / control char (Requirement 9).
- **`_subject_excluded`**: Helper module-level baru di `imap_engine.py`,
  signature `_subject_excluded(subject: str, patterns: list[str]) ->
  bool`. Returns `True` iff `any(p in subject.lower() for p in
  patterns)`, dengan asumsi `patterns` sudah lowercased oleh loader.
- **OTP_SUBJECT_REGEX (legacy)**: Compiled regex
  `^Booking\.com – \w+ is your verification code$` yang dulu di module
  level di `imap_engine.py:27`. Dihapus seluruhnya oleh fitur ini
  (Requirement 5.2).

### Design-time additions (nama simbol Python konkret)

- **`SUBJECT_EXCLUSION_PATH`**: Konstanta module-level di
  `imap_engine.py` yang merealisasikan `Subject_Exclusion_File`.
  Definisi:
  ```python
  SUBJECT_EXCLUSION_PATH = os.path.join(
      os.path.dirname(os.path.abspath(__file__)),
      "subject_exclusion_list.json",
  )
  ```
- **`SUBJECT_EXCLUSION_LOCK_PATH`**: Konstanta module-level
  `SUBJECT_EXCLUSION_PATH + ".lock"` yang merealisasikan
  `Subject_Exclusion_Lock_Path`.
- **`_DEFAULT_SUBJECT_EXCLUSION_PATTERNS`**: Konstanta module-level
  **privat** (prefix underscore) yang merealisasikan
  `Default_Subject_Exclusion_Patterns`. Tipe: `list[str]` (bukan
  `tuple` atau `frozenset`) supaya dapat di-pass langsung ke
  `_save_subject_exclusion_list` saat seed first-run dan ke
  `list(...)` constructor saat fallback timeout.
  ```python
  _DEFAULT_SUBJECT_EXCLUSION_PATTERNS = ["is your verification code"]
  ```
- **`_normalize_exclusion_entries(raw: list[str]) -> list[str]`**:
  Helper pure-function module-level yang menerapkan strip → lower →
  drop-empty → dedupe-preserving-order (Requirement 4.1, 4.2). Sejajar
  dengan `_normalize_skip_entries` (`imap_engine.py:251`) tapi tidak
  share implementation — keduanya tetap dua helper terpisah agar diff
  Requirement-level antara skip-domains dan subject-exclusion tidak
  pernah cross-leak.
- **`_validate_exclusion_entries(raw: list[str]) -> tuple[list[str],
  list[str]]`**: Helper pure-function module-level yang
  mengembalikan `(valid_normalized, rejected_raw)` per Requirement 9.1
  (short-circuit empty → >500 → control-char) dan 9.5
  (all-or-nothing). `rejected_raw` adalah subset `raw` yang melanggar
  salah satu cek, sebelum strip/lower, dengan urutan kemunculan dan
  duplikasi dipertahankan.
- **`_save_subject_exclusion_list(value: list[str]) -> None`**:
  Helper module-level baru. Menulis `value` (sudah dinormalisasi dan
  divalidasi oleh caller) ke `SUBJECT_EXCLUSION_PATH` via tmp + fsync +
  `os.replace` di dalam `FileLock(SUBJECT_EXCLUSION_LOCK_PATH,
  timeout=MASTER_LOCK_TIMEOUT_SECONDS)`.
- **`_load_subject_exclusion_list() -> list[str]`**: Helper module-level
  baru. Membaca `SUBJECT_EXCLUSION_PATH` di dalam FileLock, melakukan
  first-run seed bila perlu, dan mengembalikan list of strings (atau
  `[]` bila file korup — Requirement 2.3, 5.4).
- **`_subject_excluded(subject: str, patterns: list[str]) -> bool`**:
  Helper module-level baru. Body persis:
  ```python
  return any(p in subject.lower() for p in patterns)
  ```
  Tidak menyentuh disk, tidak ambil lock. Dipanggil per-email di
  `_worker` (Requirement 6.1).
- **`self.subject_exclusion_patterns`**: Atribut instance baru pada
  `ImapChecker`, tipe `list[str]`. Diisi sekali di `__init__` dan
  TIDAK pernah dimutasi setelahnya (snapshot per-job, Requirement 5.3).

## Architecture

Semua perubahan terlokalisasi di tiga file:

1. `imap_engine.py` — 3 konstanta + 5 helper baru + 1 perubahan di
   `__init__` + 1 perubahan di `_worker` + penghapusan
   `OTP_SUBJECT_REGEX`.
2. `app.py` — 2 endpoint baru (paralel dengan dua endpoint
   skip-domains existing).
3. `templates/admin.html` — 1 section baru + 1 IIFE inline JS baru.

Tidak ada file Python source baru. Satu file disk baru:
`subject_exclusion_list.json` (auto-create saat first read).

### `imap_engine.py` changes

#### Module-level constants (sekitar baris 27, BEFORE `MASTER_IMAP_SUCCESS_PATH` block)

```python
# Subject-exclusion file (project root) — user-editable list of
# substring patterns matched (case-insensitively) against decoded
# email Subject. Loaded per-job in ImapChecker.__init__. Replaces
# the legacy OTP_SUBJECT_REGEX.
SUBJECT_EXCLUSION_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "subject_exclusion_list.json",
)
SUBJECT_EXCLUSION_LOCK_PATH = SUBJECT_EXCLUSION_PATH + ".lock"

# First-run seed only. NOT referenced from _worker (Requirement 5.2).
# This single substring is the invariant trailing phrase of every
# subject the previous OTP_SUBJECT_REGEX
# (^Booking\.com – \w+ is your verification code$) matched, so seeding
# with it preserves the prior exclusion semantics by default
# (Requirement 2.4, 12.1).
_DEFAULT_SUBJECT_EXCLUSION_PATTERNS = ["is your verification code"]
```

`MASTER_LOCK_TIMEOUT_SECONDS` (`imap_engine.py:33`) di-reuse tanpa
duplikasi.

`OTP_SUBJECT_REGEX` di `imap_engine.py:27` **dihapus** (Requirement 5.2).

#### `_normalize_exclusion_entries(raw)` helper (pure function)

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

Catatan Requirement 4.3: `strip()` hanya membuang leading/trailing
whitespace; ASCII space `" "` (`U+0020`) di tengah dan whitespace
non-control lain (NBSP `U+00A0`, ideographic space `U+3000`, dst.) di
tengah pattern dipertahankan apa adanya. Contoh:
`"  is your verification code  "` → `"is your verification code"`
(tiga internal spaces utuh).

#### `_validate_exclusion_entries(raw)` helper (pure function)

Implementasi Requirement 9.1 (short-circuit dengan urutan tetap:
empty → >500 → control-char) dan 9.5 (all-or-nothing).

```python
# Control-char detector for Requirement 9.4. Must be defined alongside
# (not shared with) _WHITESPACE_RE which custom-skip-domains uses for
# its stricter "no whitespace" rule.
_EXCLUSION_CONTROL_CHARS = {"\r", "\n", "\t", "\v", "\f", "\x00"}


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


def _validate_exclusion_entries(raw):
    """Validate per Requirement 9.1 with short-circuit; all-or-nothing
    per Requirement 9.5.

    Returns (valid_normalized, rejected_raw). rejected_raw entries are
    reported as-sent (no strip/lower), preserving first-occurrence
    order, including duplicates (Requirement 9.5(a)-(c)).
    """
    rejected = []
    valid_pre_dedupe = []
    for entry in raw:
        stripped = entry.strip()
        # Check 1 (Requirement 9.2): empty after strip
        if stripped == "":
            rejected.append(entry)
            continue
        # Check 2 (Requirement 9.3): length > 500 after strip
        if len(stripped) > 500:
            rejected.append(entry)
            continue
        # Check 3 (Requirement 9.4): contains control character
        if _has_control_char(stripped):
            rejected.append(entry)
            continue
        valid_pre_dedupe.append(entry)
    if rejected:
        return [], rejected
    return _normalize_exclusion_entries(valid_pre_dedupe), []
```

Catatan tentang Requirement 9.7: validasi "elemen non-string"
**bukan** tanggung jawab `_validate_exclusion_entries`. Itu ditangani
lebih awal di POST handler dengan pengecekan
`all(isinstance(x, str) for x in patterns)` → respons HTTP 400
`{"error": "Invalid payload"}` (Requirement 8.4) sebelum helper ini
dipanggil. Helper ini meng-asumsikan input sudah `list[str]`.

Threshold 500 (vs 255 di `_validate_skip_entries`) dipilih karena
subject patterns adalah natural-language phrases yang sah-sah saja
lebih panjang dari domain keyword (Requirement 9.3).

#### `_save_subject_exclusion_list(value)` helper

```python
def _save_subject_exclusion_list(value):
    """Atomically write list[str] to SUBJECT_EXCLUSION_PATH under
    FileLock(SUBJECT_EXCLUSION_LOCK_PATH).

    Caller is responsible for normalization + validation. This helper
    only does the atomic tmp + fsync + os.replace under FileLock.
    Propagates filelock.Timeout and OSError to the caller.
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

Format on-disk byte-identik dengan `_save_skip_domains`
(`imap_engine.py:308`) dan `_atomic_update_master`
(`imap_engine.py:194`) untuk payload yang sama (Requirement 3.5):
`json.dump(value, f, indent=2)`, UTF-8, `flush` + `fsync` +
`os.replace`.

#### `_load_subject_exclusion_list()` helper

```python
def _load_subject_exclusion_list():
    """Load subject-exclusion list, seeding _DEFAULT_SUBJECT_EXCLUSION_PATTERNS
    on first run.

    Acquires FileLock(SUBJECT_EXCLUSION_LOCK_PATH,
    timeout=MASTER_LOCK_TIMEOUT_SECONDS).

    - Missing file → write _DEFAULT_SUBJECT_EXCLUSION_PATTERNS via the
      same atomic tmp + fsync + os.replace sequence as
      _save_subject_exclusion_list and return
      list(_DEFAULT_SUBJECT_EXCLUSION_PATTERNS) (Requirement 2.1, 12.4).
    - Corrupt file (JSON error or not list[str]) → return [] WITHOUT
      rewriting (Requirement 2.3, 5.4).
    - filelock.Timeout propagates to the caller (Requirement 3.4).
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

Bentuk paralel dengan `_load_skip_domains` (`imap_engine.py:326`).

#### `_subject_excluded(subject, patterns)` helper

```python
def _subject_excluded(subject, patterns):
    """Return True iff any pattern is a substring of subject.lower().

    Assumes patterns has already been lowercased by the loader
    (Requirement 4.4) — does NOT lowercase patterns here. Lowercases
    subject exactly once, before the `in` check (Requirement 6.1, 6.5).

    Special case (Requirement 6.2): when patterns == [], any() over
    an empty iterable is False, so no email is excluded.
    """
    s = subject.lower()
    return any(p in s for p in patterns)
```

Helper ini adalah satu-satunya predicate Subject-level yang dipakai
runtime. Tidak dependen `OTP_SUBJECT_REGEX` (yang akan dihapus).

#### `ImapChecker.__init__` change

Sebelum (`imap_engine.py:393`–`405`, akhir `__init__` tepat setelah
`self.skip_domain_keywords = ...` block):

```python
        # Skip-domains snapshot for this job (Requirement 5.1, 5.3).
        try:
            self.skip_domain_keywords = _load_skip_domains()
        except Timeout:
            print(
                "[imap_engine] skip-domains lock timeout, falling back to default in-memory",
                file=sys.stderr,
            )
            self.skip_domain_keywords = list(_DEFAULT_SKIP_SET)
```

Sesudah — tambah satu blok `try/except Timeout` parallel **persis di
bawah** blok skip-domains, sebelum `# Progress tracking`:

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

Karena `run()` (`imap_engine.py:781`) baru men-spawn thread worker
SETELAH `__init__` selesai, snapshot dijamin ada sebelum eksekusi
paralel mulai (Requirement 5.1).

#### `_worker` change

Satu boolean expression berubah (`imap_engine.py:748`):

Sebelum:

```python
                                msg = email.message_from_bytes(part[1])
                                subject = decode_mime_words(msg.get("Subject", ""))
                                if not OTP_SUBJECT_REGEX.match(subject.strip()):
                                    from_ = decode_mime_words(msg.get("From", ""))
```

Sesudah:

```python
                                msg = email.message_from_bytes(part[1])
                                subject = decode_mime_words(msg.get("Subject", ""))
                                if not _subject_excluded(subject, self.subject_exclusion_patterns):
                                    from_ = decode_mime_words(msg.get("From", ""))
```

Tidak ada perubahan lain di `_worker`. Body if (`from_`/`from_addr`
parse, three-arm OR sender/keyword union check, `emails_data.append`)
**byte-identical** dengan pre-feature (Requirement 6.3).

Catatan Requirement 6.4: legacy code memanggil `subject.strip()` karena
regex anchored `^...$`. `_subject_excluded` melakukan `subject.lower()`
internal dan substring matching tidak sensitive ke leading/trailing
whitespace, jadi argumen yang diteruskan adalah `subject` apa adanya
(BUKAN `subject.strip()`). Ini intentional — `.strip()` di call site
akan jadi dead code.

Semantics flip (Requirement 6.3):
- Legacy: keep iff regex tidak match → `if not OTP_SUBJECT_REGEX.match(...)`
- Baru: keep iff tidak ada pattern yang match → `if not _subject_excluded(...)`

Bentuk `if not _subject_excluded(...)` mempertahankan polaritas dan
struktur control flow kode di sekitarnya byte-for-byte; hanya
ekspresi yang berubah.

#### Removal of `OTP_SUBJECT_REGEX`

Baris `OTP_SUBJECT_REGEX = re.compile(r'^Booking\.com – \w+ is your
verification code$')` di `imap_engine.py:27` **dihapus seluruhnya**
(Requirement 5.2). `_DEFAULT_SUBJECT_EXCLUSION_PATTERNS`
**MENGGANTIKAN-nya** hanya untuk seed first-run dan fallback timeout
di `__init__`. `_worker` tidak boleh mereferensi
`_DEFAULT_SUBJECT_EXCLUSION_PATTERNS` (Requirement 5.2 — analog dengan
constraint pada `_DEFAULT_SKIP_SET` di `custom-skip-domains`). Dijamin
oleh grep-test di test suite (lihat Testing Strategy).

`import re` di `imap_engine.py:9` tetap dipakai oleh `EMAIL_REGEX`
(`imap_engine.py:26`), jadi tidak perlu dihapus.

### `app.py` changes

Dua endpoint baru, ditambahkan **persis di bawah** dua endpoint
skip-domains existing (`app.py:194`–`236`), sebelum
`@app.route("/api/check", ...)` (`app.py:239`). Auth pattern persis
sama dengan `get_skip_domains` / `post_skip_domains`.

Update import di `app.py:14`–`22`:

```python
from imap_engine import (
    ImapChecker,
    parse_accounts,
    parse_senders,
    _load_skip_domains,
    _save_skip_domains,
    _validate_skip_entries,
    # New imports for subject-exclusion-list:
    _load_subject_exclusion_list,
    _save_subject_exclusion_list,
    _validate_exclusion_entries,
)
```

Endpoint definitions:

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

Dekorator `login_required` (`app.py:73`) sudah menangani 401 untuk
request `/api/...` yang belum ter-autentikasi (mengembalikan
`jsonify({"error": "Unauthorized"}), 401`), jadi Requirement 7.3 dan
8.5 dipenuhi tanpa kode tambahan. Pengecekan
`session.get("is_admin")` di body handler menangani 403 (Requirement
7.4, 8.6).

Tidak ada endpoint lain di `app.py` yang disentuh — Requirement 11.1
dijamin secara struktural (diff hanya menambah dua route baru).

### `templates/admin.html` changes

#### Section baru

Disisipkan **persis setelah** penutup `</div>` dari section "Skip
Domains" (`templates/admin.html:117`) dan SEBELUM `</main>`
(`templates/admin.html:118`):

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

Styling (gradient orange, padding, font Fira Code) di-mirror dari
section Skip Domains untuk konsistensi visual (Requirement 10.1).

#### IIFE inline JS

Ditambahkan di blok `<script>` yang sudah ada di
`templates/admin.html:181`–`230`, **persis setelah** IIFE
`// Skip Domains: load on page mount + save on click ...` (akhir
sekitar `templates/admin.html:230`), mengikuti pola persis sama:

```javascript
// Subject Exclusion List: load on page mount + save on click (Requirement 10.1-10.5)
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

Section ini hanya ada di `admin.html`, dan `/admin` di `app.py:148`
sudah melakukan redirect non-admin ke `/`, sehingga Requirement 10.4
dipenuhi tanpa Jinja `{% if is_admin %}` tambahan di sekitar section
(seluruh template `admin.html` adalah admin-only).

JS pakai `exclTextarea.value = ...` (text content, bukan
`innerHTML`), `JSON.stringify({patterns: lines})` (preserves blank
lines untuk server-side `strip()` + empty-drop apply, Requirement
10.3), dan `split("\n")` mentah tanpa pre-trim.

## Components and Interfaces

```
┌──────────────────────────────────────────────────────────────────┐
│                      Browser (Admin_UI)                          │
│   templates/admin.html  +  inline JS                             │
│   • on mount: GET /api/admin/subject-exclusion-list → fill box   │
│   • on click Simpan: POST /api/admin/subject-exclusion-list      │
└──────────────────────┬───────────────────────────────────────────┘
                       │ JSON over HTTP, session cookie
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│                      app.py (Admin_API)                          │
│   GET  /api/admin/subject-exclusion-list  @login_required+admin  │
│   POST /api/admin/subject-exclusion-list  @login_required+admin  │
│   • POST: structural validate → _validate_exclusion_entries      │
│           → _save_subject_exclusion_list                         │
└──────────────────────┬───────────────────────────────────────────┘
                       │ Python function call
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│                   imap_engine.py (helpers)                       │
│   _load_subject_exclusion_list() — FileLock + read + first-run   │
│   _save_subject_exclusion_list(value) — FileLock+tmp+fsync+rename│
│   _normalize_exclusion_entries() — pure: strip/lower/dedupe      │
│   _validate_exclusion_entries()  — pure: 9.2/9.3/9.4 short-circuit│
│   _subject_excluded(subject, patterns) — pure: substring lookup  │
└──────────────────────┬───────────────────────────────────────────┘
                       │ filelock + filesystem
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│                  subject_exclusion_list.json                     │
│   project root, JSON array of strings, indent=2, UTF-8           │
│   coordinated by subject_exclusion_list.json.lock (FileLock)     │
└──────────────────────────────────────────────────────────────────┘

       ▲
       │ same FileLock-coordinated read at job start
       │
┌──────┴────────────────────────────────────────────────────────────┐
│              ImapChecker (Engine, per-job instance)               │
│   __init__:                                                       │
│     self.subject_exclusion_patterns = _load_subject_exclusion_list│
│     (with Timeout fallback to _DEFAULT_SUBJECT_EXCLUSION_PATTERNS)│
│              ── BEFORE workers spawn in run() ──                  │
│   _worker (per email after fetch):                                │
│     if not _subject_excluded(subject, self.subject_exclusion_patterns):│
│         emails_data.append({...})                                 │
└───────────────────────────────────────────────────────────────────┘
```

### Public surface

| Interface | Defined in | Consumers | Stability |
|-----------|------------|-----------|-----------|
| `GET /api/admin/subject-exclusion-list` | `app.py` | Admin_UI, ad-hoc curl | New (this spec) |
| `POST /api/admin/subject-exclusion-list` | `app.py` | Admin_UI | New (this spec) |
| `_load_subject_exclusion_list()` | `imap_engine.py` | `app.py`, `ImapChecker.__init__` | Module-internal (underscore prefix), but imported by `app.py` |
| `_save_subject_exclusion_list(value)` | `imap_engine.py` | `app.py` POST handler | Module-internal, imported by `app.py` |
| `_normalize_exclusion_entries(raw)` | `imap_engine.py` | `_validate_exclusion_entries`, tests | Module-internal |
| `_validate_exclusion_entries(raw)` | `imap_engine.py` | `app.py` POST handler, tests | Module-internal |
| `_subject_excluded(subject, patterns)` | `imap_engine.py` | `_worker`, migrated tests in `tests/test_subject_keyword_match_shown_in_live_*.py` | Module-internal, but imported by tests (Requirement 14.5) |
| `_DEFAULT_SUBJECT_EXCLUSION_PATTERNS` | `imap_engine.py` | `_load_subject_exclusion_list` (seed), `__init__` (Timeout fallback), tests | Module-internal, but importable per Requirement 14.5 |
| `ImapChecker.subject_exclusion_patterns` | `imap_engine.py` | `_worker` (read-only after `__init__`) | Internal attribute |
| `OTP_SUBJECT_REGEX` | `imap_engine.py` (DELETED) | n/a | **Removed** by this spec (Requirement 5.2) |

## Data Models

### On-disk: `subject_exclusion_list.json`

Format: JSON array of strings, UTF-8, `indent=2`, trailing newline
opsional (apa pun yang `json.dump(..., indent=2)` hasilkan). Contoh
isi setelah first-run seed (Requirement 12.4):

```json
[
  "is your verification code"
]
```

Setiap elemen adalah `Subject_Exclusion_Entry` ternormalisasi:
stripped, lowercased, non-empty, length 1–500, tidak mengandung
control char (per definisi Requirement 9.4). ASCII space `U+0020` di
tengah entry diperbolehkan dan dipertahankan.

Invariants on-disk:

- `isinstance(json.load(f), list)` selalu True untuk file yang valid.
- `all(isinstance(x, str) for x in json.load(f))` selalu True.
- Bila file ditulis oleh fitur ini (bukan diedit manual), maka
  `json.load(f) == _normalize_exclusion_entries(json.load(f))` —
  yaitu idempoten terhadap normalisasi.
- File yang ditulis manual dan melanggar invariant di atas akan
  dianggap korup oleh `_load_subject_exclusion_list` dan menyebabkan
  `self.subject_exclusion_patterns = []` untuk job tersebut
  (Requirement 2.3, 5.4).

### In-memory (per `ImapChecker` instance)

Atribut baru: `self.subject_exclusion_patterns: list[str]`.

- Diisi sekali di `__init__` (Requirement 5.1).
- Tidak ada lock instance — read-only setelah `__init__` selesai.
  Worker thread membaca-nya secara concurrent tanpa lock (Python GIL +
  immutability cukup karena tidak ada writer setelah konstruktor).
- Iterasi `any(p in subject.lower() for p in patterns)` di hot path
  `_worker` aman karena list tidak pernah dimutasi.
- Nilai snapshot ditentukan saat `__init__` dipanggil; tidak mengikuti
  edit admin yang terjadi setelah-nya (Requirement 5.3, 12.3).

### HTTP request/response shapes

**`GET /api/admin/subject-exclusion-list`**

| Status | Body |
|--------|------|
| 200 | `{"patterns": ["is your verification code", ...]}` |
| 401 | `{"error": "Unauthorized"}` (via `login_required`) |
| 403 | `{"error": "Admin only"}` |
| 503 | `{"error": "Subject-exclusion list is busy, please retry"}` (FileLock timeout) |

**`POST /api/admin/subject-exclusion-list`**

Request body: `{"patterns": ["IS YOUR VERIFICATION CODE", "  Newsletter ", "promo"]}`
(mentah, sebelum strip/lower).

| Status | Body | Trigger |
|--------|------|---------|
| 200 | `{"success": true, "patterns": ["is your verification code", "newsletter", "promo"]}` | Semua valid; `patterns` adalah hasil normalisasi yang tersimpan |
| 400 | `{"error": "Invalid payload"}` | Body bukan JSON / `patterns` bukan list / ada elemen non-string |
| 400 | `{"error": "Invalid entries", "rejected": ["", "x"*600, "bad\nentry"]}` | Ada entry yang melanggar 9.2/9.3/9.4 |
| 401 | `{"error": "Unauthorized"}` | Belum login |
| 403 | `{"error": "Admin only"}` | Login tapi bukan admin |
| 503 | `{"error": "Subject-exclusion list is busy, please retry"}` | FileLock timeout |


## Sequence Diagrams

### Diagram 1: Admin saves a new list (POST happy path)

```
Browser            Flask (app.py)        imap_engine.py        Filesystem
   │                    │                       │                   │
   │ POST /api/admin/   │                       │                   │
   │ subject-exclusion- │                       │                   │
   │ list               │                       │                   │
   │ {"patterns":[...]} │                       │                   │
   │───────────────────>│                       │                   │
   │                    │ login_required → ok   │                   │
   │                    │ session.is_admin == True                  │
   │                    │                       │                   │
   │                    │ structural check:     │                   │
   │                    │ dict → list → all str │                   │
   │                    │                       │                   │
   │                    │ _validate_exclusion_entries(raw)          │
   │                    │──────────────────────>│                   │
   │                    │   short-circuit:      │                   │
   │                    │   empty → >500 → ctrl │                   │
   │                    │  (valid_norm, [])     │                   │
   │                    │<──────────────────────│                   │
   │                    │                       │                   │
   │                    │ _save_subject_exclusion_list(valid_norm)  │
   │                    │──────────────────────>│                   │
   │                    │                       │ FileLock acquire  │
   │                    │                       │──────────────────>│
   │                    │                       │ open tmp + json.dump
   │                    │                       │ flush + fsync     │
   │                    │                       │ os.replace        │
   │                    │                       │ FileLock release  │
   │                    │                       │<──────────────────│
   │                    │<──────────────────────│                   │
   │                    │                       │                   │
   │ 200 {"success":    │                       │                   │
   │ true, "patterns":  │                       │                   │
   │ [...]}             │                       │                   │
   │<───────────────────│                       │                   │
```

### Diagram 2: Job starts during admin save (FileLock serialization, snapshot semantics)

Tujuan: menunjukkan bahwa `_save_subject_exclusion_list` (Admin_API) dan
`_load_subject_exclusion_list` (`ImapChecker.__init__`) tidak pernah
race — keduanya acquire lock yang sama. Job yang start ditengah save
akan menunggu hingga save selesai, lalu memuat snapshot baru. Job yang
sudah berjalan (worker thread sudah spawn) tidak terpengaruh edit yang
terjadi mid-flight (Requirement 5.3, 12.3).

```
Time   Admin_API              ImapChecker(job_A)        ImapChecker(job_B)        subject_exclusion_list.json.lock
 │      (POST in flight)       (workers running with     (NEW init starting)
 │                              snapshot V1 = ["is your
 │                              verification code"])
 │
 │  POST .../subject-                                                              [unlocked]
 │  exclusion-list
 │      │
 │      │ FileLock.acquire ───────────────────────────────────────────────────→  [LOCKED by API]
 │      │
 │      │ tmp write + fsync                       __init__ starts
 │      │                                              │
 │      │                                              │ _load_subject_exclusion_list()
 │      │                                              │ FileLock.acquire ────→  [BLOCKED, waiting]
 │      │ os.replace V1→V2                             │ (waiting...)
 │      │  (V2 = ["newsletter",                        │
 │      │        "is your verif..."])                  │
 │      │                                              │
 │      │ FileLock.release ──────────────────────────────────────────────────→  [unlocked]
 │      │                                              │
 │  200 OK                                             │ FileLock.acquire ────→  [LOCKED by job_B]
 │                                                    │
 │      (job_A still running                          │ read V2 → return
 │       with snapshot V1 —                           │   ["newsletter",
 │       Requirement 5.3,                             │    "is your verification code"]
 │       non-retroactive)                             │
 │                                                    │ FileLock.release ────→  [unlocked]
 │                                                    │
 │                                                    │ self.subject_exclusion_patterns = V2
 │                                                    │
 │                                                    │ run() spawns workers (use V2)
```

Catatan: `FileLock` di sini adalah `filelock.FileLock` (cross-process,
OS-level). Jika `__init__` dipanggil di proses worker WSGI yang
berbeda dari Admin_API, serialization tetap berlaku karena lock
berdasarkan file handle OS, bukan in-process state. Job_A memakai V1
sampai selesai meski V2 sudah persisted.

### Diagram 3: First-run seed (cold start)

```
Time   ImapChecker.__init__              imap_engine.py         Filesystem
 │      (first job after deploy,
 │       OR first GET admin call —
 │       both are read paths that
 │       hit _load_subject_exclusion_list)
 │
 │  _load_subject_exclusion_list()
 │      │
 │      │ FileLock.acquire ───────────────────────────────────────────→  [LOCKED]
 │      │
 │      │ os.path.exists(SUBJECT_EXCLUSION_PATH) → False
 │      │
 │      │ Inline atomic seed:
 │      │   open("subject_exclusion_list.json.tmp", "w") ─────────────→  [tmp created]
 │      │   json.dump(list(_DEFAULT_SUBJECT_EXCLUSION_PATTERNS),
 │      │             f, indent=2)                          ─────────→  [tmp filled]
 │      │   f.flush(); os.fsync(f.fileno())                 ─────────→  [fsynced]
 │      │   close (with __exit__)                           ─────────→  [tmp closed]
 │      │   os.replace(tmp, SUBJECT_EXCLUSION_PATH)         ─────────→  [subject_exclusion_list.json
 │      │                                                                exists with seed]
 │      │
 │      │ FileLock.release ───────────────────────────────────────────→  [unlocked]
 │      │
 │      │ return list(_DEFAULT_SUBJECT_EXCLUSION_PATTERNS)
 │      ▼
 │  self.subject_exclusion_patterns = ["is your verification code"]
 │
 │  ... run() spawns workers ...
 │
 │  _worker per email after fetch:
 │    if not _subject_excluded(subject, ["is your verification code"]):
 │       emails_data.append({...})
 │
 │  Untuk subject = "Booking.com – ABC123 is your verification code":
 │    "is your verification code" in subject.lower() → True
 │    _subject_excluded → True
 │    `if not True` → False → email DROPPED.
 │
 │  → byte-identik dengan perilaku pre-feature OTP_SUBJECT_REGEX (Requirement 12.1, 12.2).
```

## Failure Handling

| Failure | Detected by | Response | State left behind |
|---|---|---|---|
| `subject_exclusion_list.json` missing | `os.path.exists` False inside `_load_subject_exclusion_list` | First-run seed: write `_DEFAULT_SUBJECT_EXCLUSION_PATTERNS` via tmp + fsync + `os.replace`, return `list(...)` (Requirement 2.1, 12.4). | File present with default content `["is your verification code"]`. |
| `subject_exclusion_list.json` corrupt (invalid JSON, or list with non-string elements, or non-list root) | `json.load` raises, OR `isinstance(data, list)` False, OR `all(isinstance(x, str) for x in data)` False | Return `[]` from `_load_subject_exclusion_list`. `ImapChecker.__init__` sets `self.subject_exclusion_patterns = []`. `_worker` `_subject_excluded(subject, [])` evaluates `False` for every email (`any` over `[]` is `False`, Requirement 6.2). Admin_API GET returns `{"patterns": []}`. | File unchanged (NOT auto-rewritten — Requirement 2.3, 5.4). Admin can fix via POST. |
| `FileLock` timeout in `_load_subject_exclusion_list` (called from `ImapChecker.__init__`) | `filelock.Timeout` raised | `__init__` catches, logs to stderr, sets `self.subject_exclusion_patterns = list(_DEFAULT_SUBJECT_EXCLUSION_PATTERNS)`. **No rewrite to disk** (Requirement 3.4(b)). Constructor returns normally; worker threads start with safe default. | File unchanged. |
| `FileLock` timeout in `_load_subject_exclusion_list` (called from `GET /api/admin/subject-exclusion-list`) | `filelock.Timeout` raised | Handler catches, returns `503 {"error": "Subject-exclusion list is busy, please retry"}` (Requirement 3.4(a), 7.5). | File unchanged. |
| `FileLock` timeout in `_save_subject_exclusion_list` (called from `POST`) | `filelock.Timeout` raised | Handler catches, returns `503 {"error": "Subject-exclusion list is busy, please retry"}` (Requirement 3.4(a), 8.7). | File unchanged. |
| Per-entry validation failure (empty after strip / >500 chars / contains control char) | `_validate_exclusion_entries` returns non-empty `rejected` list | `400 {"error": "Invalid entries", "rejected": [...]}`. **Short-circuit**: only the first violated rule per entry is evaluated, dan response **tidak** mengekspos rule mana yang gagal. **All-or-nothing**: file is NOT written even if some entries are valid (Requirement 9.5, 9.6). | File unchanged. |
| Structural payload error (non-JSON body, missing `patterns` key, `patterns` not a list, list with non-string element) | `request.get_json(silent=True)` returns `None`, OR type checks fail | `400 {"error": "Invalid payload"}` (Requirement 8.4, 9.7). | File unchanged. |
| Disk full / permission denied during `_save_subject_exclusion_list` write or `os.replace` | `OSError` raised in `_save_subject_exclusion_list` | `OSError` propagates from helper. POST handler does NOT catch this; Flask returns `500` (default error handler). The tmp file may remain on disk; subsequent `_load_subject_exclusion_list` ignores it because reads only consult `SUBJECT_EXCLUSION_PATH` (Requirement 3.3). | `SUBJECT_EXCLUSION_PATH` retains old valid content if `os.replace` did not run. Stale `.tmp` may exist — harmless. |
| Crash mid-write (process killed between tmp open and `os.replace`) | (External — observable on next start) | Next `_load_subject_exclusion_list` reads `SUBJECT_EXCLUSION_PATH` and returns the OLD valid content. Dangling `.tmp` is ignored (`_load` only reads `SUBJECT_EXCLUSION_PATH`). On next successful `_save`, the tmp file is overwritten in place. | Old content preserved (Requirement 3.3). |
| Non-admin authenticated user calls API | `session.get("is_admin")` False | `403 {"error": "Admin only"}` for both GET and POST (Requirement 7.4, 8.6). | No state change. |
| Unauthenticated user calls API | `session.get("authenticated")` False (in `login_required`) | `401 {"error": "Unauthorized"}` (existing decorator behavior for `/api/...` paths, Requirement 7.3, 8.5). | No state change. |
| Leftover `.lock` file from prior run | (None — `filelock` releases via OS file handle, not file existence, Requirement 3.2) | Engine SHALL NOT delete or treat any leftover `.lock` file as stale during startup. Next `FileLock(...)` acquire works normally on the existing file. | `.lock` file persists harmlessly. |


## Correctness Properties

*A property is a characteristic or behavior that should hold true
across all valid executions of a system — essentially, a formal
statement about what the system should do. Properties serve as the
bridge between human-readable specifications and machine-verifiable
correctness guarantees.*

The 8 properties below are the result of prework + reflection
consolidating the testable acceptance criteria from `requirements.md`.
Acceptance criteria classified as EXAMPLE, EDGE_CASE, INTEGRATION, or
SMOKE in the prework are handled by non-property tests (see Testing
Strategy).

### Property 1: Normalization correctness and idempotency

*For any* `raw: list[str]`, `_normalize_exclusion_entries(raw)` SHALL
satisfy all of:

1. Every output element `e` equals `s.strip().lower()` for some `s` in
   `raw`,
2. No output element is the empty string,
3. No output element is duplicated,
4. The relative order of output elements equals the order of first
   occurrence in `raw` of inputs whose `s.strip().lower()` is non-empty,
5. Every output element `e` satisfies `e == e.lower()` (lowercased
   guarantee — Requirement 4.4),
6. Internal ASCII spaces inside an entry are preserved verbatim:
   for any `s` in `raw` whose stripped form `s.strip()` is non-empty
   and contains no leading/trailing whitespace inside the stripped
   form, `_normalize_exclusion_entries([s])[0]` contains the same
   internal characters as `s.strip().lower()` (no collapsing,
   replacement, or removal of internal `" "`),
7. `_normalize_exclusion_entries(_normalize_exclusion_entries(raw)) ==
   _normalize_exclusion_entries(raw)` (idempotency).

**Validates: Requirements 4.1, 4.2, 4.3, 4.4**

### Property 2: On-disk round trip and byte-format equivalence

*For any* `value: list[str]` where each element is already normalized
(non-empty after `strip()`, lowercased, length ≤ 500, no control
character), the following SHALL hold after
`_save_subject_exclusion_list(value)`:

1. `_load_subject_exclusion_list() == value` (round-trip),
2. The raw bytes of `subject_exclusion_list.json` equal
   `json.dumps(value, indent=2).encode("utf-8")` (byte-identical with
   the format produced by `_save_skip_domains` and
   `_atomic_update_master` for an equivalent `list[str]` payload —
   Requirement 1.2, 3.5),
3. A subsequent `GET /api/admin/subject-exclusion-list` (as admin)
   returns `200 {"patterns": value}` (Requirement 7.2),
4. A subsequent call to `_load_subject_exclusion_list()` does NOT
   modify `subject_exclusion_list.json` — pre-call bytes equal
   post-call bytes AND mtime is unchanged (Requirement 2.2).

**Validates: Requirements 1.2, 2.2, 3.5, 7.2**

### Property 3: POST normalize-and-persist with full replace

*For any* pre-existing on-disk content `O: list[str]` (valid and
normalized) and *for any* raw payload `R: list[str]` such that
`_validate_exclusion_entries(R)` returns `(_, [])` (no rejections),
after `POST /api/admin/subject-exclusion-list` with body
`{"patterns": R}`:

1. The HTTP response is `200` with body
   `{"success": true, "patterns": _normalize_exclusion_entries(R)}`,
2. `_load_subject_exclusion_list() == _normalize_exclusion_entries(R)`
   (the file content equals the normalized payload),
3. Any element of `O` that does not appear in
   `_normalize_exclusion_entries(R)` is absent from the post-write
   file (PUT-like full-replace semantics — Requirement 8.3).

**Validates: Requirements 8.2, 8.3**

### Property 4: Validation rejection semantics

*For any* raw payload `R: list[str]` where at least one element `e`
satisfies `e.strip() == ""` OR `len(e.strip()) > 500` OR
`_has_control_char(e.strip()) is True`, the following SHALL hold:

1. `_validate_exclusion_entries(R)` returns `([], rejected)` where
   `rejected` is the sublist of `R` containing exactly those elements
   that violate any of the three rules, in first-occurrence order,
   with duplicates preserved (entries appear in `rejected` as-sent,
   before any `strip`/`lower`),
2. `POST /api/admin/subject-exclusion-list` with body `{"patterns": R}`
   (as admin) returns `400` with body `{"error": "Invalid entries",
   "rejected": rejected}` where `rejected` matches the list from (1)
   AND the response body does NOT contain any field naming which rule
   failed (Requirement 9.5(d)),
3. `_load_subject_exclusion_list()` after the failed POST returns the
   same value as `_load_subject_exclusion_list()` before the POST
   (all-or-nothing: file unchanged — Requirement 9.6),
4. Internal ASCII space `U+0020` inside an entry is NOT a control
   character: an entry of the form `"is your verification code"` (or
   any non-empty string ≤ 500 chars composed of letters, digits, and
   ASCII spaces) is NEVER in `rejected` (Requirement 9.4 explicit
   carve-out).

**Validates: Requirements 9.1, 9.2, 9.3, 9.4, 9.5, 9.6**

### Property 5: Subject-exclusion predicate equivalence

*For any* `subject: str` and *for any* `patterns: list[str]` where
every element is non-empty AND equals its own `.lower()` (i.e., the
loader-normalized state), the predicate
`_subject_excluded(subject, patterns)` SHALL equal
`any(p in subject.lower() for p in patterns)`.

In particular:

1. `_subject_excluded(subject, []) == False` for every `subject`
   (Requirement 6.2 — `any` over an empty iterable is `False`),
2. The predicate is case-insensitive on both sides — for any pattern
   stored as `"is your verification code"` (lowercased by the loader)
   and any subject `"Booking.com – ABC123 IS YOUR VERIFICATION CODE"`,
   `_subject_excluded` returns `True` (Requirement 6.5).

**Validates: Requirements 6.1, 6.2, 6.5**

### Property 6: Default seed preserves OTP exclusion semantics

*For any* `t: str` matching the regex `\w+` (i.e., one or more
alphanumeric or underscore characters), let `S = "Booking.com \u2013 "
+ t + " is your verification code"` (en-dash U+2013, exactly the
shape that the legacy `OTP_SUBJECT_REGEX = re.compile(r'^Booking\.com
– \w+ is your verification code$')` matches). Then:

1. `_subject_excluded(S, ["is your verification code"]) == True`,
2. `_subject_excluded(S, list(_DEFAULT_SUBJECT_EXCLUSION_PATTERNS)) ==
   True` (because `_DEFAULT_SUBJECT_EXCLUSION_PATTERNS == ["is your
   verification code"]`),
3. After `_load_subject_exclusion_list()` is called with no prior
   `subject_exclusion_list.json` on disk, the returned list is
   `["is your verification code"]`, and item (1) holds for every
   subject `S` of the above shape (the seeded default behavior is
   byte-identical with pre-feature `OTP_SUBJECT_REGEX` exclusion for
   such subjects — Requirement 12.1, 12.4).

**Validates: Requirements 2.4, 12.1, 12.4**

### Property 7: `_worker` drops iff `_subject_excluded` returns True

*For any* `subject: str`, *for any* normalized `patterns: list[str]`,
and *for any* surrounding `(target_senders, keywords, from_addr,
eid_bytes, subject_matched_ids)` for which the existing three-arm OR
gate `(not target_senders) or sender_matches(from_addr, target_senders)
or eid_bytes in subject_matched_ids` evaluates to `True` (the gate
introduced by spec `subject-keyword-match-shown-in-live`), running
`ImapChecker._worker` against a single fetched email with that
`subject` SHALL include the email in `emails_data` if and only if
`_subject_excluded(subject, patterns) == False`.

Equivalently:

1. When `_subject_excluded(subject, patterns) is True`, the email is
   NOT appended to `emails_data` (drop),
2. When `_subject_excluded(subject, patterns) is False` and the
   three-arm gate holds, the email IS appended to `emails_data`
   (keep),
3. The three-arm gate from `subject-keyword-match-shown-in-live` is
   evaluated AFTER the new exclusion gate (Requirement 6.3 — the
   exclusion is the outermost Subject-level gate, replacing the
   legacy OTP gate at the same position).

**Validates: Requirements 6.3**

### Property 8: Neighbor storage files untouched

*For any* `value: list[str]` valid for `_save_subject_exclusion_list`,
calling `_save_subject_exclusion_list(value)` SHALL leave both
`imap_success.json` (master, owned by `centralize-imap-success-master`)
and `skip_domains.json` (owned by `custom-skip-domains`) byte-identical
and with unchanged modification time. Symmetrically,
`_load_subject_exclusion_list()` SHALL NOT modify either file. The
lock paths `MASTER_IMAP_SUCCESS_LOCK_PATH`, `SKIP_DOMAINS_LOCK_PATH`,
and `SUBJECT_EXCLUSION_LOCK_PATH` SHALL be pairwise distinct
(Requirement 13.5), so operations on the new file never block
operations on the other two.

**Validates: Requirements 13.1, 13.2, 13.3, 13.4, 13.5**

## Error Handling

(See the Failure Handling table above — kept under that name to
parallel the structure of `centralize-imap-success-master/design.md`
and `custom-skip-domains/design.md`.)

## Migration / Backward Compatibility

**No data migration is required for production state.**

Path:
- On deploy, `subject_exclusion_list.json` does not exist.
- The first call to `_load_subject_exclusion_list` (whether from
  `ImapChecker.__init__` for the first job, or from
  `GET /api/admin/subject-exclusion-list` if admin opens the panel
  first) seeds the file with `_DEFAULT_SUBJECT_EXCLUSION_PATTERNS`
  (Requirement 2.1, 12.4).
- Until that first call, no file exists — also no harm, because
  `_load_subject_exclusion_list` is the only code path that touches
  the file.
- After the seed, content equals `["is your verification code"]`.
  Property 6 + Property 5 together guarantee that for every subject
  `S` of shape `Booking.com – \w+ is your verification code`,
  `_subject_excluded(S, [...])` returns `True`, so the worker drops
  exactly the same emails the legacy `OTP_SUBJECT_REGEX` did
  (Requirement 12.1). Per-job file outputs (`live.txt`, `noemail.txt`,
  `die.txt`, `unreg.txt`, `domain_skipped.txt`) are byte-identical
  with the pre-feature implementation for identical fixtures
  (Requirement 12.2, validated by INTEGRATION test).

**Existing storage files untouched (Requirement 13.1–13.5):**

- `imap_success.json` (owned by `centralize-imap-success-master`) —
  Property 8 guards this.
- `skip_domains.json` (owned by `custom-skip-domains`) — Property 8
  guards this.
- `access_codes.json` — never read or written by this feature.
- `jobs/<job_id>/...` — never read or written by this feature
  (Requirement 13.6).

### Test-code migration (Requirement 14.3)

This is the **only** code-level migration outside `imap_engine.py` /
`app.py` / `templates/admin.html`. The three subject-keyword-match
test files reach into the now-deleted `OTP_SUBJECT_REGEX` symbol and
must be updated mechanically. The migration is enumerated below
exhaustively — no other test-code change is permitted (Requirement
14.1, 14.2 require zero changes to the other two suites).

#### `tests/test_subject_keyword_match_shown_in_live_preservation.py`

Three call sites + one import + two doc references. Exact replacements:

1. **Line 51–55 (import)** — current:
   ```python
   from imap_engine import (  # noqa: E402
       ImapChecker,
       OTP_SUBJECT_REGEX,
       sender_matches,
   )
   ```
   Replace with:
   ```python
   from imap_engine import (  # noqa: E402
       ImapChecker,
       _subject_excluded,
       sender_matches,
   )
   ```

2. **Line 173 (subject generator filter)** — current:
   ```python
   ).filter(lambda s: not OTP_SUBJECT_REGEX.match(s.strip()))
   ```
   Replace with:
   ```python
   ).filter(lambda s: not _subject_excluded(s, ["is your verification code"]))
   ```

3. **Line 300 (per-UID OTP check inside reference loop)** — current:
   ```python
               if OTP_SUBJECT_REGEX.match(subj_for_uid.strip()):
   ```
   Replace with:
   ```python
               if _subject_excluded(subj_for_uid, ["is your verification code"]):
   ```

4. **Line 413 (test-internal sanity check on generated OTP token)** —
   current:
   ```python
           if not OTP_SUBJECT_REGEX.match(otp_subject.strip()):
   ```
   Replace with:
   ```python
           if not _subject_excluded(otp_subject, ["is your verification code"]):
   ```
   **Polarity note**: this is the inverted form. Original says
   "skip example if regex does NOT match"; the migrated form says
   "skip example if subject is NOT excluded by default pattern". Both
   are semantically equivalent because Property 6 guarantees the
   default seed accepts every `^Booking\.com – \w+ is your
   verification code$` subject.

5. **Lines 11, 139, 163, 371, 375 (docstring/comment references)** —
   purely textual references like "matches `OTP_SUBJECT_REGEX`" or
   "`if not OTP_SUBJECT_REGEX.match(subject.strip()):`" inside
   docstrings/comments. Update to mention the new helper, e.g.
   "matches the default exclusion pattern" or
   "`if not _subject_excluded(subject, self.subject_exclusion_patterns):`".
   These are non-functional but Requirement 14.3(c) explicitly permits
   "updating any synthetic-subject generator that previously asserted
   'matches `OTP_SUBJECT_REGEX`' to instead assert 'is excluded by
   `_subject_excluded` against `Default_Subject_Exclusion_Patterns`'".

#### `tests/test_subject_keyword_match_shown_in_live_fix.py`

One import + comment references:

1. **Line 55 (import)** — current:
   ```python
   from imap_engine import OTP_SUBJECT_REGEX, sender_matches  # noqa: E402
   ```
   Replace with:
   ```python
   from imap_engine import _subject_excluded, sender_matches  # noqa: E402
   ```

2. **Lines 257, 276 (comments)** — purely textual references; update
   per Requirement 14.3(c) to mention `_subject_excluded` against the
   default pattern. The constant `_OTP_SUBJECT` at line 260
   (`"Booking.com \u2013 ABC123 is your verification code"`) is a
   string literal, not a regex symbol, so it does NOT need to change.
   Test logic that uses `_OTP_SUBJECT` to construct emails continues
   to work because Property 6 guarantees the default seed excludes
   exactly those subjects.

#### `tests/test_subject_keyword_match_shown_in_live_bug.py`

This file has NO `OTP_SUBJECT_REGEX` import (it does not appear in any
import block) and NO code-level use of the symbol. The four
references in this file are all inside string literals (docstrings,
function names like `test_otp_subject_regex_remains_outermost_gate`,
and assertion messages). Per Requirement 14.3, those textual
references MAY be updated for clarity but the **test logic itself**
already constructs an email with subject `"Booking.com \u2013 ABC123
is your verification code"` and asserts `_write_live` is NOT called.
That assertion remains valid because Property 6 guarantees the email
is dropped under the default seed. The function name
`test_otp_subject_regex_remains_outermost_gate` MAY be renamed to
`test_default_subject_exclusion_remains_outermost_gate` for accuracy,
but this is non-mandatory; the test itself does not import or call
`OTP_SUBJECT_REGEX`.

#### Summary of required changes

- **Imports replaced**: 2 lines (one in preservation, one in fix).
- **Code-level replacements**: 3 expressions (preservation lines 173,
  300, 413).
- **Total mandatory line edits**: ~5 (matching the user's "~5-line
  test migration" estimate).
- **Optional textual cleanup**: comments/docstrings/test names — does
  not affect green-status.

After these edits, the entire `tests/test_subject_keyword_match_shown_in_live_*.py`
suite passes against the new `imap_engine.py` (Requirement 14.3).

The `tests/test_centralize_imap_success_master_*.py` and
`tests/test_custom_skip_domains_*.py` suites do NOT reference
`OTP_SUBJECT_REGEX` and require zero changes (Requirement 14.1, 14.2).

### Deletion / rollback

To roll back: delete `subject_exclusion_list.json` and
`subject_exclusion_list.json.lock` from project root, restore the
`OTP_SUBJECT_REGEX` constant + the legacy `if not
OTP_SUBJECT_REGEX.match(subject.strip()):` gate in `_worker`, redeploy
previous version. No state in any other file refers to
subject-exclusion data, so the rollback is clean.

To "reset to default" without rolling back: admin deletes
`subject_exclusion_list.json` from disk; next
`_load_subject_exclusion_list` re-seeds.

## Testing Strategy

Test layout (pytest, in workspace root, paralleling the
`custom-skip-domains` suite):

```
tests/subject-exclusion-list/
  test_constants.py                       — SMOKE (1.1, 1.3, 13.5)
  test_normalize.py                       — Property 1 (Hypothesis)
  test_load_save_round_trip.py            — Property 2 (Hypothesis)
  test_post_persist.py                    — Property 3 (Hypothesis + Flask test_client)
  test_validate_reject.py                 — Property 4 (Hypothesis)
  test_subject_excluded.py                — Property 5 (Hypothesis)
  test_default_seed_otp_semantics.py      — Property 6 (Hypothesis)
  test_worker_exclusion_gate.py           — Property 7 (Hypothesis + worker mocks)
  test_neighbors_untouched.py             — Property 8 (Hypothesis)
  test_first_run_seed.py                  — EXAMPLE (2.1, 12.4)
  test_corrupt_file.py                    — EDGE_CASE (2.3, 5.4)
  test_atomic_write_sequence.py           — EXAMPLE (3.1)
  test_filelock_serialization.py          — EXAMPLE (3.2)
  test_crash_mid_write.py                 — EXAMPLE (3.3)
  test_filelock_timeout.py                — EXAMPLE (3.4)
  test_init_before_workers.py             — EXAMPLE (5.1)
  test_snapshot_semantics.py              — EXAMPLE (5.3, 12.3)
  test_no_module_constant.py              — SMOKE (5.2, 14.5)
  test_worker_passes_unstripped_subject.py — EXAMPLE (6.4)
  test_endpoint_auth.py                   — EXAMPLE (7.1, 7.3, 7.4, 8.1, 8.5, 8.6, 10.4, 10.5)
  test_invalid_payload.py                 — EDGE_CASE (8.4, 9.7)
  test_admin_template.py                  — EXAMPLE (10.1, 10.2, 10.3)
  test_no_new_routes.py                   — SMOKE (11.2)
  test_classification_byte_identical.py   — INTEGRATION (12.2, 13.6)
  test_no_new_dependency.py               — SMOKE (14.4)
  test_existing_suites.py                 — INTEGRATION wrapper (11.1, 14.1, 14.2, 14.3)
```

### Unit tests (example-based)

Library: `pytest` (already in repo). Cover EXAMPLE, EDGE_CASE, and
SMOKE classifications:

- **Constants** (`test_constants.py`): `SUBJECT_EXCLUSION_PATH`
  resolves to `os.path.dirname(os.path.abspath(imap_engine.__file__))
  + "/subject_exclusion_list.json"`; `SUBJECT_EXCLUSION_LOCK_PATH ==
  SUBJECT_EXCLUSION_PATH + ".lock"`; the three lock paths
  (`MASTER_IMAP_SUCCESS_LOCK_PATH`, `SKIP_DOMAINS_LOCK_PATH`,
  `SUBJECT_EXCLUSION_LOCK_PATH`) are pairwise distinct (Requirement
  13.5).
- **First-run seed** (`test_first_run_seed.py`): delete file, call
  `_load_subject_exclusion_list()`, assert returned list ==
  `["is your verification code"]` AND on-disk file content matches
  via `json.load` (Requirement 2.1, 12.4).
- **Corrupt file** (`test_corrupt_file.py`): parameterized fixtures
  for `[]` / non-list / non-string-element / invalid JSON; each →
  `_load` returns `[]` AND file bytes unchanged AND
  `ImapChecker(...)` produces `subject_exclusion_patterns == []`
  (Requirement 2.3, 5.4).
- **Atomic write sequence** (`test_atomic_write_sequence.py`): mock
  `os.fsync` and `os.replace`; call `_save_subject_exclusion_list`;
  assert call order tmp-open → write → flush → fsync → close →
  replace (Requirement 3.1).
- **FileLock serialization** (`test_filelock_serialization.py`): spy
  `FileLock.__enter__`/`__exit__`; call `_save` and `_load`; assert
  lock acquired before file I/O and released after (Requirement 3.2).
- **Crash mid-write** (`test_crash_mid_write.py`): pre-write valid V;
  monkeypatch `os.replace` to raise `OSError`; call `_save(V')`;
  assert `OSError` propagates AND on-disk file content equals V
  (Requirement 3.3).
- **FileLock timeout** (`test_filelock_timeout.py`): monkeypatch
  `FileLock` to raise `Timeout`; assert (a) GET endpoint → 503; (b)
  POST endpoint → 503; (c) `ImapChecker(...)` → no raise,
  `subject_exclusion_patterns == list(_DEFAULT_SUBJECT_EXCLUSION_PATTERNS)`
  (Requirement 3.4(a), 3.4(b), 7.5, 8.7).
- **Init before workers** (`test_init_before_workers.py`): spy
  `_load_subject_exclusion_list` and `threading.Thread.start`; assert
  load is called inside `__init__`, before any thread.start triggered
  by `run()` (Requirement 5.1).
- **Snapshot semantics** (`test_snapshot_semantics.py`): instantiate
  `ImapChecker` with file V1; mid-test, `_save(V2)`; assert
  `checker.subject_exclusion_patterns` still equals V1; instantiate a
  second `ImapChecker`; assert its snapshot equals V2 (Requirement
  5.3, 12.3).
- **No module constant** (`test_no_module_constant.py`): assert `not
  hasattr(imap_engine, "OTP_SUBJECT_REGEX")`; assert
  `inspect.getsource(ImapChecker._worker)` contains
  `"self.subject_exclusion_patterns"` AND does NOT contain
  `"OTP_SUBJECT_REGEX"` AND does NOT contain
  `"_DEFAULT_SUBJECT_EXCLUSION_PATTERNS"`; assert
  `_subject_excluded` and `_DEFAULT_SUBJECT_EXCLUSION_PATTERNS` are
  importable from `imap_engine` (Requirement 5.2, 14.5).
- **Worker passes un-stripped subject** (`test_worker_passes_unstripped_subject.py`):
  spy `_subject_excluded`; run `_worker` with mocked mail_conn
  delivering subject `"  is your verification code  "`; assert spy
  received the un-stripped string (Requirement 6.4).
- **Endpoint auth** (`test_endpoint_auth.py`): Flask test_client;
  exhaustive: GET as (anon, non-admin, admin) → (401, 403, 200);
  POST same matrix; non-admin GET to `/admin` → 302 redirect to `/`
  (Requirement 7.1, 7.3, 7.4, 8.1, 8.5, 8.6, 10.4, 10.5).
- **Invalid payload** (`test_invalid_payload.py`): parameterized POST
  with: not-JSON, missing key, non-list, list with int/None/dict
  element → all 400 `{"error": "Invalid payload"}` AND file unchanged
  (Requirement 8.4, 9.7).
- **Admin template** (`test_admin_template.py`): render `/admin` as
  admin; parse HTML; assert presence of element IDs
  `subject-exclusion-textarea`, `save-subject-exclusion-btn`,
  `subject-exclusion-msg`; assert template source contains
  `"/api/admin/subject-exclusion-list"` (both fetch sites) and the
  `JSON.stringify({patterns: lines})` payload shape (Requirement
  10.1, 10.2, 10.3).
- **No new routes** (`test_no_new_routes.py`): snapshot
  `app.url_map.iter_rules()`; assert the only added rules vs
  pre-feature snapshot are `GET` and `POST
  /api/admin/subject-exclusion-list` (Requirement 11.2).
- **No new dependency** (`test_no_new_dependency.py`): read
  `requirements.txt`; assert `filelock` is the only filesystem-lock
  entry and was already pinned by `centralize-imap-success-master`;
  no new entries added by this feature (Requirement 14.4).

### Property-based tests (Hypothesis)

Library: `hypothesis` (already pinned by
`centralize-imap-success-master` test suite — see `requirements.txt`;
this spec adds zero new dependencies, Requirement 14.4).

Each property test is configured with `@settings(max_examples=100)`
(or higher where cheap) and tagged with a comment of the form:

```python
# Feature: subject-exclusion-list, Property 1: Normalization correctness and idempotency
```

The 8 property tests map 1:1 to Properties 1–8 above:

| Property | Generators | Assertion sketch |
|---|---|---|
| 1 | `lists(text(min_size=0, max_size=600))` (allows whitespace, casing, duplicates, control chars) | `_normalize` output: every elem == some `s.strip().lower()`; no empty / no dups / order preserved / lowercased / internal-whitespace preserved / idempotent |
| 2 | `lists(valid_entry_strategy)` where `valid_entry_strategy = text(min_size=1, max_size=500).filter(no_control_char_after_strip).map(strip_lower)` | After `_save` → `_load == value` AND raw bytes == `json.dumps(value, indent=2).encode("utf-8")` AND GET as admin returns `{"patterns": value}` AND second `_load` does not modify bytes/mtime |
| 3 | tuple of (`lists(valid_entry_strategy)` as `O`, `lists(text())` filtered to `_validate(...)[1] == []` as `R`) | After POST(R), file == `_normalize_exclusion_entries(R)` and response.patterns == `_normalize_exclusion_entries(R)` regardless of `O` |
| 4 | `lists(text())` with at least one entry violating empty / >500 / control-char (custom strategy that injects control chars from `_EXCLUSION_CONTROL_CHARS` ∪ `chr(c) for c in range(0x20) if c != 0x20`) | `_validate` returns `([], rejected_subset)`; POST returns 400 with rejected list AND no rule-disclosure field; file unchanged |
| 5 | `text()` for `subject`; `lists(valid_entry_strategy)` for `patterns` | `_subject_excluded(subject, patterns) == any(p in subject.lower() for p in patterns)` |
| 6 | `text(alphabet=string.ascii_letters + string.digits + "_", min_size=1, max_size=20)` for `t` | `_subject_excluded(f"Booking.com \u2013 {t} is your verification code", ["is your verification code"]) == True`; `_subject_excluded(...same..., list(_DEFAULT_SUBJECT_EXCLUSION_PATTERNS)) == True`; first-run-seeded `_load(...)` returns the same pattern list |
| 7 | `text()` for subject; `lists(valid_entry_strategy)` for patterns; mocked mail_conn delivering one email | `emails_data` after `_worker` contains the email iff `_subject_excluded(subject, patterns) is False` (modulo three-arm gate held True via fixture) |
| 8 | `lists(valid_entry_strategy)` | Snapshot `imap_success.json` and `skip_domains.json` content + mtime; call `_save_subject_exclusion_list` and `_load_subject_exclusion_list` across generated values; assert both neighbor files unchanged |

Hypothesis strategies for control-char-violating entries use a pool
that includes the explicit set `{"\r", "\n", "\t", "\v", "\f",
"\x00"}` plus randomly-chosen `chr(c)` for `c < 0x20 and c != 0x20`
— to verify Requirement 9.4's full definition. The strategies
**explicitly include ASCII space `" "` as a NON-rejected character**
to encode the carve-out in Property 4(4).

### Integration tests

#### Flask test_client tests for the two new endpoints

Construct a Flask `test_client` with a fixture that:
1. Sets up an isolated `SUBJECT_EXCLUSION_PATH` via `monkeypatch` to
   point inside `tmp_path` (so tests don't pollute project root).
2. Provides three session contexts: unauthenticated, authenticated
   non-admin, authenticated admin.

Tests exercise the matrix described in `test_endpoint_auth.py` plus
property-based scenarios from Property 2 and Property 3.

#### `ImapChecker` end-to-end byte-identical (`test_classification_byte_identical.py`)

Cover Requirement 12.2 / 13.6 (byte-identical per-job output when
defaults unchanged):

1. Fixture: a small accounts/sender/keyword set with a deterministic
   IMAP fixture where some subjects match `^Booking\.com – \w+ is
   your verification code$` and some do not.
2. Mock `_try_imap_variants` and `imaplib.IMAP4_SSL` so all network
   I/O is replaced by deterministic in-memory responses.
3. Run `ImapChecker(...).run()` to completion (poll `status`).
4. Compute SHA-256 of `live.txt`, `noemail.txt`, `die.txt`,
   `unreg.txt`, `domain_skipped.txt`.
5. Compare against reference digests captured from the pre-feature
   run (committed as fixture file, computed on the pre-feature
   `OTP_SUBJECT_REGEX` path).

#### Cross-suite preservation (`test_existing_suites.py`)

Wrapper test runs the prior-spec suites via subprocess and asserts
exit code 0 (Requirement 11.1, 14.1, 14.2, 14.3):

```python
import subprocess, os
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def test_centralize_master_suite_passes():
    result = subprocess.run(
        ["python", "-m", "pytest", "tests/test_centralize_imap_success_master_*.py", "-q"],
        capture_output=True, text=True, cwd=PROJECT_ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr

def test_custom_skip_domains_suite_passes():
    result = subprocess.run(
        ["python", "-m", "pytest", "tests/test_custom_skip_domains_*.py", "-q"],
        capture_output=True, text=True, cwd=PROJECT_ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr

def test_subject_keyword_match_suite_passes_after_migration():
    """Asserts the migration enumerated in design.md §Migration is
    sufficient. Run by the implementation phase AFTER the 5-line edits
    are applied."""
    result = subprocess.run(
        ["python", "-m", "pytest", "tests/test_subject_keyword_match_shown_in_live_*.py", "-q"],
        capture_output=True, text=True, cwd=PROJECT_ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr
```

### Test execution

Test runner: `pytest`, single-execution mode (no `--watch`).

Recommended commands:

```bash
# Run only the new feature suite
python -m pytest tests/subject-exclusion-list/ -q

# Run preservation suites referenced by Requirement 11 / 14
python -m pytest tests/test_centralize_imap_success_master_*.py \
                 tests/test_custom_skip_domains_*.py \
                 tests/test_subject_keyword_match_shown_in_live_*.py -q

# Run everything
python -m pytest -q
```

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **Timeout-during-init blocks `ImapChecker` construction** if the file lock is held by another process for >30s, causing job creation to fail. | Low (lock is held for milliseconds in normal operation) | High (job creation fails for end users) | Requirement 3.4(b): `__init__` catches `filelock.Timeout` and falls back to `list(_DEFAULT_SUBJECT_EXCLUSION_PATTERNS)` in-memory rather than raising. Constructor always returns. Logged to stderr for ops visibility. Validated by `test_filelock_timeout.py` (EXAMPLE 3.4(c)). |
| **Accidental wipe via empty POST**: admin clicks Save with empty textarea, all subject-exclusion patterns gone, every email (including OTPs) appears in `live.txt`. | Medium (UX hazard) | Medium (regression of OTP-spam-in-live for default users) | (a) Behavior is by design — empty list is valid (Requirement 6.2). (b) UI shows confirmation message with count after save: `"Tersimpan (0 entries)"` makes the wipe visible. (c) Default seed is recoverable: admin deletes `subject_exclusion_list.json` from disk, next read re-seeds to `["is your verification code"]`. (d) Future enhancement (out of scope): "Reset to default" button. |
| **Semantics flip introduces silent regression** if the implementer transcribes `_subject_excluded` returning the wrong polarity (returns `True` when it should be `False` or vice versa). | Low (helper is 2 lines and explicitly documented) | High (every email either always dropped or never dropped; tests catch but only at integration level) | (a) Property 5 directly asserts the predicate equals `any(p in subject.lower() for p in patterns)` — direct, by-construction. (b) Property 6 asserts the default-seed-preserves-OTP property explicitly. (c) Property 7 ties `_worker` behavior back to `_subject_excluded`. Three independent properties triangulate the polarity. |
| **`_DEFAULT_SUBJECT_EXCLUSION_PATTERNS` accidentally referenced from `_worker`** (mirrors the `_DEFAULT_SKIP_SET` / `DOMAINS_TO_SKIP_KEYWORDS` regression risk from `custom-skip-domains`). Would break Requirement 5.2's "single source of truth" guarantee for snapshot semantics. | Low (small file, reviewer-visible) | Medium (snapshot semantics break: jobs would see the default rather than the on-disk edited list) | SMOKE test `test_no_module_constant.py` greps `inspect.getsource(ImapChecker._worker)` for the constant name and fails the build if it appears. Validated continuously by CI. |
| **`OTP_SUBJECT_REGEX` accidentally re-introduced** by a developer unaware of the deletion. | Low (constant is small) | Low (test would still pass because the new gate runs first; but creates dead code) | SMOKE test `test_no_module_constant.py` asserts `not hasattr(imap_engine, "OTP_SUBJECT_REGEX")`. |
| **FileLock contention between three storage files** if all three are heavily written (master, skip-domains, subject-exclusion). | Low (separate lock paths — Requirement 13.5 + Property 8) | Low | Three separate `FileLock` instances on three separate paths. None can block any other. Verified by `test_constants.py` (SMOKE) and Property 8 (Hypothesis). |
| **Encoding issues with Unicode whitespace inside patterns**: admin pastes a pattern with NBSP (`U+00A0`) or ideographic space (`U+3000`). Requirement 9.4 explicitly allows these (only ASCII control chars are rejected); they are stored verbatim. Then the pattern compares against a subject that contains a regular ASCII space — substring match fails. | Medium (admin pastes from Word doc) | Low (entry passes validation but won't match anything in real subjects) | (a) Requirement 9.4 explicitly allows non-control whitespace because subject patterns naturally contain them. (b) Documentation in admin UI text emphasizes "substring, case-insensitive". (c) Future enhancement (out of scope): NFKC-normalize patterns before storage to fold NBSP→ASCII space. |
| **Stale `.tmp` file accumulates** if many writes crash mid-replace. | Very low | Negligible (file is overwritten on next successful write) | `_save_subject_exclusion_list` opens `tmp_path` in `"w"` mode (truncates), so stale content from a previous crash is replaced atomically on the next write. Requirement 3.3 explicitly disallows treating stale tmp as a source of truth on read. |
| **Hypothesis flakiness in concurrency tests**: Property 2/3/8 each touch real disk via `_save` / `_load`. Multiple test workers could race. | Low | Low (test failures, not production bugs) | Each Hypothesis test uses `tmp_path` (per-test isolation). FileLock path is also derived from the per-test tmp directory via `monkeypatch` of `SUBJECT_EXCLUSION_PATH` and `SUBJECT_EXCLUSION_LOCK_PATH`. Cross-test races cannot occur. |
| **Frontend XSS via pattern rendering**: malicious admin saves a pattern containing HTML; UI textarea displays it raw. | Very low (only admin can write; admin already has full control) | Negligible (textarea preserves plain text via `.value`, not `.innerHTML`) | The JS uses `exclTextarea.value = ...` which sets text content, not HTML. No `innerHTML` writes anywhere. Validation rejects control chars but not `<`/`>` characters by design (subjects naturally contain those). |
| **Test migration drift**: a future spec modifies the three subject-keyword-match test files in a way that re-introduces `OTP_SUBJECT_REGEX` references. | Low | Low (caught by import error since the symbol no longer exists) | The Python import `from imap_engine import OTP_SUBJECT_REGEX` would raise `ImportError` at collection time. CI guarantees this regression is loud. |
| **Worker migration regression**: the literal change from `if not OTP_SUBJECT_REGEX.match(subject.strip()):` to `if not _subject_excluded(subject, self.subject_exclusion_patterns):` is one expression but spans the entire downstream logic of the email-keep branch. Indentation drift in this dense block could break the SUBJECT-branch UID union check from `subject-keyword-match-shown-in-live`. | Low (single-expression change) | Medium (regression of `subject-keyword-match-shown-in-live` semantics) | (a) The diff is one boolean expression — surrounding lines remain byte-identical (Requirement 6.3). (b) Property 7 directly asserts `_worker` keep/drop semantics. (c) Cross-suite test (`test_existing_suites.py`) re-runs `subject-keyword-match-shown-in-live` test suite end-to-end. |

## Final Requirement Coverage Validation

Each acceptance criterion from `requirements.md` maps to one or more
sections of this design and one or more tests. Every numbered
acceptance criterion (1.1 through 14.5) is covered.

| Req | Design coverage | Test coverage |
|---|---|---|
| 1.1 `SUBJECT_EXCLUSION_PATH` resolves relative to `imap_engine.py` | Architecture → Module-level constants | SMOKE `test_constants.py` |
| 1.2 JSON array of strings, UTF-8, indent=2, byte-equivalent format | Architecture → `_save_subject_exclusion_list`; Data Models → On-disk | Property 2 |
| 1.3 `SUBJECT_EXCLUSION_LOCK_PATH = SUBJECT_EXCLUSION_PATH + ".lock"` | Architecture → Module-level constants | SMOKE `test_constants.py` |
| 2.1 First-run seed | Architecture → `_load_subject_exclusion_list`; Sequence Diagram 3 | EXAMPLE `test_first_run_seed.py` |
| 2.2 Existing file not overwritten on read | Architecture → `_load_subject_exclusion_list` | Property 2(4) |
| 2.3 Corrupt file → return `[]` without rewrite | Architecture → `_load_subject_exclusion_list`; Failure Handling | EDGE_CASE `test_corrupt_file.py` |
| 2.4 Default seed semantics preserves OTP exclusion | Overview; Architecture → `_DEFAULT_SUBJECT_EXCLUSION_PATTERNS` rationale | Property 6 |
| 3.1 Atomic write sequence | Architecture → `_save_subject_exclusion_list` | EXAMPLE `test_atomic_write_sequence.py` |
| 3.2 FileLock wraps RMW | Architecture → both load/save helpers; Sequence Diagram 2 | EXAMPLE `test_filelock_serialization.py` |
| 3.3 Crash mid-write preserves old state | Architecture → `_save_subject_exclusion_list`; Failure Handling | EXAMPLE `test_crash_mid_write.py` |
| 3.4 FileLock timeout → 503 / fallback | Architecture → `__init__`, endpoints; Failure Handling | EXAMPLE `test_filelock_timeout.py` |
| 3.5 Byte-format equivalence with `_atomic_update_master`/`_save_skip_domains` | Architecture → `_save_subject_exclusion_list` | Property 2(2) |
| 4.1 Normalization sequence (strip → lower → drop-empty → dedupe) | Architecture → `_normalize_exclusion_entries` | Property 1 |
| 4.2 First-occurrence order preserved | (subsumed by 4.1) | Property 1(4) |
| 4.3 Internal whitespace preserved | Architecture → `_normalize_exclusion_entries` (rationale paragraph) | Property 1(6) |
| 4.4 Output already-lowercased | Architecture → `_normalize_exclusion_entries` | Property 1(5) |
| 5.1 `__init__` reads BEFORE workers spawn | Architecture → `ImapChecker.__init__` change; Sequence Diagram 2 | EXAMPLE `test_init_before_workers.py` |
| 5.2 `OTP_SUBJECT_REGEX` removed; default privatized; `_worker` reads instance attr | Architecture → Removal section, `_worker` change | SMOKE `test_no_module_constant.py` |
| 5.3 Snapshot semantics non-retroactive | Architecture → `__init__` (single assignment); Sequence Diagram 2 | EXAMPLE `test_snapshot_semantics.py` |
| 5.4 Corrupt file at init → empty list, no rewrite | Architecture → `__init__`; Failure Handling | EDGE_CASE `test_corrupt_file.py` |
| 6.1 `_subject_excluded` helper signature/semantics | Architecture → `_subject_excluded` helper | Property 5 |
| 6.2 Empty list ⇒ no exclusion | (subsumed by 6.1) | Property 5(1) |
| 6.3 `_worker` predicate replacement; control-flow byte-identical | Architecture → `_worker` change | Property 7 + SMOKE `test_no_module_constant.py` |
| 6.4 Pass `subject` un-stripped to helper | Architecture → `_worker` change (rationale paragraph) | EXAMPLE `test_worker_passes_unstripped_subject.py` |
| 6.5 Case-insensitive both sides | Architecture → `_subject_excluded`, `_normalize_exclusion_entries` | Property 5(2) |
| 7.1 GET requires `@login_required` + `is_admin` | Architecture → `app.py` GET handler | EXAMPLE `test_endpoint_auth.py` |
| 7.2 GET returns `{"patterns": [...]}` | Architecture → `app.py` GET handler | Property 2(3) |
| 7.3 GET 401 unauthenticated | Architecture → `login_required` reuse | EXAMPLE `test_endpoint_auth.py` |
| 7.4 GET 403 non-admin | Architecture → GET handler | EXAMPLE `test_endpoint_auth.py` |
| 7.5 GET 503 on FileLock timeout | Architecture → GET handler; Failure Handling | EXAMPLE `test_filelock_timeout.py` |
| 8.1 POST requires `@login_required` + `is_admin` | Architecture → POST handler | EXAMPLE `test_endpoint_auth.py` |
| 8.2 POST normalize+persist+200 | Architecture → POST handler | Property 3 |
| 8.3 Full-replace semantics | Architecture → POST handler | Property 3 (varied pre-state) |
| 8.4 Invalid payload → 400 | Architecture → POST handler structural check | EDGE_CASE `test_invalid_payload.py` |
| 8.5 POST 401 | Architecture → `login_required` reuse | EXAMPLE `test_endpoint_auth.py` |
| 8.6 POST 403 | Architecture → POST handler | EXAMPLE `test_endpoint_auth.py` |
| 8.7 POST 503 on FileLock timeout | Architecture → POST handler; Failure Handling | EXAMPLE `test_filelock_timeout.py` |
| 9.1 Validation order short-circuit (empty → >500 → ctrl) | Architecture → `_validate_exclusion_entries` | Property 4 |
| 9.2 Reject empty after strip | Architecture → `_validate_exclusion_entries` | Property 4 |
| 9.3 Reject >500 chars | Architecture → `_validate_exclusion_entries` | Property 4 |
| 9.4 Reject control chars; allow ASCII space | Architecture → `_has_control_char`, `_EXCLUSION_CONTROL_CHARS` | Property 4(4) |
| 9.5 Rejection format (as-sent, first-occurrence, dups, no rule disclosure) | Architecture → POST handler + `_validate_exclusion_entries` | Property 4(2) |
| 9.6 All-or-nothing | Architecture → `_validate_exclusion_entries`, POST handler | Property 4(3) |
| 9.7 Non-string element → Invalid payload | Architecture → POST handler structural check | EDGE_CASE `test_invalid_payload.py` |
| 10.1 New section in template | Architecture → `templates/admin.html` Section baru | EXAMPLE `test_admin_template.py` |
| 10.2 Template loads via GET on mount | Architecture → admin.html IIFE JS | EXAMPLE `test_admin_template.py` |
| 10.3 Save POSTs `{"patterns": lines}` (split("\n")) | Architecture → admin.html IIFE JS | EXAMPLE `test_admin_template.py` |
| 10.4 Non-admin doesn't see section | Architecture → relies on `/admin` redirect at `app.py:148` | EXAMPLE `test_endpoint_auth.py` (redirect for non-admin) |
| 10.5 Same auth mechanism, no extra layer | Architecture → reuses `@login_required` + `is_admin` body check | EXAMPLE `test_endpoint_auth.py` |
| 11.1 No regressions on existing API surface | Architecture → only two new routes | INTEGRATION `test_existing_suites.py` |
| 11.2 Only two new routes | Architecture → `app.py` changes | SMOKE `test_no_new_routes.py` |
| 12.1 Default-seeded behavior matches OTP_SUBJECT_REGEX exclusion | Migration; Property 6 | Property 6 |
| 12.2 Byte-identical per-job files on default seed | Migration; Testing Strategy → ImapChecker end-to-end | INTEGRATION `test_classification_byte_identical.py` |
| 12.3 Edited list affects new jobs only | Architecture → `__init__` (snapshot); Sequence Diagram 2 | EXAMPLE `test_snapshot_semantics.py` |
| 12.4 First-run seed writes exactly `["is your verification code"]` | Architecture → `_DEFAULT_SUBJECT_EXCLUSION_PATTERNS`; Sequence Diagram 3 | EXAMPLE `test_first_run_seed.py` |
| 13.1 No write to `imap_success.json` | Architecture → separate helpers / locks | Property 8 |
| 13.2 No schema change to `imap_success.json` | (subsumed by 13.1 byte-identity) | Property 8 |
| 13.3 No write to `skip_domains.json` | Architecture → separate helpers / locks | Property 8 |
| 13.4 No schema change to `skip_domains.json` | (subsumed by 13.3 byte-identity) | Property 8 |
| 13.5 Distinct lock paths | Architecture → `SUBJECT_EXCLUSION_LOCK_PATH` definition | SMOKE `test_constants.py` |
| 13.6 No change to per-job file format | Architecture → `_worker` change scope | INTEGRATION `test_classification_byte_identical.py` |
| 14.1 `centralize-imap-success-master` suite passes unchanged | Migration → tests not affected | INTEGRATION `test_existing_suites.py` |
| 14.2 `custom-skip-domains` suite passes unchanged | Migration → tests not affected | INTEGRATION `test_existing_suites.py` |
| 14.3 `subject-keyword-match-shown-in-live` suite passes after enumerated migration | Migration → exact line-by-line edits enumerated | INTEGRATION `test_existing_suites.py` (after migration applied) |
| 14.4 No new dependency in `requirements.txt` | Architecture → reuses `filelock` | SMOKE `test_no_new_dependency.py` |
| 14.5 `_subject_excluded` and `_DEFAULT_SUBJECT_EXCLUSION_PATTERNS` importable | Architecture → Public surface table | SMOKE `test_no_module_constant.py` |

All 56 numbered acceptance criteria across Requirements 1.1 through
14.5 are mapped to design coverage and at least one test (property,
example, edge-case, integration, or smoke).
