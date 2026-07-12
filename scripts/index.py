#!/usr/bin/env python3
"""Build searchable JSON index and tag cross-reference index from processed links."""

import json
import sys
from datetime import datetime, timezone

import yaml
from fetch import DATA_DIR, GENERATED_DATA_FILES, LINKS_DIR


def load_all_links() -> list[dict]:
    """Load all processed links from JSON data files."""
    links = []
    if not DATA_DIR.exists():
        return links

    for json_file in sorted(DATA_DIR.glob("*.json")):
        if json_file.name in GENERATED_DATA_FILES:
            continue  # never re-ingest the generated index/directory aggregates
        try:
            data = json.loads(json_file.read_text())
            links.append(data)
        except Exception:
            continue

    return links


def _created_at_key(link: dict) -> tuple[int, datetime]:
    """Chronological sort key. Dated records sort first (by date); undated last."""
    raw = (link.get("created_at") or "").strip()
    try:
        return (0, datetime.fromisoformat(raw.replace("Z", "+00:00")))
    except (ValueError, AttributeError):
        return (1, datetime.min.replace(tzinfo=timezone.utc))


def build_json_index(links: list[dict]):
    """Build a single searchable JSON index file."""
    # Sort by created date (chronological; undated records last)
    links.sort(key=_created_at_key)

    index = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_links": len(links),
        "links": links,
    }

    index_path = DATA_DIR / "index.json"
    index_path.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"JSON index: {index_path} ({len(links)} links)")


def build_tag_index(links: list[dict]) -> dict[str, list[str]]:
    """Build a tag -> link mapping for cross-referencing."""
    tag_index = {}

    for link in links:
        tags = link.get("tags", [])
        title = link.get("title", "Untitled")
        url = link.get("url", "")
        issue = link.get("issue_number")
        date = link.get("created_at", "")[:10] if link.get("created_at") else ""

        for tag in tags:
            tag_lower = tag.lower()
            if tag_lower not in tag_index:
                tag_index[tag_lower] = []

            tag_index[tag_lower].append({
                "title": title,
                "url": url,
                "issue_number": issue,
                "date": date,
                "category": link.get("category", "other"),
            })

    # Write tag index files
    tags_dir = LINKS_DIR.parent / "tags"
    tags_dir.mkdir(exist_ok=True)

    # Write individual tag files
    for tag, items in tag_index.items():
        tag_file = tags_dir / f"{tag}.json"
        tag_file.write_text(
            json.dumps({"tag": tag, "count": len(items), "links": items}, indent=2),
            encoding="utf-8",
        )

    # Write master tag index
    master = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_tags": len(tag_index),
        "tags": {k: v for k, v in sorted(tag_index.items())},
    }
    (tags_dir / "_tags.json").write_text(
        json.dumps(master, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"Tag index: {len(tag_index)} tags, {sum(len(v) for v in tag_index.values())} link references")
    return tag_index


def build_directory_listing():
    """Build a simple directory listing for browsing."""
    listing = {}

    for category_dir in sorted(LINKS_DIR.iterdir()):
        if category_dir.is_dir() and not category_dir.name.startswith("_"):
            files = []
            for md_file in sorted(category_dir.glob("*.md")):
                try:
                    content = md_file.read_text(encoding="utf-8")
                    # Extract title from frontmatter
                    if content.startswith("---"):
                        parts = content.split("---", 2)
                        if len(parts) >= 3:
                            fm = yaml.safe_load(parts[1]) or {}
                            files.append({
                                "title": fm.get("title", "Untitled"),
                                "url": fm.get("url", ""),
                                "issue_number": fm.get("issue_number"),
                                "tags": fm.get("tags", []),
                                "date": fm.get("created_at", "")[:10] if fm.get("created_at") else "",
                            })
                except Exception:
                    continue

            listing[category_dir.name] = files

    listing_path = DATA_DIR / "directory.json"
    listing_path.write_text(json.dumps(listing, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Directory listing: {len(listing)} categories")


def main():
    print("Building indexes...")

    links = load_all_links()
    if not links:
        print("No processed links found.")
        sys.exit(0)

    build_json_index(links)
    build_tag_index(links)
    build_directory_listing()

    print("Indexing complete.")


if __name__ == "__main__":
    main()
