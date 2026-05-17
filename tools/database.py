from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv
from pymongo import ASCENDING, MongoClient, TEXT
from pymongo.collection import Collection
from pymongo.database import Database

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class MongoConfig:
    uri: str = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
    db_name: str = os.getenv("MONGODB_DB", "social_account_resolver")
    persons_collection: str = os.getenv("PERSONS_COLLECTION", "persons")
    social_accounts_collection: str = os.getenv(
        "SOCIAL_ACCOUNTS_COLLECTION", "social_accounts"
    )
    resolution_tasks_collection: str = os.getenv(
        "RESOLUTION_TASKS_COLLECTION", "resolution_tasks"
    )
    search_queries_collection: str = os.getenv(
        "SEARCH_QUERIES_COLLECTION", "search_queries"
    )
    image_tasks_collection: str = os.getenv("IMAGE_TASKS_COLLECTION", "image_tasks")
    server_selection_timeout_ms: int = int(
        os.getenv("MONGODB_SERVER_SELECTION_TIMEOUT_MS", "3000")
    )


CONFIG = MongoConfig()
RELIABLE_STATUSES = ("confirmed", "high_confidence")
FIRST_STAGE_STATUSES = (
    "high_confidence",
    "need_review",
    "candidate_only",
    "rejected",
)

_client: MongoClient[dict[str, Any]] | None = None


class DatabaseUnavailable(RuntimeError):
    """Raised when MongoDB cannot be reached."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def make_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


def get_client() -> MongoClient[dict[str, Any]]:
    global _client
    if _client is None:
        _client = MongoClient(
            CONFIG.uri,
            serverSelectionTimeoutMS=CONFIG.server_selection_timeout_ms,
        )
    return _client


def get_db() -> Database[dict[str, Any]]:
    return get_client()[CONFIG.db_name]


def ping() -> bool:
    try:
        get_client().admin.command("ping")
        return True
    except Exception as exc:  # pragma: no cover - depends on local MongoDB.
        raise DatabaseUnavailable(str(exc)) from exc


def persons() -> Collection[dict[str, Any]]:
    return get_db()[CONFIG.persons_collection]


def social_accounts() -> Collection[dict[str, Any]]:
    return get_db()[CONFIG.social_accounts_collection]


def resolution_tasks() -> Collection[dict[str, Any]]:
    return get_db()[CONFIG.resolution_tasks_collection]


def search_queries() -> Collection[dict[str, Any]]:
    return get_db()[CONFIG.search_queries_collection]


def image_tasks() -> Collection[dict[str, Any]]:
    return get_db()[CONFIG.image_tasks_collection]


def ensure_indexes() -> dict[str, Any]:
    persons().create_index([("name", ASCENDING)])
    persons().create_index([("aliases", ASCENDING)])
    persons().create_index([("category", ASCENDING)])

    social_accounts().create_index([("person_id", ASCENDING), ("platform", ASCENDING)])
    social_accounts().create_index(
        [("platform", ASCENDING), ("uid", ASCENDING)],
        unique=True,
        partialFilterExpression={"uid": {"$type": "string"}},
    )
    social_accounts().create_index(
        [("platform", ASCENDING), ("url", ASCENDING)],
        unique=True,
        partialFilterExpression={"url": {"$type": "string"}},
    )
    social_accounts().create_index([("nickname", TEXT), ("bio", TEXT)])

    resolution_tasks().create_index([("status", ASCENDING), ("created_at", ASCENDING)])
    resolution_tasks().create_index([("person_name", ASCENDING)])

    search_queries().create_index([("platform", ASCENDING), ("keyword", ASCENDING)])
    search_queries().create_index([("expires_at", ASCENDING)])

    image_tasks().create_index([("status", ASCENDING), ("created_at", ASCENDING)])
    image_tasks().create_index([("source_platform", ASCENDING), ("page_url", ASCENDING)])
    image_tasks().create_index(
        [("image_url", ASCENDING)],
        unique=True,
        partialFilterExpression={"image_url": {"$type": "string"}},
    )
    return {"ok": True}


def serialize(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, list):
        return [serialize(item) for item in value]
    if isinstance(value, tuple):
        return [serialize(item) for item in value]
    if isinstance(value, dict):
        return {key: serialize(item) for key, item in value.items()}
    return value


def _compact_aliases(aliases: list[str] | None) -> list[str]:
    if not aliases:
        return []
    seen: set[str] = set()
    compacted: list[str] = []
    for alias in aliases:
        normalized = str(alias).strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            compacted.append(normalized)
    return compacted


def _keyword_regex(keyword: str) -> dict[str, str]:
    return {"$regex": re.escape(keyword.strip()), "$options": "i"}


def find_person(name: str, aliases: list[str] | None = None) -> dict[str, Any] | None:
    names = [name.strip(), *_compact_aliases(aliases)]
    names = [item for item in names if item]
    if not names:
        return None
    query = {"$or": [{"name": {"$in": names}}, {"aliases": {"$in": names}}]}
    doc = persons().find_one(query)
    return serialize(doc) if doc else None


def ensure_person(
    name: str,
    aliases: list[str] | None = None,
    category: str | None = None,
) -> dict[str, Any]:
    existing = find_person(name, aliases)
    if existing:
        return existing

    now = utc_now()
    doc = {
        "_id": make_id("person"),
        "name": name.strip(),
        "aliases": _compact_aliases(aliases),
        "category": category or "coser",
        "source_events": [],
        "created_at": now,
        "updated_at": now,
    }
    persons().insert_one(doc)
    return serialize(doc)


def find_accounts_for_person(
    person_id: str,
    platform: str | None = None,
    statuses: list[str] | tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {"person_id": person_id}
    if platform:
        query["platform"] = platform
    if statuses:
        query["status"] = {"$in": list(statuses)}
    docs = social_accounts().find(query).sort("confidence_score", -1)
    return serialize(list(docs))


def find_person_profile(
    name: str,
    aliases: list[str] | None = None,
    include_candidates: bool = True,
) -> dict[str, Any]:
    person = find_person(name, aliases)
    if not person:
        return {
            "found": False,
            "person": None,
            "reliable_accounts": [],
            "candidate_accounts": [],
        }

    reliable = find_accounts_for_person(person["_id"], statuses=RELIABLE_STATUSES)
    candidates = (
        find_accounts_for_person(person["_id"])
        if include_candidates
        else []
    )
    return {
        "found": True,
        "person": person,
        "reliable_accounts": reliable,
        "candidate_accounts": candidates,
    }


def get_recent_search_result(
    platform: str,
    keyword: str,
    ttl_seconds: int,
) -> dict[str, Any] | None:
    now = utc_now()
    doc = search_queries().find_one(
        {
            "platform": platform,
            "keyword": keyword.strip(),
            "status": "success",
            "expires_at": {"$gt": now},
        },
        sort=[("created_at", -1)],
    )
    return serialize(doc) if doc else None


def save_search_result(
    platform: str,
    keyword: str,
    status: str,
    results: list[dict[str, Any]] | None = None,
    error: str | None = None,
    ttl_seconds: int = 86400,
) -> dict[str, Any]:
    now = utc_now()
    doc = {
        "_id": make_id("query"),
        "platform": platform,
        "keyword": keyword.strip(),
        "status": status,
        "result_count": len(results or []),
        "results": results or [],
        "error": error,
        "created_at": now,
        "expires_at": now + timedelta(seconds=ttl_seconds),
    }
    search_queries().insert_one(doc)
    return serialize(doc)


def upsert_candidate_account(
    person_id: str,
    candidate: dict[str, Any],
    score_result: dict[str, Any],
) -> dict[str, Any]:
    platform = candidate.get("platform")
    if not platform:
        raise ValueError("candidate.platform is required")

    uid = candidate.get("uid") or candidate.get("account_id")
    url = candidate.get("url") or candidate.get("profile_url")
    nickname = candidate.get("nickname") or candidate.get("display_name")

    if uid:
        selector = {"platform": platform, "uid": str(uid)}
    elif url:
        selector = {"platform": platform, "url": str(url)}
    else:
        selector = {
            "person_id": person_id,
            "platform": platform,
            "nickname": nickname,
        }

    status = score_result.get("status", "candidate_only")
    if status == "confirmed":
        status = "high_confidence"
    if status not in FIRST_STAGE_STATUSES:
        status = "candidate_only"

    now = utc_now()
    set_fields: dict[str, Any] = {
        "person_id": person_id,
        "platform": platform,
        "nickname": nickname,
        "bio": candidate.get("bio"),
        "avatar_url": candidate.get("avatar_url"),
        "followers": candidate.get("followers"),
        "confidence_score": int(score_result.get("score", 0)),
        "status": status,
        "evidence": score_result.get("evidence", []),
        "query_source": candidate.get("query_source", "live_search"),
        "source_platform": candidate.get("source_platform", platform),
        "raw_search_result": candidate.get("raw_search_result", {}),
        "last_checked_at": now,
        "updated_at": now,
    }
    if uid is not None:
        set_fields["uid"] = str(uid)
    if url:
        set_fields["url"] = str(url)

    update = {
        "$setOnInsert": {
            "_id": make_id("account"),
            "created_at": now,
        },
        "$set": set_fields,
    }
    result = social_accounts().update_one(selector, update, upsert=True)
    doc = social_accounts().find_one(
        {"_id": result.upserted_id} if result.upserted_id else selector
    )
    return serialize(doc)


def create_image_tasks(
    page_url: str,
    images: list[dict[str, Any]],
    source_platform: str = "bilibili",
    task_type: str = "ocr",
) -> dict[str, Any]:
    now = utc_now()
    saved: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for image in images:
        image_url = image.get("url") or image.get("image_url")
        if not image_url:
            skipped.append({"image": image, "reason": "missing_image_url"})
            continue

        selector = {"image_url": str(image_url)}
        update = {
            "$setOnInsert": {
                "_id": make_id("image_task"),
                "image_url": str(image_url),
                "page_url": page_url,
                "source_platform": source_platform,
                "task_type": task_type,
                "status": "pending",
                "attempts": 0,
                "ocr_result": None,
                "cleaned_result": None,
                "created_at": now,
            },
            "$set": {
                "last_seen_at": now,
                "updated_at": now,
                "image_metadata": image,
            },
        }
        result = image_tasks().update_one(selector, update, upsert=True)
        doc = image_tasks().find_one(
            {"_id": result.upserted_id} if result.upserted_id else selector
        )
        if doc:
            saved.append(serialize(doc))

    return {
        "ok": True,
        "page_url": page_url,
        "source_platform": source_platform,
        "created_count": len(saved),
        "skipped_count": len(skipped),
        "tasks": saved,
        "skipped": skipped,
    }


def get_image_task(task_id: str) -> dict[str, Any] | None:
    doc = image_tasks().find_one({"_id": task_id})
    return serialize(doc) if doc else None


def claim_pending_image_tasks(limit: int = 5) -> list[dict[str, Any]]:
    now = utc_now()
    claimed: list[dict[str, Any]] = []
    for doc in image_tasks().find({"status": "pending"}).sort("created_at", 1).limit(limit):
        result = image_tasks().find_one_and_update(
            {"_id": doc["_id"], "status": "pending"},
            {
                "$set": {
                    "status": "running",
                    "started_at": now,
                    "updated_at": now,
                },
                "$inc": {"attempts": 1},
            },
            return_document=True,
        )
        if result:
            claimed.append(serialize(result))
    return claimed


def mark_image_task_ocr_success(
    task_id: str,
    ocr_result: dict[str, Any],
    extracted_text: str,
) -> dict[str, Any] | None:
    now = utc_now()
    image_tasks().update_one(
        {"_id": task_id},
        {
            "$set": {
                "status": "ocr_done",
                "ocr_result": ocr_result,
                "extracted_text": extracted_text,
                "finished_at": now,
                "updated_at": now,
                "error": None,
            }
        },
    )
    return get_image_task(task_id)


def mark_image_task_failed(task_id: str, error: str) -> dict[str, Any] | None:
    now = utc_now()
    image_tasks().update_one(
        {"_id": task_id},
        {
            "$set": {
                "status": "failed",
                "error": error,
                "finished_at": now,
                "updated_at": now,
            }
        },
    )
    return get_image_task(task_id)


def health_check() -> dict[str, Any]:
    ping()
    ensure_indexes()
    return {
        "ok": True,
        "database": CONFIG.db_name,
        "collections": {
            "persons": CONFIG.persons_collection,
            "social_accounts": CONFIG.social_accounts_collection,
            "resolution_tasks": CONFIG.resolution_tasks_collection,
            "search_queries": CONFIG.search_queries_collection,
            "image_tasks": CONFIG.image_tasks_collection,
        },
    }
