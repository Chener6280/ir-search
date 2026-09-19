# IMA 素材 adapter

2026-09-16。`ima` 已作为独立来源进入 `search_materials` / `retrieve` 的 Python SDK 与 MCP。认证与协议参考 [IMA 官方 Agent 接入页](https://ima.qq.com/agent-interface)及其发布的 [官方 skill 1.1.10](https://app-dl.ima.qq.com/skills/ima-skills-1.1.10.zip)，实现为包内 Python 客户端；不依赖安装官方 skill、Node、桌面客户端或开发者的 skills 路径。参考包未随项目发布。

## 已实现

| 能力 | 处理方式 |
|---|---|
| 知识库发现 | 官方 `search_knowledge_base`，接受实测 `kb_id/kb_name` 和文档 `id/name` 两种字段；最多读取目录第一页 20 项，并明确未遍历所有知识库 |
| 知识库检索 | `search_knowledge`，每次最多两个字面查询，优先 `keywords`、其次 `entities`、最后完整 `question`；共享核心继续本地匹配、去重及定位引用 |
| 个人笔记检索 | `search_note` 正文关键词查询；摘要、创建时间和修改时间分别记录 |
| 原文读取 | `get_media_info` 返回文件/原始网页；笔记媒体的 `notebook_ext_info.notebook_id` 必须作为 `get_doc_content` 的 `note_id` |
| 文本类型 | 本人有权读取的笔记、PDF、DOCX、PPTX、UTF-8 TXT/Markdown、HTML、公开网页及微信公众号原文 |
| 引用 | `ima://media/<ID>`、`ima://note/<ID>` 是稳定来源引用，交给 `retrieve`；不是可在普通浏览器打开的公开文章地址 |
| 失败与权限 | 不可读取正文时保留已取得的元数据/摘要，分别标记权限、格式、分页、时间与数量预算等诊断 |

IMA 是内容存储和发现渠道，不保证材料是研报、电话会、原始事实或相互独立的来源。未知体裁使用 `document`，已知笔记使用 `note`；默认出版者、证据类型及权威性为未知，保守使用 UGC 来源层级，不把 PDF 自动标成公告或券商研报。

## 配置

在每台电脑自己的私有 env 中设置（模板为 `credentials.env.example`）：

```dotenv
IMA_MATERIALS_ENABLED=true
IMA_API_KEY=
IMA_CLIENT_ID=
IMA_INCLUDE_NOTES=true
IMA_KNOWLEDGE_BASE_IDS=
IMA_MAX_KNOWLEDGE_BASES=3
IMA_MAX_PAGES=2
```

`IMA_KNOWLEDGE_BASE_IDS` 为逗号分隔的知识库 ID；为空时发现目录第一页并按供应商顺序选择前几个库。默认最多 3 个知识库；每库、每个查询最多 2 页，个人笔记使用单独分页但共享候选/正文/整次请求预算。不是全账户穷尽搜索。

请求中的 `ima_knowledge_base_ids` 可从已配置范围中进一步选择；没有配置范围时可显式指定有权访问的库。结果 `collection_id` / `collection_name` 记录发现位置；显式选择 ID 而未额外查询库名称时名称为 null。配置和请求的知识库选择限制**发现范围**；`retrieve` 的显式媒体 ID 由当前 IMA 账号权限验证，不能把这些选择当作服务端安全访问控制。`IMA_INCLUDE_NOTES` 和 `ima_include_notes` 控制搜索是否包含个人笔记；显式笔记引用仍可按官方权限读取。

凭证只读取指定 env 文件，不从桌面 skill、官方 CLI 配置或系统环境中寻找备用账号。POSIX 权限要求 600。`IR_SEARCH_CREDENTIALS_FILE` 可指定本机 env；API Key / Client ID / 临时下载头、签名地址不进入日志、诊断、返回的 Document 或发布包。

## 调用

```python
from ir_search import MaterialSearchRequest, MaterialRequest, search_materials, retrieve

result = search_materials(MaterialSearchRequest(
    question="贵州茅台动销与渠道库存的相关材料",
    entities=["贵州茅台"], keywords=["茅台", "动销"],
    providers=["ima"],
    published_start="2026-09-01", published_end="2026-09-16",
    text_reads_per_source=3,
    # ima_knowledge_base_ids=["从配置或目录取得的实际 ID"],
    ima_include_notes=True,
))
# 原始返回中的覆盖、日期未知、正文/摘要、权限诊断必须一并检查。
refs = [version["source_ref"] for item in result.items for version in item["versions"]]
if refs:
    originals = retrieve(MaterialRequest(question="茅台动销", urls=refs[:3]))
```

MCP `search_materials` 支持相同字段。`retrieve` 接受上述 `ima://` 引用；需要指定来源时使用 `providers=["ima"]`，无需调用 `deep_research`。

## 口径、权限与边界

- 官方知识库搜索目前没有可靠的发布日期筛选；实测部分响应也不含 `is_end/next_cursor`。缺字段返回 `ima_pagination_unknown`，不会宣布扫描完成或擅自编造游标。
- `published_start/end` 仍按统一契约必填；拿到原始网页日期后进行本地过滤。日期未知的文件、笔记仍可返回并明确标记。`source_created_at/source_updated_at` 是笔记创建/修改时间，绝不代替内容发布日期或经营数据所属期间。
- 搜索关键词高亮只标为 `search_snippet`，个人笔记简介为 `abstract`；只有独立成功取得正文才升级为 `extracted_text`。摘要命中、跨渠道转载或多个来源计数不是事实核验。
- 官方 `get_doc_content` 限有权读取的笔记。已实测“搜索成功但 `get_media_info` 220030 拒绝正文”的情况，返回 `entitlement_denied`；未识别的新业务错误返回 `ima_upstream_rejected`，不输出供应商原始报错。权限不同于零命中。
- PDF 需要可选 `ir-search[extract]` 中的 PyMuPDF。DOCX/PPTX 使用有大小上限的标准库 OOXML 文本提取，不解析宏、不执行嵌入内容；图片、图表、演讲者备注、OCR、Excel、旧 DOC/PPT、音视频及 AI 会话未提供正文解析。相关格式返回明确不支持；Office 文本附带范围警告。
- 下载最多 8 MiB，API 响应最多 2 MiB，OOXML 解压大小及成员数量另外限制。临时文件认证只发往校验过的 IMA/Tencent COS HTTPS 主机，使用公共 DNS 地址固定、TLS 校验、请求期限与取消；携带认证的重定向关闭。官方空的 `X-IMA-Resource-Category` 头已兼容。
- 微信文章通过已有包内正文读取器，必要时采用已配置的极致了补充读取并保留 `text_provider`；可能计入该供应商套餐。网页只转发公开 URL，不转发 IMA 认证头。文件下载的临时签名 URL 不用作公开出处。
- 文件直接 `retrieve` 时，官方媒体详情未提供可信标题，因此返回“IMA 文档/笔记”及 `title_unknown`；从搜索返回的版本继续保留搜索标题。文件 publication/publisher 未知不会由文件名、知识库名或收藏时间猜测。

所有外部文本均为不可信来源。当前不提供 IMA AI 聊天、自动研究报告、上传、追加笔记、知识库写入、持久化索引或自动官方 skill 更新；不改动兼容 `deep_research`。
