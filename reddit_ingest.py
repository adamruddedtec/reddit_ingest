#!/usr/bin/env python3
"""
fantell-reddit-ingest — minimal, honest Reddit fan-signal ingester for FanTell.

This is a reference implementation of the "discover → dedup → normalise → freshness-stamp → write"
pipeline from Soc's social-ingestion playbook. It pulls public comments from UK-football subreddits
via Reddit's OAuth2 script-app flow (password grant), pseudonymises authors with a salted hash, and
emits normalised evidence records as JSON lines (the FanTell `evidence_records` shape).

It does NOT write to Postgres directly — it prints/writes JSONL so it's safe to run anywhere and the
DB write-path stays owned by the FanTell app (Kop/Logbook contract on vpsf). Point it at a file or pipe.

Design rules (from the playbook):
- Spine = /r/{sub}/comments (new comments), NOT search; /new for posts.
- Multi-sub in one call to save rate budget: /r/a+b+c/comments
- Back off on the X-Ratelimit headers, never a fixed sleep.
- Edits/deletes are silent → append a new record on re-observe, never mutate.
- Pseudonymise author at ingest (salted SHA-256); never store the raw username.
- Public content only. No DMs, no private subs.

Usage:
    export REDDIT_CLIENT_ID=...        # from old.reddit.com/prefs/apps (script app)
    export REDDIT_CLIENT_SECRET=...
    export REDDIT_USERNAME=fantell_ingest
    export REDDIT_PASSWORD=...
    export REDDIT_USER_AGENT="fantell-kop/0.1 by /u/fantell_ingest"
    export KOP_AUTHOR_SALT="<random-32+ char secret>"   # any long random string
    python3 reddit_ingest.py --subs soccer Gunners reddevils --limit 100 --out evidence.jsonl

Dependencies: just `requests`  (pip install requests)
"""
from __future__ import annotations
import argparse, hashlib, json, os, sys, time
from datetime import datetime, timezone

import requests

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
API_BASE = "https://oauth.reddit.com"

# Default UK-football subreddit cluster (clubs + the general sub). Extend freely.
DEFAULT_SUBS = ["soccer", "Gunners", "reddevils", "LiverpoolFC", "chelseafc", "MCFC", "coys"]


def env(name: str, required: bool = True) -> str:
    v = os.environ.get(name, "").strip()
    if required and not v:
        sys.exit(f"ERROR: environment variable {name} is not set. See the README / module docstring.")
    return v


def get_token(s: requests.Session) -> str:
    """OAuth2 'password' grant for a Reddit *script* app."""
    auth = requests.auth.HTTPBasicAuth(env("REDDIT_CLIENT_ID"), env("REDDIT_CLIENT_SECRET"))
    data = {
        "grant_type": "password",
        "username": env("REDDIT_USERNAME"),
        "password": env("REDDIT_PASSWORD"),
    }
    r = s.post(TOKEN_URL, auth=auth, data=data,
               headers={"User-Agent": env("REDDIT_USER_AGENT")}, timeout=20)
    r.raise_for_status()
    return r.json()["access_token"]


def author_hash(username: str, salt: str) -> str | None:
    """Salted SHA-256 of the author. Returns None for deleted/removed authors.
    We NEVER store the raw username — only this irreversible pseudonym (GDPR posture)."""
    if not username or username in ("[deleted]", "[removed]", "AutoModerator"):
        return None
    return "sha256:" + hashlib.sha256((salt + ":" + username).encode("utf-8")).hexdigest()[:32]


def backoff_on_headers(resp: requests.Response) -> None:
    """Respect Reddit's rate-limit headers; sleep only when the remaining budget is low."""
    try:
        remaining = float(resp.headers.get("X-Ratelimit-Remaining", "100"))
        reset = float(resp.headers.get("X-Ratelimit-Reset", "0"))
    except ValueError:
        return
    if remaining < 5 and reset > 0:
        print(f"  [rate] {remaining:.0f} left, sleeping {reset:.0f}s", file=sys.stderr)
        time.sleep(min(reset, 60))


def fetch_comments(s: requests.Session, token: str, subs: list[str], limit: int) -> list[dict]:
    """One poll of /r/{a+b+c}/comments — the high-frequency fan-sentiment tap."""
    multi = "+".join(subs)
    url = f"{API_BASE}/r/{multi}/comments"
    headers = {"Authorization": f"bearer {token}", "User-Agent": env("REDDIT_USER_AGENT")}
    r = s.get(url, headers=headers, params={"limit": limit}, timeout=20)
    backoff_on_headers(r)
    r.raise_for_status()
    return [c["data"] for c in r.json().get("data", {}).get("children", [])]


def normalise(c: dict, salt: str) -> dict:
    """Map a raw Reddit comment to the FanTell evidence_records shape.
    Note observed_at (now) vs event_at (when posted) kept separate — the gap is real latency."""
    now = datetime.now(timezone.utc).isoformat()
    event_at = datetime.fromtimestamp(c.get("created_utc", 0), tz=timezone.utc).isoformat()
    body = (c.get("body") or "").strip()
    return {
        "source_kind": "reddit",
        "source_subreddit": c.get("subreddit"),
        "content_anchor": f"https://reddit.com{c.get('permalink', '')}",
        "excerpt": body[:1000],                       # cap; full text not retained verbatim
        "author_hash": author_hash(c.get("author", ""), salt),
        "score_at_observed": c.get("score"),          # time-series signal, not truth-at-ingest
        "is_edited": bool(c.get("edited")),
        "removed": c.get("body") in ("[removed]", "[deleted]"),
        "event_at": event_at,                         # when the user posted
        "observed_at": now,                           # when we ingested
        "reliability_tier": 3,                        # social = tertiary; discounted downstream
        "ingester": "kop",
        "dedup_key": hashlib.sha256(
            (str(c.get("subreddit")) + "|" + body[:200] + "|" + event_at[:16]).encode()
        ).hexdigest()[:16],                           # content+time bucket, NOT comment id
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="FanTell Reddit fan-signal ingester (reference).")
    ap.add_argument("--subs", nargs="*", default=DEFAULT_SUBS, help="subreddits (no r/ prefix)")
    ap.add_argument("--limit", type=int, default=100, help="comments per poll (max 100)")
    ap.add_argument("--polls", type=int, default=1, help="number of polls (1 = single shot)")
    ap.add_argument("--interval", type=float, default=5.0, help="seconds between polls (adaptive ceiling)")
    ap.add_argument("--out", default="-", help="output JSONL file, or - for stdout")
    args = ap.parse_args()

    salt = env("KOP_AUTHOR_SALT")
    s = requests.Session()
    token = get_token(s)
    print(f"[auth] got token; polling {len(args.subs)} subs: {', '.join(args.subs)}", file=sys.stderr)

    out = sys.stdout if args.out == "-" else open(args.out, "a", encoding="utf-8")
    seen: set[str] = set()
    total = 0
    try:
        for poll in range(args.polls):
            comments = fetch_comments(s, token, args.subs, args.limit)
            for c in comments:
                rec = normalise(c, salt)
                if rec["dedup_key"] in seen:          # dedup on content, not id
                    continue
                seen.add(rec["dedup_key"])
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                total += 1
            out.flush()
            print(f"[poll {poll+1}/{args.polls}] {len(comments)} fetched, {total} unique total",
                  file=sys.stderr)
            if poll + 1 < args.polls:
                time.sleep(args.interval)
    finally:
        if out is not sys.stdout:
            out.close()
    print(f"[done] {total} unique evidence records written", file=sys.stderr)


if __name__ == "__main__":
    main()
