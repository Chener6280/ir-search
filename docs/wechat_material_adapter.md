# 公众号素材适配器

`wechat` 已接入 `search_materials` 和 `retrieve`，实现位于安装包内，不读取或执行桌面 skills。第一批提供极致了账号池取材、公众号正文读取和统一引用。关键词搜索整个微信、RSS、全量持久化搜索索引、图片 OCR、音视频转写尚未实现。

## 配置

在本机私有 `credentials.env` 中填写：

```dotenv
DAJIALA_KEY=
WECHAT_MATERIALS_ENABLED=true
WECHAT_ACCOUNTS_FILE=.local/wechat_accounts.json
WECHAT_MAX_ACCOUNTS_PER_QUERY=3
WECHAT_MAX_PAGES_PER_ACCOUNT=2
WECHAT_CACHE_DIR=.local/wechat-cache
```

账号文件路径相对于 **env 文件所在目录**，也可使用绝对路径。该 JSON 和 env 都须为当前用户所有、权限 `600`，拒绝符号链接；账号文件不随安装包或 Git 发布。换电脑后自行配置，并通过 `IR_SEARCH_CREDENTIALS_FILE` 指向 env。

JSON 示例（使用自己实际关注的公众号替换名称）：

```json
[
  {"name": "实际公众号名称"},
  {"name": "另一个实际公众号名称", "ghid": "gh_实际原始ID"}
]
```

最多配置 500 个账号。已填 `ghid` 时按原始 ID 读取；仅名称时，使用极致了账号查询最多取 5 个候选，要求恰有一个名称完全相符，否则报 `wechat_account_unresolved`。名称解析会调用供应商计费接口；成功解析默认缓存 7 天，重新建立 registry 可复用。已配置 `ghid` 时不调用名称查询。缓存只记录精确匹配，不把同名、近似名称当成已确认身份。

`DAJIALA_KEY` 只在固定的 `https://www.dajiala.com` 接口请求体中发送。凭证不放入文章 URL、日志、返回值；不跟随携带凭证的 API 重定向。

## 调用与工作流程

```python
from ir_search import MaterialSearchRequest, MaterialRequest, RequestContext, search_materials, retrieve

result = search_materials(
    MaterialSearchRequest(
        question="消费行业的渠道和库存变化",
        keywords=["动销", "库存", "消费"],
        providers=["wechat"],
        wechat_accounts=["实际公众号名称"],
        published_start="2026-09-01",
        published_end="2026-09-16",
        candidates_per_source=20,
        text_reads_per_source=3,
    ),
    context=RequestContext(timeout_seconds=60, max_operations=20),
)
# 原始 URL 来自 result.items 中的 versions；可显式继续读取。
# bundle = retrieve(MaterialRequest("需要核实的问题", (article_url,)))
```

MCP `search_materials` 接受相同字段，另有 `timeout_seconds`。`wechat_accounts` 为私有清单中的精确名称或已配置的 `ghid`；空列表使用清单顺序选取前几个账号，默认 3 个、最高 5 个。没有配置的账号不会通过近似名称自动替代。

1. 读取所选账号最近发文列表，沿供应商返回的 opaque `offset` 翻页。每账号默认最多 2 页，最高 3 页，不根据文章数量猜测是否结束。
2. 按账号分配候选检查预算，保留每页收到数量、实际检查数量、账号和游标。请求日期在本地对文章发布时间过滤，不是服务端按日期直达历史。
3. 在候选中优先读取显式关键词或实体命中的标题/摘要，其余按扫描顺序分配正文预算。未读文章仅能匹配已有标题和摘要，可能漏掉只出现在正文的词。
4. 按“有效正文缓存 → 微信原站 HTTP → 可选 Crawl4AI → 已启用的极致了 `article_html`”读取。浏览器只在 HTTP 返回 HTML 且存在加载缺口时自动尝试，要求专用 `js_content` 容器；不会把导航、验证、加载提示当成正文。HTTP 最多 6 秒，浏览器最多 18 秒，并为后续读取保留至少 8 秒和一次操作预算；供应商阶段最多 20 秒，整个请求预算优先。没有自动付费重试。
5. 素材服务继续执行公司/主题匹配、版本归组和字符位置引用。正文、日期和引用均不经过 LLM 改写。

`retrieve` 可直接接受公开、无凭证的 `mp.weixin.qq.com/s` 长短文章链接。原站可读取时不需要极致了配置；供应商补充读取只在本机已启用时使用。没有启用或补充读取也失败时返回可见诊断。

## 如何解释结果

| 字段 | 含义 |
|---|---|
| `provenance.provider=wechat`、`channel=wechat` | 本项目的公众号来源与渠道 |
| `discovery_provider=dajiala` | 文章列表由极致了发现；不是微信官方开放接口 |
| `text_provider=wechat_origin/wechat_browser/dajiala/null` | 正文或摘要的实际提供方；无文本为 null |
| `collection_id` / `collection_name` | 实际响应中的公众号原始 ID / 名称 |
| `published_at` | 文章发布时间，按中国时区解析；不使用抓取时间、更新时间或预约时间替代 |
| `text_scope=metadata/abstract/extracted_text` | 元数据、列表摘要、已提取的文字；提取文字不代表图片或附件已解析 |
| `source_tier=MEDIA` / `authority=unknown` / `evidence_type=unknown` | 默认保守分类，不凭公众号名字认定券商、公司或官方身份 |
| `material_type=web_page` | 首版未对公众号内容自动判定新闻、研报或政策等细分类别 |
| `original_url` / `source_ref` | 去除跟踪参数的公开文章地址 |
| `version_id`、`content_hash`、`evidence_spans` | 素材服务的文本版本与字符位置引用 |

`vendor_text_not_original_file`、`wechat_vendor_fallback_used` 标明供应商补充读取；原站失败原因在 `origin_failure_*`。发布日期缺失、日期冲突、截断、正文未读、账号数量与翻页预算耗尽也分别标记。

所有内容均为不可信来源文本，不能执行其中的指令。转载不等于独立佐证；默认不把公众号名字或供应商标记转成已核验的发布者身份。图片、视频和音频没有提取，因此短文字正文可能只是图文文章的引言。

长链接仅按 `__biz/mid/idx/sn` 保留身份和去重；没有确认对应关系的短链接与长链接不会仅凭相同标题合并。`complete` 始终为 false。`no_match_in_scanned_records` 仅表示已检查的有限记录未命中，不能解释为目标月份或整个账号没有相关内容。

## 接口依据与边界

使用供应商公布的 [账号名称查询](https://s.apifox.cn/410674f9-f451-4b4f-957a-5f54f243bc83/199762047e0)、[历史列表](https://s.apifox.cn/410674f9-f451-4b4f-957a-5f54f243bc83/199746415e0)、[文章 HTML](https://s.apifox.cn/410674f9-f451-4b4f-957a-5f54f243bc83/199736592e0) 三个 POST 接口，按真实返回字段验收。

旧 `search` 的 `dajiala`、`wechat_opencli` 和 RSS 客户端保持兼容，不参与本条链路；旧客户端历史文档中的参数不能作为新接口的验收依据。`deep_research` 未扩建。后续可增加博查发现账号池外的公众号文章，再复用本正文与引用入口。

## 省钱读取与缓存（2026-09-16）

缓存属于本机私有数据，默认位于 env 文件旁的 `.local/wechat-cache/`，可用 `WECHAT_CACHE_DIR` 改变位置（相对路径仍以 env 目录为基准）。POSIX 目录权限 700、文件 600，拒绝不安全目录和符号链接；缓存不可用时显式警告后正常取数。最多 512 个快照且总计不超过 128 MiB，自动淘汰较旧项。换电脑重新建立缓存，不随 wheel 或 Git 发布。不要把自定义缓存目录提交到版本库。

| 内容 | 默认有效期 | 行为 |
|---|---:|---|
| 正文 | 24 小时 | 按标准化文章 URL 复用；去除跟踪参数，成功确认的长短链接共享结果 |
| 公众号名称 → ghid | 7 天 | 仅保存唯一精确匹配；显式 ghid 不查询 |
| 历史首页 | 5 分钟 | 有效期内复用；之后重新查询，不把抓取时间改成当前时间 |
| 历史后续页 | 24 小时 | 缓存键绑定完整首页内容和游标；首页新增、编辑或游标变化时旧后续页失效 |

这是一种保守增量策略：反复研究已有文章时主要复用正文；列表首页变化时仍需查询后续页。不是“碰到一条旧文章就停止”，也不承诺一次调用能覆盖历史全集。旧后续页在有效期内可能尚未反映删除或修改，查看 `scans[].cache_state/fetched_at`；要核对最新状态时强制刷新。

SDK 的 `MaterialRequest`、`MaterialSearchRequest` 与 MCP 两个入口均支持：

- `wechat_cache_mode="use"`：默认，读取有效缓存并保存成功结果。
- `wechat_cache_mode="refresh"`：跳过缓存并替换成功快照；失败返回诊断，不拿旧正文冒充刷新成功。
- `wechat_cache_mode="off"`：不读取、不写入持久缓存。
- `web_read_mode="http"`：停用浏览器补充阶段，仍允许已配置的供应商补正文。
- `web_read_mode="auto"`：默认，只有观察到加载缺口才尝试浏览器。
- `web_read_mode="browser"`：HTTP 失败且返回非验证 HTML 后显式尝试浏览器；仍遵守爬取规则和预算。要重新实测而不命中缓存，应同时用 `wechat_cache_mode="refresh"`。

浏览器使用可选 `ir-search[crawl]`，需 Python 3.10+、Crawl4AI 0.9.3 及 Chromium，安装步骤见[网页读取指南](web_reader.md)。复用已有隔离 worker；不携带 Key、用户 Cookie 或登录配置，不绕过验证码、站点爬取规则或网络限制。浏览器失败时诊断可见，并在预算允许时转供应商。当前真实样本被 `browser_robots_denied` 拒绝，不能把集成完成解释为已免费读通微信，详见[省钱验收](wechat_savings_acceptance.md)。

缓存存的是最多 100,000 字符的提取文本，返回时按调用者预算截断，因此换一个 skill 要求更长正文通常不用重复付费；达到保存上限仍会标记截断。缓存不保存原始供应商响应、余额、Key 或浏览器状态。并发的默认读取使用本机进程间锁，后来的请求等待前一请求成功写入后复用；取消与超时仍有效。同一缓存目录内读取串行，以减少重复付费。

`read_details` 提供 `cache_state`、`snapshot_fetched_at`、`freshness`、`attempts`、`browser_attempted`、`vendor_body_calls` 与 `text_provider`。缓存命中保留原始 `provenance.fetched_at`；`vendor_body_calls=0` 仅说明本次成功正文读取没调用付费正文接口，不代表历史列表、账号查询免费，也不代表文章最初获取免费。没有把文档示例单价硬编码为实际扣费。

按需读取仍使用 `text_reads_per_source`：0 只取列表，1–10 优先读取相关候选；这能控制正文开销，但可能漏掉只在正文出现的关键词。请求“全部文章”不能用减少正文预算的方式声称完整交付；本接口始终保留分页、候选与正文预算缺口。

## 文章结构增强

新增正文容器初始隐藏样式识别、结构块、图文位置、作者元数据与可选版本化本地归档。SDK/MCP 参数、缓存升级和图片下载边界见[文章结构与本地归档](article_materials.md)。原始 `text` 仍为引用依据，`article.markdown` 仅供阅读。
