from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from . import database
from .bilibili import search_bilibili_user
from .fydmwd import search_fydmwd_account
from .scorer import score_account_match
from .weibo import search_weibo_user

DEFAULT_PLATFORMS = ["bilibili", "weibo", "douyin", "kuaishou"]
FYDMWD_PLATFORMS = {"douyin", "kuaishou"}


def _unique_key(candidate: dict[str, Any]) -> tuple[str, str]:
    platform = str(candidate.get("platform") or "unknown")
    key = (
        candidate.get("uid")
        or candidate.get("account_id")
        or candidate.get("url")
        or candidate.get("profile_url")
        or candidate.get("nickname")
        or candidate.get("display_name")
        or ""
    )
    return platform, str(key)


def _search_source(
    source_name: str,
    person_name: str,
    aliases: list[str] | None,
    limit: int,
    force_refresh: bool,
    fydmwd_platforms: list[str] | None = None,
) -> dict[str, Any]:
    if source_name == "bilibili":
        return search_bilibili_user(person_name, aliases, limit, force_refresh)
    if source_name == "weibo":
        return search_weibo_user(person_name, aliases, limit, force_refresh)
    if source_name == "fydmwd":
        return search_fydmwd_account(
            person_name,
            platforms=fydmwd_platforms or ["douyin", "kuaishou"],
            limit=limit,
            force_refresh=force_refresh,
        )
    raise ValueError(f"Unsupported source: {source_name}")


def resolve_person_social_accounts(
    person_name: str,
    aliases: list[str] | None = None,
    platforms: list[str] | None = None,
    limit_per_source: int = 10,
    result_limit: int = 20,
    force_refresh: bool = False,
    save_candidates: bool = False,
    category: str | None = None,
    negative_keywords: list[str] | None = None,
) -> dict[str, Any]:
    platform_scope = platforms or DEFAULT_PLATFORMS
    platform_scope = [platform.lower() for platform in platform_scope]
    person = {"name": person_name, "aliases": aliases or []}
    errors: list[str] = []
    saved_accounts: list[dict[str, Any]] = []

    try:
        existing_profile = database.find_person_profile(
            person_name, aliases, include_candidates=True
        )
    except Exception as exc:
        existing_profile = {
            "found": False,
            "person": None,
            "reliable_accounts": [],
            "candidate_accounts": [],
        }
        errors.append(f"MongoDB profile lookup failed: {exc}")

    person_id: str | None = None
    if save_candidates:
        try:
            ensured_person = database.ensure_person(person_name, aliases, category)
            person_id = ensured_person["_id"]
            person = ensured_person
        except Exception as exc:
            errors.append(f"MongoDB person ensure failed: {exc}")

    source_jobs: list[tuple[str, list[str] | None]] = []
    if "bilibili" in platform_scope:
        source_jobs.append(("bilibili", None))
    if "weibo" in platform_scope:
        source_jobs.append(("weibo", None))

    fydmwd_scope = [platform for platform in platform_scope if platform in FYDMWD_PLATFORMS]
    if fydmwd_scope:
        source_jobs.append(("fydmwd", fydmwd_scope))

    searches: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=max(1, len(source_jobs))) as executor:
        futures = {
            executor.submit(
                _search_source,
                source_name,
                person_name,
                aliases,
                limit_per_source,
                force_refresh,
                fydmwd_platforms,
            ): source_name
            for source_name, fydmwd_platforms in source_jobs
        }
        for future in as_completed(futures):
            source_name = futures[future]
            try:
                searches[source_name] = future.result()
            except Exception as exc:
                searches[source_name] = {
                    "query": person_name,
                    "query_source": "error",
                    "results": [],
                    "errors": [str(exc)],
                }

    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for source_name, search_result in searches.items():
        for error in search_result.get("errors", []) or []:
            errors.append(f"{source_name}: {error}")
        for candidate in search_result.get("results", []) or []:
            if candidate.get("platform") not in platform_scope:
                continue
            key = _unique_key(candidate)
            if key in seen:
                continue
            seen.add(key)

            score_result = score_account_match(person, candidate, negative_keywords)
            enriched = {
                **candidate,
                "confidence_score": score_result["score"],
                "status": score_result["status"],
                "evidence": score_result["evidence"],
                "score_result": score_result,
            }
            candidates.append(enriched)

            if save_candidates and person_id:
                try:
                    saved = database.upsert_candidate_account(
                        person_id, candidate, score_result
                    )
                    saved_accounts.append(saved)
                except Exception as exc:
                    errors.append(
                        f"save {candidate.get('platform')} candidate failed: {exc}"
                    )

    candidates.sort(
        key=lambda item: (
            int(item.get("confidence_score") or 0),
            str(item.get("followers") or ""),
        ),
        reverse=True,
    )

    source_summary = {
        source_name: {
            "count": len(search_result.get("results", []) or []),
            "query_source": search_result.get("query_source"),
            "errors": search_result.get("errors", []) or [],
        }
        for source_name, search_result in searches.items()
    }

    return {
        "ok": True,
        "query": person_name,
        "aliases": aliases or [],
        "platforms": platform_scope,
        "existing_profile": existing_profile,
        "source_summary": source_summary,
        "result_count": len(candidates[:result_limit]),
        "results": candidates[:result_limit],
        "saved_count": len(saved_accounts),
        "saved_accounts": saved_accounts,
        "errors": errors,
    }
