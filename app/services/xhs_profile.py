from __future__ import annotations

import time

from app.services import mongo_cache
from app.services.xhs_browser import XhsBrowserError, get_browser, normalize_profile_url


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


def xhs_get_user_profile(
    user_id_or_url: str,
    use_cache: bool = True,
) -> dict[str, object]:
    started = time.monotonic()
    value = user_id_or_url.strip()

    try:
        user_id, profile_url = normalize_profile_url(value)
    except Exception as exc:
        return {
            "user_id": None,
            "profile_url": value,
            "nickname": None,
            "red_id": None,
            "desc": None,
            "avatar": None,
            "stats": {
                "followers": None,
                "following": None,
                "likes_and_collects": None,
            },
            "cached": False,
            "error": str(exc),
        }

    try:
        if use_cache:
            cached = mongo_cache.get_user_profile(user_id or profile_url)
            if cached is None and user_id and profile_url:
                cached = mongo_cache.get_user_profile(profile_url)
            if cached is not None:
                cached["cached"] = True
                cached["error"] = None
                return _public_profile(cached)

        profile = get_browser().get_user_profile(profile_url)
        mongo_cache.set_user_profile(profile)
        _safe_log_fetch(
            "xhs_get_user_profile",
            user_id or profile_url,
            "ok",
            int((time.monotonic() - started) * 1000),
        )
        profile["cached"] = False
        profile["error"] = None
        return _public_profile(profile)
    except XhsBrowserError as exc:
        _safe_log_fetch(
            "xhs_get_user_profile",
            user_id or profile_url,
            exc.status,
            int((time.monotonic() - started) * 1000),
            exc.message,
        )
        return _empty_profile(user_id, profile_url, exc.status)
    except Exception as exc:
        message = str(exc)
        _safe_log_fetch(
            "xhs_get_user_profile",
            user_id or profile_url,
            "error",
            int((time.monotonic() - started) * 1000),
            message,
        )
        return _empty_profile(user_id, profile_url, message)


def _empty_profile(
    user_id: str | None,
    profile_url: str,
    error: str,
) -> dict[str, object]:
    return {
        "user_id": user_id,
        "profile_url": profile_url,
        "nickname": None,
        "red_id": None,
        "desc": None,
        "avatar": None,
        "stats": {
            "followers": None,
            "following": None,
            "likes_and_collects": None,
        },
        "cached": False,
        "error": error,
    }


def _public_profile(profile: dict[str, object]) -> dict[str, object]:
    return {
        "user_id": profile.get("user_id"),
        "profile_url": str(profile.get("profile_url") or ""),
        "nickname": profile.get("nickname"),
        "red_id": profile.get("red_id"),
        "desc": profile.get("desc"),
        "avatar": profile.get("avatar"),
        "stats": profile.get(
            "stats",
            {
                "followers": None,
                "following": None,
                "likes_and_collects": None,
            },
        ),
        "cached": bool(profile.get("cached")),
        "error": profile.get("error"),
    }
