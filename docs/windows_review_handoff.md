# 给 Windows Claude 的 rc2 复验交接

使用同一仓库、同一版本，不另建 Windows 专用实现。此分支包含 PR #2 的 Windows 修复和后续综合修复，`main` 在合并前仍可能是 rc1。不要把旧 PR 的通过数当成本分支证据。

## 取得修复分支

先确保你自己的修改已经保存；不执行 reset/clean 强行覆盖。进入仓库后：

```powershell
git fetch origin
git switch --track origin/codex/review-consolidation
git rev-parse HEAD
py -3.12 -m venv .venv-rc2
.\.venv-rc2\Scripts\python.exe -m pip install ".[dev,mcp,mysql]"
```

若本地已有同名分支，改用 `git switch codex/review-consolidation` 后 `git pull --ff-only`。新建环境避免旧 editable install 指向其他 checkout。

## 离线验收（新 PowerShell 窗口）

先读根目录 AGENTS.md、CHANGELOG、[修复对照](review_resolution.md)。不读取/打印/回传真实 credentials.env，不使用真实登录态，不执行带凭证或收费调用。以下测试自动生成私有合成配置，允许的网络只有测试自己的本机回环。

```powershell
$env:IR_SEARCH_LIVE = "0"
$env:IR_SEARCH_RUN_LIVE_TESTS = "0"
$env:IR_SEARCH_CREDENTIALS_FILE = "$env:TEMP\ir-search-offline-unused.env"
$env:PYTHONUTF8 = "1"
.\.venv-rc2\Scripts\python.exe -m pytest -m "not live" -q -rs
$env:PYTHONUTF8 = "0"
.\.venv-rc2\Scripts\python.exe -m pytest -m "not live" -q -rs
.\.venv-rc2\Scripts\python.exe -m pytest tests/test_standalone_package.py -q
```

不要创建上面的占位 env 来复制真实 Key；每个测试有独立文件。基础无 MCP 组合由 CI 的 base-only job 验证；可另建空环境仅安装 `.[dev]` 后运行 standalone 测试。

重点独立复核，而非只检查绿灯：

1. `test_buffered_socket_interrupt.py` 的三个真实缓冲读取路径全部运行，无依赖跳过；确认取消后原对象失效、二次关闭不伤及新 socket。五次重复不是长期压力证明。
2. `test_review_regressions.py` 中首次双进程密钥、微信部分页与游标、敏感输入不回显、路径异常、配置键、磁盘满诊断；修改代码前先复现。
3. `test_consolidated_review.py`：真实 Windows 大小写路径、8.3（文件系统启用时）、junction 越界拒绝、远端 UNC 不解析；共享配置与数据集配置隔离。
4. `test_windows_lock_contention.py` 与分页并发用例必须原生运行。EOF 范围锁不写初始化字节；永久权限故障不能作为成功缓存。
5. 继续核对 POSIX 条件化只跳过平台不能实现的断言；不要删测试或削弱来源/权限检查。

## 实际 Agent 与 Skill 复验

离线通过后，另开正常服务窗口，选择本机凭证路径；不要把 Mac 的缓存/证书/会话路径照搬到 Windows。新 MCP 配置在服务环境设置 `IR_SEARCH_MCP_MODE=core`，保留 `IR_SEARCH_CREDENTIALS_FILE` 的本机绝对路径。工具应为 get_data、search_materials、retrieve 及能力/诊断/公告辅助工具；要使用旧工具显式选 legacy。

在 Agent 中先验证缺失 providers 时零调用、已选来源预览、输出根内相对目录、错误与部分成功处理。真实样本由用户选择并授权后按 [Agent 题库](agent_acceptance.md) 执行：显式证券代码（包括未收录公司）、行情/财报、素材摘要与正文引用、失效来源、额度交接。SDK 能调用不等于真实 Skill 的研究效果已通过。

报告记录提交号、Windows 版本、Python/编码、安装 extras、两轮数量、每个跳过理由、实际 MCP 宿主与测试题。只回传脱敏问题和复现；不回传凭证、Cookie、私有清单或授权原文。不要自动合并 PR 或改写历史。
