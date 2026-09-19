# 网页工具与 RSS 验收（2026-09-18）

本轮实现与用法见 [网页与订阅源改进](web_toolkit.md)。实验响应及样本保存在被 Git 忽略的 `.local/web-toolkit-acceptance/`；不将原始材料、密钥或参考库打入 wheel。

后续补充：用户填入 Key 后，Firecrawl 已完成有限样本真实读取与 SDK 引用验证；本文下方保留初次验收记录。当前结果、失败样本和用量见[Exa / Tavily / Firecrawl 对比](search_provider_comparison.md)，不能再将“初次没有 Key”理解为当前配置状态。

## 公开来源实际读取

| 检查 | 结果 | 范围与诊断 |
|---|---|---|
| 美联储 press_all RSS | 20 条可解析记录，20 条有日期，1 次 HTTP 请求 | 当次快照，非完整历史 |
| 欧洲央行 press RSS | 上游 15 条，其中 14 条有效且有日期，1 次 HTTP 请求 | 1 条空标题被过滤，保留 `invalid_rss_entry` |
| 美联储原文 / Scrapling 对比 | 普通解析 9,417 字符；语义正文 658 字符 | 同一份 HTML，标题与原始 hash 一致，4 条引用逐字符匹配 |
| 欧洲央行原文 / Scrapling 对比 | 普通解析 19,186 字符；语义正文 5,780 字符 | 同一份 HTML，标题与原始 hash 一致，20 条引用逐字符匹配 |
| SDK RSS 检索到原文 | 查询 inflation expectations，得到 1 组、1 个原文版本，共用 4 次操作 | `partial`；快照不穷尽、空标题过滤等诊断保持可见 |
| SDK retrieve | 读取上述命中原文，返回 1 份材料和引用 | `partial`；`material_has_caveats` 保持可见 |
| MCP 正式 payload 执行预览 | rss 被选中，0 次来源调用 | 仅预览，不把它当作在线检索验收 |
| Firecrawl | 未配置 Key，未发起云调用 | 离线协议/错误路径通过；不能宣称线上成功 |

固定原文样本：[美联储执行措施终止公告](https://www.federalreserve.gov/newsevents/pressreleases/enforcement20260918b.htm)、[欧洲央行消费者预期调查](https://www.ecb.europa.eu/press/pr/date/2026/html/ecb.pr260918~295b3ab978.en.html)。查看了所选正文，主要减少站点导航、页尾等内容；字符减少比例不代表完整性或准确率认证。两篇样本不能推断所有网站都有效，故默认读取策略未改变。

## 离线与安装包检查

- `python3 -m pytest -q`：**1965 passed, 21 skipped**。跳过项包含环境/可选依赖相关检查，其中六个真实 Scrapling 选择器样本在基础 Python 中跳过。
- Python 3.12 私有测试环境，Scrapling 0.4.15、MCP 1.x：新 RSS、网页工具及安装包相关测试 **49 passed**，包括上述六个真实 Selector 用例。依赖中出现 lxml 的弃用提示，基础环境另有原有 PDF/SWIG 弃用提示，均未导致失败。
- 协议与边界：RSS/Atom 日期口径、xml:base、重复链接、摘要/原文区分、正文预算、跳转同一原文、正文日期冲突、空标题、无效链接、XML 实体/DTD（UTF-8/16/32）、过深/过大 XML、HTTPS 降级。
- 网页路径：Scrapling 唯一区域/多篇歧义/正文过短/缺包/异常/截断，Firecrawl 显式启用、Key 不进 repr、无 LLM 参数、无 Cookie 转发、拒绝认证/查询 URL、API 重定向、原站状态与最终地址、200 验证页、取消、错误脱敏；引用对应最终文本。
- 构建 wheel 并安装到与源码无关的目录，验证 SDK、实际 MCP `call_tool`（固定离线数据）、材料能力、RSS 元数据/引用、诊断、资源加载，以及核心未导入 `ir_search.research`。安装包内含全部新增模块，运行不依赖 Desktop 或本地参考库。
- 独立构建的 wheel 已检查私有文件、当前本地密钥及 Cookie 值均未进入包。新代码、测试、公开 env 模板和指南另行通过已知密钥扫描。

wheel：`ir_search-0.1.0-py3-none-any.whl`；SHA-256：`acbb77ab86e5d33747c2380844b48f10ebe59fa56e8ebde8be075de12aa26d3a`。本地文件位于 `.local/web-toolkit-acceptance/wheels/`。

本机 `credentials.env` 已保留 `FIRECRAWL_API_KEY` 空位并设置 `FIRECRAWL_ENABLED=false`；没有产生 Firecrawl 云调用费用。两个公开 RSS 已启用。Scrapling 只安装于项目私有测试环境，其他运行环境按指南安装可选 extra；配置就绪或依赖存在不自动升级为实时健康通过。

参考库由 39 条增至 42 条。三个上游 README 与许可证按以下 revision 固定保存：Firecrawl `e3e324e3d4258bbf1c72ddfde90ad7649cf676cb`，Scrapling `2b160ee18bfee79bb0115e2d9e9c746c8d9bf4c9`，TrendRadar `792bcc3928b1617bba09df34989fd5675c159b86`。Scrapling 实际安装版本为 PyPI 0.4.15，与 README 分支快照的 revision 分别记录。

本轮没有重新证明雪球可自动读取，也没有更改其验证失败状态。
