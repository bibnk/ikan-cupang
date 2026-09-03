# Bugfix Design Document

## Overview

Bug ini terjadi di `ImapChecker._worker` (`imap_engine.py`, around line 518). Setelah per-account IMAP `SEARCH` membangun `email_ids` sebagai union dari hasil FROM dan SUBJECT search, post-fetch loop di sekitar line 618–635 memfilter setiap email yang sudah di-fetch dengan satu-satunya gate:

```python
if sender_matches(from_addr, self.target_senders):
    emails_data.append({...})
```

Karena `sender_matches(from_addr, [])` selalu `False` (loopnya tidak pernah dijalankan saat `target_senders` kosong), seluruh hasil keyword-only dibuang setelah fetch dan akun salah diklasifikasikan sebagai `noemail`. Pada regime "both filled", filter ini juga membuang setiap email yang masuk ke `email_ids` solely melalui SUBJECT branch — bertentangan dengan union semantics yang sudah dibangun di SEARCH stage.

Strategi fix tunggal-koheren (surgical):
- Catat UID hasil per-keyword SUBJECT search ke set lokal `subject_matched_ids` di samping `email_ids.update(...)` yang sudah ada.
- Ganti gate post-fetch dari satu `if sender_matches(...)` menjadi tiga-cabang OR: bypass jika tidak ada sender constraint, OR sender match, OR UID berasal dari SUBJECT branch.
- Tidak menyentuh: `sender_matches`, `OTP_SUBJECT_REGEX`, `_write_live`, master `imap_success.json`, atomic update helper, `FileLock`, `_escape_imap_string` — semua artefak `centralize-imap-success-master` tetap utuh.

Total perubahan: ~5 baris dalam satu method (`_worker`), tanpa API surface change, tanpa migrasi.

## Glossary

- **Bug_Condition (C)**: Email yang lolos `OTP_SUBJECT_REGEX` dibuang oleh post-fetch sender filter padahal seharusnya di-keep menurut union semantics `email_ids`. Detail formal di Bug Details.
- **Property (P)**: Setelah fix, post-fetch filter hanya dienforce bila user mensuplai sender constraint, dan diperluas ke union (`sender_match` OR `subject-branch member`). Semua email yang lolos OTP filter dan masuk via salah satu cabang `email_ids` di-keep.
- **Preservation**: Format `live.txt`/`noemail.txt`/`die.txt`/`unreg.txt`/`domain_skipped.txt`, OTP filter, set-union semantics `email_ids`, format kolom `<from_addr>` vs raw `From` di `_write_live`, dan seluruh kontrak master `imap_success.json` (file lock, atomic update, IMAP escape) — tidak berubah.
- **`_worker`** (`imap_engine.py`:518): Method per-thread yang menjalankan SEARCH + FETCH + klasifikasi untuk satu akun IMAP. Tempat tunggal yang disentuh fix ini.
- **`sender_matches`** (`imap_engine.py`:148): Helper modul yang mengembalikan `False` saat `target_senders` kosong (`for target in target_senders:` tidak pernah dijalankan). Tidak diubah.
- **`OTP_SUBJECT_REGEX`** (`imap_engine.py`:27): Compiled regex `^Booking\.com – \w+ is your verification code$`. Outermost gate, tidak diubah.
- **`email_ids`** (set lokal di `_worker`, baris 600): Set UID hasil union FROM+SUBJECT SEARCH. Diisi via `email_ids.update(msgs[0].split())` — elemen bertipe `bytes` (mis. `b'42'`).
- **`subject_matched_ids`** (set lokal BARU di `_worker`): Subset UID yang spesifik berasal dari SUBJECT SEARCH branch, bertipe `bytes` agar konsisten dengan `email_ids` dan `eid_bytes`.
- **`eid_bytes`** (variabel loop di `_worker`, baris 618): UID per-email dalam tipe `bytes`, hasil `sorted_ids` yang turunan langsung dari `email_ids`.

## Bug Details

### Bug Condition

Bug muncul saat sebuah email yang sudah di-fetch lolos `OTP_SUBJECT_REGEX` tetapi dibuang oleh post-fetch `if sender_matches(from_addr, self.target_senders):` di `imap_engine.py`:630, padahal seharusnya tetap di-keep menurut union semantics `email_ids`.

**Formal Specification** (sumber kebenaran: `bugfix.md` §"Bug Condition"; ringkasan):

```
TYPE FetchedEmailInput =
  RECORD
    target_senders : List of String
    keywords       : List of String
    subject        : String          // decoded Subject header
    from_addr      : String          // parseaddr(decoded From)[1]
    matched_via    : Set of {FROM, SUBJECT}
  END

FUNCTION isBugCondition(X)
  INPUT:  X of type FetchedEmailInput
  OUTPUT: boolean

  // OTP gate stays outermost (Requirement 3.2).
  IF OTP_SUBJECT_REGEX.match(X.subject.strip()) THEN
    RETURN false
  END IF

  // Case A: keyword-only — bypass dropped everything.
  IF X.target_senders is empty
     AND X.keywords is non-empty
     AND SUBJECT in X.matched_via THEN
    RETURN true
  END IF

  // Case B: both filled, subject-only match — union semantics ignored.
  IF X.target_senders is non-empty
     AND X.keywords is non-empty
     AND SUBJECT in X.matched_via
     AND NOT sender_matches(X.from_addr, X.target_senders) THEN
    RETURN true
  END IF

  RETURN false
END FUNCTION
```

### Examples

- **Case A (worked example)** — `target_senders = []`, `keywords = ["Booking confirmation"]`, akun `alice@example.com:hunter2`. Inbox berisi satu email E1 dari `reservations@hotelchain.com` dengan subject `Booking confirmation #12345 — see you in Paris`.
  - SEARCH: FROM loop tidak dijalankan; SUBJECT loop menambahkan UID E1 ke `email_ids` via `email_ids.update(msgs[0].split())` (line 611).
  - FETCH: E1 di-fetch; `OTP_SUBJECT_REGEX.match("Booking confirmation #12345 ...")` → False (lolos gate, line 627).
  - Bug: line 630 mengevaluasi `sender_matches("reservations@hotelchain.com", [])` → False → E1 tidak masuk `emails_data`.
  - Akibat: `emails_data` kosong → branch `else` di line 637–638 → `_write_noemail(...)` + `_update_progress("noemail")`. Akun salah-klasifikasi.

- **Case B (worked example)** — `target_senders = ["@booking.com"]`, `keywords = ["invoice"]`. Inbox berisi E1 dari `billing@stripe.com` dengan subject `Your invoice #998`.
  - SEARCH: FROM loop tidak menemukan UID E1 (bukan dari `@booking.com`); SUBJECT loop menemukan E1 (`SUBJECT "invoice"`) → `email_ids` berisi UID E1.
  - FETCH: lolos OTP.
  - Bug: line 630 → `sender_matches("billing@stripe.com", ["@booking.com"])` → False → E1 dibuang.
  - Akibat: emails legit yang masuk via SUBJECT branch dibuang meski union `email_ids` jelas berisi UID-nya. Bertentangan dengan kontrak union yang sudah dibangun di line 600–611.

- **OTP-filtered (must NOT change)** — `target_senders = []`, `keywords = ["Booking.com"]`. Email E2 dari `noreply@booking.com` dengan subject `Booking.com – ABC123 is your verification code`.
  - OTP regex match → drop di line 627. Fix tidak menyentuh path ini.

- **Sender-only (must NOT change)** — `target_senders = ["@booking.com"]`, `keywords = []`. SUBJECT loop tidak dijalankan; `subject_matched_ids` kosong. Gate fixed `(not self.target_senders) or sender_matches(...) or eid_bytes in subject_matched_ids` runtuh menjadi `sender_matches(...)` — identik dengan kode lama.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- `OTP_SUBJECT_REGEX` tetap outermost gate; semua email yang match-nya tetap dibuang sebelum logic baru dievaluasi (Requirement 3.2).
- Sender-only callers (`target_senders` non-empty, `keywords` empty) menghasilkan byte-identical `live.txt`/`noemail.txt`/`die.txt`/`unreg.txt`/`domain_skipped.txt` (Requirement 3.1).
- Set-union semantics `email_ids` lewat dua loop `email_ids.update(msgs[0].split())` di line 605 dan 611 tidak diubah (Requirement 3.4).
- `_write_live` tetap merender `<from_addr>` saat `sender_matches(from_addr, self.target_senders)` True dan raw `From` header bila tidak (line 385–387, Requirement 3.5). Konsekuensi natural: untuk Case A — di mana `target_senders = []` — `sender_matches(_, [])` selalu False sehingga branch highlight di-skip dan kolom From dirender raw, sesuai Requirement 2.1.1.
- Both-empty regime (`target_senders=[]` AND `keywords=[]`): kedua loop SEARCH skip, `email_ids` kosong, `sorted_ids` kosong, `if sorted_ids:` False → fall-through ke `_write_noemail` + `_update_progress("noemail")` (Requirement 3.3).
- Master `imap_success.json` flow (`_atomic_update_master`, `FileLock`, `_escape_imap_string`) tidak disentuh (Requirement 3.6) — fix berada di method yang berbeda dan tidak menyentuh helper modul-level mana pun.
- HTTP endpoint contracts (`/api/check`, `/api/status`, `/api/jobs`, ...) tidak berubah (Requirement 3.7).
- File names dan on-disk format per-job tidak berubah (Requirement 3.8).

**Scope:**

Semua input di mana `isBugCondition` mengembalikan `false` harus berperilaku byte-identik dengan kode lama. Termasuk:
- Sender-only checks (Case di mana `target_senders` non-empty dan `keywords` kosong).
- OTP-filtered emails (di-drop di line 627 sebelum logic post-fetch baru).
- Both-empty (zero SEARCH, fall-through ke noemail).
- Email yang `from_addr`-nya match `sender_matches(...)` di mana pun di `_worker` (jalur F dan F' menyetujui keep).

## Hypothesized Root Cause

Berdasarkan pembacaan langsung `imap_engine.py`:

1. **Gate post-fetch yang tunggal & tidak konsisten dengan SEARCH-stage** (root cause utama). Line 630 menulis:

   ```python
   if sender_matches(from_addr, self.target_senders):
       emails_data.append({...})
   ```

   Ini hanya menerima emails yang lolos sender-only criterion. Tidak ada cabang yang mengakui SUBJECT branch dari `email_ids` — meskipun SEARCH stage di line 607–611 dengan jelas menambahkan UID hasil per-keyword `SEARCH SUBJECT` ke `email_ids`. Kontradiksi inilah bug-nya.

2. **Empty-list semantics dari `sender_matches`** (root cause amplifier). `sender_matches` (line 148) memiliki:

   ```python
   for target in target_senders:
       ...
       return True
   return False
   ```

   Saat `target_senders = []`, loopnya zero-iteration, fungsi langsung jatuh ke `return False`. Helper ini secara desain tidak boleh "auto-pass" (akan break sender-only checks), jadi fix harus dilakukan di call-site (gate di `_worker`), bukan di helper.

3. **Tidak ada record per-account UID-yang-cocok-via-SUBJECT**. Saat ini SUBJECT match disatukan ke `email_ids` tanpa pemisahan source. Untuk membedakan "FROM-only", "SUBJECT-only", dan "FROM+SUBJECT" di stage post-fetch, kita perlu set tambahan yang spesifik mencatat SUBJECT branch.

Catatan: bukan DOM/timing/event-listener issue (template umum) — kode ini single-process Python imaplib, tidak ada UI binding. Hipotesis di atas dapat dikonfirmasi langsung dari kode.

## Correctness Properties

Property 1: Bug Condition — Keyword-Only and Subject-Branch Emails Are Kept

_For any_ input where the bug condition holds (`isBugCondition` returns true), the fixed `_worker` post-fetch filter SHALL include the email in `emails_data` exactly once, and (if such inclusion is the sole reason `emails_data` becomes non-empty for the account) the account SHALL be written to `live.txt` via `_write_live` with the `live` progress counter incremented and the `noemail` counter NOT incremented.

**Validates: Requirements 2.1, 2.1.1, 2.2, 2.3**

Property 2: Preservation — Behavior Equivalence Outside Bug Condition

_For any_ input where the bug condition does NOT hold (`isBugCondition` returns false), the fixed `_worker` SHALL produce exactly the same observable result as the original — preserving:
- byte-identical contents of `live.txt`, `noemail.txt`, `die.txt`, `unreg.txt`, `domain_skipped.txt` (sender-only and both-empty regimes);
- OTP-filter exclusion as the outermost gate;
- set-union semantics of `email_ids`;
- `<from_addr>` vs raw `From` rendering in `_write_live`;
- master `imap_success.json` write contract via `_atomic_update_master` + `FileLock` + `_escape_imap_string`;
- file names and on-disk per-job format;
- HTTP endpoint contracts.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8**

## Architecture

### Surgical Change to `_worker`

Tiga edit terlokalisir di method `ImapChecker._worker` (`imap_engine.py`:518–642). Semua di luar method ini tetap utuh.

**Edit 1 — Inisialisasi set baru, di samping `email_ids` (line ~600):**

```python
# BEFORE
mail_conn.select("INBOX")
email_ids = set()
# Search by sender
for sender in self.target_senders:
    ...
```

```python
# AFTER
mail_conn.select("INBOX")
email_ids = set()
subject_matched_ids = set()  # UIDs hasil SUBJECT SEARCH (bytes), untuk gate post-fetch
# Search by sender
for sender in self.target_senders:
    ...
```

**Edit 2 — Catat hasil SUBJECT branch ke `subject_matched_ids` (line ~607–611):**

```python
# BEFORE
for kw in self.keywords:
    criteria = f'(SINCE "{self.search_since_date}" SUBJECT "{_escape_imap_string(kw)}")'
    status, msgs = mail_conn.search(None, criteria)
    if status == "OK" and msgs and msgs[0]:
        email_ids.update(msgs[0].split())
```

```python
# AFTER
for kw in self.keywords:
    criteria = f'(SINCE "{self.search_since_date}" SUBJECT "{_escape_imap_string(kw)}")'
    status, msgs = mail_conn.search(None, criteria)
    if status == "OK" and msgs and msgs[0]:
        ids = msgs[0].split()
        email_ids.update(ids)
        subject_matched_ids.update(ids)
```

`msgs[0].split()` mengembalikan `list[bytes]`; `subject_matched_ids` karenanya berisi `bytes`, konsisten dengan `email_ids` dan dengan `eid_bytes` di loop berikutnya. Tidak ada konversi tipe.

**Edit 3 — Ganti single-arm gate dengan three-arm OR (line ~630):**

```python
# BEFORE (line 630)
if sender_matches(from_addr, self.target_senders):
    date_str = decode_mime_words(msg.get("Date", ""))
    emails_data.append({
        "subject": subject,
        "from": from_,
        "date": date_str
    })
```

```python
# AFTER
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

Tiga cabang OR (urutan dipilih berdasarkan biaya evaluasi naik):
- `not self.target_senders` — branch keyword-only / both-empty bypass, O(1) check pada list reference.
- `sender_matches(from_addr, self.target_senders)` — sender-only / both-filled FROM-match path, O(len(target_senders)).
- `eid_bytes in subject_matched_ids` — both-filled SUBJECT-only path, O(1) set membership pada `bytes`.

Karena Python `or` short-circuits, urutan ini juga memastikan kasus sender-only (di mana `target_senders` non-empty dan `subject_matched_ids` kosong) tidak pernah perlu memeriksa cabang ketiga — preservasi performa untuk regime existing.

### Why Not Modify `sender_matches`

Mengubah `sender_matches([], _)` agar return `True` saat list kosong terlihat menggoda tetapi merusak call-site lain di line 385 (`_write_live`): bila helper auto-pass saat empty, kolom From akan dirender `<from_addr>` untuk semua email keyword-only — melanggar Requirement 2.1.1 yang menetapkan raw `From` rendering. Fix di call-site (gate di `_worker`) menjaga helper tetap stabil dan rendering `_write_live` tetap natural.

## Design Decisions

| Keputusan | Alasan |
|-----------|--------|
| Bypass post-fetch sender filter saat `target_senders` empty | Mencerminkan kontrak union: bila tidak ada sender constraint, tidak ada filter sender. Konsisten dengan SEARCH stage yang juga zero-iterates `for sender in self.target_senders:` saat empty. |
| OR dengan `eid_bytes in subject_matched_ids` (bukan re-match `Subject` string) | Persis seperti `bugfix.md` 2.3 mengharuskan: keputusan inclusion via set membership, bukan re-parse subject. Murah (O(1) set lookup), reliable, dan tidak duplicate logic dari `mail_conn.search`. |
| `subject_matched_ids` menyimpan `bytes` (bukan decoded str) | `email_ids`, `sorted_ids`, dan `eid_bytes` semuanya `bytes`. Menjaga kesatuan tipe menghindari edge-case comparison `b'42' != '42'`. Tidak ada decode/encode trip. |
| Tidak mengubah `sender_matches` | Empty-list returning False adalah kontrak fungsi yang dipakai juga di `_write_live` line 385. Mengubahnya akan mengubah rendering `<from_addr>` (regression terhadap 2.1.1 dan 3.5). Fix di call-site menjaga single-responsibility. |
| Tidak mengubah `email_ids` set-union | Requirement 3.4 mengikat ini explicit. `subject_matched_ids` ditambahkan sebagai *paralel* bookkeeping, tidak menggantikan `email_ids`. Sorted/fetched IDs tetap berasal dari `email_ids`. |
| Urutan cabang OR: empty → sender_matches → set membership | Short-circuit Python `or`. Preservasi performa untuk sender-only (cabang 3 tidak dievaluasi). Untuk Case A, cabang 1 meng-eliminasi cabang 2 dan 3 — tidak ada panggilan `sender_matches` yang mubazir. |
| Lokal set, bukan instance attribute | Scope `subject_matched_ids` adalah satu invocation `_worker` per akun. Membuatnya `self.*` akan share state antar akun antar thread — cross-account leak. Lokal aman dan cocok dengan `email_ids`/`sorted_ids` yang juga lokal. |
| Tidak mengubah `_write_live` | Renderingnya sudah benar by accident: untuk Case A (`target_senders=[]`), `sender_matches(from_addr, [])` False → `fmt_from = raw_from`. Memenuhi 2.1.1 tanpa perubahan kode. |

## Sequence Diagram

### Regime Keyword-Only (Case A): `target_senders=[]`, `keywords=["foo"]`

```
                 _worker (per account)
                 ─────────────────────
SEARCH stage:
  for sender in []:           ← zero iterations (target_senders empty)
    (skipped)
  for kw in ["foo"]:
    SEARCH SUBJECT "foo"  →  msgs = [b"7 12 19"]
    email_ids        ← {b"7", b"12", b"19"}
    subject_matched_ids ← {b"7", b"12", b"19"}     ← NEW

FETCH+filter loop:
  sorted_ids = [b"19", b"12", b"7"]   (newest first, capped 20)
  for eid_bytes in sorted_ids:
    fetch + decode
    if OTP_SUBJECT_REGEX.match(...):  continue
    # Gate evaluation:
    not self.target_senders           → True (short-circuits OR to True)
    → emails_data.append(...)
  ...

if emails_data:
  _write_live(account, ..., emails_data)   ← live.txt updated
  _update_progress("live")
```

### Regime Both-Filled (Case B): `target_senders=["@x.com"]`, `keywords=["bar"]`

```
                 _worker (per account)
                 ─────────────────────
SEARCH stage:
  for sender in ["@x.com"]:
    SEARCH FROM "@x.com" → msgs = [b"3 11"]
    email_ids ← {b"3", b"11"}
  for kw in ["bar"]:
    SEARCH SUBJECT "bar" → msgs = [b"11 25"]
    email_ids        ← {b"3", b"11", b"25"}
    subject_matched_ids ← {b"11", b"25"}     ← NEW

FETCH+filter loop (per email):
  ─ b"3"  (FROM only):
      not self.target_senders            → False
      sender_matches(from_addr, ["@x.com"]) → True   ← keep (sender-only branch)
  ─ b"11" (FROM ∩ SUBJECT):
      not self.target_senders            → False
      sender_matches(from_addr, ["@x.com"]) → True   ← keep (short-circuit)
  ─ b"25" (SUBJECT only):
      not self.target_senders            → False
      sender_matches(from_addr, ["@x.com"]) → False
      b"25" in subject_matched_ids       → True      ← keep (NEW path)
```

Untuk regime sender-only (`target_senders` non-empty, `keywords` empty), `subject_matched_ids` tetap empty (loop SUBJECT zero-iterates), sehingga cabang ke-3 selalu False dan gate runtuh ke `sender_matches(...)` saja — identik dengan F.

## Failure Handling

| Failure mode | Behavior | Reasoning |
|--------------|----------|-----------|
| `mail_conn.search` mengembalikan `status != "OK"` untuk SUBJECT criterion | Skip update kedua set (kondisi `if status == "OK" and msgs and msgs[0]` tidak terpenuhi), persis seperti pre-fix. `subject_matched_ids` tetap apa-adanya. | Inherit pre-fix robustness; tidak introduce new failure mode. |
| `msgs[0]` empty/`None` | `if status == "OK" and msgs and msgs[0]` evaluasi False → tidak ada `update()` call → kedua set tidak berubah. | Sama dengan pre-fix; tidak ada perubahan kontrak. |
| `msgs[0].split()` menghasilkan bytes "aneh" (mis. UID dengan whitespace internal) | Disimpan apa adanya (sebagai bytes) di `email_ids` — sama persis dengan pre-fix. Konsekuensi: `int(x)` di `sorted_ids = sorted(..., key=lambda x: int(x))` di line 613 akan raise `ValueError` jika UID non-numeric → exception ditangkap oleh `try/except` di sekitar `_worker` line ~592 → akun masuk ke `die.txt`. | Behavior identik dengan pre-fix; bug ini tidak introduce penambahan failure path. |
| `eid_bytes` bukan element dari `subject_matched_ids` (FROM-only di regime both-filled) dan `from_addr` tidak match | Cabang 3 False, cabang 2 False, cabang 1 False (target_senders non-empty) → email di-drop dari `emails_data`. | Konsisten dengan SEARCH-stage union: UID tersebut datang dari FROM branch, tetapi `from_addr` aktual setelah parsing tidak match — biasanya hasil mismatch alias/domain. Pre-fix juga drop di sini, jadi tidak ada perubahan. |
| Stop event (`self.is_stopped`) selama loop | `break` di line 620 (existing). Tidak terpengaruh fix. | No-op untuk fix. |
| `OTP_SUBJECT_REGEX.match(subject.strip())` raise (regex tidak akan, tapi `.strip()` pada None aman karena `decode_mime_words` selalu return str) | Unchanged. | Outer `try/except` di `_worker` tetap menangkap. |

Tidak ada failure mode baru yang diintroduce. Semua exception path di `_worker` (existing `try` di line ~590, `except Exception:` di line ~643) tetap menangani: koneksi gagal → `die.txt`, parser error → `die.txt`.

## Migration / Backward Compatibility

- **Tidak ada migrasi disk-state.** Fix murni perubahan logic di memory; tidak ada file format change, tidak ada schema change.
- **Tidak ada API surface change.** `ImapChecker.__init__` signature, `run()`, `stop()`, `get_progress()` — semua identik.
- **Behavior change yang sengaja**: caller dengan `keywords` non-empty dan `target_senders` empty sekarang akan melihat akun-akun bermigrasi dari `noemail.txt` ke `live.txt` (untuk akun-akun yang memang punya keyword-matching email). Ini adalah inten fix; bukan regresi. Caller existing yang mengisi `target_senders` tidak akan melihat perubahan apapun.
- **Caller dengan kedua field terisi** akan melihat tambahan emails (yang sebelumnya hilang) muncul di `live.txt` rows-nya. Ini juga inten (Case B), konsisten dengan union semantics yang sudah dijanjikan SEARCH stage.
- **Centralize-imap-success-master fix tetap utuh.** Tidak ada line dari `_atomic_update_master`, `MASTER_IMAP_SUCCESS_PATH`, `FileLock`, atau `_escape_imap_string` yang disentuh.

## Fix Implementation

### Changes Required

**File**: `imap_engine.py`

**Method**: `ImapChecker._worker` (definisi pada line 518)

**Specific Changes**:

1. **Tambah set lokal `subject_matched_ids`** — Insert satu baris setelah `email_ids = set()` (line ~600):

   ```python
   subject_matched_ids = set()
   ```

   Scope: lokal pada satu invocation `_worker`, garbage-collected saat method return. Tipe element: `bytes` (konsisten dengan `email_ids`).

2. **Populate `subject_matched_ids` di SUBJECT loop** — Modifikasi blok SUBJECT search pada line 607–611:

   ```python
   for kw in self.keywords:
       criteria = f'(SINCE "{self.search_since_date}" SUBJECT "{_escape_imap_string(kw)}")'
       status, msgs = mail_conn.search(None, criteria)
       if status == "OK" and msgs and msgs[0]:
           ids = msgs[0].split()
           email_ids.update(ids)
           subject_matched_ids.update(ids)
   ```

   Hanya menambah dua baris (`ids = msgs[0].split()` dan `subject_matched_ids.update(ids)`) dan refactor `email_ids.update(msgs[0].split())` ke `email_ids.update(ids)`. Set-union semantics `email_ids` tidak berubah (Requirement 3.4).

   Catatan: FROM loop (line 601–605) **TIDAK** menyentuh `subject_matched_ids` — UID dari FROM branch tidak boleh tercatat sebagai SUBJECT match.

3. **Ganti gate post-fetch tunggal dengan three-arm OR** — Modifikasi line 630–636:

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

   Body di dalam `if` tidak berubah (sama dengan pre-fix line 632–636).

4. **Tidak ada perubahan lain.** `_write_live` (line 327+) tidak disentuh — rendering `<from_addr>` vs raw `From` di line 385–387 secara natural memenuhi Requirement 2.1.1 untuk Case A karena `sender_matches(_, [])` selalu False.

### Files NOT Touched

- `imap_engine.py:148` (`sender_matches`) — kontrak unchanged.
- `imap_engine.py:27` (`OTP_SUBJECT_REGEX`) — outermost gate unchanged.
- `imap_engine.py:160-200` (`_atomic_update_master`) — unchanged.
- `imap_engine.py:_escape_imap_string` — unchanged.
- `imap_engine.py:_write_live` — unchanged.
- `imap_engine.py:_write_noemail`, `_write_die`, `_write_unreg`, `_write_domain_skip` — unchanged.
- `app.py`, `imap_config.py`, `requirements.txt` — unchanged.

## Testing Strategy

### Validation Approach

Pendekatan dua-fase, paralel dengan `centralize-imap-success-master`: pertama surface counterexample pada kode UNFIXED untuk memvalidasi root cause, lalu verifikasi fix dengan property-based testing untuk fix checking dan preservation.

### Exploratory Bug Condition Checking

**Goal**: Confirm root cause analysis dengan failing test pada kode UNFIXED.

**Test Plan**: Tulis tiga unit test terhadap `ImapChecker._worker` dengan `mail_conn` yang di-mock (mengembalikan UID tertentu untuk SEARCH dan raw bytes RFC822 untuk FETCH). Jalankan terhadap kode UNFIXED → harus FAIL.

**Test Cases**:

1. **Case A — Keyword-only drop** (will fail on unfixed code).
   - `target_senders=[]`, `keywords=["confirmation"]`.
   - Mock SEARCH SUBJECT mengembalikan `b"42"`; FETCH UID 42 mengembalikan email dari `reservations@hotelchain.com` dengan subject `Booking confirmation #12345`.
   - Assert: `_write_live` dipanggil dengan emails_data yang berisi 1 entry, dan `_write_noemail` TIDAK dipanggil.
   - Pre-fix: `_write_noemail` dipanggil → test fail. Post-fix: pass.

2. **Case B — Both-filled, SUBJECT-only drop** (will fail on unfixed code).
   - `target_senders=["@booking.com"]`, `keywords=["invoice"]`.
   - Mock SEARCH FROM mengembalikan `b""` (no match); SEARCH SUBJECT mengembalikan `b"77"`; FETCH UID 77 mengembalikan email dari `billing@stripe.com`.
   - Assert: `emails_data` berisi 1 entry.
   - Pre-fix: `sender_matches("billing@stripe.com", ["@booking.com"])` False → drop → fail. Post-fix: cabang `eid_bytes in subject_matched_ids` keep → pass.

3. **OTP filter persistence** (will pass on both unfixed and fixed; sanity).
   - `target_senders=[]`, `keywords=["Booking.com"]`.
   - Subject `Booking.com – ABC123 is your verification code` → OTP regex match.
   - Assert: emails_data empty.

**Expected Counterexamples**:
- Test 1 produces counterexample untuk Case A (keyword-only).
- Test 2 produces counterexample untuk Case B (subject-only di regime both-filled).
- Possible causes confirmed: empty-list semantics `sender_matches`, single-arm gate yang tidak match union semantics.

### Fix Checking

**Goal**: Untuk semua input yang memenuhi `isBugCondition`, fixed `_worker` keep email di `emails_data`.

**Pseudocode**:

```
FOR ALL X WHERE isBugCondition(X) DO
  result := _worker_fixed(X)
  ASSERT email X is included in emails_data exactly once
  ASSERT _write_live invoked for X.account
  ASSERT _update_progress("live") called, _update_progress("noemail") NOT called
END FOR
```

**Test Approach**: Property-based test (Hypothesis) yang menggenerate `(target_senders, keywords, fetched_emails)` triples di mana minimal salah satu fetched email memenuhi Case A atau Case B dari `isBugCondition`. Mock `mail_conn.search` untuk mengembalikan UID yang konsisten dengan generated email set, mock `mail_conn.fetch` untuk return raw RFC822 dari generated email. Assert email tersebut muncul di `_write_live` arguments.

### Preservation Checking

**Goal**: Untuk semua input yang TIDAK memenuhi `isBugCondition`, `_worker_fixed(X) = _worker_original(X)` byte-for-byte.

**Pseudocode**:

```
FOR ALL X WHERE NOT isBugCondition(X) DO
  ASSERT _worker_original(X) = _worker_fixed(X)
END FOR
```

**Testing Approach**: Property-based testing (Hypothesis) sangat sesuai karena:
- Generate banyak kombinasi `(target_senders, keywords, fetched_emails)` random untuk regime sender-only, OTP, both-empty.
- Catch edge case (UID dengan whitespace, subject dengan unicode, from_addr dengan display-name vs bare).
- Bandingkan dua runner (one importing pre-fix `_worker`, one importing fixed) sisi-by-sisi pada input yang sama.

**Test Plan**: Observe behavior pada UNFIXED code untuk regime sender-only dan OTP, simpan output `(emails_data, live_count, noemail_count, _write_live calls)` sebagai golden, lalu jalankan FIXED dan assert byte-equal.

**Test Cases**:

1. **Sender-only preservation**: `target_senders=["@a.com", "@b.com"]`, `keywords=[]`. Random emails. Assert pre-fix dan post-fix menghasilkan `emails_data` identik (urutan dan konten).
2. **OTP preservation**: Email dengan subject yang match `OTP_SUBJECT_REGEX` → tidak masuk `emails_data` di kedua versi.
3. **Both-empty preservation**: `target_senders=[]`, `keywords=[]`. Assert nol SEARCH call (mock counter = 0), assert `_write_noemail` dipanggil sekali, `_write_live` nol kali.
4. **`<from_addr>` rendering preservation**: Untuk Case A account yang masuk ke `live.txt`, assert kolom From di output `_write_live` adalah raw `From` header (bukan `<from_addr>`). Diuji dengan capture argumen ke `open(self.live_file, 'a')` dan parsing baris.
5. **Master imap_success.json untouched**: Property test memastikan tidak ada call ke `_atomic_update_master` di luar `_update_imap_config` flow yang sudah ada (fix tidak introduce write baru ke master).
6. **`email_ids` set-union semantics**: Property test membandingkan `email_ids` set di kedua versi untuk input acak — harus identik.

### Unit Tests

- `_worker` Case A — keyword-only, satu email subject-match → `_write_live` dipanggil, raw From dirender (table-driven across 3 keyword variants).
- `_worker` Case B — both-filled, satu email subject-only → `_write_live` dipanggil dengan email tersebut included.
- `_worker` Case A dengan OTP subject — email di-drop, akun masuk ke `noemail.txt`.
- `_worker` sender-only baseline — output identik dengan pre-fix (snapshot test).
- `_worker` both-empty — zero SEARCH call, akun masuk `noemail.txt`.
- Bytes consistency — `subject_matched_ids` berisi bytes (mis. `b"42"`), `eid_bytes in subject_matched_ids` returns True untuk match.

### Property-Based Tests

- **Property: Fix Checking — keyword-only** (Hypothesis `@given`): generate non-empty list of keywords, empty target_senders, dan list random emails (sebagian subject-match keywords, sebagian tidak). Run mocked `_worker`. Assert: setiap email dengan subject-match dan non-OTP subject muncul di `emails_data`.
- **Property: Fix Checking — both-filled subject-only**: generate non-empty senders dan keywords, mix subject-only + from-only + both. Assert: setiap subject-only non-OTP muncul di `emails_data`.
- **Property: Preservation — sender-only equivalence**: generate non-empty senders, empty keywords, random emails. Run two `_worker` (pre-fix dan post-fix dalam test harness) → assert `emails_data` byte-equal.
- **Property: Preservation — OTP gate stays outermost**: generate emails dengan subject yang match `OTP_SUBJECT_REGEX` di seluruh kombinasi `target_senders`/`keywords` regime. Assert: tidak pernah masuk `emails_data` di post-fix.
- **Property: Preservation — `email_ids` union unchanged**: generate random SEARCH return values, instrument worker untuk capture final `email_ids`. Assert pre-fix == post-fix.
- **Property: Bytes-vs-str invariant**: generate random keywords (ASCII + unicode) dan UID values; assert `subject_matched_ids` selalu berisi bytes dan `eid_bytes in subject_matched_ids` works (no encoding mismatch).

### Integration Tests

- End-to-end keyword-only flow: spin up `ImapChecker` dengan mocked `imaplib.IMAP4_SSL` (via `unittest.mock.patch`), feed satu akun dan satu inbox dengan satu subject-match email. Assert `jobs/{job_id}/live.txt` contains the account and `noemail.txt` does not.
- End-to-end sender-only flow: same setup tapi keyword empty. Assert byte-identical `live.txt` dengan baseline (snapshot from pre-fix run).
- End-to-end mixed regime: 3 akun dengan campuran (subject-only match, sender-only match, both match, no match). Assert klasifikasi sesuai expected: 3 di `live.txt`, 1 di `noemail.txt`.
- Stop-flag during fetch: simulate `self._stop_event.set()` di tengah loop fetch keyword-only batch. Assert `break` happens dan partial `emails_data` di-flush ke `live.txt` jika non-empty.

## Risks

| Risk | Severity | Likelihood | Mitigation |
|------|----------|------------|------------|
| Regression pada sender-only callers | High | Very low | Three-arm OR runtuh ke `sender_matches(...)` saat `target_senders` non-empty dan `subject_matched_ids` empty (keywords empty). Diverify dengan PBT preservation property. |
| `subject_matched_ids` bocor antar akun (state leak) | Medium | Very low | Set adalah lokal per `_worker` invocation; setiap akun me-rebuild dari awal. Tidak ada `self.*` attribute baru. Diverify dengan integration test multi-account. |
| Bytes vs str comparison silently mismatch | Medium | Low | Disengaja: keduanya `bytes` (`msgs[0].split()` dan `eid_bytes` keduanya bytes). PBT bytes-invariant test memvalidasi. |
| OTP filter accidentally dilewati untuk Case A | High | Very low | Gate baru berada SETELAH `if not OTP_SUBJECT_REGEX.match(subject.strip())` (line 627). Tidak menyentuh OTP path. PBT property "OTP gate stays outermost" memvalidasi. |
| `_write_live` salah render `<from_addr>` untuk Case A | Medium | Very low | `_write_live` line 385 menggunakan `sender_matches(from_addr, self.target_senders)` — saat `target_senders=[]` selalu False → raw rendering. Tidak ada perubahan ke method ini. Unit test capture & assert raw rendering. |
| Performance regresi dari set membership di FETCH loop | Low | Very low | `set.__contains__` adalah O(1) untuk bytes. FETCH loop sudah dibatasi 20 element (`sorted_ids[-20:]`). Cost dapat diabaikan dibanding network IO IMAP fetch. |
| User memiliki keyword yang menghasilkan ribuan UID match | Low | Low | `sorted_ids` sudah cap di 20 newest emails. `subject_matched_ids` bisa membesar (semua hasil SEARCH SUBJECT), tapi masih dalam orde puluhan ribu paling banyak — set bytes ringan, masih << batas memori per worker. |
| Interaksi dengan `centralize-imap-success-master` fix | High | Very low | Fix ini berada di method `_worker` block fetch loop; master file flow ada di method `_update_imap_config` dan helpers modul-level. Tidak ada overlap garis kode. PBT preservation property "Master imap_success.json untouched" memvalidasi. |

## Final Requirement Coverage Validation

| Req | Description | Property | Notes |
|-----|-------------|----------|-------|
| 2.1 | Keyword-only bypass post-fetch sender filter | Property 1 | Cabang `not self.target_senders` di gate baru. |
| 2.1.1 | Raw `From` rendering untuk Case A | Property 1 + Property 2 | Natural behavior `_write_live` line 385 saat `target_senders=[]`; tidak ada code change. Unit test capture & assert. |
| 2.2 | `_write_live` + `_update_progress("live")` saat ada surviving email Case A | Property 1 | Branch existing `if emails_data:` di line ~639 — fix hanya memastikan `emails_data` non-empty saat seharusnya. |
| 2.3 | Both-filled inclusion via OR (sender match OR subject branch membership) | Property 1 | Three-arm OR, dengan `eid_bytes in subject_matched_ids` sebagai branch ketiga. Single logical OR per email — `or` short-circuit + `if` body executes once. |
| 3.1 | Sender-only byte-identical output | Property 2 | Gate runtuh ke `sender_matches(...)` saat keywords empty. PBT sender-only equivalence. |
| 3.2 | OTP regex outermost gate | Property 2 | Line 627 `if not OTP_SUBJECT_REGEX.match(...)` tidak disentuh. PBT OTP gate property. |
| 3.3 | Both-empty zero SEARCH path | Property 2 | Tidak ada perubahan ke kontrol flow di luar gate. PBT both-empty property. |
| 3.4 | `email_ids` set-union semantics | Property 2 | `email_ids.update(ids)` tetap dijalankan di kedua loop. PBT email_ids invariant property. |
| 3.5 | `<from_addr>` vs raw `From` di `_write_live` | Property 2 | `_write_live` tidak diubah. |
| 3.6 | `_atomic_update_master` + `FileLock` + `_escape_imap_string` intact | Property 2 | Fix tidak menyentuh helper modul-level. Unit test "no new write to master". |
| 3.7 | HTTP endpoint contracts | Property 2 | Tidak ada perubahan di `app.py`. |
| 3.8 | File names dan format per-job | Property 2 | Tidak ada perubahan ke `_write_*` methods. |

Coverage komplit: setiap acceptance criterion 2.x dan 3.x dipetakan ke salah satu Property 1 atau Property 2, dengan jejak ke unit/PBT test spesifik dan/atau referensi line code yang tidak disentuh.
