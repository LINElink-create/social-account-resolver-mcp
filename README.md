# social-account-resolver-mcp

这是一个用于解析 Coser / 嘉宾 / UP 主 / 主播等人物多平台公开社媒账号的 Python MCP Server。

项目当前保留多平台能力：

- B 站用户搜索
- 微博用户搜索
- FYDMWD 抖音 / 快手搜索
- 小红书公开用户搜索与主页资料补全
- MongoDB 缓存
- 候选账号置信度评分
- 候选账号保存
- B 站会员购页面图片提取
- 腾讯 OCR
- 活动页面结构化解析

本轮精简只移除了旧版小红书获取方式。B 站、微博、抖音/快手、OCR 等模块保持不变。

## 当前小红书方案

小红书现在统一使用新版 Playwright 方案：

- 使用 Playwright Chromium
- 使用持久化浏览器 profile 复用登录态
- 登录态目录由 `XHS_BROWSER_PROFILE_DIR` 指定，默认 `./data/xhs-browser-profile`
- 搜索结果和用户资料写入 MongoDB 缓存
- 只提取公开页面可见信息

旧方案已移除：

- Selenium / Edge / ChromeDriver
- Edge 专用 profile 登录脚本
- Cookie 注入式浏览器搜索
- 未签名 API 尝试
- 小红书旧移动端 / PC 静态页面兜底解析

为了兼容原有聚合逻辑，`tools.xiaohongshu` 中的旧函数名仍保留：

- `search_xiaohongshu_user`
- `search_xiaohongshu_cached_user`
- `enqueue_xiaohongshu_search`
- `run_xiaohongshu_worker`

这些函数内部已经转到新版 Playwright + MongoDB cache 实现。

## 项目结构

```text
social-account-resolver-mcp/
  app/
    mcp_server.py              # 新版小红书专用 MCP 入口
    config.py                  # Playwright 小红书配置
    schemas/
      xhs.py
    services/
      xhs_browser.py           # Playwright 浏览器与页面提取
      xhs_search.py            # 小红书用户搜索
      xhs_profile.py           # 小红书公开主页资料
      xhs_resolver.py          # 小红书候选解析流程
      xhs_scorer.py            # 小红书评分规则
      mongo_cache.py           # 小红书 MongoDB 缓存
      rate_limiter.py          # 小红书页面操作限速
  tools/
    bilibili.py                # B 站用户搜索
    weibo.py                   # 微博用户搜索
    fydmwd.py                  # 抖音 / 快手候选搜索
    xiaohongshu.py             # 小红书兼容包装层，调用 app/services
    resolver.py                # 多平台聚合解析
    scorer.py                  # 多平台候选评分
    database.py                # 多平台 MongoDB 数据访问
    webpage.py                 # 网页抓取与图片提取
    ocr.py                     # 腾讯 OCR
    event_parser.py            # 活动页结构化解析
  server.py                    # 旧多平台 MCP 入口
  login_xhs_playwright.py      # 小红书 Playwright 首次登录脚本
  test_ocr_flow.py             # OCR 测试脚本
  pyproject.toml
  .env.example
```

`data/xhs-browser-profile/` 是本地运行时生成的小红书浏览器登录态目录，不要提交。

## MCP 入口

### 小红书专用入口

默认入口：

```bash
social-account-resolver-mcp
```

对应：

```toml
social-account-resolver-mcp = "app.mcp_server:main"
```

暴露工具：

- `xhs_login_status`
- `xhs_search_users`
- `xhs_get_user_profile`
- `xhs_resolve_user`

### 多平台入口

旧多平台入口仍保留：

```bash
legacy-social-account-resolver-mcp
```

对应：

```toml
legacy-social-account-resolver-mcp = "server:main"
```

包含 B 站、微博、FYDMWD、小红书、OCR、网页图片提取、活动解析等工具。

## 主要工具

第一阶段账号解析工具：

- `find_person_profile`
- `search_bilibili_user`
- `search_weibo_user`
- `search_fydmwd_account`
- `search_xiaohongshu_user`
- `resolve_person_social_accounts`
- `score_account_match`
- `save_candidate_account`

小红书专用工具：

- `xhs_login_status`
- `xhs_search_users`
- `xhs_get_user_profile`
- `xhs_resolve_user`

网页 / OCR / 活动解析工具：

- `fetch_webpage`
- `collect_page_images`
- `filter_image_candidates`
- `collect_and_filter_page_images`
- `create_image_tasks`
- `ocr_image_url`
- `run_ocr_for_image_task`
- `run_pending_ocr_tasks`
- `parse_bilibili_show_event`

## 环境变量

复制 `.env.example` 为 `.env`。

MongoDB 兼容两套配置名：

```env
MONGO_URI=mongodb+srv://user:password@example.mongodb.net/?appName=Cluster0
MONGO_DATABASE=social_account_resolver
MONGODB_URI=mongodb+srv://user:password@example.mongodb.net/?appName=Cluster0
MONGODB_DB=social_account_resolver
MONGODB_DATABASE=social_account_resolver
```

微博和 OCR 如需使用，需要填写：

```env
WEIBO_COOKIE=
TENCENTCLOUD_SECRET_ID=
TENCENTCLOUD_SECRET_KEY=
TENCENTCLOUD_REGION=ap-guangzhou
```

小红书 Playwright 配置：

```env
XHS_BROWSER_PROFILE_DIR=./data/xhs-browser-profile
XHS_HEADLESS=false
XHS_MIN_PAGE_INTERVAL_SECONDS=5
XHS_NAVIGATION_TIMEOUT_MS=30000
XHS_SEARCH_CACHE_DAYS=7
XHS_PROFILE_CACHE_DAYS=14
```

说明：

- `XHS_BROWSER_PROFILE_DIR` 保存 Playwright 浏览器登录态。
- 首次登录必须使用 `XHS_HEADLESS=false`。
- 服务器后台运行可尝试 `XHS_HEADLESS=true`，但小红书可能对服务器 IP 或 headless 环境触发风控。
- `XHS_MIN_PAGE_INTERVAL_SECONDS` 默认 5 秒，用于限制小红书页面操作频率。

## 安装

Windows 当前 conda 环境：

```powershell
cd D:\MCP\SearchCoser\social-account-resolver-mcp
C:\Users\LINE\anaconda3\envs\CoserSearcher\python.exe -m pip install -e .
C:\Users\LINE\anaconda3\envs\CoserSearcher\python.exe -m playwright install chromium
```

Linux：

```bash
cd /opt/social-account-resolver-mcp
python -m pip install -e .
python -m playwright install --with-deps chromium
```

## 小红书首次登录

运行：

```powershell
C:\Users\LINE\anaconda3\envs\CoserSearcher\python.exe .\login_xhs_playwright.py
```

在打开的小红书页面里扫码或手机号登录。确认已登录后回到终端按 Enter。后续小红书工具会复用同一个 `XHS_BROWSER_PROFILE_DIR`。

服务器端如果没有桌面环境，可以用远程桌面、VNC、noVNC 或 SSH X11 forwarding 完成首次登录。

## 启动

小红书专用 MCP：

```powershell
C:\Users\LINE\anaconda3\envs\CoserSearcher\python.exe -m app.mcp_server
```

多平台 MCP：

```powershell
C:\Users\LINE\anaconda3\envs\CoserSearcher\python.exe -m server
```

或使用安装后的脚本：

```powershell
social-account-resolver-mcp
legacy-social-account-resolver-mcp
```

## 快速测试

```powershell
$env:PYTHONIOENCODING='utf-8'
```

小红书登录状态：

```powershell
C:\Users\LINE\anaconda3\envs\CoserSearcher\python.exe -c "from app.mcp_server import xhs_login_status; import json; print(json.dumps(xhs_login_status(), ensure_ascii=False, indent=2))"
```

小红书搜索：

```powershell
C:\Users\LINE\anaconda3\envs\CoserSearcher\python.exe -c "from app.mcp_server import xhs_search_users; import json; print(json.dumps(xhs_search_users('慕慕有奶糖', 10, True), ensure_ascii=False, indent=2))"
```

多平台聚合：

```powershell
C:\Users\LINE\anaconda3\envs\CoserSearcher\python.exe -c "from tools.resolver import resolve_person_social_accounts; import json; print(json.dumps(resolve_person_social_accounts('慕慕有奶糖', platforms=['bilibili','weibo','douyin','kuaishou','xiaohongshu'], realtime_xiaohongshu=False), ensure_ascii=False, indent=2))"
```

MongoDB 健康检查：

```powershell
C:\Users\LINE\anaconda3\envs\CoserSearcher\python.exe -c "from tools.database import health_check; import json; print(json.dumps(health_check(), ensure_ascii=False, indent=2))"
```

## MongoDB 集合

多平台集合：

- `persons`
- `social_accounts`
- `resolution_tasks`
- `search_queries`
- `image_tasks`

小红书专用缓存集合：

- `xhs_search_cache`
- `xhs_user_profiles`
- `xhs_user_candidates`
- `xhs_fetch_logs`

## 小红书评分摘要

`xhs_resolve_user` 会综合昵称、别名、小红书号、简介关键词和搜索排名评分。

低于 `min_confidence` 时返回：

```json
{
  "status": "needs_review",
  "manual_review_required": true
}
```

不会自动确认账号。

## 安全边界

项目只处理公开可见信息。

明确不实现：

- 验证码绕过
- 代理池
- UA 轮换
- stealth 插件
- 批量大规模采集
- 自动关注、点赞、收藏、评论、私信
- 评论抓取
- 私密内容抓取
- 图片或视频下载

遇到登录失效、验证码、访问频繁或风控页面时，小红书工具会返回 `login_required`、`rate_limited` 或 `error`。

## 注意事项

- `.env` 不提交。
- `data/` 不提交。
- 小红书登录态保存在 `data/xhs-browser-profile/`。
- 如果 `.env` 被覆盖，微博 Cookie 和腾讯 OCR 密钥需要重新填写。
- 小红书服务器端运行可能受 IP 和 headless 环境影响，必要时可采用“本地 worker 写 MongoDB，服务器 MCP 读缓存”的部署方式。

