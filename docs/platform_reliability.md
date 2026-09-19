# 来源诊断、续取与运行经验（2026-09-18）

本轮在现有 `ir_search` 内独立实现三项改进：来源体检、失败后有界续取、私有运行摘要。核心仍为 `get_data`、`search_materials`、`retrieve`，调用 skill 负责研究判断和流程编排；没有引入 LLM 到搜索热路径，没有扩建 `deep_research`。

## 参考项目及取舍

| 参考 | 核对范围 | 本项目采用的部分 |
| --- | --- | --- |
| [Agent-Reach](https://github.com/Panniantong/Agent-Reach/tree/a19a171fa980a0785849596492e0af4db800c82f) | README、安装说明、doctor、channel 基类、小红书渠道 | 独立的来源体检、声明后端与真实探测分开、失败给出处理建议。没有运行其安装器或增加运行依赖 |
| [MediaCrawler](https://github.com/NanmiCoder/MediaCrawler/tree/8ecfa31de22c5cbc890e5b0b520b450f24ec4b21) | README、基础配置、采集抽象、存储说明和许可证 | 借鉴采集与存储分离、主帖/评论归属和有界采样；补强已有续取位置的保存和消费。未复制其实现或声称接入全部平台。当前许可证为非商业学习用途，保留为设计参考 |
| [browser-use/web-ui](https://github.com/browser-use/web-ui/tree/61962296c38a0d064e0ba02c827192b7a81d1819) | README、浏览器和会话封装 | 将会话就绪、本人验证、读取结果分开报告；复用已有登录助手/本地后端边界。没有引入 Gradio 或 LLM 浏览器执行器 |
| [公众号 WikiSkill 介绍](https://mp.weixin.qq.com/s/rmS1mtEBMdb_png2Nvddvg)、[论文 v1](https://arxiv.org/abs/2608.27454v1) | 已取得公众号正文，并核对论文摘要 | 运行观察、维护者审阅的规则、回归验证分开保存。这里只采用分层思路，没有复现其技能进化算法或实验 |

源文档和固定版本 SHA-256 记录在私有本地参考库，当前共 39 条记录。运行时不读取参考库或 Desktop。上述资源不会因为被引用就变成可执行指令。

## 来源体检

```python
from ir_search import diagnose_sources, RequestContext

local = diagnose_sources()  # 19 个来源；不联网、不安装依赖、不登录
xhs = diagnose_sources(["xhs"], live=True,
                       context=RequestContext(timeout_seconds=40, max_operations=3))
```

CLI 与 SDK 使用同一实现：

```sh
ir-search-doctor
ir-search-doctor --provider xhs --live --timeout 40
```

MCP 复用现有 `source_health(providers=["xhs"], live=true, timeout_seconds=40)`，返回新增的 `operational_status`；旧 `sources` 和 `configured_sources` 保持兼容。默认调用只检查本地状态。

| 状态 | 含义 |
| --- | --- |
| `disabled` | 来源未启用 |
| `configuration_error` | 当前配置无法使用，需检查本机 env |
| `dependency_missing` | 当前 Python 环境缺少来源必需依赖；附 `installation_extra` |
| `configured_unverified` | 配置和必需 Python 模块检查通过；尚未证明账号或取数可用 |
| `login_verified` | 本次只读登录探测通过；不等于搜索、正文或行情取数验收通过 |
| `probe_failed` | 本次探测失败，包含稳定诊断和处理建议 |

`backend_chain` 是现有路径的声明，`active_backend` 只有实际探测通过时才有值。可选依赖缺少时单独列出，不会把可用的 HTTP 路径整体判为不可用。Python 包存在也不代表其浏览器二进制或远端平台可用，`runtime_verified=false` 始终明确保留。

当前轻量 live 探测只实现小红书登录状态，其他来源返回 `live_probe_not_implemented`，不发送付费数据请求。实际搜索/正文/数据可用性应以具体任务结果判断。一次登录成功不会将 `search_live_verified`、`retrieve_live_verified`、`get_data_live_verified` 改成 true。报告顶层 `status=ok` 仅表示诊断流程完成，不表示所有来源已联网验收。

同一台电脑的不同 Python 环境可能有不同依赖。体检应在实际运行 SDK/MCP 的环境执行；安装提示面向该环境，不会自动修改系统 Python、网络代理或浏览器配置。XHS 显式 live 检查会使用既有私有缓存锁；默认本地检查不创建会话文件。

## 中断、限流与续取

修复了公众号和 IMA 翻到下一页时遇到中断、限流或上游故障，上一页已经提供的下一位置没有及时保留的问题。星球在详情读取受限时，续取位置现在停在实际检查的位置，不跳过当前批次尚未检查的帖子。

同一来源遇到认证失败、验证挑战、限流、额度、网络不可用和预算停止时，本次不再继续请求该来源的其他页/详情。资源级 `entitlement_denied` 与账户级认证不同：停止当前资源，允许继续调用方已经选择且独立授权的其他集合。不会改用另一后端绕过该资源权限。网页原有 HTTP→可选浏览器的条件和边界保持不变。

完整超时/取消仍遵守共享请求期限；不能将期限之后才完成的页面当作及时结果。此轮修复的是 adapter 已返回页面的续取保留、来源级停止和实际消费位置，不是让超时请求无限运行。

```python
from ir_search import MaterialSearchRequest, search_materials, next_material_request, RequestContext

request = MaterialSearchRequest(
    "茅台动销", keywords=["茅台"], providers=["xhs"],
    published_start="2026-09-01", published_end="2026-09-18",
    candidates_per_source=3, text_reads_per_source=1,
)
first = search_materials(request, context=RequestContext(timeout_seconds=180))
following = next_material_request(first, candidates_per_source=5, text_reads_per_source=1)
if following is not None:
    second = search_materials(following, context=RequestContext(timeout_seconds=180))
```

辅助函数只选择实际提供游标的来源，保留查询、日期与账户绑定，也保留搜索时解析出的证券代码。它不重跑没有游标的其他来源。`None` 仅表示本次没有可继续的位置，不表示已搜完平台。XHS 快照仍为 5 分钟有效期；辅助函数不会续命或重新生成平台游标。

若结果因全局 `limit` 丢弃了已匹配项，辅助函数会要求先扩大返回上限或缩小候选预算重做本页，避免直接续取漏掉这些结果。支持游标的来源仍为 IMA、公众号、星球和小红书，其他来源没有因此获得历史全量分页。

另修复游标长度契约：普通检索词的 2,000 字符上限不再误用于编码后的游标；游标继续采用独立 12,000 字符上限、严格结构与绑定校验。既有合法 v1 游标继续兼容。

## 私有运行摘要与经验维护

```python
from ir_search import search_materials, record_material_run

result = search_materials(request, audit_dir=".local/material-runs")
# 对已有结果也可显式记录：
receipt = record_material_run(result, ".local/material-runs")
```

MCP 的 `search_materials` 同样接受可选 `audit_dir`。默认不写运行日志。记录包括请求/结果指纹、来源状态、扫描/命中数量、正文层级计数、已知诊断及处理建议；不保存问题明文、正文、URL、作者/账户名、游标、原始异常或模型思维过程。未知诊断归并为 `unknown_diagnostic` 并计数，避免任意上游字符串落入日志。素材原文如需归档，仍显式使用已有 `retrieve(..., archive_dir=...)`，两类文件用途分开。

运行文件以摘要 SHA-256 命名，目录 0700、文件 0600，原子写入；相同结果验证后复用，已有文件内容异常时报告错误而不覆盖。每目录上限 1,000 个记录、64 MiB，单记录上限 256 KiB；不会自动删除历史。路径、权限或容量错误只出现在 `result.audit`，不会丢弃已经读取的素材。操作记录是私有数据，指纹不等于匿名化承诺，也不是投研证据。

维护闭环为：私有原始验收结果/运行摘要 → 人工审阅的 [故障模式](reliability_patterns.md) → 包内固定恢复规则与回归测试 → 验证后发布。外部页面、文章或一次成功调用都不能自动修改规则、skills 或来源优先级。测试失败就保留失败记录并修复；不靠“自进化”绕过验证。

本轮实测和打包结果见 [验收记录](platform_reliability_acceptance.md)。
