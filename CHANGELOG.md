# 变更记录

## 0.2.0rc2 — 2026-09-19，综合评审修复候选

整合四份本机评审、Windows PR #2（`891847e..3f918f7`）及其独立审核。维护同一套跨平台代码，不分叉 Windows/Mac 产品版本。逐项处理与未验边界见 [修复对照](docs/review_resolution.md)；Windows 接手步骤见 [复验交接](docs/windows_review_handoff.md)。测试通过不等于真实供应商、物理电脑或生产 SLA 已验收。

**可靠性与跨平台**
- 9 处连接取消复用 `_interrupt`：Windows 在取消时先 detach 使原对象失效，再关闭其拥有的句柄，覆盖 makefile 缓冲引用；POSIX 保持 shutdown。新增真实本机 TCP、HTTPResponse、TLS、PyMySQL 缓冲读及关闭后新连接测试。
- Windows 首字节锁直接锁定文件范围（可越过 EOF），去掉加锁前的竞争写入，不吞掉首次写入权限错误。
- 缓存 mtime 使用实际保存时间；相同时间以文件名稳定排序。不再人为推进时间，避免影响音频 TTL；不承诺相同时间写入的跨进程严格先后。
- 归档路径过长仅依据实际 `ENAMETOOLONG` / Windows 206 分类；磁盘满不再误报。旧 64 位十六进制图片 manifest 可复用。
- 三平台完整离线 CI、Windows UTF-8 开/关、Python 3.9/3.12 无 MCP 基础安装检查；测试崩溃无摘要也生成注解并保存日志。

**检索与诊断**
- 来源按权重分配剩余时间（内置微信 2，其他 1），未用时间顺延。局部超时保留已返回且通过校验的文本、扫描记录和续取游标；父请求取消/超时仍停止接受结果。
- 浏览器预算不足时保留微信发现结果并报告 `wechat_browser_budget_insufficient`，不因时间切分自动转收费正文。
- 单条文本契约/转换错误隔离为 `candidate_rejected`；伪造来源身份、未授权 generated 内容、重复记录、无效页/扫描/游标仍拒绝整页。
- 长中文部分匹配保留“有色、对冲、在建”等词内字符与首部主题区分；重合度仅是字面提示，不是主题或事实置信度。保留英文/代码边界。
- `timing` 为附加运行指标，包含它的运行指纹每次可能不同，不是内容指纹。
- `Diagnostic.datasets` 为附加的配置作用域；数值单位/财务币种错误只阻断相关数据集，共享认证配置错误仍阻断该来源，不自动换源。微信配置指出实际错误键；路径展开错误有稳定诊断。
- Tushare/JYDB 保留原始发布者类别，同时声明供应商转录、发布者未独立核验；排序不把这种类别当已取得官方原件。

**安全与兼容变化**
- MCP 校验错误只可能包含固定字段名，不反射 Python 异常正文；服务器输出根错误用 `invalid_output_root`，坏工具参数用 `invalid_request`。
- MCP `audit_dir/archive_dir` 仅限 `IR_SEARCH_OUTPUT_ROOT` 内，默认凭证目录下 `.local/exports`。SDK 仍允许显式本地目录。路径别名由本平台规范化，拒绝越界 symlink/junction 和异卷/远端 UNC 目标。
- 新集成可设服务进程环境 `IR_SEARCH_MCP_MODE=core`，只注册核心数据/素材及诊断工具；默认 `legacy` 保留所有旧入口，`deep_research` 不扩建。
- Wind/JYDB 共用的数据库分页游标改用安装级随机密钥，经跨进程锁和原子写入持久化。**旧密码签名游标失效**，重新查询；星球/微信/IMA/XHS 的 material cursor 不因此失效。状态不可写或密钥损坏返回 `cursor_state_unavailable`，不再静默使用进程内密钥。
- 旧公开 fetch 复用验证并固定 IP 的 public_web 传输，重定向共享预算，DNS 等待有截止/取消与并发上限。MCP 工具参数不能单独授权私网；仅可信服务配置 `IR_SEARCH_ALLOW_PRIVATE_NETWORK=1` 可开放旧私网读取。SDK 的显式私网选项保留。
- 修复合法 `abc.de/cafe.de` 被当数字地址拒绝的问题；真正的非标准数字地址仍拒绝。
- 旧模拟结果使用 `mock.invalid`、显眼 MOCK 标题及不可作证据标记，不再显示为真实官方披露。旧 Tushare 默认官方 HTTPS，其他代理需要显式配置；不输出上游异常正文。
- 离线测试固定 live 开关、隔离合成凭证，禁止外部 DNS/socket（本机回环测试允许）。个人公众号选集从当前版本移除，保留公共示例；没有改写 Git 历史。

本轮不新增数据源、不执行真实带凭证/收费调用。A 股复权/交易日历/PIT、统一证券代码、Windows ACL、跨进程额度账本、向量库和大规模接口重构均不冒充已完成。

## 0.2.0rc1 — 2026-09-18，交接候选

这是 GitHub 源码交接候选的版本标识，尚未声明稳定版或生产上线。

- **行为变化：** `search_materials.providers` 缺失或空列表不再自动选源；返回 `required_inputs`、可选来源与零调用状态。已有用户选择应由调用方继续显式传入。`get_data` 数值路由不变。
- 新核心入口、各独立 adapter、原文和引用、诊断/预算/续取与私有配置均包含在该工作区候选中；能力和已知失败以 [当前清单](docs/current_capabilities.md) 为准。
- 网页支持博查、Exa、兼容 AnySearch；明确额度耗尽时返回 Agent 原生 Web Search 交接，不伪装服务端已完成。
- 整理当前文档入口、跨电脑安装、Agent 验收题库、外部参考名录与交接文件清单。
- `deep_research` 继续兼容维护，暂停扩建；旧 `web_search` 名称含义未改变。

不复制 credentials.env、账号文件、Cookie、会话、私有技能清单或真实素材到发布/交接包。此前各日期的测试记录保留历史效力，不代表 0.2.0rc1 已在所有平台或全部供应商实测通过。
