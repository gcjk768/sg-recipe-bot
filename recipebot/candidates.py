"""Candidate pages mode: the app fetches recipe pages from lists the owner keeps in
data/candidates/<category>.txt, extracts their schema.org Recipe data, and hands the model
clean text so it only has to choose and rewrite (section 2 of the prompt pack)."""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from pathlib import Path

from recipebot.textutil import normalise_url
from recipebot.web import Fetcher, extract_recipe_jsonld, page_title, recipe_node_title, recipe_node_to_text

log = logging.getLogger(__name__)

MAX_CONTENT_CHARS = 6000


@dataclass
class CandidatePage:
    url: str
    title: str
    content: str


def load_candidate_urls(candidates_dir: Path, category: str) -> list[str]:
    """URLs from <dir>/<category>.txt, one per line, blank lines and # comments ignored."""
    path = Path(candidates_dir) / f"{category}.txt"
    if not path.is_file():
        return []
    urls: list[str] = []
    seen: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key = normalise_url(line)
        if key in seen:
            continue
        seen.add(key)
        urls.append(line)
    return urls


def build_candidate_page(url: str, fetcher: Fetcher) -> CandidatePage | None:
    result = fetcher.fetch(url)
    if not result.ok:
        log.warning("candidate %s skipped: %s", url, result.error or f"HTTP {result.status}")
        return None
    nodes = extract_recipe_jsonld(result.text)
    if not nodes:
        log.warning("candidate %s skipped: no schema.org Recipe data", url)
        return None
    node = nodes[0]
    title = recipe_node_title(node) or page_title(result.text) or url
    content = recipe_node_to_text(node)
    if len(content) > MAX_CONTENT_CHARS:
        content = content[:MAX_CONTENT_CHARS].rstrip() + "\n[truncated]"
    return CandidatePage(url=url, title=title, content=content)


def gather_candidates(
    candidates_dir: Path,
    category: str,
    fetcher: Fetcher,
    *,
    exclude_urls: set[str] | None = None,
    n: int = 6,
    rng: random.Random | None = None,
) -> list[CandidatePage]:
    """Up to n pages for the category, in random order, skipping URLs already sent."""
    urls = load_candidate_urls(candidates_dir, category)
    if not urls:
        return []
    exclude = exclude_urls or set()
    pool = [u for u in urls if normalise_url(u) not in exclude]
    if not pool:
        log.warning("all %d candidate urls for %s were already sent", len(urls), category)
        return []
    (rng or random).shuffle(pool)
    pages: list[CandidatePage] = []
    attempts = 0
    for url in pool:
        if len(pages) >= n or attempts >= max(3 * n, n + 3):
            break
        attempts += 1
        page = build_candidate_page(url, fetcher)
        if page is not None:
            pages.append(page)
    return pages


def format_candidate_pages(pages: list[CandidatePage]) -> str:
    blocks = []
    for page in pages:
        blocks.append(f"URL: {page.url}\nTITLE: {page.title}\nCONTENT: {page.content}")
    return "\n\n".join(blocks)
