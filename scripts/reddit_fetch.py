#!/usr/bin/env python3
"""Reddit-aware fetching and digest-checkbox parsing.

**Reddit is read exclusively through the OAuth Data API (PRAW, application-only
/ read-only).** There is exactly one access path, and it requires
`REDDIT_CLIENT_ID` + `REDDIT_CLIENT_SECRET`. With no credentials the fetch fails
loudly and nothing is saved — there is deliberately **no unauthenticated
fallback** (guarded by `test_no_creds_fails_closed_with_no_fallback`).

A fallback to the public `<thread>.json` endpoint used to exist as a local
stopgap while Data-API pre-approval was pending. It was removed 2026-07: Reddit
now blocks that endpoint for non-browser clients regardless of IP (it 403s a
plain `requests`/`curl` GET while serving a browser), and reaching for it would
mean working around an access control rather than through the sanctioned API.
See ADR 0001.

`fetch_reddit_content` returns a dict carrying the load-bearing
`"source": "reddit"` provenance flag (see `tag._is_reddit_sourced` and the
provenance note in CLAUDE.md) — a record that dropped the flag would leak back
into the classifier's training set.

`praw` and `requests` are imported lazily inside the fetch helpers so the pure
helpers (`is_reddit_url`, `parse_checkboxes`, `newly_checked_urls`) — and the
modules that import them — work without those deps installed (e.g. the hermetic
test suite, which exercises the parsers directly with no network).
"""

import os
import re
from datetime import datetime, timezone

_DEFAULT_USER_AGENT = (
    "research-link-collector by /u/laurigates "
    "(+https://github.com/laurigates/research)"
)

# Matches a reddit *comments* (thread) URL on the common host variants. We only
# scrape threads, not subreddit listings or user pages.
REDDIT_URL_RE = re.compile(
    r"https?://(?:www\.|old\.|new\.|np\.|amp\.)?reddit\.com/r/[^\s)]+/comments/[^\s)]+",
    re.IGNORECASE,
)

# Mobile-app "share" links (/r/<sub>/s/<token>): opaque 301 redirects to the
# canonical thread URL. They must be resolved before fetching — left to the
# generic HTML fetcher they only yield Reddit's bot-check interstitial.
REDDIT_SHARE_URL_RE = re.compile(
    r"https?://(?:www\.|old\.|new\.|np\.|amp\.)?reddit\.com/r/[^\s/)]+/s/[^\s)]+",
    re.IGNORECASE,
)

# A markdown task-list line: "- [ ] ..." / "* [x] ...". Group 1 is the mark.
_CHECKBOX_RE = re.compile(r"^\s*[-*]\s*\[(?P<mark>[ xX])\]\s+(?P<rest>.*)$")


def is_reddit_url(url: str) -> bool:
    """True if the URL points at a reddit thread we know how to scrape via PRAW.

    Share links count: fetch_reddit_content resolves them to the canonical
    /comments/ URL before dispatching.
    """
    return bool(
        REDDIT_URL_RE.search(url or "") or REDDIT_SHARE_URL_RE.search(url or "")
    )


def _canonical_thread_url(location: str | None) -> str | None:
    """Reduce a share-link redirect target to a bare canonical thread URL.

    Strips the tracking query/fragment (?share_id=…&utm_…) and rejects targets
    that are not /comments/ URLs (login walls, the homepage, interstitials), so
    a failed resolution can never masquerade as a fetchable thread.
    """
    bare = (location or "").split("#")[0].split("?")[0]
    return bare if REDDIT_URL_RE.search(bare) else None


def resolve_share_url(url: str) -> str | None:
    """Resolve a /s/ share link to its canonical /comments/ thread URL, or None.

    One redirect-suppressed GET reads the Location header — the body (which may
    be a bot-check page) is never parsed.
    """
    import requests  # lazy: keep the pure helpers importable without requests

    try:
        resp = requests.get(
            url,
            headers={
                "User-Agent": os.environ.get("REDDIT_USER_AGENT", _DEFAULT_USER_AGENT)
            },
            timeout=30,
            allow_redirects=False,
        )
    except Exception as e:  # network failure — caller decides how to report
        print(f"Error resolving reddit share link {url}: {e}")
        return None
    return _canonical_thread_url(resp.headers.get("Location"))


def first_reddit_url(text: str) -> str | None:
    """Return the first reddit thread URL found in a string, or None."""
    match = REDDIT_URL_RE.search(text or "")
    return match.group(0) if match else None


def parse_checkboxes(body: str) -> dict[str, bool]:
    """Map each reddit thread URL in a digest body to its checkbox state.

    Keyed by URL (not line position) so the result is robust to reordering or
    edits elsewhere in the issue. A line without a reddit URL is ignored, so the
    blockquote summary lines beneath each item don't pollute the map.
    """
    states: dict[str, bool] = {}
    for line in (body or "").splitlines():
        m = _CHECKBOX_RE.match(line)
        if not m:
            continue
        url = first_reddit_url(m.group("rest"))
        if url:
            states[url] = m.group("mark").lower() == "x"
    return states


def newly_checked_urls(old_body: str, new_body: str) -> list[str]:
    """URLs that flipped from unchecked (or absent) to checked between two bodies.

    This is the scrape trigger: only freshly-ticked threads are scraped, so
    re-editing the issue never re-scrapes already-handled items. Order follows
    appearance in the new body for deterministic processing.
    """
    old = parse_checkboxes(old_body)
    new = parse_checkboxes(new_body)
    ordered = list(dict.fromkeys(first_reddit_url(line) for line in new_body.splitlines()))
    return [
        url
        for url in ordered
        if url and new.get(url) and not old.get(url, False)
    ]


def _reddit_client():
    """Build a read-only PRAW client from REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET.

    With no username/password/refresh token, PRAW operates in application-only
    (read-only) mode — enough to read public submissions, listings and comments.
    """
    import praw  # lazy: keep the pure helpers importable without praw

    client_id = os.environ.get("REDDIT_CLIENT_ID")
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError(
            "REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET must be set to use PRAW. "
            "Create a 'script' app at https://www.reddit.com/prefs/apps and store "
            "them as repo secrets."
        )
    return praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        user_agent=os.environ.get("REDDIT_USER_AGENT", _DEFAULT_USER_AGENT),
        check_for_updates=False,
    )


def _build_content(
    *,
    url: str,
    title: str,
    selftext: str,
    subreddit: str,
    score: int,
    num_comments: int,
    is_self: bool,
    link_url: str,
    comments: list[tuple[str, int, str]],
) -> dict:
    """Assemble the content dict from already-extracted fields.

    Kept separate from the PRAW call so the shape — including the load-bearing
    `source: "reddit"` flag — is pure and hermetically testable with no network.
    `comments` is a list of (author, score, body) tuples.
    """
    selftext = (selftext or "").strip()

    parts = [f"# {title}", ""]
    parts.append(f"Subreddit: r/{subreddit}")
    parts.append(f"Score: {score} · Comments: {num_comments}")
    if not is_self and link_url:
        parts.append(f"Link: {link_url}")
    parts.append("")
    if selftext:
        parts.append(selftext)
        parts.append("")
    parts.append(f"## Top {len(comments)} comments")
    parts.append("")
    for author, c_score, body in comments:
        parts.append(f"**u/{author}** (▲{c_score}):")
        parts.append((body or "").strip())
        parts.append("")

    content_text = "\n".join(parts)
    description = selftext[:300] if selftext else f"Reddit thread in r/{subreddit}"

    return {
        "url": url,
        "title": title,
        "description": description,
        "content": content_text[:10000],  # match fetch.py's content cap
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        # Durable provenance: this flag — NOT content inspection — is what keeps
        # the record out of the tag.py classifier's training set, even after the
        # text is later rewritten/summarized. See tag.py _is_reddit_sourced and
        # the provenance note in CLAUDE.md.
        "source": "reddit",
    }


def _fetch_via_praw(url: str, max_comments: int) -> dict:
    """Fetch a thread via PRAW (OAuth, application-only / read-only)."""
    reddit = _reddit_client()
    submission = reddit.submission(url=url)
    submission.comment_sort = "top"
    submission.comments.replace_more(limit=0)  # drop "load more" stubs
    top_comments = submission.comments[:max_comments]

    comments = [
        (
            getattr(c.author, "name", "[deleted]") if c.author else "[deleted]",
            getattr(c, "score", 0),
            c.body or "",
        )
        for c in top_comments
    ]
    return _build_content(
        url=url,
        title=submission.title,
        selftext=submission.selftext,
        subreddit=submission.subreddit.display_name,
        score=submission.score,
        num_comments=submission.num_comments,
        is_self=submission.is_self,
        link_url=submission.url,
        comments=comments,
    )


def fetch_reddit_content(url: str, max_comments: int = 15) -> dict | None:
    """Fetch a reddit thread (selftext + top N comments) as a content dict.

    Reads via the OAuth Data API (PRAW) and nothing else: with no
    REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET this returns None and the caller
    reports a failed fetch. There is no unauthenticated fallback — scraping the
    public `.json` endpoint would be working around an access control rather
    than through the sanctioned API (ADR 0001).

    Returns the same shape as fetch.fetch_page_content so the rest of the
    pipeline (save_link -> tag -> index) consumes it unchanged.
    """
    try:
        if REDDIT_SHARE_URL_RE.search(url):
            resolved = resolve_share_url(url)
            if not resolved:
                raise RuntimeError(
                    "could not resolve share link to a /comments/ URL"
                )
            url = resolved
        return _fetch_via_praw(url, max_comments)
    except Exception as e:  # missing creds / network / auth / deleted thread
        print(f"Error fetching reddit thread {url}: {e}")
        return None
