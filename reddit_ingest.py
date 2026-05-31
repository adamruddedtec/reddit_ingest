#!/usr/bin/env python3
"""
reddit_ingest — a small, public-content Reddit comment collector.

Polls new comments from one or more public subreddits via Reddit's official OAuth2 API
(script app, password grant), pseudonymises the author with a salted hash, de-duplicates on
content, and writes normalised JSON-lines records. Read-only, public content only.

Usage:
    export REDDIT_CLIENT_ID=...        # from old.reddit.com/prefs/apps (type: script)
    export REDDIT_CLIENT_SECRET=...
    export REDDIT_USERNAME=...         # the bot/automation account
    export REDDIT_PASSWORD=...
    export REDDIT_USER_AGENT="reddit-ingest/0.1 by /u/<username>"
    export AUTHOR_SALT="<random secret string>"
    python3 reddit_ingest.py --subs soccer --limit 100 --out comments.jsonl

Dependency: requests  (pip install requests)
"""
from __future__ import annotations
import argparse, hashlib, json, os, sys, time
from datetime import datetime, timezone

import requests

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
API_BASE = "https://oauth.reddit.com"


def env(name: str, required: bool = True) -> str:
    v = os.environ.get(name, "").strip()
    if required and not v:
        sys.exit(f"ERROR: environment variable {name} is not set (see the module docstring).")
    return v


def get_token(s: requests.Session) -> str:
    """OAuth2 'password' grant for a Reddit *script* app."""
    auth = requests.auth.HTTPBasicAuth(env("REDDIT_CLIENT_ID"), env("REDDIT_CLIENT_SECRET"))
    data = {"grant_type": "password",
            "username": env("REDDIT_USERNAME"),
            "password": env("REDDIT_PASSWORD")}
    r = s.post(TOKEN_URL, auth=auth, data=data,
               headers={"User-Agent": env("REDDIT_USER_AGENT")}, timeout=20)
    r.raise_for_status()
    return r.json()["access_token"]


def author_hash(username: str, salt: str) -> str | None:
    """Salted SHA-256 of the author; None for deleted/removed. The raw username is never stored."""
    if not username or username in ("[deleted]", "[removed]", "AutoModerator"):
        return None
    return "sha256:" + hashlib.sha256((salt + ":" + username).encode("utf-8")).hexdigest()[:32]


def backoff_on_headers(resp: requests.Response) -> None:
    """Respect Reddit's rate-limit headers; sleep only when remaining budget is low."""
    try:
        remaining = float(resp.headers.get("X-Ratelimit-Remaining", "100"))
        reset = float(resp.headers.get("X-Ratelimit-Reset", "0"))
    except ValueError:
        return
    if remaining < 5 and reset > 0:
        print(f"  [rate] {remaining:.0f} left, sleeping {reset:.0f}s", file=sys.stderr)
        time.sleep(min(reset, 60))


def fetch_comments(s: requests.Session, token: str, subs: list[str], limit: int) -> list[dict]:
    """One poll of /r/{a+b+c}/comments (new comments across the listed subs)."""
    url = f"{API_BASE}/r/{'+'.join(subs)}/comments"
    headers = {"Authorization": f"bearer {token}", "User-Agent": env("REDDIT_USER_AGENT")}
    r = s.get(url, headers=headers, params={"limit": limit}, timeout=20)
    backoff_on_headers(r)
    r.raise_for_status()
    return [c["data"] for c in r.json().get("data", {}).get("children", [])]


def normalise(c: dict, salt: str) -> dict:
    """Map a raw Reddit comment to a flat record. observed_at (now) and event_at (posted) are
    kept separate so latency is explicit."""
    now = datetime.now(timezone.utc).isoformat()
    event_at = datetime.fromtimestamp(c.get("created_utc", 0), tz=timezone.utc).isoformat()
    body = (c.get("body") or "").strip()
    return {
        "source": "reddit",
        "subreddit": c.get("subreddit"),
        "permalink": f"https://reddit.com{c.get('permalink', '')}",
        "excerpt": body[:1000],
        "author_hash": author_hash(c.get("author", ""), salt),
        "score": c.get("score"),
        "edited": bool(c.get("edited")),
        "removed": body in ("[removed]", "[deleted]"),
        "event_at": event_at,
        "observed_at": now,
        "dedup_key": hashlib.sha256(
            (str(c.get("subreddit")) + "|" + body[:200] + "|" + event_at[:16]).encode()
        ).hexdigest()[:16],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Collect public Reddit comments to JSONL.")
    ap.add_argument("--subs", nargs="*", default=["soccer"], help="subreddits (no r/ prefix)")
    ap.add_argument("--limit", type=int, default=100, help="comments per poll (max 100)")
    ap.add_argument("--polls", type=int, default=1, help="number of polls")
    ap.add_argument("--interval", type=float, default=5.0, help="seconds between polls")
    ap.add_argument("--out", default="-", help="output JSONL file, or - for stdout")
    args = ap.parse_args()

    salt = env("AUTHOR_SALT")
    s = requests.Session()
    token = get_token(s)
    print(f"[auth] token acquired; polling: {', '.join(args.subs)}", file=sys.stderr)

    out = sys.stdout if args.out == "-" else open(args.out, "a", encoding="utf-8")
    seen: set[str] = set()
    total = 0
    try:
        for poll in range(args.polls):
            for c in fetch_comments(s, token, args.subs, args.limit):
                rec = normalise(c, salt)
                if rec["dedup_key"] in seen:
                    continue
                seen.add(rec["dedup_key"])
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                total += 1
            out.flush()
            print(f"[poll {poll+1}/{args.polls}] {total} unique total", file=sys.stderr)
            if poll + 1 < args.polls:
                time.sleep(args.interval)
    finally:
        if out is not sys.stdout:
            out.close()
    print(f"[done] {total} records written", file=sys.stderr)


if __name__ == "__main__":
    main()
