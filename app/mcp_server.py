from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from app.services.mongo_cache import iso_now
from app.services.xhs_browser import XhsBrowserError, get_browser
from app.services.xhs_profile import xhs_get_user_profile as get_profile
from app.services.xhs_resolver import xhs_resolve_user as resolve_user
from app.services.xhs_search import xhs_search_users as search_users

mcp = FastMCP(
    "xhs-user-resolver-mcp",
    instructions=(
        "Resolve public Xiaohongshu user candidates with MongoDB cache and "
        "a persistent Playwright browser session. Tools return JSON only."
    ),
)


@mcp.tool()
def xhs_login_status() -> dict[str, Any]:
    """Check whether the persistent Playwright Xiaohongshu browser profile is logged in."""
    try:
        result = get_browser().login_status()
        return {
            "logged_in": bool(result["logged_in"]),
            "status": str(result["status"]),
            "message": str(result["message"]),
            "last_checked_at": iso_now(),
        }
    except XhsBrowserError as exc:
        return {
            "logged_in": False,
            "status": exc.status,
            "message": exc.message,
            "last_checked_at": iso_now(),
        }
    except Exception as exc:
        return {
            "logged_in": False,
            "status": "error",
            "message": str(exc),
            "last_checked_at": iso_now(),
        }


@mcp.tool()
def xhs_search_users(
    keyword: str,
    limit: int = 10,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Search public Xiaohongshu user candidates."""
    return search_users(keyword, limit, use_cache)


@mcp.tool()
def xhs_get_user_profile(
    user_id_or_url: str,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Fetch a public Xiaohongshu user profile."""
    return get_profile(user_id_or_url, use_cache)


@mcp.tool()
def xhs_resolve_user(
    name: str,
    aliases: list[str] | None = None,
    context_keywords: list[str] | None = None,
    limit: int = 10,
    min_confidence: int = 70,
) -> dict[str, Any]:
    """Search, enrich, score, and resolve the best public Xiaohongshu user candidate."""
    return resolve_user(name, aliases or [], context_keywords or [], limit, min_confidence)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

