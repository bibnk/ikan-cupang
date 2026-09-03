# 📖 Tutorial Instalasi IMAP Checker di VPS Ubuntu 24.04

Tutorial lengkap install, deploy, dan operasi aplikasi **IMAP Checker** di VPS Ubuntu via GitHub.

**Repo:** https://github.com/bibnk/ikan-cupang

---

## 📋 Persyaratan

| Item | Keterangan |
|------|------------|
| **VPS** | Ubuntu 24.04 LTS (minimal RAM 1 GB) |
| **Akses** | Root atau user dengan sudo |
| **Koneksi** | Internet aktif di VPS |
| **Domain** *(opsional)* | Untuk HTTPS via Let's Encrypt |

---

## 🚀 Cara Cepat (One-Click Setup via Git Clone)

### Langkah 1 — SSH ke VPS

```bash
ssh root@IP_VPS_KAMU
```

### Langkah 2 — Clone Repo dari GitHub

```bash
apt install -y git
cd ~
git clone https://github.com/bibnk/ikan-cupang.git imap-checker-vps
cd imap-checker-vps
```

### Langkah 3 — Jalankan Installer

```bash
chmod +x setup.sh
./setup.sh
```

Script akan otomatis:
1. Update sistem (`apt update && upgrade`)
2. Install Python 3, pip, venv, Nginx
3. Buat user khusus `imapchecker`
4. Copy file ke `/opt/imap-checker/`
5. Buat virtual environment + install dependency (flask, gunicorn, filelock)
6. Set permission supaya Nginx bisa serve static files (CSS/JS)
7. Install + start systemd service (dengan LimitNOFILE=16384, threads=32)
8. Konfigurasi Nginx reverse proxy (HTTP-only, HTTPS belakangan via certbot)
9. Buka firewall (Nginx Full + OpenSSH)

Setelah selesai akan muncul:

```
======================================
 ✅ Setup Complete!
======================================
 App running at: http://IP_VPS:5000
 Nginx proxy at: http://IP_VPS
 Default access code: sbb
======================================
```

### Langkah 4 — Akses Aplikasi

Buka browser dan akses `http://IP_VPS_KAMU`. Login dengan kode akses `sbb` (default admin).

---

## 🔄 Cara Update Aplikasi (Git Pull)

Workflow upgrade kalau ada perubahan kode di repo:

### Di VPS (folder staging):

```bash
cd ~/imap-checker-vps
git pull
```

### Sync ke folder live:

```bash
# Stop service
sudo systemctl stop imap-checker

# Sync HANYA file kode (bukan state JSON files)
sudo cp ~/imap-checker-vps/app.py \
        ~/imap-checker-vps/imap_engine.py \
        ~/imap-checker-vps/imap_config.py \
        ~/imap-checker-vps/get_email_engine.py \
        ~/imap-checker-vps/loop_delete_engine.py \
        /opt/imap-checker/
sudo cp ~/imap-checker-vps/static/* /opt/imap-checker/static/
sudo cp ~/imap-checker-vps/templates/* /opt/imap-checker/templates/

# Update dependency kalau requirements.txt berubah
sudo /opt/imap-checker/venv/bin/pip install -r ~/imap-checker-vps/requirements.txt

# Update systemd unit / nginx kalau berubah
sudo cp ~/imap-checker-vps/imap-checker.service /etc/systemd/system/
sudo cp ~/imap-checker-vps/nginx.conf /etc/nginx/sites-available/imap-checker
sudo systemctl daemon-reload
sudo nginx -t && sudo systemctl reload nginx

# Fix permission + restart
sudo chown -R imapchecker:imapchecker /opt/imap-checker
sudo chmod -R a+rX /opt/imap-checker/static /opt/imap-checker/templates
sudo systemctl start imap-checker
sudo systemctl status imap-checker
```

> ⚠️ **JANGAN** `cp -r ~/imap-checker-vps/* /opt/imap-checker/` karena akan menimpa state files (`imap_success.json`, `access_codes.json`, `.secret_key`, dst.). State files **tidak ada di repo** (di-`.gitignore`) supaya aman dari overwrite.

### Workflow Singkat (one-liner):

```bash
cd ~/imap-checker-vps && git pull && sudo systemctl stop imap-checker && \
  sudo cp app.py imap_engine.py imap_config.py get_email_engine.py loop_delete_engine.py /opt/imap-checker/ && \
  sudo cp static/* /opt/imap-checker/static/ && \
  sudo cp templates/* /opt/imap-checker/templates/ && \
  sudo cp imap-checker.service /etc/systemd/system/ && \
  sudo /opt/imap-checker/venv/bin/pip install -r requirements.txt -q && \
  sudo systemctl daemon-reload && \
  sudo chown -R imapchecker:imapchecker /opt/imap-checker && \
  sudo chmod -R a+rX /opt/imap-checker/static /opt/imap-checker/templates && \
  sudo systemctl start imap-checker && \
  sudo systemctl status imap-checker --no-pager
```

---

## 🎛️ Fitur Admin Panel

Login sebagai admin (`sbb`) dan buka menu **Admin** untuk mengelola tiga konfigurasi global:

### 1. Daftar Kode Akses
Tambah/hapus kode login pengguna. Tiap kode bisa dikasih label.

### 2. 🚫 Skip Domains
Daftar substring pattern yang dipakai untuk skip akun berdasarkan domain emailnya. Substring match, case-insensitive.

- **Default seed** (auto-create di job pertama): `hotmail`, `live`, `msn`, `outlook`, `yahoo`, `interia`, `poczta.fm`
- **Edit**: tulis satu pattern per baris di textarea, klik **Simpan**
- **Reset ke default**: hapus file `/opt/imap-checker/skip_domains.json` di server, daftar otomatis re-seed di job berikutnya

Akun dengan domain yang memuat salah satu pattern (mis. `user@hotmail.com`) akan masuk ke `domain_skipped.txt` tanpa di-cek.

### 3. 🛑 Subject Exclusion List
Daftar substring pattern yang dipakai untuk exclude email dari `live.txt`. Substring match, case-insensitive, **mendukung emoji/symbol**.

- **Default seed**: `is your verification code` (kompatibilitas dengan filter OTP Booking.com lama)
- **Edit**: satu pattern per baris di textarea, klik **Simpan**
- **Multi-pattern**: bisa lebih dari satu pattern, OR-logic
- **Reset ke default**: hapus file `/opt/imap-checker/subject_exclusion_list.json`, daftar re-seed di job berikutnya

Email yang Subject-nya memuat salah satu pattern akan disembunyikan dari `live.txt`.

### Validasi Input
Semua entry di-normalisasi otomatis (`strip` + `lower` + dedupe). Entry yang kosong, terlalu panjang (>255 untuk skip-domains, >500 untuk subject-exclusion), atau mengandung whitespace internal terlarang akan ditolak dengan pesan error spesifik.

### 4. ⚙️ High-Concurrency Support (1-1000 threads)
Field **Threads** di halaman utama menerima 1-1000 (default: 500). systemd unit di-tune untuk:
- `LimitNOFILE=16384` — 16k file descriptors
- `LimitNPROC=8192` — 8k thread limit
- Gunicorn `--threads 32`, `--timeout 600`

> ⚠️ Saat menjalankan 500-1000 thread, Anda menerima **risiko rate-limit / IP block** dari beberapa provider mail (Yahoo, Outlook, dll.). Naikkan secara bertahap kalau baru pertama kali.

### 5. ✂️ Email Deduplication
Saat upload akun untuk dicek:
- Hanya format `email:password` yang diterima (line lain di-drop)
- Email duplikat (case-insensitive) di-drop, **walaupun password-nya beda** — yang dipertahankan adalah occurrence pertama
- Banner kuning muncul di UI menampilkan "X duplikat dihapus" (auto-hide 8 detik)

---

## ⚙️ Perintah Operasi Sehari-hari

### Status & Kontrol Service
```bash
sudo systemctl status imap-checker     # Cek status
sudo systemctl restart imap-checker    # Restart
sudo systemctl stop imap-checker       # Stop
sudo systemctl start imap-checker      # Start
```

### Log
```bash
sudo journalctl -u imap-checker -f     # Real-time
sudo journalctl -u imap-checker -n 100 # 100 baris terakhir
tail -f /opt/imap-checker/error.log    # File error log
tail -f /opt/imap-checker/access.log   # File access log
```

### Cek File Konfigurasi Aktif
```bash
# State files (boleh diedit langsung kalau perlu, tapi pakai admin UI lebih aman)
cat /opt/imap-checker/skip_domains.json
cat /opt/imap-checker/subject_exclusion_list.json
cat /opt/imap-checker/access_codes.json

# Master config IMAP yang sudah dipelajari (200+ domain)
ls -lh /opt/imap-checker/imap_success.json
```

---

## 🌐 Setup HTTPS (Opsional tapi Direkomendasikan)

Kalau Anda punya domain yang sudah diarahkan ke IP VPS:

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d domainmu.com
```

Ikuti instruksi di layar (email + agree TOS + redirect HTTP → HTTPS). Certbot akan:
1. Validasi kepemilikan domain via HTTP-01 challenge
2. Generate sertifikat SSL gratis dari Let's Encrypt
3. **Auto-edit `/etc/nginx/sites-available/imap-checker`** untuk menambah block HTTPS + redirect HTTP→HTTPS — Anda **tidak perlu** edit manual
4. Setup auto-renewal (test: `sudo certbot renew --dry-run`)

> 💡 `nginx.conf` yang dibundle di repo sengaja HTTP-only supaya `setup.sh` di fresh VPS tidak gagal karena reference ke sertifikat yang belum ada. HTTPS dikonfigurasi belakangan via certbot.

---

## 🔐 Keamanan Production

### Ganti Default Access Code

Default code `sbb` adalah admin. Setelah deploy:
1. Login dengan `sbb`
2. Buka **Admin** > Daftar Kode Akses
3. Tambah kode baru untuk admin Anda
4. Hapus kode `sbb` (atau rename label-nya)

### Backup State Files Berkala

State files yang penting (jangan sampai hilang) dan **tidak ada di GitHub**:
- `imap_success.json` — 200+ entri domain yang sudah dipelajari engine
- `access_codes.json` — daftar user
- `skip_domains.json`, `subject_exclusion_list.json` — daftar konfigurasi admin
- `.secret_key` — kalau hilang, semua session user akan invalid

Backup sederhana via cron (setiap jam):
```bash
sudo crontab -e
# Tambah baris ini:
0 * * * * tar czf /root/backup-imap-$(date +\%Y\%m\%d-\%H).tar.gz -C /opt/imap-checker .secret_key access_codes.json imap_success.json skip_domains.json subject_exclusion_list.json 2>/dev/null
```

### Firewall

Pastikan hanya port yang diperlukan terbuka:
```bash
sudo ufw status
# Yang seharusnya ada:
# - OpenSSH (22)
# - Nginx Full (80 + 443)
sudo ufw enable  # Kalau belum aktif
```

Port 5000 (Flask internal) **tidak perlu** dibuka ke luar — sudah di-proxy oleh Nginx.

---

## ❓ Troubleshooting

### Aplikasi Tidak Bisa Diakses

```bash
sudo systemctl status imap-checker
sudo journalctl -u imap-checker -n 50 --no-pager
sudo systemctl status nginx
sudo nginx -t
sudo ufw status
```

### Service Gagal Start

```bash
sudo journalctl -u imap-checker -n 100 --no-pager

# Coba jalankan manual untuk lihat traceback Python
sudo -u imapchecker /opt/imap-checker/venv/bin/python /opt/imap-checker/app.py
```

Kalau muncul error `ModuleNotFoundError: No module named 'filelock'`, install ulang dependency:
```bash
sudo /opt/imap-checker/venv/bin/pip install -r /opt/imap-checker/requirements.txt
sudo systemctl restart imap-checker
```

### CSS / Static Files 404

Setup script sudah meng-handle permission, tapi kalau muncul lagi:
```bash
sudo chmod 755 /opt /opt/imap-checker
sudo chmod -R a+rX /opt/imap-checker/static /opt/imap-checker/templates
sudo systemctl reload nginx
```

### Port 80 Sudah Dipakai

```bash
sudo lsof -i :80
sudo fuser -k 80/tcp
sudo systemctl restart nginx
```

### Skip-Domains / Subject-Exclusion Tidak Tersimpan

Cek file lock yang stale:
```bash
sudo systemctl stop imap-checker
sudo rm -f /opt/imap-checker/*.lock
sudo systemctl start imap-checker
```

Cek isi file langsung:
```bash
sudo cat /opt/imap-checker/skip_domains.json
sudo cat /opt/imap-checker/subject_exclusion_list.json
```

### Master imap_success.json Corrupt

Kalau setelah restart aplikasi tampak "kehilangan" config domain:
```bash
sudo /opt/imap-checker/venv/bin/python -c "import json; print(len(json.load(open('/opt/imap-checker/imap_success.json'))))"
```
Kalau muncul `JSONDecodeError`, restore dari backup:
```bash
sudo cp ~/backup-imap-YYYYMMDD-HH.tar.gz /tmp/
cd /tmp && tar xzf backup-imap-*.tar.gz
sudo cp imap_success.json /opt/imap-checker/
sudo chown imapchecker:imapchecker /opt/imap-checker/imap_success.json
sudo systemctl restart imap-checker
```

### IMAP Lemot di VPS Padahal Lokal Cepat

Beberapa penyebab umum:
1. VPS region jauh dari mail server target (latency RTT tinggi). Pilih VPS region terdekat ke mayoritas mail server (mis. AS untuk Gmail/Outlook, EU untuk Yahoo/Interia).
2. Threads terlalu sedikit. Naikkan ke 500 (default sudah 500).
3. ISP VPS rate-limit IMAP outbound. Coba VPS provider lain (DigitalOcean, Vultr, Hetzner umumnya OK).

---

## 📁 Lokasi File Penting di VPS

| Path | Keterangan |
|------|-----------|
| `/opt/imap-checker/` | Folder utama aplikasi |
| `/opt/imap-checker/venv/` | Python virtual environment |
| `/opt/imap-checker/jobs/` | Hasil pengecekan per-job (live.txt, die.txt, dll.) |
| `/opt/imap-checker/imap_success.json` | Master config domain (auto-update) |
| `/opt/imap-checker/skip_domains.json` | User-editable skip list |
| `/opt/imap-checker/subject_exclusion_list.json` | User-editable subject exclusion list |
| `/opt/imap-checker/access_codes.json` | Daftar kode akses |
| `/opt/imap-checker/.secret_key` | Flask session key (jangan share) |
| `/opt/imap-checker/access.log` | Log akses HTTP (gunicorn) |
| `/opt/imap-checker/error.log` | Log error aplikasi |
| `/etc/systemd/system/imap-checker.service` | File service systemd |
| `/etc/nginx/sites-available/imap-checker` | Konfigurasi Nginx |
| `/etc/letsencrypt/live/<domain>/` | Sertifikat SSL (kalau pakai HTTPS) |
| `~/imap-checker-vps/` | Folder staging hasil `git clone` (untuk update via git pull) |

---

## ✅ Ringkasan Quick-Deploy

```bash
# Di VPS:
ssh root@IP_VPS
apt install -y git
cd ~
git clone https://github.com/bibnk/ikan-cupang.git imap-checker-vps
cd imap-checker-vps
chmod +x setup.sh
./setup.sh

# Lalu buka http://IP_VPS, login dengan `sbb`. Done.
```

## ✅ Ringkasan Quick-Update

```bash
# Di VPS:
ssh root@IP_VPS
cd ~/imap-checker-vps && git pull
# Jalankan one-liner sync di section "Cara Update Aplikasi"
```
