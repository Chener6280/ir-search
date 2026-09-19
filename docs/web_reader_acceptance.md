# 统一网页读取层验收

日期：2026-09-16。范围：引用修复、HTTP/可选 Crawl4AI 统一读取、正文状态、有限链接展开与文本变化标记。实现均位于 `ir_search` 包内；没有修改桌面 skills、私有 env 或接入旧 `deep_research` 编排。

## 实际取数

使用 Crawl4AI 0.9.3、Python 3.12、macOS ARM64。浏览器按固定参数启动，所有网页子请求经 DNS 固定连接与证书验证的传输完成；没有继承用户 Key/代理或加载账户 Cookie。没有调用付费搜索、LLM 或登录内容。

| 验收项 | 结果 | 保留的限制 |
|---|---|---|
| NVIDIA 动态财报目录，SDK | 约 15.24 秒，目录文字 10,433 字符，65 个不同 PDF 地址；10 条引用位置及文本哈希一致 | 目录发布日期未知；全部公开链接受 100 条上限约束；图片等资源被拦截，不声称全站覆盖 |
| Apple 动态 IR 目录，SDK | 约 14.41 秒，目录文字 6,036 字符，32 个不同 PDF 地址；10 条引用校验通过 | 部分样式/附属请求失败或被拒绝，均有诊断；本项未逐份下载 PDF |
| 已安装 wheel 的 MCP：NVIDIA 目录 → 一份电话会 PDF | 约 20.31 秒，显式 `follow_links=1`、域名 `q4cdn.com`；目录取得 65 个 PDF 地址，实际下载并提取其中一份 Q1 FY2027 电话会 PDF，返回前 50,000 字符；目录及文件各 10 条引用均通过位置/哈希核对 | PDF 明确截断，发布日期未从文件名猜测；不能算完整电话会全文验收。字体样式请求证书失败被拒绝，未关闭校验 |
| 已安装 wheel 的 SDK：统计局年度公报 | 约 0.54 秒，HTTP 读取，正确解析 `2026/02/28 09:30`，10 条引用通过 | 时区未知，返回文本截到 50,000 字符；链接和正文截断分别标记 |
| 已安装 wheel 的 SDK：NVIDIA 年度业绩稿 | 约 3.27 秒，HTTP 读取 33,367 字符，10 条引用通过；未启动浏览器 | 页面发布日期仍未知，未为了“完整字段”猜测；尚未逐数字/逐单元格审计财务表格 |
| 已安装 wheel 的 SDK：余杭政府图解 | 返回 `unavailable` / `web_content_image_only`，没有输出正文材料 | 需要后续图片/OCR能力；标题和日期不冒充图解原文 |
| 前期保存的真实 NVIDIA Markdown | 同一 50,000 字符范围复验，10 条返回引用，0 条位置不一致 | 此项仅验证已保存文本上的引用逻辑，不代表重新读取全部原文 |
| 3 秒截止后的进程清理 | 返回 `deadline_exceeded`；包含检查等待约 3.57 秒，观察到的 5 个子进程全部退出，worker 已回收 | 是本机 POSIX 实测；不将其外推为 Windows/Linux 已逐平台验收 |

这些耗时来自各一次特定网络运行，不是吞吐承诺。目录的 `partial` 和图解的 `unavailable` 是正确暴露限制；没有用工具 `success=True` 冒充正文成功。

实际来源：[NVIDIA 财报目录](https://investor.nvidia.com/financial-info/financial-reports/default.aspx)、[Apple IR](https://investor.apple.com/investor-relations/)、[读取的电话会 PDF](https://s201.q4cdn.com/141608511/files/doc_financials/2027/q1/NVDA-Q1-2027-Earnings-Call-20-May-2026-5_00-PM-ET.pdf)、[统计局公报](https://www.stats.gov.cn/sj/zxfb/202602/t20260228_1962662.html)、[NVIDIA 业绩稿](https://nvidianews.nvidia.com/news/nvidia-announces-financial-results-for-fourth-quarter-and-fiscal-2026)、[余杭图解](https://www.yuhang.gov.cn/col/col1229188665/art/2026/art_9b7b5319a74c4c77829b2af66e2e6582.html)。

## 受控测试与安装包

- 完整项目测试：`python3 -m pytest -q`，1250 项通过、12 项跳过；5 条现有 PyMuPDF/SWIG 弃用警告。
- Python 3.12 环境补充运行安装包、SDK/MCP、网页与浏览器测试：115 项通过。包括构建 wheel、删除构建源码，在无 checkout 导入路径的目录验证资源、模块、来源诊断与 MCP。
- 实际浏览器/MCP 测试另将 wheel 安装进隔离环境，使用 `python -I`，工作目录切到临时目录，并确认导入来自 `site-packages`。凭证配置为临时空文件，不读取本机真实 env。
- 已检查 wheel 不含私有 env、账户文件、桌面源码或个人运行路径；依赖一致性检查通过，共 106 个已安装包。基础依赖没有引入 Crawl4AI，重依赖在 `crawl` extra 下。
- 新增回归涵盖：长英文/中文/表格/emoji/重复段落引用、无效分块大小、HTTP/TLS/权限拒绝不触发浏览器、加载/验证/工具错误/图解状态、缺依赖、固定只读接口、公网传输与重定向、robots、预算/取消、禁止环境凭证继承、有界单层发现、增量 `new/changed/unchanged` 与不同层级的来源引用。
- 增量状态使用已知答案的合成页面测试，不把它报告成实际网站增量索引已建成。服务仍重新取数，返回文本变化仅用于调用方去重分析。

## 本机交付位置

- 实现：仓库的 `ir_search/infrastructure/web_documents.py`、`web_browser.py`、`_crawl_worker.py`、`_browser_network.py`，以及合同、引用、SDK/MCP 的配套修改。
- 可直接使用的安装环境：项目下 `.local/crawl4ai-venv`，已安装本项目 wheel、MCP 和浏览器依赖。
- Chromium：`.local/crawl4ai-browsers`；启动该环境时设置 `PLAYWRIGHT_BROWSERS_PATH` 为本机此目录的绝对路径。
- 本地构建 wheel：`.local/crawl4ai-delivery/wheels/`。
- 私有实验资产：`.local/crawl4ai-evaluation/` 下的 `integration-directories.json`、`integration-wheel.json`、`integration-citation-fix.json`、`integration-cancellation.json`。这些不进入发布包。

独立环境不替换调用方当前解释器；部署 MCP 时应选择安装了所需 extras 的解释器。换电脑仍需各自安装依赖与浏览器、提供自己的数据源配置。本轮完成本地独立安装与实际调用，未宣称已在另一台物理电脑运行或已发布 GitHub。

## 保留的边界

未做持久化全文索引、定时监控、自动多层爬取、登录/付费墙绕过、OCR 或结构化财务表格入库。原始 HTML 使用后不永久存档；调用方负责保存返回文本和版本信息。浏览器总 RSS 没有硬内存限制；初始 HTTP DNS/提取仍有协作式期限边界。普通网页标题/正文提取及正文状态判断属于规则，不保证对所有站点完美。

调用与安装方式见 [统一网页读取指南](web_reader.md)。
