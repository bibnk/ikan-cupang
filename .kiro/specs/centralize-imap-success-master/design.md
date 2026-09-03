# Bugfix Design Document

## Overview

Bug ini terdiri dari tiga cacat yang saling berkaitan di `imap_engine.py`:

1. `imap_success.json` ditulis per-job di `jobs/{job_id}/` sehingga konfigurasi IMAP yang dipelajari tidak terakumulasi ke master di project root.
2. Penulisan master tidak atomik dan tidak dilindungi cross-process lock — rentan corrupt saat crash dan rentan lost-update saat dijalankan dengan multi-worker WSGI.
3. Argumen IMAP `SEARCH` (`FROM`, `SUBJECT`) tidak di-escape sesuai RFC 3501 — bila user memasukkan `"` atau `\`, command malformed dan akun salah diklasifikasikan sebagai `die`.

Strategi perbaikan tunggal-koheren:
- Definisikan path master sebagai konstanta module-level di `imap_engine.py`, lalu arahkan semua baca/tulis `imap_success.json` ke path tersebut (tidak ada lagi `imap_success.json` per-job).
- Bungkus seluruh siklus read-modify-write master ke dalam helper `_atomic_update_master(updater_fn)` yang memegang `filelock.FileLock` (cross-process), membaca master, memanggil callback untuk memodifikasi dict, lalu menulis ke `imap_success.json.tmp` dan memanggil `os.replace` untuk rename atomik.
- Tambahkan helper `_escape_imap_string(s)` yang mengganti `\` → `\\` lalu `"` → `\"` sebelum string disisipkan ke quoted-string IMAP `SEARCH`.
- Tambahkan dependency `filelock>=3.0` ke `requirements.txt`.

File hasil per-job lain (`live.txt`, `die.txt`, `noemail.txt`, `unreg.txt`, `domain_skipped.txt`) dan kontrak API Flask tidak berubah (memenuhi 3.1, 3.6).

## Glossary

- **Bug_Condition (C)**: Operasi LOAD/UPDATE `imap_success.json` yang menarget path per-job (bukan master), atau UPDATE yang tidak atomik / tidak dilindungi cross-process lock, atau IMAP `SEARCH` yang argumennya berisi `"`/`\` tanpa di-escape RFC 3501.
- **Property (P)**: Setelah fix, semua LOAD/UPDATE diarahkan ke `MASTER_IMAP_SUCCESS_PATH` di project root, UPDATE selalu via `tmp + os.replace` di dalam `FileLock`, dan argumen `SEARCH` selalu di-escape.
- **Preservation**: Format JSON master (`indent=2`, UTF-8, dict `{domain: {server, port, ssl?}}`), fallback `return {}` saat parse error, kontrak API Flask, file per-job lain, substring match `DOMAINS_TO_SKIP_KEYWORDS`, urutan lookup config domain — semuanya tidak berubah.
- **MASTER_IMAP_SUCCESS_PATH**: Konstanta module-level di `imap_engine.py` yang mengevaluasi ke `os.path.join(os.path.dirname(os.path.abspath(__file__)), "imap_success.json")` — file master tunggal di project root.
- **MASTER_IMAP_SUCCESS_LOCK_PATH**: `MASTER_IMAP_SUCCESS_PATH + ".lock"` — file sentinel yang dipakai `filelock.FileLock` untuk koordinasi antar proses.
- **`_atomic_update_master(updater_fn)`**: Helper module-level yang menjalankan siklus `acquire FileLock → load JSON → updater_fn(dict) → write tmp → os.replace → release FileLock` dan mengembalikan boolean apakah master benar-benar berubah.
- **`_escape_imap_string(s)`**: Helper yang melakukan `s.replace("\\", "\\\\").replace('"', '\\"')` sesuai RFC 3501 §4.3.

## Bug Details

### Bug Condition

Bug muncul pada tiga jalur operasi yang berbeda namun terkait. Lihat `bugfix.md` §"Bug Condition (Pseudocode)" sebagai sumber kebenaran formal — ringkasan:

**Formal Specification:**

```
FUNCTION isBugCondition(input)
  INPUT: input = (operation, target_path, write_mode, process_lock,
                  search_args)
         operation IN {LOAD_IMAP_CONFIG, UPDATE_IMAP_CONFIG, IMAP_SEARCH}
  OUTPUT: boolean

  IF operation IN {LOAD_IMAP_CONFIG, UPDATE_IMAP_CONFIG} THEN
    IF target_path != MASTER_IMAP_SUCCESS_PATH THEN
      RETURN true                              // (a) per-job path bug
    END IF
    IF operation = UPDATE_IMAP_CONFIG THEN
      IF write_mode != ATOMIC_TMP_RENAME THEN
        RETURN true                            // (b) non-atomic write
      END IF
      IF process_lock != FILE_LOCK THEN
        RETURN true                            // (c) no cross-proc lock
      END IF
    END IF
  END IF

  IF operation = IMAP_SEARCH THEN
    FOR EACH s IN search_args DO
      IF (contains(s, '"') OR contains(s, '\\'))
         AND NOT properly_escaped_for_imap(s) THEN
        RETURN true                            // (d) unescaped SEARCH arg
      END IF
    END FOR
  END IF

  RETURN false
END FUNCTION
```

### Examples

- **Per-job path** — Job baru dengan domain `some-new-isp.com` → `_update_imap_config` menulis ke `jobs/{job_id}/imap_success.json`, master di project root tidak berubah, dan ketika `cleanup_expired_jobs` menghapus folder job, entri tersebut hilang.
- **Non-atomic write** — Master berisi 200+ entri (~50 KB). `_update_imap_config` membuka file dengan mode `'w'` (truncate ke 0 byte), proses di-kill saat 30% data sudah ditulis. File master sekarang berisi JSON terpotong dan tidak valid; load berikutnya fallback ke `{}` → 200+ entri hilang permanen.
- **Lost update** — `gunicorn -w 4`. Worker A dan Worker B sama-sama load master 199 entri, masing-masing menambah domain berbeda, lalu menulis bergiliran. Tulis terakhir menimpa tulis pertama → satu domain hilang.
- **Edge case escaping** — Keyword `say "hi"` membentuk `(SINCE "01-Jan-2024" SUBJECT "say "hi"")` yang melanggar RFC 3501. Server merespons `BAD`/`NO`, exception ditangkap, akun yang sebenarnya valid masuk ke `die.txt`.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**

- File hasil per-job lain (`live.txt`, `noemail.txt`, `die.txt`, `unreg.txt`, `domain_skipped.txt`) tetap ditulis di `jobs/{job_id}/` (3.1).
- Urutan lookup konfigurasi domain tetap: `DEFAULT_IMAP_CONFIG` → cached `imap_success_config` → wildcard `*.rr.com` → `_try_imap_variants` (3.2).
- `_update_imap_config` tetap idempotent: bila domain sudah ada dengan nilai sama, skip rename (3.3).
- Format JSON master tetap dictionary `{ "<domain>": { "server", "port", "ssl"? } }`, `indent=2`, encoding UTF-8 (3.4, 3.11).
- Master existing di project root dipakai apa adanya; entri lama tidak dihapus saat merge (3.5).
- Kontrak API Flask (`/api/check`, `/api/stop/<job_id>`, `/api/status/<job_id>`, `/api/download/<job_id>/<file_type>`, `/api/jobs`, `/api/jobs/<job_id>/extend`, `/api/jobs/<job_id>/delete`, `/api/jobs/<job_id>/live`) sama persis (3.6).
- Multi-thread dalam satu proses tetap aman dari race condition (3.7).
- `_merge_imap.py` dan `_populate_imap.py` tetap kompatibel (3.8).
- Substring match `DOMAINS_TO_SKIP_KEYWORDS` tidak diubah.
- Untuk argumen `SEARCH` tanpa `"` atau `\`, kriteria yang dikirim ke server identik dengan sebelum fix (escape no-op) (3.9, 3.10).
- File `.tmp` tidak menumpuk; lock file boleh persisten antar run (3.11, 3.13).
- Tidak ada deadlock atau latensi material di kasus normal (3.12).

**Scope:**

Semua input yang TIDAK berhubungan dengan baca/tulis `imap_success.json` master, dan TIDAK berisi karakter `"`/`\` di argumen `SEARCH`, harus berperilaku identik dengan kode lama. Termasuk:
- Penulisan `live.txt` / `die.txt` / `noemail.txt` / `unreg.txt` / `domain_skipped.txt`.
- Koneksi IMAP, login, fetch, parsing email, regex OTP, decoding MIME.
- Endpoint Flask, autentikasi, cleanup expired jobs.
- Lookup `DEFAULT_IMAP_CONFIG` dan auto-discovery `_try_imap_variants`.

## Hypothesized Root Cause

Berdasarkan pembacaan langsung `imap_engine.py`:

1. **Per-job results_dir di __init__**: Baris `self.imap_output_file = os.path.join(self.results_dir, "imap_success.json")` mengikat output ke folder job. Akibatnya `_load_imap_success_config()` dan `_update_imap_config()` keduanya membaca/menulis ke file yang berbeda untuk setiap job. Root cause: scope path yang salah.
2. **Direct overwrite tanpa atomik**: `_update_imap_config` memakai `with open(self.imap_output_file, 'w', encoding='utf-8') as f: json.dump(current, f, indent=2)`. Mode `'w'` melakukan truncate di awal, sehingga setiap interrupt setelah open dan sebelum json.dump selesai meninggalkan file dengan isi parsial atau kosong.
3. **`threading.Lock` tidak menjangkau lintas proses**: `self.imap_config_lock` hanya melindungi thread di proses Python yang sama. Multi-worker WSGI memunculkan banyak proses; lock tersebut tidak menyerialisasi mereka.
4. **f-string interpolation langsung untuk IMAP SEARCH**: `criteria = f'(SINCE "{...}" FROM "{sender}")'` dan analog untuk `SUBJECT`. Tidak ada escaping `\` dan `"`. Per RFC 3501 §4.3 quoted string, kedua karakter tersebut wajib di-escape dengan backslash.

## Correctness Properties

Property 1: Bug Condition — Master File Centralization, Atomicity, Cross-Process Safety, dan IMAP SEARCH Escaping

_For any_ input where the bug condition holds (`isBugCondition` returns true), the fixed code SHALL:
(a) untuk operasi LOAD/UPDATE, menggunakan path tunggal `MASTER_IMAP_SUCCESS_PATH` di project root;
(b) untuk operasi UPDATE, menulis ke `MASTER_IMAP_SUCCESS_PATH + ".tmp"` lalu `os.replace` ke target, sehingga file master tidak pernah dalam state truncated/partial;
(c) untuk operasi UPDATE, memegang `filelock.FileLock(MASTER_IMAP_SUCCESS_LOCK_PATH)` selama keseluruhan siklus read-modify-write, dengan `threading.Lock` (`self.imap_config_lock`) sebagai layer pelindung in-process di luarnya;
(d) untuk operasi IMAP_SEARCH, meng-escape `\` → `\\` dan `"` → `\"` (urutan tepat) pada setiap argumen yang masuk ke quoted string `FROM "..."` / `SUBJECT "..."`;
(e) merge entri baru ke isi master tanpa menghapus entri lain.

**Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8**

Property 2: Preservation — Behavior Equivalence Outside Bug Condition

_For any_ input where the bug condition does NOT hold (`isBugCondition` returns false), the fixed code SHALL produce exactly the same observable result as the original code, preserving:
- format JSON master (key/value/indent=2/UTF-8) saat tidak ada crash;
- urutan lookup config domain (`DEFAULT_IMAP_CONFIG` → cached → wildcard `*.rr.com` → auto-discovery);
- idempotency `_update_imap_config` (skip rename bila tidak ada perubahan);
- penulisan file per-job lain ke `jobs/{job_id}/`;
- kontrak endpoint Flask;
- kriteria IMAP `SEARCH` untuk argumen tanpa `"` / `\` (escape no-op);
- fallback `{}` ketika master tidak ada / parse error.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.11, 3.12, 3.13**

## Architecture

### Module-Level Constants (`imap_engine.py`)

```python
import os
from filelock import FileLock, Timeout

MASTER_IMAP_SUCCESS_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "imap_success.json",
)
MASTER_IMAP_SUCCESS_LOCK_PATH = MASTER_IMAP_SUCCESS_PATH + ".lock"
MASTER_LOCK_TIMEOUT_SECONDS = 30
```

Dengan `os.path.dirname(os.path.abspath(__file__))`, path tetap menunjuk ke direktori `imap_engine.py` (project root) terlepas dari `cwd` saat gunicorn dijalankan (memenuhi 2.1, 2.3, 2.5).

### Atomic Update Helper

Helper module-level (bukan method) supaya bisa direuse oleh utilitas / test, tapi state-nya masih aman karena lock-nya tied ke filesystem path:

```python
def _atomic_update_master(updater_fn):
    """
    Read master JSON, call updater_fn(dict_in_place) which returns True
    if it modified the dict, then write atomically (tmp + os.replace) only
    if changed. Protected by cross-process FileLock + (caller's threading.Lock).

    Returns True if master changed on disk, False otherwise.
    Raises filelock.Timeout if lock cannot be acquired in MASTER_LOCK_TIMEOUT_SECONDS.
    """
    lock = FileLock(MASTER_IMAP_SUCCESS_LOCK_PATH, timeout=MASTER_LOCK_TIMEOUT_SECONDS)
    with lock:
        # 1. Load current master (preserves existing fallback-to-empty behavior)
        current = {}
        if os.path.exists(MASTER_IMAP_SUCCESS_PATH):
            try:
                with open(MASTER_IMAP_SUCCESS_PATH, "r", encoding="utf-8") as f:
                    current = json.load(f)
            except Exception:
                current = {}

        # 2. Mutate in place; updater returns True iff something changed
        changed = updater_fn(current)
        if not changed:
            return False  # idempotent skip (3.3)

        # 3. Write to tmp in the same directory
        tmp_path = MASTER_IMAP_SUCCESS_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=2)
            f.flush()
            os.fsync(f.fileno())  # durability, defensif

        # 4. Atomic rename (POSIX & Windows: os.replace overwrites target)
        os.replace(tmp_path, MASTER_IMAP_SUCCESS_PATH)
        return True
```

Catatan:
- `FileLock` library lintas-platform — di Windows menggunakan `msvcrt.locking`, di POSIX `fcntl.flock`. Lock file (`imap_success.json.lock`) boleh tertinggal di disk antar run; library mengelola lock release berdasarkan kepemilikan file handle, bukan keberadaan file (memenuhi 3.13).
- `os.replace` di Python ≥ 3.3 dijamin atomik bila source dan destination berada di filesystem yang sama; file `.tmp` ditempatkan di direktori master untuk memastikan ini.
- `os.fsync` opsional tapi defensif terhadap kehilangan daya. Aman karena master file kecil (<100 KB).

### Perubahan `ImapChecker`

```python
class ImapChecker:
    def __init__(self, job_id, results_dir, accounts, ...):
        ...
        # PER-JOB FILES (unchanged, memenuhi 3.1)
        self.live_file        = os.path.join(self.results_dir, "live.txt")
        self.noemail_file     = os.path.join(self.results_dir, "noemail.txt")
        self.die_file         = os.path.join(self.results_dir, "die.txt")
        self.unreg_file       = os.path.join(self.results_dir, "unreg.txt")
        self.domain_skip_file = os.path.join(self.results_dir, "domain_skipped.txt")

        # MASTER FILE: hapus self.imap_output_file per-job
        # self.imap_output_file = os.path.join(self.results_dir, "imap_success.json")  # DELETED

        ...
        self.imap_config_lock = threading.Lock()
        self.imap_success_config = self._load_imap_success_config()

    def _load_imap_success_config(self):
        # Memenuhi 2.3, 3.5; preservation: fallback {} jika tidak ada / parse error
        if os.path.exists(MASTER_IMAP_SUCCESS_PATH):
            try:
                with open(MASTER_IMAP_SUCCESS_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _update_imap_config(self, domain, config_data):
        # Memenuhi 2.2, 2.4, 2.6, 2.7, 3.3, 3.7
        with self.imap_config_lock:                          # in-process serializer
            def updater(current):
                if current.get(domain) == config_data:
                    return False                             # idempotent (3.3)
                current[domain] = config_data
                return True

            try:
                changed = _atomic_update_master(updater)     # cross-proc + atomic
            except Timeout:
                # Failure mode: log, skip update for this cycle, jangan crash worker
                # (lihat Failure Handling section)
                return
            except Exception:
                # os.replace gagal (mis. permission Windows): jangan re-raise
                return

            if changed:
                # Refresh in-memory cache supaya thread lain dalam proses yang sama
                # melihat entri baru tanpa harus hit disk lagi
                self.imap_success_config[domain] = config_data
```

### IMAP SEARCH Escape Helper

```python
def _escape_imap_string(s: str) -> str:
    """RFC 3501 §4.3 quoted string escape: '\\' first, then '"'."""
    return s.replace("\\", "\\\\").replace('"', '\\"')
```

Apply di `_worker` (memenuhi 2.8, 3.9, 3.10):

```python
for sender in self.target_senders:
    criteria = f'(SINCE "{self.search_since_date}" FROM "{_escape_imap_string(sender)}")'
    status, msgs = mail_conn.search(None, criteria)
    ...

for kw in self.keywords:
    criteria = f'(SINCE "{self.search_since_date}" SUBJECT "{_escape_imap_string(kw)}")'
    status, msgs = mail_conn.search(None, criteria)
    ...
```

`self.search_since_date` aman dari escaping karena format `%d-%b-%Y` (mis. `01-Jan-2024`) tidak mengandung `"`/`\`. Untuk argumen tanpa karakter spesial, fungsi escape adalah no-op sehingga kriteria identik dengan sebelum fix (3.9, 3.10).

## Design Decisions

| Keputusan | Alasan |
|-----------|--------|
| Konstanta module-level untuk path master | Setiap instance `ImapChecker` (dan utilitas test) berbagi satu sumber kebenaran. Mudah di-monkeypatch di test. |
| Helper `_atomic_update_master` mengambil callback `updater_fn` | Memisahkan kebijakan locking + atomicity dari logika merge. Bisa direuse untuk operasi lain (mis. delete entry) tanpa duplikasi. |
| `threading.Lock` di luar `FileLock` | `threading.Lock` lebih ringan untuk kasus umum (single proses, banyak thread). `FileLock` hanya menjadi tambahan saat butuh lintas-proses. Urutan ini juga mencegah dua thread di proses sama saling menghantam acquisition file lock. |
| Idempotent skip via `updater` return value | Hindari `os.replace` yang tidak perlu (3.3) — mengurangi disk I/O dan tidak mengubah mtime file. |
| `os.replace` (bukan `os.rename`) | `os.rename` di Windows gagal bila destination ada. `os.replace` (Python ≥ 3.3) dijamin overwrite atomik di kedua platform. |
| Lock file persisten | Sederhana, selaras dengan `filelock` library; lock release ditentukan oleh handle, bukan keberadaan file (3.13). |
| Catch `Timeout` dan `Exception` di `_update_imap_config` (tidak re-raise) | IMAP check tetap berjalan walau master sementara tidak bisa di-update. Domain berikutnya akan auto-discovery ulang. |
| Tidak migrasi `jobs/*/imap_success.json` lama | Job-job tersebut akan expired dan terhapus alamiah. Migrasi opsional via skrip terpisah (out of scope). |
| `fsync` setelah write tmp | Defensif terhadap power loss antara `write` dan `os.replace`. Biaya kecil (<1ms untuk file <100KB). |

## Sequence Diagram

### Skenario: Worker A (proses 1) dan Worker B (proses 2) update domain berbeda

```
Time   Worker A (proc 1, thread T_A)         Worker B (proc 2, thread T_B)
────   ─────────────────────────────         ─────────────────────────────
T0     acquire self.imap_config_lock
T1     FileLock.acquire("...lock")
T2     load master  → 199 entries
T3     updater(current) → adds "A.com"
                                               acquire self.imap_config_lock (proc 2)
                                               FileLock.acquire("...lock")  [BLOCKED]
T4     write tmp (200 entries)
T5     os.replace(tmp, master)  ← ATOMIC
T6     FileLock.release()
T7     release self.imap_config_lock
                                          T8   FileLock acquired
                                          T9   load master  → 200 entries (incl "A.com")
                                          T10  updater(current) → adds "B.com"
                                          T11  write tmp (201 entries)
                                          T12  os.replace(tmp, master)  ← ATOMIC
                                          T13  FileLock.release()
                                          T14  release self.imap_config_lock

Final master: 201 entries — both "A.com" and "B.com" preserved (memenuhi 2.4, 2.7)
```

### Skenario: Crash tepat sebelum `os.replace`

```
T0   open master (read)            → current dict in memory
T1   updater mutates dict
T2   open tmp (write, truncate)
T3   json.dump → tmp file populated
T4   ── PROCESS KILLED ──
                                    master unchanged on disk (still 199 entries valid)
                                    tmp file leftover (will be overwritten next update)

Next run T5+:
T5   load master → 199 entries valid (no JSON corruption, memenuhi 2.6)
```

## Failure Handling

| Failure mode | Behavior | Reasoning |
|--------------|----------|-----------|
| `FileLock` timeout (>30 detik tidak bisa acquire) | Catch `filelock.Timeout`, log warning (mis. `"master imap_success.json lock timeout, skipping update for {domain}"`), skip update. Worker IMAP tidak crash. | IMAP check tidak boleh terhenti hanya karena master sibuk. Domain akan re-discover di job berikutnya — tidak optimal tapi aman. |
| Master file korup (JSON invalid) | `_load_imap_success_config` tetap `try/except → return {}` (tidak diubah). Update berikutnya akan menulis dict kosong + entri baru — secara teknis mereset master. | Mempertahankan perilaku lama (3.5 untuk normal case; korup adalah edge case yang sudah ada sebelum fix). Tidak introduce regresi. Bisa dipulihkan manual via `_merge_imap.py`. |
| `os.replace` gagal (PermissionError di Windows, antivirus locking, dll) | Catch `OSError`/`Exception`, log error, biarkan tmp file ada (akan di-overwrite di update berikutnya), JANGAN re-raise. | Worker IMAP harus terus jalan. Tmp file orphan tidak berbahaya — selalu di-overwrite (mode `'w'`) oleh update berikutnya. |
| Lock file leftover di disk | `filelock` mendeteksi lock hilang via handle, bukan file presence. Tidak butuh cleanup khusus. | Mengikuti default behavior library; selaras dengan 3.13. |
| Concurrent update untuk domain yang sama | `updater_fn` membandingkan `current.get(domain) == config_data`. Bila identik, skip rename. Bila berbeda, last-writer wins (kedua worker berhasil tulis valid JSON, hanya nilai akhir mungkin berbeda). | Acceptable: konfigurasi yang valid sama untuk satu domain (server, port, ssl) — tidak ada "kehilangan informasi" di sini. |
| Disk penuh saat menulis tmp | `open(tmp, 'w')` atau `json.dump` akan raise `OSError`. Kita catch di `_update_imap_config`, log, skip. Master file lama tetap valid. | Atomicity terjaga — `os.replace` hanya dipanggil bila write tmp sukses. |

Pseudocode handling di `_update_imap_config`:

```python
try:
    changed = _atomic_update_master(updater)
except Timeout:
    logger.warning("master lock timeout, skip update for %s", domain)
    return
except OSError as e:
    logger.error("master write failed: %s", e)
    return
```

(Logging eksplisit boleh disubstitusi `print` ke `stderr` mengikuti style `imap_engine.py` saat ini yang belum pakai logger formal.)

## Migration / Backward Compatibility

- **`jobs/{job_id}/imap_success.json` lama**: Tidak di-migrate. Job-job lama akan expired (`DEFAULT_EXPIRY_DAYS = 3`) dan folder-nya dihapus oleh `cleanup_expired_jobs`/`delete_job` yang sudah ada. Tidak ada perubahan kode dibutuhkan untuk path cleanup ini.
- **Master file existing di project root**: Dipakai apa adanya. Format JSON sudah sesuai (`{ "<domain>": { "server", "port", "ssl"? } }` indent=2 UTF-8) — tidak ada konversi (3.4, 3.5).
- **Skrip utilitas opsional (out of scope)**: Bila di kemudian hari diperlukan migrasi eksplisit, sebuah skrip `_migrate_jobs_imap.py` bisa membaca semua `jobs/*/imap_success.json` lalu memanggil `_atomic_update_master` untuk mem-merge ke master. Dimasukkan sebagai catatan, BUKAN bagian dari fix ini.
- **`_merge_imap.py` dan `_populate_imap.py`**: Tetap bekerja tanpa perubahan karena keduanya beroperasi di `imap_success.json` di project root (memenuhi 3.8). Tidak ada koordinasi dengan `FileLock` baru karena skrip ini biasanya dijalankan saat aplikasi tidak running; bila ingin presisi, bisa diadaptasi nanti — di luar scope.

## Fix Implementation

### Changes Required

**File**: `imap_engine.py`

1. **Import baru**: Tambah `from filelock import FileLock, Timeout` dan konstanta module-level `MASTER_IMAP_SUCCESS_PATH`, `MASTER_IMAP_SUCCESS_LOCK_PATH`, `MASTER_LOCK_TIMEOUT_SECONDS`.

2. **Helper module-level baru**: `_atomic_update_master(updater_fn)` (lihat snippet di Architecture).

3. **Helper module-level baru**: `_escape_imap_string(s)` (lihat snippet di Architecture).

4. **`ImapChecker.__init__`**: Hapus `self.imap_output_file = os.path.join(self.results_dir, "imap_success.json")`. Tetap pertahankan `self.imap_config_lock = threading.Lock()` dan pemanggilan `self._load_imap_success_config()`.

5. **`ImapChecker._load_imap_success_config`**: Ganti referensi `self.imap_output_file` → `MASTER_IMAP_SUCCESS_PATH`. Body tetap sama (try/except → `{}`).

6. **`ImapChecker._update_imap_config`**: Refactor untuk memakai `_atomic_update_master(updater)` di dalam `with self.imap_config_lock:`. Tangkap `Timeout` dan `OSError`, jangan re-raise. Update `self.imap_success_config[domain]` hanya bila `changed`.

7. **`ImapChecker._worker`**: Bungkus `sender` dan `kw` dengan `_escape_imap_string(...)` saat membangun `criteria`. Total dua call site.

**File**: `requirements.txt`

8. Tambah baris `filelock>=3.0`.

## Testing Strategy

### Validation Approach

Pendekatan dua fase: pertama verifikasi bug muncul pada kode unfixed dengan counterexample konkret, lalu verifikasi fix berhasil dan preservasi tidak rusak via property-based + unit + integration tests.

### Exploratory Bug Condition Checking

**Goal**: Surface counterexample pada kode UNFIXED untuk konfirmasi root cause analysis.

**Test Plan**: Tulis 4 test sederhana yang dijalankan terhadap `imap_engine.py` versi sekarang.

**Test Cases**:
1. **Per-job path** — Konstruksi `ImapChecker` dengan `results_dir=jobs/test_X`, panggil `_update_imap_config("foo.com", {...})`, assert master di project root TIDAK berubah dan `jobs/test_X/imap_success.json` ADA. (Akan PASS pada unfixed → membuktikan bug.)
2. **Crash mid-write** — Patch `json.dump` agar raise `KeyboardInterrupt` setelah menulis ~30% bytes, panggil `_update_imap_config`, baca master file → assert isinya truncated/invalid JSON. (Akan PASS pada unfixed → membuktikan non-atomicity.)
3. **Concurrent update lost** — Spawn 2 proses `multiprocessing` yang masing-masing memanggil `_update_imap_config` dengan domain berbeda di waktu yang sama. Assert master akhir HANYA berisi salah satu domain (lost update). (Akan kadang PASS pada unfixed — tergantung interleaving.)
4. **Unescaped SEARCH** — Mock `imaplib.IMAP4.search`, panggil `_worker` flow dengan keyword `say "hi"`, assert string `criteria` yang dikirim ke mock berisi `"say "hi""` (malformed). (Akan PASS pada unfixed → membuktikan absence of escaping.)

**Expected Counterexamples**:
- Master tidak berisi entri yang ditulis worker (per-job path bug).
- Master truncated/invalid setelah crash injection.
- Lost update di skenario multi-proses.
- Quoted-string IMAP malformed di test mock.

### Fix Checking

**Goal**: `FOR ALL X WHERE isBugCondition(X) DO ASSERT property holds on F'(X)`.

**Pseudocode:**

```
FOR ALL input WHERE isBugCondition(input) DO
  result := fixedFunction(input)
  // (a) path
  IF op IN {LOAD, UPDATE}: ASSERT result.path = MASTER_IMAP_SUCCESS_PATH
  // (b) atomic
  IF op = UPDATE: ASSERT trace contains tmp + os.replace, not direct open(master,'w')
  // (c) cross-proc lock
  IF op = UPDATE: ASSERT FileLock acquired around read+write
  // (d) escape
  IF op = SEARCH: ASSERT escaped string matches '\\'→'\\\\' then '"'→'\\"'
  // (e) merge
  IF op = UPDATE: ASSERT before-entries preserved AND new-entry added
END FOR
```

### Preservation Checking

**Goal**: `FOR ALL X WHERE NOT isBugCondition(X) DO ASSERT F(X) = F'(X)`.

**Pseudocode:**

```
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT originalFunction(input) = fixedFunction(input)
END FOR
```

**Testing Approach**: Property-based testing (Hypothesis) sangat sesuai karena:
- Mampu generate sequence operasi LOAD/UPDATE secara acak dan menjamin invariants di seluruh interleaving.
- Otomatis menemukan edge case (urutan key di JSON, nilai duplikat, domain dengan karakter unicode).
- Untuk preservasi, perbandingan F vs F' hanya butuh 2 implementasi paralel — Hypothesis menggenerate input yang membedakan.

**Test Plan**: Observasi behavior pada kode UNFIXED untuk operasi non-bug, lalu tulis test paralel di kode FIXED dengan input yang sama dan assert identik.

**Test Cases**:
1. **JSON format preservation** — Generate dict random `{domain: {server, port, ssl?}}`, pakai `_atomic_update_master` untuk menulis, baca raw, assert `indent=2`, UTF-8, key/value identik dengan ekspektasi `json.dumps(d, indent=2)`.
2. **Idempotent skip** — Panggil `_update_imap_config` dua kali dengan domain & config sama, assert call kedua tidak menyebabkan rename (mtime sama / `os.replace` tidak dipanggil).
3. **Per-job files unchanged** — Jalankan job kecil end-to-end (mock IMAP), assert `live.txt`/`die.txt`/`noemail.txt`/`unreg.txt`/`domain_skipped.txt` ditulis di `jobs/{job_id}/` dengan format identik.
4. **API contract unchanged** — Smoke test endpoint Flask `/api/check`, `/api/status`, `/api/jobs` — semua respons schema identik dengan baseline.
5. **SEARCH no-op for normal input** — Untuk input tanpa `"`/`\` (mis. `noreply@booking.com`, `verification`), assert `_escape_imap_string(x) == x`.
6. **Empty target_senders/keywords** — Assert tidak ada SEARCH tambahan yang dipanggil (alur `_worker` identik dengan unfixed).

### Unit Tests

- `_escape_imap_string` — table-driven: `""→""`, `'a'→'a'`, `'a"b'→'a\\"b'`, `'a\\b'→'a\\\\b'`, `'\\"'→'\\\\\\"'` (test urutan replace yang benar).
- `_atomic_update_master` — happy path: dict kosong → tambah entry, dict existing → merge, dict identik → skip.
- `_atomic_update_master` — error path: simulasi `OSError` saat tulis tmp, simulasi `Timeout` saat acquire lock.
- `_load_imap_success_config` — file tidak ada → `{}`, file korup → `{}`, file valid → dict.

### Property-Based Tests

- **Property: Fix Checking — atomicity invariant.** Generate sequence acak `[update(d_i, c_i)]` dengan Hypothesis, jalankan via `_atomic_update_master`. Antara setiap operasi, baca raw bytes file master dan assert: (1) parse-able JSON, (2) berisi semua `(d, c)` dari operasi yang sudah selesai. Tidak pernah ada state intermediate yang invalid.
- **Property: Fix Checking — escape correctness.** Generate string acak (termasuk `"`/`\`/unicode), assert `_escape_imap_string(s)` membentuk valid IMAP quoted string per RFC 3501 (parser kecil sebagai oracle, atau roundtrip melalui imaplib mock).
- **Property: Preservation — escape no-op for safe strings.** Generate string ASCII tanpa `"`/`\`, assert `_escape_imap_string(s) == s`.
- **Property: Preservation — merge equivalent to dict.update.** Untuk sequence operasi tanpa crash, hasil akhir master = `dict.update` chaining seluruh entri, sama persis dengan implementasi lama (3.11).

### Integration Tests

- **Crash injection test** — Jalankan `_atomic_update_master` di subprocess dengan `os.kill(pid, SIGKILL)` di-trigger pada beberapa titik (sebelum write tmp, di tengah json.dump, antara write tmp dan os.replace, setelah os.replace). Setelah subprocess mati, parent assert master file tetap valid JSON (atau berisi update final bila kill setelah replace).
- **Concurrent multi-process test** — Spawn N=8 proses `multiprocessing` masing-masing menambah 50 domain unik via `_atomic_update_master`. Setelah join semua, assert master berisi tepat 400 domain.
- **End-to-end Flask test** — Start app, kirim job kecil dengan akun mock IMAP, verifikasi: master ter-update di project root, `jobs/{job_id}/` berisi 5 file txt yang biasa, tidak ada `imap_success.json` di `jobs/{job_id}/`.
- **Cross-platform sanity (CI matrix)** — Jalankan test suite di Linux dan Windows untuk memastikan `filelock` dan `os.replace` bekerja di kedua platform.

## Risks

| Risiko | Mitigasi | Trade-off |
|--------|----------|-----------|
| `FileLock` menambah latency per update | Lock hanya dipegang sekitar 1-5 ms (read+merge+write file <100KB). `_update_imap_config` jarang dipanggil (hanya saat domain baru/berubah). Idempotent skip mengurangi frekuensi rename. | Acceptable. |
| Tambahan dependency `filelock` | Library mature, dependensi tunggal pure-Python (no C ext), license BSD. | Diterima — alternatif `fcntl` murni POSIX-only, tidak portable ke Windows. |
| Disk I/O bertambah (tmp file + fsync) | File master kecil (<100KB), update jarang. fsync ~ms order. | Acceptable. |
| Lock file persisten meningkatkan visual clutter | Beri nama eksplisit (`imap_success.json.lock`) supaya jelas peran-nya. Bisa di-`.gitignore`. | Diterima. |
| Migrasi tidak otomatis untuk `jobs/*/imap_success.json` lama | Job lama expired alamiah (3 hari). Dokumentasikan opsi skrip migrasi opsional di README. | Acceptable — minimalisasi scope perubahan. |
| Cross-platform `os.replace` di Windows kadang gagal jika file di-lock antivirus | Catch `OSError`, skip update, tidak crash worker. Master file lama tetap valid. Operasi akan retry secara natural di update berikutnya. | Acceptable, well-documented. |
| Master corrupt (sudah ada sebelum fix) | Fallback `{}` mempertahankan perilaku lama; pulih manual via `_merge_imap.py`. | Tidak introduce regresi (3.5 untuk normal case). |

---

**Validasi requirement coverage**: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8 — fix; 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.11, 3.12, 3.13 — preservation. Setiap acceptance criterion telah dipetakan ke section design di atas.
