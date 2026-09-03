"""
POST normalize-and-persist with full replace (subject-exclusion-list spec).

Encodes Property 3 from ``design.md`` §"Correctness Properties".

Validates: Requirements 8.2, 8.3
"""
from __future__ import annotations

import os
import sys

from hypothesis import HealthCheck, given, settings, strategies as st

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import imap_engine  # noqa: E402
from imap_engine import _normalize_exclusion_entries  # noqa: E402


def _redirect(tmp_path, monkeypatch):
    p = str(tmp_path / "subject_exclusion_list.json")
    monkeypatch.setattr(imap_engine, "SUBJECT_EXCLUSION_PATH", p)
    monkeypatch.setattr(imap_engine, "SUBJECT_EXCLUSION_LOCK_PATH", p + ".lock")
    return p


def _reset(p):
    for x in (p, p + ".tmp", p + ".lock"):
        try:
            os.remove(x)
        except FileNotFoundError:
            pass


valid_entry = st.from_regex(r"[a-z0-9][a-z0-9 .\-]{0,30}", fullmatch=True).filter(
    lambda s: s.strip() == s and 1 <= len(s) <= 500 and s == s.lower()
)


@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    pre=st.lists(valid_entry, min_size=0, max_size=8, unique=True),
    new=st.lists(valid_entry, min_size=0, max_size=8),
)
def test_property3_post_normalize_persist_full_replace(tmp_path, monkeypatch, pre, new):
    """Validates: Requirements 8.2, 8.3."""
    p = _redirect(tmp_path, monkeypatch)
    _reset(p)

    # 1. Pre-write O.
    imap_engine._save_subject_exclusion_list(pre)

    expected_normalized = _normalize_exclusion_entries(new)

    # 2. POST as admin.
    import app as flask_app

    client = flask_app.app.test_client()
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["is_admin"] = True
        sess["code_hash"] = "test_hash"
        sess["user_label"] = "tester"

    res = client.post(
        "/api/admin/subject-exclusion-list",
        json={"patterns": list(new)},
    )

    # 3. Status 200 with normalized body.
    assert res.status_code == 200, (
        f"POST expected 200, got {res.status_code}; "
        f"body={res.get_data(as_text=True)!r}\n  pre={pre!r}, new={new!r}"
    )
    body = res.get_json()
    assert body == {"success": True, "patterns": expected_normalized}

    # 4. _load returns normalized R (full replace).
    loaded = imap_engine._load_subject_exclusion_list()
    assert loaded == expected_normalized

    # 5. Any element of pre not in normalized R is absent.
    for o in pre:
        if o not in expected_normalized:
            assert o not in loaded, (
                f"residue {o!r} from pre persisted after full-replace POST"
            )
