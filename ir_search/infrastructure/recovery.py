"""Reviewed operational rules, never instructions learned from source text."""
from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class _Recovery:
    category: str
    next_action: str
    stop_source: bool = False
    automatic_retry: bool = False


_RULES = {}


def _rule(codes, category, action, stop=False):
    for code in codes.split(): _RULES[code] = _Recovery(category, action, stop)


_rule('authentication_failed no_credential xhs_login_required video_captions_login_required', 'authentication',
      '检查本机凭证或由本人重新登录，再显式发起请求。', True)
_rule('web_content_challenge alphapai_login_challenge gangtise_login_challenge sec_access_blocked',
      'human_action', '在平台允许的登录或验证窗口完成本人验证，再重新读取。', True)
_rule('quota rate_limit', 'access_or_limit',
      '检查访问权限或供应商限额；限流后等待恢复再发起新请求。', True)
_rule('entitlement_denied', 'scope_permission',
      '当前资源权限不足；保留缺口，仅继续调用方已选择且独立授权的其他资源。')
_rule('tls_error tls_not_supported blocked_url browser_robots_denied', 'transport_policy',
      '检查目标、传输配置和平台访问条件，保持现有安全边界。', True)
_rule('fake_ip_blocked', 'transport_policy',
      '本机 DNS 返回的是代理 fake-ip 模式的占位地址（198.18.0.0/15），公网地址校验因此拦截了本次连接；'
      '这是本机网络环境所致，不是目标站点的问题。由本人在私有 credentials.env 中设置 PUBLIC_WEB_FAKE_IP_HTTPS=true 后，'
      '只有 ir-search 进程自己直接发出、并做证书与主机名校验的 HTTPS/WSS 连接可以放行：网页与接口读取、浏览器模式网页读取、'
      '语音转写，以及 Firecrawl 读取对 https 目标的本地核对。以下情形不适用该开关，开启后仍报本错误码：'
      '明文 http 目标（含交给 Firecrawl 的 http 目标及其报告的 http 最终地址）；雪球浏览器读取（隧道连接不由本进程做 TLS，尚未接入）。', True)
_rule('timeout network xhs_backend_unavailable browser_unavailable', 'availability',
      '检查服务是否运行及网络状况；保留已得结果，按诊断决定下一次有界请求。', True)
_rule('cancelled deadline_exceeded operation_budget_exhausted browser_budget_exhausted', 'budget',
      '使用已返回的续取位置继续，或缩小范围；需要时显式调整下次预算。', True)
_rule('archive_path_too_long', 'configuration',
      '归档路径超过 Windows 的 260 字符上限：把归档根目录改到更短的路径（如用户目录下的一层文件夹），或在系统中启用长路径支持。')
_rule('source_time_share_exceeded', 'budget',
      '该来源用完了本次请求分给它的时间份额，其余来源已继续执行；需要时单独查询该来源或显式增大 timeout_seconds。')
_rule('candidate_rejected', 'schema',
      '个别记录未通过契约校验已被丢弃，同一来源的其余记录已保留；结合 rejected_count 判断是否需要核对上游结构。')
_rule('dependency_missing browser_dependency_missing browser_version_unsupported video_dependency_missing audio_dependency_missing',
      'dependency', '在当前运行 ir-search 的 Python 环境安装对应可选依赖，并重新验收。', True)
_rule('source_config_error invalid_source_config unsafe_credentials_file source_disabled', 'configuration',
      '检查私有 credentials.env 中的启用开关、字段和权限。', True)
_rule('invalid_cursor material_cursor_stale xhs_reference_unavailable', 'resume',
      '使用相同查询和账户的有效续取位置；快照或访问令牌失效时重新搜索。', True)
_rule('no_extracted_text video_captions_unavailable video_caption_language_unavailable web_content_image_only',
      'content_scope', '保留元数据及缺失原因；只对实际取得的正文或字幕生成引用。')
_rule('video_caption_url_unavailable', 'content_scope',
      '平台返回了字幕条目但没有下载地址；保留视频元数据和缺口，不将简介或搜索摘要当作字幕。')
_rule('video_caption_timing_mismatch', 'content_scope',
      '字幕时间超出所选视频分段时长；保留元数据并核对平台原文，不发布不一致的字幕引用。')
_rule('upstream_schema invalid_material_response xhs_backend_error browser_failed', 'schema',
      '保存脱敏运行摘要，核对上游结构并补回归样本后修复。', True)


def _recovery(code):
    return asdict(_RULES.get(code, _Recovery('inspect_diagnostics',
        '结合来源覆盖、内容范围和失败诊断判断下一步。')))


def _stop_source(code):
    return bool(_RULES.get(code) and _RULES[code].stop_source)
