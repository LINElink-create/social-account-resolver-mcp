from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from pymongo import ASCENDING, MongoClient
from pymongo.collection import Collection
from pymongo.database import Database

from app import config

_client: MongoClient | None = None
_db: Database | None = None
_indexes_ready = False


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso_now() -> str:
    return utc_now().isoformat().replace("+00:00", "Z")


def clamp_limit(limit: int) -> int:
    return max(1, min(int(limit), 20))


def make_cache_key(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _sanitize(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _sanitize(item)
            for key, item in value.items()
            if key != "_id"
        }
    return value


def get_db() -> Database:
    global _client, _db

    if not config.MONGO_URI:
        raise RuntimeError("MONGO_URI is not configured")
    if _client is None:
        _client = MongoClient(config.MONGO_URI, serverSelectionTimeoutMS=8000)
        _db = _client[config.MONGO_DATABASE]
        ensure_indexes()
    if _db is None:
        raise RuntimeError("MongoDB database is not initialized")
    return _db


def collection(name: str) -> Collection:
    return get_db()[name]


def ensure_indexes() -> None:
    global _indexes_ready

    if _indexes_ready:
        return
    if _db is None:
        return

    _db.xhs_search_cache.create_index([("cache_key", ASCENDING)], unique=True)
    _db.xhs_search_cache.create_index("expires_at", expireAfterSeconds=0)
    _db.xhs_user_profiles.create_index([("user_id", ASCENDING)])
    _db.xhs_user_profiles.create_index([("profile_url", ASCENDING)])
    _db.xhs_user_profiles.create_index("expires_at", expireAfterSeconds=0)
    _db.xhs_user_candidates.create_index(
        [("platform", ASCENDING), ("user_id", ASCENDING), ("status", ASCENDING)]
    )
    _db.xhs_fetch_logs.create_index([("created_at", ASCENDING)])
    _indexes_ready = True


def get_search_cache(cache_key: str) -> list[dict[str, Any]] | None:
    doc = collection("xhs_search_cache").find_one(
        {"cache_key": cache_key, "expires_at": {"$gt": utc_now()}}
    )
    if not doc:
        return None
    return _sanitize(doc.get("results") or [])


def set_search_cache(
    cache_key: str,
    keyword: str,
    results: list[dict[str, Any]],
    aliases: list[str] | None = None,
    context_keywords: list[str] | None = None,
) -> None:
    now = utc_now()
    collection("xhs_search_cache").update_one(
        {"cache_key": cache_key},
        {
            "$set": {
                "cache_key": cache_key,
                "keyword": keyword,
                "aliases": aliases or [],
                "context_keywords": context_keywords or [],
                "results": results,
                "created_at": now,
                "expires_at": now + timedelta(days=config.XHS_SEARCH_CACHE_DAYS),
            }
        },
        upsert=True,
    )


def _profile_query(user_id_or_url: str) -> dict[str, Any]:
    if user_id_or_url.startswith("http"):
        return {"profile_url": user_id_or_url}
    return {"user_id": user_id_or_url}


def get_user_profile(user_id_or_url: str) -> dict[str, Any] | None:
    query = _profile_query(user_id_or_url)
    query["expires_at"] = {"$gt": utc_now()}
    doc = collection("xhs_user_profiles").find_one(query)
    return _sanitize(doc) if doc else None


def set_user_profile(profile: dict[str, Any]) -> None:
    now = utc_now()
    user_id = profile.get("user_id")
    profile_url = profile.get("profile_url")
    query: dict[str, Any]
    if user_id:
        query = {"user_id": user_id}
    else:
        query = {"profile_url": profile_url}

    doc = {
        **profile,
        "updated_at": now,
        "expires_at": now + timedelta(days=config.XHS_PROFILE_CACHE_DAYS),
    }
    collection("xhs_user_profiles").update_one(
        query,
        {"$set": doc, "$setOnInsert": {"created_at": now}},
        upsert=True,
    )


def find_confirmed_user(names: list[str]) -> dict[str, Any] | None:
    doc = collection("xhs_user_candidates").find_one(
        {
            "platform": "xhs",
            "status": "confirmed",
            "$or": [
                {"matched_names": {"$in": names}},
                {"nickname": {"$in": names}},
                {"red_id": {"$in": names}},
            ],
        },
        sort=[("updated_at", -1)],
    )
    return _sanitize(doc) if doc else None


def save_candidate(candidate: dict[str, Any]) -> None:
    now = utc_now()
    user_id = candidate.get("user_id")
    profile_url = candidate.get("profile_url")
    query = {"platform": "xhs", "user_id": user_id} if user_id else {
        "platform": "xhs",
        "profile_url": profile_url,
    }
    collection("xhs_user_candidates").update_one(
        query,
        {
            "$set": {**candidate, "platform": "xhs", "updated_at": now},
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )


def log_fetch(
    tool: str,
    keyword: str | None,
    status: str,
    duration_ms: int,
    error: str | None = None,
) -> None:
    collection("xhs_fetch_logs").insert_one(
        {
            "tool": tool,
            "keyword": keyword,
            "status": status,
            "error": error,
            "duration_ms": duration_ms,
            "created_at": utc_now(),
        }
    )
