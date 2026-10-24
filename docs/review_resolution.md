# 综合评审修复对照（0.2.0rc2）

基线：`891847e`；纳入 Windows PR #2 的 10 个提交至 `3f918f7`，在 `codex/review-consolidation` 继续修复。输入为四份本机评审的已复核问题、Windows 评审与 PR #2 的独立反例。重复意见合并，不把模型数量当证据。

本轮没有读取真实 credentials.env，没有真实供应商、登录或付费调用。测试只用合成配置、临时目录与本机回环服务器；没有合并原 PR 或改写历史。源码与安装包不依赖 Desktop/skills。

## 前四份评审

| 已核实问题/建议 | 本轮处理 | 证据与边界 |
|---|---|---|
| 旧 mock 像真实官方披露 | MOCK 标题、保留域名 `mock.invalid`、未知证据类型、最低 tier、不可作证据标记；核心 MCP 模式 | `test_consolidated_review.py`；旧入口保留，不扩建 deep_research |
| 旧公开读取 DNS/IP 安全不足、MCP 可自授内网权限 | 复用固定公网 IP 的传输；重定向共享预算；DNS 等待可停止且并发受限；私网须可信服务配置 | 同上及 `test_public_web_transport.py`/URL 测试；已启动的系统 DNS 工作线程不能强杀，最多 8 个，完成后释放 |
| 旧 Tushare 隐式第三方代理 | 官方 HTTPS 默认；其他 HTTPS 代理必须显式配置；拒绝含凭证的 URL | `test_tushare.py`、综合回归；不代表现行语料 MCP 曾泄漏 |
| 无 MCP 基础 wheel 探针缺失导入 | 修正无条件使用的导入；CI 新增 Python 3.9/3.12 基础安装组合 | `test_standalone_package.py`，无 checkout、无 skills、合成 env |
| 离线测试继承 live 开关 | 每条非 live 测试隔离 env、关闭旧 live、阻止外部 DNS/socket；旧在线用例补 marker | `tests/conftest.py`；本机回环仅用于传输测试 |
| 原始发布者与传播渠道混淆 | 转录、发布者未独立核验、原件未核验字段；未知发布者不赋官方 tier；未核验转录的 tier 排序贡献有上限 | Tushare/JYDB 与素材服务；不把转录内容一律判作媒体，仍需原件核对 |
| 单个数据集配置错误阻断整个 Wind | `Diagnostic.datasets`；按数据集验证单位/币种并注册；单个构造器错误隔离 | 综合回归；共享凭证/TLS 配置错误仍阻断，不能悄悄切换 JYDB |
| 公共接口字段越来越多 | 本轮保留兼容字段，文档明确数据/素材的职责与边界 | 有类型的 provider options、reader 注册属于演进建议，不为修缺陷进行大拆分 |
| 未收录公司与 skill 端到端验收 | 显式代码不依赖四家公司词典的回归；已安装 SDK/MCP 探针和 Windows 实际 Agent 交接题 | 离线不证明真实取数、召回质量或每家供应商权限；物理电脑 Agent 验收待接手者执行 |
| 个人公众号选集跟踪在 Git | 当前文件替换为中性公共示例 | 不复制个人选集，不改写已公开历史；本机现有私有配置不动 |

未采纳的意见：跳过所有权限检查；把无 LLM/测试数量当事实正确或 PIT 证明；把供应商摘要说成原文；合并职责不同的数值/素材注册表；为此添加向量库、报告合成、自动收费重试。原有网页 token 链接过滤、手工微信 fallback 标记已存在，本轮未宣称它们原先缺失。

## PR #2 独立审核 12 项

| 编号 | 修复 | 回归 |
|---|---|---|
| 1 Windows 缓冲 socket 未真正关闭 | Windows detach 转移并失效原对象后关闭句柄；POSIX shutdown 保留 | `test_buffered_socket_interrupt.py`：真实 TCP/HTTPResponse/TLS/PyMySQL，重复取消和后续 socket；`test_review_regressions.py` 强制分支不是原生 Windows 证明 |
| 2 分页密钥首次并发竞态 | 读写均用进程锁，原子发布完整密钥；损坏/不可写显式错误，无随机进程回退 | 双进程首次创建、跨进程相同签名、损坏不自动覆盖、旧游标拒绝 |
| 3 时间份额丢素材和游标 | 父请求有效时先验收返回的部分页，再标记份额超时；微信权重与免费浏览器预算边界 | 真实微信 adapter 加离线 reader；next_material_request 保留续取；禁止预算不足直接付费 |
| 4 MCP 回显敏感值 | 仅固定提示及白名单字段名，无异常正文 | 合成 token 分别放枚举、日期、类型等输入；不回显 |
| 5 合法域名被误拦 | 只识别实际数字/0x 形式 | `abc.de/cafe.de` 正例、私网/数字域名反例 |
| 6 路径异常绕过结构化返回 | 调用方路径错误归 invalid_request；服务根错误归 invalid_output_root；异卷 UNC 在解析前拒绝 | tilde、symlink、Windows 大小写/短路径/junction/UNC 分支 |
| 7 记录隔离承诺过宽 | 文本契约/转换逐条隔离，保留健康记录；来源/页不变量仍整页拒绝 | mutated text=None、转换失败、伪造来源、未授权 generated 正文等 |
| 8 微信配置指出错误键 | 列表、Key、数字预算分开校验 | 三组参数化反例均指出真实键名 |
| 9 mtime 被推到未来 | 按真实保存时间写入；同时间稳定排序，不保证严格先后 | 跨进程先后、突发写入没有未来 TTL、utime 失败不推翻成功写入 |
| 10 词内虚词被剥除 | 保留有色/对冲/在建，要求首部主题区分匹配；局部连词规则仍明确有词面局限 | 黑色/有色、公募/对冲负例，光模块/港股、AI/800G/H20 等正反例 |
| 11 路径过长误诊磁盘满 | 依实际系统错误码分类 | ENOSPC 注入、Windows 路径、旧 64 位图片 manifest |
| 12 CI 崩溃前无摘要 | 固定失败注解兜底，上传日志 | 正常摘要转义、无摘要崩溃日志重放 |

文件锁不再需要加锁前写首字节：Windows 允许锁定 EOF 之外的范围，省去产生竞态和权限歧义的步骤。测试保留 POSIX 权限/属主/symlink 断言，平台不支持的检查按理由跳过。

## 验证记录

本机 macOS 26.5.2、Python 3.12.13。完整计数以本轮最终提交记录及 GitHub Actions 为准；曾跑过的中间提交不替代最终提交验证。

- 完整离线回归：2217 passed / 0 failed / 4 skipped / 3 deselected（19.77 秒）；10 条第三方弃用警告。此前中间轮为 2212 passed / 3 skipped / 3 deselected。
- 4 个跳过均为 Windows 原生路径/锁/MAX_PATH/bootstrap；3 个 live 用例通过标记排除，没有因失败而跳过。
- 全新无 MCP 基础环境安装 `.[dev]`，独立 wheel 验证 3 passed（1.46 秒）。
- 新测试直接在 Mac 本机执行了 HTTP、TLS 和 PyMySQL 缓冲取消；只有本机回环流量。
- 安装探针构建 wheel，检查无私密文件，删除构建源后在独立目录测试 SDK、MCP、规则和来源注册。
- Windows UTF-8 与默认编码、Linux、基础安装结果以当前提交的 [Actions](https://github.com/Chener6280/ir-search/actions/workflows/standalone.yml) 为准，不沿用 PR #2 的通过数。

未宣称验证：真实 Windows 11 中文系统的 Agent 宿主/浏览器/供应商登录，网络盘锁，真实 DNS rebinding 攻击，长期并发压力，实际数据口径/额度、全市场覆盖。Windows 路径与缓冲读取会在 CI 原生任务执行，Claude 应在物理电脑复测并补交接报告。

首轮原生 Windows CI 发现 `~其他用户名` 会被 Python 拼成未经确认的目录，现明确拒绝 Windows 命名用户简写；同时修复新增旧 manifest 回归测试遗漏 UTF-8 的问题。没有跳过失败用例。新 buffered HTTP/TLS/PyMySQL 取消、文件锁与其他路径检查在首轮已执行通过，最终全套结果仍须看最新提交。
