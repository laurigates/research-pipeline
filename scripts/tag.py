#!/usr/bin/env python3
"""Auto-tag processed links using ML classification and update file structure."""

import json
import sys
from pathlib import Path

import yaml
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression

from fetch import DATA_DIR, GENERATED_DATA_FILES, LINKS_DIR
from reddit_fetch import is_reddit_url


def _is_reddit_sourced(link: dict) -> bool:
    """True if a record originated from Reddit, by DURABLE PROVENANCE — not by
    inspecting the content.

    Reddit's Responsible Builder Policy forbids using Reddit content as input for
    model training. We keep such records out of the classifier entirely. The flag
    is stamped at fetch time (`source: "reddit"`) and survives re-tagging and any
    later rewriting/summarizing of the text — which is the whole point: a
    content-based check would let a rewritten thread silently leak back into
    training. The URL check is a fallback for legacy records written before the
    `source` field existed.
    """
    return link.get("source") == "reddit" or is_reddit_url(link.get("url", ""))


# Predefined category definitions with seed keywords for initial training
CATEGORIES = {
    "technology": {
        "keywords": ["software", "api", "algorithm", "code", "programming", "ai", "machine learning", "blockchain", "cloud", "database", "cybersecurity", "hardware", "robotics", "internet", "web", "app", "platform", "digital", "computing", "data science", "automation", "devops", "startup", "tech"],
        "description": "Technology, software, AI, cybersecurity, startups",
    },
    "science": {
        "keywords": ["physics", "chemistry", "biology", "research", "experiment", "hypothesis", "quantum", "genetics", "astronomy", "climate", "environment", "particle", "molecule", "evolution", "scientific method"],
        "description": "Pure and applied sciences",
    },
    "business": {
        "keywords": ["market", "revenue", "startup", "investment", "finance", "economy", "strategy", "leadership", "management", "entrepreneur", "growth", "profit", "company", "industry", "trade", "ecommerce", "marketing", "brand"],
        "description": "Business, finance, entrepreneurship, markets",
    },
    "health": {
        "keywords": ["health", "medical", "medicine", "therapy", "nutrition", "fitness", "mental health", "disease", "treatment", "hospital", "doctor", "patient", "drug", "pharma", "wellness", "public health", "epidemic"],
        "description": "Health, medicine, wellness, public health",
    },
    "education": {
        "keywords": ["education", "learning", "teaching", "school", "university", "course", "student", "curriculum", "pedagogy", "training", "skill", "academic", "research method", "study", "e-learning"],
        "description": "Education, learning, academia, training",
    },
}


def load_existing_tags() -> list[dict]:
    """Load all processed links from JSON data files."""
    links = []
    if not DATA_DIR.exists():
        return links

    for json_file in DATA_DIR.glob("*.json"):
        if json_file.name in GENERATED_DATA_FILES:
            continue  # skip generated aggregates, not per-link records
        try:
            data = json.loads(json_file.read_text())
            data["_filepath"] = json_file
            links.append(data)
        except Exception:
            continue

    return links


def train_classifier(links: list[dict]) -> tuple[LogisticRegression, TfidfVectorizer]:
    """Train a simple classifier on links that already have tags.

    Reddit-sourced records are excluded from the training set so Reddit content
    is never an input to the model fit (see _is_reddit_sourced)."""
    tagged = [
        l
        for l in links
        if l.get("tags") and len(l["tags"]) > 0 and not _is_reddit_sourced(l)
    ]

    if len(tagged) < 3:
        print("Not enough tagged examples for ML. Using keyword fallback.")
        return None, None

    texts = []
    labels = []

    for link in tagged:
        text_parts = []
        if link.get("title"):
            text_parts.append(link["title"])
        if link.get("description"):
            text_parts.append(link["description"])
        if link.get("content"):
            text_parts.append(link["content"][:2000])
        if link.get("notes"):
            text_parts.append(link["notes"])

        text = " ".join(text_parts)
        # Use primary category as label
        primary_cat = link.get("category", "other")
        if primary_cat in CATEGORIES:
            texts.append(text)
            labels.append(primary_cat)

    # LogisticRegression needs samples from at least 2 classes; with a young
    # corpus every tagged record can land in a single category. Fall back to
    # keyword matching until the data spans 2+ categories.
    if len(texts) < 2 or len(set(labels)) < 2:
        print("Tagged examples span fewer than 2 categories. Using keyword fallback.")
        return None, None

    vectorizer = TfidfVectorizer(max_features=500, stop_words="english")
    X = vectorizer.fit_transform(texts)
    y = labels

    clf = LogisticRegression(max_iter=1000, C=1.0)
    clf.fit(X, y)

    return clf, vectorizer


def classify_text(text: str, clf, vectorizer) -> str | None:
    """Classify a text string into a category."""
    if clf is None:
        return None

    X = vectorizer.transform([text])
    prediction = str(clf.predict(X)[0])
    return prediction


def keyword_match(text: str) -> str:
    """Fallback: match text against predefined keyword lists."""
    text_lower = text.lower()
    scores = {}

    for category, info in CATEGORIES.items():
        score = sum(1 for kw in info["keywords"] if kw.lower() in text_lower)
        if score > 0:
            scores[category] = score

    if scores:
        return max(scores, key=scores.get)
    return "other"


def update_file_tags(filepath: Path, tags: list[str], category: str):
    """Update the YAML frontmatter of a markdown file with new tags and category."""
    content = filepath.read_text(encoding="utf-8")

    # Parse frontmatter
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            fm = yaml.safe_load(parts[1]) or {}
            fm["tags"] = sorted(set(tags))
            fm["category"] = category

            # Rebuild file
            new_content = f"---\n{yaml.dump(fm, allow_unicode=True, sort_keys=False).strip()}\n---\n{parts[2]}"
            filepath.write_text(new_content, encoding="utf-8")


def move_file_to_category(filepath: Path, new_category: str) -> Path:
    """Move a file to the correct category directory. Frontmatter is written by
    the caller AFTER the move, so this function only relocates the file."""
    new_category_dir = LINKS_DIR / new_category
    new_category_dir.mkdir(exist_ok=True)

    new_path = new_category_dir / filepath.name

    if filepath.exists() and filepath != new_path:
        filepath.rename(new_path)
        print(f"  Moved to {new_category}/: {filepath.name}")

    return new_path


def update_record_tags(json_path: Path, link: dict, tags: list[str], category: str):
    """Persist computed tags + category back to the per-link JSON record so the
    index (which reads JSON, not markdown) reflects the auto-tagging."""
    record = {k: v for k, v in link.items() if k != "_filepath"}
    record["tags"] = tags
    record["category"] = category
    json_path.write_text(
        json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def main():
    print("Loading processed links...")
    links = load_existing_tags()

    if not links:
        print("No processed links found. Run fetch.py first.")
        sys.exit(0)

    print(f"Found {len(links)} links. Training classifier...")

    # Train ML classifier on existing tagged data
    clf, vectorizer = train_classifier(links)

    # Process each link
    for link in links:
        title = link.get("title", "")
        content = link.get("content", "") or ""
        description = link.get("description", "") or ""
        notes = link.get("notes", "") or ""
        desired_tags = link.get("desired_tags", [])

        # Combine text for classification
        text_for_classify = f"{title} {description} {content[:3000]} {notes}"

        # Keyword categorization is pure Python string matching — no model — so
        # it is always safe to run on any content.
        kw_category = keyword_match(text_for_classify)

        if _is_reddit_sourced(link):
            # Reddit content must not pass through the classifier at all (neither
            # fit nor transform/predict), so categorize it by keyword only.
            category = kw_category
        else:
            ml_category = classify_text(text_for_classify, clf, vectorizer)
            category = ml_category or kw_category

        # Generate tags from content analysis. Start from the union of the
        # record's existing (curated) tags and the submitter's desired tags so
        # auto-tagging is ADDITIVE and never destroys hand-picked tags.
        existing_tags = link.get("tags", []) or []
        tags = list(set(existing_tags) | set(desired_tags))

        # Add category as primary tag
        if category and category != "other":
            tags.append(category)

        # Extract additional tags from title and description
        text_lower = f"{title} {description}".lower()
        for cat_name, cat_info in CATEGORIES.items():
            if cat_name == category:
                continue
            matches = [kw for kw in cat_info["keywords"] if kw.lower() in text_lower]
            if matches:
                # Add top matching keywords as tags
                tags.extend(matches[:3])

        # Clean and deduplicate tags
        tags = sorted(set(t for t in tags if t and len(t) > 1))

        # Persist to the JSON record first — this is what index.py/search.py read.
        update_record_tags(link["_filepath"], link, tags, category)

        # Then relocate + update the corresponding markdown files (move, THEN
        # write frontmatter, so the tags aren't wiped by the relocation).
        slug_base = Path(link["_filepath"]).stem
        md_files = list(LINKS_DIR.rglob(f"{slug_base}*.md"))

        for md_file in md_files:
            moved = move_file_to_category(md_file, category)
            update_file_tags(moved, tags, category)

        print(f"  #{link.get('issue_number')}: {title[:60]} -> [{category}] tags={tags}")

    print("Tagging complete.")


if __name__ == "__main__":
    main()
