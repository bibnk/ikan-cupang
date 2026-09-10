# get_email_engine.py
# Fetch and display emails from a single account — adapted for web use.
# Uses built-in HTMLParser (no external dependencies like BeautifulSoup/html2text).

import imaplib
import email
import email.utils
import os
import json
import re
import socket
import hashlib
from email.header import decode_header
from html.parser import HTMLParser
from datetime import datetime, timedelta

from imap_config import DEFAULT_IMAP_CONFIG, lookup_imap_config, IMAP_SUCCESS_PATH, load_imap_success, save_imap_success
import ssl as _ssl

socket.setdefaulttimeout(60)

# Permissive SSL context untuk legacy IMAP server (DH_KEY_TOO_SMALL, dll).
# Sama dengan yang dipakai di imap_engine.py.
def _make_ssl_context():
    ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = _ssl.CERT_NONE
    try:
        ctx.set_ciphers("ALL:@SECLEVEL=0")
    except Exception:
        pass
    return ctx

EMAIL_REGEX = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')

# File konfigurasi IMAP success — central database
IMAP_OUTPUT_FILE = IMAP_SUCCESS_PATH

# Daftar domain yang dilewati
DOMAINS_TO_SKIP_KEYWORDS = {"hotmail", "live", "msn", "outlook", "yahoo", "interia", "poczta.fm"}


def decode_mime_words(s):
    """Decode header MIME-encoded words."""
    if not s:
        return ""
    decoded = []
    for word, encoding in decode_header(s):
        try:
            decoded.append(word.decode(encoding or 'utf-8', errors='replace') if isinstance(word, bytes) else word)
        except Exception:
            decoded.append(str(word) if word else "")
    return ''.join(decoded)


def _smart_decode(payload, declared_charset):
    """Try to decode byte payload using several common encodings."""
    encodings = [declared_charset, 'utf-8', 'windows-1252', 'iso-8859-1', 'latin-1']
    encodings = [e for e in encodings if e and isinstance(e, str)]

    for enc in encodings:
        try:
            return payload.decode(enc, errors='strict')
        except (UnicodeDecodeError, LookupError):
            continue

    for enc in encodings + ['utf-8', 'latin-1']:
        try:
            return payload.decode(enc, errors='replace')
        except (UnicodeDecodeError, LookupError):
            continue

    return str(payload)


def load_imap_success_config():
    """Memuat konfigurasi IMAP yang sukses dari imap_success.json."""
    if os.path.exists(IMAP_OUTPUT_FILE):
        try:
            with open(IMAP_OUTPUT_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


# ---- Built-in HTML Parser (no external deps) ----

class _HTMLTextExtractor(HTMLParser):
    """Parser untuk mengkonversi HTML ke plain text, kumpulkan link terpisah."""
    def __init__(self):
        super().__init__()
        self._result = []
        self._links = []  # list of (label, url)
        self._skip = False
        self._current_href = None
        self._link_text = []

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self._skip = True
        elif tag == 'a':
            attrs_dict = dict(attrs)
            href = attrs_dict.get('href', '')
            if href and not href.startswith('#') and not href.startswith('mailto:'):
                self._current_href = href
                self._link_text = []
        elif tag in ('br', 'p', 'div', 'tr', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            self._result.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self._skip = False
        elif tag == 'a' and self._current_href:
            label = ''.join(self._link_text).strip()
            self._links.append((label, self._current_href))
            # Tampilkan teks link saja di body (tanpa URL)
            if label:
                self._result.append(label)
            self._current_href = None
            self._link_text = []
        elif tag in ('p', 'div', 'tr', 'table', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            self._result.append('\n')

    def handle_data(self, data):
        if not self._skip:
            if self._current_href is not None:
                self._link_text.append(data)
            else:
                self._result.append(data)

    def get_text(self):
        text = ''.join(self._result)
        lines = [line.strip() for line in text.splitlines()]
        cleaned = '\n'.join(line for line in lines if line)
        return cleaned.strip()

    def get_links(self):
        """Return daftar link unik."""
        seen = set()
        unique = []
        for label, url in self._links:
            if url not in seen:
                seen.add(url)
                unique.append((label, url))
        return unique


def html_to_text_and_links(html_content):
    """Konversi HTML ke plain text. Return (text, links)."""
    parser = _HTMLTextExtractor()
    try:
        parser.feed(html_content)
        return parser.get_text(), parser.get_links()
    except Exception:
        text = re.sub(r'<[^>]+>', ' ', html_content)
        return re.sub(r'\s+', ' ', text).strip(), []


def _sanitize_html_for_web(html_content):
    """
    Sanitize HTML for safe display in web browser.
    Remove dangerous tags, make links open in new tabs.
    Returns sanitized HTML string.
    """
    # Remove script, style, iframe, object, embed, form, input tags
    dangerous_tags = ['script', 'style', 'iframe', 'object', 'embed', 'form', 'input']
    result = html_content
    for tag in dangerous_tags:
        result = re.sub(
            r'<' + tag + r'[^>]*>.*?</' + tag + r'>',
            '', result, flags=re.DOTALL | re.IGNORECASE
        )
        result = re.sub(
            r'<' + tag + r'[^>]*/?>',
            '', result, flags=re.IGNORECASE
        )

    # Make all links open in new tab
    result = re.sub(
        r'<a\s+([^>]*?)>',
        lambda m: _fix_link_tag(m.group(0), m.group(1)),
        result, flags=re.IGNORECASE
    )

    return result


def _fix_link_tag(full_tag, attrs_str):
    """Add target=_blank and rel=noopener to link tags."""
    # Remove existing target and rel
    attrs_str = re.sub(r'\btarget\s*=\s*["\'][^"\']*["\']', '', attrs_str, flags=re.IGNORECASE)
    attrs_str = re.sub(r'\brel\s*=\s*["\'][^"\']*["\']', '', attrs_str, flags=re.IGNORECASE)
    attrs_str = attrs_str.strip()
    return f'<a {attrs_str} target="_blank" rel="noopener noreferrer" class="email-link">'


def get_email_body_html(msg):
    """
    Extract email body and return sanitized HTML with clickable links.
    Returns (body_html, body_text, links_list).
    Uses built-in HTMLParser — no BeautifulSoup/html2text needed.
    """
    plain_text = ""
    html_content = ""

    def _process_part(part):
        nonlocal plain_text, html_content
        payload = part.get_payload(decode=True)
        if not payload:
            return
        charset = part.get_content_charset() or part.get_param('charset')
        text = _smart_decode(payload, charset)
        ctype = (part.get_content_type() or '').lower()

        if ctype == 'text/plain' and not plain_text:
            plain_text = text
        elif ctype == 'text/html' and not html_content:
            html_content = text

    if getattr(msg, "is_multipart", lambda: False)():
        for part in msg.walk():
            cdisp = (part.get('Content-Disposition') or '').lower()
            if 'attachment' in cdisp:
                continue
            ctype = (part.get_content_type() or '').lower()
            if ctype in ('text/plain', 'text/html'):
                _process_part(part)
    else:
        _process_part(msg)

    # Convert to displayable HTML
    if html_content:
        # Extract text and links using built-in parser
        text_version, links = html_to_text_and_links(html_content)

        # Sanitize HTML for web display
        sanitized_html = _sanitize_html_for_web(html_content)

        return sanitized_html, text_version.strip(), links

    if plain_text:
        # Extract links from plain text
        url_pattern = re.compile(r'(https?://[^\s<>"\']+)')
        found_urls = url_pattern.findall(plain_text)
        links = [(url, url) for url in found_urls]

        # Convert plain text URLs to clickable links
        escaped = plain_text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        linked = url_pattern.sub(
            r'<a href="\1" target="_blank" rel="noopener noreferrer" class="email-link">\1</a>',
            escaped
        )
        html_body = '<pre style="white-space: pre-wrap; word-wrap: break-word;">' + linked + '</pre>'
        return html_body, plain_text, links

    return '<p style="color: #666;">Tidak ada konten email yang ditemukan.</p>', "", []


def try_imap_variants(domain, email_address, password):
    """Try common IMAP server prefixes and ports for auto-discovery."""
    domains_to_check = [domain]

    if domain.endswith('.jp'):
        parts = domain.split('.')
        is_complex_tld = len(parts) >= 3 and len(parts[-2]) <= 3
        if (is_complex_tld and len(parts) > 3) or (not is_complex_tld and len(parts) > 2):
            root_domain = '.'.join(parts[1:])
            if root_domain not in domains_to_check:
                domains_to_check.append(root_domain)

    for d in domains_to_check:
        for port in [993, 143]:
            for prefix in ["imap", "mail"]:
                server = f"{prefix}.{d}"
                try:
                    if port == 993:
                        m = imaplib.IMAP4_SSL(server, port, timeout=15, ssl_context=_make_ssl_context())
                    else:
                        m = imaplib.IMAP4(server, port, timeout=15)
                        m.starttls()
                    m.login(email_address, password)
                    m.logout()
                    return {"server": server, "port": port}
                except Exception:
                    continue
    return None


def sender_matches(from_address, target_senders):
    """Check if from_address matches any target sender (exact or @domain)."""
    if not from_address:
        return False
    from_lower = from_address.lower()
    for target in target_senders:
        t = target.lower().strip()
        if t.startswith("@"):
            if from_lower.endswith(t):
                return True
        else:
            if from_lower == t:
                return True
    return False


def get_imap_config(domain):
    """Ambil konfigurasi IMAP untuk domain tertentu (config bawaan + imap_success.json).
    Otomatis cek parent domain jika subdomain tidak ditemukan."""
    extra = load_imap_success_config()
    return lookup_imap_config(domain, extra_configs=extra)


def _save_imap_success(domain, config_data):
    """Simpan konfigurasi IMAP yang berhasil ke imap_success.json."""
    try:
        existing = load_imap_success_config()
        existing[domain] = config_data
        with open(IMAP_OUTPUT_FILE, 'w', encoding='utf-8') as f:
            json.dump(existing, f, indent=2)
    except Exception:
        pass


def _try_connect(cfg, email_address):
    """Coba koneksi IMAP dengan konfigurasi tertentu. Returns mail connection atau raise."""
    use_ssl = cfg.get('ssl', True)
    if cfg['port'] == 993 and use_ssl is not False:
        mail = imaplib.IMAP4_SSL(cfg['server'], cfg['port'], ssl_context=_make_ssl_context())
    elif use_ssl is False:
        mail = imaplib.IMAP4(cfg['server'], cfg['port'])
    else:
        mail = imaplib.IMAP4(cfg['server'], cfg['port'])
        mail.starttls()
    mail.login(email_address, cfg.get('_password', '') or email_address.split('@')[0])
    return mail


def connect_imap(email_address, password):
    """
    Koneksi IMAP dengan email dan password.
    Flow: cek skip list → cek config (DEFAULT + imap_success.json) → kalau gaada, auto-discover
    pakai try_imap_variants → kalau config ada tapi gagal connect, fallback ke auto-discover juga.
    Returns: (mail_connection, error_string) — mail is None on failure.
    """
    domain = email_address.split('@')[-1].lower()

    # Cek domain skip list
    if any(keyword in domain for keyword in DOMAINS_TO_SKIP_KEYWORDS):
        return None, f"Domain {domain} ada di skip list (hotmail/outlook/yahoo/interia)."

    # Get IMAP config (bawaan + cached success)
    cfg = get_imap_config(domain)

    # Wildcard fallback untuk *.rr.com
    if not cfg and domain.endswith(".rr.com"):
        cfg = {"server": "mail.twc.com", "port": 143, "ssl": False}

    # Kalau ada config, coba connect dulu
    if cfg:
        try:
            use_ssl = cfg.get('ssl', True)
            if cfg['port'] == 993 and use_ssl is not False:
                mail = imaplib.IMAP4_SSL(cfg['server'], cfg['port'], ssl_context=_make_ssl_context())
            elif use_ssl is False:
                mail = imaplib.IMAP4(cfg['server'], cfg['port'])
            else:
                mail = imaplib.IMAP4(cfg['server'], cfg['port'])
                mail.starttls()
            mail.login(email_address, password)
            return mail, None
        except Exception:
            # Config ada tapi gagal connect, lanjut ke auto-discover
            pass

    # Auto-discover: coba berbagai kombinasi IMAP server/port (sama kayak check imap)
    result = try_imap_variants(domain, email_address, password)
    if result:
        # Simpan config yang berhasil ke imap_success.json biar next time nggak perlu discover lagi
        _save_imap_success(domain, result)

        # Connect ulang pakai config yang baru ditemukan
        try:
            use_ssl = result.get('ssl', True)
            if result['port'] == 993 and use_ssl is not False:
                mail = imaplib.IMAP4_SSL(result['server'], result['port'], ssl_context=_make_ssl_context())
            elif use_ssl is False:
                mail = imaplib.IMAP4(result['server'], result['port'])
            else:
                mail = imaplib.IMAP4(result['server'], result['port'])
                mail.starttls()
            mail.login(email_address, password)
            return mail, None
        except imaplib.IMAP4.error as e:
            return None, f"Login gagal: {e}"
        except socket.timeout:
            return None, "Timeout koneksi ke server IMAP."
        except Exception as e:
            return None, f"Koneksi gagal: {e}"

    # Semua cara gagal
    if cfg:
        return None, f"Koneksi gagal ke server IMAP untuk domain '{domain}' (config ditemukan tapi gagal connect)."
    return None, f"Gagal menemukan konfigurasi IMAP untuk domain '{domain}'."


def fetch_emails(email_address, password, senders=None, keywords=None, search_days=365):
    """
    Connect to IMAP, search for emails by senders and/or keywords, return top 20 (newest first) with full body.
    Returns dict: {success, error, emails: [{subject, from, date, body_html, body_text, links}]}
    """
    senders = senders or []
    keywords = keywords or []

    if not EMAIL_REGEX.match(email_address):
        return {"success": False, "error": "Format email tidak valid.", "emails": []}

    if not senders and not keywords:
        return {"success": False, "error": "Isi minimal salah satu: sender atau keyword.", "emails": []}

    # Connect using the unified connect_imap function
    mail, error = connect_imap(email_address, password)
    if not mail:
        return {"success": False, "error": error, "emails": []}

    since_date = (datetime.now() - timedelta(days=search_days)).strftime('%d-%b-%Y')

    try:
        mail.select("INBOX", readonly=True)

        # Search by senders and keywords
        all_uids = set()
        for sender in senders:
            try:
                criteria = f'(SINCE "{since_date}" FROM "{sender}")'
                status, messages = mail.uid('SEARCH', None, criteria)
                if status == "OK" and messages[0]:
                    all_uids.update(messages[0].split())
            except Exception:
                continue

        for kw in keywords:
            try:
                criteria = f'(SINCE "{since_date}" SUBJECT "{kw}")'
                status, messages = mail.uid('SEARCH', None, criteria)
                if status == "OK" and messages[0]:
                    all_uids.update(messages[0].split())
            except Exception:
                continue

        if not all_uids:
            mail.logout()
            return {"success": True, "error": None, "emails": [], "message": "Tidak ada email ditemukan."}

        # Get dates to sort (newest first)
        uid_dates = []
        for uid in all_uids:
            try:
                _, date_data = mail.uid('FETCH', uid, '(INTERNALDATE)')
                if date_data and date_data[0]:
                    date_match = re.search(r'INTERNALDATE "([^"]+)"', date_data[0].decode())
                    if date_match:
                        from email.utils import parsedate_to_datetime
                        dt = parsedate_to_datetime(date_match.group(1))
                        uid_dates.append((uid, dt))
                    else:
                        uid_dates.append((uid, datetime.min))
            except Exception:
                uid_dates.append((uid, datetime.min))

        # Sort by date, newest first, take top 20
        uid_dates.sort(key=lambda x: x[1], reverse=True)
        top_uids = uid_dates[:20]

        emails_result = []
        for uid, dt in top_uids:
            try:
                _, msg_data = mail.uid('FETCH', uid, '(RFC822)')
                if msg_data and isinstance(msg_data[0], tuple):
                    msg = email.message_from_bytes(msg_data[0][1])

                    subject = decode_mime_words(msg.get("Subject", ""))
                    from_header = decode_mime_words(msg.get("From", ""))
                    date_header = decode_mime_words(msg.get("Date", ""))

                    # Check if sender matches (skip if using keyword-only search)
                    _name, from_addr = email.utils.parseaddr(from_header)
                    if senders and not sender_matches(from_addr, senders):
                        # Only filter by sender if senders were specified
                        continue

                    body_html, body_text, links = get_email_body_html(msg)

                    # Format date nicely
                    try:
                        dt_obj = email.utils.parsedate_to_datetime(date_header)
                        formatted_date = dt_obj.strftime('%d %b %Y, %H:%M')
                    except Exception:
                        formatted_date = date_header

                    # Format links for JSON response
                    links_data = []
                    for label, url in links:
                        links_data.append({
                            "label": label if label and label != url else "",
                            "url": url
                        })

                    emails_result.append({
                        "uid": uid.decode() if isinstance(uid, bytes) else str(uid),
                        "subject": subject,
                        "from": from_addr or from_header,
                        "date": formatted_date,
                        "body_html": body_html,
                        "body_text": body_text[:500] if body_text else "",
                        "links": links_data,
                    })
            except Exception:
                continue

        mail.logout()
        return {"success": True, "error": None, "emails": emails_result}

    except Exception as e:
        try:
            mail.logout()
        except Exception:
            pass
        return {"success": False, "error": f"Error saat mengambil email: {e}", "emails": []}


def list_folders(email_address, password):
    """List all IMAP folders with email counts."""
    if not EMAIL_REGEX.match(email_address):
        return {"success": False, "error": "Format email tidak valid.", "folders": []}

    mail, error = connect_imap(email_address, password)
    if not mail:
        return {"success": False, "error": error, "folders": []}

    try:
        status, folder_list = mail.list()
        if status != 'OK':
            mail.logout()
            return {"success": False, "error": "Gagal mengambil daftar folder.", "folders": []}

        folders = []
        for item in folder_list:
            if not item:
                continue
            decoded = item.decode() if isinstance(item, bytes) else str(item)
            # Parse LIST response: (\HasNoChildren) "/" "INBOX"
            match = re.match(r'\(([^)]*)\)\s+"([^"]*?)"\s+"?(.+?)"?\s*$', decoded)
            if not match:
                match = re.match(r'\(([^)]*)\)\s+(\S+)\s+"?(.+?)"?\s*$', decoded)
            if not match:
                continue

            flags = match.group(1)
            folder_name = match.group(3).strip().strip('"')

            # Skip \Noselect folders
            if '\\Noselect' in flags:
                continue

            # Get message count
            try:
                st, data = mail.select(folder_name, readonly=True)
                count = int(data[0]) if st == 'OK' else 0
            except Exception:
                count = 0

            folders.append({"name": folder_name, "count": count})

        mail.logout()
        return {"success": True, "folders": folders}

    except Exception as e:
        try:
            mail.logout()
        except Exception:
            pass
        return {"success": False, "error": f"Error: {e}", "folders": []}


def list_folder_emails(email_address, password, folder="INBOX", page=1, per_page=30):
    """List emails in a folder with pagination (newest first). Headers only."""
    empty = {"success": False, "emails": [], "total": 0, "page": 1, "pages": 0}

    if not EMAIL_REGEX.match(email_address):
        return {**empty, "error": "Format email tidak valid."}

    mail, error = connect_imap(email_address, password)
    if not mail:
        return {**empty, "error": error}

    try:
        status, data = mail.select(folder, readonly=True)
        if status != 'OK':
            mail.logout()
            return {**empty, "error": f"Gagal membuka folder '{folder}'."}

        status, messages = mail.uid('SEARCH', None, 'ALL')
        if status != 'OK' or not messages[0]:
            mail.logout()
            return {"success": True, "folder": folder, "emails": [], "total": 0, "page": 1, "pages": 0}

        all_uids = messages[0].split()
        total = len(all_uids)
        all_uids.reverse()  # newest first (higher UIDs = newer)

        pages = max(1, (total + per_page - 1) // per_page)
        page = max(1, min(page, pages))
        start_idx = (page - 1) * per_page
        page_uids = all_uids[start_idx:start_idx + per_page]

        if not page_uids:
            mail.logout()
            return {"success": True, "folder": folder, "emails": [], "total": total, "page": page, "pages": pages}

        emails = []
        for uid in page_uids:
            try:
                st, fetch_data = mail.uid('FETCH', uid, '(FLAGS INTERNALDATE BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])')
                if st != 'OK' or not fetch_data:
                    continue

                for part in fetch_data:
                    if isinstance(part, tuple) and len(part) >= 2:
                        meta_line = part[0].decode() if isinstance(part[0], bytes) else str(part[0])
                        header_bytes = part[1]

                        flags_match = re.search(r'FLAGS\s+\(([^)]*)\)', meta_line)
                        flags = flags_match.group(1) if flags_match else ""

                        date_match = re.search(r'INTERNALDATE\s+"([^"]+)"', meta_line)
                        internal_date = date_match.group(1) if date_match else ""

                        msg = email.message_from_bytes(header_bytes)
                        subject = decode_mime_words(msg.get("Subject", ""))
                        from_header = decode_mime_words(msg.get("From", ""))
                        _name, from_addr = email.utils.parseaddr(from_header)

                        formatted_date = internal_date
                        if internal_date:
                            try:
                                dt = email.utils.parsedate_to_datetime(internal_date)
                                formatted_date = dt.strftime('%d %b %Y, %H:%M')
                            except Exception:
                                pass

                        uid_str = uid.decode() if isinstance(uid, bytes) else str(uid)

                        emails.append({
                            "uid": uid_str,
                            "from": from_addr or from_header,
                            "from_name": _name or "",
                            "subject": subject or "(Tanpa subjek)",
                            "date": formatted_date,
                            "seen": '\\Seen' in flags,
                        })
                        break
            except Exception:
                continue

        mail.logout()
        return {"success": True, "folder": folder, "emails": emails, "total": total, "page": page, "pages": pages}

    except Exception as e:
        try:
            mail.logout()
        except Exception:
            pass
        return {**empty, "error": f"Error: {e}"}


def get_single_email(email_address, password, folder="INBOX", uid=""):
    """Get full email content by UID from a specific folder."""
    if not EMAIL_REGEX.match(email_address):
        return {"success": False, "error": "Format email tidak valid."}

    mail, error = connect_imap(email_address, password)
    if not mail:
        return {"success": False, "error": error}

    try:
        status, _ = mail.select(folder, readonly=True)
        if status != 'OK':
            mail.logout()
            return {"success": False, "error": f"Gagal membuka folder '{folder}'."}

        uid_bytes = uid.encode() if isinstance(uid, str) else uid
        status, msg_data = mail.uid('FETCH', uid_bytes, '(RFC822)')

        if status != 'OK' or not msg_data or not isinstance(msg_data[0], tuple):
            mail.logout()
            return {"success": False, "error": "Email tidak ditemukan."}

        msg = email.message_from_bytes(msg_data[0][1])

        subject = decode_mime_words(msg.get("Subject", ""))
        from_header = decode_mime_words(msg.get("From", ""))
        to_header = decode_mime_words(msg.get("To", ""))
        cc_header = decode_mime_words(msg.get("Cc", ""))
        date_header = decode_mime_words(msg.get("Date", ""))
        _name, from_addr = email.utils.parseaddr(from_header)

        try:
            dt_obj = email.utils.parsedate_to_datetime(date_header)
            formatted_date = dt_obj.strftime('%d %b %Y, %H:%M')
        except Exception:
            formatted_date = date_header

        body_html, body_text, links = get_email_body_html(msg)

        # Attachments info
        attachments = []
        if msg.is_multipart():
            for part in msg.walk():
                cdisp = (part.get('Content-Disposition') or '').lower()
                if 'attachment' in cdisp:
                    filename = part.get_filename()
                    if filename:
                        filename = decode_mime_words(filename)
                    payload = part.get_payload(decode=True)
                    size = len(payload) if payload else 0
                    attachments.append({
                        "filename": filename or "unnamed",
                        "size": size,
                        "content_type": part.get_content_type(),
                    })

        links_data = [{"label": l if l and l != u else "", "url": u} for l, u in links]

        mail.logout()
        return {
            "success": True,
            "email": {
                "uid": uid,
                "subject": subject or "(Tanpa subjek)",
                "from": from_addr or from_header,
                "from_name": _name or "",
                "to": to_header,
                "cc": cc_header,
                "date": formatted_date,
                "body_html": body_html,
                "body_text": body_text[:500] if body_text else "",
                "links": links_data,
                "attachments": attachments,
            }
        }

    except Exception as e:
        try:
            mail.logout()
        except Exception:
            pass
        return {"success": False, "error": f"Error: {e}"}


def delete_email(email_address, password, uid_to_delete, folder="INBOX"):
    r"""
    Permanently delete a specific email by UID.
    Steps:
    1. Connect to IMAP
    2. Select folder (read-write)
    3. Mark email as \Deleted and EXPUNGE
    4. Search all Trash-like folders and permanently delete there too
    """
    if not EMAIL_REGEX.match(email_address):
        return {"success": False, "error": "Format email tidak valid."}

    # Connect using unified function
    mail, error = connect_imap(email_address, password)
    if not mail:
        return {"success": False, "error": error}

    uid_bytes = uid_to_delete.encode() if isinstance(uid_to_delete, str) else uid_to_delete

    try:
        # Step 1: Delete from specified folder
        mail.select(folder)
        # Fetch to verify UID exists
        status, data = mail.uid('FETCH', uid_bytes, '(FLAGS)')
        if status != 'OK' or not data or data[0] is None:
            mail.logout()
            return {"success": False, "error": f"Email tidak ditemukan di {folder}."}

        # Mark as deleted and expunge
        mail.uid('STORE', uid_bytes, '+FLAGS', '(\\Deleted)')
        mail.expunge()

        # Step 2: Also purge from Trash-like folders
        trash_names = [
            '[Gmail]/Trash', '[Gmail]/Bin', 'Trash', 'Deleted Items',
            'Deleted Messages', 'Deleted', '[Gmail]/Papierkorb',
            'Junk', 'Spam', '[Gmail]/Spam',
            'INBOX.Trash', 'INBOX.Deleted Items', 'INBOX.Deleted',
        ]

        # Also discover folders from LIST
        try:
            status, folder_list = mail.list()
            if status == 'OK' and folder_list:
                for fl in folder_list:
                    if fl:
                        decoded = fl.decode() if isinstance(fl, bytes) else str(fl)
                        lower = decoded.lower()
                        if any(t in lower for t in ['trash', 'deleted', 'papierkorb', 'bin', 'corbeille']):
                            # Extract folder name from LIST response
                            match = re.search(r'"([^"]+)"\s*$', decoded)
                            if match:
                                fname = match.group(1)
                                if fname not in trash_names:
                                    trash_names.append(fname)
                            else:
                                parts = decoded.rsplit(' ', 1)
                                if len(parts) == 2:
                                    fname = parts[1].strip()
                                    if fname not in trash_names:
                                        trash_names.append(fname)
        except Exception:
            pass

        for folder in trash_names:
            try:
                status, _ = mail.select(folder)
                if status != 'OK':
                    continue
                # Search all messages and delete them that match
                status, msgs = mail.search(None, 'ALL')
                if status == 'OK' and msgs and msgs[0]:
                    for eid in msgs[0].split():
                        mail.store(eid, '+FLAGS', '(\\Deleted)')
                    mail.expunge()
            except Exception:
                continue

        mail.logout()
        return {"success": True, "message": "Email berhasil dihapus permanen."}

    except Exception as e:
        try:
            mail.logout()
        except Exception:
            pass
        return {"success": False, "error": f"Gagal menghapus email: {e}"}


def delete_duplicate_emails(email_address, password, senders=None):
    """
    Hapus email duplikat berdasarkan Subject + Date + From.
    Fitur dari reference code — adapted for web use.
    Returns dict: {success, error, total_found, duplicates_found, deleted_count}
    """
    senders = senders or ["booking.com", "noreply@booking.com",
                          "email.campaign@sg.booking.com",
                          "noreply-payments@booking.com", "agoda.com"]

    if not EMAIL_REGEX.match(email_address):
        return {"success": False, "error": "Format email tidak valid."}

    mail, error = connect_imap(email_address, password)
    if not mail:
        return {"success": False, "error": error}

    try:
        mail.select("INBOX")

        # Cari email dari semua sender patterns
        all_email_ids = set()
        for pattern in senders:
            status, messages = mail.search(None, f'(FROM "{pattern}")')
            if status == "OK" and messages and messages[0]:
                all_email_ids.update(messages[0].split())

        if not all_email_ids:
            mail.logout()
            return {
                "success": True,
                "total_found": 0,
                "duplicates_found": 0,
                "deleted_count": 0,
                "message": "Tidak ada email dari sender tersebut."
            }

        sorted_ids = sorted(list(all_email_ids), key=lambda x: int(x))

        # Kelompokkan email berdasarkan hash (Subject + Date + From)
        email_hashes = {}  # hash -> list of email IDs
        for eid_bytes in sorted_ids:
            eid_str = eid_bytes.decode() if isinstance(eid_bytes, bytes) else str(eid_bytes)
            _, msg_data = mail.fetch(eid_str, "(RFC822.HEADER)")

            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    subject = decode_mime_words(msg.get("Subject", ""))
                    from_ = decode_mime_words(msg.get("From", ""))
                    date_ = msg.get("Date", "")

                    # Buat hash unik dari kombinasi Subject + From + Date
                    unique_key = f"{subject}|{from_}|{date_}"
                    hash_key = hashlib.md5(unique_key.encode('utf-8', errors='replace')).hexdigest()

                    if hash_key not in email_hashes:
                        email_hashes[hash_key] = []
                    email_hashes[hash_key].append(eid_str)

        # Cari duplikat
        duplicates_to_delete = []
        for hash_key, eid_list in email_hashes.items():
            if len(eid_list) > 1:
                # Simpan yang pertama, hapus sisanya
                duplicates_to_delete.extend(eid_list[1:])

        if not duplicates_to_delete:
            mail.logout()
            return {
                "success": True,
                "total_found": len(sorted_ids),
                "duplicates_found": 0,
                "deleted_count": 0,
                "message": "Tidak ada email duplikat ditemukan."
            }

        # Hapus duplikat
        deleted_count = 0
        for eid_str in duplicates_to_delete:
            try:
                mail.store(eid_str, '+FLAGS', '\\Deleted')
                deleted_count += 1
            except Exception:
                pass

        # Expunge untuk menghapus permanen
        mail.expunge()
        mail.logout()

        return {
            "success": True,
            "total_found": len(sorted_ids),
            "duplicates_found": len(duplicates_to_delete),
            "deleted_count": deleted_count,
            "message": f"Berhasil menghapus {deleted_count} email duplikat."
        }

    except Exception as e:
        try:
            mail.logout()
        except Exception:
            pass
        return {"success": False, "error": f"Error: {e}"}
