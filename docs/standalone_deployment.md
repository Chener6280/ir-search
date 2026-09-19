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

Windows 使用当前用户私有目录及文件访问控制；PowerShell 可设置 `$env:IR_SEARCH_CREDENTIALS_FILE='C:/PRIVATE/credentials.env'`。不要直接执行/source env 内容，库按字面解析，不执行其中命令。

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

Windows 使用 `C:/.../.venv/Scripts/python.exe`。从无源码工作目录启动已安装服务也应正常加载包内资源。MCP 应列出 12 个工具；新业务使用 `list_capabilities`、`source_health`、`describe_dataset` 与三个核心入口。旧工具保留，不要求测试 Agent 使用旧报告流程。

## 6. 外部验收与升级

执行[Agent 测试流程](agent_acceptance.md)，以[报告模板](test_report_template.md)提交结果。先验证来源选择为零调用，再做用户选定来源的小样本与原文引用。回退实测需要宿主原生联网工具；无该工具应明确 pending，不伪装成功。

修改后重新构建、安装 wheel 并从无关目录测试；不要只在源码目录跑通就宣称跨电脑完成。稳定版发布前还需实际多系统 CI、隐私检查和版本/散列固定。[GitHub Actions](https://github.com/Chener6280/ir-search/actions/workflows/standalone.yml) 执行 `.github/workflows/standalone.yml`；以当前提交对应的运行结果为准，存在配置不代表检查通过。

早期逐次安装记录归档在[历史部署记录](history_standalone_deployment.md)。
