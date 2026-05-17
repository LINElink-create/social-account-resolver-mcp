from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup

from .webpage import (
    DEFAULT_TIMEOUT_SECONDS,
    _add_image,
    _bilibili_show_project_id,
    _clean_text,
    _headers,
    _is_image_url,
    collect_and_filter_page_images,
)


def _timestamp_to_iso(value: Any) -> str:
    if value in (None, "", 0, "0"):
        return ""
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return str(value)
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


def _project_id_from_url_or_value(value: str) -> str:
    project_id = _bilibili_show_project_id(value)
    if project_id:
        return project_id
    parsed = urlparse(value)
    query_id = parse_qs(parsed.query).get("id", [None])[0]
    if query_id and query_id.isdigit():
        return query_id
    if value.isdigit():
        return value
    raise ValueError("Bilibili show project id is required")


def _fetch_bilibili_show_data(project_id: str, referer: str | None = None) -> dict[str, Any]:
    api_url = f"https://show.bilibili.com/api/ticket/project/get?id={project_id}"
    response = httpx.get(
        api_url,
        headers={**_headers(), "Referer": referer or api_url},
        timeout=DEFAULT_TIMEOUT_SECONDS,
        follow_redirects=True,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("errno") not in (0, "0", None) and payload.get("success") is not True:
        raise RuntimeError(f"Bilibili show API error: {payload.get('msg') or payload}")
    return payload.get("data") or {}


def _description_from_performance_desc(performance_desc: Any) -> str:
    if not isinstance(performance_desc, dict):
        return ""

    parts: list[str] = []
    for module in performance_desc.get("list") or []:
        if not isinstance(module, dict):
            continue
        details = module.get("details")
        if isinstance(details, str):
            text = BeautifulSoup(details, "lxml").get_text(" ", strip=True)
            if text:
                parts.append(text)
        elif isinstance(details, list):
            for item in details:
                if isinstance(item, dict):
                    title = _clean_text(item.get("title"))
                    content = _clean_text(item.get("content"))
                    if title or content:
                        parts.append(f"{title}: {content}".strip(": "))
                else:
                    text = _clean_text(item)
                    if text:
                        parts.append(text)
        elif isinstance(details, dict):
            text = _clean_text(details)
            if text:
                parts.append(text)

    return re.sub(r"\s+", " ", "\n".join(parts)).strip()


def _venue_from_data(data: dict[str, Any]) -> tuple[str, str]:
    place_info = data.get("place_info") or {}
    venue_info = data.get("venue_info") or {}
    city = (
        data.get("city_name")
        or place_info.get("city")
        or place_info.get("city_name")
        or venue_info.get("city")
        or ""
    )
    venue = (
        data.get("venue_name")
        or place_info.get("name")
        or place_info.get("venue_name")
        or venue_info.get("name")
        or ""
    )
    address = (
        data.get("address")
        or place_info.get("address")
        or venue_info.get("address")
        or ""
    )
    if address and address not in venue:
        venue = f"{venue} {address}".strip()
    return _clean_text(city), _clean_text(venue)


def _city_from_description(description_text: str) -> str:
    match = re.search(r"([\u4e00-\u9fa5]{2,12}市)", description_text)
    return match.group(1) if match else ""


def _image_urls_from_data(page_url: str, data: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    images: list[dict[str, Any]] = []
    seen: set[str] = set()

    def walk(value: Any, path: str = "") -> None:
        if len(images) >= limit:
            return
        if isinstance(value, dict):
            direct_url = value.get("url")
            if _is_image_url(direct_url):
                _add_image(
                    images,
                    seen,
                    page_url,
                    direct_url,
                    f"bilibili_show_api.{path}.url",
                    alt=value.get("desc") or value.get("title"),
                    context=path,
                )
            for key, item in value.items():
                walk(item, f"{path}.{key}" if path else str(key))
            return
        if isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]")
            return
        if isinstance(value, str):
            if "<img" in value:
                fragment = BeautifulSoup(value, "lxml")
                for img in fragment.find_all("img"):
                    _add_image(
                        images,
                        seen,
                        page_url,
                        img.get("src"),
                        f"bilibili_show_api.{path}.html_img",
                        alt=img.get("alt"),
                        context=path,
                    )
                    if len(images) >= limit:
                        return
            elif _is_image_url(value):
                _add_image(
                    images,
                    seen,
                    page_url,
                    value,
                    f"bilibili_show_api.{path}",
                    context=path,
                )

    walk(data)
    return images[:limit]


def parse_bilibili_show_event(
    url_or_project_id: str,
    image_limit: int = 100,
    filter_images: bool = True,
) -> dict[str, Any]:
    project_id = _project_id_from_url_or_value(url_or_project_id)
    page_url = (
        url_or_project_id
        if url_or_project_id.startswith(("http://", "https://"))
        else f"https://show.bilibili.com/platform/detail.html?id={project_id}"
    )
    data = _fetch_bilibili_show_data(project_id, page_url)
    city, venue = _venue_from_data(data)
    description_text = _description_from_performance_desc(data.get("performance_desc"))
    if not city:
        city = _city_from_description(description_text)

    if filter_images:
        image_result = collect_and_filter_page_images(page_url, limit=image_limit)
        images = image_result.get("images", [])
    else:
        images = _image_urls_from_data(page_url, data, image_limit)

    return {
        "ok": True,
        "project_id": str(data.get("id") or project_id),
        "title": _clean_text(data.get("name")),
        "city": city,
        "venue": venue,
        "start_time": _timestamp_to_iso(data.get("start_time")),
        "end_time": _timestamp_to_iso(data.get("end_time")),
        "description_text": description_text,
        "image_count": len(images),
        "images": images,
    }
