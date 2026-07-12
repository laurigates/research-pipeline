# 1. Reddit data access via PRAW, not Devvit

## Status

Accepted

## Context

The repo's reddit front-end (digest discovery + checkbox-triggered scrape, PR #7)
pulls reddit threads — post bodies and top comments — *out of* reddit and into the
fetch → tag → index pipeline. Two constraints shape how that data can be obtained:

- **The pipeline is external-CI-driven.** A weekly GitHub Actions cron opens the
  digest issue; ticking a checkbox triggers another Action that scrapes. The
  trigger and the compute both live outside reddit.
- **CI runs from datacenter IPs.** Reddit aggressively 429/403s the
  unauthenticated `.json` endpoint from those IPs, so an authenticated path is
  required in CI.

The chosen path is the **Reddit Data API via PRAW** (OAuth, application-only /
read-only). As of November 2025 a "script" app on the Data API requires one-time
pre-approval under Reddit's Responsible Builder Policy. Our app is pending that
approval, which prompted the question: should we pivot to **Devvit** (Reddit's
app platform) instead, since the Data-API hoops feel heavier than expected?

## Decision

**Stay on the Reddit Data API via PRAW** (OAuth application-only, read-only).
Wait out the one-time pre-approval rather than re-architecting around Devvit.

The Data API is not deprecated, PRAW is actively maintained, and the
pre-approval is a single gate, not an ongoing cost.

## Alternatives considered

### Devvit (Reddit's app platform) — rejected

Devvit cannot do this job. Three architectural blockers, none clearable:

1. **No external trigger.** Devvit apps run on reddit's infrastructure and
   cannot be invoked from a GitHub Actions cron. Our entire pipeline is driven
   by external CI.
2. **Per-subreddit install boundary.** A Devvit app can only read subreddits
   where it is installed, and you can only install in subreddits you moderate.
   The digest surveils r/LocalLLaMA, r/MachineLearning, r/ClaudeAI, etc. — none
   of which we moderate — so this is impossible by construction.
3. **Wrong direction.** Devvit's model is "build experiences *inside* reddit,"
   the opposite of extracting data *out* of reddit into an external corpus.

### Third-party scrapers (e.g. Apify, as a cold path) — rejected for now

Cost, ToS exposure, and — critically — still bound by the same
training-exclusion and redistribution limits as the Data API. No upside over
PRAW while approval is pending; revisit only if PRAW becomes non-viable.

## Consequences

- **One-time pre-approval wait** before the CI reddit paths function. The rest
  of the pipeline (web links, local files) is unaffected.
- **Secrets are to be gitops-managed**, not set ad-hoc: `REDDIT_CLIENT_ID` /
  `REDDIT_CLIENT_SECRET` are pushed to this repo via `reddit_digest = true` on
  its `repositories.tf` entry (values in Google Secret Manager). **Not yet in
  place** — gitops PR #162 is open, blocked on the secret values being
  provisioned out-of-band (gitops issue #204). Until it lands, every reddit path
  is non-functional; see the 2026-07 amendment below.
- **A local stopgap existed** for working before approval: with no creds set,
  `reddit_fetch` fell back to the public `<thread>.json` endpoint. **Removed
  2026-07** — see the amendment below.
- **Reddit content stays out of the classifier.** Reddit's Responsible Builder
  Policy forbids using reddit content as training input, so reddit-sourced
  records are excluded from the TF-IDF/LogisticRegression training set via a
  durable `source: "reddit"` provenance flag (see CLAUDE.md). The repo stays
  private; the corpus is not fed into any LLM/RAG/fine-tuning.

## When to re-litigate

Revisit Devvit or third-party alternatives only if one of these becomes true:

- **Devvit gains cross-subreddit read** (read subs you don't moderate) — which
  clears blocker #2 — **and** an externally-invocable trigger — which clears
  blocker #1. Both are required; either alone is insufficient.
- **Reddit denies or revokes** Data-API pre-approval for this project, or
  withdraws the free non-commercial tier → PRAW becomes non-viable; re-evaluate
  Apify/Devvit.
- **Reddit deprecates the OAuth Data API.**

Explicitly **not** a trigger: the current pre-approval wait, or rate-limit
tuning. Those are expected costs of the chosen path, not signals to change it.

## Amendment — 2026-07: the `.json` stopgap is removed; PRAW is the only path

The original Consequences described a local fallback to the public
`<thread>.json` endpoint, on the premise that it was "reliable from a
residential IP" and blocked only from CI's datacenter IPs. **That premise was
wrong, and is now moot.**

- **The discriminator was never the IP — it is the client.** Reddit blocks the
  `.json` endpoint by request fingerprint: a plain `requests`/`curl` GET gets
  `403 Blocked` while a browser is served normally *from the same residential
  address*. Verified 2026-07 across both hosts (`www`/`old`), the project
  user-agent, a browser user-agent, and an empty one — all 403. Only a full
  browser-shaped request (browser headers plus cookies from a warm-up GET) gets
  through.
- **So the fallback was dead**, and the only way to revive it would be to
  impersonate a browser — i.e. to defeat a bot-detection control rather than go
  through the sanctioned API. That is squarely what Reddit's Responsible Builder
  Policy exists to prevent, and doing it would undercut this project's own
  Data-API application (whose source link a reviewer reads).
- **Decision: delete the fallback.** `reddit_fetch` now has exactly one access
  path — OAuth application-only via PRAW. With no credentials a reddit fetch
  fails closed and saves nothing. `test_no_creds_fails_closed_with_no_fallback`
  guards against reintroduction.

This does not change the ADR's decision; it removes a stopgap the decision had
allowed. **Consequence:** there is now no way to ingest reddit content — locally
or in CI — until the Data-API credentials land (gitops #204 → #162). The weekly
digest has in fact been failing on empty credentials since it was built; the
stopgap masked that only for local runs.
