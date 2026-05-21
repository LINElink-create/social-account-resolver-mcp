# social-account-resolver-mcp

Xiaohongshu public user resolver MCP.

The current MCP server only exposes these tools:

- `xhs_login_status`
- `xhs_search_users`
- `xhs_get_user_profile`
- `xhs_resolve_user`

## Install

```powershell
C:\Users\LINE\anaconda3\envs\CoserSearcher\python.exe -m pip install -e .
C:\Users\LINE\anaconda3\envs\CoserSearcher\python.exe -m playwright install chromium
```

Linux:

```bash
python -m pip install -e .
python -m playwright install --with-deps chromium
```

## Environment

Create `.env` from `.env.example`:

```env
MONGO_URI=mongodb+srv://user:password@example.mongodb.net/?appName=Cluster0
MONGO_DATABASE=social_account_resolver
XHS_BROWSER_PROFILE_DIR=./data/xhs-browser-profile
XHS_HEADLESS=false
XHS_MIN_PAGE_INTERVAL_SECONDS=5
XHS_NAVIGATION_TIMEOUT_MS=30000
XHS_SEARCH_CACHE_DAYS=7
XHS_PROFILE_CACHE_DAYS=14
```

`XHS_BROWSER_PROFILE_DIR` stores the Playwright browser session. Do not commit `data/`.

## First Login

Run on a machine that can show a browser window:

```powershell
C:\Users\LINE\anaconda3\envs\CoserSearcher\python.exe .\login_xhs_playwright.py
```

Log in to Xiaohongshu in the opened browser, then press Enter in the terminal. Later MCP calls reuse the same profile directory.

## Start MCP

```powershell
C:\Users\LINE\anaconda3\envs\CoserSearcher\python.exe -m app.mcp_server
```

Or after installation:

```powershell
social-account-resolver-mcp
```

## Quick Tests

```powershell
$env:PYTHONIOENCODING='utf-8'
C:\Users\LINE\anaconda3\envs\CoserSearcher\python.exe -c "from app.mcp_server import xhs_login_status; import json; print(json.dumps(xhs_login_status(), ensure_ascii=False, indent=2))"
C:\Users\LINE\anaconda3\envs\CoserSearcher\python.exe -c "from app.mcp_server import xhs_search_users; import json; print(json.dumps(xhs_search_users('慕慕有奶糖', 10, True), ensure_ascii=False, indent=2))"
C:\Users\LINE\anaconda3\envs\CoserSearcher\python.exe -c "from app.mcp_server import xhs_resolve_user; import json; print(json.dumps(xhs_resolve_user('慕慕有奶糖', [], ['coser'], 10, 70), ensure_ascii=False, indent=2))"
```

## OpenClaw Example

```json
{
  "tool": "xhs_resolve_user",
  "arguments": {
    "name": "慕慕有奶糖",
    "aliases": [],
    "context_keywords": ["coser"],
    "limit": 10,
    "min_confidence": 70
  }
}
```

## MongoDB Collections

- `xhs_search_cache`: search cache, 7 day TTL by default.
- `xhs_user_profiles`: public profile cache, 14 day TTL by default.
- `xhs_user_candidates`: scored user candidates.
- `xhs_fetch_logs`: fetch logs without cookie, token, localStorage, or sessionStorage data.

## Safety Boundaries

- Only public profile data is extracted.
- No comments, private data, image downloads, or video downloads.
- No follow, like, collect, comment, or message actions.
- No CAPTCHA bypass, proxy pool, UA rotation, or stealth plugin.
- If login expires or Xiaohongshu shows verification/risk control, tools return `login_required`, `rate_limited`, or `error`.
- Search `limit` is clamped to `1..20`.
