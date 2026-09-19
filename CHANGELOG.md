# 变更记录

## 未发布 —— 0.2.0rc1 之后的本地修复（2026-09-19）

依据对 `891847e` 的独立评审，在 Windows 11（中文系统，Python 3.12）上实测修复。契约只增不改：没有删除或改名任何已有字段。

**跨平台**
- 完整离线测试在 Windows 上由 40 个失败降为 0；CI 改为三个操作系统都跑全量，并新增一个不启用 UTF-8 模式的 Windows 任务（五个任务均已通过）。依赖 POSIX 权限位、symlink 特权或 zsh 的用例带明确理由跳过。CI 失败时把失败用例写入公开注解，无需登录即可查看。
- 取消或超时现在能在 Windows 上立即中断阻塞中的读取（此前 `shutdown` 唤不醒阻塞读，MySQL 用例要等 120 秒）；十处重复的监视线程代码合并为 `infrastructure/_interrupt.py`。
- 修复 Windows 文件锁的竞态：多个线程同时首次创建锁文件时，后到者会在**没有拿到锁**的情况下继续执行，可能造成重复的付费正文调用。
- 缓存淘汰不再依赖文件系统时间戳粒度：每次写入的修改时间严格递增，排序以文件名作稳定次序。
- 旧 Cursor 工作区 bootstrap 明确为仅支持 macOS/Linux，在 Windows 上给出说明后退出；修复其把路径未转义写入 JSON 的问题（与平台无关）。

**配置与诊断**
- 配置错误带上要修改的键名（`key`，从不含值）；`ir-search-doctor` 保留具体原因 `detail_code`，不再只给笼统的 `source_config_error`。
- 另一种操作系统的绝对路径报 `path_not_absolute_on_this_platform`；`*_SSL_CA` 指向的文件不在本机时体检直接报 `ssl_ca_file_missing`。
- 全新电脑上：`search_materials` 返回 `no_material_source_enabled`，`get_data` 返回 `no_data_source_enabled`，说明凭证文件是否找到及下一步。
- 体检报告新增 `private_file_protection`，如实说明本平台是否校验了属主与权限位（目前仅 POSIX）。

**素材检索**
- 主题词推断不再把整段中文当成一个词；泛词不再作为主题词；英文/数字词按完整词匹配；长中文词支持按二元组重合度的部分匹配（`partial_topic_term_match`）。新增 `plan.topic_term_basis`。
- 每个来源独立的时间份额（`source_time_share_exceeded`）；单条坏记录只丢弃该条（`candidate_rejected`）；新增 `timing` 字段（与 `items`/`coverage` 分开，保持后者可比）；适配器诊断的 `message` 不再被丢弃。

**MCP**
- 区分 `invalid_request`（参数被拒，`detail` 指出字段与取值范围）与 `internal_error`（参数有效、服务或来源失败）。此前服务端内部异常会被报成“请检查参数”。
- `audit_dir` / `archive_dir` 限制在一个本机输出根目录内（`IR_SEARCH_OUTPUT_ROOT`，默认凭证目录下的 `.local/exports`）。**行为变化：** 经 MCP 传入根目录之外的绝对路径会被拒绝；Python SDK 不受影响。

**安全**
- 分页游标改用安装级随机密钥签名（存于凭证目录下 `.local/state/cursor.key`），不再使用数据库密码。**行为变化：** 旧游标失效，需重新发起查询。
- 旧抓取入口的 URL 校验补上：十进制/十六进制/短点分形式的地址、单标签与内网后缀主机名、非默认端口，以及“公网域名解析到内网地址”的情况。

**其他**
- 新增 `ir_search.__version__`（取自安装元数据，单一来源）。

尚未处理（需要真实数据库或更大改动，见评审报告）：A 股复权与交易状态、A 股交易日历、统一证券代码规范化、返回体总量预算、适配器插件化、Windows ACL 校验。

## 0.2.0rc1 — 2026-09-18，交接候选

这是 GitHub 源码交接候选的版本标识，尚未声明稳定版或生产上线。

- **行为变化：** `search_materials.providers` 缺失或空列表不再自动选源；返回 `required_inputs`、可选来源与零调用状态。已有用户选择应由调用方继续显式传入。`get_data` 数值路由不变。
- 新核心入口、各独立 adapter、原文和引用、诊断/预算/续取与私有配置均包含在该工作区候选中；能力和已知失败以 [当前清单](docs/current_capabilities.md) 为准。
- 网页支持博查、Exa、兼容 AnySearch；明确额度耗尽时返回 Agent 原生 Web Search 交接，不伪装服务端已完成。
- 整理当前文档入口、跨电脑安装、Agent 验收题库、外部参考名录与交接文件清单。
- `deep_research` 继续兼容维护，暂停扩建；旧 `web_search` 名称含义未改变。

不复制 credentials.env、账号文件、Cookie、会话、私有技能清单或真实素材到发布/交接包。此前各日期的测试记录保留历史效力，不代表 0.2.0rc1 已在所有平台或全部供应商实测通过。
