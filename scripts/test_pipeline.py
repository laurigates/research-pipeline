#!/usr/bin/env python3
"""Hermetic end-to-end tests for the link processing pipeline.

Every stage runs as a subprocess against a throwaway base directory (via the
RESEARCH_BASE_DIR override), so the tests NEVER touch the repo's real links/ and
data/ directories. Each test asserts on real output — a crashing or
misbehaving stage fails the suite.

Run with:  pytest scripts/test_pipeline.py
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent

# Three valid sample links. The AI link carries curated tags whose survival
# through the pipeline is explicitly asserted.
SAMPLES = [
    {
        "slug": "issue-1-ai-breakthrough",
        "issue_number": 1,
        "url": "https://example.com/ai-breakthrough",
        "title": "Major AI Breakthrough in Natural Language Processing",
        "description": "Researchers achieve a new milestone in language understanding",
        "content": "A team announced a breakthrough in natural language processing.",
        "tags": ["ai", "machine learning", "NLP"],
        "category": "technology",
        "created_at": "2024-01-15T10:00:00Z",
        "fetched_at": "2024-01-15T10:05:00Z",
        "author": "testuser",
        "notes": "Interesting development",
        "desired_tags": ["research"],
    },
    {
        "slug": "issue-2-quantum-milestone",
        "issue_number": 2,
        "url": "https://example.com/quantum-computing",
        "title": "Quantum Computing Reaches New Milestone",
        "description": "A 1000-qubit processor demonstrates quantum supremacy",
        "content": "A 1000-qubit quantum processor solved a hard problem.",
        "tags": ["quantum", "computing", "physics"],
        "category": "science",
        "created_at": "2024-01-16T14:00:00Z",
        "fetched_at": "2024-01-16T14:05:00Z",
        "author": "testuser",
        "notes": "Quantum progress",
        "desired_tags": ["physics"],
    },
    {
        "slug": "issue-3-healthtech-funding",
        "issue_number": 3,
        "url": "https://example.com/startup-funding",
        "title": "Series B Funding Round Raises $50M for HealthTech Startup",
        "description": "Health technology startup secures major funding",
        "content": "An AI-powered diagnostics startup raised $50 million in Series B.",
        "tags": ["startup", "healthcare", "funding"],
        "category": "business",
        "created_at": "2024-01-17T09:00:00Z",
        "fetched_at": "2024-01-17T09:05:00Z",
        "author": "testuser",
        "notes": "Healthcare startup funding",
        "desired_tags": ["startup"],
    },
]


def run_stage(script: str, *args: str, base_dir: Path) -> subprocess.CompletedProcess:
    """Run a pipeline script as a subprocess against a throwaway base dir."""
    return subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / script), *args],
        env={**os.environ, "RESEARCH_BASE_DIR": str(base_dir)},
        capture_output=True,
        text=True,
        check=True,
    )


def _markdown_for(sample: dict) -> str:
    return (
        "---\n"
        f"title: {sample['title']}\n"
        f"url: {sample['url']}\n"
        f"issue_number: {sample['issue_number']}\n"
        f"author: {sample['author']}\n"
        f"fetched_at: {sample['fetched_at']}\n"
        f"created_at: {sample['created_at']}\n"
        f"tags: {json.dumps(sample['tags'])}\n"
        f"category: {sample['category']}\n"
        "---\n\n"
        f"# {sample['title']}\n\n## Content\n\n{sample['content']}\n"
    )


@pytest.fixture
def seeded(tmp_path: Path) -> Path:
    """A base dir seeded with the raw per-link JSON records and markdown."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    for sample in SAMPLES:
        record = {k: v for k, v in sample.items() if k != "slug"}
        (data_dir / f"{sample['slug']}.json").write_text(json.dumps(record, indent=2))
        cat_dir = tmp_path / "links" / sample["category"]
        cat_dir.mkdir(parents=True, exist_ok=True)
        (cat_dir / f"{sample['slug']}.md").write_text(_markdown_for(sample))
    return tmp_path


@pytest.fixture
def built(seeded: Path) -> Path:
    """A fully processed corpus: tag.py then index.py have run."""
    run_stage("tag.py", base_dir=seeded)
    run_stage("index.py", base_dir=seeded)
    return seeded


def _load_index(base: Path) -> dict:
    return json.loads((base / "data" / "index.json").read_text())


def _record(base: Path, slug: str) -> dict:
    return json.loads((base / "data" / f"{slug}.json").read_text())


def test_tagging_preserves_curated_tags(seeded: Path):
    """tag.py must enrich, never overwrite, hand-picked tags."""
    run_stage("tag.py", base_dir=seeded)
    tags = set(_record(seeded, "issue-1-ai-breakthrough")["tags"])
    assert {"ai", "machine learning", "NLP"}.issubset(tags), tags


def test_auto_tags_reach_the_index(built: Path):
    """Computed tags must flow JSON -> index (the tag.py/index.py seam)."""
    ai = next(
        l for l in _load_index(built)["links"] if l["issue_number"] == 1
    )
    assert "machine learning" in ai["tags"]


def test_index_has_no_self_ingestion(built: Path):
    """index.py must not re-ingest its own generated aggregates."""
    index = _load_index(built)
    assert index["total_links"] == len(SAMPLES)
    assert all(link.get("title") for link in index["links"])  # no "Untitled" phantoms

    # Running index again must not inflate the count.
    run_stage("index.py", base_dir=built)
    assert _load_index(built)["total_links"] == len(SAMPLES)


def test_search_filters_by_query(built: Path):
    """The core function: a query returns only matching links."""
    out = run_stage("search.py", "quantum", base_dir=built).stdout
    assert "Quantum Computing" in out
    assert "Series B Funding" not in out
    assert "AI Breakthrough" not in out


def test_search_distinguishes_terms(built: Path):
    out = run_stage("search.py", "AI", base_dir=built).stdout
    assert "AI Breakthrough" in out
    assert "Quantum Computing" not in out


def test_search_category_filter(built: Path):
    in_business = run_stage(
        "search.py", "startup", "--category", "business", base_dir=built
    ).stdout
    assert "Series B Funding" in in_business

    wrong_category = run_stage(
        "search.py", "startup", "--category", "science", base_dir=built
    ).stdout
    assert "Series B Funding" not in wrong_category


def test_index_builds_all_artifacts(built: Path):
    assert (built / "data" / "index.json").exists()
    assert (built / "data" / "directory.json").exists()
    assert (built / "tags" / "_tags.json").exists()


def test_save_link_frontmatter_survives_colon_in_title(tmp_path: Path, monkeypatch):
    """Regression: a page title with colons (e.g. "GitHub - ocornut/imgui:
    Dear ImGui: ...") must produce valid YAML frontmatter, or the next stage
    (tag.py) crashes loading it with yaml.safe_load."""
    import yaml

    sys.path.insert(0, str(SCRIPTS_DIR))
    import fetch

    monkeypatch.setattr(fetch, "LINKS_DIR", tmp_path / "links")
    monkeypatch.setattr(fetch, "DATA_DIR", tmp_path / "data")

    entry = {
        "issue_number": 9,
        "author": "tester",
        "created_at": "2024-01-01T00:00:00Z",
        "notes": "",
        "category": "other",
    }
    content = {
        "url": "https://github.com/ocornut/imgui",
        "title": "GitHub - ocornut/imgui: Dear ImGui: Bloat-free graphical user interface",
        "description": "",
        "content": "body",
        "fetched_at": "2024-01-01T00:00:00Z",
    }

    filepath = fetch.save_link(entry, content)
    raw_frontmatter = filepath.read_text(encoding="utf-8").split("---", 2)[1]
    parsed = yaml.safe_load(raw_frontmatter)
    assert parsed["title"] == content["title"]


def test_reddit_provenance_survives_retagging(tmp_path: Path):
    """A reddit-sourced record keeps its `source` flag through tag.py, so it stays
    excluded from the classifier on every future run — even after re-tagging."""
    data = tmp_path / "data"
    data.mkdir()
    record = {
        "issue_number": 7,
        "url": "https://www.reddit.com/r/LocalLLaMA/comments/abc123/slug/",
        "title": "A thread about local AI models and software",
        "description": "discussion",
        "content": "People discuss running ai models and machine learning locally.",
        "tags": ["ai"],
        "category": "technology",
        "created_at": "2024-02-01T00:00:00Z",
        "fetched_at": "2024-02-01T00:05:00Z",
        "author": "reddit-digest",
        "notes": "",
        "desired_tags": [],
        "source": "reddit",
    }
    (data / "issue-7-reddit-thread.json").write_text(json.dumps(record, indent=2))

    run_stage("tag.py", base_dir=tmp_path)
    after = _record(tmp_path, "issue-7-reddit-thread")
    assert after["source"] == "reddit", "provenance flag must persist through re-tagging"
    assert "ai" in after["tags"], "curated tags preserved"

    # A second pass must still preserve it (idempotent provenance).
    run_stage("tag.py", base_dir=tmp_path)
    assert _record(tmp_path, "issue-7-reddit-thread")["source"] == "reddit"


def test_tagging_survives_single_category_corpus(tmp_path: Path):
    """A young corpus where every tagged record lands in one category must not
    crash tag.py: LogisticRegression needs 2+ classes, so the classifier falls
    back to keyword matching instead of calling fit() on a single class."""
    data = tmp_path / "data"
    data.mkdir()
    for i in range(3):
        record = {
            "issue_number": 100 + i,
            "url": f"https://example.com/tech-{i}",
            "title": f"A software engineering article number {i}",
            "description": "code and software development",
            "content": "Discussion of software, code, and programming frameworks.",
            "tags": ["software"],
            "category": "technology",
            "created_at": "2024-03-01T00:00:00Z",
            "fetched_at": "2024-03-01T00:05:00Z",
            "author": "testuser",
            "notes": "",
            "desired_tags": [],
        }
        slug = f"issue-{100 + i}-tech"
        (data / f"{slug}.json").write_text(json.dumps(record, indent=2))
        cat_dir = tmp_path / "links" / "technology"
        cat_dir.mkdir(parents=True, exist_ok=True)
        (cat_dir / f"{slug}.md").write_text(
            _markdown_for({**record, "slug": slug})
        )

    # Must not raise; keyword fallback categorizes all three records.
    run_stage("tag.py", base_dir=tmp_path)
    after = _record(tmp_path, "issue-100-tech")
    assert after["category"] == "technology"
    assert "software" in after["tags"], "curated tags preserved through fallback"


def test_fetch_file_local_path(tmp_path: Path):
    """fetch.py --file ingests a local markdown file without any network call."""
    src = tmp_path / "submission.md"
    src.write_text(
        "---\nurl: https://example.com/test\nnotes: Test article\ntags: [test]\n---\n\nbody\n"
    )
    run_stage("fetch.py", "--file", str(src), base_dir=tmp_path)
    records = list((tmp_path / "data").glob("*.json"))
    assert records, "fetch --file should write a JSON record"
    assert any(
        json.loads(r.read_text()).get("url") == "https://example.com/test"
        for r in records
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
