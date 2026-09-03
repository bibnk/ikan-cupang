# Bugfix Requirements Document

## Introduction

Setiap kali user menjalankan IMAP check, engine (`ImapChecker` di `imap_engine.py`) menulis hasil discovery konfigurasi IMAP ke file `jobs/{job_id}/imap_success.json` yang spesifik per job. Akibatnya, hasil pembelajaran konfigurasi IMAP terpecah-pecah di banyak folder job dan tidak terkumpul di file master `imap_success.json` di project root yang sudah ada.

Master file di project root sudah berisi banyak entri (200+ domain) yang dipakai sebagai sumber data utama (lihat `_merge_imap.py` dan `_populate_imap.py`), tetapi `ImapChecker` tidak ikut menulis ke file tersebut, sehingga setiap konfigurasi domain baru yang ditemukan saat check tidak terakumulasi ke data master.

Selain itu, walaupun file master sudah dipakai, cara baca-tulisnya saat ini tidak aman terhadap kegagalan dan konkurensi:

- **Tulis tidak atomik**: `_update_imap_config` menulis langsung ke `imap_success.json` dengan `open(..., 'w')` + `json.dump`. Jika proses crash, di-kill, atau disk penuh saat penulisan, master file (yang berisi 200+ entri) bisa menjadi truncated, kosong, atau JSON tidak valid — konfigurasi yang sudah dipelajari hilang permanen.
- **Tidak ada lock antar proses**: `self.imap_config_lock` adalah `threading.Lock` yang hanya melindungi akses dalam satu proses Python. Saat aplikasi dijalankan di balik multi-worker WSGI server (gunicorn / uwsgi), beberapa worker proses bisa membaca file, mengubah di memori, lalu menulis balik secara bersamaan, sehingga update saling menimpa (last writer wins) dan domain yang ditemukan worker lain hilang.
- **String IMAP search tidak di-escape**: Di `_worker`, kriteria IMAP dibangun dengan f-string `f'(SINCE "..." FROM "{sender}")'` dan `f'(SINCE "..." SUBJECT "{kw}")'`. Bila `sender` atau `kw` mengandung karakter `"` atau `\`, server IMAP menerima perintah yang malformed sesuai RFC 3501 (yang mewajibkan escape `\` dan `"` dengan backslash di dalam quoted string), sehingga search error / mengembalikan hasil salah dan akun bisa salah diklasifikasikan sebagai `die`.

Bug ini diperbaiki secara terpadu dengan memastikan `ImapChecker` selalu membaca dari, dan menulis ke, satu file master tunggal `imap_success.json` di project root, dan setiap baca/tulis ke master tersebut dilakukan secara aman: penulisan atomik via temp file + rename, dilindungi oleh cross-process file lock (selain `threading.Lock`), serta semua argumen yang masuk ke kriteria IMAP `SEARCH` di-escape dengan benar. File-file hasil per job lainnya (`live.txt`, `noemail.txt`, `die.txt`, `unreg.txt`, `domain_skipped.txt`) tetap berada di folder per job seperti sekarang.

## Bug Analysis

### Current Behavior (Defect)

Saat job IMAP check berjalan, konfigurasi IMAP yang berhasil ditemukan disimpan di file per job, bukan di master tunggal di project root. Tambahan: cara baca/tulis master file tidak atomik, tidak terlindung lock antar proses, dan string masukan IMAP `SEARCH` tidak di-escape.

1.1 WHEN user memulai job IMAP check baru THEN the system membuat file `imap_success.json` di dalam `jobs/{job_id}/` bukan di project root

1.2 WHEN `ImapChecker._update_imap_config(domain, config_data)` dipanggil setelah discovery domain baru berhasil THEN the system menulis konfigurasi domain tersebut hanya ke `jobs/{job_id}/imap_success.json`, sehingga master `imap_success.json` di project root tidak ter-update

1.3 WHEN `ImapChecker.__init__` memuat konfigurasi IMAP yang sudah dipelajari sebelumnya melalui `_load_imap_success_config()` THEN the system membaca dari `jobs/{job_id}/imap_success.json` (yang biasanya kosong/baru), bukan dari master `imap_success.json` di project root, sehingga konfigurasi yang sudah dipelajari job-job sebelumnya tidak dipakai

1.4 WHEN job dihapus atau di-cleanup setelah expired (folder `jobs/{job_id}` dihapus oleh `shutil.rmtree`) THEN the system ikut menghapus `imap_success.json` per job tersebut, sehingga konfigurasi domain yang baru ditemukan selama job tersebut hilang

1.5 WHEN `_update_imap_config` sedang menulis isi master `imap_success.json` dengan `open(..., 'w')` + `json.dump(...)` dan proses crash, di-kill, kehabisan memori, atau disk penuh di tengah penulisan THEN the system meninggalkan file master dalam keadaan truncated, kosong, atau JSON tidak valid (parsing gagal saat job berikutnya), sehingga seluruh entri domain yang sudah dipelajari (200+) bisa hilang permanen

1.6 WHEN aplikasi dijalankan di balik multi-worker WSGI server (mis. `gunicorn -w N` atau uwsgi) dan dua atau lebih worker proses memanggil `_update_imap_config` untuk domain berbeda secara bersamaan THEN the system membiarkan setiap worker membaca master, mengubah di memori, lalu menulis balik tanpa lock antar proses, sehingga penulisan terakhir menimpa penulisan sebelumnya (last writer wins) dan domain yang ditemukan worker lain hilang dari master

1.7 WHEN `target_senders` atau `keywords` yang dimasukkan user mengandung karakter `"` atau `\` (mis. `weird"sender@example.com` atau keyword `path\to\file`) THEN the system membangun kriteria IMAP `SEARCH` via f-string tanpa escape, sehingga server IMAP menerima perintah quoted string yang malformed (sesuai RFC 3501); search dapat error sehingga akun salah diklasifikasikan sebagai `die`, atau mengembalikan hasil yang tidak benar

### Expected Behavior (Correct)

Hanya ada satu file master `imap_success.json` di project root yang menjadi sumber data konfigurasi IMAP yang dipelajari, semua job baca/tulis ke file tersebut secara aman, atomik, dan terlindung dari race condition lintas-proses, serta semua input yang masuk ke kriteria IMAP `SEARCH` selalu di-escape.

2.1 WHEN user memulai job IMAP check baru THEN the system SHALL TIDAK membuat file `imap_success.json` di dalam `jobs/{job_id}/` dan SHALL menggunakan satu file master tunggal di project root (`imap_success.json` di direktori yang sama dengan `imap_engine.py` / `app.py`)

2.2 WHEN `ImapChecker._update_imap_config(domain, config_data)` dipanggil setelah discovery domain baru berhasil THEN the system SHALL meng-update master `imap_success.json` di project root dengan menggabungkan (merge) entri baru ke isi master yang sudah ada, tanpa menghapus entri lain

2.3 WHEN `ImapChecker.__init__` memuat konfigurasi IMAP yang sudah dipelajari sebelumnya THEN the system SHALL membaca dari master `imap_success.json` di project root, sehingga semua job mewarisi pengetahuan domain yang sudah dipelajari job-job sebelumnya

2.4 WHEN dua atau lebih job berjalan bersamaan dan masing-masing memanggil `_update_imap_config` untuk domain berbeda THEN the system SHALL menulis ke master `imap_success.json` secara aman tanpa kehilangan entri (concurrent-safe), sehingga seluruh entri yang dihasilkan oleh semua job tetap ada di master

2.5 WHEN job dihapus atau di-cleanup (folder `jobs/{job_id}` dihapus) THEN the system SHALL TIDAK menghapus master `imap_success.json` di project root, sehingga konfigurasi domain yang sudah dipelajari tetap tersedia untuk job berikutnya

2.6 WHEN `_update_imap_config` menulis konfigurasi terbaru ke master `imap_success.json` THEN the system SHALL menulis terlebih dahulu ke file sementara `imap_success.json.tmp` di direktori yang sama, lalu memanggil `os.replace(tmp_path, master_path)` untuk melakukan rename atomik (bekerja di POSIX maupun Windows selama berada di filesystem yang sama), sehingga jika proses crash / di-kill / disk penuh di tengah penulisan, master file tetap berada dalam state lama yang valid (tidak pernah truncated / kosong / JSON corrupt)

2.7 WHEN aplikasi dijalankan di balik multi-worker WSGI server (gunicorn / uwsgi) dan dua atau lebih worker proses memanggil `_update_imap_config` bersamaan THEN the system SHALL melindungi siklus read-modify-write penuh terhadap master `imap_success.json` dengan cross-process file lock (mis. `filelock.FileLock("imap_success.json.lock")`) DI DALAM `self.imap_config_lock` (`threading.Lock`), sehingga hanya satu worker di seluruh proses yang dapat membaca-mengubah-menulis file dalam satu waktu, dan tidak ada update yang hilang

2.8 WHEN `_worker` membangun kriteria IMAP `SEARCH` dari `target_senders` atau `keywords` yang mengandung karakter `"` atau `\` THEN the system SHALL meng-escape karakter tersebut sesuai RFC 3501 (mengganti `\` → `\\` dan `"` → `\"`) sebelum disisipkan ke dalam quoted string `FROM "..."` atau `SUBJECT "..."`, sehingga server IMAP selalu menerima perintah `SEARCH` yang valid secara sintaksis dan akun tidak salah diklasifikasikan sebagai `die`

### Unchanged Behavior (Regression Prevention)

Semua perilaku lain dari `ImapChecker` dan aplikasi Flask harus tetap sama persis seperti sebelum perbaikan.

3.1 WHEN job IMAP check berjalan THEN the system SHALL CONTINUE TO menulis `live.txt`, `noemail.txt`, `die.txt`, `unreg.txt`, dan `domain_skipped.txt` di dalam folder `jobs/{job_id}/` (per job, tidak terpengaruh oleh perubahan ini)

3.2 WHEN `ImapChecker` mencoba mencari konfigurasi IMAP untuk sebuah domain THEN the system SHALL CONTINUE TO menggunakan urutan lookup yang sama: pertama `DEFAULT_IMAP_CONFIG` dari `imap_config.py`, lalu konfigurasi yang sudah dipelajari (sekarang dari master), lalu wildcard `*.rr.com`, terakhir auto-discovery via `_try_imap_variants`

3.3 WHEN `ImapChecker._update_imap_config` dipanggil dengan domain yang sudah ada di file dengan nilai konfigurasi yang sama persis THEN the system SHALL CONTINUE TO tidak menulis ulang file (idempotent, hindari penulisan disk yang tidak perlu)

3.4 WHEN format JSON file `imap_success.json` dibaca atau ditulis THEN the system SHALL CONTINUE TO menggunakan format dictionary `{ "<domain>": { "server": "...", "port": <int>, "ssl": <bool optional> } }` dengan `indent=2` dan encoding UTF-8 (kompatibel dengan `_merge_imap.py` dan `_populate_imap.py`)

3.5 WHEN file master `imap_success.json` di project root sudah ada dan berisi data sebelum perbaikan THEN the system SHALL CONTINUE TO menjaga seluruh entri yang sudah ada (tidak boleh menghapus atau menimpa entri yang tidak relevan dengan job yang sedang berjalan)

3.6 WHEN endpoint Flask `/api/check`, `/api/stop/<job_id>`, `/api/status/<job_id>`, `/api/download/<job_id>/<file_type>`, `/api/jobs`, `/api/jobs/<job_id>/extend`, `/api/jobs/<job_id>/delete`, `/api/jobs/<job_id>/live` dipanggil THEN the system SHALL CONTINUE TO berfungsi sama persis seperti sebelumnya (tidak ada perubahan kontrak API)

3.7 WHEN multiple thread di dalam satu `ImapChecker` memanggil `_update_imap_config` bersamaan THEN the system SHALL CONTINUE TO aman dari race condition (tidak ada partial write atau JSON corrupt)

3.8 WHEN script utilitas `_merge_imap.py` atau `_populate_imap.py` dijalankan THEN the system SHALL CONTINUE TO membaca/menulis ke master `imap_success.json` di project root dengan perilaku yang sama (tidak boleh dirusak oleh perubahan engine)

3.9 WHEN `target_senders` dan `keywords` hanya berisi karakter ASCII biasa tanpa `"` atau `\` (kasus normal — mis. `noreply@booking.com`, `@booking.com`, `verification`) THEN the system SHALL CONTINUE TO menghasilkan kriteria IMAP `SEARCH` yang sama persis seperti sebelum perbaikan (escape adalah no-op untuk input tanpa karakter spesial), sehingga semua akun yang tadinya `live`/`noemail`/`die` tetap diklasifikasikan dengan hasil yang sama

3.10 WHEN `target_senders` dan `keywords` kosong THEN the system SHALL CONTINUE TO mengikuti alur logika yang sama seperti sebelumnya (tidak ada `SEARCH` tambahan yang dipicu oleh perubahan escape)

3.11 WHEN `_update_imap_config` selesai menjalankan tulis atomik (tmp + `os.replace`) THEN the system SHALL CONTINUE TO meninggalkan master `imap_success.json` dengan isi JSON yang identik (key, value, urutan, indentasi `indent=2`, encoding UTF-8) seperti yang akan dihasilkan oleh implementasi lama bila tidak terjadi crash; file `.tmp` perantara harus terhapus / ter-rename habis (tidak menumpuk file `*.tmp` sisa)

3.12 WHEN tidak ada job lain yang sedang menulis master (kasus normal single-process, single-thread) THEN the system SHALL CONTINUE TO menyelesaikan `_update_imap_config` tanpa blocking yang berarti — penambahan file lock tidak boleh menambah latensi material atau mengakibatkan deadlock

3.13 WHEN file lock (`imap_success.json.lock`) tertinggal di disk setelah proses sebelumnya berakhir THEN the system SHALL CONTINUE TO bisa mengakuisisi lock tersebut pada job berikutnya (lock file boleh persisten; isinya tidak signifikan dan tidak boleh menjadi sumber konflik)

## Bug Condition (Pseudocode)

```pascal
FUNCTION isBugCondition(X)
  INPUT: X = (operation, results_dir, project_root, write_mode,
              process_lock_used, search_args)
         operation in {LOAD_IMAP_CONFIG, UPDATE_IMAP_CONFIG, IMAP_SEARCH}
         write_mode in {DIRECT_WRITE, ATOMIC_TMP_RENAME}
         process_lock_used in {NONE, FILE_LOCK}
         search_args = list of strings yang akan masuk ke FROM "..." / SUBJECT "..."
  OUTPUT: boolean

  // (a) Path master: setiap LOAD/UPDATE imap_success.json yang tidak menunjuk
  //     ke master di project_root adalah bug.
  IF operation IN {LOAD_IMAP_CONFIG, UPDATE_IMAP_CONFIG} THEN
    IF results_dir != project_root THEN
      RETURN true
    END IF

    // (b) Atomic write: UPDATE_IMAP_CONFIG yang tidak menulis lewat
    //     temp + os.replace adalah bug (resiko corrupt master saat crash).
    IF operation = UPDATE_IMAP_CONFIG AND write_mode != ATOMIC_TMP_RENAME THEN
      RETURN true
    END IF

    // (c) Cross-process lock: UPDATE_IMAP_CONFIG yang tidak dilindungi
    //     file lock antar proses adalah bug (lost update di multi-worker).
    IF operation = UPDATE_IMAP_CONFIG AND process_lock_used != FILE_LOCK THEN
      RETURN true
    END IF
  END IF

  // (d) IMAP search escaping: setiap IMAP_SEARCH yang argumennya mengandung
  //     karakter `"` atau `\` dan tidak di-escape sesuai RFC 3501 adalah bug.
  IF operation = IMAP_SEARCH THEN
    FOR EACH s IN search_args DO
      IF (contains(s, "\"") OR contains(s, "\\"))
         AND NOT properly_escaped_for_imap(s) THEN
        RETURN true
      END IF
    END FOR
  END IF

  RETURN false
END FUNCTION
```

## Property: Fix Checking

```pascal
// Untuk semua operasi yang memenuhi isBugCondition, F'(X) harus:
//  (a) memakai path master di project_root,
//  (b) menulis update via temp file + os.replace (atomik),
//  (c) memegang FILE_LOCK lintas-proses selama siklus read-modify-write,
//  (d) merge isi (tidak hilang entri lain),
//  (e) meng-escape karakter `\` dan `"` di argumen IMAP SEARCH.

FOR ALL X WHERE isBugCondition(X) DO

  // ---------- (a) Path master ----------
  IF X.operation IN {LOAD_IMAP_CONFIG, UPDATE_IMAP_CONFIG} THEN
    path ← F'(X).resolved_path
    ASSERT path = join(project_root, "imap_success.json")
  END IF

  // ---------- LOAD ----------
  IF X.operation = LOAD_IMAP_CONFIG THEN
    ASSERT F'(X) = read_json(join(project_root, "imap_success.json"))
  END IF

  // ---------- UPDATE ----------
  IF X.operation = UPDATE_IMAP_CONFIG THEN
    before ← read_json(path)

    // (b) atomic write: harus melalui tmp + rename
    trace ← record_filesystem_calls(F'(X))
    ASSERT trace contains write_to(path + ".tmp")
    ASSERT trace contains os.replace(path + ".tmp", path)
    ASSERT trace does NOT contain direct open(path, 'w')

    // (c) cross-process lock harus dipegang selama read-modify-write
    ASSERT FILE_LOCK("imap_success.json.lock") was acquired
           BEFORE the read of `path`
           AND released AFTER os.replace completes
    ASSERT threading.Lock (self.imap_config_lock) juga dipegang
           selama keseluruhan operasi UPDATE

    after ← read_json(path)

    // (d) merge: entri baru ada, entri lain utuh
    ASSERT after[X.domain] = X.config_data
    ASSERT FOR ALL d IN keys(before) WHERE d ≠ X.domain:
              after[d] = before[d]

    // tidak ada file .tmp tersisa
    ASSERT NOT exists(path + ".tmp")

    // crash safety: bila proses dimatikan kapan saja sebelum os.replace selesai,
    // isi `path` setelah restart harus tetap valid JSON dan equal ke `before`.
    FOR ALL kill_point IN possible_interruption_points(F'(X)) DO
      simulate_process_kill_at(kill_point)
      content ← read_raw(path)
      ASSERT is_valid_json(content)
      ASSERT parse_json(content) = before  -- tidak pernah corrupt / partial
    END FOR
  END IF

  // ---------- (e) IMAP SEARCH escaping ----------
  IF X.operation = IMAP_SEARCH THEN
    FOR EACH raw IN X.search_args DO
      escaped ← F'(X).escape(raw)
      ASSERT escaped = raw
                 .replace("\\", "\\\\")
                 .replace("\"", "\\\"")
      ASSERT criteria_string sent to IMAP server uses `escaped`
             inside quoted FROM "..." / SUBJECT "..."
    END FOR
    ASSERT IMAP server tidak menerima quoted-string yang malformed
  END IF

END FOR
```

## Property: Preservation Checking

```pascal
// Untuk semua perilaku yang TIDAK terkait baca/tulis imap_success.json
// dan TIDAK terkait IMAP SEARCH dengan argumen ber-karakter spesial
// (mis. menulis live.txt/die.txt, koneksi IMAP, parsing email, lookup
// DEFAULT_IMAP_CONFIG, auto-discovery, endpoint Flask, format JSON file
// master) — fungsi yang sudah diperbaiki harus berperilaku identik
// dengan fungsi sebelum perbaikan.
//
// Secara khusus:
//  - untuk argumen IMAP SEARCH tanpa `"` atau `\`, escape adalah no-op
//    sehingga kriteria yang dikirim ke server identik dengan sebelum fix;
//  - untuk hasil akhir master imap_success.json (saat tidak ada crash),
//    isi setelah F'(X) identik dengan isi yang akan dihasilkan oleh F(X)
//    (key, value, urutan merge, indent=2, encoding UTF-8).

FOR ALL X WHERE NOT isBugCondition(X) DO
  ASSERT F(X) = F'(X)
END FOR
```

## Counterexample (Demonstrates the Bug Today)

### Counterexample 1 — Konfigurasi domain baru hilang setelah job dihapus

1. Mulai job IMAP check dengan akun di domain baru (mis. `foo@some-new-isp.com`) yang belum ada di `DEFAULT_IMAP_CONFIG` maupun di master `imap_success.json`.
2. Engine menjalankan auto-discovery via `_try_imap_variants` dan menemukan konfigurasi yang valid.
3. `_update_imap_config("some-new-isp.com", {...})` dipanggil.
4. **Saat ini**: file `jobs/{job_id}/imap_success.json` dibuat dengan entri `some-new-isp.com`, sedangkan master `imap_success.json` di project root TIDAK berubah.
5. Setelah job dihapus / expired, folder `jobs/{job_id}` dihapus oleh `cleanup_expired_jobs` / `delete_job`, sehingga konfigurasi `some-new-isp.com` yang sempat ditemukan **hilang permanen** dan job berikutnya yang menemui domain itu lagi harus auto-discovery dari nol.

Setelah perbaikan, langkah 4 harus meng-update master `imap_success.json` di project root, dan langkah 5 tidak boleh menghilangkan entri tersebut.

### Counterexample 2 — Master file corrupt karena tulis tidak atomik

1. Master `imap_success.json` saat ini berisi 200+ entri domain (puluhan KB).
2. Job IMAP check sedang berjalan; salah satu thread memanggil `_update_imap_config(domain_baru, cfg)`.
3. Di dalam `with open(self.imap_output_file, 'w', ...) as f:` file segera ter-truncate jadi 0 byte. Sebelum `json.dump(current, f, indent=2)` selesai menulis seluruh isi (mis. baru menulis ~30% dari 200 entri), proses Python di-kill oleh OOM-killer / `systemctl stop imap-checker` / disk penuh / power loss.
4. **Saat ini**: file master tinggal berisi JSON terpotong (mis. `{"booking.com": {"server"...`) yang tidak valid. Di job berikutnya, `_load_imap_success_config` memanggil `json.load(...)` → exception → fallback `return {}` → seluruh 200+ entri yang sudah dipelajari **hilang permanen**.

Setelah perbaikan (langkah 3 menggunakan `imap_success.json.tmp` + `os.replace`), kill kapan pun di tengah penulisan akan menyisakan master file **dalam state lama yang valid** (tidak pernah truncated), dan `imap_success.json.tmp` yang setengah-jadi tidak pernah dianggap sebagai master.

### Counterexample 3 — Lost update karena tidak ada cross-process lock

1. Aplikasi dijalankan via `gunicorn -w 4 app:app` (4 worker proses Python terpisah).
2. Worker A memproses akun di `domain-A.com`; Worker B memproses akun di `domain-B.com`. Pada saat hampir bersamaan keduanya ingin meng-update master.
3. `self.imap_config_lock` (`threading.Lock`) tidak berarti antar proses, jadi tidak ada serialisasi:
   - T0: Worker A memuat master → in-memory copy `M_A` (199 entri).
   - T1: Worker B memuat master → in-memory copy `M_B` (199 entri, sama dengan M_A).
   - T2: Worker A menambahkan `domain-A.com` ke `M_A`, menulis (200 entri: original 199 + A).
   - T3: Worker B menambahkan `domain-B.com` ke `M_B`, menulis (200 entri: original 199 + B). **Update Worker A ter-overwrite** — `domain-A.com` hilang dari master.
4. **Saat ini**: salah satu domain selalu hilang setiap kali dua worker meng-update bersamaan. Bug ini sulit terlihat karena tampak seperti hasil discovery yang flaky.

Setelah perbaikan (langkah 3 dilindungi `filelock.FileLock("imap_success.json.lock")` di sekeliling read+modify+atomic-write), Worker B akan blocking sampai Worker A selesai, sehingga `M_B` yang dibaca Worker B sudah berisi `domain-A.com`, dan kedua entri tetap ada di master akhir.

### Counterexample 4 — IMAP SEARCH error karena karakter tidak di-escape

1. User memasukkan keyword `say "hi"` (mengandung `"`) atau target sender `weird"name@example.com`.
2. `_worker` membangun:
   ```
   criteria = f'(SINCE "01-Jan-2024" SUBJECT "say "hi"")'
   ```
   Kriteria ini melanggar RFC 3501 — quoted string IMAP harus meng-escape `"` dengan `\"`. Server IMAP merespons `BAD` atau `NO`.
3. **Saat ini**: `mail_conn.search(...)` melempar exception, ditangkap oleh `except Exception:` di `_worker`, lalu akun ditulis ke `die.txt` walaupun login dan password sebenarnya valid — akun **salah diklasifikasikan**.

Setelah perbaikan (helper `_escape_imap_string(s)` mengganti `\` → `\\` dan `"` → `\"`, dipakai sebelum interpolasi), kriteria menjadi `(SINCE "01-Jan-2024" SUBJECT "say \"hi\"")`, server menerima command yang valid, dan akun diklasifikasikan ke `live`/`noemail` sesuai isi inbox sebenarnya.
