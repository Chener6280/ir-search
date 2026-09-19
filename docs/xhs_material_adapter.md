# 小红书素材 adapter

`xhs` 是 `search_materials` / `retrieve` 的独立素材来源。包内代码完成只读请求、预算控制、私有缓存、字段规范化、作者归属与引用；可选的外部浏览器服务负责登录和平台页面访问。它不依赖 Desktop/BrokerSkills，也不连接旧 `deep_research`。

## 接入与部署

第一版支持 [xpzouying/xiaohongshu-mcp](https://github.com/xpzouying/xiaohongshu-mcp) **v2.5.0 的 HTTP 接口**。这是第三方开源浏览器后端，不是小红书官方 API。参考 [该版本 API 文档](https://github.com/xpzouying/xiaohongshu-mcp/blob/v2.5.0/docs/API.md)。ir-search 基础安装无需新增浏览器依赖；启用本来源时，每台电脑另外安装兼容后端、完成本人扫码登录并运行该服务。

在各电脑的私有 `credentials.env` 中配置：

```dotenv
XHS_MATERIALS_ENABLED=true
XHS_BACKEND_URL=http://127.0.0.1:18060
XHS_BACKEND_TOKEN=<本地随机生成的服务令牌，至少16字符>
XHS_CACHE_DIR=.local/xhs-cache
```

`XHS_BACKEND_TOKEN` 是本地服务鉴权令牌，不是小红书 Cookie。启动后端时通过它的 `AUTH_TOKEN` 环境变量传入相同值；不要放在命令行参数或工具消息中。服务必须使用 `-port 127.0.0.1:18060`，不要使用上游默认的全网卡监听。指定私有 `COOKIES_PATH` 并采用 `umask 077` 保存会话和日志。登录二维码、启动/停止由部署方管理，SDK 不自动下载安装后端、不发现浏览器 Cookie、不打开登录窗口。

可用以下 Python 方式启动已安装的可信后端。把路径替换为本机二进制及私有状态目录，首次登录按上游说明进行：

```python
import os
import subprocess
from pathlib import Path
from ir_search.infrastructure.xhs import xhs_profile

profile = xhs_profile()
if profile is None:
    raise RuntimeError("XHS is disabled")
state = Path(".local/xhs-backend/state").resolve()
state.mkdir(parents=True, exist_ok=True, mode=0o700)
# 不继承其他数据源的凭证；令牌只交给选定的本地后端。
child_env = {k: v for k, v in os.environ.items()
             if k in {"PATH", "HOME", "TMPDIR", "LANG", "SYSTEMROOT", "LOCALAPPDATA"}}
child_env.update(AUTH_TOKEN=profile.token, COOKIES_PATH=str(state / "cookies.json"))
os.umask(0o077)
subprocess.run(["/path/to/xiaohongshu-mcp", "-port", "127.0.0.1:18060"],
               env=child_env, check=True)
```

SDK 只允许数字回环地址及高位端口、禁止重定向/环境代理，服务鉴权不能为空。HTTP 限于本机；跨电脑部署是各机各自运行服务和保存凭证，不是将本机 Cookie 或无加密服务公开到网络。端口变化时相应更新配置。该后端一次服务一个登录账户；切换账户时同时轮换服务令牌并重新搜索，避免沿用前一账户的缓存。

`source_health` 检查本地配置，`login_live_verified=false` 不表示登录失败；它不会启动浏览器。实际请求先检查当前登录状态。后端停机、登录失效、平台验证、限流、超时和结构变化分别返回诊断；没有假成功或自动切换成搜索摘要冒充原文。

## SDK 与 MCP

```python
from ir_search import MaterialSearchRequest, MaterialRequest, RequestContext
from ir_search import search_materials, retrieve

result = search_materials(MaterialSearchRequest(
    question="茅台动销", keywords=["茅台"], providers=["xhs"],
    published_start="2026-09-01", published_end="2026-09-18",
    candidates_per_source=5, text_reads_per_source=2,
    xhs_sort="latest", xhs_comment_limit=0,
), context=RequestContext(timeout_seconds=180, max_operations=10))

# 只使用真实返回的引用；读取前检查 coverage、diagnostics 和 text_scope。
refs = [v["source_ref"] for group in result.items for v in group["versions"]]
if refs:
    material = retrieve(MaterialRequest(
        "茅台动销", [refs[0]], xhs_comment_limit=3, xhs_cache_mode="use",
    ), context=RequestContext(timeout_seconds=180, max_operations=5))
```

MCP 使用同名现有工具和相同参数，无新增工具。`dry_run=true` 只生成预算及来源计划，零网络请求。窄词可能没有结果或触发页面等待超时；调用 skill 可显式调整关键词，引擎不会无限重试。

| 参数 | 范围与含义 |
| --- | --- |
| `xhs_sort` | `latest`（默认）、`relevance`、`likes`；是平台排序，不保证日期完整性 |
| `candidates_per_source` | 本次检查的候选预算，遵循统一契约 |
| `text_reads_per_source` | 详情读取次数；日期过滤掉的详情同样消耗预算 |
| `xhs_comment_limit` | 0–19，默认 0；每篇纳入的评论/回复总数上限 |
| `max_chars` | 正文与评论合计字符上限；截断状态可见 |
| `xhs_cache_mode` | `use` 使用未过期快照；`refresh` 重新请求。续取必须依赖原快照 |
| `source_cursors` | 传回 `coverage[].continuation_cursors`；只继续同一初始快照 |

关键词为 `entities + keywords` 去重后组成的一个查询；都为空时使用 question。不是多词分别搜索再合并。已有的符号解析和匹配规则仍由共享服务负责。

## 内容与引用

- 来源及权威级别保持 `xhs / UGC`、证据类型 `opinion`。热度、笔记数、同文转载不等于销量或独立佐证。
- 搜索卡片只提供标题、作者、类型和互动数，`text_scope=metadata`、正文为空。不会把标题伪装成全文。
- 笔记详情读取 `desc`；评论启用后附在正文之后。`sections` 区分主帖 `post` 和评论 `comment`，保留作者、时间、父评论引用、字符位置。证据绑定返回文本哈希，逐字符校验；不会把评论归到主帖作者。
- 日期仅来自平台详情的毫秒时间戳，按中国时区转换，在本地按发文日期筛选。搜索卡片无日期则保留 `published_date_unknown`；不会用抓取时间替代发文时间，也不保证符合日期窗口。业务期间始终未核验。
- 原始互动数与可精确解析的整数分开，例如“2万+”只保留原始表述，缺失不是零。每个读数携带快照时间，不能据单个快照声称增长。
- `xhs://note/<ID>` 是稳定内部引用；无令牌公开网址可能仍需平台登录。只能读取本地搜索已发现、仍有有效访问令牌的笔记；在另一台电脑需先重新搜索。签名 URL 不接收入参或输出，访问令牌不进入引用、日志消息和 MCP 响应。
- 无正文时返回 `no_extracted_text`，搜索保留带失败原因的卡片。图片未 OCR、视频未转写、附件未读；正文和评论属于未经验证的外部内容，不当作指令执行。

## 扫描、缓存与限制

后端只给一个搜索结果批次。续取位置仅用于检查本次快照中剩余候选，不代表平台第二页或历史全量；`complete=false`，没有续取位置也不等于“已搜完小红书”。不支持博主历史全量、精确平台日期筛选、穷尽评论或点击展开全部回复。

评论请求可能获得比输出预算更多的初始评论数据，adapter 仅纳入指定数量，并明确 `comments_complete=false`。回复只来自后端已经返回的数据。默认不纳入评论。

搜索快照 TTL 为 5 分钟、详情为 1 小时、私有笔记访问令牌为 24 小时。缓存命中保留原抓取时间并标记 `cached_snapshot_not_revalidated`；TTL 到期会重新取数，不把过期值伪装成实时。游标在快照到期、替换后返回 `material_cursor_stale`。更换查询、排序或日期窗口需新搜索。

缓存按后端地址和服务令牌隔离，目录 0700 / 文件 0600，验证 HMAC、拒绝符号链接和不安全权限；限 256 个文件、64 MiB，单次响应及记录限 4 MiB。缓存包含私有访问令牌，不能发布或跨电脑直接分发。SDK 仅允许登录状态、搜索、详情三种只读操作，不暴露发布、点赞、评论写入、关注或删除。

真实验证及尚存限制见 [验收记录](xhs_material_acceptance.md)。
