# Design Document

## Overview

Saat ini `imap_engine.py` mendefinisikan satu set hardcoded di module
scope:

```python
# imap_engine.py:30
DOMAINS_TO_SKIP_KEYWORDS = {"hotmail", "live", "msn", "outlook", "yahoo", "interia", "poczta.fm"}
```

dan `_worker` mengevaluasi-nya dengan substring containment
(`imap_engine.py:410`):

```python
if any(kw in domain for kw in DOMAINS_TO_SKIP_KEYWORDS):
    self._write_domain_skip(email_addr, password)
    self._update_progress("skipped")
    queue.task_done()
    continue
```

Daftar tersebut tidak bisa diedit tanpa redeploy. Fitur ini
mempromosikan-nya menjadi state on-disk yang dipersist ke
`skip_domains.json` di project root, di-load ulang per-job di
`ImapChecker.__init__`, dan diekspos ke admin lewat dua endpoint Flask
baru (`GET` dan `POST /api/admin/skip-domains`) plus satu section di
`templates/admin.html`.

Implementasi reuse pola filesystem yang sudah dibangun spec sebelumnya
`centralize-imap-success-master`:

- `MASTER_LOCK_TIMEOUT_SECONDS = 30` (`imap_engine.py:35`) dipakai ulang
  sebagai timeout `filelock.FileLock` untuk file baru.
- `_atomic_update_master` (`imap_engine.py:138`) — pola `tmp` +
  `flush` + `fsync` + `os.replace` di dalam `FileLock` — disalin
  bentuknya untuk dua helper baru `_load_skip_domains` dan
  `_save_skip_domains`. Bedanya: payload-nya `list[str]` bukan `dict`,
  dan helper baru memang melakukan unconditional write saat seed
  first-run, tidak hanya update conditional.

Snapshot semantics: setiap `ImapChecker` memuat daftar skip ke
`self.skip_domain_keywords` SEKALI di `__init__` sebelum thread worker
di-spawn di `run()` (`imap_engine.py:484`). Job yang sedang berjalan
memakai snapshot-nya sendiri; edit admin di tengah eksekusi job lain
tidak retroaktif.

Auth pakai pola yang persis sama dengan `/api/admin/add-code`
(`app.py:148`) dan `/api/admin/delete-code` (`app.py:171`): dekorator
`@login_required` (`app.py:65`) plus pengecekan
`session.get("is_admin")` di body handler.

UI: satu section baru di bawah "Daftar Kode Akses"
(`templates/admin.html:81`) berisi `<textarea>` + tombol Simpan + status
message. Script inline di template yang sama mengikuti pola yang sudah
dipakai handler `add-code-btn` dan `deleteCode`.

## Glossary

Term di bawah ini direplikasi verbatim dari `requirements.md` agar
desain dan requirement berbicara dalam vocabulary yang sama. Term di
bagian "Design-time additions" hanya muncul di desain — itu nama-nama
simbol Python konkret.

### Direplikasi dari `requirements.md`

- **Skip_Domains_File**: File JSON tunggal di project root,
  `skip_domains.json`, di direktori yang sama dengan `imap_engine.py`,
  `app.py`, dan master `imap_success.json`. Format: array of strings
  (`["hotmail", "live", ...]`).
- **Skip_Domains_Lock_Path**: Path file lock cross-process yang dipakai
  `filelock.FileLock` untuk koordinasi baca/tulis `Skip_Domains_File`.
  Bernilai `Skip_Domains_File + ".lock"`.
- **Default_Skip_Set**: Daftar default yang menjadi seed pada first run
  bila `Skip_Domains_File` belum ada — `["hotmail", "live", "msn",
  "outlook", "yahoo", "interia", "poczta.fm"]`.
- **Engine**: Class `ImapChecker` di `imap_engine.py`.
- **Admin_UI**: Halaman `/admin` (`templates/admin.html`).
- **Admin_API**: Pasangan endpoint Flask
  `GET /api/admin/skip-domains` dan `POST /api/admin/skip-domains`.
- **Skip_Domain_Entry**: String pattern setelah normalisasi (strip,
  lower, dedupe-preserve-order).

### Design-time additions (nama simbol Python konkret)

- **`SKIP_DOMAINS_PATH`**: Konstanta module-level di `imap_engine.py`
  yang merealisasikan `Skip_Domains_File`.
  Definisi:
  ```python
  SKIP_DOMAINS_PATH = os.path.join(
      os.path.dirname(os.path.abspath(__file__)),
      "skip_domains.json",
  )
  ```
- **`SKIP_DOMAINS_LOCK_PATH`**: Konstanta module-level
  `SKIP_DOMAINS_PATH + ".lock"` yang merealisasikan
  `Skip_Domains_Lock_Path`.
- **`_DEFAULT_SKIP_SET`**: Konstanta module-level **privat** (prefix
  underscore — sengaja tidak diekspor) yang merealisasikan
  `Default_Skip_Set`. Tipe: `list[str]` (bukan `set`) supaya urutan
  seed deterministik untuk Requirement 12.3:
  ```python
  _DEFAULT_SKIP_SET = ["hotmail", "live", "msn", "outlook", "yahoo", "interia", "poczta.fm"]
  ```
- **`_load_skip_domains() -> list[str]`**: Helper module-level baru di
  `imap_engine.py` yang membaca `SKIP_DOMAINS_PATH` di dalam
  `FileLock`, melakukan first-run seed bila perlu, dan mengembalikan
  list of strings (atau `[]` bila file korup — Requirement 2.3).
- **`_save_skip_domains(value: list[str]) -> None`**: Helper
  module-level baru yang menulis `value` (sudah dinormalisasi dan
  divalidasi oleh caller) ke `SKIP_DOMAINS_PATH` via tmp + fsync +
  `os.replace`, di dalam `FileLock`.
- **`_normalize_skip_entries(raw: list[str]) -> list[str]`**: Helper
  pure-function yang menerapkan strip → lower → drop-empty →
  dedupe-preserving-order (Requirement 4.1).
- **`_validate_skip_entries(raw: list[str]) -> tuple[list[str], list[str]]`**:
  Helper pure-function yang mengembalikan `(valid_normalized, rejected_raw)`.
  `rejected_raw` adalah subset dari `raw` (entry asli sebelum strip/lower)
  yang melanggar salah satu dari empty / >255 / contains-whitespace
  (Requirement 9.1–9.3, short-circuit).
- **`self.skip_domain_keywords`**: Atribut instance baru pada
  `ImapChecker`, tipe `list[str]`. Diisi sekali di `__init__` dan
  TIDAK pernah dimutasi setelahnya (snapshot per-job, Requirement 5.3).

## Architecture

Semua perubahan terlokalisasi di tiga file:

1. `imap_engine.py` — konstanta + dua helper baru + perubahan
   `__init__` + perubahan `_worker` + penghapusan `DOMAINS_TO_SKIP_KEYWORDS`.
2. `app.py` — dua endpoint baru + dua helper validation/normalization
   (di-import dari `imap_engine`).
3. `templates/admin.html` — satu section baru + satu blok JS inline.

Tidak ada file baru di Python source (helper-helper hidup di
`imap_engine.py` agar Engine dan Admin_API berbagi satu sumber
kebenaran). Satu file baru di disk: `skip_domains.json` (auto-create
saat first read).

### `imap_engine.py` changes

#### Module-level constants (sekitar baris 30, mengganti `DOMAINS_TO_SKIP_KEYWORDS`)

```python
# REMOVED: DOMAINS_TO_SKIP_KEYWORDS = {"hotmail", "live", ...}

# Skip-domains file (project root) — user-editable list of substrings
# matched against email domain. Loaded per-job in ImapChecker.__init__.
SKIP_DOMAINS_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "skip_domains.json",
)
SKIP_DOMAINS_LOCK_PATH = SKIP_DOMAINS_PATH + ".lock"

# First-run seed only. NOT referenced from _worker — see Requirement 5.2.
_DEFAULT_SKIP_SET = ["hotmail", "live", "msn", "outlook", "yahoo", "interia", "poczta.fm"]
```

`MASTER_LOCK_TIMEOUT_SECONDS` (`imap_engine.py:35`) dipakai ulang
tanpa duplikasi konstanta baru.

#### `_load_skip_domains()` helper

Tanggung jawab:

1. Acquire `FileLock(SKIP_DOMAINS_LOCK_PATH, timeout=MASTER_LOCK_TIMEOUT_SECONDS)`.
2. Bila `os.path.exists(SKIP_DOMAINS_PATH)` False → seed first-run:
   tulis `_DEFAULT_SKIP_SET` via jalur atomik (tmp + fsync +
   `os.replace`) lalu kembalikan `list(_DEFAULT_SKIP_SET)`.
3. Bila file ada → `json.load`. Validasi tipe: harus `list` dan setiap
   elemen `str`. Bila tidak → return `[]` tanpa menulis (Requirement 2.3).
4. Pada `filelock.Timeout` propagate ke caller (caller `__init__`
   menangkap-nya untuk fallback ke `_DEFAULT_SKIP_SET` in-memory per
   Requirement 3.4(b); caller Admin_API menangkap-nya untuk respons
   503 per Requirement 3.4(a)).

Pseudocode:

```python
def _load_skip_domains() -> list[str]:
    """Load skip-domains list from disk, seeding default on first run.

    Acquires SKIP_DOMAINS_LOCK_PATH (filelock, MASTER_LOCK_TIMEOUT_SECONDS).
    On missing file: writes _DEFAULT_SKIP_SET via tmp + fsync + os.replace,
    returns a copy of _DEFAULT_SKIP_SET.
    On corrupt file (JSON error or not list[str]): returns [] without writing.
    Raises filelock.Timeout if lock unavailable within timeout.
    """
    lock = FileLock(SKIP_DOMAINS_LOCK_PATH, timeout=MASTER_LOCK_TIMEOUT_SECONDS)
    with lock:
        if not os.path.exists(SKIP_DOMAINS_PATH):
            # First-run seed.
            _atomic_write_skip_domains(list(_DEFAULT_SKIP_SET))
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

`_atomic_write_skip_domains` di atas adalah inner staticmethod / helper
modul yang melakukan tmp + flush + fsync + `os.replace` (lihat
`_save_skip_domains` di bawah — keduanya share implementation; in
practice ini adalah satu helper privat yang dipanggil baik oleh
seeder maupun saver).

#### `_save_skip_domains(value)` helper

Tanggung jawab:

1. Acquire `FileLock(SKIP_DOMAINS_LOCK_PATH, timeout=MASTER_LOCK_TIMEOUT_SECONDS)`.
2. Tulis `value` (list of strings yang sudah dinormalisasi) ke
   `SKIP_DOMAINS_PATH + ".tmp"` dengan `json.dump(..., indent=2)`,
   lalu `flush` + `fsync`.
3. `os.replace(tmp, SKIP_DOMAINS_PATH)` — rename atomik.
4. Propagate `filelock.Timeout` dan `OSError` ke caller.

Pseudocode:

```python
def _save_skip_domains(value: list[str]) -> None:
    """Atomically write list of skip-domain entries to SKIP_DOMAINS_PATH.

    Caller is responsible for normalization (strip/lower/dedupe) and
    validation. This helper only does the atomic write under FileLock.
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

Format on-disk byte-identik dengan `_atomic_update_master`
(Requirement 3.5): keduanya pakai `json.dump(value, f, indent=2)`,
UTF-8 default, `flush` + `fsync` + `os.replace`. Diff byte-level antar
keduanya untuk `value` yang sama hanya akan berbeda karena `value`-nya
beda struktur (list vs dict), bukan format encoder.

#### `_normalize_skip_entries(raw)` helper

Pure function. Tidak menyentuh disk, tidak ambil lock. Dipanggil oleh
Admin_API POST handler sebelum `_save_skip_domains`.

```python
def _normalize_skip_entries(raw: list[str]) -> list[str]:
    """Normalize per Requirement 4.1: strip → lower → drop-empty → dedupe (preserve order)."""
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

#### `_validate_skip_entries(raw)` helper

Pure function. Mengimplementasikan Requirement 9.1–9.3 (short-circuit).
Mengembalikan `(valid_normalized, rejected_raw)`. `rejected_raw` adalah
list of entry **apa adanya** dari `raw` yang gagal salah satu cek
(belum di-strip / belum di-lower) dengan urutan kemunculan
dipertahankan dan duplikat dipertahankan (Requirement 9.4).

```python
import re
_WHITESPACE_RE = re.compile(r"\s")

def _validate_skip_entries(raw: list[str]) -> tuple[list[str], list[str]]:
    """Validate skip entries per Requirement 9.1-9.3 with short-circuit.

    Returns (valid_normalized, rejected_raw).
    - valid_normalized: list of entries that pass all three checks, after
      strip+lower normalization, deduplicated preserving first-occurrence order.
    - rejected_raw: entries from `raw` that failed at least one check,
      reported as-sent (no strip/lower), preserving first-occurrence order
      and including duplicates.
    """
    rejected = []
    valid_pre_dedupe = []
    for entry in raw:
        stripped = entry.strip()
        # Check 1: empty after strip
        if stripped == "":
            rejected.append(entry)
            continue
        # Check 2: length > 255 after strip
        if len(stripped) > 255:
            rejected.append(entry)
            continue
        # Check 3: contains whitespace after strip (\s = ASCII + Unicode)
        if _WHITESPACE_RE.search(stripped):
            rejected.append(entry)
            continue
        valid_pre_dedupe.append(entry)

    if rejected:
        # Requirement 9.5: all-or-nothing. Caller will short-circuit.
        return [], rejected

    # All entries valid → normalize+dedupe (Requirement 4)
    return _normalize_skip_entries(valid_pre_dedupe), []
```

Catatan tentang Requirement 9.6: validasi "elemen non-string" (int,
None, dict di list) **bukan** tanggung jawab `_validate_skip_entries`.
Itu ditangani lebih awal di POST handler dengan pengecekan
`all(isinstance(x, str) for x in domains)` → respons HTTP 400
`{"error": "Invalid payload"}` (Requirement 8.4) sebelum helper ini
dipanggil.

#### `ImapChecker.__init__` change

Sebelum (`imap_engine.py` ~baris 220, akhir `__init__`):

```python
# State
self.unreg_domains = set()
self.imap_success_config = self._load_imap_success_config()
```

Sesudah:

```python
# State
self.unreg_domains = set()
self.imap_success_config = self._load_imap_success_config()

# Skip-domains snapshot for this job (Requirement 5.1, 5.3).
# Loaded BEFORE any worker thread is spawned in run().
try:
    self.skip_domain_keywords = _load_skip_domains()
except Timeout:
    # Requirement 3.4(b): fallback to default in-memory; do NOT raise from constructor.
    print(
        f"[imap_engine] skip-domains lock timeout, falling back to default in-memory",
        file=sys.stderr,
    )
    self.skip_domain_keywords = list(_DEFAULT_SKIP_SET)
```

Karena `run()` (`imap_engine.py:484`) baru men-spawn thread worker
SETELAH `__init__` selesai, snapshot dijamin ada sebelum eksekusi
paralel mulai (Requirement 5.1).

#### `_worker` change

Satu baris berubah (`imap_engine.py:410`):

Sebelum:

```python
if any(kw in domain for kw in DOMAINS_TO_SKIP_KEYWORDS):
```

Sesudah:

```python
if any(kw in domain for kw in self.skip_domain_keywords):
```

Tidak ada perubahan lain di `_worker`. Semantics matching sama persis
(Requirement 6.1, 6.3): `domain = email_addr.split("@")[-1].lower()` di
sisi kiri, dan setiap entry di `self.skip_domain_keywords` sudah
lowercase di sisi kanan (karena disimpan via `_normalize_skip_entries`,
atau berasal dari `_DEFAULT_SKIP_SET` yang memang sudah lowercase).

#### Removal of `DOMAINS_TO_SKIP_KEYWORDS`

Konstanta `DOMAINS_TO_SKIP_KEYWORDS` (`imap_engine.py:30`) dihapus
seluruhnya. `_DEFAULT_SKIP_SET` MENGGANTIKAN-nya hanya untuk seed
first-run dan fallback timeout di `__init__`. `_worker` tidak boleh
mereferensi `_DEFAULT_SKIP_SET` (Requirement 5.2). Dijamin oleh
grep-test di test suite (lihat Testing Strategy).

### `app.py` changes

Dua endpoint baru, ditambahkan di area antara `delete_code`
(`app.py:171`) dan `start_check` (`app.py:189`) untuk mengelompokkan
endpoint admin di satu blok. Auth pattern persis sama dengan
`add_code` / `delete_code`.

```python
from imap_engine import (
    _load_skip_domains,
    _save_skip_domains,
    _normalize_skip_entries,
    _validate_skip_entries,
)
from filelock import Timeout


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


@app.route("/api/admin/skip-domains", methods=["POST"])
@login_required
def post_skip_domains():
    if not session.get("is_admin"):
        return jsonify({"error": "Admin only"}), 403

    # Requirement 8.4: structural validation
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid payload"}), 400
    domains_raw = data.get("domains")
    if not isinstance(domains_raw, list):
        return jsonify({"error": "Invalid payload"}), 400
    # Requirement 9.6 → 8.4: any non-string element ⇒ 400 Invalid payload
    if not all(isinstance(x, str) for x in domains_raw):
        return jsonify({"error": "Invalid payload"}), 400

    # Requirement 9.1-9.5: per-entry validation, all-or-nothing
    valid, rejected = _validate_skip_entries(domains_raw)
    if rejected:
        return jsonify({"error": "Invalid entries", "rejected": rejected}), 400

    # Requirement 3.1, 8.2, 8.3: atomic full-replace write
    try:
        _save_skip_domains(valid)
    except Timeout:
        return jsonify({"error": "Skip-domains file is busy, please retry"}), 503

    return jsonify({"success": True, "domains": valid}), 200
```

Dekorator `login_required` (`app.py:65`) sudah menangani 401 untuk
request `/api/...` yang belum ter-autentikasi (mengembalikan
`jsonify({"error": "Unauthorized"}), 401`), sehingga Requirement 7.3
dan 8.5 dipenuhi tanpa kode tambahan. Pengecekan
`session.get("is_admin")` di body handler menangani 403 sesuai
Requirement 7.4 dan 8.6.

Tidak ada endpoint lain di `app.py` yang disentuh — Requirement 11.1
dijamin secara struktural (diff hanya menambah dua route baru).

### `templates/admin.html` changes

Section baru disisipkan setelah penutup `</div>` dari section "Daftar
Kode Akses" (`templates/admin.html:81`–~98) dan SEBELUM `</main>`:

```html
<!-- Skip Domains -->
<div class="form-section">
    <h2 style="margin-bottom:20px;">🚫 Skip Domains</h2>
    <p style="color:#94a3b8;margin-bottom:12px;font-size:14px;">
        Satu pattern per baris. Akun dengan domain yang memuat salah satu
        pattern (substring, case-insensitive) akan di-skip oleh job IMAP
        check.
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

JavaScript inline ditambahkan di blok `<script>` yang sudah ada di
bawah, mengikuti pola `addBtn` / `deleteCode`:

```javascript
// Skip Domains: load on page mount
const skipTextarea = document.getElementById("skip-domains-textarea");
const skipSaveBtn = document.getElementById("save-skip-domains-btn");
const skipMsgDiv = document.getElementById("skip-domains-msg");

function showSkipMsg(text, isError) {
    skipMsgDiv.textContent = text;
    skipMsgDiv.className = "admin-msg " + (isError ? "error" : "success");
    skipMsgDiv.classList.remove("hidden");
    setTimeout(() => skipMsgDiv.classList.add("hidden"), 3000);
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

Section ini hanya ada di `admin.html`, dan `/admin` di `app.py:140`
sudah melakukan redirect non-admin ke `/`, sehingga Requirement 10.4
dipenuhi tanpa Jinja `{% if is_admin %}` tambahan di sekitar section
(seluruh template `admin.html` adalah admin-only).

## Components and Interfaces

```
┌──────────────────────────────────────────────────────────────────┐
│                      Browser (Admin_UI)                          │
│   templates/admin.html  +  inline JS                             │
│   • on mount: GET /api/admin/skip-domains → fill textarea        │
│   • on click Simpan: POST /api/admin/skip-domains → show msg     │
└──────────────────────┬───────────────────────────────────────────┘
                       │ JSON over HTTP, session cookie
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│                      app.py (Admin_API)                          │
│   GET  /api/admin/skip-domains   @login_required + is_admin      │
│   POST /api/admin/skip-domains   @login_required + is_admin      │
│   • POST: structural validate → _validate_skip_entries           │
│           → _save_skip_domains                                   │
└──────────────────────┬───────────────────────────────────────────┘
                       │ Python function call
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│                   imap_engine.py (helpers)                       │
│   _load_skip_domains()       — FileLock + read + first-run seed  │
│   _save_skip_domains(value)  — FileLock + tmp + fsync + replace  │
│   _normalize_skip_entries()  — pure: strip/lower/dedupe          │
│   _validate_skip_entries()   — pure: 9.1/9.2/9.3 short-circuit   │
└──────────────────────┬───────────────────────────────────────────┘
                       │ filelock + filesystem
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│                       skip_domains.json                          │
│   project root, JSON array of strings, indent=2, UTF-8           │
│   coordinated by skip_domains.json.lock (FileLock)               │
└──────────────────────────────────────────────────────────────────┘

       ▲
       │ same FileLock-coordinated read at job start
       │
┌──────┴────────────────────────────────────────────────────────────┐
│              ImapChecker (Engine, per-job instance)               │
│   __init__:  self.skip_domain_keywords = _load_skip_domains()     │
│              (with Timeout fallback to _DEFAULT_SKIP_SET)         │
│              ── BEFORE workers spawn in run() ──                  │
│   _worker:   if any(kw in domain for kw in self.skip_domain_keywords): │
│                  self._write_domain_skip(...)                     │
└───────────────────────────────────────────────────────────────────┘
```

### Public surface

| Interface | Defined in | Consumers | Stability |
|-----------|------------|-----------|-----------|
| `GET /api/admin/skip-domains` | `app.py` | Admin_UI, ad-hoc curl | New (this spec) |
| `POST /api/admin/skip-domains` | `app.py` | Admin_UI | New (this spec) |
| `_load_skip_domains()` | `imap_engine.py` | `app.py`, `ImapChecker.__init__` | Module-internal (underscore prefix), but imported by `app.py` |
| `_save_skip_domains(value)` | `imap_engine.py` | `app.py` POST handler | Module-internal, imported by `app.py` |
| `_normalize_skip_entries(raw)` | `imap_engine.py` | `_validate_skip_entries`, tests | Module-internal |
| `_validate_skip_entries(raw)` | `imap_engine.py` | `app.py` POST handler, tests | Module-internal |
| `ImapChecker.skip_domain_keywords` | `imap_engine.py` | `_worker` (read-only after `__init__`) | Internal attribute |

Catatan: helper underscore-prefixed di-import dari `app.py` mengikuti
pola yang sudah dipakai (`_atomic_update_master` adalah underscore tapi
`_update_imap_config` pada `ImapChecker` memanggilnya). Tes akan
mengakses-nya langsung untuk unit + property tests.

## Data Models

### On-disk: `skip_domains.json`

Format: JSON array of strings, UTF-8, `indent=2`, trailing newline
opsional (apa pun yang `json.dump(..., indent=2)` hasilkan). Contoh
isi setelah first-run seed:

```json
[
  "hotmail",
  "live",
  "msn",
  "outlook",
  "yahoo",
  "interia",
  "poczta.fm"
]
```

Setiap elemen adalah `Skip_Domain_Entry` ternormalisasi: stripped,
lowercased, non-empty, tidak mengandung whitespace, panjang 1–255 char.

Invariants on-disk:

- `isinstance(json.load(f), list)` selalu True untuk file yang valid.
- `all(isinstance(x, str) for x in json.load(f))` selalu True.
- Bila file ditulis oleh fitur ini (bukan diedit manual), maka
  `json.load(f) == _normalize_skip_entries(json.load(f))` — yaitu
  idempoten terhadap normalisasi.
- File yang ditulis manual dan melanggar invariant di atas akan
  dianggap korup oleh `_load_skip_domains` dan menyebabkan
  `self.skip_domain_keywords = []` untuk job tersebut (Requirement 2.3,
  5.4).

### In-memory (per `ImapChecker` instance)

Atribut baru: `self.skip_domain_keywords: list[str]`.

- Diisi sekali di `__init__`.
- Tidak ada lock instance untuk atribut ini — read-only setelah
  `__init__` selesai. Worker thread membaca-nya secara concurrent
  tanpa lock (Python GIL + immutability cukup karena tidak ada writer
  setelah konstruktor).
- Iterasi `for kw in self.skip_domain_keywords` di hot path
  `_worker` aman karena list tidak pernah dimutasi.

### HTTP request/response shapes

**`GET /api/admin/skip-domains`**

| Status | Body |
|--------|------|
| 200 | `{"domains": ["hotmail", "live", ...]}` |
| 401 | `{"error": "Unauthorized"}` (via `login_required`) |
| 403 | `{"error": "Admin only"}` |
| 503 | `{"error": "Skip-domains file is busy, please retry"}` (FileLock timeout) |

**`POST /api/admin/skip-domains`**

Request body: `{"domains": ["hotmail", "  Yahoo ", "outlook"]}` (mentah,
sebelum strip/lower).

| Status | Body | Trigger |
|--------|------|---------|
| 200 | `{"success": true, "domains": ["hotmail", "yahoo", "outlook"]}` | Semua valid; `domains` adalah hasil normalisasi yang tersimpan |
| 400 | `{"error": "Invalid payload"}` | Body bukan JSON / `domains` bukan list / ada elemen non-string |
| 400 | `{"error": "Invalid entries", "rejected": ["", "ya hoo", "x"*300]}` | Ada entry yang melanggar 9.1/9.2/9.3 |
| 401 | `{"error": "Unauthorized"}` | Belum login |
| 403 | `{"error": "Admin only"}` | Login tapi bukan admin |
| 503 | `{"error": "Skip-domains file is busy, please retry"}` | FileLock timeout |

## Sequence Diagrams

### Diagram 1: Admin saves a new list (happy path)

```
Browser            Flask (app.py)        imap_engine.py        Filesystem
   │                    │                       │                   │
   │ POST /api/admin/   │                       │                   │
   │ skip-domains       │                       │                   │
   │ {"domains":[...]}  │                       │                   │
   │───────────────────>│                       │                   │
   │                    │ check session         │                   │
   │                    │ is_admin == True      │                   │
   │                    │                       │                   │
   │                    │ structural check:     │                   │
   │                    │ dict → list → all str │                   │
   │                    │                       │                   │
   │                    │ _validate_skip_entries(raw)               │
   │                    │──────────────────────>│                   │
   │                    │  (valid_norm, [])     │                   │
   │                    │<──────────────────────│                   │
   │                    │                       │                   │
   │                    │ _save_skip_domains(valid_norm)            │
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
   │ true, "domains":   │                       │                   │
   │ [...]}             │                       │                   │
   │<───────────────────│                       │                   │
```

### Diagram 2: Job starts during admin save (FileLock serialization)

Tujuan: menunjukkan bahwa `_save_skip_domains` (Admin_API) dan
`_load_skip_domains` (ImapChecker.__init__) tidak pernah race — keduanya
acquire lock yang sama. Job yang start ditengah save akan menunggu
hingga save selesai, lalu memuat snapshot baru. Job yang sudah
berjalan (worker thread sudah spawn) tidak terpengaruh.

```
Time   Admin_API              ImapChecker(job_A)        ImapChecker(job_B)        skip_domains.json.lock
 │      (running)              (workers running with     (NEW init starting)
 │                              snapshot V1)
 │
 │  POST /api/admin/                                                              [unlocked]
 │  skip-domains
 │      │
 │      │ FileLock.acquire ───────────────────────────────────────────────────→  [LOCKED by API]
 │      │
 │      │ tmp write + fsync                       __init__ starts
 │      │                                              │
 │      │                                              │ _load_skip_domains()
 │      │                                              │ FileLock.acquire ────→  [BLOCKED, waiting]
 │      │ os.replace V1→V2                             │ (waiting...)
 │      │                                              │
 │      │ FileLock.release ──────────────────────────────────────────────────→  [unlocked]
 │      │                                              │
 │  200 OK                                             │ FileLock.acquire ────→  [LOCKED by job_B]
 │                                                    │
 │      (job_A still running                          │ read V2 → return ["...", "gmail"]
 │       with snapshot V1 —                           │
 │       Requirement 5.3,                             │ FileLock.release ────→  [unlocked]
 │       non-retroactive)                             │
 │                                                    │ self.skip_domain_keywords = V2
 │                                                    │
 │                                                    │ run() spawns workers (use V2)
```

Catatan: FileLock di sini adalah `filelock.FileLock` (cross-process,
OS-level). Jika `__init__` dipanggil di proses worker WSGI yang berbeda
dari Admin_API, serialization tetap berlaku karena lock berdasarkan
file handle OS, bukan in-process state.

### Diagram 3: First-run seed (cold start)

```
Time   ImapChecker.__init__         imap_engine.py         Filesystem
 │      (first job after deploy)
 │
 │  _load_skip_domains()
 │      │
 │      │ FileLock.acquire ───────────────────────────────────────────→  [LOCKED]
 │      │
 │      │ os.path.exists(SKIP_DOMAINS_PATH) → False
 │      │
 │      │ inner _atomic_write(_DEFAULT_SKIP_SET):
 │      │   open("skip_domains.json.tmp", "w") ───────────────────────→  [tmp created]
 │      │   json.dump(default, f, indent=2) ──────────────────────────→  [tmp filled]
 │      │   f.flush(); os.fsync(f.fileno())  ──────────────────────────→  [fsynced]
 │      │   close (with __exit__)            ──────────────────────────→  [tmp closed]
 │      │   os.replace(tmp, SKIP_DOMAINS_PATH) ────────────────────────→  [skip_domains.json exists with seed]
 │      │
 │      │ FileLock.release ───────────────────────────────────────────→  [unlocked]
 │      │
 │      │ return list(_DEFAULT_SKIP_SET)
 │      ▼
 │  self.skip_domain_keywords = ["hotmail", "live", ..., "poczta.fm"]
 │
 │  ... run() spawns workers ...
 │
 │  worker evaluates: any("hotmail" in domain for kw in self.skip_domain_keywords)
 │  → byte-identik dengan perilaku pre-feature (Requirement 12.1, 12.3)
```

## Correctness Properties

*A property is a characteristic or behavior that should hold true
across all valid executions of a system — essentially, a formal
statement about what the system should do. Properties serve as the
bridge between human-readable specifications and machine-verifiable
correctness guarantees.*

Tujuh properties di bawah ini adalah hasil prework + reflection yang
mengonsolidasikan 14 acceptance criteria yang testable-as-property.
Acceptance criteria yang lain ditangani via EXAMPLE / EDGE_CASE /
INTEGRATION / SMOKE tests (lihat Testing Strategy).

### Property 1: Normalization correctness and idempotency

*For any* `raw: list[str]`,
`_normalize_skip_entries(raw)` SHALL satisfy all of:

1. Every output element `e` equals `s.strip().lower()` for some `s` in `raw`,
2. No output element is the empty string,
3. No output element is duplicated,
4. The relative order of output elements equals the order of first
   occurrence in `raw` of inputs whose `s.strip().lower()` is non-empty,
5. `_normalize_skip_entries(_normalize_skip_entries(raw)) == _normalize_skip_entries(raw)`
   (idempotency).

**Validates: Requirements 4.1, 4.2**

### Property 2: On-disk round trip and byte-format equivalence

*For any* `value: list[str]` where each element is non-empty, contains
no whitespace (per `re.search(r"\s", e) is None`), and has length ≤ 255,
the following SHALL hold after `_save_skip_domains(value)`:

1. `_load_skip_domains() == value`,
2. The raw bytes of `skip_domains.json` equal
   `json.dumps(value, indent=2).encode("utf-8")` (byte-identical with the
   format `_atomic_update_master` produces for an equivalent payload),
3. A subsequent `GET /api/admin/skip-domains` (as admin) returns
   `200 {"domains": value}`.

**Validates: Requirements 1.2, 3.5, 7.2**

### Property 3: POST normalize-and-persist with full replace

*For any* pre-existing on-disk content `O: list[str]` (valid normalized)
and *for any* raw payload `R: list[str]` such that
`_validate_skip_entries(R)` returns `(_, [])` (no rejections), after
`POST /api/admin/skip-domains` with body `{"domains": R}`:

1. The HTTP response is `200` with body
   `{"success": true, "domains": _normalize_skip_entries(R)}`,
2. `_load_skip_domains() == _normalize_skip_entries(R)` (the file
   content equals the normalized payload),
3. Any element of `O` that does not appear in
   `_normalize_skip_entries(R)` is absent from the post-write file
   (full-replace / PUT-like semantics).

**Validates: Requirements 8.2, 8.3**

### Property 4: Existing file preserved on read

*For any* on-disk content `V: list[str]` that satisfies the disk
invariants (well-formed JSON list of strings), `_load_skip_domains()`
SHALL return a value `R` such that `R == V` AND the on-disk bytes after
the call are byte-identical to the on-disk bytes before the call (the
read path SHALL NOT rewrite the file when it is already valid).

**Validates: Requirements 2.2**

### Property 5: Validation rejection semantics

*For any* raw payload `R: list[str]` where at least one element `e`
satisfies `e.strip() == ""` OR `len(e.strip()) > 255` OR
`re.search(r"\s", e.strip()) is not None`, the following SHALL hold:

1. `_validate_skip_entries(R)` returns `([], rejected)` where `rejected`
   is the sublist of `R` containing exactly those elements that
   violate any of the three rules, in first-occurrence order, with
   duplicates preserved (entries appear in `rejected` as-sent, before
   any `strip`/`lower`),
2. `POST /api/admin/skip-domains` with body `{"domains": R}` returns
   `400` with body `{"error": "Invalid entries", "rejected": rejected}`
   where `rejected` matches the list from (1),
3. `_load_skip_domains()` after the failed POST returns the same value
   as `_load_skip_domains()` before the POST (all-or-nothing: file
   unchanged).

**Validates: Requirements 9.2, 9.3, 9.4, 9.5**

### Property 6: Skip predicate equivalence

*For any* `domain: str` and *for any* `keyword_list: list[str]` where
every element is lowercase, non-empty, and contains no whitespace, the
skip decision in `_worker`
(`any(kw in domain for kw in self.skip_domain_keywords)`) SHALL equal
`any(kw in domain.lower() for kw in keyword_list)` — i.e., substring
containment over the lowercased domain.

**Validates: Requirements 6.1, 6.2**
(6.2 is the special case `keyword_list == []` ⇒ `any(...) == False`.)

### Property 7: Master imap_success.json untouched

*For any* `value: list[str]` (valid for `_save_skip_domains`), calling
`_save_skip_domains(value)` SHALL leave `imap_success.json` and its
lock file (`imap_success.json.lock`) byte-identical and with unchanged
modification time. Symmetrically, `_load_skip_domains()` SHALL NOT
modify `imap_success.json`.

**Validates: Requirements 13.1, 13.2**

## Error Handling

| Failure | Detected by | Response | State left behind |
|---|---|---|---|
| `skip_domains.json` missing | `os.path.exists` False inside `_load_skip_domains` | First-run seed: write `_DEFAULT_SKIP_SET` via tmp + fsync + `os.replace`, return seed. | File present with default content. |
| `skip_domains.json` corrupt (invalid JSON, or list with non-string elements, or non-list root) | `json.load` raises, OR `isinstance(data, list)` False, OR `all(isinstance(x, str) for x in data)` False | Return `[]` from `_load_skip_domains`. `ImapChecker.__init__` sets `self.skip_domain_keywords = []`. `_worker` skip predicate evaluates `False` for every account. Admin_API GET returns `{"domains": []}`. | File unchanged (NOT auto-rewritten — Requirement 2.3, 5.4). Admin can fix via POST. |
| `FileLock` timeout in `_load_skip_domains` (called from `ImapChecker.__init__`) | `filelock.Timeout` raised | `__init__` catches, logs to stderr, sets `self.skip_domain_keywords = list(_DEFAULT_SKIP_SET)`. **No rewrite to disk** (this fallback is in-memory only — Requirement 3.4(b)). Constructor returns normally; worker threads start with safe default. | File unchanged. |
| `FileLock` timeout in `_load_skip_domains` (called from `GET /api/admin/skip-domains`) | `filelock.Timeout` raised | Handler catches, returns `503 {"error": "Skip-domains file is busy, please retry"}` (Requirement 3.4(a)). | File unchanged. |
| `FileLock` timeout in `_save_skip_domains` (called from `POST`) | `filelock.Timeout` raised | Handler catches, returns `503 {"error": "Skip-domains file is busy, please retry"}` (Requirement 3.4(a)). | File unchanged. |
| Per-entry validation failure (empty after strip / >255 chars / contains whitespace) | `_validate_skip_entries` returns non-empty `rejected` list | `400 {"error": "Invalid entries", "rejected": [...]}`. **Short-circuit**: only the first violated rule per entry is evaluated. **All-or-nothing**: file is NOT written even if some entries are valid. | File unchanged (Requirement 9.5). |
| Structural payload error (non-JSON body, missing `domains` key, `domains` not a list, list with non-string element) | `request.get_json(silent=True)` returns `None`, OR type checks fail | `400 {"error": "Invalid payload"}` (Requirement 8.4). | File unchanged. |
| Disk full / permission denied during `_save_skip_domains` write or `os.replace` | `OSError` raised in `_save_skip_domains` | `OSError` propagates from helper. POST handler does NOT catch this; Flask returns `500` (default error handler). The tmp file may remain on disk; subsequent `_load_skip_domains` ignores it because reads only consult `SKIP_DOMAINS_PATH` (Requirement 3.3). | `SKIP_DOMAINS_PATH` retains old valid content if `os.replace` did not run. Stale `.tmp` may exist — harmless. |
| Crash mid-write (process killed between tmp open and `os.replace`) | (External — observable on next start) | Next `_load_skip_domains` reads `SKIP_DOMAINS_PATH` and returns the OLD valid content. The dangling `.tmp` is ignored (`_load_skip_domains` only reads `SKIP_DOMAINS_PATH`). On the next successful `_save_skip_domains`, the tmp file is overwritten in place. | Old content preserved. |
| Non-admin authenticated user calls API | `session.get("is_admin")` False | `403 {"error": "Admin only"}` for both GET and POST (Requirement 7.4, 8.6). | No state change. |
| Unauthenticated user calls API | `session.get("authenticated")` False (in `login_required`) | `401 {"error": "Unauthorized"}` (existing decorator behavior for `/api/...` paths, Requirement 7.3, 8.5). | No state change. |

## Migration / Backward Compatibility

**No data migration is required.**

Path:
- On deploy, `skip_domains.json` does not exist.
- The first call to `_load_skip_domains` (whether from
  `ImapChecker.__init__` for the first job, or from
  `GET /api/admin/skip-domains` if admin opens the panel first) seeds
  the file with `_DEFAULT_SKIP_SET`.
- Until that first call, no file exists — also no harm, because
  `_load_skip_domains` is the only code path that touches the file.
- After the seed, `_DEFAULT_SKIP_SET` content matches the previous
  `DOMAINS_TO_SKIP_KEYWORDS` set, so worker classification (live /
  noemail / die / unreg / domain_skipped) is byte-identical with the
  pre-feature behavior (Requirement 12.1, 12.3, validated by Property 2
  + cross-suite integration test).

**Existing files untouched:**

- `imap_success.json` (master IMAP config, owned by spec
  `centralize-imap-success-master`) — Property 7 guards this.
- `access_codes.json` — never read or written by this feature.
- `jobs/<job_id>/...` — never read or written by this feature.

**Deletion / rollback:**

To roll back: delete `skip_domains.json` and `skip_domains.json.lock`
from project root, redeploy previous version. No state in any other
file refers to skip-domains data, so the rollback is clean.

To "reset to default" without rolling back: admin deletes
`skip_domains.json` from disk; next `_load_skip_domains` re-seeds.

## Testing Strategy

Test layout (pytest, in workspace root):

```
tests/custom-skip-domains/
  test_constants.py             — SMOKE
  test_normalize.py             — Property 1 (Hypothesis)
  test_load_save_round_trip.py  — Property 2, 4 (Hypothesis)
  test_post_persist.py          — Property 3 (Hypothesis + Flask test_client)
  test_validate_reject.py       — Property 5 (Hypothesis)
  test_skip_predicate.py        — Property 6 (Hypothesis)
  test_master_untouched.py      — Property 7 (Hypothesis)
  test_first_run_seed.py        — EXAMPLE (2.1, 12.3)
  test_corrupt_file.py          — EDGE_CASE (2.3, 5.4)
  test_atomic_write_sequence.py — EXAMPLE (3.1)
  test_filelock_serialization.py— EXAMPLE (3.2)
  test_crash_mid_write.py       — EXAMPLE (3.3)
  test_filelock_timeout.py      — EXAMPLE (3.4)
  test_init_before_workers.py   — EXAMPLE (5.1)
  test_snapshot_semantics.py    — EXAMPLE (5.3, 12.2)
  test_no_module_constant.py    — SMOKE (5.2)
  test_endpoint_auth.py         — EXAMPLE (7.1, 7.3, 7.4, 8.1, 8.5, 8.6, 10.4)
  test_invalid_payload.py       — EDGE_CASE (8.4, 9.6)
  test_validation_short_circuit.py — EXAMPLE (9.1)
  test_admin_template.py        — EXAMPLE (10.1, 10.2, 10.3)
  test_no_new_routes.py         — SMOKE (11.2)
  test_classification_byte_identical.py — INTEGRATION (6.3, 12.1)
  test_separate_lock.py         — SMOKE (13.3)
  test_no_new_dependency.py     — SMOKE (14.3)
  test_existing_suites.py       — INTEGRATION wrapper (14.1, 14.2)
```

### Unit tests (example-based)

Library: `pytest` (already in repo per `centralize-imap-success-master`).

Cover EXAMPLE and SMOKE classifications:
- Constants resolve correctly (1.1, 1.3).
- First-run seed writes `_DEFAULT_SKIP_SET` in exact order (2.1, 12.3).
- Corrupt file scenarios return `[]` without rewrite (2.3, 5.4).
- Atomic write sequence: mock `os.fsync` and `os.replace`, assert call
  order tmp-open → write → flush → fsync → close → replace (3.1).
- FileLock timeout paths: mock `FileLock.acquire` to raise `Timeout`,
  assert API → 503, constructor → fallback (3.4).
- Endpoint auth gates: 401 / 403 / 200 paths (7.1, 8.1).
- Source code invariants: no `DOMAINS_TO_SKIP_KEYWORDS` in
  `imap_engine.py`, `_worker` body references `self.skip_domain_keywords`
  (5.2). `requirements.txt` unchanged (14.3). Lock paths differ (13.3).
  No removed routes in `app.url_map` (11.2).

### Property-based tests (Hypothesis)

Library: `hypothesis` (already pinned by `centralize-imap-success-master`
test suite — see `requirements.txt`; this spec adds zero new
dependencies, Requirement 14.3).

Each property test is configured with `@settings(max_examples=100)` (or
higher where cheap) and tagged with a comment of the form:

```python
# Feature: custom-skip-domains, Property 1: Normalization correctness and idempotency
```

The 7 property tests map 1:1 to Properties 1–7 above:

| Property | Generators | Assertion sketch |
|---|---|---|
| 1 | `lists(text(min_size=0, max_size=300))` (allows whitespace, casing, duplicates) | `_normalize` output has no empty / no dups / order preserved / idempotent |
| 2 | `lists(valid_entry_strategy)` where `valid_entry_strategy = text(min_size=1, max_size=255).filter(no_whitespace)` | After `_save` → `_load == value` AND raw bytes == `json.dumps(value, indent=2).encode("utf-8")` AND GET as admin returns `{"domains": value}` |
| 3 | tuple of (`lists(valid_entry_strategy)` as `O`, `lists(text())` filtered to `_validate(...)[1] == []` as `R`) | After POST(R), file == `_normalize(R)` and response.domains == `_normalize(R)` regardless of `O` |
| 4 | `lists(valid_entry_strategy)` | Pre-write file with V; `_load` returns V; file bytes/mtime unchanged after `_load` |
| 5 | `lists(text())` with at least one entry violating empty / >255 / `\s` | `_validate` returns `([], rejected_subset)`; POST returns 400 with rejected list; file unchanged |
| 6 | `text()` for `domain`; `lists(valid_entry_strategy)` for `keyword_list` | Skip decision == `any(kw in domain.lower() for kw in keyword_list)` |
| 7 | `lists(valid_entry_strategy)` | Snapshot `imap_success.json` content + mtime before; call `_save_skip_domains`; assert content + mtime unchanged |

Hypothesis strategies for whitespace-violating entries use a pool that
includes ASCII whitespace, NBSP (`U+00A0`), ideographic space
(`U+3000`), and zero-width space candidates that match `\s` — to verify
Requirement 9.3's "Unicode whitespace" claim.

### Integration tests

#### Flask test_client tests for the two new endpoints

Construct a Flask `test_client` with a fixture that:
1. Sets up an isolated `SKIP_DOMAINS_PATH` via `monkeypatch` to point
   inside `tmp_path` (so tests don't pollute project root).
2. Provides three session contexts: unauthenticated, authenticated
   non-admin, authenticated admin.

Tests exercise:
- GET as admin returns 200 + `{"domains": [...]}` (Property 2 + 7.2).
- POST as admin with valid normalized payload writes file, returns 200.
- POST as admin with rejection-triggering payload returns 400 +
  rejected list, file unchanged (Property 5).
- POST as admin with structural-bad payload returns 400 "Invalid
  payload" (8.4, 9.6).
- GET / POST as non-admin / unauthenticated → 403 / 401 respectively
  (7.3, 7.4, 8.5, 8.6).

#### ImapChecker end-to-end with mocked `mail_conn`

Cover Requirement 6.3 / 12.1 (byte-identical classification when
defaults unchanged):

1. Fixture: create a small accounts/sender/keyword set whose expected
   classification is hand-computed.
2. Mock `_try_imap_variants` and `imaplib.IMAP4_SSL` so all network
   I/O is replaced by deterministic in-memory responses.
3. Run `ImapChecker(...).run()` to completion (poll `status`).
4. Compute SHA-256 of `live.txt`, `noemail.txt`, `die.txt`,
   `unreg.txt`, `domain_skipped.txt`.
5. Compare against reference digests captured from a pre-feature run
   (committed as fixture file, ASCII-only, computed on the
   pre-feature `DOMAINS_TO_SKIP_KEYWORDS` path).

#### Cross-suite preservation (Requirements 11.1, 14.1, 14.2)

A wrapper test `test_existing_suites.py` runs the existing suites via
subprocess and asserts exit code 0:

```python
def test_centralize_master_suite_passes():
    result = subprocess.run(
        ["python", "-m", "pytest", "tests/centralize-imap-success-master/", "-q"],
        capture_output=True, text=True, cwd=PROJECT_ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr

def test_subject_keyword_match_suite_passes():
    result = subprocess.run(
        ["python", "-m", "pytest", "tests/subject-keyword-match-shown-in-live/", "-q"],
        capture_output=True, text=True, cwd=PROJECT_ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr
```

### Test execution

Test runner: `pytest`, single-execution mode (no `--watch`).

Recommended commands:

```bash
# Run only the new feature suite
python -m pytest tests/custom-skip-domains/ -q

# Run preservation suites referenced by Requirement 14
python -m pytest tests/centralize-imap-success-master/ tests/subject-keyword-match-shown-in-live/ -q

# Run everything
python -m pytest -q
```

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **Timeout-during-init blocks ImapChecker construction** if file lock is held by another process for >30s, causing job creation to fail. | Low (lock is held for milliseconds in normal operation) | High (job creation fails for end users) | Requirement 3.4(b): `__init__` catches `filelock.Timeout` and falls back to `_DEFAULT_SKIP_SET` in-memory rather than raising. Constructor always returns. Logged to stderr for ops visibility. |
| **Non-admin discovery via API**: a curious non-admin learns about the endpoint and probes it. | Medium (API path is discoverable) | Low (returns 403 with no data leak) | `@login_required` → 401 for unauthenticated; `session.get("is_admin")` check → 403 for non-admin. No information returned in body beyond `{"error": "Admin only"}`. Same hardening as `/api/admin/add-code` (`app.py:148`). |
| **Accidental wipe via empty POST**: admin clicks Save with empty textarea, all skip-domains gone, every account is checked (potentially against domains that always rate-limit). | Medium (UX hazard) | Medium (more accounts hit rate-limited servers; classification quality drops) | (a) Behavior is by design — empty list is valid (Requirement 6.2). (b) UI shows confirmation message with count after save: `"Tersimpan (0 entries)"` makes the wipe visible. (c) Default seed is recoverable: admin deletes the file from disk, next read re-seeds. (d) Future enhancement (out of scope): "Reset to default" button. |
| **FileLock contention with master `imap_success.json`** if both files are heavily written. | Low (separate lock paths — Requirement 13.3 + Property 7) | Low | Separate `FileLock` instances on separate paths (`skip_domains.json.lock` vs `imap_success.json.lock`). One lock cannot block the other. Verified by `test_separate_lock.py` (SMOKE) and Property 7. |
| **Encoding issues with Unicode whitespace detection**: `re.search(r"\s", ...)` pattern's coverage of Unicode whitespace depends on Python's regex flavor. Some "looks-like-space" code points (zero-width joiner, soft hyphen, BOM `U+FEFF`) are NOT in `\s` and would slip through. | Medium (admin pastes from a doc with weird chars) | Low (entry is allowed but won't match anything in real domains, so just dead weight in the list) | (a) Requirement 9.3 explicitly defines `\s` — design conforms. (b) Such entries pass validation but will fail to match any real domain (real-world domain names are LDH ASCII). (c) Future enhancement (out of scope): tighten validation to ASCII-only with Punycode for IDN domains. |
| **Stale `.tmp` file accumulates** if many writes crash mid-replace. | Very low | Negligible (file is overwritten on next successful write) | `_save_skip_domains` opens `tmp_path` in `"w"` mode (truncates), so stale content from a previous crash is replaced atomically on the next write. Requirement 3.3 explicitly disallows treating stale tmp as a source of truth on read. |
| **Hypothesis flakiness in concurrency tests**: Property 2/3/4/7 each touch real disk via `_save_skip_domains` / `_load_skip_domains`. Multiple test workers could race. | Low | Low (test failures, not production bugs) | Each Hypothesis test uses `tmp_path` (per-test isolation). FileLock path is also derived from the per-test tmp directory via `monkeypatch` of `SKIP_DOMAINS_PATH` and `SKIP_DOMAINS_LOCK_PATH`, so cross-test races cannot occur. |
| **Frontend XSS via skip-domain rendering**: malicious admin saves a value containing HTML; UI textarea displays it raw. | Very low (only admin can write; admin already has full control) | Negligible (textarea preserves plain text via `.value`, not `.innerHTML`) | The JS uses `skipTextarea.value = ...` which sets text content, not HTML. No `innerHTML` writes anywhere. Validation rejects whitespace, but does not reject HTML characters — by design (some real domains contain `-`, dots, etc.; HTML chars like `<` `>` would match no real domain anyway). |

## Final Requirement Coverage Validation

Each acceptance criterion from `requirements.md` maps to one or more
sections of this design and one or more tests. Every numbered
acceptance criterion (1.1 through 14.3) is covered.

| Req | Design coverage | Test coverage |
|---|---|---|
| 1.1 `SKIP_DOMAINS_PATH` resolves relative to `imap_engine.py` | Architecture → Module-level constants | SMOKE `test_constants.py` |
| 1.2 JSON array of strings, UTF-8, indent=2 | Architecture → `_save_skip_domains`; Data Models → On-disk | Property 2 |
| 1.3 `SKIP_DOMAINS_LOCK_PATH = SKIP_DOMAINS_PATH + ".lock"` | Architecture → Module-level constants | SMOKE `test_constants.py` |
| 2.1 First-run seed | Architecture → `_load_skip_domains`; Sequence Diagram 3 | EXAMPLE `test_first_run_seed.py` |
| 2.2 Existing file not overwritten | Architecture → `_load_skip_domains` | Property 4 |
| 2.3 Corrupt file → return `[]` without rewrite | Architecture → `_load_skip_domains`; Failure Handling | EDGE_CASE `test_corrupt_file.py` |
| 3.1 Atomic write sequence | Architecture → `_save_skip_domains` | EXAMPLE `test_atomic_write_sequence.py` |
| 3.2 FileLock wraps RMW | Architecture → both helpers; Sequence Diagram 2 | EXAMPLE `test_filelock_serialization.py` |
| 3.3 Crash mid-write preserves old state | Architecture → `_save_skip_domains`; Failure Handling | EXAMPLE `test_crash_mid_write.py` |
| 3.4 FileLock timeout → 503 / fallback | Architecture → `__init__`, endpoints; Failure Handling | EXAMPLE `test_filelock_timeout.py` |
| 3.5 Byte-format equivalence with `_atomic_update_master` | Architecture → `_save_skip_domains` | Property 2 (byte equality vs `json.dumps(value, indent=2).encode("utf-8")`) |
| 4.1 Normalization sequence | Architecture → `_normalize_skip_entries` | Property 1 |
| 4.2 Order preservation | (subsumed by 4.1) | Property 1 |
| 4.3 No additional transformation | Architecture → `_normalize_skip_entries` | EXAMPLE in `test_normalize.py` (specific cases) |
| 5.1 `__init__` reads BEFORE workers spawn | Architecture → `ImapChecker.__init__`; Sequence Diagram 2 | EXAMPLE `test_init_before_workers.py` |
| 5.2 Module constant removed; default privatized; `_worker` reads instance attr | Architecture → Removal of `DOMAINS_TO_SKIP_KEYWORDS`, `_worker` change | SMOKE `test_no_module_constant.py` |
| 5.3 Snapshot semantics non-retroactive | Architecture → `__init__` (atribut diisi sekali); Sequence Diagram 2 | EXAMPLE `test_snapshot_semantics.py` |
| 5.4 Corrupt file at init → empty list, no rewrite | Architecture → `__init__`; Failure Handling | EDGE_CASE `test_corrupt_file.py` (covers both `_load` and `__init__`) |
| 6.1 `_worker` predicate | Architecture → `_worker` change | Property 6 |
| 6.2 Empty list ⇒ no skip | (subsumed by 6.1) | Property 6 (special case `[]`) |
| 6.3 Byte-identical classification on defaults | Migration; Testing Strategy → ImapChecker end-to-end | INTEGRATION `test_classification_byte_identical.py` |
| 7.1 GET requires `@login_required` + `is_admin` | Architecture → `app.py` GET handler | EXAMPLE `test_endpoint_auth.py` |
| 7.2 GET returns `{"domains": [...]}` | Architecture → `app.py` GET handler | Property 2 |
| 7.3 GET 401 unauthenticated | Architecture → `login_required` reuse | EXAMPLE `test_endpoint_auth.py` |
| 7.4 GET 403 non-admin | Architecture → GET handler | EXAMPLE `test_endpoint_auth.py` |
| 8.1 POST requires `@login_required` + `is_admin` | Architecture → POST handler | EXAMPLE `test_endpoint_auth.py` |
| 8.2 POST normalize+persist+200 | Architecture → POST handler | Property 3 |
| 8.3 Full-replace semantics | Architecture → POST handler | Property 3 (varied pre-state) |
| 8.4 Invalid payload → 400 | Architecture → POST handler structural check | EDGE_CASE `test_invalid_payload.py` |
| 8.5 POST 401 | Architecture → `login_required` reuse | EXAMPLE `test_endpoint_auth.py` |
| 8.6 POST 403 | Architecture → POST handler | EXAMPLE `test_endpoint_auth.py` |
| 9.1 Validation order short-circuit | Architecture → `_validate_skip_entries` | EXAMPLE `test_validation_short_circuit.py` |
| 9.2 Reject >255 chars | Architecture → `_validate_skip_entries` | Property 5 |
| 9.3 Reject internal whitespace (incl. Unicode) | Architecture → `_validate_skip_entries` | Property 5 (with Unicode whitespace strategy) |
| 9.4 Rejection format | Architecture → POST handler | Property 5 |
| 9.5 All-or-nothing | Architecture → POST handler | Property 5 |
| 9.6 Non-string element → Invalid payload | Architecture → POST handler structural check | EDGE_CASE `test_invalid_payload.py` |
| 10.1 New section in template | Architecture → `templates/admin.html` | EXAMPLE `test_admin_template.py` |
| 10.2 Template loads via GET | Architecture → admin.html JS | EXAMPLE `test_admin_template.py` |
| 10.3 Save POSTs to endpoint | Architecture → admin.html JS | EXAMPLE `test_admin_template.py` |
| 10.4 Non-admin doesn't see section | Architecture → relies on `/admin` redirect at `app.py:140` | EXAMPLE `test_endpoint_auth.py` (302 for non-admin) |
| 10.5 No new auth layer | Architecture → reuse existing `login_required` + `is_admin` | SMOKE `test_no_module_constant.py` (extended check) |
| 11.1 No regression on existing endpoints | Architecture → only two new routes | INTEGRATION `test_existing_suites.py` |
| 11.2 Only two new routes | Architecture → `app.py` changes | SMOKE `test_no_new_routes.py` |
| 12.1 Byte-identical on defaults | Migration; Testing Strategy | INTEGRATION `test_classification_byte_identical.py` |
| 12.2 Edited list affects new jobs only | Architecture → snapshot at init; Sequence Diagram 2 | EXAMPLE `test_snapshot_semantics.py` |
| 12.3 First-run seed order | Architecture → `_DEFAULT_SKIP_SET` definition | EXAMPLE `test_first_run_seed.py` |
| 13.1 No write to `imap_success.json` | Architecture → separate file paths | Property 7 |
| 13.2 No schema change to `imap_success.json` | Architecture → separate file paths | Property 7 |
| 13.3 Separate lock files | Architecture → Module-level constants | SMOKE `test_separate_lock.py` |
| 14.1 `centralize-imap-success-master` suite passes | Migration → no overlap with master file | INTEGRATION `test_existing_suites.py` |
| 14.2 `subject-keyword-match-shown-in-live` suite passes | Migration → no changes to subject matching code path | INTEGRATION `test_existing_suites.py` |
| 14.3 No new dependencies | Reuses `filelock` from previous spec | SMOKE `test_no_new_dependency.py` |

All 14 requirements (54 acceptance criteria total) are mapped to design
sections and test plan entries.

