from __future__ import annotations

import os
import re
import time
from typing import Any

import httpx
from bs4 import BeautifulSoup

from . import database

BILIBILI_SEARCH_URL = "https://api.bilibili.com/x/web-interface/search/type"
DEFAULT_TIMEOUT_SECONDS = float(os.getenv("HTTP_TIMEOUT_SECONDS", "12"))
DEFAULT_CACHE_TTL_SECONDS = int(os.getenv("SEARCH_CACHE_TTL_SECONDS", "86400"))


def _strip_html(value: Any) -> str | None:
    if value is None:
        return None
    text = BeautifulSoup(str(value), "lxml").get_text("", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_avatar(url: str | None) -> str | None:
    if not url:
        return None
    if url.startswith("//"):
        return f"https:{url}"
    return url


def _normalize_user(raw: dict[str, Any]) -> dict[str, Any]:
    uid = raw.get("mid")
    official = raw.get("official_verify") or {}
    verify_info = raw.get("verify_info") or official.get("desc") or ""
    return {
        "platform": "bilibili",
        "source_platform": "bilibili",
        "source": "bilibili_search_api",
        "query_source": "live_search",
        "uid": str(uid) if uid is not None else None,
        "nickname": _strip_html(raw.get("uname")),
        "bio": _strip_html(raw.get("usign") or verify_info),
        "avatar_url": _normalize_avatar(raw.get("upic")),
        "followers": raw.get("fans"),
        "url": f"https://space.bilibili.com/{uid}" if uid is not None else None,
        "verified": bool(verify_info or official.get("type", -1) != -1),
        "verified_reason": _strip_html(verify_info),
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
            if item.get("platform") == "bilibili"
        ]
        if reliable:
            return {
                "query": person_name,
                "platform": "bilibili",
                "query_source": "reliable_cache",
                "results": reliable[:limit],
                "errors": [],
            }

        cached = database.get_recent_search_result(
            "bilibili", person_name, cache_ttl_seconds
        )
        if cached:
            results = cached.get("results", [])[:limit]
            for result in results:
                result["query_source"] = "search_cache"
            return {
                "query": person_name,
                "platform": "bilibili",
                "query_source": "search_cache",
                "results": results,
                "errors": [],
            }
    except Exception as exc:
        return {
            "query": person_name,
            "platform": "bilibili",
            "query_source": "cache_error",
            "results": [],
            "errors": [f"MongoDB cache check failed: {exc}"],
        }
    return None


def search_bilibili_user(
    person_name: str,
    aliases: list[str] | None = None,
    limit: int = 10,
    force_refresh: bool = False,
    cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
) -> dict[str, Any]:
    cached = _cached_response(
        person_name, aliases, limit, force_refresh, cache_ttl_seconds
    )
    if cached and cached["query_source"] != "cache_error":
        return cached

    errors = cached.get("errors", []) if cached else []
    payload: dict[str, Any] | None = None
    last_error: Exception | None = None
    header_profiles = [
        {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://search.bilibili.com/",
            "Accept": "application/json, text/plain, */*",
        },
        {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.bilibili.com/",
            "Accept": "application/json, text/plain, */*",
        },
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Referer": "https://search.bilibili.com/upuser",
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://search.bilibili.com",
        },
    ]
    try:
        base_params = {
                    "search_type": "bili_user",
                    "keyword": person_name,
                    "page": 1,
        }
        for headers in header_profiles:
            if payload is not None:
                break
            with httpx.Client(
                timeout=DEFAULT_TIMEOUT_SECONDS,
                follow_redirects=True,
                headers=headers,
            ) as client:
                for order in ("fans", "totalrank", None):
                    params = dict(base_params)
                    if order:
                        params["order"] = order
                    try:
                        response = client.get(BILIBILI_SEARCH_URL, params=params)
                        response.raise_for_status()
                        candidate_payload = response.json()
                        if candidate_payload.get("code") not in (0, "0", None):
                            raise RuntimeError(
                                f"Bilibili API code={candidate_payload.get('code')}: "
                                f"{candidate_payload.get('message')}"
                            )
                        result_items = (
                            candidate_payload.get("data", {}).get("result", []) or []
                        )
                        payload = candidate_payload
                        if result_items:
                            break
                    except Exception as exc:
                        last_error = exc
                        time.sleep(0.2)
                        continue
        if payload is None and last_error:
            raise last_error
    except Exception as exc:
        errors.append(f"Bilibili live search failed: {exc}")
        try:
            database.save_search_result(
                "bilibili",
                person_name,
                "failed",
                error=str(exc),
                ttl_seconds=cache_ttl_seconds,
            )
        except Exception:
            pass
        return {
            "query": person_name,
            "platform": "bilibili",
            "query_source": "live_search",
            "results": [],
            "errors": errors,
        }

    result_items = payload.get("data", {}).get("result", []) or []
    results = [_normalize_user(item) for item in result_items[:limit]]
    try:
        database.save_search_result(
            "bilibili",
            person_name,
            "success",
            results=results,
            ttl_seconds=cache_ttl_seconds,
        )
    except Exception as exc:
        errors.append(f"MongoDB search cache save failed: {exc}")

    return {
        "query": person_name,
        "platform": "bilibili",
        "query_source": "live_search",
        "results": results,
        "errors": errors,
    }
