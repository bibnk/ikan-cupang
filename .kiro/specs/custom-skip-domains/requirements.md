# Requirements Document

## Introduction

Saat ini engine `ImapChecker` di `imap_engine.py` melakukan skip terhadap akun
yang domain-nya cocok dengan salah satu keyword di set hardcoded
`DOMAINS_TO_SKIP_KEYWORDS = {"hotmail", "live", "msn", "outlook", "yahoo", "interia", "poczta.fm"}`
yang dievaluasi sekali saat module di-load. Daftar ini tidak bisa diubah
tanpa redeploy aplikasi, sehingga admin tidak punya cara untuk menambah
domain baru yang ingin di-skip (misalnya provider yang sering rate-limit)
atau untuk menghapus salah satu default (misalnya membolehkan `outlook`
saat memang dibutuhkan).

Fitur ini menjadikan daftar tersebut user-editable, dipersist ke disk
pada satu file global di project root, dan dibaca ulang oleh engine pada
setiap job baru. Daftar diekspos lewat halaman Admin yang sudah ada
(`templates/admin.html`) dengan textarea satu pattern per baris dan
sepasang endpoint admin-gated (`GET` dan `POST /api/admin/skip-domains`).
Persistensi global (satu file dipakai oleh semua user/job), penulisan
atomik dan cross-process safe via `filelock.FileLock` + `tmp` +
`os.replace` (mengikuti pola `_atomic_update_master` yang sudah dibuat di
spec `centralize-imap-success-master`). Pada first run file di-seed
dengan default lama agar user yang tidak pernah meng-edit melihat
klasifikasi job yang byte-identical dengan perilaku saat ini.

Out of scope: per-user / per-job skip list, regex / wildcard semantics
(tetap substring containment seperti sekarang), dan fitur
`subject-exclusion-list` (spec terpisah).

## Glossary

- **Skip_Domains_File**: File JSON tunggal di project root,
  `skip_domains.json`, di direktori yang sama dengan `imap_engine.py`,
  `app.py`, dan master `imap_success.json`. Format:
  array of strings (`["hotmail", "live", ...]`).
- **Skip_Domains_Lock_Path**: Path file lock cross-process yang dipakai
  `filelock.FileLock` untuk koordinasi baca/tulis
  `Skip_Domains_File`. Bernilai `Skip_Domains_File + ".lock"`.
- **Default_Skip_Set**: Daftar default yang menjadi seed pada first run
  bila `Skip_Domains_File` belum ada — `["hotmail", "live", "msn",
  "outlook", "yahoo", "interia", "poczta.fm"]` (sama persis dengan
  `DOMAINS_TO_SKIP_KEYWORDS` saat ini).
- **Engine**: Class `ImapChecker` di `imap_engine.py`. Konstruktor di-load
  daftar skip aktif ke atribut instance `skip_domain_keywords`, lalu
  dipakai di `_worker` untuk evaluasi
  `any(kw in domain for kw in self.skip_domain_keywords)`.
- **Admin_UI**: Halaman `/admin` (`templates/admin.html`) yang sudah
  admin-gated lewat `session.get("is_admin")`. Section baru
  ditambahkan di halaman ini.
- **Admin_API**: Pasangan endpoint Flask baru:
  `GET /api/admin/skip-domains` dan
  `POST /api/admin/skip-domains`. Keduanya melalui dekorator
  `@login_required` yang sudah ada plus pengecekan
  `session.get("is_admin")` (mekanisme yang sama dengan
  `/api/admin/add-code` dan `/api/admin/delete-code`).
- **Skip_Domain_Entry**: Sebuah string pattern yang dipakai untuk
  substring containment terhadap domain bagian dari alamat email
  (`email.split("@")[-1].lower()`). Setelah normalisasi: di-`strip`,
  di-`lower`, dan di-deduplicate sambil mempertahankan urutan
  kemunculan pertama dari input user.

## Requirements

### Storage and Defaults

#### Requirement 1: Lokasi file dan format on-disk

**User Story:** Sebagai admin yang mengelola server, saya ingin daftar
skip-domains tersimpan di satu file global di project root, sehingga
seluruh job dan worker proses berbagi konfigurasi yang sama dan saya
tahu persis di mana mencari konfigurasi-nya.

#### Acceptance Criteria

1. THE Engine SHALL mendefinisikan path `Skip_Domains_File` sebagai konstanta module-level yang dievaluasi ke `os.path.join(os.path.dirname(os.path.abspath(__file__)), "skip_domains.json")`, sehingga path tetap menunjuk ke direktori `imap_engine.py` (project root) terlepas dari current working directory proses.
2. THE Engine SHALL menyimpan isi `Skip_Domains_File` sebagai dokumen JSON yang berbentuk array of strings dengan encoding UTF-8 dan indentasi `indent=2` (contoh: `["hotmail", "live"]`).
3. THE Engine SHALL mendefinisikan `Skip_Domains_Lock_Path` sebagai `Skip_Domains_File + ".lock"` dan menggunakannya sebagai sentinel untuk `filelock.FileLock`.

#### Requirement 2: Seed default pada first run

**User Story:** Sebagai admin yang baru deploy versi baru, saya ingin
file `skip_domains.json` otomatis dibuat dengan default lama saat
pertama kali dipakai, sehingga klasifikasi job sebelum saya menyentuh
daftar ini tetap sama persis dengan perilaku versi sebelumnya.

#### Acceptance Criteria

1. WHEN `Skip_Domains_File` tidak ada di disk dan Engine atau Admin_API mencoba membaca daftar skip, THE Engine SHALL menginisialisasi file tersebut dengan isi `Default_Skip_Set` (`["hotmail", "live", "msn", "outlook", "yahoo", "interia", "poczta.fm"]`) menggunakan jalur tulis atomik (Requirement 3).
2. WHEN `Skip_Domains_File` sudah ada di disk, THE Engine SHALL membaca isinya apa adanya dan TIDAK menimpanya dengan `Default_Skip_Set`, sehingga edit user (termasuk penghapusan salah satu default) dipertahankan secara permanen.
3. IF `Skip_Domains_File` ada tetapi tidak bisa di-parse sebagai JSON valid atau bukan berbentuk array of strings, THEN THE Engine SHALL memperlakukan daftar skip aktif sebagai array kosong (`[]`) untuk job yang sedang berjalan dan TIDAK melakukan tulis ulang otomatis ke disk untuk file yang korup.

#### Requirement 3: Atomic write dan cross-process safety

**User Story:** Sebagai admin yang menjalankan aplikasi di balik
multi-worker WSGI server, saya ingin perubahan skip-list aman terhadap
crash dan race condition antar proses, sehingga daftar tidak pernah
ter-corrupt atau kehilangan update.

#### Acceptance Criteria

1. WHEN Admin_API atau Engine menulis perubahan ke `Skip_Domains_File`, THE Engine SHALL menjalankan urutan operasi filesystem yang dapat diobservasi tester sebagai berikut secara berurutan: (a) `open(Skip_Domains_File + ".tmp", "w", encoding="utf-8")` untuk membuat / truncate tmp file di direktori yang sama dengan target, (b) `json.dump(value, f, indent=2)` di mana `value` adalah list of strings yang sudah dinormalisasi (sehingga byte-equivalence on-disk identik dengan yang akan dihasilkan oleh `_atomic_update_master`), (c) `f.flush()`, (d) `os.fsync(f.fileno())`, (e) penutupan file (akhir context manager `with`), (f) `os.replace(tmp_path, Skip_Domains_File)` untuk rename atomik (POSIX maupun Windows selama sumber dan tujuan satu filesystem).
2. WHEN Admin_API atau Engine melakukan baca-modifikasi-tulis pada `Skip_Domains_File`, THE Engine SHALL membungkus seluruh siklus tersebut di dalam `filelock.FileLock(Skip_Domains_Lock_Path, timeout=MASTER_LOCK_TIMEOUT_SECONDS)` (memakai konstanta yang sama dengan master imap_success), sehingga hanya satu proses di seluruh worker yang bisa membaca-mengubah-menulis dalam satu waktu; `Skip_Domains_Lock_Path` boleh persist on-disk antar run karena `filelock` mengelola release lewat OS file handle, bukan keberadaan file, dan Engine SHALL TIDAK menghapus atau menganggap stale lock file yang tertinggal saat startup.
3. IF proses crash, di-kill, atau disk penuh setelah `Skip_Domains_File + ".tmp"` mulai ditulis tetapi sebelum `os.replace` selesai, THEN THE Engine SHALL meninggalkan `Skip_Domains_File` dalam state lama yang valid — file master tidak pernah berada dalam state truncated, parsial, atau berisi byte campuran lama+baru — invariant ini dijamin oleh kombinasi tmp + os.replace; bila ada `.tmp` file yang tertinggal di disk dari kegagalan sebelumnya, Engine SHALL TIDAK memperlakukannya sebagai sumber kebenaran pada run berikutnya (hanya `Skip_Domains_File` yang sudah ter-rename oleh `os.replace` yang valid).
4. IF `filelock.FileLock` tidak dapat di-acquire dalam `MASTER_LOCK_TIMEOUT_SECONDS` detik, THEN THE Engine SHALL memunculkan exception `filelock.Timeout` ke caller, dan caller SHALL menangani-nya sebagai berikut: (a) untuk handler Admin_API (`GET` / `POST /api/admin/skip-domains`), respons HTTP 503 dengan body JSON `{"error": "Skip-domains file is busy, please retry"}` dikembalikan dan TIDAK ada penulisan ke `Skip_Domains_File` yang dilakukan; (b) untuk `ImapChecker.__init__` pada saat seed first-run, fallback ke `Default_Skip_Set` in-memory sebagai `self.skip_domain_keywords` tanpa menandai file sebagai sudah-diseed dan tanpa raise dari constructor (worker proses tidak crash).
5. THE Engine SHALL menulis on-disk format yang byte-identical dengan format yang dihasilkan `_atomic_update_master` dari spec `centralize-imap-success-master`, yaitu hasil `json.dump(value, f, indent=2)` di mana `value` adalah list of strings; tester dapat memverifikasi byte-equivalence ini dengan diff byte-level antara file hasil tulisan fitur ini dan output `json.dumps(value, indent=2).encode("utf-8")` (pada platform tanpa newline translation) untuk `value` yang sama.

#### Requirement 4: Normalisasi entry pada penyimpanan

**User Story:** Sebagai admin yang menulis daftar di textarea, saya ingin
entry tersimpan dalam bentuk yang konsisten sehingga substring match di
engine (yang lowercase domain) selalu deterministik.

#### Acceptance Criteria

1. WHEN Admin_API menerima list pattern dari client untuk disimpan, THE Engine SHALL menerapkan urutan transformasi berikut secara persis: (a) `strip()` setiap entry, (b) `lower()` setiap entry, (c) buang entry yang kosong setelah strip, (d) deduplicate sambil mempertahankan urutan kemunculan pertama dari input.
2. WHILE proses normalisasi berjalan, THE Engine SHALL mempertahankan urutan kemunculan pertama dari input user di hasil yang disimpan ke `Skip_Domains_File`.
3. THE Engine SHALL TIDAK menerapkan transformasi tambahan (mis. trimming karakter di tengah, regex stripping, dll) selain langkah-langkah pada acceptance criterion 4.1.

### Engine Integration

#### Requirement 5: ImapChecker membaca daftar skip dari file pada saat init

**User Story:** Sebagai user yang menjalankan job IMAP check, saya ingin
job memakai daftar skip-domains terbaru saat itu, sehingga edit yang
admin lakukan langsung berlaku untuk job berikutnya tanpa perlu restart
aplikasi.

#### Acceptance Criteria

1. WHEN `ImapChecker.__init__` dipanggil untuk job baru, THE Engine SHALL membaca isi `Skip_Domains_File` (melakukan seed default jika file belum ada — Requirement 2.1) di dalam `filelock.FileLock(Skip_Domains_Lock_Path, timeout=MASTER_LOCK_TIMEOUT_SECONDS)` — read-only acquire diterima karena lock mengoordinasikan keseluruhan baca-modifikasi-tulis (Requirement 3.2) — lalu menyimpan hasilnya sebagai atribut instance `self.skip_domain_keywords` berbentuk list of strings; pembacaan ini SHALL selesai SEBELUM thread worker (`self._worker`) di `run()` di-spawn, sehingga snapshot per-job dipasang sebelum eksekusi paralel dimulai.
2. THE Engine SHALL menghapus simbol module-level `DOMAINS_TO_SKIP_KEYWORDS` dari `imap_engine.py`, menjadikan loader dari `Skip_Domains_File` sebagai satu-satunya sumber kebenaran daftar skip aktif; `Default_Skip_Set` SHALL dipertahankan sebagai konstanta module-level privat hanya untuk keperluan first-run seeding (Requirement 2.1) dan SHALL TIDAK direferensikan dari `_worker` atau alur evaluasi skip; di dalam `_worker`, predikat skip SHALL membaca dari `self.skip_domain_keywords` (bukan dari konstanta module-level mana pun).
3. WHEN dua atau lebih job berjalan bersamaan dalam proses worker yang sama atau di proses worker WSGI berbeda, THE Engine SHALL memberi setiap job snapshot daftar skip yang dimuat saat job tersebut di-init (di dalam `__init__`, sebelum thread worker dimulai per criterion 1), sehingga edit yang dilakukan via `POST /api/admin/skip-domains` di tengah eksekusi job lain SHALL TIDAK mengubah `self.skip_domain_keywords` job yang sedang berjalan dan SHALL TIDAK mempengaruhi klasifikasi `domain_skipped` job tersebut (snapshot semantics per-job, non-retroaktif).
4. IF `Skip_Domains_File` ada di disk tetapi tidak bisa di-parse sebagai JSON valid atau bukan berbentuk array of strings (kondisi yang ditangani oleh Requirement 2.3), THEN `ImapChecker.__init__` SHALL menetapkan `self.skip_domain_keywords = []` untuk job tersebut, dan `_worker` SHALL mengevaluasi predikat skip sebagai `False` untuk setiap akun (tidak ada akun yang masuk `domain_skipped` semata-mata karena daftar kosong, sesuai Requirement 6.2), dan Engine SHALL TIDAK menulis ulang otomatis ke `Skip_Domains_File` sebagai efek samping dari init job (file korup tetap apa adanya hingga admin mengganti via `POST /api/admin/skip-domains`).

#### Requirement 6: Semantik matching dipertahankan persis

**User Story:** Sebagai admin yang tidak mengedit daftar, saya ingin job
menghasilkan klasifikasi (`live` / `noemail` / `die` / `unreg` /
`domain_skipped`) yang byte-identical dengan perilaku saat ini.

#### Acceptance Criteria

1. WHEN `_worker` mengevaluasi apakah sebuah account harus di-skip, THE Engine SHALL menggunakan ekspresi `any(kw in domain for kw in self.skip_domain_keywords)` di mana `domain = email_addr.split("@")[-1].lower()` — yaitu substring containment, case-insensitive (lowercase di kedua sisi karena entry di-`lower` saat penyimpanan dan `domain` sudah di-`lower` di `_worker`).
2. WHEN `self.skip_domain_keywords` adalah list kosong, THE Engine SHALL mengevaluasi predikat `any(...)` sebagai `False` untuk setiap account, sehingga tidak ada account yang diklasifikasikan sebagai `domain_skipped` semata-mata karena daftar skip kosong.
3. WHEN admin tidak pernah mengedit daftar setelah first-run seeding, THE Engine SHALL menghasilkan klasifikasi `live` / `noemail` / `die` / `unreg` / `domain_skipped` yang sama persis (byte-identical) untuk input akun, sender, dan keyword yang sama dibandingkan dengan implementasi sebelum fitur ini ada.

### Admin UI and Endpoints

#### Requirement 7: Endpoint GET untuk membaca daftar saat ini

**User Story:** Sebagai admin yang membuka halaman admin, saya ingin
melihat daftar skip-domains saat ini agar bisa mengedit dari state
terkini.

#### Acceptance Criteria

1. THE Admin_API SHALL menyediakan endpoint `GET /api/admin/skip-domains` yang dilindungi dekorator `@login_required` dan memerlukan `session.get("is_admin")` bernilai true.
2. WHEN sebuah request `GET /api/admin/skip-domains` diterima dari user yang ter-autentikasi sebagai admin, THE Admin_API SHALL membaca `Skip_Domains_File` (melakukan seed default jika belum ada — Requirement 2.1) dan mengembalikan respons JSON `{"domains": [<entry>, ...]}` dengan HTTP status 200 dan urutan entry sesuai isi file (yang sudah ternormalisasi sesuai Requirement 4).
3. IF request `GET /api/admin/skip-domains` diterima dari user yang belum ter-autentikasi (tidak ada `session["authenticated"]`), THEN THE Admin_API SHALL mengembalikan HTTP 401 dengan body JSON `{"error": "Unauthorized"}` (mengikuti perilaku dekorator `login_required` saat ini untuk path yang diawali `/api/`).
4. IF request `GET /api/admin/skip-domains` diterima dari user yang ter-autentikasi tetapi bukan admin, THEN THE Admin_API SHALL mengembalikan HTTP 403 dengan body JSON `{"error": "Admin only"}` (mengikuti pola `/api/admin/add-code`).

#### Requirement 8: Endpoint POST untuk mengganti seluruh daftar

**User Story:** Sebagai admin, saya ingin mengganti daftar skip-domains
dengan satu kali submit, sehingga semantiknya jelas (PUT-like) dan tidak
perlu memanggil endpoint terpisah untuk add/remove per entry.

#### Acceptance Criteria

1. THE Admin_API SHALL menyediakan endpoint `POST /api/admin/skip-domains` yang dilindungi dekorator `@login_required` dan memerlukan `session.get("is_admin")` bernilai true.
2. WHEN `POST /api/admin/skip-domains` diterima dari admin yang valid dengan body JSON `{"domains": [<string>, ...]}`, THE Admin_API SHALL menormalisasi list (Requirement 4), memvalidasinya (Requirement 9), lalu — bila semua entry valid — menulis hasil normalisasi ke `Skip_Domains_File` melalui jalur atomik (Requirement 3) dan mengembalikan HTTP 200 dengan body JSON `{"success": true, "domains": [<string>, ...]}` di mana `domains` adalah daftar yang akhirnya tersimpan.
3. WHEN penulisan via Requirement 8.2 berhasil, THE Admin_API SHALL melakukan replace penuh isi `Skip_Domains_File` (PUT-like): entry yang sebelumnya ada tetapi tidak hadir di payload yang dinormalisasi SHALL dihapus dari file akhir.
4. IF body request `POST /api/admin/skip-domains` bukan JSON valid atau tidak berisi key `domains` yang nilainya array of strings, THEN THE Admin_API SHALL mengembalikan HTTP 400 dengan body JSON `{"error": "Invalid payload"}` dan TIDAK menulis ke `Skip_Domains_File`.
5. IF request `POST /api/admin/skip-domains` diterima dari user yang belum ter-autentikasi, THEN THE Admin_API SHALL mengembalikan HTTP 401 dengan body JSON `{"error": "Unauthorized"}`.
6. IF request `POST /api/admin/skip-domains` diterima dari user yang ter-autentikasi tetapi bukan admin, THEN THE Admin_API SHALL mengembalikan HTTP 403 dengan body JSON `{"error": "Admin only"}`.

#### Requirement 9: Validasi entry pada save

**User Story:** Sebagai admin, saya ingin sistem menolak entry yang
tidak valid sebelum tersimpan, sehingga daftar yang akhirnya dipakai
engine selalu masuk akal untuk substring match terhadap domain email.

#### Acceptance Criteria

1. THE Admin_API SHALL menerapkan urutan validasi per-entry sebagai berikut secara DETERMINISTIK: PERTAMA cek apakah `entry.strip()` adalah string kosong (criterion ini), KEDUA cek apakah panjang `entry.strip()` lebih dari 255 karakter (Requirement 9.2), KETIGA cek apakah `re.search(r'\s', entry.strip())` mengembalikan match (Requirement 9.3); jika satu cek gagal, cek-cek berikutnya untuk entry tersebut SHALL TIDAK dijalankan (short-circuit), dan rejection list hanya melaporkan entry-nya, bukan rule mana yang gagal.
2. THE Admin_API SHALL menolak entry yang panjangnya (jumlah karakter setelah `strip()`) lebih dari 255 sebagai invalid (cek KEDUA per criterion 9.1).
3. THE Admin_API SHALL menolak entry yang setelah `strip()` mengandung whitespace internal — dideteksi dengan `re.search(r'\s', entry.strip()) is not None` di mana Python `\s` mencakup ASCII whitespace (` `, `\t`, `\n`, `\r`, `\v`, `\f`) maupun Unicode whitespace (mis. NBSP `U+00A0`, ideographic space `U+3000`) — sebagai invalid, karena substring match terhadap alamat email tidak pernah cocok dengan whitespace di tengah pattern (cek KETIGA per criterion 9.1).
4. IF satu atau lebih entry pada payload `POST /api/admin/skip-domains` melanggar Requirement 9.1, 9.2, atau 9.3, THEN THE Admin_API SHALL mengembalikan HTTP 400 dengan body JSON `{"error": "Invalid entries", "rejected": [<entry_apa_adanya>, ...]}` di mana (a) `rejected` melaporkan entry persis seperti yang dikirim user (sebelum normalisasi `strip()`/`lower()`), (b) urutan kemunculan di list `rejected` mempertahankan urutan kemunculan pertama di payload, (c) duplikasi entry yang invalid disertakan apa adanya bila user mengirim duplikat, dan (d) Admin_API SHALL TIDAK mengekspos rule mana yang gagal (rejection list hanya berisi entry-nya, sesuai criterion 9.1); Admin_API SHALL TIDAK menulis ke `Skip_Domains_File`.
5. WHEN validasi gagal (Requirement 9.4), THE Admin_API SHALL mempertahankan isi `Skip_Domains_File` apa adanya — tidak ada partial write, tidak ada penyimpanan entry valid pada payload yang sama (semantics all-or-nothing).
6. THE Admin_API SHALL mengasumsikan setiap elemen di array `domains` adalah string ketika menjalankan validasi 9.1–9.5; IF sebuah elemen bukan string (mis. integer, null, dict), THEN penanganan SHALL didelegasikan ke Requirement 8.4 — payload dianggap invalid secara struktural dan respons HTTP 400 dengan body `{"error": "Invalid payload"}` dikembalikan, tanpa mengeksekusi validasi per-entry pada array tersebut.

#### Requirement 10: Section baru di halaman admin

**User Story:** Sebagai admin, saya ingin mengelola daftar skip-domains
lewat halaman web yang sama dengan halaman admin lainnya, sehingga tidak
perlu tool eksternal.

#### Acceptance Criteria

1. THE Admin_UI SHALL menambahkan satu section baru di `templates/admin.html` di bawah section "Daftar Kode Akses" yang berisi: heading, satu textarea untuk skip-domains (satu pattern per baris), dan satu tombol "Simpan".
2. WHEN halaman `/admin` dirender untuk user admin, THE Admin_UI SHALL memuat daftar saat ini melalui pemanggilan klien-side `GET /api/admin/skip-domains` (atau via context Jinja yang setara) dan mengisi textarea dengan satu entry per baris dalam urutan yang dikembalikan endpoint.
3. WHEN admin menekan tombol "Simpan", THE Admin_UI SHALL mem-parse isi textarea dengan `split("\n")`, mengirim payload `{"domains": [<line>, ...]}` ke `POST /api/admin/skip-domains`, dan menampilkan pesan sukses (response 200) atau error (response 400/401/403/5xx) sesuai respons endpoint.
4. WHERE user yang sedang login bukan admin (`session.get("is_admin")` bernilai false), THE Admin_UI SHALL TIDAK merender section skip-domains di halaman manapun (halaman `/admin` itu sendiri sudah meredirect non-admin ke `/`, sehingga section hanya perlu dijaga di template `admin.html`).
5. THE Admin_UI SHALL menggunakan mekanisme autentikasi yang sudah ada (`@login_required` + `session.get("is_admin")`) untuk seluruh interaksi dengan section skip-domains, dan SHALL TIDAK memperkenalkan layer auth tambahan.

### Backward Compatibility / Preservation

#### Requirement 11: Tidak ada regresi pada API surface yang sudah ada

**User Story:** Sebagai user dan integrator endpoint yang sudah ada,
saya ingin semua endpoint Flask saat ini berperilaku sama persis,
sehingga klien yang ada tidak rusak.

#### Acceptance Criteria

1. THE Engine SHALL TIDAK memodifikasi kontrak (path, method, request body, response body, status code) dari endpoint berikut: `/login`, `/logout`, `/`, `/get-email`, `/admin`, `/api/admin/add-code`, `/api/admin/delete-code`, `/api/check`, `/api/stop/<job_id>`, `/api/status/<job_id>`, `/api/download/<job_id>/<file_type>`, `/api/get-email`, `/api/delete-email`, `/history`, `/api/jobs`, `/api/jobs/<job_id>/extend`, `/api/jobs/<job_id>/delete`, `/api/jobs/<job_id>/live`, `/loop-delete`, `/api/loop-delete/start`, `/api/loop-delete/stop/<job_id>`, `/api/loop-delete/restart/<job_id>`, `/api/loop-delete/delete/<job_id>`, `/api/loop-delete/status/<job_id>`, `/api/loop-delete/list`.
2. THE Engine SHALL menambahkan endpoint baru `GET /api/admin/skip-domains` dan `POST /api/admin/skip-domains` saja, tanpa menyentuh path Flask lain.

#### Requirement 12: Klasifikasi job byte-identical saat default tidak diubah

**User Story:** Sebagai admin yang tidak mengedit daftar setelah upgrade,
saya ingin output job sama persis dengan sebelum fitur ini, sehingga
upgrade ini transparan untuk pengguna saya.

#### Acceptance Criteria

1. WHEN admin tidak pernah memanggil `POST /api/admin/skip-domains` setelah deploy versi baru pertama kali — mencakup (a) kasus `Skip_Domains_File` tidak ada di disk pada saat deploy dan kemudian di-seed oleh Engine pada first read di `ImapChecker.__init__`, dan (b) kasus interaksi admin pertama adalah `GET /api/admin/skip-domains` (read-only, juga memicu first-run seeding via Requirement 2.1) — sehingga `Skip_Domains_File` berisi `Default_Skip_Set` apa adanya, THE Engine SHALL menghasilkan isi `live.txt`, `noemail.txt`, `die.txt`, `unreg.txt`, dan `domain_skipped.txt` yang byte-identical (didefinisikan sebagai: SHA-256 digest tiap file per-job sama persis dengan SHA-256 digest file pada reference run yang dilakukan terhadap implementasi sebelum fitur ini, dengan input job (akun, sender, keyword, search_days, max_threads, proxy_config) yang identik dan respons server IMAP yang identik melalui mock/fixture deterministik).
2. WHEN admin telah meng-edit daftar via `POST /api/admin/skip-domains` (mis. menambah `gmail` atau menghapus `outlook`) dan penulisan ke `Skip_Domains_File` sudah selesai (Requirement 3.1 — `os.replace` selesai), THE Engine SHALL memperlakukan daftar yang ter-edit sebagai sumber kebenaran baru pada pemanggilan `ImapChecker.__init__` berikutnya, sehingga klasifikasi `domain_skipped` pada job baru tersebut mencerminkan daftar yang sudah diedit, sementara job yang sudah berjalan saat penulisan terjadi SHALL tetap memakai snapshot daftar yang dimuat saat init-nya (non-retroaktif, sesuai Requirement 5.3).
3. WHEN Engine melakukan first-run seeding terhadap `Skip_Domains_File` (Requirement 2.1), THE Engine SHALL menulis isi file sebagai array JSON dengan urutan elemen persis `["hotmail", "live", "msn", "outlook", "yahoo", "interia", "poczta.fm"]` sebagaimana didefinisikan oleh `Default_Skip_Set` di Glossary, dengan catatan bahwa urutan ini dipertahankan untuk stabilitas isi file dan kemudahan diff, bukan untuk korektnesssemantik `_worker` (predikat `any(kw in domain for kw in self.skip_domain_keywords)` tidak sensitif terhadap urutan elemen, hanya terhadap keanggotaan).

#### Requirement 13: Master imap_success.json tidak terpengaruh

**User Story:** Sebagai admin yang sudah memakai master
`imap_success.json` (dari spec sebelumnya), saya ingin fitur ini sama
sekali tidak menyentuh on-disk format atau kontrak file tersebut,
sehingga 200+ entri konfigurasi domain yang sudah ada tetap aman.

#### Acceptance Criteria

1. THE Engine SHALL TIDAK melakukan operasi tulis ke `imap_success.json` di project root sebagai bagian dari penanganan `Skip_Domains_File`.
2. THE Engine SHALL TIDAK mengubah skema, format, atau urutan key/value pada `imap_success.json` sebagai efek samping dari load/save `Skip_Domains_File`.
3. THE Engine SHALL menggunakan file lock (`Skip_Domains_File + ".lock"`) yang TERPISAH dari lock file `imap_success.json.lock`, sehingga operasi pada salah satu file tidak memblok operasi pada file yang lain.

#### Requirement 14: Test suite spec sebelumnya tetap hijau

**User Story:** Sebagai developer yang menjaga regression suite, saya
ingin test dari spec `centralize-imap-success-master` dan
`subject-keyword-match-shown-in-live` tetap lulus tanpa modifikasi,
sehingga upgrade ini tidak menghasilkan test breakage di luar fitur
sendiri.

#### Acceptance Criteria

1. WHEN test suite dari spec `centralize-imap-success-master` dijalankan setelah implementasi fitur ini, THE Engine SHALL menyebabkan seluruh test pada suite tersebut PASS tanpa modifikasi pada test code, expected fixtures, atau konfigurasi runner.
2. WHEN test suite dari spec `subject-keyword-match-shown-in-live` dijalankan setelah implementasi fitur ini, THE Engine SHALL menyebabkan seluruh test pada suite tersebut PASS tanpa modifikasi pada test code, expected fixtures, atau konfigurasi runner.
3. THE Engine SHALL TIDAK mengubah dependency `requirements.txt` dengan menambahkan paket baru selain yang sudah ada — `filelock` sudah ditambahkan oleh spec `centralize-imap-success-master`, dan fitur ini reuse dependency tersebut tanpa upgrade major.
