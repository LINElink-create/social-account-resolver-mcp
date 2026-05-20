from __future__ import annotations

from typing import Any

from app.services import mongo_cache
from app.services.xhs_profile import xhs_get_user_profile
from app.services.xhs_scorer import score_candidate
from app.services.xhs_search import xhs_search_users


def xhs_resolve_user(
    name: str,
    aliases: list[str] | None = None,
    context_keywords: list[str] | None = None,
    limit: int = 10,
    min_confidence: int = 70,
) -> dict[str, Any]:
    aliases = aliases or []
    context_keywords = context_keywords or []
    limit = mongo_cache.clamp_limit(limit)
    names = [name, *aliases]

    try:
        confirmed = mongo_cache.find_confirmed_user(names)
        if confirmed:
            best = {
                "user_id": confirmed.get("user_id"),
                "nickname": confirmed.get("nickname") or "",
                "profile_url": confirmed.get("profile_url"),
                "confidence": int(confirmed.get("confidence") or 100),
                "reason": ["confirmed_cache"],
            }
            return {
                "query": name,
                "status": "matched",
                "best_candidate": best,
                "candidates": [best],
                "manual_review_required": False,
                "error": None,
            }
    except Exception as exc:
        return _error_response(name, "error", str(exc))

    search_result = xhs_search_users(name, limit=limit, use_cache=True)
    if search_result.get("error"):
        error = str(search_result["error"])
        status = "login_required" if error == "login_required" else error
        if status not in {"login_required", "rate_limited"}:
            status = "error"
        return _error_response(name, status, error)

    candidates = list(search_result.get("candidates") or [])
    if not candidates:
        return {
            "query": name,
            "status": "not_found",
            "best_candidate": None,
            "candidates": [],
            "manual_review_required": True,
            "error": None,
        }

    enriched: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates):
        merged = dict(candidate)
        if index < 3 and candidate.get("profile_url"):
            profile = xhs_get_user_profile(str(candidate["profile_url"]), use_cache=True)
            if not profile.get("error"):
                merged.update(
                    {
                        "user_id": profile.get("user_id") or merged.get("user_id"),
                        "nickname": profile.get("nickname") or merged.get("nickname"),
                        "red_id": profile.get("red_id") or merged.get("red_id"),
                        "desc": profile.get("desc") or merged.get("desc"),
                        "avatar": profile.get("avatar") or merged.get("avatar"),
                        "profile_url": profile.get("profile_url") or merged.get("profile_url"),
                    }
                )

        scored = score_candidate(name, aliases, context_keywords, merged)
        item = {
            "user_id": merged.get("user_id"),
            "nickname": merged.get("nickname") or "",
            "profile_url": merged.get("profile_url"),
            "confidence": scored["confidence"],
            "reason": scored["reason"],
            "raw_rank": int(merged.get("raw_rank") or index + 1),
        }
        enriched.append(item)
        _save_scored_candidate(merged, item, name, aliases)

    enriched.sort(key=lambda item: int(item["confidence"]), reverse=True)
    best = enriched[0] if enriched else None
    matched = bool(best and int(best["confidence"]) >= min_confidence)
    return {
        "query": name,
        "status": "matched" if matched else "needs_review",
        "best_candidate": best,
        "candidates": enriched,
        "manual_review_required": not matched,
        "error": None,
    }


def _save_scored_candidate(
    raw: dict[str, Any],
    scored: dict[str, Any],
    name: str,
    aliases: list[str],
) -> None:
    status = "candidate" if int(scored["confidence"]) < 70 else "needs_review"
    mongo_cache.save_candidate(
        {
            "platform": "xhs",
            "user_id": raw.get("user_id"),
            "nickname": raw.get("nickname"),
            "red_id": raw.get("red_id"),
            "desc": raw.get("desc"),
            "avatar": raw.get("avatar"),
            "profile_url": raw.get("profile_url"),
            "confidence": scored["confidence"],
            "status": status,
            "matched_names": [name, *aliases],
            "evidence": scored["reason"],
            "source": "xhs_resolve_user",
            "last_checked_at": mongo_cache.utc_now(),
        }
    )


def _error_response(query: str, status: str, error: str) -> dict[str, Any]:
    return {
        "query": query,
        "status": status,
        "best_candidate": None,
        "candidates": [],
        "manual_review_required": True,
        "error": error,
    }

