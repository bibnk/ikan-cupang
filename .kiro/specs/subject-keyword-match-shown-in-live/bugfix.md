# Bugfix Requirements Document

## Introduction

When a user runs an IMAP check with one or more entries in the **Keyword (Subject)** field but leaves the **Target Sender** field empty, emails whose `Subject` genuinely matches a keyword are silently dropped from `jobs/{job_id}/live.txt`, and the account is misclassified as `noemail`.

The root cause is a post-fetch sender filter inside `ImapChecker._worker` (in `imap_engine.py`). After the per-account IMAP `SEARCH` builds the union of FROM-based and SUBJECT-based message IDs, every fetched message is run through:

```python
if sender_matches(from_addr, self.target_senders):
    emails_data.append({...})
```

`sender_matches(from_addr, [])` always returns `False` because its `for target in target_senders:` loop never executes. So when `target_senders` is empty, every SUBJECT-matched email is discarded after fetch, regardless of whether its subject matched a user-supplied keyword.

The user has decided that the post-fetch sender filter must only be enforced when the user actually supplied a sender constraint. When the user supplied only keywords, every SUBJECT-matched email (that is not already excluded by `OTP_SUBJECT_REGEX`) must appear in `live.txt`. Behavior for users who fill in **Target Sender** must remain byte-identical.

## Bug Analysis

### Current Behavior (Defect)

When a user runs a check with `keywords` non-empty and `target_senders` empty, the IMAP `SEARCH` correctly collects message IDs whose `Subject` matches one of the keywords, but the post-fetch filter drops every one of them because `sender_matches(from_addr, [])` is `False` for every email.

1.1 WHEN `target_senders` is empty AND `keywords` is non-empty AND an email's UID is collected into `email_ids` via the SUBJECT branch of the IMAP `SEARCH` because its `Subject` matches one of `keywords` AND that email is then fetched and decoded AND its decoded `Subject` does not match `OTP_SUBJECT_REGEX` THEN inside `ImapChecker._worker` the post-fetch `if sender_matches(from_addr, self.target_senders):` guard evaluates to False (because `sender_matches(_, [])` returns False — its `for target in target_senders:` loop never executes over an empty `target_senders`), the system does not append that email to the `emails_data` list, and the email is therefore never passed to `_write_live`
1.2 WHEN `target_senders` is empty AND `keywords` is non-empty AND at least one fetched email for the account has a decoded `Subject` that matches a keyword in `keywords` AND that email's decoded `Subject` does not match `OTP_SUBJECT_REGEX` AND `emails_data` is empty for that account when `_worker` reaches its post-fetch classification branch (because every keyword-matched email was dropped by the post-fetch `sender_matches(from_addr, [])` filter described in 1.1) THEN `_worker` takes the `else` branch and (a) appends the line `<email_addr>:<password>` to `jobs/{job_id}/noemail.txt` via `_write_noemail(email_addr, password)`, (b) invokes `self._update_progress("noemail")` so the per-job `noemail` counter increases by exactly 1 while the per-job `live` counter is unchanged, and (c) does not call `_write_live` and writes no entry for that account in `jobs/{job_id}/live.txt` — misclassifying the account as `noemail` even though its inbox contains at least one email whose `Subject` matched a user-supplied keyword and was not OTP-filtered, and would have qualified for `live.txt` under the union semantics of `email_ids`
1.3 WHEN `target_senders` is non-empty AND `keywords` is non-empty AND a fetched email's UID is in `email_ids` solely because at least one per-keyword SUBJECT `SEARCH` returned it (no per-sender FROM `SEARCH` returned that UID) AND `sender_matches(from_addr, target_senders)` returns False for the email's parsed `From` address AND `OTP_SUBJECT_REGEX.match(subject.strip())` returns None THEN the system omits that email from the `emails_data` list passed to `_write_live` (and therefore from `live.txt`), even though `email_ids` was built as the set-union of per-sender FROM hits and per-keyword SUBJECT hits via `email_ids.update(msgs[0].split())` — discarding every subject-only match even when the user supplied `keywords`

### Expected Behavior (Correct)

The post-fetch sender filter must be conditional on the user having supplied a sender constraint. Inclusion semantics must mirror the union semantics already used to build `email_ids`: an email is kept if it matches the sender constraint OR if there is no sender constraint to enforce (i.e. the keyword match alone is sufficient).

2.1 WHEN `target_senders` is empty AND `keywords` is non-empty AND an email was previously collected into `email_ids` via the SUBJECT branch of the IMAP `SEARCH` (it would otherwise not have been fetched) AND that email's `Subject` does not match `OTP_SUBJECT_REGEX` THEN the system SHALL bypass the post-fetch sender filter and append that email to `emails_data` regardless of its `From` address, because the absence of a user-supplied sender constraint means there is no sender list to enforce

2.1.1 WHEN an email is included in `emails_data` under 2.1 AND `_write_live` formats the `From` column for that email THEN the system SHALL render the raw `From` header value (not the `<from_addr>` form produced by the sender-matched branch), because no sender constraint was supplied to highlight against

2.2 WHEN `target_senders` is empty AND `keywords` is non-empty AND at least one fetched email for the account satisfies `OTP_SUBJECT_REGEX.match(subject.strip()) is False` (and is therefore retained in `emails_data` per 2.1) THEN the system SHALL (a) invoke `_write_live(account, emails_data)`, which SHALL append the standard live header to `jobs/{job_id}/live.txt` only if the file does not yet contain it and SHALL append an account block containing exactly one `(Date, From, Subject)` row per surviving email in `emails_data` in the existing on-disk format, (b) increment `self.live_count` via `_update_progress("live")`, mirroring the existing live path, and (c) NOT append the account to `jobs/{job_id}/noemail.txt` and NOT increment `self.noemail_count`

2.3 WHEN `target_senders` is non-empty AND `keywords` is non-empty AND a fetched email's MIME-decoded, stripped `Subject` does not match `OTP_SUBJECT_REGEX` THEN the system SHALL include that email in `emails_data` if either (a) `sender_matches(from_addr, target_senders)` returns True for the email's `parseaddr(From)[1]`, OR (b) the email's message ID is a member of the per-account set of SUBJECT-matched IDs recorded during the per-keyword `SEARCH SUBJECT <keyword>` loop in `_worker` (the SUBJECT branch of `email_ids`); SUBJECT-branch membership SHALL be evaluated by set membership against the IDs recorded during that per-keyword SUBJECT `SEARCH` loop and SHALL NOT be evaluated by re-parsing or re-matching the `Subject` string after fetch; the inclusion decision SHALL be a single logical OR per email (the email is appended to `emails_data` at most once even when both (a) and (b) hold), and the union semantics of `email_ids` (the union of IDs returned by all FROM `SEARCH` calls and all SUBJECT `SEARCH` calls for the account) SHALL remain unchanged end-to-end so that the fix is scoped to the post-fetch filter and does not alter how `email_ids` is built or fetched

### Unchanged Behavior (Regression Prevention)

The fix must be scoped to the post-fetch filter. All other behavior of the engine — especially the OTP-subject filter, the `email_ids` set-union, the per-job file format, and the existing sender-only flow — must be preserved exactly.

3.1 WHEN `target_senders` is non-empty AND `keywords` is empty AND the input is otherwise unchanged across pre-fix and post-fix runs (identical account list and order, identical `target_senders` entries and order/casing, identical `search_days` resolved to the same `SINCE`/`BEFORE` window, identical IMAP `SEARCH` and `FETCH` server responses, and identical fetched raw message bytes per UID) THEN the system SHALL CONTINUE TO produce byte-identical contents for `live.txt`, `noemail.txt`, `die.txt`, `unreg.txt`, and `domain_skipped.txt` under `jobs/{job_id}/`, where byte-identical means (a) each file's header line is unchanged (e.g. `EMAIL CHECKER REPORT - LIVE ACCOUNTS WITH TARGET EMAILS` for `live.txt`), and (b) every per-account record emitted by `_write_live` preserves its existing on-disk format including the `<from_addr>` rendering used for emails whose `from_addr` satisfies `sender_matches(from_addr, self.target_senders)` and the raw `From` header rendering for all other emails, plus identical record ordering, field separators, and trailing newlines, so a byte-level diff between the pre-fix and post-fix `jobs/{job_id}/*.txt` files yields zero differences
3.2 WHEN a fetched email's decoded `Subject` header, after `.strip()` is applied, matches `OTP_SUBJECT_REGEX` (the compiled pattern `^Booking\.com – \w+ is your verification code$` evaluated via `OTP_SUBJECT_REGEX.match(subject.strip())`) THEN the system SHALL CONTINUE TO exclude that email from `emails_data` as the outermost gate — that is, this exclusion SHALL CONTINUE TO be evaluated before the new conditional post-fetch sender-filter logic, so a matching `Subject` causes the email to be dropped without consulting `target_senders`, `keywords`, `from_addr`, or `sender_matches` — and this exclusion SHALL CONTINUE TO apply identically across all three post-fetch regimes: (a) `target_senders` non-empty AND `keywords` empty (sender-only), (b) `target_senders` empty AND `keywords` non-empty (keyword-only), and (c) `target_senders` non-empty AND `keywords` non-empty (both filled); the fourth regime (`target_senders` empty AND `keywords` empty) issues no IMAP `SEARCH` calls and therefore never reaches the post-fetch path where this filter executes
3.3 WHEN `target_senders` is empty AND `keywords` is empty THEN the system SHALL CONTINUE TO issue zero `mail_conn.search` invocations for that account (because both the `for sender in self.target_senders:` and `for kw in self.keywords:` loops execute zero iterations), leave `email_ids` empty so that `sorted_ids` is empty and the `if sorted_ids:` guard evaluates to `False`, fall through to the `else` branch that invokes `_write_noemail` exactly once for that account and `self._update_progress("noemail")` exactly once, and produce no `live.txt` entry and no increment of the `live` counter for that account
3.4 WHEN building `email_ids` inside `_worker` THEN the system SHALL CONTINUE TO populate it as the union of message IDs returned by the FROM searches and the SUBJECT searches (no change to the `email_ids.update(msgs[0].split())` set-union semantics)
3.5 WHEN `_write_live` formats the `From` column for an account THEN the system SHALL CONTINUE TO render `<from_addr>` for emails whose `from_addr` satisfies `sender_matches(from_addr, self.target_senders)` and the raw `From` header otherwise
3.6 WHEN the engine writes to or reads from the master `imap_success.json` THEN the system SHALL CONTINUE TO use the atomic `_atomic_update_master` helper, the `filelock.FileLock` on `MASTER_IMAP_SUCCESS_LOCK_PATH`, and the `_escape_imap_string` helper for IMAP `SEARCH` criteria (the centralize-imap-success-master fix stays intact)
3.7 WHEN any HTTP endpoint is invoked (form submission, progress polling, file download) THEN the system SHALL CONTINUE TO honor its existing request and response contract (no API surface changes)
3.8 WHEN the engine writes per-job result files THEN the system SHALL CONTINUE TO use the same file names (`live.txt`, `noemail.txt`, `die.txt`, `unreg.txt`, `domain_skipped.txt`) and the same on-disk format inside the `jobs/{job_id}/` directory

## Bug Condition

The input `X` to the buggy function is the tuple of values that determines whether a fetched email is kept by the post-fetch filter inside `ImapChecker._worker`.

```pascal
TYPE FetchedEmailInput =
  RECORD
    target_senders : List of String  // self.target_senders
    keywords       : List of String  // self.keywords
    subject        : String          // decoded Subject header of the fetched email
    from_addr      : String          // parseaddr(decoded From header)[1]
    matched_via    : Set of {FROM, SUBJECT}  // which SEARCH branch(es) included this email in email_ids
  END

FUNCTION isBugCondition(X)
  INPUT:  X of type FetchedEmailInput
  OUTPUT: boolean

  // The OTP filter still applies; only emails that pass it reach the buggy branch.
  IF OTP_SUBJECT_REGEX.match(X.subject.strip()) THEN
    RETURN false
  END IF

  // Case A: user supplied keywords but no senders, and this email was matched by SUBJECT.
  //   Today: dropped because sender_matches(from_addr, []) is False.
  //   Should: kept.
  IF X.target_senders is empty
     AND X.keywords is non-empty
     AND SUBJECT in X.matched_via
  THEN
    RETURN true
  END IF

  // Case B: user supplied both senders and keywords; email matched ONLY by SUBJECT
  // (its From does not satisfy sender_matches).
  //   Today: dropped, contradicting the union semantics of email_ids.
  //   Should: kept.
  IF X.target_senders is non-empty
     AND X.keywords is non-empty
     AND SUBJECT in X.matched_via
     AND NOT sender_matches(X.from_addr, X.target_senders)
  THEN
    RETURN true
  END IF

  RETURN false
END FUNCTION
```

`F` is the current `ImapChecker._worker` post-fetch filter (the `if sender_matches(...)` branch as written). `F'` is the fixed version that includes the email whenever `isBugCondition(X)` would have flagged it as wrongly dropped.

## Property: Fix Checking

For every input that satisfies the bug condition, the fixed engine must keep the email and surface it through `live.txt` (and the `live` progress counter), not drop it into the `noemail` bucket.

```pascal
// Property: Fix Checking — keyword-only and union-semantics emails are kept
FOR ALL X WHERE isBugCondition(X) DO
  result ← F'(X)
  ASSERT result.kept_in_emails_data = true
  ASSERT result.dropped_by_sender_filter = false
END FOR

// Account-level corollary: when at least one fetched email satisfies isBugCondition
// for an account A, F'(A) writes A to live.txt and increments the live counter
// instead of writing it to noemail.txt.
FOR ALL accounts A WHERE
  exists fetched email e for A such that isBugCondition(e) is true
DO
  ASSERT F'(A).live_file_contains(A) = true
  ASSERT F'(A).noemail_file_contains(A) = false
  ASSERT F'(A).progress_counter_increment = "live"
END FOR
```

## Property: Preservation Checking

For every input that does not satisfy the bug condition, the fixed engine must behave identically to the original engine. This covers the three preservation regimes the user called out: sender-only flows, OTP-subject filtering, and the both-empty no-search path. It also covers emails that are matched both by FROM and SUBJECT but whose `from_addr` already satisfies `sender_matches` — those are kept identically by both `F` and `F'`.

```pascal
// Property: Preservation Checking — non-buggy inputs are unchanged
FOR ALL X WHERE NOT isBugCondition(X) DO
  ASSERT F(X) = F'(X)
END FOR

// Concrete preservation regimes that must hold byte-for-byte:

// (a) Sender-only callers (keywords empty, target_senders non-empty)
FOR ALL accounts A WHERE A.keywords is empty AND A.target_senders is non-empty DO
  ASSERT F(A).live_file_bytes      = F'(A).live_file_bytes
  ASSERT F(A).noemail_file_bytes   = F'(A).noemail_file_bytes
  ASSERT F(A).die_file_bytes       = F'(A).die_file_bytes
  ASSERT F(A).unreg_file_bytes     = F'(A).unreg_file_bytes
  ASSERT F(A).domain_skipped_bytes = F'(A).domain_skipped_bytes
END FOR

// (b) OTP-filtered subjects are still excluded
FOR ALL X WHERE OTP_SUBJECT_REGEX.match(X.subject.strip()) DO
  ASSERT F'(X).kept_in_emails_data = false
END FOR

// (c) Both empty — no SEARCH, account goes straight to noemail
FOR ALL accounts A WHERE A.keywords is empty AND A.target_senders is empty DO
  ASSERT F'(A).search_calls_made = 0
  ASSERT F'(A).noemail_file_contains(A) = true
  ASSERT F'(A).live_file_contains(A) = false
END FOR

// (d) email_ids set-union semantics unchanged
FOR ALL accounts A DO
  ASSERT F'(A).email_ids = union(
    {ids returned by FROM SEARCH for each s in A.target_senders},
    {ids returned by SUBJECT SEARCH for each k in A.keywords}
  )
END FOR
```

## Counterexample

A concrete reproduction that exercises Case A of `isBugCondition`:

```
Form input
----------
Target Sender : (empty)
Keyword       : Booking confirmation
Accounts      : alice@example.com:hunter2

Inbox state for alice@example.com (within search_days)
------------------------------------------------------
Email E1
  From    : reservations@hotelchain.com
  Subject : Booking confirmation #12345 — see you in Paris
  Date    : 2025-01-04
  (Subject matches keyword; From does not match any sender; OTP regex does not match.)

Pre-fix behavior (F)
--------------------
- email_ids contains E1's UID (collected via the SUBJECT search).
- E1 is fetched.
- OTP_SUBJECT_REGEX.match("Booking confirmation #12345 — see you in Paris") is False.
- sender_matches("reservations@hotelchain.com", []) is False  ← bug.
- emails_data ends empty.
- alice@example.com:hunter2 is appended to jobs/{job_id}/noemail.txt.
- noemail counter is incremented.

Post-fix behavior (F')
----------------------
- email_ids identical (no change to set-union).
- E1 is fetched.
- OTP_SUBJECT_REGEX.match(...) still False (preserved).
- target_senders is empty, so the post-fetch sender filter is bypassed.
- E1 is appended to emails_data.
- _write_live appends alice@example.com:hunter2 (with E1's row) to jobs/{job_id}/live.txt.
- live counter is incremented; noemail counter is not.
```
