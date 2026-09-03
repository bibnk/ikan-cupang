"""
Property test for skip predicate equivalence (custom-skip-domains spec).

Encodes Property 6 from ``design.md`` §"Correctness Properties":

  Skip predicate equivalence. For every domain string and every keyword
  list ``L`` of normalized (already-lowercase) entries,
  ``any(kw in domain.lower() for kw in L)`` agrees with whether
  ``ImapChecker._worker`` classifies the account as ``domain_skipped``.
  When ``L == []``, the predicate is False for every domain
  (Requirement 6.2).

Validates: Requirements 6.1, 6.2
"""
from __future__ import annotations

import os
import sys
import uuid
from queue import Queue
from unittest.mock import MagicMock

from hypothesis import HealthCheck, given, settings, strategies as st

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import imap_engine  # noqa: E402
from imap_engine import _WHITESPACE_RE  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _redirect_skip_paths(tmp_path, monkeypatch):
    skip_path = str(tmp_path / "skip_domains.json")
    monkeypatch.setattr(imap_engine, "SKIP_DOMAINS_PATH", skip_path)
    monkeypatch.setattr(imap_engine, "SKIP_DOMAINS_LOCK_PATH", skip_path + ".lock")
    return skip_path


def _isolate_master(tmp_path, monkeypatch):
    master = str(tmp_path / "imap_success.json")
    monkeypatch.setattr(imap_engine, "MASTER_IMAP_SUCCESS_PATH", master)
    monkeypatch.setattr(imap_engine, "MASTER_IMAP_SUCCESS_LOCK_PATH", master + ".lock")
    return master


def _force_known_domain_for(domain, monkeypatch):
    """Stub DEFAULT_IMAP_CONFIG to contain *domain* so ``_worker`` skips
    auto-discovery on the non-skipped path."""
    monkeypatch.setattr(
        imap_engine,
        "DEFAULT_IMAP_CONFIG",
        {domain: {"server": f"imap.{domain}", "port": 993}},
    )


valid_entry = st.from_regex(r"[a-z0-9][a-z0-9.\-]{0,15}", fullmatch=True).filter(
    lambda s: s.strip() == s
    and s == s.lower()
    and not _WHITESPACE_RE.search(s)
    and 1 <= len(s) <= 30
)


# ---------------------------------------------------------------------------
# Property 6 — Skip predicate equivalence
# ---------------------------------------------------------------------------
@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    domain=st.from_regex(r"[a-z0-9][a-z0-9.\-]{0,30}", fullmatch=True).filter(
        lambda s: 1 <= len(s) <= 50 and not _WHITESPACE_RE.search(s)
    ),
    keyword_list=st.lists(valid_entry, min_size=0, max_size=10, unique=True),
)
def test_property6_skip_predicate_equivalence(
    tmp_path, monkeypatch, domain, keyword_list
):
    """Validates: Requirements 6.1, 6.2.

    For every (domain, keyword_list) pair:

    1. The bare predicate ``any(kw in domain.lower() for kw in keyword_list)``
       agrees with ``any(kw in domain for kw in keyword_list)`` because
       every ``kw`` is already lowercase by construction (and the
       generated domain is also lowercase).
    2. When the predicate is True, ``ImapChecker._worker`` classifies the
       account as ``domain_skipped``.
    3. When the predicate is False, the worker does NOT classify the
       account as ``domain_skipped`` (it proceeds further; we observe via
       the absence of a write to the domain_skipped file).
    4. ``keyword_list == []`` ⇒ predicate is False for every domain
       (Requirement 6.2).
    """
    _redirect_skip_paths(tmp_path, monkeypatch)
    _isolate_master(tmp_path, monkeypatch)
    _force_known_domain_for(domain, monkeypatch)

    # Force loader to return our generated keyword_list.
    monkeypatch.setattr(
        imap_engine, "_load_skip_domains", lambda: list(keyword_list)
    )

    # Avoid real network: a connection error → DIE classification, which
    # is fine for our purposes (we only observe the skip flag).
    def _no_real_imap(*args, **kwargs):
        raise OSError("mocked: no network")

    monkeypatch.setattr(imap_engine.imaplib, "IMAP4_SSL", _no_real_imap)
    monkeypatch.setattr(imap_engine.imaplib, "IMAP4", _no_real_imap)


    # Predicate evaluated outside the engine.
    domain_lower = domain.lower()
    expected_skip = any(kw in domain_lower for kw in keyword_list)

    # 1+4. Bare-predicate equivalence (since both sides are lowercase by
    # construction).
    bare_lhs = any(kw in domain for kw in keyword_list)
    assert bare_lhs == expected_skip, (
        f"bare predicate mismatch (domain is already lower).\n"
        f"  domain={domain!r}, keyword_list={keyword_list!r}\n"
        f"  any(kw in domain)        = {bare_lhs}\n"
        f"  any(kw in domain.lower())= {expected_skip}"
    )

    if not keyword_list:
        assert expected_skip is False, (
            f"empty keyword_list must yield False predicate (Requirement 6.2). "
            f"domain={domain!r}"
        )

    # 2+3. Drive _worker for one synthetic account; observe the
    # domain_skipped file contents.
    # Use a UUID-suffixed results dir per example to avoid Hypothesis
    # cross-example pollution (tmp_path is function-scoped, so prior
    # iterations leave domain_skipped.txt in the directory).
    results_dir = tmp_path / f"jobs_{uuid.uuid4().hex[:12]}"
    results_dir.mkdir(parents=True, exist_ok=True)
    checker = imap_engine.ImapChecker(
        job_id="test_skip_pred",
        results_dir=str(results_dir),
        accounts=[{"email": f"alice@{domain}", "password": "hunter2"}],
        target_senders=[],
        keywords=[],
        search_days=1,
    )

    # Confirm constructor pulled the patched loader's value.
    assert checker.skip_domain_keywords == list(keyword_list), (
        f"constructor must snapshot keyword_list from _load_skip_domains.\n"
        f"  expected: {list(keyword_list)!r}\n"
        f"  actual  : {checker.skip_domain_keywords!r}"
    )

    q = Queue()
    q.put(checker.accounts[0])
    checker._worker(q)

    domain_skip_file = os.path.join(str(results_dir), "domain_skipped.txt")
    if expected_skip:
        assert os.path.exists(domain_skip_file), (
            f"predicate True but domain_skipped.txt missing.\n"
            f"  domain={domain!r}, keyword_list={keyword_list!r}"
        )
        contents = open(domain_skip_file, "r", encoding="utf-8").read()
        assert f"alice@{domain}:hunter2" in contents, (
            f"domain_skipped.txt missing entry. contents={contents!r}"
        )
        assert checker.skipped_count == 1, (
            f"skipped_count must be 1 when predicate True; got {checker.skipped_count}"
        )
    else:
        # Either file does not exist or it does not contain the account.
        if os.path.exists(domain_skip_file):
            contents = open(domain_skip_file, "r", encoding="utf-8").read()
            assert f"alice@{domain}:hunter2" not in contents, (
                f"predicate False but account written to domain_skipped.txt. "
                f"contents={contents!r}, domain={domain!r}, "
                f"keyword_list={keyword_list!r}"
            )
        assert checker.skipped_count == 0, (
            f"skipped_count must be 0 when predicate False; got "
            f"{checker.skipped_count}; domain={domain!r}, "
            f"keyword_list={keyword_list!r}"
        )
