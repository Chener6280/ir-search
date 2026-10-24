# 博查 TLS 与 DuckDuckGo 验证挑战补测

日期：2026-10-24（America/Los_Angeles）。仅 c-mac，继续在 `feat/web-search-chain` 本地修复与提交，不 push。首轮历史见[原验收报告](web_search_chain_validation.md)，不覆盖此前失败记录。

## 定位证据

博查[当前官方 SDK](https://github.com/bocha-ai/dsh-web-search-bocha)默认 API 为 `https://api.bocha.cn`，路径 `/v1/web-search`。本机只读参考 Pi 0.44.0 及旧实现仍使用 `api.bochaai.com`。在现有系统网络/代理/TUN 路径上，以不带 key 的 HEAD 请求比较：

| 运行时 | 旧域 `api.bochaai.com` | 当前官方域 `api.bocha.cn` |
|---|---|---|
| 系统 Python 3.9.6，LibreSSL 2.8.3 | SSLEOFError，约 5.03 秒 | TLS 完成，HTTP 405，约 2.42 秒 |
| 独立 Python 3.12.13，OpenSSL 3.6.2 | TLS 完成，HTTP 405，约 1.77 秒 | TLS 完成，HTTP 405，约 2.48 秒 |

405 是 HEAD 方法不被接口接受，说明已建立 TLS 并收到 HTTP 响应，不证明搜索成功。两种 Python 运行时读取到的 HTTPS 代理选择相同。该对照将故障定位到旧域名与本机旧 TLS 运行时组合的握手兼容性；没有证据断言只是证书根缺失或服务端只允许 TLS 1.3。系统运行时不支持 TLS 1.3，但不据此猜测服务端完整配置。

迁移的是供应商当前官方 API 端点，不是替换网络出口、关闭代理、跟随重定向或 TLS 失败后自动尝试其他域名。没有设置未验证 SSL context、修改密码套件、增加信任根或下载 CA；证书及主机名校验仍使用 Python/系统既有默认行为。

DuckDuckGo 原请求一次返回 HTTP 202，HTML 含 `challenge-form` / `anomaly-modal__box`，没有搜索结果或真实空结果标识。确认是上游验证页；没有访问表单目标、验证码图片、Lite/其他域名变体，没有切换 UA、Cookie、浏览器或代理。该限制下无法修复搜索可用性。

## 实现与回归

- 博查普通 adapter、素材搜索客户端及固定搜索 API 白名单同步使用 `https://api.bocha.cn/v1/web-search`，请求字段、凭证隔离和发布日期规则不变。
- DuckDuckGo 明确挑战用已有 `web_content_challenge` 诊断，failure_kind 为 `blocked_by_policy`；该请求标记 host 受限，避免在 `both` 等情况下再次发到同一站点。
- HTTP 200 混有搜索/空结果标识时，挑战仍优先；真正空结果仍成功；普通 429 或没有明确挑战标识的 202 保留 `rate_limit`。挑战本身不产生 quota 交接。
- 新增回归覆盖当前官方端点、证书/主机名校验保持开启且验证失败不重试、200/202 挑战、混合内容、同一请求零重复联网、扫描诊断、空结果和错误正文不外泄。独立安装的 Bocha 请求断言同步更新。

## 少量真实补测

以下三条均通过正式 `search_materials(providers=['web'])` 路径、固定单引擎运行，正文预算 0，总候选预算 3；使用此前失败的原系统 Python 3.9.6，保持 TLS 校验。标题是供应商结果，不代表相关性充分或财务事实已经验证。

| Engine | Language | SDK status | Scan state | Count | Seconds | First title | Published date |
|---|---|---|---|---:|---:|---|---|
| bocha | zh | partial | queried | 3 | 0.181 | [三季报]宁德时代(300750):2026年三季度报告- CFi.CN 中财网 | 2026-10-20T22:11:52+08:00 |
| bocha | en | partial | queried | 2 | 0.201 | NVIDIA 发布 2026 财年第四季度及全年财务报告_硬件动态_智能制造新闻_新闻_e-works数字化企业网 | 2026-02-26T00:00:00+08:00 |
| duckduckgo | en | unavailable | web_content_challenge | 0 | 1.292 | - | unknown |

博查英文返回结果包含其他财年材料；这次证明 TLS 和搜索接口恢复，不把结果相关性升级为验收通过。返回 `partial` 来自有界/非穷尽搜索和零正文读取，`scans.state=queried` 才是该引擎搜索完成证据。

本轮本机端点验证共 8 次请求（不含官方文档查阅）：4 次无凭证 HEAD、1 次 DuckDuckGo 原始诊断 GET、2 次带凭证博查搜索、1 次 DuckDuckGo 修复后 SDK GET。没有重复刷搜索、自动购买额度或调用其他付费引擎。两次博查和 DuckDuckGo SDK 返回均做已知凭证回显检查，通过；供应商原始错误正文没有输出。

## 结果和未完成验收

博查 TLS 修复及中英文搜索补测：通过。DuckDuckGo 挑战识别、诊断和同一请求停止重复访问：通过；DuckDuckGo 在线搜索可用性：未通过，保留 `unavailable`。

首轮 A 项仍未全部通过（当前 9/10 家具有双语有限成功样本）；B 项此前开发测试回显事件不能撤销，本轮未新增泄露；C 仍待正常使用 3 天或 10 次研究调用；D 仍待 b-mac 验证。本轮没有修改凭证文件、系统网络设置或其他电脑。

最终 `python3 -m pytest`：**2330 passed、25 skipped、8 warnings，42.00 秒，exit 0**（独立 Python 3.12 测试环境、剥离真实凭证、空测试 env）。包含 11 项新增回归及 `tests/test_standalone_package.py` 三项独立安装/SDK/MCP 检查；没有为修复单独添加运行依赖。

仓库变更和本轮补测输出的已知凭证扫描通过，`git diff --check` 通过。修复将本地提交，未 merge/push。
