from __future__ import annotations

import re
from typing import Any

COSER_KEYWORDS = ["coser", "cos", "漫展", "嘉宾", "摄影", "妆娘", "Lolita", "汉服", "声优"]
MARKETING_KEYWORDS = ["营销号", "合集", "搬运", "投稿", "代发", "广告", "种草号"]


def score_candidate(
    name: str,
    aliases: list[str],
    context_keywords: list[str],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    score = 0
    reason: list[str] = []
    nickname = str(candidate.get("nickname") or "")
    red_id = str(candidate.get("red_id") or "")
    desc = str(candidate.get("desc") or "")
    raw_rank = int(candidate.get("raw_rank") or 999)

    if nickname == name:
        score += 35
        reason.append("nickname_exact_name:+35")
    elif name and name in nickname:
        score += 25
        reason.append("nickname_contains_name:+25")

    for alias in aliases:
        if alias and alias in nickname:
            score += 20
            reason.append(f"nickname_contains_alias:{alias}:+20")
            break

    for alias in aliases:
        if alias and red_id == alias:
            score += 40
            reason.append(f"red_id_exact_alias:{alias}:+40")
            break

    keyword_hits = 0
    for keyword in context_keywords:
        if keyword and keyword in desc:
            keyword_hits += 1
    if keyword_hits:
        added = min(keyword_hits * 5, 20)
        score += added
        reason.append(f"context_keywords:{keyword_hits}:+{added}")

    if any(keyword.lower() in desc.lower() for keyword in COSER_KEYWORDS):
        score += 10
        reason.append("coser_context:+10")

    if raw_rank <= 3:
        score += 10
        reason.append("search_rank_top_3:+10")
    elif raw_rank <= 10:
        score += 5
        reason.append("search_rank_4_to_10:+5")

    if not candidate.get("avatar") or not desc:
        score -= 10
        reason.append("missing_avatar_or_desc:-10")

    combined = f"{nickname} {desc}"
    if any(re.search(keyword, combined, re.IGNORECASE) for keyword in MARKETING_KEYWORDS):
        score -= 20
        reason.append("marketing_or_repost_signal:-20")

    return {
        "confidence": max(0, min(100, score)),
        "reason": reason,
    }

