# 独立安装与跨电脑测试

适用：0.2.0rc1 交接候选。源码入口是 [GitHub 仓库](https://github.com/Chener6280/ir-search) 的 `main` 分支；记录实际提交号，以便复现测试。先读 [HANDOFF](../HANDOFF.md)。运行不需要 Desktop、BrokerSkills 或原电脑的私有目录。

## 1. 创建独立环境

建议统一 Python 3.12。包的基础 SDK 声明支持 3.9+，MCP 及部分可选依赖要求更高；不要用基础 SDK 的最低版本推断所有 extras 都能安装。

先取得源码（所有系统相同）：

```sh
git clone https://github.com/Chener6280/ir-search.git
cd ir-search
git rev-parse HEAD
```

macOS/Linux，从源码根目录运行：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip setuptools wheel
.venv/bin/python -m pip install '.[dev,mcp]'
```

Windows PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
.\.venv\Scripts\python.exe -m pip install '.[dev,mcp]'
```

也可安装测试包内 wheel：`python -m pip install 'dist/ir_search-0.2.0rc1-py3-none-any.whl[mcp]'`。离线源码测试仍需保留源码中的 tests/templates/configs/scripts；已安装 SDK/MCP 运行不依赖这些 checkout 文件。

## 2. 按来源补充依赖

| 使用范围 | 源码安装 extra / 外部条件 |
|---|---|
| Wind/JYDB 股票、财务、基金 | `.[mysql]`；数据库网络与账户权限 |
| AKShare 日内 | `.[akshare]`；上游网络可达 |
| 普通网页、FMP、官方 HTTP/MCP 来源 | 通常基础包即可；作为 Agent MCP 服务另装 `.[mcp]` |
| PDF / 增强提取 | `.[extract]` |
| 动态网页 | `.[crawl,extract]`，再执行当前环境的 `python -m playwright install chromium` |
| Alpha派、岗底斯登录助手、实验性雪球浏览器 | `.[browser]`，安装匹配浏览器；新设备/平台验证按官方流程完成 |
| Scrapling | `.[scrapling]`；只使用解析器，不安装 stealth/AI fetcher |
| YouTube 平台字幕 | `.[video]`；字幕/语言/网络条件单独验收 |
| 小宇宙显式 ASR | `.[audio]`、当前机器 PATH 中的 FFmpeg、火山 Agent Plan 语音权限 |
| 小红书 | 独立 Xiaohongshu MCP 后端、本人登录、loopback 地址与私有 token；见[配置](xhs_material_adapter.md) |

不必一次装全部 extras。Python 包存在不代表浏览器二进制、FFmpeg、登录态或远端数据可用。依赖声明以 `pyproject.toml` 为准；候选包未提供跨系统统一依赖锁，测试报告应记录实际版本。

旧 Cursor 工作区模板的启动脚本还依赖 `/bin/zsh`；macOS 通常已带，Linux 全套兼容测试前需安装 `zsh`（例如 `sudo apt-get install zsh`）。新版安装包的 SDK 和直接 Python MCP 启动不使用这套旧脚本，Windows 按下文的直接启动方式配置。

## 3. 配置本机凭证

复制 `credentials.env.example` 到本机私有目录，填写需要的字段，只开启本轮用户选用来源的 `ENABLED` 开关。模板不会包含其他人的密钥或会话。

macOS/Linux（先确认目标文件不存在，避免覆盖已有凭证）：

```sh
mkdir -p "$HOME/.config/ir-search"
chmod 700 "$HOME/.config/ir-search"
cp -n credentials.env.example "$HOME/.config/ir-search/credentials.env"
chmod 600 "$HOME/.config/ir-search/credentials.env"
export IR_SEARCH_CREDENTIALS_FILE="$HOME/.config/ir-search/credentials.env"
```

Windows 把凭证放在**当前用户目录**下（该目录默认只对本人、SYSTEM 和管理员开放），例如 PowerShell：

```powershell
New-Item -ItemType Directory -Force "$env:LOCALAPPDATA\ir-search" | Out-Null
Copy-Item credentials.env.example "$env:LOCALAPPDATA\ir-search\credentials.env"
$env:IR_SEARCH_CREDENTIALS_FILE = "$env:LOCALAPPDATA\ir-search\credentials.env"
```

不要放在盘符根目录（如 `C:/PRIVATE`）或共享目录：那里新建的目录会继承对普通用户开放的访问权限。属主与权限位检查只在 macOS/Linux 上执行，`ir-search-doctor` 的 `private_file_protection` 会如实标出本平台是否做了这项检查。不要直接执行/source env 内容，库按字面解析，不执行其中命令。

### 路径类配置不可跨电脑照搬

密钥可以原样带到另一台电脑，**路径不行**。`*_CACHE_DIR`、`*_STATE_DIR`、`*_BROWSER_EXECUTABLE`、`*_SSL_CA`、`WECHAT_ACCOUNTS_FILE` 这类键：

- 可选缓存/状态路径留空采用各 adapter 的默认值，多数位于凭证目录旁 `.local/...`，AlphaPai 使用用户缓存目录；微信账号列表不能省略，TLS CA 是否可省略取决于模式。`~/...` 可表示当前用户目录；不要复制另一台电脑的绝对路径；
- 写了另一种操作系统的绝对路径（Windows 上的 `/Users/...`，或 macOS/Linux 上的 `C:\...`）时，来源会报 `path_not_absolute_on_this_platform`，并在 `key` 字段指出是哪个键；
- 配置了 `*_SSL_CA` 但证书文件不在本机时，体检直接报 `ssl_ca_file_missing`，不必等到第一次查询才以 `tls_error` 失败。

`ir-search-doctor` 对配置错误给出 `detail_code`（具体原因），能确定修复目标时附带 `key`（键名，从不含值）；`code` 仍为 `source_config_error`，保持对已有调用方兼容。

每台电脑分别维护 Cookie、账号池、星球范围、IMA 权限、本地 XHS 后端和登录会话。辅助路径按对应指南配置，不把原电脑的绝对路径当作可移植配置。本机已有 Wind 非 TLS 设置是用户明确选择；不自动从 TLS 失败降级。

新入口读取集中 env。`IR_SEARCH_LIVE` 只影响旧搜索管线，不是新 adapter 总开关；新来源通过各自配置启用，且 `search_materials` 还需用户明确选择 `providers`。

## 4. 本地零网络检查

在实际运行 MCP 的解释器中执行：

```sh
.venv/bin/python -m ir_search.services.source_diagnostics
.venv/bin/python -m pytest
.venv/bin/python -m pytest tests/test_standalone_package.py -q
```

Windows 换成对应解释器路径。doctor 默认不取数；`configured_unverified` 不能当真实可用。轻量 live probe 仅小红书登录状态另行实现，不能证明所有搜索/正文/数据接口已通过。

测试跳过项须报告原因；独立安装检查若因缺少现代打包工具被跳过，不算通过。全量回归不要求真的联网；收费/登录来源真实测试在后续明确选择后执行。

## 5. 配置 Agent 的 MCP

通用 stdio 配置示例，外层格式按宿主调整：

```json
{
  "mcpServers": {
    "ir_search": {
      "command": "/ABSOLUTE/PATH/TO/.venv/bin/python",
      "args": ["-m", "ir_search.mcp_server"],
      "env": {
        "IR_SEARCH_CREDENTIALS_FILE": "/ABSOLUTE/PATH/TO/private/credentials.env"
      }
    }
  }
}
```

Windows 使用 `C:/.../.venv/Scripts/python.exe`（或直接用 `C:/.../.venv/Scripts/ir-search-mcp.exe`，不带 `args`）。旧 Cursor 工作区模板及其 bootstrap 依赖 zsh 包装脚本，只支持 macOS/Linux，在 Windows 上会直接给出提示并退出。

MCP 工具的路径参数由模型填写，而模型同时会读到不可信的网页和文章，所以 `search_materials.audit_dir` 与 `retrieve.archive_dir` 只能写到一个本机根目录之内：默认是凭证文件所在目录下的 `.local/exports`，可用环境变量 `IR_SEARCH_OUTPUT_ROOT`（绝对路径）改到别处。传相对目录名（如 `runs/2026-09`）即可；指向根目录之外的路径返回 `invalid_request`。Python SDK 由本机可信代码调用，不受此限制。

从无源码工作目录启动已安装服务也应正常加载包内资源。MCP 应列出 12 个工具；新业务使用 `list_capabilities`、`source_health`、`describe_dataset` 与三个核心入口。旧工具保留，不要求测试 Agent 使用旧报告流程。

## 6. 外部验收与升级

执行[Agent 测试流程](agent_acceptance.md)，以[报告模板](test_report_template.md)提交结果。先验证来源选择为零调用，再做用户选定来源的小样本与原文引用。回退实测需要宿主原生联网工具；无该工具应明确 pending，不伪装成功。

修改后重新构建、安装 wheel 并从无关目录测试；不要只在源码目录跑通就宣称跨电脑完成。稳定版发布前还需实际多系统 CI、隐私检查和版本/散列固定。[GitHub Actions](https://github.com/Chener6280/ir-search/actions/workflows/standalone.yml) 执行 `.github/workflows/standalone.yml`；以当前提交对应的运行结果为准，存在配置不代表检查通过。

早期逐次安装记录归档在[历史部署记录](history_standalone_deployment.md)。

## 评审修复候选 rc2

同一代码库支持 Windows、macOS、Linux，平台差异集中在文件锁、连接取消和路径处理。安装建议使用 Python 3.12；Python 3.9 仅验证基础 SDK，MCP 依赖更高版本。

新 MCP 集成建议在 **MCP 服务进程环境** 设置 `IR_SEARCH_MCP_MODE=core`（不是数据源 env 配置项），默认 `legacy` 保持旧工具兼容。`IR_SEARCH_OUTPUT_ROOT` 同样属于服务环境；它只约束 MCP 输出，SDK 可显式选择其他安全目录。

数据库分页需要凭证目录旁 `.local/state` 可私有写入，多个 worker 共用该目录并要求文件系统提供可靠锁与原子替换。只读安装请把凭证路径指向本机可写的私有目录。损坏密钥不自动重建，以免掩盖状态问题；修复状态后重新发起第一页。未验证 SMB/NFS 的锁语义，不建议把状态目录放网络盘。旧密码签名数据库游标失效，素材来源的独立续取游标不受该迁移影响。

`Diagnostic.datasets=[]` 表示共享诊断；非空时只适用于列出的数据集。共享认证/连接配置错误不得伪装成覆盖不足后切换来源。
