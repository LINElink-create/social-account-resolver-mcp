from __future__ import annotations

import time

from app.services import mongo_cache
from app.services.xhs_browser import XhsBrowserError, get_browser


def _safe_log_fetch(
    tool: str,
    keyword: str | None,
    status: str,
    duration_ms: int,
    error: str | None = None,
) -> None:
    try:
        mongo_cache.log_fetch(tool, keyword, status, duration_ms, error)
    except Exception:
        pass


def xhs_search_users(
    keyword: str,
    limit: int = 10,
    use_cache: bool = True,
) -> dict[str, object]:
    started = time.monotonic()
    limit = mongo_cache.clamp_limit(limit)
    keyword = keyword.strip()
    cache_key = mongo_cache.make_cache_key({"tool": "xhs_search_users", "keyword": keyword})

    if not keyword:
        return {
            "keyword": keyword,
            "source": "xhs_browser_search",
            "cached": False,
            "candidates": [],
            "error": "keyword is required",
        }

    try:
        if use_cache:
            cached = mongo_cache.get_search_cache(cache_key)
            if cached is not None:
                return {
                    "keyword": keyword,
                    "source": "xhs_browser_search",
                    "cached": True,
                    "candidates": cached[:limit],
                    "error": None,
                }

        candidates = get_browser().search_users(keyword, limit)
        mongo_cache.set_search_cache(cache_key, keyword, candidates)
        _safe_log_fetch(
            "xhs_search_users",
            keyword,
            "ok",
            int((time.monotonic() - started) * 1000),
        )
        return {
            "keyword": keyword,
            "source": "xhs_browser_search",
            "cached": False,
            "candidates": candidates[:limit],
            "error": None,
        }
    except XhsBrowserError as exc:
        _safe_log_fetch(
            "xhs_search_users",
            keyword,
            exc.status,
            int((time.monotonic() - started) * 1000),
            exc.message,
        )
        return {
            "keyword": keyword,
            "source": "xhs_browser_search",
            "cached": False,
            "candidates": [],
            "error": exc.status,
        }
    except Exception as exc:
        message = str(exc)
        _safe_log_fetch(
            "xhs_search_users",
            keyword,
            "error",
            int((time.monotonic() - started) * 1000),
            message,
        )
        return {
            "keyword": keyword,
            "source": "xhs_browser_search",
            "cached": False,
            "candidates": [],
            "error": message,
        }
