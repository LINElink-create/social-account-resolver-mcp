# social-account-resolver-mcp

小红书公开用户解析 MCP。当前默认 MCP 入口只暴露 4 个工具：

- `xhs_login_status`
- `xhs_search_users`
- `xhs_get_user_profile`
- `xhs_resolve_user`

## 安装依赖

```powershell
C:\Users\LINE\anaconda3\envs\CoserSearcher\python.exe -m pip install -e .
C:\Users\LINE\anaconda3\envs\CoserSearcher\python.exe -m playwright install chromium
```

Linux 示例：

```bash
python -m pip install -e .
python -m playwright install chromium
```

## 环境变量

复制 `.env.example` 为 `.env`，至少设置：

```env
MONGO_URI=mongodb+srv://user:password@example.mongodb.net/?appName=Cluster0
MONGO_DATABASE=social_account_resolver
XHS_BROWSER_PROFILE_DIR=./data/xhs-browser-profile
XHS_HEADLESS=false
XHS_MIN_PAGE_INTERVAL_SECONDS=5
```

服务器无图形界面时可设置：

```env
XHS_HEADLESS=true
```

但小红书对服务器网络和 headless 环境可能返回登录、验证或访问异常；本项目不实现验证码绕过、代理池、UA 轮换或 stealth 插件。

## 首次登录

在可打开浏览器的机器上运行：

```powershell
C:\Users\LINE\anaconda3\envs\CoserSearcher\python.exe .\login_xhs_playwright.py
```

打开的小红书页面里扫码或手机号登录，确认已登录后回到终端按 Enter。登录态会保存在 `XHS_BROWSER_PROFILE_DIR`，不要提交 `data/` 目录。

## 启动 MCP Server

```powershell
C:\Users\LINE\anaconda3\envs\CoserSearcher\python.exe -m app.mcp_server
```

安装为可执行脚本后也可以：

```powershell
social-account-resolver-mcp
```

旧的多平台入口仍保留：

```powershell
legacy-social-account-resolver-mcp
```

## 简单测试

不通过 MCP，直接调用服务函数：

```powershell
$env:PYTHONIOENCODING='utf-8'
C:\Users\LINE\anaconda3\envs\CoserSearcher\python.exe -c "from app.mcp_server import xhs_login_status; import json; print(json.dumps(xhs_login_status(), ensure_ascii=False, indent=2))"
C:\Users\LINE\anaconda3\envs\CoserSearcher\python.exe -c "from app.mcp_server import xhs_search_users; import json; print(json.dumps(xhs_search_users('慕慕有奶糖', 10, True), ensure_ascii=False, indent=2))"
C:\Users\LINE\anaconda3\envs\CoserSearcher\python.exe -c "from app.mcp_server import xhs_resolve_user; import json; print(json.dumps(xhs_resolve_user('慕慕有奶糖', [], ['coser'], 10, 70), ensure_ascii=False, indent=2))"
```

OpenClaw 中优先调用：

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

## MongoDB 集合

- `xhs_search_cache`：搜索缓存，默认 7 天 TTL。
- `xhs_user_profiles`：公开主页资料缓存，默认 14 天 TTL。
- `xhs_user_candidates`：候选用户、评分和证据。
- `xhs_fetch_logs`：抓取日志，不记录 Cookie、token、localStorage 或 sessionStorage。

## 已知限制

- 只提取公开可见资料，不抓评论、私密内容、图片或视频文件。
- 不自动关注、点赞、收藏、评论或私信。
- 页面结构变化时可能返回 `error`，不会编造候选。
- 遇到登录失效、验证码或风控时返回 `login_required`、`rate_limited` 或 `error`。
- 单次搜索 `limit` 会被限制在 `1..20`。

