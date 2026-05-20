from __future__ import annotations

from typing import Any, Literal, TypedDict


class XhsCandidate(TypedDict, total=False):
    user_id: str | None
    nickname: str
    red_id: str | None
    desc: str | None
    avatar: str | None
    profile_url: str | None
    raw_rank: int


class XhsStats(TypedDict):
    followers: int | None
    following: int | None
    likes_and_collects: int | None


class XhsProfile(TypedDict):
    user_id: str | None
    profile_url: str
    nickname: str | None
    red_id: str | None
    desc: str | None
    avatar: str | None
    stats: XhsStats
    cached: bool
    error: str | None


class XhsScoredCandidate(TypedDict, total=False):
    user_id: str | None
    nickname: str
    profile_url: str | None
    confidence: int
    reason: list[str]
    raw_rank: int


ResolveStatus = Literal[
    "matched",
    "needs_review",
    "not_found",
    "login_required",
    "rate_limited",
    "error",
]


JsonDict = dict[str, Any]

