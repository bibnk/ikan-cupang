"""
Preservation property tests for the spec ``centralize-imap-success-master``.

Encodes the invariants from ``design.md`` §"Preservation Checking":

1. **JSON format identical** — Master file written via ``_atomic_update_master``
   has the same byte layout as ``json.dumps(d, indent=2).encode("utf-8")``.
2. **Idempotent skip** — Re-applying the same ``(domain, cfg)`` does not
   touch disk (mtime unchanged, ``os.replace`` never invoked).
3. **Per-job files unchanged** — ``ImapChecker.run`` still writes the per-job
   text files (``live.txt`` / ``noemail.txt`` / ``die.txt`` / ``unreg.txt``
   / ``domain_skipped.txt``) and does NOT create ``imap_success.json``
   inside ``jobs/{job_id}/``.
4. **API contract unchanged** — Flask ``/api/check``, ``/api/jobs``,
   ``/api/status/<job_id>`` still return the same JSON schema.
5. **SEARCH no-op for safe input** — ``_escape_imap_string(s) == s`` for any
   string with no ``"`` and no ``\\``.
6. **Lookup order unchanged** — ``_worker`` consults config sources in the
   order ``DEFAULT_IMAP_CONFIG`` → cached ``imap_success_config`` →
   wildcard ``*.rr.com`` → ``_try_imap_variants``.
7. **Empty target_senders / keywords** — ``mail_conn.search`` is invoked
   exactly zero times when both lists are empty.
8. **Master corrupt fallback** — A truncated master file makes
   ``ImapChecker.imap_success_config`` fall back to ``{}`` and a subsequent
   ``_update_imap_config`` succeeds (overwriting the corrupt file with
   valid JSON).

All tests monkey-patch ``imap_engine.MASTER_IMAP_SUCCESS_PATH`` and
``imap_engine.MASTER_IMAP_SUCCESS_LOCK_PATH`` to ``tmp_path``-scoped paths so
the real master ``imap_success.json`` at the project root is NEVER touched.

Validates: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.11, 3.12, 3.13
"""
from __future__ import annotations

import json
import os
import sys
import time
from queue import Queue
from unittest.mock import MagicMock

import pytest
from hypothesis import given, settings, HealthCheck, strategies as st

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import imap_engine  # noqa: E402  (after sys.path setup)
from imap_engine import (  # noqa: E402
    ImapChecker,
    _atomic_update_master,
    _escape_imap_string,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _redirect_master(monkeypatch, tmp_path):
    """Point the engine's master path + lock path inside *tmp_path*.

    Always call this BEFORE constructing ``ImapChecker`` or invoking
    ``_atomic_update_master`` so the real master at the project root is
    never read or written.
    """
    master = tmp_path / "imap_success.json"
    lock = tmp_path / "imap_success.json.lock"
    monkeypatch.setattr(imap_engine, "MASTER_IMAP_SUCCESS_PATH", str(master))
    monkeypatch.setattr(imap_engine, "MASTER_IMAP_SUCCESS_LOCK_PATH", str(lock))
    return master, lock


def _make_mock_imap_conn(search_results=None):
    """Return a ``MagicMock`` impersonating an ``imaplib.IMAP4(_SSL)`` instance.

    ``search_results`` is an optional ``list`` of return values to use for
    successive ``search`` calls (``("OK", [b""])`` by default).
    """
    conn = MagicMock(name="imap_conn")
    conn.login.return_value = ("OK", [b""])
    conn.select.return_value = ("OK", [b"0"])
    if search_results is None:
        conn.search.return_value = ("OK", [b""])
    else:
        conn.search.side_effect = list(search_results)
    conn.logout.return_value = ("BYE", [b""])
    return conn


# RFC 3501 §4.3 quoted-string oracle (shared with the bug exploration test).
def _is_valid_rfc3501_quoted(s: str) -> bool:
    if len(s) < 2 or s[0] != '"' or s[-1] != '"':
        return False
    inner = s[1:-1]
    i = 0
    while i < len(inner):
        c = inner[i]
        if c in ("\r", "\n"):
            return False
        if c == "\\":
            if i + 1 >= len(inner):
                return False
            nxt = inner[i + 1]
            if nxt not in ("\\", '"'):
                return False
            i += 2
        elif c == '"':
            return False
        else:
            i += 1
    return True


# ---------------------------------------------------------------------------
# Test 1 — JSON format identical (preserves 3.4, 3.11)
# ---------------------------------------------------------------------------
# Strategy: master schema is dict[str, dict] where each value has at least
# {server: str, port: int} and optionally {ssl: bool}. Use ASCII-only keys so
# json.dumps's default ``ensure_ascii=True`` produces stable bytes; both sides
# of the equality go through json.dumps so any encoding choice is symmetric.

_ASCII = st.characters(min_codepoint=33, max_codepoint=126, blacklist_characters="\\\"")
_DOMAIN = st.text(alphabet=_ASCII, min_size=1, max_size=20)
_SERVER = st.text(alphabet=_ASCII, min_size=1, max_size=30)

_CONFIG = st.fixed_dictionaries(
    {"server": _SERVER, "port": st.integers(min_value=1, max_value=65535)},
    optional={"ssl": st.booleans()},
)

_MASTER_DICT = st.dictionaries(keys=_DOMAIN, values=_CONFIG, max_size=8)


@given(d=_MASTER_DICT)
@settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_json_format_identical(tmp_path_factory, d):
    """``_atomic_update_master`` writes ``json.dumps(d, indent=2)`` byte-for-byte.

    Validates 3.4 (format dict + indent=2 + UTF-8) and 3.11 (final on-disk
    bytes identical to the unfixed implementation when no crash occurs).
    """
    tmp_path = tmp_path_factory.mktemp("json_format")
    master = tmp_path / "imap_success.json"
    lock = tmp_path / "imap_success.json.lock"

    # Hypothesis function-scoped fixture quirk: build a fresh monkey-patch
    # context per example by saving / restoring directly.
    saved_master = imap_engine.MASTER_IMAP_SUCCESS_PATH
    saved_lock = imap_engine.MASTER_IMAP_SUCCESS_LOCK_PATH
    imap_engine.MASTER_IMAP_SUCCESS_PATH = str(master)
    imap_engine.MASTER_IMAP_SUCCESS_LOCK_PATH = str(lock)
    try:
        def setter(current):
            current.clear()
            current.update(d)
            return True  # always force a write so empty dicts are exercised too

        wrote = _atomic_update_master(setter)
        assert wrote is True

        # Compare logically: ``open(..., 'w')`` in the engine uses text mode,
        # which on Windows translates ``\n`` → ``\r\n``. The pre-fix engine
        # used the same mode, so the on-disk byte stream is platform-
        # dependent but the logical content (indent=2, UTF-8, dict roundtrip)
        # is identical. Read in text mode so universal-newline handling
        # normalises line endings before comparison — this validates the
        # platform-portable byte format requirement of 3.4 / 3.11.
        text = master.read_text(encoding="utf-8")
        expected_text = json.dumps(d, indent=2)
        assert text == expected_text, (
            f"master text format diverged:\n"
            f"  got     : {text!r}\n"
            f"  expected: {expected_text!r}\n"
            f"  dict    : {d!r}"
        )

        # Raw byte payload must be UTF-8 encoded with no BOM and parse back
        # to the same dict.
        raw = master.read_bytes()
        # Strip platform-specific newline translation so the assertion holds
        # on both POSIX and Windows.
        raw_normalised = raw.replace(b"\r\n", b"\n")
        assert raw_normalised == expected_text.encode("utf-8")
        assert json.loads(raw.decode("utf-8")) == d
    finally:
        imap_engine.MASTER_IMAP_SUCCESS_PATH = saved_master
        imap_engine.MASTER_IMAP_SUCCESS_LOCK_PATH = saved_lock


# ---------------------------------------------------------------------------
# Test 2 — Idempotent skip (3.3)
# ---------------------------------------------------------------------------
def test_idempotent_skip_does_not_touch_disk(tmp_path, monkeypatch):
    """Calling ``_update_imap_config`` with an unchanged ``(domain, cfg)`` MUST
    NOT modify the master file: mtime unchanged, ``os.replace`` never invoked.
    """
    master, _ = _redirect_master(monkeypatch, tmp_path)

    domain = "d.com"
    cfg = {"server": "imap.d.com", "port": 993, "ssl": True}

    # Pre-seed master via the engine's own helper so the on-disk format
    # matches what the engine would later compare against.
    def seed(current):
        current[domain] = cfg
        return True

    assert _atomic_update_master(seed) is True
    mtime_before = os.stat(str(master)).st_mtime_ns

    # Spy on os.replace at the imap_engine namespace. monkeypatch reverts
    # automatically; the spy still delegates to the real implementation so
    # behavior is preserved if the call ever happens.
    real_replace = os.replace
    replace_calls = []

    def spy_replace(src, dst, *a, **k):
        replace_calls.append((src, dst))
        return real_replace(src, dst, *a, **k)

    monkeypatch.setattr(imap_engine.os, "replace", spy_replace)

    # Construct the checker AFTER pre-seeding so __init__ loads the master.
    checker = ImapChecker(
        job_id="idem_test",
        results_dir=str(tmp_path / "jobs" / "idem_test"),
        accounts=[],
    )
    assert checker.imap_success_config == {domain: cfg}

    # On a cold filesystem some clock implementations have ms-granularity
    # mtime. Sleep briefly so a stray rename would produce an observable
    # mtime delta.
    time.sleep(0.05)

    checker._update_imap_config(domain, cfg)

    mtime_after = os.stat(str(master)).st_mtime_ns
    assert mtime_after == mtime_before, (
        f"master mtime changed despite identical update:\n"
        f"  before: {mtime_before}\n"
        f"  after : {mtime_after}\n"
    )
    assert replace_calls == [], (
        f"os.replace was invoked during an idempotent update: {replace_calls!r}"
    )


# ---------------------------------------------------------------------------
# Test 3 — Per-job files unchanged (3.1)
# ---------------------------------------------------------------------------
def test_per_job_files_present_master_not_in_job_dir(tmp_path, monkeypatch):
    """Drive a tiny in-process job through ``ImapChecker.run()`` with mocked
    IMAP connections; assert the per-job result files are written under
    ``jobs/{job_id}/`` and that ``jobs/{job_id}/imap_success.json`` does NOT
    exist (the entry must land at the master path instead).

    Note: the byte-format baseline of ``live.txt`` etc. is asserted by
    spot-checking the headers / record format produced by the engine
    helpers; the most important assertion (per task spec) is the absence of
    ``imap_success.json`` and the presence of the other files.
    """
    _redirect_master(monkeypatch, tmp_path)

    # Cover the 5 outcome categories. Domain names must avoid every
    # substring in DOMAINS_TO_SKIP_KEYWORDS (hotmail / live / msn / outlook /
    # yahoo / interia / poczta.fm) so only the explicitly-skipped account
    # falls into the skip bucket.
    #   ok-domain  → one mail id returned, fetch yields a target email
    #   nomail     → search ok, no ids
    #   bad        → login fails
    #   ghost      → no DEFAULT cfg, no .rr.com, _try_imap_variants → None
    #   hotmail.com→ matches DOMAINS_TO_SKIP_KEYWORDS → domain_skipped
    accounts = [
        {"email": "a@ok-domain.test", "password": "p"},
        {"email": "b@nomail.test", "password": "p"},
        {"email": "c@bad.test", "password": "p"},
        {"email": "d@ghost.test", "password": "p"},
        {"email": "e@hotmail.com", "password": "p"},
    ]

    monkeypatch.setattr(
        imap_engine,
        "DEFAULT_IMAP_CONFIG",
        {
            "ok-domain.test": {"server": "imap.ok-domain.test", "port": 993},
            "nomail.test": {"server": "imap.nomail.test", "port": 993},
            "bad.test": {"server": "imap.bad.test", "port": 993},
        },
    )

    # Build per-call IMAP4_SSL behaviour keyed off the host argument.
    live_email_id = b"42"
    rfc822_payload = (
        b"Subject: Promo News\r\n"
        b'From: "Booking" <noreply@booking.com>\r\n'
        b"Date: Mon, 01 Jan 2024 12:00:00 +0000\r\n"
        b"\r\n"
        b"Hello world\r\n"
    )

    def imap4_ssl_factory(host, port, *a, **k):
        if host == "imap.ok-domain.test":
            conn = _make_mock_imap_conn(
                search_results=[("OK", [live_email_id])]
            )
            conn.fetch.return_value = (
                "OK",
                [(b"42 (RFC822 {%d}" % len(rfc822_payload), rfc822_payload), b")"],
            )
            return conn
        if host == "imap.nomail.test":
            return _make_mock_imap_conn()
        if host == "imap.bad.test":
            conn = _make_mock_imap_conn()
            conn.login.side_effect = Exception("login failed")
            return conn
        # Any unexpected host → raise so the worker treats it as a failure.
        raise Exception(f"unexpected IMAP host: {host}")

    monkeypatch.setattr(imap_engine.imaplib, "IMAP4_SSL", imap4_ssl_factory)
    monkeypatch.setattr(
        imap_engine.imaplib,
        "IMAP4",
        lambda *a, **k: (_ for _ in ()).throw(Exception("plain IMAP not used")),
    )

    job_id = "perjob_files"
    results_dir = tmp_path / "jobs" / job_id

    checker = ImapChecker(
        job_id=job_id,
        results_dir=str(results_dir),
        accounts=accounts,
        target_senders=["@booking.com"],
        keywords=[],
        max_threads=1,
    )

    # Force unreg path for "unknown-isp.test" / die path for die.test:
    # _try_imap_variants must always return None so unknown domains end up
    # in unreg.txt and connection-failure domains end up in die.txt.
    checker._try_imap_variants = lambda *a, **k: None

    checker.run()

    # Wait for the watcher thread to mark the checker as done.
    deadline = time.time() + 10
    while time.time() < deadline and checker.status not in ("done", "stopped"):
        time.sleep(0.05)
    assert checker.status == "done", f"checker did not finish: status={checker.status}"

    # Per-job text files must all exist with non-empty content where
    # appropriate. (unreg.txt / domain_skipped.txt may be absent only if
    # nothing fell into that bucket — by construction here, both are hit.)
    assert (results_dir / "live.txt").exists(), "live.txt missing"
    assert (results_dir / "noemail.txt").exists(), "noemail.txt missing"
    assert (results_dir / "die.txt").exists(), "die.txt missing"
    assert (results_dir / "unreg.txt").exists(), "unreg.txt missing"
    assert (results_dir / "domain_skipped.txt").exists(), "domain_skipped.txt missing"

    # Spot-check live.txt header (preserves the byte-format laid out in
    # ImapChecker._write_live).
    live_text = (results_dir / "live.txt").read_text(encoding="utf-8")
    assert "EMAIL CHECKER REPORT - LIVE ACCOUNTS WITH TARGET EMAILS" in live_text
    assert "[+] Account: a@ok-domain.test:p" in live_text

    # noemail.txt format: "<email>:<password>\n" per line.
    noemail_text = (results_dir / "noemail.txt").read_text(encoding="utf-8")
    assert noemail_text.strip() == "b@nomail.test:p"

    # die.txt format: "<email>:<password>\n".
    die_text = (results_dir / "die.txt").read_text(encoding="utf-8")
    assert die_text.strip() == "c@bad.test:p"

    # unreg.txt format: "<domain>\n".
    unreg_text = (results_dir / "unreg.txt").read_text(encoding="utf-8")
    assert unreg_text.strip() == "ghost.test"

    # domain_skipped.txt format: "<email>:<password>\n".
    skip_text = (results_dir / "domain_skipped.txt").read_text(encoding="utf-8")
    assert skip_text.strip() == "e@hotmail.com:p"

    # CRITICAL: there must be no per-job imap_success.json.
    per_job_master = results_dir / "imap_success.json"
    assert not per_job_master.exists(), (
        f"unexpected per-job master file at {per_job_master}; "
        f"contents={per_job_master.read_text(encoding='utf-8') if per_job_master.exists() else None!r}"
    )


# ---------------------------------------------------------------------------
# Test 4 — API contract unchanged (3.6)
# ---------------------------------------------------------------------------
def test_api_contract_unchanged(tmp_path, monkeypatch):
    """Hit ``/api/check``, ``/api/jobs``, ``/api/status/<job_id>`` via Flask's
    test client and assert response JSON keys match the pre-fix snapshot.

    ``ImapChecker.run`` is replaced with a no-op (status='done') so no
    background threads are spawned. The master path is redirected to
    ``tmp_path`` and so is ``app.JOBS_DIR``.
    """
    _redirect_master(monkeypatch, tmp_path)

    import app as app_module

    # Redirect job storage to tmp_path and clear any state from prior runs.
    monkeypatch.setattr(app_module, "JOBS_DIR", str(tmp_path / "jobs"))
    os.makedirs(str(tmp_path / "jobs"), exist_ok=True)
    saved_jobs = dict(app_module.jobs)
    app_module.jobs.clear()

    def fake_run(self):
        self.status = "done"

    monkeypatch.setattr(imap_engine.ImapChecker, "run", fake_run)

    try:
        client = app_module.app.test_client()
        with client.session_transaction() as sess:
            sess["authenticated"] = True
            sess["is_admin"] = True
            sess["code_hash"] = "test_hash"
            sess["user_label"] = "tester"

        # ---- POST /api/check ---------------------------------------------
        payload = {
            "accounts": "user@foo.com:pass123",
            "senders": "@booking.com",
            "keywords": "",
            "search_days": 30,
            "max_threads": 1,
        }
        resp = client.post("/api/check", json=payload)
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        expected_check_keys = {"job_id", "total_accounts", "total_senders", "total_keywords", "duplicates_removed"}
        assert set(body.keys()) == expected_check_keys, (
            f"/api/check response keys diverged: got {set(body.keys())!r}, "
            f"expected {expected_check_keys!r}"
        )
        job_id = body["job_id"]

        # ---- GET /api/jobs ----------------------------------------------
        resp = client.get("/api/jobs")
        assert resp.status_code == 200
        jobs_list = resp.get_json()
        assert isinstance(jobs_list, list) and len(jobs_list) >= 1
        entry = next(j for j in jobs_list if j["job_id"] == job_id)
        # The base set of keys present for every caller (admin sees an
        # additional "owner_label").
        expected_job_keys = {
            "job_id", "status", "percentage", "checked", "total",
            "live", "die", "noemail", "unreg", "skipped",
            "created_at", "expires_at", "meta",
        }
        assert expected_job_keys.issubset(set(entry.keys())), (
            f"/api/jobs entry missing keys: "
            f"missing={expected_job_keys - set(entry.keys())!r}, got={set(entry.keys())!r}"
        )

        # ---- GET /api/status/<job_id> -----------------------------------
        # Status is a Server-Sent-Events stream. With status='done' the
        # generator yields exactly one progress payload then breaks.
        resp = client.get(f"/api/status/{job_id}")
        assert resp.status_code == 200
        assert resp.mimetype == "text/event-stream"
        sse_body = resp.get_data(as_text=True)
        # The first SSE message: "data: {json}\n\n"
        assert sse_body.startswith("data: "), sse_body
        first_line = sse_body.split("\n\n", 1)[0]
        progress_json = first_line[len("data: "):]
        progress = json.loads(progress_json)
        expected_progress_keys = {
            "status", "total", "checked", "percentage",
            "live", "noemail", "die", "unreg", "skipped",
            "live_accounts",
        }
        assert expected_progress_keys.issubset(set(progress.keys())), (
            f"/api/status payload missing keys: "
            f"missing={expected_progress_keys - set(progress.keys())!r}, "
            f"got={set(progress.keys())!r}"
        )
    finally:
        # Restore app.jobs so other tests / runtime state is unaffected.
        app_module.jobs.clear()
        app_module.jobs.update(saved_jobs)


# ---------------------------------------------------------------------------
# Test 5 — SEARCH no-op for safe input (3.9)
# ---------------------------------------------------------------------------
# blacklist_characters='"\\\\' is the Python string `"\\` whose unique chars
# are `"` and `\`; deduplicated by the alphabet builder. Use the literal four
# backslashes spelled out in tasks.md to keep the source unambiguous.
@given(s=st.text(alphabet=st.characters(blacklist_characters='"\\\\')))
@settings(max_examples=200, deadline=None)
def test_escape_imap_string_is_noop_for_safe_input(s):
    """For any string containing neither ``"`` nor ``\\``, the escape helper
    is a strict no-op so the IMAP ``SEARCH`` criteria byte-stream is identical
    to the unfixed implementation.
    """
    assert _escape_imap_string(s) == s, (
        f"_escape_imap_string mutated a safe input: "
        f"in={s!r}, out={_escape_imap_string(s)!r}"
    )


# ---------------------------------------------------------------------------
# Test 6 — Lookup order unchanged (3.2)
# ---------------------------------------------------------------------------
def test_lookup_order_default_then_cached_then_wildcard_then_discover(
    tmp_path, monkeypatch
):
    """Drive ``_worker`` against four single-account queues and assert the
    config source used for each domain matches the documented lookup order:

        DEFAULT_IMAP_CONFIG → cached imap_success_config →
        wildcard ``*.rr.com`` → ``_try_imap_variants``.

    Recording strategy:
      - ``imap_engine.imaplib.IMAP4_SSL`` and ``imaplib.IMAP4`` are replaced
        with factories that record ``(host, port)`` for every connection.
      - ``self._try_imap_variants`` is replaced with a recording stub that
        returns ``None`` (so unknown domains end up in unreg.txt).
    """
    _redirect_master(monkeypatch, tmp_path)

    default_cfg = {
        "default-only.test": {"server": "imap.default-only.test", "port": 993},
    }
    monkeypatch.setattr(imap_engine, "DEFAULT_IMAP_CONFIG", default_cfg)

    ssl_calls = []
    plain_calls = []

    def fake_ssl(host, port, *a, **k):
        ssl_calls.append((host, port))
        return _make_mock_imap_conn()

    def fake_plain(host, port, *a, **k):
        plain_calls.append((host, port))
        return _make_mock_imap_conn()

    monkeypatch.setattr(imap_engine.imaplib, "IMAP4_SSL", fake_ssl)
    monkeypatch.setattr(imap_engine.imaplib, "IMAP4", fake_plain)

    checker = ImapChecker(
        job_id="lookup_order",
        results_dir=str(tmp_path / "jobs" / "lookup_order"),
        accounts=[],
        target_senders=[],
        keywords=[],
        max_threads=1,
    )
    checker.imap_success_config = {
        "cached-only.test": {"server": "imap.cached-only.test", "port": 993},
    }

    discover_calls = []

    def fake_try_variants(domain, email_addr, password):
        discover_calls.append(domain)
        return None  # forces unknown domain into unreg.txt

    checker._try_imap_variants = fake_try_variants

    accounts = [
        {"email": "a1@default-only.test", "password": "p"},
        {"email": "a2@cached-only.test", "password": "p"},
        {"email": "a3@weird.rr.com", "password": "p"},
        {"email": "a4@nobody-knows.test", "password": "p"},
    ]
    q = Queue()
    for acc in accounts:
        q.put(acc)
        checker.total += 1  # keep get_progress() consistent (not asserted)
    checker._worker(q)

    # Connections recorded in queue order. Default and cached use port 993
    # (SSL); the rr.com wildcard config is plain IMAP on port 143; the
    # unknown domain never reaches a connection (auto-discovery returned
    # None → unreg).
    assert ssl_calls == [
        ("imap.default-only.test", 993),
        ("imap.cached-only.test", 993),
    ], f"unexpected SSL connection sequence: {ssl_calls!r}"

    assert plain_calls == [
        ("mail.twc.com", 143),
    ], f"unexpected plain-IMAP connection sequence: {plain_calls!r}"

    assert discover_calls == ["nobody-knows.test"], (
        f"_try_imap_variants call sequence diverged: {discover_calls!r}"
    )

    # The unknown domain must have been recorded in unreg.txt.
    unreg = (tmp_path / "jobs" / "lookup_order" / "unreg.txt").read_text(encoding="utf-8")
    assert "nobody-knows.test" in unreg


# ---------------------------------------------------------------------------
# Test 7 — Empty target_senders / keywords (3.10)
# ---------------------------------------------------------------------------
def test_empty_senders_and_keywords_issue_no_search_calls(tmp_path, monkeypatch):
    """With empty ``target_senders`` and empty ``keywords`` the worker must
    invoke ``mail_conn.search`` exactly zero times — identical to the pre-fix
    implementation, which only ran SEARCH inside the now-skipped per-sender
    and per-keyword loops.
    """
    _redirect_master(monkeypatch, tmp_path)

    monkeypatch.setattr(
        imap_engine,
        "DEFAULT_IMAP_CONFIG",
        {"only.test": {"server": "imap.only.test", "port": 993}},
    )

    mock_conn = _make_mock_imap_conn()
    monkeypatch.setattr(imap_engine.imaplib, "IMAP4_SSL", lambda *a, **k: mock_conn)
    monkeypatch.setattr(
        imap_engine.imaplib,
        "IMAP4",
        lambda *a, **k: (_ for _ in ()).throw(Exception("plain not expected")),
    )

    checker = ImapChecker(
        job_id="empty_search",
        results_dir=str(tmp_path / "jobs" / "empty_search"),
        accounts=[{"email": "u@only.test", "password": "p"}],
        target_senders=[],
        keywords=[],
        max_threads=1,
    )

    q = Queue()
    q.put(checker.accounts[0])
    checker._worker(q)

    assert mock_conn.search.call_count == 0, (
        f"mail_conn.search was invoked {mock_conn.search.call_count}x with empty "
        f"target_senders + keywords; expected 0. Calls: {mock_conn.search.call_args_list!r}"
    )

    # Account should land in noemail (empty result set, no emails to write).
    noemail = tmp_path / "jobs" / "empty_search" / "noemail.txt"
    assert noemail.exists(), "noemail.txt should be produced"
    assert noemail.read_text(encoding="utf-8").strip() == "u@only.test:p"


# ---------------------------------------------------------------------------
# Test 8 — Master corrupt fallback (3.5 corrupt edge case)
# ---------------------------------------------------------------------------
def test_corrupt_master_falls_back_to_empty_and_next_update_succeeds(
    tmp_path, monkeypatch
):
    """A truncated/malformed master file must:
       (a) make ``ImapChecker.imap_success_config`` fall back to ``{}``;
       (b) NOT prevent the next ``_update_imap_config`` call from succeeding.
       The corrupt file is overwritten with valid JSON containing the new entry.
    """
    master, _ = _redirect_master(monkeypatch, tmp_path)

    # Truncated JSON: starts a key but never closes the object/string.
    master.write_bytes(b'{"victim.com": {"server"')

    checker = ImapChecker(
        job_id="corrupt_master",
        results_dir=str(tmp_path / "jobs" / "corrupt_master"),
        accounts=[],
    )

    assert checker.imap_success_config == {}, (
        f"corrupt master should yield empty in-memory cache, got "
        f"{checker.imap_success_config!r}"
    )

    new_cfg = {"server": "imap.foo.com", "port": 993, "ssl": True}
    checker._update_imap_config("foo.com", new_cfg)

    # File must now be valid JSON.
    raw_after = master.read_bytes()
    parsed = json.loads(raw_after.decode("utf-8"))
    assert parsed == {"foo.com": new_cfg}, (
        f"corrupt master not properly rewritten; parsed={parsed!r}"
    )

    # In-memory cache should also reflect the new entry.
    assert checker.imap_success_config == {"foo.com": new_cfg}
