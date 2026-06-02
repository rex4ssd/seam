from __future__ import annotations
import time
from datetime import date, timedelta

import requests

from ..core.models import Candidate


_SEARCH_URL = "https://api.github.com/search/repositories"
_THROTTLE_S = 0.5   # between requests


def _headers(token: str) -> dict[str, str]:
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _parse_repo(item: dict) -> Candidate:
    return Candidate(
        source="github",
        id=item["full_name"],
        title=item["full_name"],
        description=(item.get("description") or "")[:400],
        stars=item.get("stargazers_count", 0),
        url=item["html_url"],
        pushed_at=item.get("pushed_at", ""),
        language=item.get("language") or "",
        topics=item.get("topics") or [],
        metadata=item,
    )


def search_github(
    queries: list[str],
    token: str = "",
    min_stars: int = 200,
    max_age_days: int = 90,
    per_query: int = 30,
) -> list[Candidate]:
    """
    Run each query against GitHub Search API, deduplicate, filter, return Candidates.
    Handles 429 with Retry-After (P-S01). Deduplicates across queries (P-S03).
    """
    cutoff_date = (date.today() - timedelta(days=max_age_days)).isoformat()
    headers = _headers(token)
    seen: set[str] = set()
    candidates: list[Candidate] = []

    for query in queries:
        params = {
            "q": query,
            "sort": "stars",
            "order": "desc",
            "per_page": per_query,
        }
        try:
            response = _get_with_retry(
                _SEARCH_URL, params=params, headers=headers, retries=3
            )
        except requests.RequestException as exc:
            # non-fatal: skip this query, warn caller via stderr
            import sys
            print(f"[seam] github query failed: {query!r}: {exc}", file=sys.stderr)
            time.sleep(_THROTTLE_S)
            continue

        items = response.get("items") or []
        for item in items:
            full_name = item.get("full_name", "")
            if full_name in seen:
                continue                               # P-S03 dedup
            seen.add(full_name)

            stars = item.get("stargazers_count", 0)
            if stars < min_stars:
                continue

            pushed = item.get("pushed_at", "")[:10]  # YYYY-MM-DD
            if pushed and pushed < cutoff_date:
                continue

            candidates.append(_parse_repo(item))

        time.sleep(_THROTTLE_S)

    # sort by stars descending
    candidates.sort(key=lambda c: c.stars, reverse=True)
    return candidates


def _get_with_retry(
    url: str,
    *,
    params: dict,
    headers: dict,
    retries: int = 3,
) -> dict:
    """GET with exponential backoff; honours Retry-After on 429. (P-S01)"""
    for attempt in range(retries):
        resp = requests.get(url, params=params, headers=headers, timeout=15)

        if resp.status_code == 200:
            return resp.json()

        if resp.status_code == 429 or resp.status_code == 403:
            retry_after = int(resp.headers.get("Retry-After", 2 ** (attempt + 1)))
            import sys
            print(f"[seam] rate limited ({resp.status_code}), waiting {retry_after}s …", file=sys.stderr)
            time.sleep(retry_after)
            continue

        resp.raise_for_status()

    raise requests.RequestException(f"Gave up after {retries} retries: {url}")
