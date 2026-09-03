# Mail Console — VPS Deployment (Ubuntu 24.04)

Web-based mail utility dengan admin panel, dedup, multi-thread (1-1000), dan filter subject yang konfigurabel. Homepage publik hanya menampilkan "Hello World"; seluruh fungsi tool disembunyikan di balik URL rahasia.

**Repo:** https://github.com/bibnk/ikan-cupang
**Tutorial lengkap:** lihat [TUTORIAL.md](./TUTORIAL.md)

---

## 🚀 Quick Deploy (via Git Clone)

### Di VPS:

```bash
ssh root@IP_VPS_KAMU
apt install -y git
cd ~
git clone https://github.com/bibnk/ikan-cupang.git imap-checker-vps
cd imap-checker-vps
chmod +x setup.sh
./setup.sh
```

Setelah selesai:
- `http://IP_VPS_KAMU/` → **halaman publik "Hello World"** (aman dibagikan, tidak ada jejak tool).
- `http://IP_VPS_KAMU/sbb/` → **tool asli** (login dengan kode akses default `sbb`).

Path tool default-nya `/sbb`. Kalau mau ganti, set env `SECRET_PATH` atau tulis
segmen yang diinginkan ke `/opt/imap-checker/.secret_path` lalu restart service.

> 💡 Setup script auto-handle: install Python/Nginx, buat user `imapchecker`, copy ke `/opt/imap-checker/`, virtualenv, set permission CSS/static, install systemd unit (LimitNOFILE=16384, threads=32), nginx HTTP-only proxy, firewall.

---

## 🔄 Update via Git Pull

```bash
cd ~/imap-checker-vps && git pull
# Lalu jalankan one-liner sync di TUTORIAL.md (section "Cara Update Aplikasi")
```

State files (`imap_success.json`, `access_codes.json`, `.secret_key`, `skip_domains.json`, `subject_exclusion_list.json`) tidak ada di repo (sudah di-`.gitignore`), jadi `git pull` aman dan tidak akan menimpa data live di VPS.

---

## ✨ Fitur Utama

| Fitur | Keterangan |
|-------|------------|
| **IMAP checker** | Cek email:pass massal dengan auto-detect domain config (200+ provider) |
| **Multi-threading** | 1-1000 threads per job (default 500), tuned untuk high concurrency |
| **Email dedup** | Otomatis hapus duplikat email (case-insensitive) sebelum cek, banner UI menampilkan jumlah duplikat |
| **Skip domains** | Admin-editable list domain yang di-skip (default: hotmail, yahoo, outlook, dll.) |
| **Subject exclusion** | Admin-editable list pattern subject untuk filter `live.txt` (mendukung emoji/symbol/multi-pattern) |
| **Master config sentralisasi** | `imap_success.json` di project root, atomic write dengan FileLock |
| **Subject keyword match** | Email yang match keyword subject akan masuk `live.txt` walau tanpa target sender |
| **Get email** | Ambil isi email tertentu dari inbox |
| **Loop delete** | Hapus email otomatis berkala |
| **Admin panel** | Kelola access codes, skip-domains, subject-exclusion list |

---

## 📋 Struktur Deployment

| File | Fungsi |
|------|--------|
| `app.py` | Aplikasi Flask utama |
| `imap_engine.py` | Engine cek IMAP + dedup + skip-domain + subject-exclusion |
| `imap_config.py` | DEFAULT_IMAP_CONFIG (200+ domain) |
| `get_email_engine.py` | Engine ambil email |
| `loop_delete_engine.py` | Engine hapus email otomatis |
| `setup.sh` | One-click installer untuk Ubuntu 24.04 |
| `imap-checker.service` | Systemd service (auto-start, LimitNOFILE=16384) |
| `nginx.conf` | Reverse proxy HTTP-only (HTTPS via certbot) |
| `requirements.txt` | flask, gunicorn, filelock |
| `static/` | CSS + JavaScript |
| `templates/` | HTML templates (index, admin, login, dst.) |
| `.gitignore` | Excludes state files dan cache |

State files (auto-generated saat runtime, **tidak di repo**):
- `.secret_key`, `access_codes.json`, `imap_success.json`
- `skip_domains.json`, `subject_exclusion_list.json`
- `jobs/` (hasil per-job)

---

## 🔧 Perintah Berguna

```bash
# Status
sudo systemctl status imap-checker

# Restart / Stop / Start
sudo systemctl restart imap-checker
sudo systemctl stop imap-checker
sudo systemctl start imap-checker

# Log real-time
sudo journalctl -u imap-checker -f

# Log file
tail -f /opt/imap-checker/error.log
tail -f /opt/imap-checker/access.log
```

---

## 🔐 Default Access Code

```
sbb
```

⚠️ **Wajib ganti di production**. Login → Admin → Daftar Kode Akses → tambah kode baru → hapus `sbb`.

---

## 🌐 HTTPS (Opsional)

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d yourdomain.com
```

Certbot otomatis edit nginx config menambah HTTPS block + redirect HTTP→HTTPS + setup auto-renewal.

---

## 📚 Dokumentasi Lengkap

Untuk panduan detail (manual setup, admin panel features, backup, troubleshooting, threading tuning, dll.) lihat **[TUTORIAL.md](./TUTORIAL.md)**.

---

## 🐛 Bug Tracking & Spec

Project ini dikembangkan dengan workflow spec-driven (`.kiro/specs/`):

| Spec | Status |
|------|--------|
| `centralize-imap-success-master` | ✅ Done — master file atomic + FileLock |
| `subject-keyword-match-shown-in-live` | ✅ Done — fix subject-keyword tidak muncul di live |
| `custom-skip-domains` | ✅ Done — admin-editable skip list |
| `subject-exclusion-list` | ✅ Done — admin-editable subject filter |

Total 120/120 tests pass.
