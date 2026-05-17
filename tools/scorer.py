from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from rapidfuzz import fuzz


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _names(person: dict[str, Any]) -> list[str]:
    names = [_text(person.get("name") or person.get("person_name"))]
    aliases = person.get("aliases") or []
    if isinstance(aliases, str):
        aliases = [aliases]
    names.extend(_text(alias) for alias in aliases)
    return [name for name in dict.fromkeys(names) if name]


def _add_evidence(
    evidence: list[dict[str, Any]],
    evidence_type: str,
    score: int,
    field: str,
    matched_value: Any,
    source: str,
    note: str,
    url: str | None = None,
) -> None:
    evidence.append(
        {
            "type": evidence_type,
            "score": score,
            "field": field,
            "matched_value": matched_value,
            "source": source,
            "url": url,
            "note": note,
            "created_at": _now_iso(),
        }
    )


def status_from_score(score: int) -> str:
    if score < 0:
        return "rejected"
    if score >= 85:
        return "high_confidence"
    if score >= 60:
        return "need_review"
    return "candidate_only"


def score_account_match(
    person: dict[str, Any],
    candidate: dict[str, Any],
    negative_keywords: list[str] | None = None,
) -> dict[str, Any]:
    names = _names(person)
    nickname = _text(candidate.get("nickname") or candidate.get("display_name"))
    bio = _text(candidate.get("bio"))
    verified_reason = _text(candidate.get("verified_reason"))
    source = candidate.get("source") or candidate.get("source_platform") or "unknown"
    url = candidate.get("url") or candidate.get("profile_url")
    score = 0
    evidence: list[dict[str, Any]] = []

    primary_name = names[0] if names else ""
    if primary_name and nickname == primary_name:
        score += 25
        _add_evidence(
            evidence,
            "nickname_exact",
            25,
            "nickname",
            nickname,
            source,
            "nickname exact match",
            url,
        )
    elif primary_name and nickname:
        ratio = fuzz.partial_ratio(primary_name, nickname)
        if ratio >= 90:
            score += 18
            _add_evidence(
                evidence,
                "nickname_fuzzy",
                18,
                "nickname",
                nickname,
                source,
                "nickname fuzzy match",
                url,
            )

    for alias in names[1:]:
        if alias and (alias == nickname or alias in nickname):
            score += 20
            _add_evidence(
                evidence,
                "alias_match",
                20,
                "nickname",
                alias,
                source,
                "alias match",
                url,
            )
            break

    for name in names:
        if name and bio and name in bio:
            score += 15
            _add_evidence(
                evidence,
                "bio_keyword_match",
                15,
                "bio",
                name,
                source,
                "bio keyword match",
                url,
            )
            break

    for name in names:
        if name and verified_reason and name in verified_reason:
            score += 15
            _add_evidence(
                evidence,
                "verification_match",
                15,
                "verified_reason",
                name,
                source,
                "verification info match",
                url,
            )
            break

    followers = candidate.get("followers")
    if followers not in (None, "", 0, "0"):
        score += 5
        _add_evidence(
            evidence,
            "activity_reasonable",
            5,
            "followers",
            followers,
            source,
            "followers or activity data available",
            url,
        )

    positive_without_nickname = sum(
        item["score"] for item in evidence if item["type"] != "nickname_exact"
    )
    if primary_name and len(primary_name) <= 2 and positive_without_nickname <= 5:
        score -= 20
        _add_evidence(
            evidence,
            "same_name_risk",
            -20,
            "nickname",
            nickname,
            source,
            "short name or same-name risk",
            url,
        )

    for keyword in negative_keywords or []:
        keyword_text = _text(keyword)
        if keyword_text and (keyword_text in bio or keyword_text in verified_reason):
            score -= 30
            _add_evidence(
                evidence,
                "bio_conflict",
                -30,
                "bio",
                keyword_text,
                source,
                "bio content conflict",
                url,
            )

    score = min(score, 100)
    return {
        "score": score,
        "status": status_from_score(score),
        "evidence": evidence,
    }
