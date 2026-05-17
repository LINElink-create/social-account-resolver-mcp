from __future__ import annotations

from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from tools import database
from tools.bilibili import search_bilibili_user as bilibili_search
from tools.fydmwd import search_fydmwd_account as fydmwd_search
from tools.ocr import general_basic_ocr_image_url as tencent_ocr_image_url
from tools.ocr import run_ocr_for_image_task as ocr_image_task
from tools.ocr import run_pending_ocr_tasks as ocr_pending_tasks
from tools.scorer import score_account_match as score_match
from tools.webpage import (
    collect_and_filter_page_images as webpage_collect_and_filter_images,
)
from tools.webpage import collect_page_images as webpage_collect_images
from tools.webpage import fetch_webpage as webpage_fetch
from tools.webpage import filter_image_candidates as webpage_filter_images
from tools.weibo import search_weibo_user as weibo_search

PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")

mcp = FastMCP(
    "social-account-resolver-mcp",
    instructions=(
        "Resolve public social-account candidates for a target person. "
        "First-stage tools discover, score, and save candidates; they do not "
        "automatically confirm identity."
    ),
)


@mcp.tool()
def find_person_profile(
    name: str,
    aliases: list[str] | None = None,
    include_candidates: bool = True,
) -> dict[str, Any]:
    """Check MongoDB for an existing person profile and known account records."""
    try:
        return database.find_person_profile(name, aliases, include_candidates)
    except Exception as exc:
        return {"found": False, "person": None, "reliable_accounts": [], "candidate_accounts": [], "error": str(exc)}


@mcp.tool()
def search_bilibili_user(
    person_name: str,
    aliases: list[str] | None = None,
    limit: int = 10,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Search Bilibili user candidates with MongoDB cache checks first."""
    return bilibili_search(person_name, aliases, limit, force_refresh)


@mcp.tool()
def search_weibo_user(
    person_name: str,
    aliases: list[str] | None = None,
    limit: int = 10,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Search Weibo user candidates with MongoDB cache checks first."""
    return weibo_search(person_name, aliases, limit, force_refresh)


@mcp.tool()
def search_fydmwd_account(
    keyword: str,
    platforms: list[str] | None = None,
    limit: int = 10,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Search FYDMWD for Douyin/Kuaishou candidates with MongoDB cache checks first."""
    return fydmwd_search(keyword, platforms, limit, force_refresh)


@mcp.tool()
def score_account_match(
    person: dict[str, Any],
    candidate: dict[str, Any],
    negative_keywords: list[str] | None = None,
) -> dict[str, Any]:
    """Score whether a candidate account appears to match the target person."""
    return score_match(person, candidate, negative_keywords)


@mcp.tool()
def save_candidate_account(
    candidate: dict[str, Any],
    score_result: dict[str, Any],
    person_id: str | None = None,
    person_name: str | None = None,
    aliases: list[str] | None = None,
    category: str | None = None,
) -> dict[str, Any]:
    """Save a candidate account and evidence to MongoDB without auto-confirming it."""
    try:
        if not person_id:
            if not person_name:
                raise ValueError("person_id or person_name is required")
            person = database.ensure_person(person_name, aliases, category)
            person_id = person["_id"]
        saved = database.upsert_candidate_account(person_id, candidate, score_result)
        return {"ok": True, "account": saved}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
def fetch_webpage(
    url: str,
    include_html: bool = False,
    max_text_chars: int = 8000,
    max_links: int = 80,
) -> dict[str, Any]:
    """Fetch a webpage and return title, text summary, links, and optional HTML."""
    try:
        return webpage_fetch(url, include_html, max_text_chars, max_links)
    except Exception as exc:
        return {"ok": False, "url": url, "error": str(exc)}


@mcp.tool()
def collect_page_images(url: str, limit: int = 200) -> dict[str, Any]:
    """Collect image URLs from a webpage for later OCR task creation."""
    try:
        return webpage_collect_images(url, limit)
    except Exception as exc:
        return {"ok": False, "url": url, "error": str(exc), "images": []}


@mcp.tool()
def filter_image_candidates(
    images: list[dict[str, Any]],
    min_width: int = 180,
    min_height: int = 120,
    keep_unknown_size: bool = True,
) -> dict[str, Any]:
    """Filter out avatars, icons, emoji, ads, and tiny images before OCR."""
    try:
        return webpage_filter_images(images, min_width, min_height, keep_unknown_size)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "images": [], "rejected": []}


@mcp.tool()
def collect_and_filter_page_images(
    url: str,
    limit: int = 200,
    min_width: int = 180,
    min_height: int = 120,
    keep_unknown_size: bool = True,
) -> dict[str, Any]:
    """Collect and filter page images in one call."""
    try:
        return webpage_collect_and_filter_images(
            url, limit, min_width, min_height, keep_unknown_size
        )
    except Exception as exc:
        return {"ok": False, "url": url, "error": str(exc), "images": [], "rejected": []}


@mcp.tool()
def create_image_tasks(
    page_url: str,
    images: list[dict[str, Any]],
    source_platform: str = "bilibili",
    task_type: str = "ocr",
) -> dict[str, Any]:
    """Create pending MongoDB image_tasks for OCR workers."""
    try:
        return database.create_image_tasks(page_url, images, source_platform, task_type)
    except Exception as exc:
        return {"ok": False, "page_url": page_url, "error": str(exc), "tasks": []}


@mcp.tool()
def ocr_image_url(image_url: str) -> dict[str, Any]:
    """Run Tencent Cloud GeneralBasicOCR on a single public image URL."""
    try:
        return tencent_ocr_image_url(image_url)
    except Exception as exc:
        return {"ok": False, "image_url": image_url, "error": str(exc)}


@mcp.tool()
def run_ocr_for_image_task(task_id: str) -> dict[str, Any]:
    """Run OCR for one MongoDB image_tasks document and persist the result."""
    return ocr_image_task(task_id)


@mcp.tool()
def run_pending_ocr_tasks(limit: int = 5) -> dict[str, Any]:
    """Claim pending image_tasks, call Tencent OCR, and persist OCR results."""
    return ocr_pending_tasks(limit)


@mcp.tool()
def health_check() -> dict[str, Any]:
    """Check MongoDB connectivity and ensure indexes exist."""
    try:
        return database.health_check()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
