#!/usr/bin/env python3
"""Fetch content from a URL submitted via GitHub issue and save as markdown."""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
import yaml
from bs4 import BeautifulSoup


# Base dir is overridable via RESEARCH_BASE_DIR so the pipeline (and its tests)
# can run against a throwaway directory instead of the repo's real links/ and data/.
BASE_DIR = Path(os.environ.get("RESEARCH_BASE_DIR") or Path(__file__).parent.parent)
LINKS_DIR = BASE_DIR / "links"
DATA_DIR = BASE_DIR / "data"

# Generated aggregate files that live in DATA_DIR but are NOT per-link records.
# Pipeline stages must skip these when scanning for links, or the index ingests
# itself and produces phantom empty entries.
GENERATED_DATA_FILES = {"index.json", "directory.json"}


def _extract_section(body: str, heading: str) -> str:
    """Return the text under a `## heading` section, with HTML comments stripped.

    Matches from the heading line up to the next `## ` heading (or end of body),
    then removes `<!-- ... -->` placeholders so the issue template's hint comments
    are never mistaken for submitted content.
    """
    match = re.search(
        rf"^##\s*{re.escape(heading)}[^\n]*\n(.*?)(?=^##\s|\Z)",
        body,
        re.DOTALL | re.MULTILINE,
    )
    if not match:
        return ""
    text = re.sub(r"<!--.*?-->", "", match.group(1), flags=re.DOTALL)
    return text.strip()


def get_issue_content(issue_number: int) -> dict:
    """Extract URL and notes from a GitHub issue body."""
    github_token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")

    url = f"https://api.github.com/repos/{repo}/issues/{issue_number}"
    headers = {"Authorization": f"token {github_token}"} if github_token else {}

    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    issue = resp.json()

    body = issue.get("body", "") or ""

    # URL: first non-empty line of the URL section.
    url_section = _extract_section(body, "URL")
    extracted_url = next(
        (line.strip() for line in url_section.splitlines() if line.strip()), ""
    )

    # Notes: the free text the submitter wrote (comments already stripped).
    notes = _extract_section(body, "Notes")

    # Desired tags: split the tags section on commas/whitespace into clean tokens.
    tags_section = _extract_section(body, "Desired tags")
    desired_tags = [t for t in re.split(r"[,\s]+", tags_section) if t]

    return {
        "issue_number": issue_number,
        "url": extracted_url,
        "notes": notes,
        "desired_tags": desired_tags,
        "created_at": issue.get("created_at", ""),
        "author": issue.get("user", {}).get("login", "unknown"),
    }


def fetch_page_content(url: str) -> dict | None:
    """Fetch and extract readable content from a URL.

    Reddit thread URLs are routed through PRAW (reddit_fetch), which returns the
    same content-dict shape; everything else uses the generic HTML extractor.
    """
    from reddit_fetch import fetch_reddit_content, is_reddit_url

    if is_reddit_url(url):
        return fetch_reddit_content(url)

    headers = {
        "User-Agent": "Mozilla/5.0 (Research Bot; +https://github.com/laurigates/research)"
    }

    try:
        resp = requests.get(url, headers=headers, timeout=30, allow_redirects=True)
        resp.raise_for_status()
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return None

    soup = BeautifulSoup(resp.text, "lxml")

    # Remove script/style elements
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()

    # Try to get the main content
    main = soup.find("main") or soup.find("article") or soup.find("body")

    if main:
        text = main.get_text(separator="\n", strip=True)
    else:
        text = soup.get_text(separator="\n", strip=True)

    # Get title
    title = soup.find("title")
    if not title:
        title = soup.find("h1")
    title_text = title.get_text(strip=True) if title else urlparse(url).netloc

    # Get meta description
    meta_desc = soup.find("meta", attrs={"name": "description"})
    description = meta_desc.get("content", "") if meta_desc else ""

    return {
        "url": url,
        "title": title_text,
        "description": description,
        "content": text[:10000],  # Cap content length
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": "web",
    }


def save_link(entry: dict, content: dict):
    """Save processed link as markdown file with YAML frontmatter."""
    # Create a slug from the title
    slug = re.sub(r"[^a-z0-9]+", "-", content["title"].lower().strip()).strip("-")
    slug = f"issue-{entry['issue_number']}-{slug}"[:80]

    # Determine category directory (will be updated by tag.py)
    category = entry.get("category", "other")
    category_dir = LINKS_DIR / category

    filename = f"{slug}.md"
    filepath = category_dir / filename

    category_dir.mkdir(parents=True, exist_ok=True)

    # Build the YAML frontmatter via yaml.dump so values containing colons,
    # quotes, or other YAML-significant characters (e.g. a page title like
    # "GitHub - ocornut/imgui: Dear ImGui: ...") are escaped correctly.
    # Hand-rolled f-string interpolation here produced invalid YAML that later
    # crashed tag.py's yaml.safe_load.
    frontmatter = {
        "title": content["title"],
        "url": content["url"],
        "issue_number": entry["issue_number"],
        "author": entry["author"],
        "fetched_at": content["fetched_at"],
        "created_at": entry["created_at"],
        "source": content.get("source", "web"),
        "tags": [],
        "category": category,
    }
    fm_text = yaml.dump(frontmatter, allow_unicode=True, sort_keys=False).strip()

    # Build markdown content
    md_content = f"""---
{fm_text}
---

# {content['title']}

**Source:** [{content['url']}]({content['url']})

{f'**Notes:** {entry["notes"]} ' if entry.get('notes') else ''}

---

## Content

{content['content']}
"""

    filepath.write_text(md_content, encoding="utf-8")

    # Also save raw JSON for the index
    DATA_DIR.mkdir(exist_ok=True)
    json_path = DATA_DIR / f"{slug}.json"
    json_path.write_text(
        json.dumps({**entry, **content, "tags": entry.get("tags", [])}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"Saved: {filepath}")
    return filepath


def load_file_entry(filepath: Path) -> dict:
    """Load entry data from a local markdown file."""
    content = filepath.read_text(encoding="utf-8")
    fm_match = re.search(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not fm_match:
        print(f"Error: No YAML frontmatter in {filepath}")
        sys.exit(1)
    fm = yaml.safe_load(fm_match.group(1))
    return {
        "issue_number": fm.get("issue_number", 0),
        "url": fm.get("url", ""),
        "notes": fm.get("notes", ""),
        "desired_tags": fm.get("tags", []),
        "created_at": fm.get("created_at", datetime.now(timezone.utc).isoformat()),
        "author": fm.get("author", "local"),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Fetch content from a GitHub issue and save as markdown.",
    )
    parser.add_argument(
        "issue_number",
        type=int,
        nargs="?",
        default=None,
        help="The GitHub issue number to process",
    )
    parser.add_argument(
        "--file",
        type=str,
        help="Path to a local markdown file with YAML frontmatter containing a URL",
    )
    parser.add_argument(
        "--url",
        type=str,
        help="Scrape an arbitrary URL directly (used by the reddit checkbox-scrape "
        "workflow). Reddit URLs route through PRAW automatically.",
    )
    parser.add_argument(
        "--source-issue",
        type=int,
        default=0,
        help="Issue number to attribute a --url scrape to (e.g. the digest issue).",
    )
    args = parser.parse_args()

    if args.issue_number is None and not args.file and not args.url:
        parser.print_help()
        sys.exit(1)

    if args.url:
        print(f"Processing: {args.url} (direct --url)")
        content = fetch_page_content(args.url)
        if not content:
            print(f"Error: Could not fetch content from {args.url}")
            sys.exit(1)
        entry = {
            "issue_number": args.source_issue,
            "url": args.url,
            "notes": f"Curated from digest issue #{args.source_issue}"
            if args.source_issue
            else "",
            "desired_tags": [],
            "created_at": content["fetched_at"],
            "author": "reddit-digest",
        }
        filepath = save_link(entry, content)
        print(f"Done: {filepath}")
        return

    if args.file:
        filepath = Path(args.file)
        if not filepath.exists():
            print(f"Error: File not found: {filepath}")
            sys.exit(1)
        entry = load_file_entry(filepath)
        if not entry["url"]:
            print(f"Error: No URL found in {filepath}")
            sys.exit(1)
        print(f"Processing: {entry['url']} (from file)")
        # Skip network fetch for local files, use placeholder content
        content = {
            "url": entry["url"],
            "title": Path(args.file).stem.replace("-", " ").title(),
            "description": "Local file entry",
            "content": f"Content from local file: {filepath.name}",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "source": "local",
        }
    else:
        issue_number = args.issue_number
        entry = get_issue_content(issue_number)
        if not entry["url"]:
            print(f"Error: No URL found in issue #{issue_number}")
            sys.exit(1)
        print(f"Processing: {entry['url']} (issue #{issue_number})")

        # Fetch content
        content = fetch_page_content(entry["url"])
        if not content:
            print(f"Error: Could not fetch content from {entry['url']}")
            sys.exit(1)

    # Save
    filepath = save_link(entry, content)
    print(f"Done: {filepath}")


if __name__ == "__main__":
    main()
