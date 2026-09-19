# 公众号结构提取与文章归档验收

验收日期：2026-09-16。实现及调用方式见[文章结构与本地归档](article_materials.md)。

本轮参考 [podcast-summary / wechat-to-md](https://github.com/hxer7963/podcast-summary/tree/main/.codebuddy/skills/wechat-to-md) 的文章、元数据与图片分别保存设计，在 `ir_search` 内独立实现；未安装或执行外部 Skill，未修改 Desktop 技能目录。未扩建 `deep_research`，未新增基础依赖。

## 真实原站读取

使用 SDK `retrieve` 强制刷新正文缓存，关闭浏览器，并在供应商请求发出前拦截付费备用请求，单独检验 HTTP 原站读取能力。此次测试没有实际调用付费正文服务。

| 样本 | HTTP 原站结果 | 文本字符 | 图片引用 | 结构块 |
|---|---|---:|---:|---:|
| 用户提供的《把播客、视频和文章变成本地知识：podcast-summary Skill Hub》 | 取得正文 | 5,412 | 7 | 176 |
| 《【广发宏观郭磊】8月经济、基建与内需定价》 | 取得正文 | 17,801 | 2 | 556 |
| 《策略小词典》第五期分刊 第二章 | 取得文字及图片引用 | 156 | 14 | 19 |
| 《两融与北上净流出放缓，股票ETF延续回流 \| 国金策略》 | 取得文字及图片引用 | 1,208 | 14 | 46 |
| 另外 3 个既有公开文章地址 | 未取得正文 | 0 | — | — |

用户文章：[原始地址](https://mp.weixin.qq.com/s/mmsc5m2wPhHiPgfgzUZ-_Q)。正文初始可见性样式不再导致整篇误判为空，此样本只经过 HTTP 读取，未调用 Crawl4AI 或付费正文接口。返回诊断保留 `wechat_initial_visibility_recovered` 等说明。

7 篇中 4 篇取得原站文字，只代表这组小样本的结果，不能据此承诺总体成功率。另 3 篇返回 HTTP 200，但响应中没有 `js_content` 正文容器；没有证据确定是删除、验证码或其他原因。生产读取链路仍保留浏览器与供应商备用通道。测试报告中 `vendor_requests_attempted=3` 是测试拦截器阻止的请求尝试，不是实际计费调用；对应 `unsupported` 也是测试禁用供应商产生的诊断，不代表供应商真实报错。

四篇返回素材均保留 `partial / material_has_caveats`，没有因提取到文字而提升为已核实事实。所有返回的文字引用及结构块字符范围均与返回纯文本逐字相符。图片未 OCR；尤其 156 字符、14 张图片的文章，不能据此声称已提取图中研究内容。广发文章链接达到 100 条上限，保留截断诊断。

## 实际图片与归档

- 用户文章的 7 张图片均实际下载成功，归档状态为 `ok`，发出 7 次图片请求。
- 再次以相同素材和导出选项归档，状态为 `reused`，新增图片请求为 0；保留归档最初抓取时间。
- 归档包含 `article.md`、`README.md`、`material.json`、`manifest.json` 和本地图片；文字引用仍以 `material.json` 中纯文本及字符范围为准。
- 图片下载为显式选项，默认只记录安全的图片引用，不下载、不 OCR。归档器也支持其他来源已经返回的 `Material` 文本和元数据；本轮丰富的文章结构解析用于公众号。
- 限额、超时、取消、部分失败续传、重复图片复用、文件校验及符号链接拒绝均有自动化覆盖。不能将读取版 Markdown 视为网页像素级还原。

本机验收结果保存在 `.local/wechat_rich_acceptance.json`。实际含图归档位于 `.local/material-archive/cc8da9e979a5f90c29b7/e173fce6f1007259c721a5d37c028fa4e8c4d28370118a5d4dcad2119d7cc1d1/`。这些文件为本地私有资料，不进入发布包。

## 自动化与独立安装

- 完整 `python3 -m pytest`：**1,291 passed，12 skipped，5 warnings**；警告为既有 PyMuPDF/SWIG 弃用提示。
- Python 3.12 下结构提取、省钱读取、公众号、浏览器、独立打包与 MCP 相关测试：**114 passed**。
- 构建 wheel 后安装至独立虚拟环境，从 `/private/tmp` 使用隔离模式运行；确认实际加载的是 `site-packages` 安装包，源代码目录不在导入路径上。
- 安装版 SDK 对用户文章实时 HTTP 读取成功：5,412 字符、7 张图片引用、176 个结构块，供应商调用数 0，引用精确匹配。
- 随后禁用网络连接，通过实际注册的 MCP `retrieve` 工具命中缓存，并成功离线导出归档；确认新增归档参数存在于工具 schema。
- wheel 检查未发现凭证、私有缓存、归档或本地资料；用本地凭证值执行包内容检查，无匹配，检查过程不输出凭证值。

本机交付包：`.local/wechat-rich-delivery/wheels/ir_search-0.1.0-py3-none-any.whl`。

SHA-256：`f7f4289d10cf7c7247d0aa0630b237ebbbc4a845accc082ab430823c7b08ee02`。

独立安装记录：`.local/wechat_rich_wheel_acceptance.json`。这是同一台电脑上脱离源码目录的安装验收，尚未在第二台实体电脑执行，也未发布至 GitHub。
