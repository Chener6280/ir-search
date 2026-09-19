# Alpha派账号素材 adapter

`provider="alphapai"`，渠道为 `research_platform`。本版使用手机号密码访问已授权的共享会议纪要，不需要 Open API Key。实现位于可安装的 `ir_search` 包内，不调用本地 BrokerSkills，不接入旧 `deep_research`。

## 使用范围

- `search_materials`：关键词/证券与发布日期过滤、最多三页的有限目录扫描、按预算读取现有 AI 摘要。
- `retrieve`：读取 `alphapai://meeting/<ID>/summary` 或 `/transcript`。优先复用本机、同账号的短期详情缓存。
- 摘要是供应商已经生成的内容；本项目不调用问答、选股、写报告、生成摘要或转写接口。
- 个人录音转记、研报/社媒等全库检索尚未启用。这些不应被解读为当前账号已覆盖的能力。

## 配置与安装

在仓库根目录、使用运行 SDK/MCP 的同一个 Python 环境安装可选浏览器依赖和 Chromium：

```bash
python -m pip install '.[browser]'
python -m playwright install chromium
```

也可以安装自己构建的 wheel 并选择 `browser` extra。本项目本轮未发布 GitHub/PyPI；不要将上述安装说明解读为某个同名公开包已发布。

在自己的私有 `credentials.env` 中配置：

```dotenv
ALPHAPAI_MATERIALS_ENABLED=true
ALPHA_PIE_PHONE=
ALPHA_PIE_PWD=
ALPHAPAI_BROWSER_EXECUTABLE=
ALPHAPAI_CACHE_DIR=
ALPHAPAI_CACHE_TTL_SECONDS=3600
ALPHAPAI_MAX_PAGES=2
ALPHAPAI_MAX_CALLS=12
```

保留用户已有的 `ALPHA_PIE_PHONE` / `ALPHA_PIE_PWD` 名称，不将其误作 API Key。Linux/macOS 的凭证文件要求仅本人可读写。跨电脑时使用 `IR_SEARCH_CREDENTIALS_FILE` 指向那台电脑的私有 env；不要复制凭证到包或 Git。

默认使用 Playwright 安装的 Chromium。可选 `ALPHAPAI_BROWSER_EXECUTABLE` 是该电脑的浏览器可执行文件绝对路径，通常留空即可；不读取系统浏览器的个人登录资料。`source_health` 只检查配置和依赖是否存在，不代表登录成功。

默认缓存位置为 `~/.cache/ir-search/alphapai`，可通过 `ALPHAPAI_CACHE_DIR` 指定私有目录。缓存目录 0700、文件 0600，账号及密码共同隔离并认证缓存，密码变化会使旧缓存无法命中。默认有效期一小时，最多 200 份快照；到期不会命中，磁盘上的旧快照在达到容量上限时淘汰。设置 TTL 为 0 可关闭。缓存只含经过字段筛选的素材，不保存密码、Cookie 或登录 token。缓存命中返回原取数时间和 `cache_state=hit`，不冒充重新获取的最新内容；缓存写入失败仍返回实时素材，并给出警告。

## SDK 示例

```python
from ir_search import (
    MaterialSearchRequest, MaterialRequest, RequestContext,
    search_materials, retrieve,
)

result = search_materials(
    MaterialSearchRequest(
        question="贵州茅台9月渠道动销",
        providers=("alphapai",),
        entities=("茅台",),
        keywords=("渠道", "动销"),
        published_start="2026-09-01", published_end="2026-09-18",
        period_start="2026-09-01", period_end="2026-09-30",
        candidates_per_source=5, text_reads_per_source=1,
    ),
    context=RequestContext(timeout_seconds=120, max_operations=30),
)

for group in result.items:
    for version in group["versions"]:
        references = version["read_details"].get("available_text_references", {})
        if "transcript" in references:
            material = retrieve(
                MaterialRequest("茅台动销", (references["transcript"],)),
                context=RequestContext(timeout_seconds=120, max_operations=30),
            )
            # Inspect material.diagnostics and each material's read_details/warnings.
```

MCP 使用同名的 `search_materials` / `retrieve` 工具与相应 JSON 参数，不新增独立工具。首次登录建议 120 秒预算。问题出现“9月”等业务期间时，按核心框架要求明确传入 `period_start/end`；它与素材发布日期不同。当前不会因为会议在 9 月发布，就把其中所有经营数据都归为 9 月。

搜索取 `entities`、`keywords`、`question` 中第一组的第一个值作为上游文字查询，其余词在本地匹配，诊断会披露。框架补全的证券代码也会传给上游。日期发送为完整的起止时间，并再次核对返回的发布元数据；供应商无时区的时间按上海时区解释且明确标记。页码固定大小，超过剩余候选预算的行只计入 received、不计入 inspected。返回的 `has_more` 仅代表供应商当前目录，整个来源仍声明 `complete=false`，不保证全量召回。目前无跨请求继续翻页游标。

## 内容与引用约定

| 内容 | 返回标记 | 边界 |
|---|---|---|
| 列表摘要 | `search_snippet` / `metadata` | 仅用于发现线索，不能当正文 |
| `aiSummary.content` | `abstract`；retrieve 中为 `provider_summary`；`generated=true` | 供应商已有 AI 摘要，不能当受访者原话 |
| `mtSummary.content` 或明确标出的备用字段 | `source_excerpt`，`machine_transcribed=true` | 只代表此次取到的机器转录，完整性未证实 |

转录若为 JSON 片段，逐段拼接文本并保留字符区间、匿名角色、原始 `bg/ed` 值。时间单位仍标 `unverified`，不生成未经验证的跳转时间链接。引用附实际文本 SHA-256、字符区间，以及可匹配到的角色和原始时间范围；不把匿名角色推断为某个人。

会议发生时间、平台发布日期、发布机构和权限标记分别保留。不同权限字段可能同时真假并存，不代表无限权限。发布机构是供应商标签，尚未独立核验；来源保守标为 `DATA_VENDOR` / `MEDIA`，证据按研究观点处理，不仅凭机构名称升级为券商、监管或上市公司官方来源。内容类型中的“渠道调研”来自标题规则，属于启发式分类。

Alpha派会在列表、详情以及不同请求中重新编码 ID。客户端将实际 HTTPS 详情响应绑定到请求引用，并在搜索时核对标题与发布日期；缓存使用请求引用。不同 ID 但元数据重复时返回歧义诊断，不能当作独立佐证。一次跨会话读取已成功，但引用有效期和长期稳定性未知；失效时需要重新搜索。`alphapai://` 是来源引用，不是公开网页地址，也不含认证 token。

## 运行边界与错误

每批查询使用一个独立浏览器进程，凭证通过私有标准输入传入，退出时销毁会话，不保存登录态。启动页面最多允许 150 个站点资源请求；取数仅允许固定官方 HTTPS 来源的列表和详情接口，每次响应最多 4 MiB。逻辑操作预算分别计登录和每次素材接口调用；资源请求另外受数量/时间上限约束。详情调用间隔至少 1.3 秒。

缺少依赖/浏览器、超时、取消、账号失效、人机验证、权限、限流、每日额度及响应结构变化均返回固定诊断，不透传包含私人信息的异常。不会自动重试额度或认证错误，不绕过验证码或权限。`dry_run` 不登录、不读取缓存、不消耗详情额度。实际额度、价格与账号全量授权范围未知。

真实验收见 [验收记录](alphapai_material_acceptance.md)，前期参考比较见 [评估报告](alphapai_evaluation.md)。
