# 文章结构与本地归档

2026-09-16。本轮参考 [podcast-summary 的 wechat-to-md](https://github.com/hxer7963/podcast-summary/tree/main/.codebuddy/skills/wechat-to-md) 的“正文、元数据、图片分别保存”设计，在 `ir_search` 内独立实现。运行时不安装、不执行、不依赖该 Skill 或个人桌面目录，不加入摘要、ASR 或研究结论功能。

## 公众号正文修复

部分微信文章将唯一正文容器 `div#js_content.rich_media_content` 初始设置为 `visibility:hidden; opacity:0`，但正文已在 HTTP 响应中。本项目现在识别这项初始化样式并提取已有正文，返回 `wechat_initial_visibility_recovered`。

例外只适用于上述容器：`display:none`、`hidden`、`aria-hidden`、隐藏父节点、其他隐藏子元素、脚本、模板和验证页仍排除；多个正文容器报结构异常。`img/br/meta` 等空元素不参与配对栈，避免误把页脚、推荐阅读和导航拼入正文。浏览器与供应商保留为后续读取途径。

正文缓存改用新解析版本命名空间。升级后不会把旧版缺少结构的快照当成新结果，首次调用可能重新取数；后续继续遵守 24 小时正文缓存、日期和来源诊断。

## 文本和阅读结构

公众号的 `search_materials` 版本、`retrieve` 素材新增 `article` 字段：

| 字段 | 含义 |
|---|---|
| `text_hash` | 对应返回纯文本的 SHA-256 |
| `blocks` | 段落、标题、列表项、引文、预格式文本、表格行、图片；文字块附精确字符范围 |
| `images` | 原图 URL、替代文字、位置、`not_downloaded` 和 `ocr_status=not_requested` |
| `links` | 合格的原文超链接，`discovered_not_retrieved`，同时放入顶层 `links` |
| `markdown` | 阅读版；不作为字符引用坐标的依据 |
| `metadata` | 标题、公众号、作者、发布时间、原始链接；发布者身份仍未独立验证 |
| `scope` | 微信正文容器，或供应商提供的 HTML 片段 |
| `structure_truncated` / `links_truncated` | 结构或链接达到限制，不等于完整页面归档 |

`text` 与 `evidence_spans` 仍为引用基准，Markdown 格式变化不移动其坐标。表格保留行、单元格、表头及正整数跨行/列属性，阅读版不是网页像素级还原；表格内图片的锚点标为所在行之后。图片未 OCR，不能把图中数值作为已提取数据。短文字加大量图片的文章仍有取材缺口。

最多保留 2,000 个结构块、100 个图片位置与 100 个链接，文本遵守调用者长度上限。正文截断时一同截断文字结构及对应阅读版；原文链接是发现元数据，不代表链接内容已阅读。带凭证、内网或非 HTTP(S) 的链接不会进入图片下载路径。

## SDK 与 MCP

既有 `retrieve` 工具增加三个可选参数，不传时不创建归档、不下载图片：

```python
from ir_search import MaterialRequest, RequestContext, retrieve

bundle = retrieve(
    MaterialRequest(
        question="哪些数据支持文章观点？",
        urls=(article_url,),
        max_chars=50000,
        archive_dir="./research-archive",
        archive_images=False,
        max_archive_images=10,
    ),
    context=RequestContext(timeout_seconds=60, max_operations=100),
)
```

- `archive_dir`：显式指定专用私有目录；自动创建，POSIX 目录 700、文件 600。相对路径以调用进程工作目录为准。
- `archive_images`：默认 false；true 才下载发现的公开图片。
- `max_archive_images`：每篇 0–20 张去重后的图片，默认 10。

MCP `retrieve` 使用同名参数，返回素材的 `archive` 包含状态、目录、版本、图片请求数、成功张数与诊断。也可先选定素材，随后调用 SDK：

```python
from ir_search import export_material
saved = export_material(bundle.materials[0], "./research-archive", download_images=True, max_images=10)
```

归档器可供所有已返回的 `Material` 复用，包括公开网页等来源；只有公众号当前提供这里描述的丰富文章结构，其他来源默认导出既有文字及元数据。不会因导出而重新搜索或重新请求正文，也不会调用 LLM。

## 归档与增量复用

```text
research-archive/
  <文章地址哈希>/
    <内容、引用与导出选项版本哈希>/
      article.md
      README.md
      material.json
      manifest.json
      images/                 # 显式下载时才创建
```

- 地址和版本参与目录标识，同名文章互不覆盖；内容或引用选择变化会生成新版本。
- `material.json` 保存提取文本、出处、日期、警告与引用；`article.md` 为阅读版。远程图片默认作为普通链接，下载成功才嵌入本地图片。
- `manifest.json` 保存文件 SHA-256、图片来源和状态。完整且校验通过的归档直接复用，返回 `reused` 和归档最初的抓取时间。
- 图片部分失败时返回 `partial`，下次可续传失败项；校验通过的成功图片不重复下载。已被手工修改或校验失败的归档不会被静默覆盖。
- 支持 PNG、JPEG、GIF、WebP；同时检查响应类型和文件签名。每图最多 8 MiB，每篇下载总预算 20 MiB，共享 `retrieve` 的总超时和操作次数；每跳重定向重新校验地址，不携带供应商 Key 或 Cookie。
- 达到数量、体积或时间限制时保留可用正文和明确诊断。未执行图片 OCR、音视频转写或摘要。
- 本地归档是长期用户资料，不随缓存过期而删除；用户管理其空间和保留期限。缓存、归档与凭证都不随本项目的 wheel/Git 发布。

实际样本、免费直读比例及独立安装结果见[本轮验收](wechat_rich_acceptance.md)。
