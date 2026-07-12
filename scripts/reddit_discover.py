#!/usr/bin/env python3
"""Weekly reddit discovery: open a digest issue of candidate threads to curate.

Lists top threads from the configured subreddits (scripts/reddit_subs.json) via
PRAW, filters by score/comment thresholds, and opens ONE GitHub issue labeled
`reddit-digest` containing a markdown checklist. You tick the boxes for threads
worth keeping; the reddit-scrape workflow then scrapes only the checked ones
into the fetch -> tag -> index pipeline.

The digest label is deliberately distinct from `link-submission`, so the
existing process-link workflow ignores these issues.

Env: GITHUB_TOKEN, GITHUB_REPOSITORY, REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

DIGEST_LABEL = "reddit-digest"
CONFIG_PATH = Path(__file__).parent / "reddit_subs.json"

GITHUB_API = "https://api.github.com"


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _sanitize(text: str) -> str:
    """Make a title safe for a single-line markdown link label."""
    return (text or "").replace("[", "(").replace("]", ")").replace("\n", " ").strip()


def collect_candidates(config: dict) -> list[dict]:
    """Gather threads meeting the thresholds, deduped and ranked by score."""
    from reddit_fetch import _reddit_client

    reddit = _reddit_client()
    ranking = config.get("ranking", "top")
    time_filter = config.get("time_filter", "week")
    limit = config.get("limit_per_sub", 15)
    min_score = config.get("min_score", 100)
    min_comments = config.get("min_comments", 25)
    summary_chars = config.get("summary_chars", 240)

    seen: set[str] = set()
    candidates: list[dict] = []

    for sub in config.get("subreddits", []):
        subreddit = reddit.subreddit(sub)
        listing = (
            subreddit.top(time_filter=time_filter, limit=limit)
            if ranking == "top"
            else getattr(subreddit, ranking)(limit=limit)
        )
        for s in listing:
            if s.id in seen:
                continue
            if s.score < min_score or s.num_comments < min_comments:
                continue
            seen.add(s.id)
            selftext = (s.selftext or "").strip().replace("\n", " ")
            summary = selftext[:summary_chars] if selftext else "(link post — no body text)"
            candidates.append(
                {
                    "title": _sanitize(s.title),
                    "url": f"https://www.reddit.com{s.permalink}",
                    "subreddit": sub,
                    "score": s.score,
                    "num_comments": s.num_comments,
                    "summary": summary,
                }
            )

    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates[: config.get("max_candidates", 40)]


def build_issue_body(candidates: list[dict], config: dict) -> str:
    """Render the digest body. The checkbox line carries the URL; the scrape
    workflow keys off that line, so keep the format stable (see reddit_fetch)."""
    subs = ", ".join(f"r/{s}" for s in config.get("subreddits", []))
    ranking = config.get("ranking", "top")
    tf = f"/{config.get('time_filter')}" if ranking == "top" else ""
    lines = [
        f"## Reddit digest — {subs}",
        "",
        f"Ranking: `{ranking}{tf}` · score ≥ {config.get('min_score')} · "
        f"comments ≥ {config.get('min_comments')} · {len(candidates)} candidates.",
        "",
        "**Tick a box to scrape that thread** (post + top comments) into the "
        "research corpus. Only newly-checked items are scraped, so you can curate "
        "in passes. Leave the rest unchecked — they're discarded.",
        "",
    ]
    for c in candidates:
        lines.append(
            f"- [ ] ▲{c['score']} · {c['num_comments']}💬 · "
            f"[{c['title']}]({c['url']}) <sub>{c['subreddit']}</sub>"
        )
        lines.append(f"  > {c['summary']}")
    if not candidates:
        lines.append("_No threads met the thresholds this run._")
    return "\n".join(lines)


def ensure_label(repo: str, headers: dict):
    """Create the digest label if it doesn't exist (issue creation 422s otherwise)."""
    resp = requests.get(
        f"{GITHUB_API}/repos/{repo}/labels/{DIGEST_LABEL}", headers=headers, timeout=30
    )
    if resp.status_code == 200:
        return
    requests.post(
        f"{GITHUB_API}/repos/{repo}/labels",
        headers=headers,
        json={
            "name": DIGEST_LABEL,
            "color": "ff4500",
            "description": "Reddit discovery digest — tick boxes to scrape threads",
        },
        timeout=30,
    )


def create_issue(repo: str, headers: dict, title: str, body: str) -> dict:
    resp = requests.post(
        f"{GITHUB_API}/repos/{repo}/issues",
        headers=headers,
        json={"title": title, "body": body, "labels": [DIGEST_LABEL]},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def main():
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not token or not repo:
        print("Error: GITHUB_TOKEN and GITHUB_REPOSITORY are required.")
        sys.exit(1)

    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }

    config = load_config()
    print(f"Collecting candidates from {len(config.get('subreddits', []))} subreddits...")
    candidates = collect_candidates(config)
    print(f"Found {len(candidates)} candidate threads.")

    if not candidates:
        print("Nothing met the thresholds; not opening an empty digest.")
        return

    now = datetime.now(timezone.utc)
    iso_year, iso_week, _ = now.isocalendar()
    title = f"[Reddit] Digest {iso_year}-W{iso_week:02d}"
    body = build_issue_body(candidates, config)

    ensure_label(repo, headers)
    issue = create_issue(repo, headers, title, body)
    print(f"Opened digest issue #{issue['number']}: {issue['html_url']}")


if __name__ == "__main__":
    main()
