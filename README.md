# research-pipeline

Source code for a **personal, single-user research-link collection pipeline**. Links —
from the web generally, and from Reddit specifically — are fetched, categorized, and
indexed into a private searchable corpus for one person's own reference.

This repository is the **code**. The corpus it produces is private and is not published.

> **Reading this because of a Reddit Data API application?** The parts you want are
> [Reddit access](#reddit-access) below, [`scripts/reddit_fetch.py`](scripts/reddit_fetch.py)
> (the only code that touches Reddit), [`scripts/reddit_subs.json`](scripts/reddit_subs.json)
> (exactly which subreddits are read), and
> [ADR 0001](docs/adrs/0001-reddit-data-access-praw-over-devvit.md) (why the Data API and
> not Devvit).

## Reddit access

**Read-only, via the OAuth Data API (PRAW), application-only. Nothing else.**

- **No writes of any kind.** The app never posts, comments, votes, messages, or
  moderates. There is no code path that writes to Reddit — `reddit_fetch.py` only reads.
- **No unauthenticated access.** Reddit is reached exclusively through the OAuth Data API
  with `REDDIT_CLIENT_ID` + `REDDIT_CLIENT_SECRET`. Without credentials a fetch fails
  closed and saves nothing. There is deliberately **no** fallback to the public `.json`
  endpoint — one existed as a stopgap and was removed (see
  [ADR 0001's 2026-07 amendment](docs/adrs/0001-reddit-data-access-praw-over-devvit.md));
  reviving it would mean working around a bot-detection control rather than going through
  the sanctioned API. `test_no_creds_fails_closed_with_no_fallback` in
  [`scripts/test_reddit.py`](scripts/test_reddit.py) asserts the fallback does not exist,
  so it cannot be quietly reintroduced.
- **No private user data.** Application-only OAuth does not authenticate as any user and
  cannot read subscriptions, votes, saved content, browsing history, or friends.

### What it does, concretely

**1. Weekly discovery** ([`reddit_discover.py`](scripts/reddit_discover.py), Mondays 09:00 UTC).
Reads the `top`/week listing of each configured subreddit, 15 posts per subreddit — **7
listing requests per week**. Reads only listing metadata (title, score, comment count,
permalink, ~240-char excerpt), filters to posts with ≥100 score and ≥25 comments, caps at
40 candidates, and writes them as a checklist into a private GitHub issue. Nothing is
fetched in depth; nothing is posted to Reddit.

**2. Manual per-thread scrape** ([`reddit_fetch.py`](scripts/reddit_fetch.py)).
A human reads the digest, opens threads on Reddit, and ticks a checkbox for any thread
worth keeping. Only **newly-ticked** threads are fetched: the post's title, author,
selftext, score, and its **top 15 comments**. Curation is manual by design — the pipeline
never bulk-ingests a subreddit.

Typical volume: **under 20 requests per week**, total.

### Subreddits read

Defined in [`scripts/reddit_subs.json`](scripts/reddit_subs.json) — currently
r/LocalLLaMA, r/MachineLearning, r/ClaudeAI, r/Anthropic, r/OpenAI, r/StableDiffusion,
r/singularity. The app has no presence in these communities; it reads their public
content and no other Redditor encounters it.

### Reddit content is excluded from model training — enforced in code

Reddit's Responsible Builder Policy forbids using Reddit content as model-training input.
This is enforced mechanically, not just promised:

- Every Reddit-derived record carries a durable `source: "reddit"` provenance flag stamped
  at fetch time (`_build_content` in [`reddit_fetch.py`](scripts/reddit_fetch.py)).
- [`tag.py`](scripts/tag.py) **excludes any record carrying that flag** from the TF-IDF /
  LogisticRegression classifier's training set. Reddit records are categorized by plain
  keyword matching — pure string operations, never a fitted model.
- The check is keyed on **metadata, not content**, precisely so that it survives the text
  being rewritten or summarized. A content-based check would silently leak such records
  back into training. Guarded by `test_reddit_provenance_is_metadata_keyed_not_content_keyed`
  and `test_reddit_provenance_survives_retagging`.
- The corpus is not published, not served, and is not fed into any LLM, RAG index, or
  fine-tuning run.

## Pipeline

The order is always **fetch → tag → index**; `search` reads the built index.

| Stage | Does |
|---|---|
| [`fetch.py`](scripts/fetch.py) | Resolves a link (GitHub issue, local file, or URL) and saves a JSON record + markdown view. Reddit URLs route to `reddit_fetch`; everything else uses a plain HTML fetcher. |
| [`tag.py`](scripts/tag.py) | Categorizes into one of five fixed categories. TF-IDF + LogisticRegression when ≥3 tagged examples exist, keyword matching otherwise. **Reddit records are always keyword-only.** |
| [`index.py`](scripts/index.py) | Builds the searchable JSON index and tag cross-references. |
| [`search.py`](scripts/search.py) | Queries the index by keyword, category, or tag. |

## Tests

Hermetic — every stage runs against a throwaway temp directory via the
`RESEARCH_BASE_DIR` override, and no test touches the network.

```
pip install -r scripts/requirements-dev.txt
pytest scripts/
```

## Note on this repository

This is a **code-only mirror** of the pipeline. The repository that runs it is private,
because it holds the collected corpus — storing that content is fine, redistributing it is
not. GitHub Actions are disabled here; the workflow files are included so the automation
is fully readable.
