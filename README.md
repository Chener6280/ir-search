# ir-search

面向投研 skills 和 agents 的确定性数据、素材搜索与证据服务。当前版本为 **0.2.0rc2**，是供其他电脑及 Agent 测试的候选版本，尚未完成长期稳定性验证。

## 从这里开始

- 接手测试：[HANDOFF.md](HANDOFF.md)
- 综合评审修复：[处理对照](docs/review_resolution.md)与 [Windows 复验](docs/windows_review_handoff.md)
- 全部当前文档：[文档索引](docs/README.md)
- 安装、凭证和 MCP：[跨电脑部署](docs/standalone_deployment.md)
- 来源与已知限制：[当前能力清单](docs/current_capabilities.md)
- 外部项目、技能与依赖：[外部参考清单](docs/external_references.md)
- 新核心业务验收：[Agent 测试流程](docs/agent_acceptance.md)

## 三个核心入口

| 入口 | 用途 |
|---|---|
| `get_data` | 行情、财务、基金、宏观、期货与期权数据；返回单位、口径和来源诊断 |
| `search_materials` | 在用户选择的来源中检索素材，返回文本层级、版本、引用、覆盖与缺口 |
| `retrieve` | 读取指定原文或内部引用，提供实际文本、定位引用和读取诊断 |

`list_capabilities`、`describe_dataset`、`source_health` 用于了解能力和配置，配置通过不代表真实取数通过。研究计划、结论与对证据的判断属于调用方。

## 来源由用户选择

`search_materials.providers` 必须显式传入用户已选的来源，如 `['web', 'tushare_corpus']`。**省略或传空列表时不调用任何来源**，返回 `required_inputs=['providers', ...]` 和 `plan.source_options`；Agent 展示选择，或沿用本次任务中用户已有的选择。来源显示顺序不表示推荐优先级，不再自动选择名称排序前四家。

启用配置只代表来源可以被调用，不代表用户已选用。`max_sources` 仍是预算上限，超出上限的已选来源会明确标记未执行。具体见[选择契约](docs/source_selection.md)。

此变化只作用于素材搜索。既有数值策略仍是国内 EOD/财务 Wind → 覆盖缺失时 JYDB，近期日内 AKShare，海外已接入数据 FMP；`get_data(provider=...)` 可显式锁定来源。Fiona 为可显式选择的衍生品来源。

选择 `web` 后，搜索服务由该电脑配置：地区路由支持国内博查、海外 Exa/AnySearch。明确额度耗尽返回待 Agent 原生 Web Search 接续的请求，保留原失败及预算；[回退说明](docs/web_search_fallback.md)。

## 最小安装

从 [GitHub 仓库](https://github.com/Chener6280/ir-search) 获取当前源码。建议统一使用 Python 3.12；测试报告记录实际提交号。

```sh
git clone https://github.com/Chener6280/ir-search.git
cd ir-search
python3 -m venv .venv
.venv/bin/python -m pip install '.[mcp]'
```

复制 `credentials.env.example` 到本机私有位置，只启用用户选用的来源。通过 `IR_SEARCH_CREDENTIALS_FILE` 指定绝对路径；每台电脑自行填写密钥、账号和登录态。不要把凭证放入聊天、报告或 Git。

```python
from ir_search import MaterialSearchRequest, search_materials

# 零来源调用：让用户选择；选项只反映当前配置，不是可用性认证。
choices = search_materials(MaterialSearchRequest('公司研究')).to_dict()

# 用户已选择 web 后，先检查计划；去掉 dry_run 才执行有界真实查询。
preview = search_materials(MaterialSearchRequest(
    'NVIDIA revenue', providers=['web'], keywords=['revenue'],
    web_region='overseas', published_start='2025-08-01', published_end='2025-08-31',
    candidates_per_source=3, text_reads_per_source=1, dry_run=True,
)).to_dict()
```

## 独立运行与验收

运行实现、字段映射和 CSV/YAML 资源位于安装包内，不导入 Desktop/BrokerSkills/skills、开发者私有路径或 checkout-only 工具。数据库、浏览器、字幕及音频依赖按需安装，见[部署指南](docs/standalone_deployment.md)。

```sh
python3 -m pytest
python3 -m pytest tests/test_standalone_package.py -q
```

独立安装测试构建 wheel，在无源码路径的目录验证 SDK/MCP 及资源加载。它不是另一台物理电脑、远端 CI 或所有供应商的在线验收。真实调用须按[Agent 测试流程](docs/agent_acceptance.md)记录，并区分通过、部分通过、失败和未测。

## 兼容与历史

旧 `search` / `fetch_document` / `deep_research` 入口保留，`deep_research` 暂停扩建。旧搜索可使用 mock，不可当真实证据；新数值及素材入口不会将 mock 当作线上来源。历史操作示例见[归档 README](docs/legacy_readme.md)。

原有 Cursor research workspace template 与 bootstrap 工具继续保留，见 [docs/cursor_research_workspace_setup.md](docs/cursor_research_workspace_setup.md)。旧研究模板和旧验收题库包含兼容流程，新测试应按 HANDOFF 使用三个核心入口。
