# SearXNG 自建搜索源

SearXNG 在本项目中只定位为低成本 fallback discovery source。它用于发现公开网页候选 URL，不替代 `manual_wechat`、`wechat_opencli`、Bocha、Exa、Tavily 等主源，也不直接提供微信公众号正文、付费研报正文或登录后内容。

## 推荐位置

当前 fallback 链路：

```text
bocha -> anysearch -> searxng -> web_search
exa -> tavily -> anysearch -> searxng -> web_search
tavily -> anysearch -> searxng -> web_search
anysearch -> searxng -> web_search
searxng -> web_search
```

SearXNG 只在 `Query.allow_fallback=True` 且 fallback policy 允许时触发。默认 source routes 不包含 `searxng`，避免它悄悄替代主搜索源。

## 环境变量

```bash
export SEARXNG_ENABLED=true
export SEARXNG_URL=http://localhost:8080
export SEARXNG_TIMEOUT=10
export SEARXNG_MAX_RESULTS=10
export SEARXNG_ENGINES=bing,duckduckgo,brave
```

可选：

```bash
export SEARXNG_FAILURE_LOG=logs/searxng_failures.log
export SEARXNG_PROXY=http://127.0.0.1:7890
export SEARXNG_DISABLE_SYSTEM_PROXY=true
```

未设置 `SEARXNG_ENABLED=true` 时，adapter 会明确失败并继续后续 fallback，不会访问 SearXNG。

## Docker 自建

快速本地验证：

```bash
docker run -d \
  --name searxng \
  -p 8080:8080 \
  searxng/searxng
```

生产或长期使用时，需要单独维护 `settings.yml`，至少确认：

- JSON API 可用，搜索 URL 支持 `format=json`；
- limiter、timeout、rate limit 合理配置；
- engine 白名单只保留稳定可用的上游；
- 实例有访问控制，不直接依赖公共实例；
- 日志目录和网络代理按机器环境配置。

## 结果语义

SearXNG 返回的是搜索结果，不是正文证据。adapter 会把命中标记为：

```json
{
  "source": "searxng",
  "adapter_mode": "fallback",
  "coverage_status": "partial",
  "evidence_type": "search_result",
  "confidence": "low_to_medium"
}
```

只有后续正文抓取成功、时间窗口匹配、分析师或机构归属明确后，外层 coverage 流程才可以把它升级为 `covered`。仅凭 title 或 snippet 不能算作正式覆盖。

## 调用示例

显式测试 SearXNG：

```bash
SEARXNG_ENABLED=true \
SEARXNG_URL=http://localhost:8080 \
python -m ir_search "华泰证券 光模块 研报 2026" --source searxng --count 5
```

作为 fallback：

```bash
python -m ir_search "中际旭创 AI capex 最新观点" \
  --source bocha \
  --fallback-policy all
```

如需降低重复请求，可使用项目已有 query cache：

```bash
python -m ir_search "中际旭创 AI capex 最新观点" \
  --source bocha \
  --fallback-policy all \
  --cache-dir .cache/search
```
