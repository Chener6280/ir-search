# 用户选择素材来源

适用版本：0.2.0rc1。`search_materials` 不设置默认来源组合，不在服务端猜测用户希望查询哪家。

## 调用契约

1. 用户直接说“查公众号和星球”，或已在当前任务选择该组合，调用方使用 `providers=['wechat','zsxq']`；无需重复确认。
2. 用户未选择时，可调用 `list_capabilities` 查看来源，或省略 `search_materials.providers` 获得 `plan.source_options`。后者返回 `unavailable`、`required_inputs` 中的 `providers`、零条素材和零次来源调用；不是上游接口故障。
3. Agent 将来源名称与能力展示给用户。选项带 `material_types`、证券约束、账户范围内的注册状态和排除标志；不包含密钥，不声明在线可用。未注册来源在 `plan.unregistered_providers`，不可宣称可用。
4. 用户选定后，将非空列表传回。顺序就是来源预算内的处理顺序；不自动追加其他来源，不把失败换成另一家。用户选用但尚未配置的来源返回 `not_registered`。
5. `max_sources` 默认 4，最多 8。若选了更多来源，应由调用方设置合适预算或分批；超出预算的来源保留 `source_budget_exhausted`，不能说全部已查。

`providers=[]` 不表示全选；`exclude_providers` 单独使用也不会自动选择剩下的全部来源。`dry_run=true` 同样要求明确选择才生成来源执行计划，缺少选择时只展示选项。没有自动存储用户偏好的服务端会话；跨任务偏好由调用方自己的配置保存。

## 迁移

旧调用依赖省略 `providers` 自动检索的，需要补入**用户实际选定的来源**。不要为兼容而在 SDK/MCP 包装器里偷偷填入全部已启用来源。MCP 参数仍允许省略，以便返回结构化选择提示而不是协议校验异常。诊断码为 `material_source_selection_required`。

这是有意的行为变化，版本标记为测试候选 0.2.0rc1。数据集 `get_data` 的既有 Wind/JYDB 等路由不变；`retrieve` 已由显式 URL/引用确定来源，不需要新增选择步骤。网页发现引擎的配置仍只在用户选择 `web` 后生效；网页额度耗尽的 Agent 接续遵循[单独契约](web_search_fallback.md)。
