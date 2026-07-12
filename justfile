# Local runner for the research link pipeline.
#
# Mirrors the CI workflow's fetch -> tag -> index shape, but runs from your own
# machine so you can process pending issues and bulk-add links.
#
# Reddit URLs need REDDIT_CLIENT_ID + REDDIT_CLIENT_SECRET (OAuth Data API) —
# here as well as in CI. Without them a reddit fetch fails and saves nothing;
# there is no unauthenticated fallback (see docs/adrs/0001-*). Non-reddit links
# need no credentials.
#
# Processing recipes leave changes STAGED-able for manual review — run
# `git diff`, then `just commit`. Nothing is pushed.

# Prefer the project venv if present, else system python3.
python := `[ -x .venv/bin/python ] && echo .venv/bin/python || echo python3`

# List recipes.
default:
    @just --list

# Create .venv and install runtime deps.
setup:
    {{python}} -m venv .venv
    .venv/bin/pip install -r scripts/requirements.txt

# A failed fetch is reported but does not abort the batch. Reddit URLs need
# PRAW creds in the environment; without them they fail and are skipped.
# Add one or more links, then tag + index.
add +urls:
    #!/usr/bin/env bash
    set -uo pipefail
    for u in {{urls}}; do
        {{python}} scripts/fetch.py --url "$u" || echo "failed: $u"
    done
    {{python}} scripts/tag.py
    {{python}} scripts/index.py
    echo "Done. Review with 'git diff', then 'just commit'."

# One URL per line; blank lines and # comments skipped. The accumulated-list path.
# Bulk-add links from a file, then tag + index once.
add-file file:
    #!/usr/bin/env bash
    set -uo pipefail
    while IFS= read -r u || [ -n "$u" ]; do
        u="${u%%#*}"                       # strip trailing comments
        u="$(echo "$u" | xargs)"           # trim whitespace
        [ -z "$u" ] && continue
        {{python}} scripts/fetch.py --url "$u" || echo "failed: $u"
    done < "{{file}}"
    {{python}} scripts/tag.py
    {{python}} scripts/index.py
    echo "Done. Review with 'git diff', then 'just commit'."

# Idempotent (same slug overwrites). NOTE: nothing closes processed issues
# automatically, so this re-processes every open one — fine for a backfill
# (e.g. reddit links that failed in CI for lack of creds). Run `just
# close-processed` after committing + pushing to retire the done ones.
# Process all open link-submission issues, then tag + index.
process-pending:
    #!/usr/bin/env bash
    set -uo pipefail
    export GITHUB_REPOSITORY="${GITHUB_REPOSITORY:-laurigates/research}"
    export GITHUB_TOKEN="${GITHUB_TOKEN:-$(gh auth token)}"
    numbers=$(gh issue list --label link-submission --state open --json number --jq '.[].number')
    for n in $numbers; do
        {{python}} scripts/fetch.py "$n" || echo "failed: issue #$n"
    done
    {{python}} scripts/tag.py
    {{python}} scripts/index.py
    echo "Done. Review with 'git diff', then 'just commit'."

# Only closes issues whose record actually fetched content — an empty record
# (e.g. a bot-check interstitial) keeps its issue open for a retry. Run this
# AFTER `just commit` + push, so a closed issue's record exists upstream.
# Close open link-submission issues that have a successfully-processed record.
[confirm("Close all successfully-processed link-submission issues?")]
close-processed:
    #!/usr/bin/env bash
    set -uo pipefail
    export GITHUB_REPOSITORY="${GITHUB_REPOSITORY:-laurigates/research}"
    processed=$({{python}} -c 'import glob, json; recs = [json.load(open(p)) for p in glob.glob("data/issue-*.json")]; print("\n".join(str(n) for n in sorted({r["issue_number"] for r in recs if r.get("issue_number") and r.get("content")})))')
    declare -i closed=0 skipped=0
    for n in $(gh issue list --label link-submission --state open --json number --jq '.[].number'); do
        if grep -qx "$n" <<< "$processed"; then
            gh issue close "$n" --comment "Processed into the collection as \`data/issue-${n}-*.json\`." && echo "closed: #$n" && closed+=1
        else
            echo "skip: #$n (no successful record in data/ — fetch failed or not yet processed)"
            skipped+=1
        fi
    done
    echo "Done: $closed closed, $skipped skipped."

# Auto-tag everything in data/.
tag:
    {{python}} scripts/tag.py

# Rebuild data/index.json, tags/, data/directory.json.
index:
    {{python}} scripts/index.py

# tag then index.
reindex: tag index

# Search the built index.
search query *args:
    {{python}} scripts/search.py "{{query}}" {{args}}

# Run the hermetic test suites (no network).
test:
    {{python}} -m pytest scripts/test_pipeline.py scripts/test_reddit.py -q

# Manual-review path — run after 'git diff'. No push.
# Stage the workflow's path set and commit.
commit message="process: local batch":
    git add links/ tags/ archive/ data/
    git commit -m "{{message}}"
