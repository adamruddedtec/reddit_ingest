# reddit_ingest

A small command-line tool that collects **public** comments from Reddit subreddits via Reddit's
official OAuth2 API, pseudonymises authors, de-duplicates, and writes normalised JSON-lines.

Read-only. Public content only — no DMs, no private subreddits, no raw usernames retained.

## Setup

1. Register a **script** app at <https://old.reddit.com/prefs/apps> ("create another app" → type
   `script`). Note the **client ID** (shown under the app name) and **secret**.
2. Install the dependency:
   ```bash
   pip install requests
   ```
3. Set environment variables:
   ```bash
   export REDDIT_CLIENT_ID="your_client_id"
   export REDDIT_CLIENT_SECRET="your_client_secret"
   export REDDIT_USERNAME="your_bot_account"
   export REDDIT_PASSWORD="your_bot_account_password"
   export REDDIT_USER_AGENT="reddit-ingest/0.1 by /u/your_bot_account"
   export AUTHOR_SALT="any-long-random-string"
   ```

## Usage

```bash
# single poll, write to file
python3 reddit_ingest.py --subs soccer --limit 100 --out comments.jsonl

# repeated polling, 5s apart
python3 reddit_ingest.py --subs soccer --polls 20 --interval 5 --out comments.jsonl

# stream to stdout
python3 reddit_ingest.py --subs soccer
```

Each output line is one record:

```json
{
  "source": "reddit",
  "subreddit": "soccer",
  "permalink": "https://reddit.com/r/soccer/comments/.../",
  "excerpt": "first 1000 chars of the comment body",
  "author_hash": "sha256:9f2c…",
  "score": 14,
  "edited": false,
  "removed": false,
  "event_at": "2026-05-31T13:02:11+00:00",
  "observed_at": "2026-05-31T13:04:55+00:00",
  "dedup_key": "a1b2c3d4e5f6a7b8"
}
```

## Notes

- Polls `/r/{subs}/comments`; combines multiple subs in one request to conserve rate budget.
- Backs off using Reddit's `X-Ratelimit-*` response headers rather than fixed sleeps.
- De-duplicates on content + time bucket, not comment ID (handles cross/reposts).
- Authors are salted-hashed at ingest; raw usernames are never stored.

## Licence

MIT.
