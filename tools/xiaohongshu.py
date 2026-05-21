from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from app.services import mongo_cache
from app.services.xhs_search import xhs_search_users
from . import database
from .scorer import score_account_match

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env", encoding="utf-8-sig")

DEFAULT_CACHE_TTL_SECONDS = int(os.getenv("SEARCH_CACHE_TTL_SECONDS", "86400"))
DEFAULT_QUEUE_COOLDOWN_SECONDS = int(os.getenv("XHS_QUEUE_COOLDOWN_SECONDS", "86400"))


def _normalize_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "platform": "xiaohongshu",
        "source_platform": "xiaohongshu",
        "source": "xiaohongshu_playwright_search",
        "query_source": "live_search",
        "uid": candidate.get("user_id"),
        "nickname": candidate.get("nickname"),
        "bio": candidate.get("desc"),
        "avatar_url": candidate.get("avatar"),
        "followers": None,
        "red_id": candidate.get("red_id"),
        "url": candidate.get("profile_url"),
        "verified": False,
        "verified_reason": None,
        "raw_rank": candidate.get("raw_rank"),
        "raw_search_result": candidate,
    }


def _search_cache_key(person_name: str) -> str:
    return mongo_cache.make_cache_key(
        {"tool": "xhs_search_users", "keyword": person_name.strip()}
    )


def _cache_only_response(person_name: str, limit: int) -> dict[str, Any] | None:
    try:
        cached = mongo_cache.get_search_cache(_search_cache_key(person_name))
    except Exception as exc:
        return {
            "query": person_name,
            "platform": "xiaohongshu",
            "query_source": "cache_error",
            "results": [],
            "errors": [f"MongoDB XHS cache check failed: {exc}"],
        }
    if cached is None:
        return None
    results = [_normalize_candidate(item) for item in cached[:limit]]
    for result in results:
        result["query_source"] = "search_cache"
    return {
        "query": person_name,
        "platform": "xiaohongshu",
        "query_source": "search_cache",
        "results": results,
        "errors": [],
    }


def search_xiaohongshu_user(
    person_name: str,
    aliases: list[str] | None = None,
    limit: int = 10,
    force_refresh: bool = False,
    cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
) -> dict[str, Any]:
    del aliases, cache_ttl_seconds

    use_cache = not force_refresh
    search_result = xhs_search_users(person_name, limit=limit, use_cache=use_cache)
    error = search_result.get("error")
    candidates = search_result.get("candidates") or []
    results = [_normalize_candidate(item) for item in candidates if isinstance(item, dict)]
    query_source = "search_cache" if search_result.get("cached") else "live_search"
    for result in results:
        result["query_source"] = query_source

    return {
        "query": person_name,
        "platform": "xiaohongshu",
        "query_source": query_source,
        "results": results[:limit],
        "errors": [str(error)] if error else [],
    }


def search_xiaohongshu_cached_user(
    person_name: str,
    aliases: list[str] | None = None,
    limit: int = 10,
    cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
) -> dict[str, Any]:
    del aliases, cache_ttl_seconds

    cached = _cache_only_response(person_name, limit)
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
