from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import quote, urljoin

import httpx
from bs4 import BeautifulSoup

from . import database

DEFAULT_BASE_URL = os.getenv("FYDMWD_BASE_URL", "https://fydmwd.com").rstrip("/")
DEFAULT_TIMEOUT_SECONDS = float(os.getenv("HTTP_TIMEOUT_SECONDS", "12"))
DEFAULT_CACHE_TTL_SECONDS = int(os.getenv("SEARCH_CACHE_TTL_SECONDS", "86400"))

PLATFORM_LABELS = {
    "抖音": "douyin",
    "快手": "kuaishou",
    "斗鱼": "douyu",
    "虎牙": "huya",
    "微博": "weibo",
    "B站": "bilibili",
    "哔哩哔哩": "bilibili",
    "小红书": "xiaohongshu",
}

ID_PATTERN = re.compile(
    r"(?P<label>抖音|快手|斗鱼|虎牙|微博|B站|哔哩哔哩|小红书)\s*ID[:：]\s*(?P<uid>[^\s]+)"
)
FOLLOWER_PATTERN = re.compile(r"粉丝[:：]\s*(?P<followers>[^\s]+)")
LIKE_PATTERN = re.compile(r"点赞[:：]\s*(?P<likes>[^\s]+)")


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _platform_cache_key(platforms: list[str]) -> str:
    return "fydmwd:" + ",".join(sorted(platforms))


def _parse_anchor(anchor: Any, base_url: str) -> dict[str, Any] | None:
    text = _clean_text(anchor.get_text(" ", strip=True))
    match = ID_PATTERN.search(text)
    if not match:
        return None

    label = match.group("label")
    platform = PLATFORM_LABELS.get(label)
    if not platform:
        return None

    nickname = _clean_text(text[: match.start()])
    follower_match = FOLLOWER_PATTERN.search(text)
    like_match = LIKE_PATTERN.search(text)
    href = anchor.get("href")

    return {
        "platform": platform,
        "source_platform": "fydmwd",
        "source": "fydmwd_search_page",
        "query_source": "live_search",
        "uid": match.group("uid"),
        "nickname": nickname or None,
        "bio": None,
        "avatar_url": None,
        "followers": follower_match.group("followers") if follower_match else None,
        "likes": like_match.group("likes") if like_match else None,
        "url": urljoin(base_url + "/", href) if href else None,
        "verified": False,
        "verified_reason": None,
        "raw_search_result": {"text": text, "href": href},
    }


def _parse_results(html: str, base_url: str, platforms: list[str], limit: int) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    allowed = set(platforms)
    seen: set[tuple[str, str]] = set()
    results: list[dict[str, Any]] = []
    for anchor in soup.find_all("a", href=True):
        candidate = _parse_anchor(anchor, base_url)
        if not candidate or candidate["platform"] not in allowed:
            continue
        key = (candidate["platform"], str(candidate.get("uid") or candidate.get("url")))
        if key in seen:
            continue
        seen.add(key)
        results.append(candidate)
        if len(results) >= limit:
            break
    return results


def search_fydmwd_account(
    keyword: str,
    platforms: list[str] | None = None,
    limit: int = 10,
    force_refresh: bool = False,
    cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
) -> dict[str, Any]:
    platform_scope = platforms or ["douyin", "kuaishou"]
    cache_key = _platform_cache_key(platform_scope)
    errors: list[str] = []

    if not force_refresh:
        try:
            cached = database.get_recent_search_result(cache_key, keyword, cache_ttl_seconds)
            if cached:
                results = cached.get("results", [])[:limit]
                for result in results:
                    result["query_source"] = "search_cache"
                return {
                    "query": keyword,
                    "platforms": platform_scope,
                    "query_source": "search_cache",
                    "results": results,
                    "errors": [],
                }
        except Exception as exc:
            errors.append(f"MongoDB search cache check failed: {exc}")

    url = f"{DEFAULT_BASE_URL}/search/{quote(keyword)}"
    try:
        with httpx.Client(
            timeout=DEFAULT_TIMEOUT_SECONDS,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        ) as client:
            response = client.get(url)
            response.raise_for_status()
            html = response.text
    except Exception as exc:
        errors.append(f"FYDMWD live search failed: {exc}")
        try:
            database.save_search_result(
                cache_key,
                keyword,
                "failed",
                error=str(exc),
                ttl_seconds=cache_ttl_seconds,
            )
        except Exception:
            pass
        return {
            "query": keyword,
            "platforms": platform_scope,
            "query_source": "live_search",
            "results": [],
            "errors": errors,
        }

    results = _parse_results(html, DEFAULT_BASE_URL, platform_scope, limit)
    try:
        database.save_search_result(
            cache_key,
            keyword,
            "success",
            results=results,
            ttl_seconds=cache_ttl_seconds,
        )
    except Exception as exc:
        errors.append(f"MongoDB search cache save failed: {exc}")

    return {
        "query": keyword,
        "platforms": platform_scope,
        "query_source": "live_search",
        "results": results,
        "errors": errors,
    }
