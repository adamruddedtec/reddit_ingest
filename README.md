# fantell-reddit-ingest

Minimal, public reference implementation of FanTell's Reddit fan-signal ingester.

FanTell builds real-time digital personae of UK sports fans from public signal (census + news +
social) and sells fan-behaviour insight to sports marketing agencies, rights-holders and sponsors.
This tool pulls **public** comments from UK-football subreddits, pseudonymises authors, and emits
normalised evidence records for downstream analysis.

## What it does

```
discover (poll /r/{subs}/comments)  →  dedup (on content+time, not id)
  →  normalise (FanTell evidence shape)  →  pseudonymise author (salted hash)
  →  freshness-stamp (observed_at vs event_at)  →  write (JSONL)
```

It reads only **public** subreddit comments via Reddit's official OAuth2 API (script app, password
grant). It does **not** read DMs, private subreddits, or any non-public content, and it never stores
raw usernames — only an irreversible salted hash.

## Setup

1. Register a **script** app at <https://old.reddit.com/prefs/apps> ("create another app" → type
   `script`). Note the **client ID** (under the app name) and **secret**.
2. Install the one dependency:
   ```bash
   pip install requests
   ```
3. Set environment variables:
   ```bash
   export REDDIT_CLIENT_ID="your_client_id"
   export REDDIT_CLIENT_SECRET="your_client_secret"
   export REDDIT_USERNAME="fantell_ingest"
   export REDDIT_PASSWORD="your_bot_account_password"
   export REDDIT_USER_AGENT="fantell-kop/0.1 by /u/fantell_ingest"
   export KOP_AUTHOR_SALT="any-long-random-string-keep-secret"
   ```

## Usage

```bash
# single poll of three subs, write to a file
python3 reddit_ingest.py --subs soccer Gunners reddevils --limit 100 --out evidence.jsonl

# continuous-ish: 20 polls, 5s apart (the "hot" cadence for match time)
python3 reddit_ingest.py --subs Gunners --polls 20 --interval 5 --out arsenal.jsonl

# stream to stdout
python3 reddit_ingest.py --subs soccer
```

Each output line is one evidence record:

```json
{
  "source_kind": "reddit",
  "source_subreddit": "Gunners",
  "content_anchor": "https://reddit.com/r/Gunners/comments/.../",
  "excerpt": "The Emirates sponsor deal actually feels decent value this season...",
  "author_hash": "sha256:9f2c…",
  "score_at_observed": 14,
  "is_edited": false,
  "removed": false,
  "event_at": "2026-05-31T13:02:11+00:00",
  "observed_at": "2026-05-31T13:04:55+00:00",
  "reliability_tier": 3,
  "ingester": "kop",
  "dedup_key": "a1b2c3d4e5f6a7b8"
}
```

## Design notes (why it's built this way)

- **`/comments` is the spine, not search.** Posts are slow; comments are where match-time fan
  sentiment floods in. Multiple subs are combined in one request (`/r/a+b+c/comments`) to save rate
  budget.
- **Back off on headers, not fixed sleeps.** Reddit returns `X-Ratelimit-Remaining` /
  `X-Ratelimit-Reset`; we sleep only when the budget is low.
- **Edits and deletes are silent.** On re-observe we would append a new record, never mutate an old
  one — the audit trail is the product.
- **Dedup on content, not ID.** Crossposts/reposts inflate volume; we hash `(subreddit, body, time
  bucket)`.
- **Pseudonymisation at ingest.** `author_hash` is a salted SHA-256; the raw username is discarded.
  No real-name linkage; aggregates are exposed only at k≥50 downstream.
- **Public content only**, respecting Reddit's API terms.

## Privacy / data protection

This tool processes only public Reddit content under legitimate-interest grounds, stores no direct
identifiers (authors are salted-hashed), retains short excerpts for provenance, and is intended to
feed aggregate, anonymised (k≥50) fan-behaviour insight — never individual profiling.

## Licence

© 2026 FanTell. Internal reference implementation.
