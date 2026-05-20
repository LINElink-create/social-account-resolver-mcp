from __future__ import annotations

import re
import threading
from typing import Any
from urllib.parse import quote

from app import config
from app.services.rate_limiter import wait_for_xhs_slot

HOME_URL = "https://www.xiaohongshu.com/"
SEARCH_URL = "https://www.xiaohongshu.com/search_result?keyword={keyword}&type=user"
PROFILE_URL = "https://www.xiaohongshu.com/user/profile/{user_id}"


class XhsBrowserError(RuntimeError):
    def __init__(self, status: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def normalize_profile_url(user_id_or_url: str) -> tuple[str | None, str]:
    value = user_id_or_url.strip()
    if not value:
        raise XhsBrowserError("error", "user_id_or_url is required")
    if value.startswith("http://") or value.startswith("https://"):
        match = re.search(r"/user/profile/([^/?#]+)", value)
        user_id = match.group(1) if match else None
        if user_id:
            return user_id, PROFILE_URL.format(user_id=user_id)
        return None, value.split("#", 1)[0].split("?", 1)[0]
    return value, PROFILE_URL.format(user_id=value)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    clean = re.sub(r"\s+", " ", str(value)).strip()
    return clean or None


def _parse_count(value: str | None) -> int | None:
    if not value:
        return None
    text = value.strip().replace(",", "")
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*([万kK]?)", text)
    if not match:
        return None
    number = float(match.group(1))
    unit = match.group(2)
    if unit == "万":
        number *= 10000
    elif unit.lower() == "k":
        number *= 1000
    return int(number)


def _is_count_text(value: str) -> bool:
    return re.fullmatch(r"[0-9]+(?:\.[0-9]+)?\s*(?:万|k|K)?", value.strip()) is not None


def _stats_from_lines(lines: list[str]) -> dict[str, int | None]:
    stats = {
        "followers": None,
        "following": None,
        "likes_and_collects": None,
    }
    for index in range(len(lines) - 5):
        if (
            _is_count_text(lines[index])
            and lines[index + 1] == "关注"
            and _is_count_text(lines[index + 2])
            and lines[index + 3] == "粉丝"
            and _is_count_text(lines[index + 4])
            and lines[index + 5] == "获赞与收藏"
        ):
            stats["following"] = _parse_count(lines[index])
            stats["followers"] = _parse_count(lines[index + 2])
            stats["likes_and_collects"] = _parse_count(lines[index + 4])
            return stats
    return stats


def _detect_page_status(text: str) -> str | None:
    if re.search(r"验证码|访问频繁|安全验证|环境异常|滑块", text):
        return "rate_limited"
    if re.search(r"登录后查看|登录小红书|手机号登录|扫码登录", text):
        return "login_required"
    return None


class XhsBrowser:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._playwright: Any = None
        self._context: Any = None

    def _ensure_context(self) -> Any:
        if self._context is not None:
            return self._context

        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            raise XhsBrowserError(
                "error",
                f"Playwright is not installed or unavailable: {exc}",
            ) from exc

        config.XHS_BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        self._playwright = sync_playwright().start()
        self._context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(config.XHS_BROWSER_PROFILE_DIR),
            headless=config.XHS_HEADLESS,
            args=["--disable-dev-shm-usage"],
            viewport={"width": 1366, "height": 900},
            locale="zh-CN",
        )
        self._context.set_default_timeout(config.XHS_NAVIGATION_TIMEOUT_MS)
        return self._context

    def close(self) -> None:
        with self._lock:
            if self._context is not None:
                self._context.close()
                self._context = None
            if self._playwright is not None:
                self._playwright.stop()
                self._playwright = None

    def _new_page(self) -> Any:
        context = self._ensure_context()
        return context.new_page()

    def _goto(self, page: Any, url: str) -> None:
        wait_for_xhs_slot()
        page.goto(url, wait_until="domcontentloaded")
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass

    def login_status(self) -> dict[str, Any]:
        with self._lock:
            page = self._new_page()
            try:
                self._goto(page, HOME_URL)
                text = page.locator("body").inner_text(timeout=5000)
                status = _detect_page_status(text)
                if status == "rate_limited":
                    return {
                        "logged_in": False,
                        "status": "error",
                        "message": "Xiaohongshu shows a verification or access anomaly page.",
                    }
                if status == "login_required":
                    return {
                        "logged_in": False,
                        "status": "login_required",
                        "message": "Login is required in the persistent browser profile.",
                    }
                return {
                    "logged_in": True,
                    "status": "ok",
                    "message": "Persistent browser profile appears to be logged in.",
                }
            finally:
                page.close()

    def search_users(self, keyword: str, limit: int) -> list[dict[str, Any]]:
        with self._lock:
            page = self._new_page()
            try:
                self._goto(page, SEARCH_URL.format(keyword=quote(keyword)))
                try:
                    page.get_by_text("用户", exact=True).click(timeout=3000)
                    page.wait_for_timeout(1500)
                except Exception:
                    pass

                text = page.locator("body").inner_text(timeout=8000)
                status = _detect_page_status(text)
                if status:
                    raise XhsBrowserError(status, f"Xiaohongshu returned {status}.")

                users = page.evaluate(
                    """
                    (limit) => {
                      const anchors = Array.from(document.querySelectorAll('a[href*="/user/profile/"]'));
                      const seen = new Set();
                      const results = [];
                      for (const anchor of anchors) {
                        const href = anchor.href;
                        if (href.includes('channel_type=web_search_result_notes')) continue;
                        const match = href.match(/\\/user\\/profile\\/([^/?#]+)/);
                        const userId = match ? decodeURIComponent(match[1]) : null;
                        if (!userId || seen.has(userId)) continue;
                        const card = anchor.closest('[class*="user"], [class*="card"], section, div') || anchor.parentElement;
                        const rawText = (card ? card.innerText : anchor.innerText || '').trim();
                        const lines = rawText.split(/\\n+/).map(x => x.trim()).filter(Boolean);
                        if (!lines.length || lines[0] === '我') continue;
                        if (!/小红书号|粉丝|笔记/.test(rawText)) continue;
                        const img = card ? card.querySelector('img') : null;
                        const redMatch = rawText.match(/小红书号[:：]\\s*([^\\n\\s]+)/);
                        const profileUrl = userId ? `https://www.xiaohongshu.com/user/profile/${userId}` : href.split('#')[0].split('?')[0];
                        seen.add(userId);
                        results.push({
                          user_id: userId,
                          nickname: lines[0] || '',
                          red_id: redMatch ? redMatch[1] : null,
                          desc: lines.slice(1, 4).join(' ') || null,
                          avatar: img ? img.src : null,
                          profile_url: profileUrl,
                          raw_rank: results.length + 1
                        });
                        if (results.length >= limit) break;
                      }
                      return results;
                    }
                    """,
                    limit,
                )
                return users or []
            finally:
                page.close()

    def get_user_profile(self, user_id_or_url: str) -> dict[str, Any]:
        user_id, profile_url = normalize_profile_url(user_id_or_url)
        with self._lock:
            page = self._new_page()
            try:
                self._goto(page, profile_url)
                text = page.locator("body").inner_text(timeout=8000)
                status = _detect_page_status(text)
                if status:
                    raise XhsBrowserError(status, f"Xiaohongshu returned {status}.")

                raw = page.evaluate(
                    """
                    () => {
                      const bodyText = document.body ? document.body.innerText : '';
                      const lines = bodyText.split(/\\n+/).map(x => x.trim()).filter(Boolean);
                      const nicknameNode =
                        document.querySelector('[class*="user-name"]') ||
                        document.querySelector('[class*="nickname"]') ||
                        document.querySelector('h1');
                      const avatarNode =
                        document.querySelector('img.user-image') ||
                        document.querySelector('[class*="user"] img[src*="avatar"]') ||
                        document.querySelector('img[src*="avatar"]');
                      const metaDesc = document.querySelector('meta[name="description"]');
                      return {
                        url: location.href,
                        title: document.title || null,
                        lines,
                        text: bodyText,
                        nickname: nicknameNode ? nicknameNode.innerText.trim() : null,
                        avatar: avatarNode ? avatarNode.src : null,
                        meta_desc: metaDesc ? metaDesc.getAttribute('content') : null
                      };
                    }
                    """
                )
                return self._normalize_profile(raw, user_id, profile_url)
            finally:
                page.close()

    def _normalize_profile(
        self,
        raw: dict[str, Any],
        user_id: str | None,
        fallback_url: str,
    ) -> dict[str, Any]:
        text = str(raw.get("text") or "")
        lines = [str(line).strip() for line in raw.get("lines") or [] if str(line).strip()]
        nickname = _text(raw.get("nickname"))
        if not nickname and raw.get("title"):
            nickname = _text(str(raw["title"]).split(" - ")[0])
        desc = _text(raw.get("meta_desc"))
        if not desc:
            desc_lines = [
                line
                for line in lines
                if not re.search(r"关注|粉丝|获赞|收藏|小红书号", line)
            ]
            desc = _text(" ".join(desc_lines[1:4]))

        red_match = re.search(r"小红书号[:：]\s*([^\n\s]+)", text)
        stats = _stats_from_lines(lines)
        red_index = next(
            (index for index, line in enumerate(lines) if line.startswith("小红书号")),
            None,
        )
        if red_index is not None:
            desc_parts: list[str] = []
            for line in lines[red_index + 1 :]:
                if _is_count_text(line) or line in {"关注", "粉丝", "获赞与收藏", "笔记", "收藏"}:
                    break
                desc_parts.append(line)
            desc = _text(" ".join(desc_parts)) or desc

        if user_id is None:
            matched = re.search(r"/user/profile/([^/?#]+)", str(raw.get("url") or ""))
            user_id = matched.group(1) if matched else None

        return {
            "user_id": user_id,
            "profile_url": PROFILE_URL.format(user_id=user_id) if user_id else fallback_url,
            "nickname": nickname,
            "red_id": red_match.group(1) if red_match else None,
            "desc": desc,
            "avatar": raw.get("avatar"),
            "stats": stats,
            "raw": {
                "title": raw.get("title"),
                "lines": lines[:80],
            },
        }


_browser = XhsBrowser()


def get_browser() -> XhsBrowser:
    return _browser
