#!/usr/bin/env python3
"""Checkbox-scrape helpers for the reddit-digest workflow.

Two subcommands, both thin so the workflow shell drives the fetch->tag->index
pipeline (mirroring process-link.yml):

  urls   Read OLD_BODY / NEW_BODY env vars (from the issues.edited payload) and
         print the reddit thread URLs that were newly checked, one per line.
         Empty output (e.g. a non-body edit) means "nothing to scrape".

  mark   Read scraped URLs from stdin and append a "✓ scraped" marker to their
         checkbox lines in the issue body, so done items are visible and a
         later edit doesn't re-trigger them.

Env for `mark`: GITHUB_TOKEN, GITHUB_REPOSITORY.
"""

import argparse
import os
import sys

import requests

from reddit_fetch import first_reddit_url, newly_checked_urls

GITHUB_API = "https://api.github.com"
SCRAPED_MARKER = "✓ scraped"


def cmd_urls():
    old_body = os.environ.get("OLD_BODY", "")
    new_body = os.environ.get("NEW_BODY", "")
    for url in newly_checked_urls(old_body, new_body):
        print(url)


def cmd_mark(issue_number: int):
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not token or not repo:
        print("Error: GITHUB_TOKEN and GITHUB_REPOSITORY are required for mark.")
        sys.exit(1)

    scraped = {line.strip() for line in sys.stdin if line.strip()}
    if not scraped:
        return

    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }
    url = f"{GITHUB_API}/repos/{repo}/issues/{issue_number}"
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    body = resp.json().get("body", "") or ""

    new_lines = []
    for line in body.splitlines():
        line_url = first_reddit_url(line)
        if line_url in scraped and SCRAPED_MARKER not in line:
            line = f"{line} — {SCRAPED_MARKER}"
        new_lines.append(line)
    new_body = "\n".join(new_lines)

    if new_body != body:
        patch = requests.patch(url, headers=headers, json={"body": new_body}, timeout=30)
        patch.raise_for_status()
        print(f"Marked {len(scraped)} thread(s) as scraped on issue #{issue_number}.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("urls", help="Print newly-checked reddit URLs from OLD_BODY/NEW_BODY env")
    mark = sub.add_parser("mark", help="Append a scraped marker to scraped URLs' lines")
    mark.add_argument("issue_number", type=int)
    args = parser.parse_args()

    if args.command == "urls":
        cmd_urls()
    elif args.command == "mark":
        cmd_mark(args.issue_number)


if __name__ == "__main__":
    main()
