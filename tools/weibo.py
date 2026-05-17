from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import quote, urljoin

import httpx
from bs4 import BeautifulSoup

from . import database

WEIBO_PC_SEARCH_URL = "https://s.weibo.com/user"
WEIBO_SEARCH_URL = "https://m.weibo.cn/api/container/getIndex"
DEFAULT_TIMEOUT_SECONDS = float(os.getenv("HTTP_TIMEOUT_SECONDS", "12"))
DEFAULT_CACHE_TTL_SECONDS = int(os.getenv("SEARCH_CACHE_TTL_SECONDS", "86400"))
FOLLOWER_PATTERN = re.compile(r"粉丝[:：]\s*(?P<followers>\S+)")
UID_PATTERN = re.compile(r"/u/(?P<uid>\d+)")


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    return BeautifulSoup(str(value), "lxml").get_text(" ", strip=True)


def _absolute_weibo_url(href: str | None) -> str | None:
    if not href:
        return None
    return urljoin("https://weibo.com/", href)


def _uid_from_href(href: str | None) -> str | None:
    if not href:
        return None
    match = UID_PATTERN.search(href)
    return match.group("uid") if match else None


def _normalize_pc_card(card: Any) -> dict[str, Any] | None:
    name_anchor = card.select_one("a.name")
    if not name_anchor:
        return None

    href = name_anchor.get("href")
    button = card.select_one("button[uid]")
    uid = _uid_from_href(str(href)) if href else None
    if uid is None and button:
        uid = button.get("uid")
    profile_url = _absolute_weibo_url(str(href)) if href else None
    avatar = card.select_one(".avator img")
    paragraphs = [_clean_text(p.get_text(" ", strip=True)) for p in card.select(".info p")]
    paragraphs = [item for item in paragraphs if item]

    bio = None
    followers = None
    for item in paragraphs:
        follower_match = FOLLOWER_PATTERN.search(item)
        if follower_match:
            followers = follower_match.group("followers")
        elif bio is None:
            bio = item

    verified_icon = card.select_one(".woo-avatar-icon[title]")
    verified_reason = verified_icon.get("title") if verified_icon else None

    return {
        "platform": "weibo",
        "source_platform": "weibo",
        "source": "s_weibo_user_search",
        "query_source": "live_search",
        "uid": str(uid) if uid is not None else None,
        "nickname": _clean_text(name_anchor.get_text(" ", strip=True)),
        "bio": bio,
        "avatar_url": avatar.get("src") if avatar else None,
        "followers": followers,
        "url": profile_url,
        "verified": bool(verified_icon),
        "verified_reason": _clean_text(verified_reason),
        "raw_search_result": {
            "text": _clean_text(card.get_text(" ", strip=True)),
            "href": str(href) if href else None,
        },
    }


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


def _search_pc_weibo_user(
    person_name: str,
    limit: int,
    cookie: str,
) -> list[dict[str, Any]]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123 Safari/537.36"
        ),
        "Referer": "https://s.weibo.com/",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    if cookie:
        headers["Cookie"] = cookie

    with httpx.Client(
        timeout=DEFAULT_TIMEOUT_SECONDS,
        follow_redirects=True,
        headers=headers,
    ) as client:
        response = client.get(WEIBO_PC_SEARCH_URL, params={"q": person_name})
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "lxml")

    if "passport.weibo.com" in str(response.url):
        raise RuntimeError("Weibo PC search redirected to passport login")

    cards = soup.select(".card-user-b")
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for card in cards:
        normalized = _normalize_pc_card(card)
        if not normalized:
            continue
        key = normalized.get("uid") or normalized.get("url") or normalized.get("nickname")
        if not key or str(key) in seen:
            continue
        seen.add(str(key))
        results.append(normalized)
        if len(results) >= limit:
            break
    return results


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

    try:
        results = _search_pc_weibo_user(person_name, limit, cookie)
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
    except Exception as exc:
        errors.append(f"Weibo PC live search failed: {exc}")

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
            if payload.get("ok") not in (1, "1"):
                signin_url = payload.get("url")
                message = payload.get("msg") or payload.get("message") or "Weibo API returned a non-success response"
                if signin_url:
                    message = f"{message}; signin_url={signin_url}"
                raise RuntimeError(f"Weibo API ok={payload.get('ok')}: {message}")
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
