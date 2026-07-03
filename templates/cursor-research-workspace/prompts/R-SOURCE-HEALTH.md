# R-SOURCE-HEALTH

请调用 ir_search.source_health，并只回答当前 source 状态，不做市场或公司分析。

如果 Cursor 工具列表里没有 `ir_search.source_health`，不要改用 Python payload 或终端脚本替代 MCP 调用。请直接说明 `ir_search` MCP 未连接，并提示用户运行：

```bash
python scripts/doctor_ir_search_mcp.py --ir-search-python <python> --ir-search-path <ir-search>
```

请按以下结构输出：

1. live sources
2. mock sources
3. placeholder sources
4. unavailable / error sources
5. credentials status，只能显示 has_KEY true/false，不得显示实际 key
6. 对当前研究问题的影响
