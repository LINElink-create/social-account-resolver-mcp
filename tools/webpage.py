from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

DEFAULT_TIMEOUT_SECONDS = float(os.getenv("HTTP_TIMEOUT_SECONDS", "12"))
MAX_HTML_CHARS = int(os.getenv("WEBPAGE_MAX_HTML_CHARS", "500000"))

IMAGE_ATTRS = (
    "src",
    "data-src",
    "data-original",
    "data-url",
    "data-img",
    "data-lazy-src",
    "data-original-src",
)
BACKGROUND_IMAGE_PATTERN = re.compile(r"url\((['\"]?)(?P<url>.+?)\1\)")
SRCSET_SPLIT_PATTERN = re.compile(r"\s*,\s*")
SMALL_DIMENSION_PATTERN = re.compile(r"(?P<value>\d+)(?:px)?")
IMAGE_URL_PATTERN = re.compile(
    r"^(?:https?:)?//[^\\s\"'<>]+\\.(?:png|jpe?g|webp|gif)(?:\\?[^\\s\"'<>]*)?$",
    re.IGNORECASE,
)

DROP_URL_KEYWORDS = (
    "avatar",
    "face",
    "icon",
    "emoji",
    "emote",
    "logo",
    "sprite",
    "badge",
    "ad-",
    "/ad/",
    "advert",
    "banner",
    "placeholder",
    "loading",
)


def _assert_http_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Only absolute http/https URLs are supported")


def _headers() -> dict[str, str]:
    return {
        "User-Agent": "Mozilla/5.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }


def _bilibili_show_project_id(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.netloc != "show.bilibili.com":
        return None
    if not parsed.path.endswith("/platform/detail.html"):
        return None
    project_id = parse_qs(parsed.query).get("id", [None])[0]
    return project_id if project_id and project_id.isdigit() else None


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _attr_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        return " ".join(str(item) for item in value if item is not None)
    return str(value)


def _int_attr(value: Any) -> int | None:
    if value is None:
        return None
    match = SMALL_DIMENSION_PATTERN.search(str(value))
    if not match:
        return None
    return int(match.group("value"))


def _absolute_url(base_url: str, value: str | None) -> str | None:
    if not value:
        return None
    clean = value.strip()
    if not clean or clean.startswith("data:") or clean.startswith("blob:"):
        return None
    return urljoin(base_url, clean)


def _is_image_url(value: str | None) -> bool:
    return bool(value and IMAGE_URL_PATTERN.search(value.strip()))


def fetch_webpage(
    url: str,
    include_html: bool = False,
    max_text_chars: int = 8000,
    max_links: int = 80,
) -> dict[str, Any]:
    _assert_http_url(url)
    response = httpx.get(
        url,
        headers=_headers(),
        timeout=DEFAULT_TIMEOUT_SECONDS,
        follow_redirects=True,
    )
    response.raise_for_status()
    html = response.text[:MAX_HTML_CHARS]
    soup = BeautifulSoup(html, "lxml")

    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()

    title = _clean_text(soup.title.get_text(" ", strip=True) if soup.title else "")
    description_tag = soup.find("meta", attrs={"name": "description"})
    description = (
        _clean_text(description_tag.get("content")) if description_tag else None
    )
    text = _clean_text(soup.get_text(" ", strip=True))[:max_text_chars]

    links: list[dict[str, str]] = []
    seen_links: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = _absolute_url(str(response.url), _attr_str(anchor.get("href")))
        if not href or href in seen_links:
            continue
        seen_links.add(href)
        links.append({"url": href, "text": _clean_text(anchor.get_text(" ", strip=True))})
        if len(links) >= max_links:
            break

    result: dict[str, Any] = {
        "ok": True,
        "url": url,
        "final_url": str(response.url),
        "status_code": response.status_code,
        "content_type": response.headers.get("content-type"),
        "title": title,
        "description": description,
        "text": text,
        "links": links,
    }
    if include_html:
        result["html"] = html
    return result


def _srcset_urls(srcset: str | None) -> list[str]:
    if not srcset:
        return []
    urls: list[str] = []
    for item in SRCSET_SPLIT_PATTERN.split(srcset.strip()):
        if not item:
            continue
        urls.append(item.split()[0])
    return urls


def _add_image(
    images: list[dict[str, Any]],
    seen: set[str],
    base_url: str,
    url_value: str | None,
    source: str,
    alt: str | None = None,
    width: Any = None,
    height: Any = None,
    context: str | None = None,
) -> None:
    image_url = _absolute_url(base_url, url_value)
    if not image_url or image_url in seen:
        return
    seen.add(image_url)
    images.append(
        {
            "url": image_url,
            "source": source,
            "alt": _clean_text(alt),
            "width": _int_attr(width),
            "height": _int_attr(height),
            "context": _clean_text(context)[:300] if context else None,
        }
    )


def collect_page_images(url: str, limit: int = 200) -> dict[str, Any]:
    page = fetch_webpage(url, include_html=True, max_text_chars=1000, max_links=0)
    html = page.pop("html")
    soup = BeautifulSoup(html, "lxml")
    final_url = page["final_url"]
    images: list[dict[str, Any]] = []
    seen: set[str] = set()

    _collect_bilibili_show_api_images(url, images, seen, limit)

    for meta in soup.find_all("meta"):
        prop = _attr_str(meta.get("property") or meta.get("name"))
        if prop in {"og:image", "twitter:image", "twitter:image:src"}:
            _add_image(images, seen, final_url, _attr_str(meta.get("content")), "meta")

    for tag in soup.find_all(["img", "source"]):
        for attr in IMAGE_ATTRS:
            _add_image(
                images,
                seen,
                final_url,
                _attr_str(tag.get(attr)),
                f"{tag.name}.{attr}",
                _attr_str(tag.get("alt")),
                _attr_str(tag.get("width")),
                _attr_str(tag.get("height")),
                tag.parent.get_text(" ", strip=True) if tag.parent else None,
            )
        for srcset_url in _srcset_urls(_attr_str(tag.get("srcset"))):
            _add_image(
                images,
                seen,
                final_url,
                srcset_url,
                f"{tag.name}.srcset",
                _attr_str(tag.get("alt")),
                _attr_str(tag.get("width")),
                _attr_str(tag.get("height")),
                tag.parent.get_text(" ", strip=True) if tag.parent else None,
            )
        if len(images) >= limit:
            break

    if len(images) < limit:
        for tag in soup.find_all(style=True):
            style = _attr_str(tag.get("style")) or ""
            for match in BACKGROUND_IMAGE_PATTERN.finditer(style):
                _add_image(
                    images,
                    seen,
                    final_url,
                    match.group("url"),
                    "style.background",
                    context=tag.get_text(" ", strip=True),
                )
                if len(images) >= limit:
                    break
            if len(images) >= limit:
                break

    return {
        "ok": True,
        "page": page,
        "image_count": len(images),
        "images": images[:limit],
    }


def _collect_bilibili_show_api_images(
    page_url: str,
    images: list[dict[str, Any]],
    seen: set[str],
    limit: int,
) -> None:
    project_id = _bilibili_show_project_id(page_url)
    if not project_id:
        return

    api_url = f"https://show.bilibili.com/api/ticket/project/get?id={project_id}"
    response = httpx.get(
        api_url,
        headers={**_headers(), "Referer": page_url},
        timeout=DEFAULT_TIMEOUT_SECONDS,
        follow_redirects=True,
    )
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data") or {}

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
                    f"bilibili_show_api{path}.url",
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
                        _attr_str(img.get("src")),
                        f"bilibili_show_api{path}.html_img",
                        alt=_attr_str(img.get("alt")),
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
                    f"bilibili_show_api{path}",
                    context=path,
                )

    walk(data)


def _looks_small(image: dict[str, Any], min_width: int, min_height: int) -> bool:
    width = image.get("width")
    height = image.get("height")
    if width is not None and width < min_width:
        return True
    if height is not None and height < min_height:
        return True
    return False


def filter_image_candidates(
    images: list[dict[str, Any]],
    min_width: int = 180,
    min_height: int = 120,
    keep_unknown_size: bool = True,
) -> dict[str, Any]:
    kept: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    for image in images:
        image_url = str(image.get("url") or image.get("image_url") or "")
        normalized_url = image_url.lower()
        reasons: list[str] = []

        for keyword in DROP_URL_KEYWORDS:
            if keyword in normalized_url:
                reasons.append(f"url_keyword:{keyword}")
                break

        if _looks_small(image, min_width, min_height):
            reasons.append("small_dimensions")

        if (
            not keep_unknown_size
            and image.get("width") is None
            and image.get("height") is None
        ):
            reasons.append("unknown_dimensions")

        if reasons:
            rejected.append({**image, "filter_reasons": reasons})
        else:
            kept.append({**image, "filter_reasons": []})

    return {
        "ok": True,
        "kept_count": len(kept),
        "rejected_count": len(rejected),
        "images": kept,
        "rejected": rejected,
    }


def collect_and_filter_page_images(
    url: str,
    limit: int = 200,
    min_width: int = 180,
    min_height: int = 120,
    keep_unknown_size: bool = True,
) -> dict[str, Any]:
    collected = collect_page_images(url, limit)
    filtered = filter_image_candidates(
        collected["images"], min_width, min_height, keep_unknown_size
    )
    return {
        "ok": True,
        "page": collected["page"],
        "collected_count": collected["image_count"],
        "kept_count": filtered["kept_count"],
        "rejected_count": filtered["rejected_count"],
        "images": filtered["images"],
        "rejected": filtered["rejected"],
    }
