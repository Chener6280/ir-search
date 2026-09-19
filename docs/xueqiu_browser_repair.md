# 雪球浏览器读取修复记录

2026-09-18。**自动正文读取仍未验收通过。** 已独立重构可选浏览器后端及失败保护，默认仍为 HTTP；没有因为浏览器窗口能看见文章，就把 SDK/MCP 标记为可用。原有账号、密码和 Cookie 未改动。

## 本地参考与采用范围

参考华福 `xueqiu-scraper/SKILL.md`（本地资源库 `s069`）：通过正常页面加载，再用 `h1.article__bd__title`、`div.article__bd__detail` 读取文章。原技能只给出已经运行的 `playwright-cli` 会话调用，没有提供独立启动、依赖安装、预算或登录态管理；其引用的批量脚本未在该目录找到。

本项目采用主帖选择器、浏览器加载和分离标题/正文的做法，继续保留技能建议删除的“来源”“来自”文字。代码全部位于可安装包内，不调用桌面技能、CLI 会话、Codex 浏览器或个人浏览器配置。没有修改参考技能，也没有扩建 `deep_research`。

## 包内实现

- `xueqiu_browser.py`：显式后端配置、可选依赖检查、隔离子进程、取消/超时和预算、返回值校验、来源与日期绑定。
- `_xueqiu_browser_worker.py`：临时 Chromium/Chrome 会话，读取唯一主帖与标题、主帖作者和关联发布时间。排除评论、导航和推荐内容。修改时间不会冒充发布时间，缺少匹配日期链接时日期未知。
- `_xueqiu_tunnel.py`：每次运行的本机临时认证代理，仅连接雪球和固定资源主机的已检查公网地址；浏览器端仍校验目标 TLS。限制流量、连接数、浏览器请求数以及最多四次主文档导航。无任意站点代理，无永久监听。
- 浏览器仅发 GET，允许必要页面脚本和样式；阻止写操作、XHR/fetch、子框架、弹窗、媒体、WebSocket 和站外跳转。不注入反检测脚本，不移除自动化标识，不自动登录或处理验证码。
- `search_materials` 和 `retrieve` 通过原有社区 reader 选择该后端；正文与字符级引用仍使用通用材料契约。UGC/opinion 标识、搜索摘要与原文的区别保持不变。
- 重复刷新返回 `web_content_challenge`；取消、时限和预算耗尽保留原始错误类型。错误页面不产生材料或引用。`source_health` 仍为配置/依赖诊断，不因选中浏览器模式就宣称在线成功。

## 实测结果与限制

公开样本：`https://xueqiu.com/1965894836/341130177`，PaulWu《我们做什么，不做什么》。

| 路径 | 观测结果 |
|---|---|
| Codex 普通浏览器页面 | 页面显示未登录，能看见约 1,300 字正文、作者及 `2025-07-03 15:59` 发布时间；重新加载后仍可见。这是人工浏览器对照，不是 adapter 验收。 |
| 独立 Chromium，匿名/本地显式 Cookie | 均没有取得主帖；页面与同站加载脚本反复返回 HTTP 200，并重复导航。HTTP 200 不等于原文成功。 |
| 稳定版 Chrome，无界面/普通窗口 | 同样未取得主帖；换版本、普通窗口和账号会话均未解决本次验证循环。 |
| 用户明确授权后的单篇对照：稳定版 Chrome 普通窗口，仅移除 `--enable-automation` | 匿名临时会话，保留沙箱、TLS、公网主机和预算限制；实际执行后仍返回 `web_content_challenge`，8 次请求，无主帖正文。此选项没有解决本次问题，未加入正式代码或配置。 |

移除 Chrome 自动化标识的对照操作最初被自动审批拒绝；用户随后明确授权单篇公开样本测试，重新提交审批后已实际执行，结果仍失败。没有扩展到其他反检测修改，也没有把这次对照选项纳入实现。仅凭以上观测不能断言平台唯一的拦截依据，也不能认定只移除自动化标识就可解决。后续仍需要经过真实验证的接入方式；本次不承诺可以自动完成账号验证。

浏览器模式目前标为实验性，不保证所有主帖类型可用，更不提供全站搜索、作者历史穷尽抓取、评论或付费内容。搜索仍依赖博查收录范围，任何摘要不得替代本次未取得的原文。

## 选项与独立部署

默认配置不启用浏览器。需要在另一台电脑评估时，先安装 optional extra 和匹配的浏览器：

```bash
python -m pip install '.[browser,mcp]'
python -m playwright install chromium --no-shell
```

此后端要求 Playwright ≥ 1.49，使用该版引入的 `chromium` 原生无界面通道，见 [官方版本说明](https://playwright.dev/python/docs/release-notes#version-149)。浏览器版本须与安装库匹配，见 [官方浏览器安装说明](https://playwright.dev/python/docs/browsers)。浏览器沙箱、TLS 和公网限制保持启用，环境不满足时明确失败。

私有 env 可显式选择：

```dotenv
XUEQIU_READ_MODE=browser
XUEQIU_BROWSER_USE_COOKIE=false
XUEQIU_BROWSER_HEADLESS=true
XUEQIU_BROWSER_CHANNEL=chromium
```

`XUEQIU_READ_MODE=http` 恢复原有最小依赖路径，不自动跨后端重试。`XUEQIU_BROWSER_CHANNEL=chrome` 使用本机已安装的 Chrome；`HEADLESS=false` 需要图形桌面且会短暂打开独立窗口。两项都不是已验证的解锁方法。仅显式 `USE_COOKIE=true` 才把本机 `XUEQIU_COOKIE` 加到临时会话的雪球主机，不输出其值，也不自动读取个人浏览器账户。

SDK 可为单次浏览器样本使用 `RequestContext(timeout_seconds=30, max_operations=35)`，所有子请求仍受同一预算限制。`retrieve` 的 MCP 包装已有请求预算，超时可通过 `timeout_seconds` 指定。

## 验证

- 全量测试：`python3 -m pytest -q`，**1,925 passed、15 skipped**；5 条既有 PDF/SWIG 警告。
- 新增 53 项测试覆盖配置、主帖和日期绑定、来源行保留、字符引用、Cookie 隔离、私有地址拒绝、资源类型边界、导航刷新上限、HTTP 失败分类、错误脱敏、子进程退出、取消和预算。
- Python 3.12 针对浏览器、SDK/MCP、独立安装及会话修复的检查：96 项通过。
- 从无源码路径的独立目录调用安装包：SDK/MCP 零网络预览、源诊断、包内字典和私有值扫描通过。真实 MCP `retrieve` 返回 `web_content_challenge`，0 材料、0 引用。一次限定博查检索有日期过滤诊断，最终 0 条符合窗口的材料，未声称该样本搜索命中。
- wheel 包含三个新浏览器模块；已检查没有私有 env、账户文件、个人路径或已知密钥/Cookie 值。未加载旧研究编排模块。
- 本地私有验收摘要放在 Git 忽略的 `.local/xueqiu-repair/`。线上正文与账户内容不写入测试夹具或发布包。
- 用户授权的追加对照摘要为 `.local/xueqiu-repair/authorized-comparison.json`。仅记录脱敏错误代码和请求数；此次未使用或更改 env 凭证，包内运行代码未改变。

本轮没有发布 GitHub。实际自动取数状态以此处的失败结果为准；离线测试通过不等于雪球平台在线放行。
