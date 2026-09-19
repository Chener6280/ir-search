# SEC 原始披露验收

验收日期：2026-09-17。实现、配置及边界见 [SEC 接口指南](sec_filings_adapter.md)。只使用 SEC 官方公开接口和本地访问者声明，未调用或升级 FMP。

## 真实取数

以下均通过 SDK 的 `search_materials(providers=["sec"])` 取得索引，并按预算读到非空主文件正文。默认正文上限本次设为 30,000 字符；表内数量是本次返回的文本量，不表示全文件长度。

| 样本 | 申报日 / 报告期末 | 正文字符 | 结果 |
|---|---|---:|---|
| [Apple 10-K](https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm) | 2025-10-31 / 2025-09-27 | 30,000 | 年报索引及原件通过；截断 |
| [Apple 10-Q](https://www.sec.gov/Archives/edgar/data/320193/000032019326000020/aapl-20260627.htm) | 2026-07-31 / 2026-06-27 | 29,999 | 季报索引及原件通过；截断后去尾部空白 |
| [Apple 8-K](https://www.sec.gov/Archives/edgar/data/320193/000032019326000018/aapl-20260730.htm) | 2026-07-30 / 2026-07-30 | 3,566 | 临时公告及业绩稿附件链接通过 |
| [台积电 20-F](https://www.sec.gov/Archives/edgar/data/1046179/000162828026025362/tsm-20251231.htm) | 2026-04-16 / 2025-12-31 | 30,000 | 外国发行人年报通过；截断 |
| [台积电 6-K](https://www.sec.gov/Archives/edgar/data/1046179/000104617926000658/tsm-revenue20260910.htm) | 2026-09-10 / 2026-08-31 | 6,005 | 外国发行人临时披露通过 |
| [TD 银行 40-F](https://www.sec.gov/Archives/edgar/data/947263/000156276225000289/40f20251031.htm) | 2025-12-04 / 2025-10-31 | 18,695 | 主文件及财务报表、MD&A 等附件链接通过；未自动读取这些附件 |
| [Apple 2020 年 10-K](https://www.sec.gov/Archives/edgar/data/320193/000032019320000096/aapl-20200926.htm) | 2020-10-30 / 2020-09-26 | 30,000 | 跨历史 submissions 文件查询通过；截断 |

Apple 和台积电年报另通过 `retrieve` 返回 99,999 / 100,000 字符，均明确截断，各得到 10 处与返回正文逐字符一致的引用。没有把受理时间（例如历史年报的 UTC 时间）替代 SEC `filingDate`。

所有搜索仍为 `partial`、`complete=false`，保留 `source_scan_incomplete`、`bounded_search_not_exhaustive` 等覆盖诊断。8-K、6-K 的测试设置候选上限为三条，并收到 `sec_candidate_limit_reached`；不宣称该窗口全部公告已读。

## 独立安装与 MCP

在临时目录构建并安装 wheel，从 `/private/tmp` 以隔离 Python 启动，源码目录不在导入路径：

- SDK 无网络预览、机构字典资源、SEC 注册与配置诊断通过；仍为 12 个 MCP 工具，`search_materials` schema 包含 `sec_ciks`、`sec_forms`。
- 已安装 MCP 真实查询 Apple 8-K 并取得 3,566 字符原文；随后读取其[业绩稿附件](https://www.sec.gov/Archives/edgar/data/320193/000032019326000018/a8-kex991q3202606272026.htm)，取得 10,910 字符与 5 处逐字符一致引用。
- 已安装 SDK 独立读取同一主文件通过，未加载 `ir_search.research`。
- wheel 包含新模块与机构资源，不含 `.local`、凭证或个人绝对路径；本机已知 Key、Token、密码、Cookie 和访问者声明扫描通过。返回结果亦不含这些私有值。

首次独立验收曾得到 `tls_error`，未取得证据；随后两个 SEC 主机的 TLS 握手复核正常，在不修改身份、不关闭证书校验的情况下复测通过。该错误保留为连通性的实际限制，不据一次通过承诺长期无故障。

本次验收 wheel SHA-256：`747944dd9f549ff45166fd8d62d32c21bf23961144b395ce948bdf95cb9f517e`。

## 测试与未覆盖项

- 全量 `python3 -m pytest -q`：**1,576 passed、15 skipped**，5 条既有 PDF/SWIG 依赖警告。
- Python 3.12 带 MCP 的相关选集：**90 passed**。
- 新增 SEC 测试覆盖私有配置、CIK/表单校验、历史文件选择、日期口径、修订版身份、隐藏 XBRL 排除、字符引用、附件域限制、正文失败保留索引、限流/403、重定向限制、取消、PDF 警告归一化和 MCP 传参。
- 本次真实正文样本全部为 HTML；PDF 分支是可选依赖和离线边界测试，未宣称 SEC PDF 实样验收。中文公司名模糊解析、全市场全文搜索、长年报尾部续读、全部附件获取、其他海外交易所和 FMP 付费扩展不在本次完成范围。

本机明细存于 Git 忽略的 `.local/sec-acceptance/`。`credentials.env` 保持 0600，其他来源凭证未改写。本轮未提交或推送 GitHub。
