# 岗底斯（Gangtise）账号素材 adapter

`providers=["gangtise"]`，渠道为 `research_platform`。实现全部位于可安装的 `ir_search` 包内，通过 Python SDK / MCP 的 `search_materials` 和 `retrieve` 使用。参照 Alpha派的独立素材接入方式，访问用户在岗底斯网页账号中已获授权的内容。

本版采用用户提供的手机号密码与正常的新设备认证，不要求 API Key。官网另有 `accessKey` / `secretKey` 的官方 MCP；它与网页登录会话是两套认证，本版未混用。参考：[岗底斯登录入口](https://open.gangtise.com/#/login)、[官网](https://www.gangtise.com/)。

## 已实现的内容边界

| 类型 | 搜索与读取 | 明确边界 |
|---|---|---|
| 会议纪要 | `SUMMARY` 搜索、详情、受权限检查的文本下载 | 优先已有人工整理稿（usage 5），其次已有 AI 纪要（10），再其他文本（7）；不把纪要当逐字转录 |
| 研究报告 | `RSRCH_REPORT` 搜索、研报详情中的摘要 | 本轮未下载研报 PDF；无摘要返回缺失，不用列表片段冒充正文 |
| 研究观点 | `CHIEF` 搜索、观点详情 | 保留正文片段/描述摘要差别；附件、图片与全文完整性尚未核实 |

列表片段为 `search_snippet`。人工纪要、AI 纪要和研报摘要为 `abstract` / `provider_summary`；AI 纪要额外标 `generated=true`。观点正文及其他文本为 `source_excerpt`。所有类型都保留 `complete=false`，引用只能证明“此次返回的来源文本如此记载”，不能证明内容真实或已被独立核实。来源为 `DATA_VENDOR` / `MEDIA`，证据为 `OPINION`；机构名称只是供应商标签。

不调用 AI 问答、报告生成、上传、关注、订阅或购买接口，不连接旧 `deep_research`，不改变 Wind/JYDB/FMP 数值取数策略。

## 配置与首次认证

在自己的 `credentials.env` 中填入以下字段；保留已有的 `GANTISE_PHONE` / `GANTISE_PWD` 拼写即可：

```dotenv
GANGTISE_MATERIALS_ENABLED=true
GANTISE_PHONE=
GANTISE_PWD=
GANGTISE_BROWSER_EXECUTABLE=
GANGTISE_STATE_DIR=
GANGTISE_MAX_PAGES=2
GANGTISE_MAX_CALLS=12
```

也支持 `GANGTISE_PHONE` / `GANGTISE_PASSWORD`，若两种都填写，以这组规范拼写优先。不要将密码粘贴到聊天中。macOS/Linux 的凭证文件要求 0600。跨电脑时使用 `IR_SEARCH_CREDENTIALS_FILE` 指向该电脑自己的 env。

浏览器只用于账号认证，已认证后的素材请求使用标准库 HTTPS，不增加基础依赖。在运行 SDK/MCP 的同一个 Python 环境中安装：

```bash
python -m pip install '.[browser]'
python -m playwright install chromium
ir-search-gangtise-login
```

上述安装命令在本仓库运行；也可安装自己构建的 wheel。本轮未发布 PyPI/GitHub，不应安装未经核实的同名公开包。未安装命令入口时使用 `python -m ir_search.infrastructure.gangtise_auth`。

首次读取会尝试正常的无界面账号登录；遇新设备/SMS/微信认证，返回 `gangtise_login_challenge` 并记住待验证状态，避免每次查询重复登录。运行登录命令，在出现的窗口中完成短信或微信认证；验证码只输入该窗口。认证完成后命令仅返回安全状态，不输出 token。默认等待上限 300 秒。

`GANGTISE_BROWSER_EXECUTABLE` 通常留空；如手动指定，应使用支持显示窗口的 Chromium 可执行文件，不能使用仅支持无界面的 headless shell。使用独立浏览器资料，不读取 Safari/Chrome 的个人登录信息。

## 私有会话与跨电脑

默认状态目录 `~/.cache/ir-search/gangtise`，可通过 `GANGTISE_STATE_DIR` 修改。账号与密码共同决定隔离子目录；子目录 0700，session 文件 0600，使用 HMAC 检验篡改，符号链接和不安全权限拒绝读取。

**该目录包含登录 token 和独立浏览器的登录资料，应视为凭证目录。** 素材客户端在一小时内复用会话 token；这只是本地复用上限，服务器仍可能提前使其失效。过期/撤销会话失败会有诊断，重新运行认证命令恢复。平台的固定诊断不会包含原始错误正文。

密码变化会产生新的隔离目录，旧目录不会自动删除。macOS/Linux 使用可在进程退出时释放的文件锁，避免并发登录。Windows 回退为独占锁文件；进程异常退出遗留 `login.lock` 时，确认没有认证进程后再删除该锁文件。

每台电脑自行认证；不要把 env 或状态目录发布到 GitHub。本机当前状态目录为仓库已忽略的 `.local/gangtise-state`。本轮没有将素材加入持久缓存，读取会重新访问上游；浏览器登录资料会持续保存至用户删除状态目录。

`source_health`、`list_capabilities` 只检查配置/注册，不联网验证 token，不将“已填凭证”当“实际可读”。

## 调用示例

```python
from ir_search import (
    MaterialSearchRequest, MaterialRequest, RequestContext,
    search_materials, retrieve,
)

result = search_materials(
    MaterialSearchRequest(
        question="茅台9月渠道动销研究素材",
        providers=("gangtise",), entities=("茅台",),
        published_start="2026-09-01", published_end="2026-09-18",
        period_start="2026-09-01", period_end="2026-09-30",
        candidates_per_source=6, text_reads_per_source=1,
    ),
    context=RequestContext(timeout_seconds=120, max_operations=20),
)

for group in result.items:
    ref = group["versions"][0]["source_ref"]
    material = retrieve(
        MaterialRequest(question="茅台渠道动销", urls=(ref,)),
        context=RequestContext(timeout_seconds=120, max_operations=20),
    )
    # Inspect diagnostics, text_origin, read_details and exact evidence_spans.
```

MCP 使用同名工具和参数。引用格式为 `gangtise://summary/<ID>`、`gangtise://report/<rptId>`、`gangtise://opinion/<ID>`。研报 `rptId` 与搜索行 `id` 不同；客户端使用正确的研报标识并核对详情身份。引用不含凭证，也不是公开网页网址。

## 搜索与稳定性

- 查询选择 `entities`、`keywords`、`question` 中第一组的第一个值。其余词及证券代码由框架本地匹配；不会把 Wind 证券代码误作岗底斯的内部代码。
- 各内容类型轮流扫描，共享候选预算；固定页大小，默认每类最多两页、上限三页，每页最多 20 条。上游时间排序后，在本地过滤日期；历史窗口可能因扫描预算未覆盖，不能将空结果解释为全库无内容。
- `pubTime` 用于研报，`msgTime` 用于纪要/观点。纪要 `msgTime` 是平台显示时间，不能保证就是首次发表时间；无时区字符串按上海时间解释。未知/不合法日期不编造。业务数据期间仍须由调用者判断。
- 默认最多 12 次素材请求，上限 20；纪要详情和文本下载分别计次。认证另受请求时间预算及最多 300 个网页资源请求限制。单次响应上限 4 MiB，连续素材请求至少间隔 0.25 秒。
- 只允许固定官方 HTTPS 来源和已核对的搜索/详情路径；纪要文本路径必须来自同次已获授权的详情，文件格式限制为文本/HTML。拒绝任意地址、重定向、查询凭证、路径穿越与不支持的文档格式。
- 认证、权限、次数上限、限流、超时、取消、响应变化均保留诊断。列表成功但详情失败时只保留列表片段并标缺口，不升级为正文。失败类别不假装空结果，多个类别也不假装独立佐证。

实测范围、数量和局限见 [岗底斯验收记录](gangtise_material_acceptance.md)。
