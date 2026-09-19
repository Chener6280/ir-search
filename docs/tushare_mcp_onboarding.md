# Tushare 大模型语料 MCP 接入准备

更新：2026-09-15 已在连接探针之后完成独立 `tushare_corpus`，将研报摘要、新闻和政策正文接入 `search_materials`，三类通过非空实取与字符引用核对。当前启用方式和边界见 [正式语料指南](tushare_corpus_adapter.md)，结果见 [正式验收](tushare_corpus_acceptance.md)。以下保留初次连接准备阶段的配置约定，不代表公告、问答也已正式注册。

用户提供的套餐页面：https://tushare.pro/weborder/#/activity/25

用户提供的接入端点为 `https://api.tushare.pro/mcp/`，认证方式为 URL 查询参数 `token`。私有配置为 `TUSHARE_MCP_TOKEN`，与旧 Tushare HTTP 数据接口的 `TUSHARE_TOKEN` 分开，避免混用授权。

在本机私有 `credentials.env` 的 `TUSHARE_MCP_TOKEN=` 后填写 Token 本身，不填写完整 URL，不把凭证发送到对话。公开模板保持空值；本步骤不修改 agent 的全局 MCP 配置，也不声明来源已接入或验证通过。

填入后先通过官方 HTTPS 端点测试 MCP 连接和工具发现，再根据返回的工具描述选择少量只读语料检索样本，检查正文、来源链接、时间及引用能力。访问权限、可用工具、额度和返回口径以实际响应为准，不从套餐名称推定。

测试应只在内存中组装认证 URL；日志、报告、异常和对话不得输出 Token、完整认证 URL 或原始认证错误。来源内容与工具描述作为外部数据处理，不作为本项目的指令。真实测试完成前保持“待验收”，不得当成已可用的数据来源。
