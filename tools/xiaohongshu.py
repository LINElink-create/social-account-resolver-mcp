from __future__ import annotations

import json
import os
import platform
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from dotenv import load_dotenv
from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.edge.service import Service as EdgeService
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from . import database
from .scorer import score_account_match

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env", encoding="utf-8-sig")

XHS_PC_SEARCH_URL = "https://www.xiaohongshu.com/search_result"
XHS_USER_SEARCH_API = "https://edith.xiaohongshu.com/api/sns/web/v1/search/usersearch"
DEFAULT_TIMEOUT_SECONDS = float(os.getenv("HTTP_TIMEOUT_SECONDS", "12"))
DEFAULT_CACHE_TTL_SECONDS = int(os.getenv("SEARCH_CACHE_TTL_SECONDS", "86400"))
DEFAULT_QUEUE_COOLDOWN_SECONDS = int(os.getenv("XHS_QUEUE_COOLDOWN_SECONDS", "86400"))
DEFAULT_BROWSER_TIMEOUT_SECONDS = int(os.getenv("XHS_BROWSER_TIMEOUT_SECONDS", "20"))
INITIAL_STATE_MARKER = "window.__INITIAL_STATE__="


def _text(value: Any) -> str | None:
    if value is None:
        return None
    return re.sub(r"\s+", " ", str(value)).strip()


def _headers(keyword: str) -> dict[str, str]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": f"https://www.xiaohongshu.com/search_result?keyword={quote(keyword)}&type=user",
    }
    cookie = os.getenv("XHS_COOKIE", "").strip()
    if cookie:
        headers["Cookie"] = cookie
    return headers


def _browser_enabled() -> bool:
    return os.getenv("XHS_BROWSER_ENABLED", "true").lower() in {"1", "true", "yes", "on"}


def _browser_type() -> str:
    value = os.getenv("XHS_BROWSER_TYPE", "").strip().lower()
    if value in {"edge", "chrome", "chromium"}:
        return "chrome" if value == "chromium" else value
    if platform.system().lower() == "windows":
        return "edge"
    return "chrome"


def _edge_user_data_dir() -> str:
    return os.getenv(
        "XHS_EDGE_USER_DATA_DIR",
        r"C:\Users\LINE\AppData\Local\Microsoft\Edge\User Data",
    )


def _edge_profile() -> str:
    return os.getenv("XHS_EDGE_PROFILE", "Default")


def _edge_binary() -> str | None:
    value = os.getenv("XHS_EDGE_BINARY", "").strip()
    return value or None


def _edge_driver_path() -> str | None:
    value = os.getenv("XHS_EDGE_DRIVER_PATH", "").strip()
    return value or None


def _chrome_user_data_dir() -> str:
    return os.getenv(
        "XHS_CHROME_USER_DATA_DIR",
        str(Path.home() / ".cache" / "social-account-resolver-mcp" / "xhs-chrome-profile"),
    )


def _chrome_profile() -> str:
    return os.getenv("XHS_CHROME_PROFILE", "Default")


def _chrome_binary() -> str | None:
    value = os.getenv("XHS_CHROME_BINARY", "").strip()
    return value or None


def _chrome_driver_path() -> str | None:
    value = os.getenv("XHS_CHROME_DRIVER_PATH", "").strip()
    return value or None


def _headless() -> bool:
    return os.getenv("XHS_BROWSER_HEADLESS", "false").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _api_headers(keyword: str) -> dict[str, str]:
    headers = _headers(keyword)
    headers.update(
        {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json;charset=UTF-8",
            "Origin": "https://www.xiaohongshu.com",
        }
    )
    return headers


def _extract_initial_state(html: str) -> dict[str, Any] | None:
    start = html.find(INITIAL_STATE_MARKER)
    if start < 0:
        return None
    start += len(INITIAL_STATE_MARKER)
    end = html.find("</script>", start)
    if end < 0:
        return None
    raw = html[start:end]
    clean = re.sub(r"(?<=[:\[,])undefined(?=[,}\]])", "null", raw)
    return json.loads(clean)


def _user_id(raw: dict[str, Any]) -> str | None:
    value = (
        raw.get("user_id")
        or raw.get("userId")
        or raw.get("id")
        or raw.get("user_id_str")
        or raw.get("userIdStr")
    )
    return str(value) if value else None


def _nickname(raw: dict[str, Any]) -> str | None:
    return _text(
        raw.get("nickname")
        or raw.get("nickName")
        or raw.get("name")
        or raw.get("userName")
    )


def _avatar(raw: dict[str, Any]) -> str | None:
    value = raw.get("image") or raw.get("avatar") or raw.get("avatarUrl")
    if isinstance(value, dict):
        value = value.get("url") or value.get("urlDefault") or value.get("url_default")
    return str(value) if value else None


def _normalize_user(raw: dict[str, Any]) -> dict[str, Any] | None:
    uid = _user_id(raw)
    nickname = _nickname(raw)
    if not uid and not nickname:
        return None
    red_id = raw.get("red_id") or raw.get("redId") or raw.get("red_id_str")
    followers = (
        raw.get("fans")
        or raw.get("fansCount")
        or raw.get("followers")
        or raw.get("follows")
    )
    return {
        "platform": "xiaohongshu",
        "source_platform": "xiaohongshu",
        "source": "xiaohongshu_pc_search",
        "query_source": "live_search",
        "uid": uid,
        "nickname": nickname,
        "bio": _text(raw.get("desc") or raw.get("description") or raw.get("descInfo")),
        "avatar_url": _avatar(raw),
        "followers": followers,
        "red_id": str(red_id) if red_id else None,
        "url": f"https://www.xiaohongshu.com/user/profile/{uid}" if uid else None,
        "verified": bool(raw.get("verified") or raw.get("verifyInfo")),
        "verified_reason": _text(raw.get("verifyInfo") or raw.get("verify_info")),
        "raw_search_result": raw,
    }


def _normalize_browser_user(raw: dict[str, Any]) -> dict[str, Any] | None:
    uid = raw.get("uid")
    raw_text = str(raw.get("text") or "").strip()
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    text = _text(raw_text) or ""
    url = str(raw.get("url") or "")
    if "channel_type=web_search_result_notes" in url:
        return None
    if lines and lines[0] == "我":
        return None
    if text and not re.search(r"小红书号|粉丝|笔记", text):
        return None
    nickname = _text(lines[0] if lines else raw.get("nickname"))
    if not uid and not nickname:
        return None
    followers = raw.get("followers")
    if text:
        match = re.search(r"粉丝[・:\s]*([^\s]+)", text)
        followers = match.group(1) if match else None
    red_id = raw.get("red_id")
    if not red_id and text:
        match = re.search(r"小红书号[:：]\s*([^\n\s]+)", text)
        red_id = match.group(1) if match else None
    return {
        "platform": "xiaohongshu",
        "source_platform": "xiaohongshu",
        "source": f"xiaohongshu_{_browser_type()}_browser",
        "query_source": "live_search",
        "uid": str(uid) if uid else None,
        "nickname": nickname,
        "bio": _text(raw.get("bio")),
        "avatar_url": raw.get("avatar_url"),
        "followers": followers,
        "red_id": red_id,
        "url": url or None,
        "verified": False,
        "verified_reason": None,
        "raw_search_result": raw,
    }


def _new_edge_driver() -> webdriver.Edge:
    options = EdgeOptions()
    options.add_argument(f"--user-data-dir={_edge_user_data_dir()}")
    options.add_argument(f"--profile-directory={_edge_profile()}")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    if _headless():
        options.add_argument("--headless=new")
    binary = _edge_binary()
    if binary:
        options.binary_location = binary

    driver_path = _edge_driver_path()
    service = EdgeService(executable_path=driver_path) if driver_path else EdgeService()
    return webdriver.Edge(service=service, options=options)


def _new_chrome_driver() -> webdriver.Chrome:
    options = ChromeOptions()
    options.add_argument(f"--user-data-dir={_chrome_user_data_dir()}")
    options.add_argument(f"--profile-directory={_chrome_profile()}")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--no-sandbox")
    if _headless():
        options.add_argument("--headless=new")
    binary = _chrome_binary()
    if binary:
        options.binary_location = binary

    driver_path = _chrome_driver_path()
    service = ChromeService(executable_path=driver_path) if driver_path else ChromeService()
    return webdriver.Chrome(service=service, options=options)


def _new_browser_driver() -> webdriver.Edge | webdriver.Chrome:
    if _browser_type() == "edge":
        return _new_edge_driver()
    return _new_chrome_driver()


def _xhs_cookie_items() -> list[dict[str, str]]:
    cookie_header = os.getenv("XHS_COOKIE", "").strip()
    if not cookie_header:
        return []

    cookies: list[dict[str, str]] = []
    for item in cookie_header.split(";"):
        if "=" not in item:
            continue
        name, value = item.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name:
            continue
        cookies.append({"name": name, "value": value})
    return cookies


def _inject_xhs_cookies(driver: webdriver.Edge | webdriver.Chrome) -> int:
    cookies = _xhs_cookie_items()
    if not cookies:
        return 0

    driver.get("https://www.xiaohongshu.com/")
    WebDriverWait(driver, DEFAULT_BROWSER_TIMEOUT_SECONDS).until(
        lambda d: d.execute_script("return document.readyState")
        in {"interactive", "complete"}
    )
    added = 0
    for cookie in cookies:
        try:
            driver.add_cookie(
                {
                    "name": cookie["name"],
                    "value": cookie["value"],
                    "domain": ".xiaohongshu.com",
                    "path": "/",
                    "secure": True,
                }
            )
            added += 1
        except WebDriverException:
            continue
    return added


def _extract_browser_users(
    driver: webdriver.Edge | webdriver.Chrome, limit: int
) -> list[dict[str, Any]]:
    script = """
const limit = arguments[0];
const anchors = Array.from(document.querySelectorAll('a[href*="/user/profile/"]'));
const seen = new Set();
const users = [];
for (const anchor of anchors) {
  const href = anchor.href;
  const match = href.match(/\\/user\\/profile\\/([^/?#]+)/);
  const uid = match ? decodeURIComponent(match[1]) : href;
  if (!uid || seen.has(uid)) continue;
  seen.add(uid);
  const card = anchor.closest('[class*="user"], [class*="card"], section, div') || anchor.parentElement;
  const text = (card ? card.innerText : anchor.innerText || '').trim();
  const lines = text.split(/\\n+/).map(x => x.trim()).filter(Boolean);
  const img = card ? card.querySelector('img') : null;
  users.push({
    uid,
    url: href,
    nickname: (anchor.innerText || lines[0] || '').trim(),
    bio: lines.slice(1, 4).join(' '),
    followers: (text.match(/粉丝\\s*[:：]?\\s*([^\\n\\s]+)/) || [])[1] || null,
    avatar_url: img ? img.src : null,
    text
  });
  if (users.length >= limit) break;
}
return users;
"""
    raw_users = driver.execute_script(script, max(limit * 4, 20)) or []
    results: list[dict[str, Any]] = []
    for raw in raw_users:
        if isinstance(raw, dict):
            normalized = _normalize_browser_user(raw)
            if normalized:
                results.append(normalized)
    return results[:limit]


def _search_browser(keyword: str, limit: int) -> list[dict[str, Any]]:
    if not _browser_enabled():
        raise RuntimeError("Xiaohongshu browser worker is disabled")

    url = f"https://www.xiaohongshu.com/search_result?keyword={quote(keyword)}&type=user"
    driver: webdriver.Edge | webdriver.Chrome | None = None
    try:
        driver = _new_browser_driver()
        driver.set_page_load_timeout(DEFAULT_BROWSER_TIMEOUT_SECONDS)
        _inject_xhs_cookies(driver)
        driver.get(url)
        wait = WebDriverWait(driver, DEFAULT_BROWSER_TIMEOUT_SECONDS)
        wait.until(
            lambda d: d.execute_script("return document.readyState")
            in {"interactive", "complete"}
        )
        time.sleep(3)
        try:
            user_tab = driver.find_elements(
                By.XPATH,
                "//*[contains(normalize-space(.), '用户') and (self::div or self::span or self::button)]",
            )
            if user_tab:
                user_tab[0].click()
                time.sleep(2)
        except WebDriverException:
            pass
        try:
            WebDriverWait(driver, 8).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, 'a[href*="/user/profile/"]'))
            )
        except Exception:
            pass
        return _extract_browser_users(driver, limit)
    finally:
        if driver:
            driver.quit()


def _search_edge_browser(keyword: str, limit: int) -> list[dict[str, Any]]:
    return _search_browser(keyword, limit)


def _users_from_initial_state(state: dict[str, Any]) -> list[dict[str, Any]]:
    search = state.get("search") or {}
    candidates = search.get("userLists") or []
    results: list[dict[str, Any]] = []
    for raw in candidates:
        if not isinstance(raw, dict):
            continue
        normalized = _normalize_user(raw)
        if normalized:
            results.append(normalized)
    return results


def _search_pc_page(keyword: str, limit: int) -> list[dict[str, Any]]:
    with httpx.Client(
        timeout=DEFAULT_TIMEOUT_SECONDS,
        follow_redirects=True,
        headers=_headers(keyword),
    ) as client:
        response = client.get(
            XHS_PC_SEARCH_URL,
            params={"keyword": keyword, "type": "user"},
        )
        response.raise_for_status()
        state = _extract_initial_state(response.text)
    if not state:
        raise RuntimeError("Xiaohongshu initial state was not found in PC search page")
    return _users_from_initial_state(state)[:limit]


def _try_unsigned_user_api(keyword: str, limit: int) -> list[dict[str, Any]]:
    payload = {
        "search_user_request": {
            "keyword": keyword,
            "search_id": "",
            "page": 1,
            "page_size": limit,
            "biz_type": "web_search_user",
            "request_id": "mcp",
        }
    }
    response = httpx.post(
        XHS_USER_SEARCH_API,
        headers=_api_headers(keyword),
        json=payload,
        timeout=DEFAULT_TIMEOUT_SECONDS,
        follow_redirects=True,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("success") is not True:
        raise RuntimeError(f"Xiaohongshu API returned non-success: {data}")
    users = data.get("data", {}).get("users") or data.get("users") or []
    results = []
    for raw in users:
        if isinstance(raw, dict):
            normalized = _normalize_user(raw)
            if normalized:
                normalized["source"] = "xiaohongshu_user_search_api"
                results.append(normalized)
    return results[:limit]


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
            if item.get("platform") == "xiaohongshu"
        ]
        if reliable:
            return {
                "query": person_name,
                "platform": "xiaohongshu",
                "query_source": "reliable_cache",
                "results": reliable[:limit],
                "errors": [],
            }

        cached = database.get_recent_search_result(
            "xiaohongshu", person_name, cache_ttl_seconds
        )
        if cached:
            results = cached.get("results", [])[:limit]
            for result in results:
                result["query_source"] = "search_cache"
            return {
                "query": person_name,
                "platform": "xiaohongshu",
                "query_source": "search_cache",
                "results": results,
                "errors": [],
            }
    except Exception as exc:
        return {
            "query": person_name,
            "platform": "xiaohongshu",
            "query_source": "cache_error",
            "results": [],
            "errors": [f"MongoDB cache check failed: {exc}"],
        }
    return None


def search_xiaohongshu_user(
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
    results: list[dict[str, Any]] = []

    if _browser_enabled():
        try:
            results = _search_browser(person_name, limit)
        except Exception as exc:
            errors.append(
                f"Xiaohongshu {_browser_type()} browser search failed: {exc}"
            )

    try:
        if not results:
            results = _search_pc_page(person_name, limit)
    except Exception as exc:
        errors.append(f"Xiaohongshu PC search page failed: {exc}")

    if not results:
        try:
            results = _try_unsigned_user_api(person_name, limit)
        except Exception as exc:
            errors.append(
                "Xiaohongshu user API failed; it usually requires browser-side "
                f"signature headers. Detail: {exc}"
            )

    status = "success" if results else "failed"
    try:
        database.save_search_result(
            "xiaohongshu",
            person_name,
            status,
            results=results,
            error="; ".join(errors) if errors and not results else None,
            ttl_seconds=cache_ttl_seconds,
        )
    except Exception as exc:
        errors.append(f"MongoDB search cache save failed: {exc}")

    return {
        "query": person_name,
        "platform": "xiaohongshu",
        "query_source": "live_search",
        "results": results,
        "errors": errors,
    }


def search_xiaohongshu_cached_user(
    person_name: str,
    aliases: list[str] | None = None,
    limit: int = 10,
    cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
) -> dict[str, Any]:
    cached = _cached_response(
        person_name,
        aliases,
        limit,
        force_refresh=False,
        cache_ttl_seconds=cache_ttl_seconds,
    )
    if cached:
        return cached
    return {
        "query": person_name,
        "platform": "xiaohongshu",
        "query_source": "cache_miss",
        "results": [],
        "errors": [],
    }


def enqueue_xiaohongshu_search(
    person_name: str,
    aliases: list[str] | None = None,
    limit: int = 10,
    category: str | None = None,
    cooldown_seconds: int = DEFAULT_QUEUE_COOLDOWN_SECONDS,
) -> dict[str, Any]:
    task = database.enqueue_resolution_task(
        person_name=person_name,
        platform_scope=["xiaohongshu"],
        task_type="xiaohongshu_search",
        payload={
            "person_name": person_name,
            "aliases": aliases or [],
            "limit": limit,
            "category": category,
        },
        dedupe=True,
        cooldown_seconds=cooldown_seconds,
    )
    return {"ok": True, "task": task}


def run_xiaohongshu_worker(limit: int = 3) -> dict[str, Any]:
    tasks = database.claim_resolution_tasks("xiaohongshu_search", limit)
    results: list[dict[str, Any]] = []

    for task in tasks:
        payload = task.get("payload") or {}
        person_name = payload.get("person_name") or task.get("person_name")
        aliases = payload.get("aliases") or []
        candidate_limit = int(payload.get("limit") or 10)
        category = payload.get("category")

        try:
            person = database.ensure_person(person_name, aliases, category)
            search_result = search_xiaohongshu_user(
                person_name,
                aliases=aliases,
                limit=candidate_limit,
                force_refresh=True,
            )
            saved_accounts: list[dict[str, Any]] = []
            for candidate in search_result.get("results", []) or []:
                score_result = score_account_match(person, candidate)
                saved_accounts.append(
                    database.upsert_candidate_account(
                        person["_id"], candidate, score_result
                    )
                )

            summary = {
                "query": person_name,
                "result_count": len(search_result.get("results", []) or []),
                "saved_count": len(saved_accounts),
                "errors": search_result.get("errors", []),
            }
            status = "done" if saved_accounts or not summary["errors"] else "failed"
            error = "; ".join(summary["errors"]) if status == "failed" else None
            finished = database.finish_resolution_task(
                task["_id"],
                result_summary=summary,
                status=status,
                error=error,
            )
            results.append(
                {
                    "ok": status == "done",
                    "task": finished,
                    "search_result": search_result,
                    "saved_accounts": saved_accounts,
                }
            )
        except Exception as exc:
            finished = database.finish_resolution_task(
                task["_id"],
                result_summary={"query": person_name, "result_count": 0},
                status="failed",
                error=str(exc),
            )
            results.append({"ok": False, "task": finished, "error": str(exc)})

    return {
        "ok": True,
        "claimed_count": len(tasks),
        "success_count": sum(1 for item in results if item.get("ok")),
        "failed_count": sum(1 for item in results if not item.get("ok")),
        "results": results,
    }
