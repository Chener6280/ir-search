# FMP 免费套餐首轮验收

本页保留首轮账号探测的历史结论。后续正式 SDK/MCP adapter 已完成并验收，当前状态见 [FMP 正式验收](fmp_adapter_acceptance.md)。

时间：2026-09-15 05:25:54–05:26:03 UTC。用户填入私有 env 后执行，使用其声明的免费套餐，最多五次串行只读请求，无重试、无重定向、无升级或购买操作。

**结论：Key 有效，五个 AAPL 样本接口均可用。** 这证明当前账户对本轮接口/标的有权限，不等于已验收全部海外市场、所有字段或长期可用率。没有单独查询账户套餐名称或剩余额度。

| 检查 | 官方 Stable 接口 | HTTP / 行数 | 结果 |
|---|---|---|---|
| 认证与公司资料 | profile | 200 / 1 | AAPL 公司资料，USD |
| 日终基础行情 | historical-price-eod/light | 200 / 5 | 09-08 至 09-14 的价格、成交量，均落在请求范围内 |
| 利润表 | income-statement | 200 / 2 | 2024、2025 财年，年度 FY，reportedCurrency=USD |
| 资产负债表 | balance-sheet-statement | 200 / 2 | 同上；两年资产减负债减股东权益均为零 |
| 现金流量表 | cash-flow-statement | 200 / 2 | 同上；两年经营现金流加带符号资本开支等于自由现金流 |

日线请求区间为 2026-09-07 至 09-14，返回日期无重复、无越界，价格为正、成交量非负。选取截止日时仅按纽约日期的上一工作日计算，未把工作日推算冒充交易所日历。

三表期间与币种相互对应：2025 财年报告期结束于 2025-09-27，2024 财年结束于 2024-09-28；它们不是自然年 12 月 31 日。供应商分别返回 filingDate=2025-10-31、2024-11-01，以及 acceptedDate 字段；本轮未独立核实其时区、历史修订或 PIT 可得性。内部恒等式成立也不等于已对照 SEC 原件审计。

## 对正式 adapter 的影响

- 当前免费权限足以先开发美股公司资料、基础日线和年度财务的首版，但本次只测试 AAPL，不推定其他股票或其他国家都免费可用。
- Light 行情只有 date/price/volume，不能伪装成完整 OHLC；响应未提供币种，价格复权基础尚未核验。正式映射前需确定这些口径，并保留来源限制。[FMP Light 接口说明](https://site.financialmodelingprep.com/developer/docs/stable/historical-price-eod-light)
- 财务接口保留 reportedCurrency、财年、报告期、filingDate、acceptedDate；净利润字段不能未经核对就映射成国内定义的归母净利润。当前字段来自标准化供应商报表，不直接等同于公告原文。[FMP 财务接口文档](https://site.financialmodelingprep.com/developer/docs/stable/income-statement)
- 本轮当时仅完成账号与样本验收，尚未注册到公共 get_data / MCP 能力目录；此后已完成注册，见上方正式验收链接。后续付费升级后再扩大已验证能力，不写死最高套餐权限。

## 凭证与记录

Key 只从本地 credentials.env 读取，HTTPS 连接仅指向 FMP 官方 API 域名；控制台和报告不输出 Key、带 apikey 的 URL 或原始认证错误。响应标明 fmp、live、data_vendor、data_table，source_tier=null；不是交易所原始记录。

去敏后的详细字段样本和检查结果保存在 Git 忽略的 `.local/fmp-free-first-acceptance.json`，权限 0600；仅存选择的字段，不发布完整供应商数据。本轮恰好发起五次 API 请求；不能由此推算整个账号的剩余额度。私有验收脚本拒绝无意重复运行；它是临时验收工具，不是安装包运行时依赖。
