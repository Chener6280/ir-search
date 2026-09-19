# 现有来源实际可用性验收

日期：2026-09-17。本轮沿用现有 SDK/MCP 入口，改动仅在 `ir_search`；没有改写 Desktop 技能，也没有扩建 `deep_research`。所有线上凭证从本地私有 env 读取。测试样本只是有界实测，不能解释为来源全量覆盖或未来可用性保证。

**2026-09-18 更新：** 下表雪球/Bilibili 行为历史记录。后续已修复 Bilibili HTTPS 请求兼容问题并刷新会话，详情、字幕和独立安装包 MCP 验收通过；雪球主帖仍被验证页阻断。以[专项修复验收](platform_session_repair_acceptance.md)为准。

## 本轮结果

| 来源 | 真实取数与本轮改进 | 尚存边界 |
|---|---|---|
| YouTube | 默认系统解析含非公网地址；新增仅对 YouTube 生效的可选 HTTPS DNS，保留公网地址和 TLS 校验。真实样本取得 217 字符平台字幕，安装包 MCP 复测成功 | 只验证了一条公开视频；依赖字幕本身可取得和网络条件，不代表所有频道或字幕语言均可读 |
| 知识星球 | 官方时间线两页共 10 条、9 个不同主题，确认边界重复。新增请求级选星球、扫描位置续取及相邻页去重。SDK 首批扫描 3 条、续批 4 条，匹配返回分别 2 条、1 条，无重复引用；详情读取 903 字符且引用精确 | 时间线、本地日期过滤和每源预算仍有界；本次评论请求返回 0 条，不能声称已验证非空评论翻页。本轮没有再次测试 PDF 附件；以先前附件验收为历史记录 |
| 微信公众号 | 原有历史接口两页取得 11+13 篇、24 个不同地址。正文取得 20,000 字符，按字数上限截断并明确标记。新增同页剩余记录和后续页的续取；SDK 两次扫描 3+4 条，复用历史缓存 | 该两批在“投资”条件下没有匹配项，属于有扫描、无命中，不是全文取数失败；仅限配置账号池，不是公众号全网/历史全集 |
| IMA | 官方目录两页 20+3 个不同知识库；查询取回 20 条，其中 17 个 PDF、2 个 PPT、1 个公众号条目。原文实测：PDF 11,392 字符、PPT 17,825 字符、笔记 976 字符、公众号 4,971 字符。SDK 续取返回 3+4 条不同素材，安装包 MCP 再次续取 4 条 | 素材查询此次缺少下一页标记，不能推测第二页；可续读当前页余项。目录的末页结束标记优先于残留游标。默认搜索仍受首批目录及知识库数量上限约束；需要显式选库扩大范围 |
| 小宇宙 | 新增 `audio_window_count=1..5`。同一真实节目连续读取 0–12 秒、12–24 秒，合计 127 字符、5 个带绝对时间的句段；首段缓存命中，仅第二段调用 ASR。再次读取及安装包 MCP 读取均全命中缓存、零 ASR，字符引用逐一校验通过 | 整集 1,395 秒，本次只验证前 24 秒，明确 `whole_episode_transcribed=false`、下一起点 24。不是整集转写验收；机器转写保留核对提示 |
| 雪球 | 用户授权后，从 Safari 已登录页面提取对应站点 Cookie 并写入本地 `XUEQIU_COOKIE`；未显示 Cookie 值。带 Cookie 的 SDK 读取再次实测 | 仍返回 `web_content_challenge`，没有正文材料；登录态存在不等于程序访问通过 |
| Bilibili | 同上，写入 `BILIBILI_COOKIE`，包含会话 Cookie；再次通过 adapter 读取真实 BV 视频 | 仍返回 `web_content_challenge`，没有真实详情/字幕验收成功；没有把浏览器已登录当作 API 已可用 |

东方财富股吧、网页、智堡、Tushare、市场数据等没有在本轮更改接口；既有实测留在各自验收文档中，不用本轮测试日期刷新其真实取数结论。

## 可供 skills 调用的增量

- `search_materials` 的 `coverage[].continuation_cursors` → 下一次请求的 `source_cursors`；星球增加 `zsxq_group_ids`。同页偏移带摘要校验，源页改变时拒绝静默跳过。续取只是扫描位置，不是排序结果分页，也不承诺穷尽来源。
- `retrieve` 的 `audio_window_count` 控制每次连续窗口数；逐段缓存、实际终点、来源音频哈希、时间引用和失败码对调用方可见。一般后段失败保留已完成内容；取消或总期限耗尽仍遵守服务停止规则，已经完成的缓存可供下次复用。
- `YOUTUBE_DNS_MODE` 默认 `system`，本机选择 `google_doh`。固定 Google DNS 端点只接收域名，不接收业务 Cookie、Key；目标连接仍验证 YouTube TLS。详见 [Google 官方说明](https://developers.google.com/speed/public-dns/docs/doh/json)与[视频指南](community_video_adapters.md)。
- `source_health` 披露 DNS 模式和 Cookie 是否配置，始终区分本地配置与实时可用性。

协议细节见[素材搜索](material_search.md)、[音频读取](xiaoyuzhou_audio_adapter.md)。引用保留正文/摘要/搜索片段、UGC/来源未知、原文或机器转写等区别，不因检索条数或跨平台转载而升级为已核实事实。

## 验证与独立部署

- 基础环境全量 `python3 -m pytest -q`：**1,542 passed，15 skipped**，5 条既有 PDF/SWIG 依赖警告。
- Python 3.12、安装 MCP/视频/音频可选依赖后的相关选集：**142 passed**。
- 新测试覆盖：游标绑定、候选预算改变、页内续取、源页变动拒绝、星球重复边界、结束标记优先、DoH 固定主机/公有地址/TTL/凭证隔离、多段尾部、后段失败和音频变化时停止拼接。
- 在临时目录构建 wheel 并安装，从 `/private/tmp` 隔离启动：源码不在导入路径，SDK/MCP 零网络预览、机构资源加载、IMA 真正续取、YouTube 真实字幕及两段音频缓存读取均通过；未加载 `ir_search.research`。
- wheel 扫描本机已知 Key、Token、密码和 Cookie 完整值通过；不含 `.local`、凭证、账号清单或个人绝对路径。新模块、CSV 资源已包含在包中。

验收 wheel SHA-256：`5e5cc2d1f13855d77e5ca9976c865d36c5c4c3380f3362d4af68ad79530dc63c`。

本机明细存于忽略提交的 `.local/usability-acceptance/`，正文和私有范围不进入测试夹具。`credentials.env` 保持 0600，其他凭证未覆盖；临时 Cookie 导入文件已删除，Safari 临时开启的开发菜单已恢复原状。本轮没有提交或推送 GitHub。其他电脑需安装相应 extras、FFmpeg 并提供自己的 env；本机 Cookie 不应随代码分发。

## 后续增量：SEC 原始披露

2026-09-17 新增 `sec`，六类 SEC 表单、历史年报和原始业绩稿附件已通过真实 SDK/MCP 读取，独立安装也已通过；本阶段全量测试为 1,576 passed、15 skipped。该结论不刷新上文其他供应商的验收结果，详见 [SEC 验收](sec_filings_acceptance.md)。
