# Wind / JYDB 首批接入与集中凭证

实现日期：2026-09-13。首批选择 Wind MySQL 证券信息、日行情，以及 JYDB 公告文本；随后新增 JYDB A 股日线和默认 Wind → JYDB 缺失回退。近期日内另接 AKShare，细节与预留范围见[市场数据来源规则](market_data_source_policy.md)。Tushare、CNINFO 保留，暂不优先重写。

## 凭证文件

源码 checkout 默认使用仓库根目录的 **`credentials.env`**。每台电脑独立创建和填写这份私有配置；安装包或其他工作目录调用时，通过 `IR_SEARCH_CREDENTIALS_FILE` 指定本机文件。模板中的账号、密码、Key 均为空，数据库来源默认关闭；不会自动读取桌面 skill 配置或系统钥匙串。

2026-09-14 按用户授权，将本机已有 Wind 凭证及用户补填的 JYDB 凭证集中到私有 env，文件权限为 0600；这只是本机迁移，不是运行时依赖桌面配置。本机 AKShare 股票后端选为 `sina`，可查询已核对的 OHLC 字段。公开模板仍无凭证、默认 `eastmoney`。AKShare 额外安装 `.[akshare]`；与数据库和 MCP 一起使用可安装 `.[mysql,mcp,akshare]`。

2026-09-15 更新：本机 Wind 已按用户选择设置 `WIND_MYSQL_TLS_MODE=disabled`，正式 SDK/MCP 直接实取，并带 `non_tls_explicitly_configured`；JYDB 保持已核验的 TLS。财务三表、期货/期权日线、合约目录和日历已注册为公共能力，详见[最新交付与验收](domestic_data_delivery.md)。

衍生品成交额使用单独的 `WIND_MYSQL_DERIVATIVES_AMOUNT_MULTIPLIER`，本机已核验为 10000，不复用 A 股参数。完整期货属性请求因 Wind 字段能力缺失而整次回退 JYDB。深市期权持仓冲突和 JYDB 财务币种缺失均明确返回 partial。

- 本地填写 `HOST / PORT / DATABASE / USER / PASSWORD`，完成后将对应 `ENABLED` 改为 `true`。
- 文件权限为 `600`，仅当前用户可读写；已由 Git 的 `*.env` 规则排除。
- 可提交的空白模板是 [credentials.env.example](../credentials.env.example)，不要把实际凭证复制到模板、文档或对话。
- 读取器要求普通文件和安全权限，拒绝符号链接。采用字面值 `KEY=value` 格式；单双引号可以包裹值。`$`、反引号、`#` 等字符保留为值，不执行命令、不展开变量。注释放在独立行；不支持多行值或行尾注释。
- 新 adapter 直接读取配置并注入自己的客户端，不修改全局环境、不跨来源借用密码；不会从旧 skill 配置或钥匙串静默回退。

从其他项目或安装包调用时，设置 **`IR_SEARCH_CREDENTIALS_FILE` 为本机私有文件的绝对路径**。这项变量只保存文件路径，可以放在 MCP 启动配置中。源代码运行默认使用仓库根目录的 credentials.env；安装包未指定路径时使用当前工作目录的同名文件。每次新请求重新读取文件，修改凭证后无需写回代码。

文件中的其他 API Key 名称目前是后续迁移预留项。旧搜索 adapters 仍沿用原来的环境变量入口，尚未自动切换到新文件；后续更新相应来源时再接入。

## 安装与连接

数据库驱动为可选依赖，安装方式：

```bash
python3 -m pip install -e '.[mysql]'
```

需要同时运行 MCP 时安装 `.[mysql,mcp]`，并使用 MCP 支持的 Python 版本。缺少驱动时返回 `dependency_missing`，不会导致整个 MCP 服务启动失败。

连接仅在业务读取时建立；Wind 和 JYDB 在同一进程内串行连接，每次查询后关闭，不保留连接池。SDK/MCP 不开放任意 SQL。所有业务查询采用固定单表 SELECT、参数绑定、行数限制和只读事务。跨进程同时运行多个客户端时，仍需遵守账户的连接数上限。

公开模板 TLS 默认校验证书和主机名；SSL_CA 可指定 CA。明确设置 Wind TLS_MODE=disabled 才使用非 TLS，并仅允许已核验的 mysql_native_password 认证插件。TLS 模式遇到不支持加密的服务器时仍在认证前拒绝，不自动降级。JYDB 不接受 disabled。

本地 JYDB skill 提供的私有 CA 模式没有 DNS SAN；如连接的确使用该模式，可设置 `JYDB_MYSQL_TLS_MODE=pinned_ca`，同时填入对应 `SSL_CA` 文件路径和经核实的 `SSL_CA_SHA256`。它校验固定 CA 指纹和证书链；不适用于未经核实的其他服务。未提供正确证书/指纹会明确失败，不尝试关闭证书校验。

配置检查不连接数据库，不显示账号、密码或服务器地址：

```python
from ir_search import source_configuration_status
status = source_configuration_status()
```

MCP 的 `source_health` 也包含 `configured_sources`。`configured=true` 只表示配置齐全，不表示登录成功、表权限有效或数据覆盖完整。

## Wind MySQL

| 数据集 | 当前支持 | 限制 |
|---|---|---|
| `securities` | A股证券代码、名称、交易所、币种、市场；股票池或分页读取目录 | 使用 asharedescription，不过滤退市证券；不是历史证券主表快照 |
| `prices_daily` | 指定 A股股票池与日期区间，未复权 OHLC、成交量、成交额、币种 | 使用 ashareeodprices；暂不提供复权、港美股和历史可得时点保证 |
| 财务、衍生品、合约与日历 | 由独立的 FinancialStatementsAdapter / DomesticDerivativesAdapter 实现 | 字段与口径见[来源规则](market_data_source_policy.md) |

字段来自本地 ashare-data 字典和客户端。成交量、成交额的实际数据库单位需另行核对，因此没有默认猜测转换倍数：

- `VOLUME_MULTIPLIER`：原始值转换为“股”的倍数。例如确认为手时填 100，确认为股时填 1。
- `AMOUNT_MULTIPLIER`：原始值转换为该行币种基本单位的倍数。例如确认为千元时填 1000，确认为元时填 1。
- 未确认这两项时，仍可只请求价格字段；请求相应数量字段会返回 `units_not_configured`。

```python
from ir_search import DataRequest, get_data

prices = get_data(DataRequest(
    dataset="prices_daily",
    provider="wind_mysql",
    symbols=["000001.SZ"],
    start="2026-09-01",
    end="2026-09-03",
    fields=["close"],
)).to_dict()
```

每页通过“证券代码＋交易日期”继续读取；游标绑定来源账户和原始请求，参数变化或游标被修改会拒绝。更换凭证后需要重新开始分页。不同日期保留为不同记录，数值缺失不填零。分页跨越数据库更新时不保证同一历史快照，因此不声称 point-in-time 安全，也不默认计算跨除权日收益。

`complete` 仅描述本次数据库查询的分页完整性，不证明数据库历史覆盖无缺口。字典存在不代表远端实例一定具备同名表/字段；不同步的数据库版本会返回 `upstream_schema`，留待按实际字典适配。

## JYDB A 股日线

`JYDBMarketAdapter` 使用本地聚源字典中的 `SecuMain`、`QT_DailyQuote` 和 `LC_STIBDailyQuote`。先查询股票的 `InnerCode` 和 `ListedSector`，科创板（7）使用独立日线表，其他支持的 A 股板块使用普通日线表。仅接入 `SecuCategory=1`，不将存托凭证、期权或基金混入普通 A 股。

每次 SQL 仍只查询一张表，最多分别读取两个有界行情页，再按证券内部编码和交易日排序、分页。游标绑定来源及原请求。上市板块不明、证券映射冲突或行情重复时明确失败；缺少部分股票映射则返回部分结果及诊断。

字典已明确成交量为股、成交额和价格为元，按人民币返回；不套用 Wind 的单位倍数。未复权、非 PIT，科创板盘后固定价格交易汇总表尚未纳入。`complete` 描述查询页完整性，不证明整段交易日覆盖。

## JYDB 公告

新增 `search_announcements`，Python SDK 和 MCP 使用同一实现。先按证券代码、市场从 SecuMain 取得公司编码，再单独查询 LC_Announcement。每个 SQL 只读一张表；无法映射某个请求证券时明确失败，不静默遗漏它。

```python
from ir_search import AnnouncementRequest, MaterialRequest, search_announcements, retrieve

announcements = search_announcements(AnnouncementRequest(
    symbols=["000001.SZ"],
    start="2026-01-01",
    end="2026-09-03",
    query="半年度",   # 可选：标题中的字面短语
    limit=10,
))
references = [item["source_ref"] for item in announcements["items"]]
materials = retrieve(MaterialRequest(question="收入和现金流", urls=references)).to_dict() if references else None
```

- 首轮覆盖 **LC_Announcement 公司公告文本表**。不声称已覆盖全部临时公告、所有市场、附件或原始 PDF；LC_NotTextAnnouncement、LC_InterimBulletin 等留待下一批接入。
- 支持最多 20 个 A股证券、明确日期区间、标题短语、每页最多 200 条。游标按发布时间和记录 ID 排序，绑定原始请求。每次 `retrieve` 最多读取 10 个资料编号。
- 数据库查询逐次计入操作预算。retrieve 默认允许 30 次操作，覆盖 10 份公告各自的服务操作、正文查询和发布者查询；显式传入更小的 RequestContext 预算时，以调用方预算为准。
- `jydb://announcement/<id>` 是本地 provider 记录引用，供 `retrieve` 调用，不是官网网页链接，也不包含服务器地址或认证信息。
- 正文有字符上限；截断、发布者未知、时间精度未经验证都会保留诊断。发布时间按聚源数据库的上海时区解释，不把午夜值当作已验证的精确公告时刻。
- 文本来源标为公司公告、获取渠道为 JYDB，authority 为 `data_vendor`。当前读取的是供应商文本，不冒充已核对的官网原件；引用位置是该文本版本的字符位置，未提供原始 PDF 页码。
- 仅做确定性标题筛选、正文读取与证据抽取，不调用外部 AI 问答。

## 验收状态与后续

本轮已实现 env 读取、只读 SQL 传输、Wind 数据页、JYDB 公告检索/正文、SDK/MCP 与异常诊断。离线测试使用明确的测试替身，并通过临时空 env 隔离用户真实凭证。

安装包已在仓库目录之外验证 SDK/MCP 调用，11 个工具可以注册；可选依赖测试在临时环境使用 PyMySQL 1.2.0、MCP 1.30.0，未修改用户现有 Python 环境。私有 env 文件不包含在 wheel 中。

本机凭证已完成配置与真实样本验收。现有数据接口可以调用，但不代表全库单位、所有品种或历史修订均通过；最新实取、稳定性和未决项见[交付报告](domestic_data_delivery.md)。下一阶段再扩展海外、基金、宏观与素材检索。
