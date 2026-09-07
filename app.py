# app.py
# Flask web app for IMAP Email Checker + Get Email + Access Code Login

import os
import uuid
import shutil
from datetime import datetime, timedelta
import time
import json
import hashlib
from functools import wraps
from flask import Flask, render_template, request, jsonify, Response, send_file, session, redirect, url_for

from imap_engine import (
    ImapChecker,
    parse_accounts,
    parse_senders,
    parse_proxies,
    _load_skip_domains,
    _save_skip_domains,
    _validate_skip_entries,
    _load_subject_exclusion_list,
    _save_subject_exclusion_list,
    _validate_exclusion_entries,
)
from filelock import Timeout
from get_email_engine import fetch_emails, delete_email, list_folders, list_folder_emails, get_single_email
from loop_delete_engine import LoopDeleteJob
from unreg_resolver import resolve_unreg_domains

app = Flask(__name__)


@app.context_processor
def _inject_base():
    # Under DispatcherMiddleware the tool is mounted at a secret prefix.
    # request.script_root == that prefix ("" when run standalone), so all
    # front-end fetch() calls can be made prefix-aware via window.APP_BASE.
    from flask import request
    return {"app_base": request.script_root or ""}

# Production: persist secret key so sessions survive restarts
_key_file = os.path.join(os.path.dirname(__file__), '.secret_key')
if os.path.exists(_key_file):
    with open(_key_file, 'r') as f:
        app.secret_key = f.read().strip()
else:
    app.secret_key = os.urandom(32).hex()
    with open(_key_file, 'w') as f:
        f.write(app.secret_key)

# ---- Access Code System ----
CODES_FILE = os.path.join(os.path.dirname(__file__), "access_codes.json")


def hash_code(code):
    return hashlib.sha256(code.encode()).hexdigest()


def load_codes():
    if os.path.exists(CODES_FILE):
        try:
            with open(CODES_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    # Default: "sbb"
    default = [{"code_hash": hash_code("sbb"), "label": "default"}]
    save_codes(default)
    return default


def save_codes(codes):
    with open(CODES_FILE, 'w') as f:
        json.dump(codes, f, indent=2)


def verify_code(code):
    codes = load_codes()
    h = hash_code(code)
    for c in codes:
        if c["code_hash"] == h:
            return c  # Return the full code entry (hash + label)
    return None


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("authenticated"):
            if request.is_json or request.path.startswith("/api/"):
                return jsonify({"error": "Unauthorized"}), 401
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return decorated


# ---- Store active jobs ----
jobs = {}  # {job_id: {"checker": ImapChecker, "created_at": datetime, "expires_at": datetime, "meta": {...}}}
JOBS_DIR = os.path.join(os.path.dirname(__file__), "jobs")
os.makedirs(JOBS_DIR, exist_ok=True)
DEFAULT_EXPIRY_DAYS = 3
EXTEND_DAYS = 7


def cleanup_expired_jobs():
    """Remove jobs older than their expiry time."""
    now = datetime.now()
    expired = [jid for jid, jdata in jobs.items() if now > jdata["expires_at"]]
    for jid in expired:
        jdata = jobs.pop(jid)
        checker = jdata.get("checker")
        if checker and hasattr(checker, 'stop'):
            try:
                checker.stop()
            except Exception:
                pass
        # clean up files
        job_dir = os.path.join(JOBS_DIR, jid)
        if os.path.isdir(job_dir):
            shutil.rmtree(job_dir, ignore_errors=True)


# ---- Login ----

@app.route("/login", methods=["GET", "POST"])
def login_page():
    if request.method == "POST":
        code = request.form.get("code", "").strip()
        code_entry = verify_code(code)
        if code_entry:
            session["authenticated"] = True
            session["is_admin"] = (code == "sbb")
            session["code_hash"] = code_entry["code_hash"]
            session["user_label"] = code_entry.get("label", "unknown")
            return redirect(url_for("index"))
        return render_template("login.html", error="Kode akses salah!")
    return render_template("login.html", error=None)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))


# ---- Pages ----

@app.route("/")
@login_required
def index():
    return render_template("index.html", is_admin=session.get("is_admin", False))


@app.route("/get-email")
@login_required
def get_email_page():
    return render_template("get_email.html", is_admin=session.get("is_admin", False))


@app.route("/admin")
@login_required
def admin_page():
    if not session.get("is_admin"):
        return redirect(url_for("index"))
    codes = load_codes()
    return render_template("admin.html", codes=codes, is_admin=True)


@app.route("/api/admin/add-code", methods=["POST"])
@login_required
def add_code():
    if not session.get("is_admin"):
        return jsonify({"error": "Admin only"}), 403
    data = request.get_json()
    new_code = data.get("code", "").strip()
    label = data.get("label", "").strip() or "custom"
    if not new_code:
        return jsonify({"error": "Kode tidak boleh kosong"}), 400
    if len(new_code) < 3:
        return jsonify({"error": "Kode minimal 3 karakter"}), 400

    codes = load_codes()
    h = hash_code(new_code)
    if any(c["code_hash"] == h for c in codes):
        return jsonify({"error": "Kode sudah ada"}), 400

    codes.append({"code_hash": h, "label": label})
    save_codes(codes)
    return jsonify({"success": True, "message": f"Kode '{label}' berhasil ditambahkan"})


@app.route("/api/admin/delete-code", methods=["POST"])
@login_required
def delete_code():
    if not session.get("is_admin"):
        return jsonify({"error": "Admin only"}), 403
    data = request.get_json()
    idx = data.get("index", -1)
    codes = load_codes()
    if idx < 0 or idx >= len(codes):
        return jsonify({"error": "Index tidak valid"}), 400
    removed = codes.pop(idx)
    save_codes(codes)
    return jsonify({"success": True, "message": f"Kode '{removed.get('label', '')}' dihapus"})


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


# ---- IMAP Checker API ----

@app.route("/api/check", methods=["POST"])
@login_required
def start_check():
    cleanup_expired_jobs()
    data = request.get_json()

    accounts_text = data.get("accounts", "").strip()
    senders_text = data.get("senders", "").strip()
    keywords_text = data.get("keywords", "").strip()
    proxies_text = data.get("proxies", "").strip()
    search_days = int(data.get("search_days", 365))
    max_threads = int(data.get("max_threads", 20))

    if not accounts_text:
        return jsonify({"error": "Accounts tidak boleh kosong"}), 400
    if not senders_text and not keywords_text:
        return jsonify({"error": "Isi minimal salah satu: Target Sender atau Keyword"}), 400

    # Count raw non-empty input lines BEFORE parsing for dedup-stats feedback.
    raw_input_lines = sum(
        1 for line in accounts_text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    )
    accounts = parse_accounts(accounts_text)
    duplicates_removed = max(0, raw_input_lines - len(accounts))

    senders = parse_senders(senders_text) if senders_text else []
    keywords = [k.strip() for k in keywords_text.splitlines() if k.strip()] if keywords_text else []
    proxy_list = parse_proxies(proxies_text) if proxies_text else []

    if not accounts:
        return jsonify({"error": "Tidak ditemukan akun valid. Format: email:password"}), 400

    max_threads = max(1, min(max_threads, 1000))
    search_days = max(1, min(search_days, 3650))

    job_id = str(uuid.uuid4())[:8]
    results_dir = os.path.join(JOBS_DIR, job_id)
    now = datetime.now()

    checker = ImapChecker(
        job_id=job_id,
        results_dir=results_dir,
        accounts=accounts,
        target_senders=senders,
        keywords=keywords,
        search_days=search_days,
        max_threads=max_threads,
        proxy_list=proxy_list,
    )

    jobs[job_id] = {
        "checker": checker,
        "created_at": now,
        "expires_at": now + timedelta(days=DEFAULT_EXPIRY_DAYS),
        "owner": session.get("code_hash", ""),
        "owner_label": session.get("user_label", "unknown"),
        "meta": {
            "total_accounts": len(accounts),
            "total_senders": len(senders),
            "total_keywords": len(keywords),
            "total_proxies": len(proxy_list),
            "senders_text": senders_text[:100],
            "keywords_text": keywords_text[:100],
        },
    }
    checker.run()

    return jsonify({
        "job_id": job_id,
        "total_accounts": len(accounts),
        "total_senders": len(senders),
        "total_keywords": len(keywords),
        "total_proxies": len(proxy_list),
        "duplicates_removed": duplicates_removed,
    })


@app.route("/api/stop/<job_id>", methods=["POST"])
@login_required
def stop_check(job_id):
    jdata = jobs.get(job_id)
    if not jdata:
        return jsonify({"error": "Job not found"}), 404
    jdata["checker"].stop()
    return jsonify({"message": "Stop signal sent", "job_id": job_id})


@app.route("/api/status/<job_id>")
@login_required
def job_status(job_id):
    jdata = jobs.get(job_id)
    if not jdata:
        return jsonify({"error": "Job not found"}), 404
    checker = jdata["checker"]

    def generate():
        while True:
            progress = checker.get_progress()
            yield f"data: {json.dumps(progress)}\n\n"
            if progress["status"] in ("done", "stopped"):
                break
            time.sleep(0.5)

    return Response(generate(), mimetype="text/event-stream")


@app.route("/api/download/<job_id>/<file_type>")
@login_required
def download_file(job_id, file_type):
    allowed = {
        "live": "live.txt",
        "noemail": "noemail.txt",
        "die": "die.txt",
        "unreg": "unreg.txt",
        "domain_skipped": "domain_skipped.txt",
    }
    if file_type not in allowed:
        return jsonify({"error": "Invalid file type"}), 400

    filepath = os.path.abspath(os.path.join(JOBS_DIR, job_id, allowed[file_type]))
    if not os.path.exists(filepath):
        return jsonify({"error": "File belum tersedia atau kosong"}), 404

    return send_file(filepath, as_attachment=True,
                     download_name=f"{file_type}_{job_id}.txt", mimetype="text/plain")


# ---- Resolve Unreg API ----

# Track resolve jobs
resolve_jobs = {}


@app.route("/api/resolve-unreg/<job_id>", methods=["POST"])
@login_required
def api_resolve_unreg(job_id):
    """Resolve unreg domains from a job's unreg.txt file."""
    # Check if already running
    if job_id in resolve_jobs and resolve_jobs[job_id].get("status") == "running":
        return jsonify({"error": "Resolve sudah berjalan untuk job ini"}), 400

    unreg_path = os.path.join(JOBS_DIR, job_id, "unreg.txt")
    if not os.path.exists(unreg_path):
        return jsonify({"error": "File unreg.txt tidak ditemukan"}), 404

    with open(unreg_path, "r", encoding="utf-8") as f:
        domains = [line.strip() for line in f if line.strip()]

    if not domains:
        return jsonify({"error": "Tidak ada domain unreg", "added": 0}), 200

    resolve_jobs[job_id] = {
        "status": "running",
        "total": len(domains),
        "phase": "starting",
        "checked": 0,
        "found": 0,
    }

    def run_resolve():
        def progress_cb(phase, checked, total, found):
            resolve_jobs[job_id].update({
                "phase": phase,
                "checked": checked,
                "phase_total": total,
                "found": found,
            })

        try:
            result = resolve_unreg_domains(domains, progress_callback=progress_cb)
            resolve_jobs[job_id].update({
                "status": "done",
                "phase": "done",
                "added": result["added"],
                "mx_mapped": result.get("mx_mapped", 0),
                "direct_found": result.get("direct_found", 0),
                "total_config": result["total_config"],
            })
        except Exception as e:
            resolve_jobs[job_id].update({
                "status": "error",
                "error": str(e),
            })

    import threading
    t = threading.Thread(target=run_resolve, daemon=True)
    t.start()

    return jsonify({"message": "Resolve dimulai", "total_domains": len(domains)})


@app.route("/api/resolve-unreg-status/<job_id>")
@login_required
def api_resolve_unreg_status(job_id):
    """Get resolve progress."""
    rjob = resolve_jobs.get(job_id)
    if not rjob:
        return jsonify({"status": "idle"})
    return jsonify(rjob)


# ---- Get Email API ----

@app.route("/api/get-email", methods=["POST"])
@login_required
def api_get_email():
    data = request.get_json()

    email_addr = data.get("email", "").strip()
    password = data.get("password", "").strip()
    senders_text = data.get("senders", "").strip()
    keywords_text = data.get("keywords", "").strip()
    search_days = int(data.get("search_days", 365))

    if not email_addr or not password:
        return jsonify({"success": False, "error": "Email dan password wajib diisi."}), 400
    # Default sender if both empty
    if not senders_text and not keywords_text:
        senders_text = "@booking.com"

    senders = [s.strip() for s in senders_text.splitlines() if s.strip()] if senders_text else []
    keywords = [k.strip() for k in keywords_text.splitlines() if k.strip()] if keywords_text else []

    result = fetch_emails(email_addr, password, senders, keywords, search_days)
    return jsonify(result)


@app.route("/api/delete-email", methods=["POST"])
@login_required
def api_delete_email():
    data = request.get_json()

    email_addr = data.get("email", "").strip()
    password = data.get("password", "").strip()
    uid = data.get("uid", "").strip()
    folder = data.get("folder", "INBOX").strip()

    if not email_addr or not password:
        return jsonify({"success": False, "error": "Email dan password wajib diisi."}), 400
    if not uid:
        return jsonify({"success": False, "error": "UID email tidak valid."}), 400

    result = delete_email(email_addr, password, uid, folder=folder)
    return jsonify(result)


# ---- Email Browser API ----

@app.route("/api/email-folders", methods=["POST"])
@login_required
def api_email_folders():
    data = request.get_json()
    email_addr = data.get("email", "").strip()
    password = data.get("password", "").strip()

    if not email_addr or not password:
        return jsonify({"success": False, "error": "Email dan password wajib diisi."}), 400

    result = list_folders(email_addr, password)
    return jsonify(result)


@app.route("/api/email-list", methods=["POST"])
@login_required
def api_email_list():
    data = request.get_json()
    email_addr = data.get("email", "").strip()
    password = data.get("password", "").strip()
    folder = data.get("folder", "INBOX").strip()
    page = int(data.get("page", 1))

    if not email_addr or not password:
        return jsonify({"success": False, "error": "Email dan password wajib diisi."}), 400

    result = list_folder_emails(email_addr, password, folder=folder, page=page)
    return jsonify(result)


@app.route("/api/email-view", methods=["POST"])
@login_required
def api_email_view():
    data = request.get_json()
    email_addr = data.get("email", "").strip()
    password = data.get("password", "").strip()
    folder = data.get("folder", "INBOX").strip()
    uid = data.get("uid", "").strip()

    if not email_addr or not password:
        return jsonify({"success": False, "error": "Email dan password wajib diisi."}), 400
    if not uid:
        return jsonify({"success": False, "error": "UID email tidak valid."}), 400

    result = get_single_email(email_addr, password, folder=folder, uid=uid)
    return jsonify(result)


# ---- History API ----

@app.route("/history")
@login_required
def history_page():
    return render_template("history.html", is_admin=session.get("is_admin", False))


@app.route("/api/jobs")
@login_required
def list_jobs():
    cleanup_expired_jobs()
    user_hash = session.get("code_hash", "")
    is_admin = session.get("is_admin", False)
    result = []
    for jid, jdata in jobs.items():
        # Only show own jobs (admin sees all)
        if not is_admin and jdata.get("owner") != user_hash:
            continue
        checker = jdata["checker"]
        progress = checker.get_progress()
        entry = {
            "job_id": jid,
            "status": progress.get("status", "unknown"),
            "percentage": progress.get("percentage", 0),
            "checked": progress.get("checked", 0),
            "total": progress.get("total", 0),
            "live": progress.get("live", 0),
            "die": progress.get("die", 0),
            "noemail": progress.get("noemail", 0),
            "unreg": progress.get("unreg", 0),
            "skipped": progress.get("skipped", 0),
            "created_at": jdata["created_at"].strftime("%Y-%m-%d %H:%M:%S"),
            "expires_at": jdata["expires_at"].strftime("%Y-%m-%d %H:%M:%S"),
            "meta": jdata.get("meta", {}),
        }
        if is_admin:
            entry["owner_label"] = jdata.get("owner_label", "unknown")
        result.append(entry)
    result.sort(key=lambda x: x["created_at"], reverse=True)
    return jsonify(result)


@app.route("/api/jobs/<job_id>/extend", methods=["POST"])
@login_required
def extend_job(job_id):
    jdata = jobs.get(job_id)
    if not jdata:
        return jsonify({"error": "Job not found"}), 404
    user_hash = session.get("code_hash", "")
    if not session.get("is_admin") and jdata.get("owner") != user_hash:
        return jsonify({"error": "Unauthorized"}), 403
    jdata["expires_at"] = jdata["expires_at"] + timedelta(days=EXTEND_DAYS)
    return jsonify({
        "success": True,
        "message": f"Job diperpanjang {EXTEND_DAYS} hari",
        "expires_at": jdata["expires_at"].strftime("%Y-%m-%d %H:%M:%S"),
    })


@app.route("/api/jobs/<job_id>/delete", methods=["POST"])
@login_required
def delete_job(job_id):
    jdata = jobs.get(job_id)
    if not jdata:
        return jsonify({"error": "Job not found"}), 404
    user_hash = session.get("code_hash", "")
    if not session.get("is_admin") and jdata.get("owner") != user_hash:
        return jsonify({"error": "Unauthorized"}), 403
    jobs.pop(job_id)
    checker = jdata.get("checker")
    if checker and hasattr(checker, 'stop'):
        try:
            checker.stop()
        except Exception:
            pass
    job_dir = os.path.join(JOBS_DIR, job_id)
    if os.path.isdir(job_dir):
        shutil.rmtree(job_dir, ignore_errors=True)
    return jsonify({"success": True, "message": "Job dihapus"})


@app.route("/api/jobs/<job_id>/live")
@login_required
def job_live_list(job_id):
    jdata = jobs.get(job_id)
    if not jdata:
        return jsonify({"error": "Job not found"}), 404
    user_hash = session.get("code_hash", "")
    if not session.get("is_admin") and jdata.get("owner") != user_hash:
        return jsonify({"error": "Unauthorized"}), 403
    live_file = os.path.join(JOBS_DIR, job_id, "live.txt")
    accounts = []
    if os.path.exists(live_file):
        with open(live_file, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                line = line.strip()
                if line:
                    accounts.append(line)
    return jsonify({"success": True, "accounts": accounts, "count": len(accounts)})


# ---- Loop Delete API ----

loop_delete_jobs = {}


@app.route("/loop-delete")
@login_required
def loop_delete_page():
    return render_template("loop_delete.html", is_admin=session.get("is_admin", False))


@app.route("/api/loop-delete/start", methods=["POST"])
@login_required
def start_loop_delete():
    data = request.get_json()

    email_addr = data.get("email", "").strip()
    password = data.get("password", "").strip()
    senders_text = data.get("senders", "").strip()
    keywords_text = data.get("keywords", "").strip()
    interval = int(data.get("interval", 5))

    if not email_addr or not password:
        return jsonify({"success": False, "error": "Email dan password wajib diisi."}), 400
    if not senders_text and not keywords_text:
        return jsonify({"success": False, "error": "Isi minimal salah satu: Sender atau Keyword."}), 400

    senders = [s.strip() for s in senders_text.splitlines() if s.strip()] if senders_text else []
    keywords = [k.strip() for k in keywords_text.splitlines() if k.strip()] if keywords_text else []

    job_id = str(uuid.uuid4())[:8]
    job = LoopDeleteJob(
        job_id=job_id,
        email_address=email_addr,
        password=password,
        senders=senders,
        keywords=keywords,
        interval=interval,
    )

    if not job.start():
        return jsonify({"success": False, "error": job.error or "Gagal memulai loop delete"}), 400

    loop_delete_jobs[job_id] = job
    return jsonify({"success": True, "job_id": job_id})


@app.route("/api/loop-delete/stop/<job_id>", methods=["POST"])
@login_required
def stop_loop_delete(job_id):
    job = loop_delete_jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    job.stop()
    return jsonify({"success": True, "message": "Loop dihentikan"})


@app.route("/api/loop-delete/restart/<job_id>", methods=["POST"])
@login_required
def restart_loop_delete(job_id):
    job = loop_delete_jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    if job.running:
        return jsonify({"error": "Job masih berjalan"}), 400
    if not job.start():
        return jsonify({"success": False, "error": job.error or "Gagal restart"}), 400
    return jsonify({"success": True, "message": "Loop dimulai ulang"})


@app.route("/api/loop-delete/delete/<job_id>", methods=["POST"])
@login_required
def delete_loop_delete(job_id):
    job = loop_delete_jobs.pop(job_id, None)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    if job.running:
        job.stop()
    return jsonify({"success": True, "message": "Job dihapus"})


@app.route("/api/loop-delete/status/<job_id>")
@login_required
def loop_delete_status(job_id):
    job = loop_delete_jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job.get_status())


@app.route("/api/loop-delete/list")
@login_required
def list_loop_delete_jobs():
    result = []
    for jid, job in loop_delete_jobs.items():
        result.append(job.get_status())
    return jsonify(result)


if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=5000, threaded=True)
