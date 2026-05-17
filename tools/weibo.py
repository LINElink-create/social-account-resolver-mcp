from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup

from . import database

WEIBO_SEARCH_URL = "https://m.weibo.cn/api/container/getIndex"
DEFAULT_TIMEOUT_SECONDS = float(os.getenv("HTTP_TIMEOUT_SECONDS", "12"))
DEFAULT_CACHE_TTL_SECONDS = int(os.getenv("SEARCH_CACHE_TTL_SECONDS", "86400"))


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    return BeautifulSoup(str(value), "lxml").get_text(" ", strip=True)


def _iter_users(value: Any) -> list[dict[str, Any]]:
    users: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if isinstance(value.get("user"), dict):
            users.append(value["user"])
        if isinstance(value.get("users"), list):
            users.extend(item for item in value["users"] if isinstance(item, dict))
        for item in value.values():
            users.extend(_iter_users(item))
    elif isinstance(value, list):
        for item in value:
            users.extend(_iter_users(item))
    return users


def _normalize_user(raw: dict[str, Any]) -> dict[str, Any]:
    uid = raw.get("id") or raw.get("idstr")
    profile_url = raw.get("profile_url")
    if profile_url and profile_url.startswith("/"):
        profile_url = f"https://m.weibo.cn{profile_url}"
    if not profile_url and uid:
        profile_url = f"https://weibo.com/u/{uid}"

    return {
        "platform": "weibo",
        "source_platform": "weibo",
        "source": "m_weibo_search_api",
        "query_source": "live_search",
        "uid": str(uid) if uid is not None else None,
        "nickname": _clean_text(raw.get("screen_name")),
        "bio": _clean_text(raw.get("description") or raw.get("desc1")),
        "avatar_url": raw.get("avatar_hd") or raw.get("profile_image_url"),
        "followers": raw.get("followers_count"),
        "url": profile_url,
        "verified": bool(raw.get("verified")),
        "verified_reason": _clean_text(raw.get("verified_reason")),
        "raw_search_result": raw,
    }


def _cached_response(
    person_name: str,
    aliases: list[str] | None,
    limit: int,
    force_refresh: bool,
    cache_ttl_seconds: int,
) -> dict[str, Any] | None:
    if force_refresh:
        return None
    try:
        profile = database.find_person_profile(person_name, aliases, include_candidates=True)
        reliable = [
            item
            for item in profile.get("reliable_accounts", [])
            if item.get("platform") == "weibo"
        ]
        if reliable:
            return {
                "query": person_name,
                "platform": "weibo",
                "query_source": "reliable_cache",
                "results": reliable[:limit],
                "errors": [],
            }

        cached = database.get_recent_search_result("weibo", person_name, cache_ttl_seconds)
        if cached:
            results = cached.get("results", [])[:limit]
            for result in results:
                result["query_source"] = "search_cache"
            return {
                "query": person_name,
                "platform": "weibo",
                "query_source": "search_cache",
                "results": results,
                "errors": [],
            }
    except Exception as exc:
        return {
            "query": person_name,
            "platform": "weibo",
            "query_source": "cache_error",
            "results": [],
            "errors": [f"MongoDB cache check failed: {exc}"],
        }
    return None


def search_weibo_user(
    person_name: str,
    aliases: list[str] | None = None,
    limit: int = 10,
    force_refresh: bool = False,
    cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
) -> dict[str, Any]:
    cached = _cached_response(person_name, aliases, limit, force_refresh, cache_ttl_seconds)
    if cached and cached["query_source"] != "cache_error":
        return cached

    errors = cached.get("errors", []) if cached else []
    cookie = os.getenv("WEIBO_COOKIE", "").strip()
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 "
            "Mobile/15E148 Safari/604.1"
        ),
        "Referer": f"https://m.weibo.cn/search?containerid=100103type%3D3%26q%3D{quote(person_name)}",
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
    }
    if cookie:
        headers["Cookie"] = cookie

    try:
        with httpx.Client(
            timeout=DEFAULT_TIMEOUT_SECONDS,
            follow_redirects=True,
            headers=headers,
        ) as client:
            response = client.get(
                WEIBO_SEARCH_URL,
                params={
                    "containerid": f"100103type=3&q={person_name}&t=0",
                    "page_type": "searchall",
                },
            )
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        errors.append(f"Weibo live search failed: {exc}")
        try:
            database.save_search_result(
                "weibo",
                person_name,
                "failed",
                error=str(exc),
                ttl_seconds=cache_ttl_seconds,
            )
        except Exception:
            pass
        return {
            "query": person_name,
            "platform": "weibo",
            "query_source": "live_search",
            "results": [],
            "errors": errors,
        }

    raw_users = _iter_users(payload.get("data", {}).get("cards", []))
    seen: set[str] = set()
    results: list[dict[str, Any]] = []
    for raw_user in raw_users:
        normalized = _normalize_user(raw_user)
        key = normalized.get("uid") or normalized.get("url") or normalized.get("nickname")
        if not key or key in seen:
            continue
        seen.add(str(key))
        results.append(normalized)
        if len(results) >= limit:
            break

    try:
        database.save_search_result(
            "weibo",
            person_name,
            "success",
            results=results,
            ttl_seconds=cache_ttl_seconds,
        )
    except Exception as exc:
        errors.append(f"MongoDB search cache save failed: {exc}")

    return {
        "query": person_name,
        "platform": "weibo",
        "query_source": "live_search",
        "results": results,
        "errors": errors,
    }
