#!/usr/bin/env python3
"""Pure-logic tests for the reddit discovery/scrape glue.

These never touch the network or PRAW: `reddit_fetch` imports praw lazily inside
`fetch_reddit_content`, so the URL/checkbox helpers and the digest body builder
import and run without it. The PRAW path itself is exercised live in CI, not here.

Run with:  pytest scripts/test_reddit.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from reddit_discover import build_issue_body
from reddit_fetch import (
    _build_content,
    _canonical_thread_url,
    first_reddit_url,
    is_reddit_url,
    newly_checked_urls,
    parse_checkboxes,
)
from tag import _is_reddit_sourced

THREAD = "https://www.reddit.com/r/LocalLLaMA/comments/abc123/some_slug/"
THREAD2 = "https://old.reddit.com/r/ClaudeAI/comments/def456/another/"
SHARE = "https://www.reddit.com/r/ClaudeCode/s/XgttEEDmAV"


def test_is_reddit_url_matches_threads_only():
    assert is_reddit_url(THREAD)
    assert is_reddit_url(THREAD2)
    assert not is_reddit_url("https://www.reddit.com/r/LocalLLaMA/")  # listing, not a thread
    assert not is_reddit_url("https://example.com/article")


def test_is_reddit_url_matches_share_links():
    """Share links (/r/<sub>/s/<token>) must route through the reddit path, not
    the generic HTML fetcher — the web fetcher only gets Reddit's bot-check
    interstitial ("Please wait for verification") and saves a junk record."""
    assert is_reddit_url(SHARE)
    assert is_reddit_url("https://old.reddit.com/r/ollama/s/yAekfIWWm5")


def test_canonical_thread_url_strips_tracking_params():
    """Share-link redirects target the thread URL plus utm/share_id params;
    only the bare thread URL should survive into the record."""
    location = f"{THREAD}?share_id=abc&utm_source=share&utm_medium=android_app"
    assert _canonical_thread_url(location) == THREAD


def test_canonical_thread_url_rejects_non_thread_targets():
    """A redirect that does not land on a /comments/ URL (login wall, homepage,
    interstitial) is a failed resolution, not a fetchable thread."""
    assert _canonical_thread_url("https://www.reddit.com/login/?dest=x") is None
    assert _canonical_thread_url("https://www.reddit.com/") is None
    assert _canonical_thread_url("") is None
    assert _canonical_thread_url(None) is None


def test_fetch_reddit_content_resolves_share_link_before_dispatch(monkeypatch):
    """A share link must be resolved to its canonical thread URL first, and the
    canonical URL (not the share link) passed to the fetcher so the saved
    record's `url` is the durable thread address."""
    import reddit_fetch

    monkeypatch.setattr(reddit_fetch, "resolve_share_url", lambda url: THREAD)

    called = {}

    def fake_praw(url, max_comments):
        called["url"] = url
        return {"source": "reddit", "url": url}

    monkeypatch.setattr(reddit_fetch, "_fetch_via_praw", fake_praw)

    result = reddit_fetch.fetch_reddit_content(SHARE)
    assert called["url"] == THREAD
    assert result["url"] == THREAD


def test_fetch_reddit_content_fails_closed_on_unresolvable_share_link(monkeypatch):
    """If resolution fails (blocked, dead link), the fetch must return None so
    the pipeline reports a failure instead of saving a junk record."""
    import reddit_fetch

    monkeypatch.setattr(reddit_fetch, "resolve_share_url", lambda url: None)

    def fake_fetch(url, max_comments):  # pragma: no cover - must not run
        raise AssertionError("no fetch may run when the share link is unresolvable")

    monkeypatch.setattr(reddit_fetch, "_fetch_via_praw", fake_fetch)

    assert reddit_fetch.fetch_reddit_content(SHARE) is None


def test_first_reddit_url_extracts_from_line():
    line = f"- [x] ▲500 · 90💬 · [Title here]({THREAD}) <sub>r/LocalLLaMA</sub>"
    assert first_reddit_url(line) == THREAD


def test_parse_checkboxes_keys_by_url_ignores_summaries():
    body = (
        f"- [x] ▲500 · 90💬 · [A]({THREAD})\n"
        "  > a summary line with no checkbox\n"
        f"- [ ] ▲200 · 30💬 · [B]({THREAD2})\n"
    )
    states = parse_checkboxes(body)
    assert states == {THREAD: True, THREAD2: False}


def test_newly_checked_only_returns_freshly_ticked():
    old = f"- [ ] [A]({THREAD})\n- [ ] [B]({THREAD2})\n"
    new = f"- [x] [A]({THREAD})\n- [ ] [B]({THREAD2})\n"
    assert newly_checked_urls(old, new) == [THREAD]


def test_newly_checked_ignores_already_checked():
    old = f"- [x] [A]({THREAD})\n"
    new = f"- [x] [A]({THREAD})\n"
    assert newly_checked_urls(old, new) == []


def test_newly_checked_handles_item_added_already_checked():
    """A brand-new line that arrives already checked counts as newly checked."""
    old = ""
    new = f"- [x] [A]({THREAD})\n"
    assert newly_checked_urls(old, new) == [THREAD]


def test_build_issue_body_is_parseable_round_trip():
    """The body builder's output must be readable by the scrape parser — this is
    the contract between discovery and scraping."""
    config = {
        "subreddits": ["LocalLLaMA"],
        "ranking": "top",
        "time_filter": "week",
        "min_score": 100,
        "min_comments": 25,
    }
    candidates = [
        {
            "title": "Cool [bracketed] title",
            "url": THREAD,
            "subreddit": "LocalLLaMA",
            "score": 500,
            "num_comments": 90,
            "summary": "a summary",
        }
    ]
    body = build_issue_body(candidates, config)
    states = parse_checkboxes(body)
    assert states == {THREAD: False}  # rendered unchecked and round-trips


def test_reddit_provenance_is_metadata_keyed_not_content_keyed():
    """The whole point: provenance survives the content being rewritten away from
    anything that looks like Reddit. A record whose URL has been replaced and
    whose text is a paraphrase is STILL excluded, on the `source` flag alone."""
    rewritten = {"source": "reddit", "url": "https://example.com/my-summary", "content": "a paraphrase"}
    assert _is_reddit_sourced(rewritten)

    # Fallback: a legacy record with no source field but a reddit URL.
    assert _is_reddit_sourced({"url": THREAD})

    # Non-reddit records are not excluded.
    assert not _is_reddit_sourced({"source": "web", "url": "https://example.com/x"})
    assert not _is_reddit_sourced({"source": "local", "url": ""})


def test_build_content_assembles_thread_and_stamps_reddit_source():
    """The content dict must carry the post body and every comment — and,
    critically, stamp `source: "reddit"` so the record stays out of the
    classifier's training set."""
    result = _build_content(
        url=THREAD,
        title="Running local LLMs on a laptop",
        selftext="Here is my setup for local inference.",
        subreddit="LocalLLaMA",
        score=512,
        num_comments=2,
        is_self=True,
        link_url=THREAD,
        comments=[("alice", 42, "Try llama.cpp."), ("bob", 17, "Quantize to 4-bit.")],
    )

    assert result["source"] == "reddit", "provenance flag must be stamped on every reddit record"
    assert result["title"] == "Running local LLMs on a laptop"
    assert "Here is my setup for local inference." in result["content"]
    assert "Try llama.cpp." in result["content"]
    assert "Quantize to 4-bit." in result["content"]
    assert "u/alice" in result["content"] and "u/bob" in result["content"]
    assert "## Top 2 comments" in result["content"]


def test_no_creds_fails_closed_with_no_fallback(monkeypatch):
    """With no PRAW credentials the fetch must fail closed — return None, save
    nothing. There is deliberately no unauthenticated `.json` fallback: reading
    the public endpoint without credentials means working around an access
    control rather than through the sanctioned Data API (ADR 0001). This test is
    the guard against that fallback being reintroduced."""
    import reddit_fetch

    monkeypatch.delenv("REDDIT_CLIENT_ID", raising=False)
    monkeypatch.delenv("REDDIT_CLIENT_SECRET", raising=False)

    assert reddit_fetch.fetch_reddit_content(THREAD) is None
    assert not hasattr(reddit_fetch, "_fetch_via_json"), (
        "an unauthenticated .json fetch path must not exist"
    )


if __name__ == "__main__":
    sys.exit(__import__("pytest").main([__file__, "-v"]))
