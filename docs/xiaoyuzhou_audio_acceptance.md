# 小宇宙 / Agent Plan 音频验收

验收日期：2026-09-17。本记录描述已观测结果，不代表所有节目、所有网络环境或账户套餐权限均可用。

## 真实链路

| 项目 | 结果 | 证据与限制 |
|---|---|---|
| 官方 Agent Plan 接入 | 通过 | 使用用户私有 env 中已有的音频 key，调用 `/api/v3/plan/sauc/bigmodel_nostream`；Seed ASR 2.0 专属资源返回终态成功 |
| 公开小宇宙页面 | 通过 | [投资笔记48：把AI用到投资上，可能你搞错了](https://www.xiaoyuzhoufm.com/episode/6a042c61e1eb34a93950f9a6)，页面给出发布时间、简介、1395 秒时长和公开 M4A 地址 |
| 音频读取与转换 | 通过 | 约 22.6 MB 音频经受限 HTTP 下载，在私有目录缓存；FFmpeg 从中转换 0–12 秒 PCM。为适配 Agent Plan 流式接口确实下载了音频，不声称只获取了 12 秒源文件 |
| 真实语音识别 | 通过 | 本轮仅一次 ASR 会话，提交 12 秒；返回 51 个字符、2 个句段，时间为 3880–6720 ms 和 7080–12000 ms |
| 引用和范围 | 通过 | 1 个证据片段与返回文本逐字符一致，绑定文本哈希与 ASR 时间；结果标记 `asr_transcript`、`machine_transcribed=true`、`whole_episode_transcribed=false`、下一起点 12 秒 |
| 缓存复用 | 通过 | `cache_only` 返回同样 51 字、`cache_status=hit`、`asr_invoked=false`；只重新读取节目页面，无第二次语音调用 |
| 博查 → 小宇宙素材搜索 | 通过，有覆盖边界 | 2025 年窗口内“AI 投资”查询扫描至多 5 条、读取至多 3 个页面，返回 2 个结果：1 篇 996 字节目简介、1 条 100 字搜索摘要。分别标记 `source_excerpt` / `search_snippet` |
| 2026 年窗口召回 | 未取得非空结果 | 宽泛及具体关键词测试仍主要召回较早节目，被本地日期过滤排除；这是本轮博查索引/召回的实际限制，不能宣称完整或及时覆盖。指定 2026 年节目链接的读取和转写成功 |

上述“partial”主要来自来源是未核验观点、检索有界、简介不是逐字稿、转写只覆盖指定窗口；不是将这些限制隐藏为“全量完成”。模型准确率未做人工逐字评测，也未测试整集长音频转写、其他 CDN、付费/登录节目或其他播客平台。

官方接口说明见[火山 Agent Plan 文档](https://ark.volcengine.com/region:cn-beijing/docs/82379/2516286?lang=zh)及[流式协议](https://docs.volcengine.com/docs/6561/1354869?lang=zh)。本轮未读取或改变账户超额计费设置，不报告未经核验的金额或剩余额度。

## 测试与独立部署

- `python3 -m pytest -q`：**1505 passed，15 skipped**；5 条既有 PDF/SWIG 依赖弃用警告。跳过项涉及可选依赖或另行显式验收的环境条件。
- 新音频模块：**36 个测试通过**，涵盖配置与凭证遮蔽、默认不转写、日期与来源、元数据空值/异常、付费拒绝、二进制终态帧、gzip 上限、权限/配额、TLS 和固定认证目标、不跟随重定向、不重试、提前终止、取消、真实 FFmpeg 合成音频、私有缓存损坏及复用、字符/时间引用与 MCP 参数。
- Python 3.12 隔离环境中，音频、独立安装包与选定 MCP 测试合计 **45 passed**。安装了可选 `websockets==15.0.1`，使用本机已有 FFmpeg。
- 构建 wheel 后从 `/private/tmp` 以隔离导入路径运行，确认导入来自 `site-packages`，SDK 和 MCP 的小宇宙预览不触网，来源注册、健康诊断、音频参数 schema、14 条内置机构资源均可用。
- 已安装包通过真实 MCP 读取上述成功缓存，校验 51 字转写与引用时间；测试中主动禁止音频下载和 ASR，证明此读取不重复消耗语音调用。
- 安装包检查排除了 env、私有缓存和本地参考库；扫描未发现本地凭证值、开发者绝对路径或桌面 skill 运行依赖。新的核心调用没有加载 legacy research。

私有详细结果位于工作区 `.local/audio-acceptance/`，音频和转写缓存位于 `.local/audio-cache/`；均被 Git 忽略。wheel 的精确哈希记录在 `.local/audio-acceptance/wheel.json`。没有把真实音频、逐字稿或密钥放入公开文档、测试夹具或发布包。本轮完成本地安装验收，未执行 GitHub 发布。

配置、调用例子和进一步边界见[音频 adapter 指南](xiaoyuzhou_audio_adapter.md)。
