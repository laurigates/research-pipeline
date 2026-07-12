#!/usr/bin/env python3
"""Search and query processed links from the research repository."""

import json
import sys

from fetch import DATA_DIR, LINKS_DIR


SEARCHABLE_FIELDS = ("title", "url", "description", "content", "notes", "category")


def _matches(link: dict, query_lower: str) -> bool:
    """True if the query substring appears in any searchable field or tag."""
    haystack = " ".join(str(link.get(field, "") or "") for field in SEARCHABLE_FIELDS)
    haystack += " " + " ".join(str(t) for t in link.get("tags", []) or [])
    return query_lower in haystack.lower()


def search_links(query: str, category: str = None, max_results: int = 50) -> list[dict]:
    """Search links by query string, optionally filtered by category."""
    links = []
    query_lower = query.lower()

    # Load from JSON index if available
    index_path = DATA_DIR / "index.json"
    if index_path.exists():
        try:
            data = json.loads(index_path.read_text())
            # Apply the query filter — the whole point of a search.
            links = [l for l in data.get("links", []) if _matches(l, query_lower)]
        except Exception:
            pass

    if not links:
        # Fallback: scan markdown files for the query term.
        for category_dir in LINKS_DIR.iterdir():
            if not category_dir.is_dir():
                continue
            for md_file in category_dir.glob("*.md"):
                try:
                    content = md_file.read_text(encoding="utf-8")
                    if query_lower in content.lower():
                        links.append({"title": md_file.stem, "path": str(md_file)})
                except Exception:
                    continue

    # Filter by category if specified
    if category:
        links = [l for l in links if l.get("category", "").lower() == category.lower()]

    # Sort by date (newest first)
    links.sort(key=lambda l: l.get("created_at", ""), reverse=True)

    return links[:max_results]


def list_categories() -> list[str]:
    """List all available categories."""
    categories = []
    if LINKS_DIR.exists():
        for item in LINKS_DIR.iterdir():
            if item.is_dir() and not item.name.startswith("_"):
                categories.append(item.name)
    return sorted(categories)


def list_tags() -> dict:
    """Load the master tag index."""
    tags_path = LINKS_DIR.parent / "tags" / "_tags.json"
    if tags_path.exists():
        try:
            return json.loads(tags_path.read_text())
        except Exception:
            pass
    return {}


def main():
    if len(sys.argv) < 2:
        print("Usage: search.py <query> [--category <category>] [--max <number>]")
        print("       search.py --categories")
        print("       search.py --tags")
        sys.exit(1)

    args = sys.argv[1:]

    # List categories
    if "--categories" in args:
        cats = list_categories()
        print(f"Categories ({len(cats)}):")
        for cat in cats:
            print(f"  - {cat}")
        return

    # List tags
    if "--tags" in args:
        tags_data = list_tags()
        tags = tags_data.get("tags", {})
        print(f"Tags ({tags_data.get('total_tags', 0)}):")
        for tag, items in sorted(tags.items()):
            print(f"  {tag}: {len(items)} links")
        return

    # Parse arguments
    query = args[0]
    category = None
    max_results = 50

    i = 1
    while i < len(args):
        if args[i] == "--category" and i + 1 < len(args):
            category = args[i + 1]
            i += 2
        elif args[i] == "--max" and i + 1 < len(args):
            try:
                max_results = int(args[i + 1])
            except ValueError:
                print(f"Invalid max value: {args[i + 1]}")
                sys.exit(1)
            i += 2
        else:
            i += 1

    # Search
    results = search_links(query, category, max_results)

    if not results:
        print(f"No results found for: {query}")
        if category:
            print(f"Category filter: {category}")
        return

    print(f"Found {len(results)} results for: {query}")
    if category:
        print(f"Category: {category}\n")

    for i, link in enumerate(results, 1):
        title = link.get("title", "Untitled")
        url = link.get("url", "")
        date = link.get("created_at", "")[:10] if link.get("created_at") else ""
        tags = link.get("tags", [])
        category = link.get("category", "")

        print(f"\n{i}. {title}")
        print(f"   URL: {url}")
        print(f"   Date: {date}")
        print(f"   Category: {category}")
        if tags:
            print(f"   Tags: {', '.join(tags)}")

    print(f"\nTotal: {len(results)} links")


if __name__ == "__main__":
    main()
