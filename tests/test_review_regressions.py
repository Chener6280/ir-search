"""Independent PR #2 review probes. Synthetic inputs; no source calls or real credentials."""
import base64
from dataclasses import replace
import hashlib
import hmac
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from ir_search import DataRequest, MaterialRegistry, MaterialSearchRequest, RequestContext, search_materials, next_material_request
from ir_search import mcp_server
from ir_search.infrastructure import pagination
from ir_search.infrastructure.credentials import MySQLProfile
from ir_search.registry import DataAdapterError
import test_material_source_isolation as fixtures


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    # Private empty fixture; never inspect the user's actual credentials file.
    path = tmp_path / 'synthetic.env'
    path.write_text('# synthetic offline fixture\n')
    path.chmod(0o600)
    monkeypatch.setenv('IR_SEARCH_CREDENTIALS_FILE', str(path))
    monkeypatch.setattr(pagination, '_KEYS', {})


@pytest.mark.parametrize('call', [
    lambda value: mcp_server.get_data_payload({'dataset': 'prices_daily', 'value_kind': value}),
    lambda value: mcp_server.get_data_payload({'dataset': 'prices_daily', 'as_of': value}),
    lambda value: mcp_server.search_materials_payload({'question': 'q', 'material_types': [value]}),
])
def test_validation_details_do_not_echo_values(call):
    marker = 'SYNTHETIC_PRIVATE_MARKER_12345'
    result = call(marker)
    assert result['diagnostics'][0]['code'] == 'invalid_request'
    assert marker not in json.dumps(result)


def test_concurrent_first_use_keeps_one_signing_key(tmp_path):
    program = """
import sys
from ir_search.infrastructure import pagination as p
real = p.secrets.token_hex
def paused(n):
    print('READY', flush=True)
    assert sys.stdin.readline().strip() == 'continue'
    return real(n)
p.secrets.token_hex = paused
print(p._cursor_key().hex(), flush=True)
"""
    creator = subprocess.Popen([sys.executable, '-c', program], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    reader = None
    try:
        assert creator.stdout.readline().strip() == 'READY'
        reader = subprocess.Popen([sys.executable, '-c',
            'from ir_search.infrastructure.pagination import _cursor_key; print(_cursor_key().hex())'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        output, errors = creator.communicate('continue\n', timeout=10)
        assert creator.returncode == 0, errors
        other, errors = reader.communicate(timeout=10)
        assert reader.returncode == 0, errors
        disk = (tmp_path / '.local/state/cursor.key').read_text(encoding="utf-8")
        assert output.strip() == other.strip() == disk
    finally:
        for process in (creator, reader):
            if process is not None and process.poll() is None:
                process.kill(); process.communicate()


def test_old_password_signed_cursor_is_rejected_but_new_cursor_roundtrips():
    profile = MySQLProfile('wind_mysql', 'db.example.test', 'wind', 'reader', 'synthetic_password')
    request = DataRequest('prices_daily', symbols=('600519.SH',), start='2026-01-01', end='2026-01-31')
    last = ['600519.SH', '2026-01-05']
    raw = json.dumps({'v': 1, 'request': pagination._binding(request, profile), 'last': last}, separators=(',', ':')).encode()
    old = base64.urlsafe_b64encode(raw).decode() + '.' + hmac.new(profile.password.encode(), raw, hashlib.sha256).hexdigest()
    with pytest.raises(DataAdapterError, match='invalid_cursor'):
        pagination._decode_cursor(replace(request, cursor=old), profile)
    new = pagination._encode_cursor(request, profile, last)
    assert pagination._decode_cursor(replace(request, cursor=new), profile) == last


def test_documented_output_root_boundary_and_sdk_unchanged(tmp_path, monkeypatch):
    from ir_search import record_material_run
    root = tmp_path / 'exports'
    monkeypatch.setenv('IR_SEARCH_OUTPUT_ROOT', str(root))
    payload = mcp_server.search_materials_payload({'question': 'q'}, registry=MaterialRegistry(), audit_dir='runs')
    assert payload['audit']['status'] == 'recorded'
    assert Path(payload['audit']['path']).is_relative_to(root)
    for value in ('../outside', str(tmp_path / 'outside')):
        payload = mcp_server.search_materials_payload({'question': 'q'}, registry=MaterialRegistry(), audit_dir=value)
        assert payload['diagnostics'][0]['code'] == 'invalid_request'
    result = search_materials(MaterialSearchRequest('q'), registry=MaterialRegistry())
    assert record_material_run(result, tmp_path / 'sdk-anywhere')['status'] == 'recorded'


def test_source_share_keeps_already_read_material_and_continuation():
    from ir_search.infrastructure.material_cursor import _continuation
    clock = fixtures.Clock()
    class PartialSource(fixtures.Source):
        def search_materials(self, request, *, context):
            page = super().search_materials(request, context=context)
            page.complete = False
            page.continuation_cursors = [_continuation(request, 'wechat', 'account', '', '',
                                                       [{'id': '1'}, {'id': '2'}], 1, '', True, context)]
            clock.now += 16  # 30-second parent still has 14s, 15-second source slice expired
            try:
                context.check_active()
            except Exception as exc:
                from ir_search import Diagnostic
                page.diagnostics.append(Diagnostic(exc.code, 'search_materials', 'wechat'))
            return page
    result = fixtures.search([PartialSource('wechat'), fixtures.Source('source_b', rows=[])],
                             context=RequestContext(timeout_seconds=30, _clock=clock))
    assert fixtures.states(result)['wechat']['state'] == 'source_time_share_exceeded'
    assert (bool(result.items), next_material_request(result) is not None) == (True, True), 'Evidence and cursor disappeared'


def test_real_wechat_adapter_keeps_history_when_body_read_uses_share():
    import test_wechat_materials as wc
    clock = fixtures.Clock()
    def read(url, **kwargs):
        clock.now += 16
        kwargs['context'].check_active()
        return wc.doc(url)
    first = wc.WechatHistoryPage((wc.row(),), wc.ACCOUNT.name, wc.ACCOUNT.ghid, 'next-page', True, wc.NOW)
    registry, client, _ = wc.setup(profile=replace(wc.PROFILE, max_pages_per_account=1), pages=[first], reader=read)
    registry.register(fixtures.Source('source_b', rows=[]))
    result = search_materials(wc.request(providers=['wechat', 'source_b']), registry=registry,
                              context=RequestContext(timeout_seconds=30, _clock=clock))
    assert len(client.calls) == 1
    assert (bool(result.items), next_material_request(result) is not None) == (True, True)


def test_one_invalid_record_does_not_drop_other_records():
    good = fixtures.candidate('source_a', 1)
    bad = fixtures.candidate('source_a', 2)
    object.__setattr__(bad, 'text', None)  # emulate malformed adapter payload at the validation boundary
    result = fixtures.search([fixtures.Source('source_a', rows=[good, bad])])
    assert result.items, 'Page-level candidate validation still rejects the whole source'
    assert fixtures.states(result)['source_a']['rejected_count'] == 1


@pytest.mark.parametrize('url', ['https://abc.de/report', 'https://cafe.de/report'])
def test_legitimate_domain_syntax_not_confused_with_numeric_address(url):
    from ir_search.documents.safety import is_url_allowed
    assert is_url_allowed(url).allowed


def test_unknown_tilde_user_is_structured_input_error(tmp_path, monkeypatch):
    monkeypatch.setenv('IR_SEARCH_OUTPUT_ROOT', str(tmp_path / 'exports'))
    result = mcp_server.search_materials_payload({'question': 'q'}, registry=MaterialRegistry(),
                                               audit_dir='~ir_search_nonexistent_review_user/runs')
    assert result['diagnostics'][0]['code'] == 'invalid_request'


def test_mtime_order_survives_another_process(tmp_path, monkeypatch):
    from ir_search.infrastructure import private_files as files
    older, newer = tmp_path / 'older', tmp_path / 'newer'
    for _ in range(2000):
        files._private_write(older, b'{}')
    code = 'from pathlib import Path; from ir_search.infrastructure.private_files import _private_write; import sys; _private_write(Path(sys.argv[1]), b"{}")'
    subprocess.run([sys.executable, '-c', code, str(newer)], check=True, capture_output=True)
    assert files._oldest_first([older, newer]) == [older, newer]


def test_old_64_hex_image_manifest_is_still_reused(tmp_path, monkeypatch):
    import test_archive_path_length as archive_test
    from ir_search.services.material_archive import export_material
    material = archive_test.prepared(monkeypatch)
    result = export_material(material, tmp_path / 'archive', download_images=True)
    root = Path(result['directory'])
    path = root / 'manifest.json'
    manifest = json.loads(path.read_text(encoding="utf-8"))
    for image in manifest['images']:
        if image['status'] != 'downloaded':
            continue
        old, full = image['path'], 'images/' + image['sha256'] + Path(image['path']).suffix
        (root / old).rename(root / full)
        image['path'] = full
        manifest['files'][full] = manifest['files'].pop(old)
        markdown = root / 'article.md'
        markdown.write_text(markdown.read_text(encoding="utf-8").replace(old, full), encoding="utf-8")
        manifest['files']['article.md'] = hashlib.sha256(markdown.read_bytes()).hexdigest()
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert export_material(material, tmp_path / 'archive', download_images=True)['status'] == 'reused'


def test_partial_lexical_matches_can_have_no_industry_discriminator():
    from ir_search.services.material_search import _partial_match, _plan
    for question in ['有色金属', '对冲', '在建工程', 'H20']:
        plan = _plan(MaterialSearchRequest(question))
        assert question in plan['topic_terms']
    assert _partial_match('有色金属需求', '黑色金属需求增加') is None
    assert _partial_match('对冲基金收益', '公募基金收益下滑') is None
    assert _partial_match('在建工程减值', '新建工程竣工') is None


def test_timing_changes_only_observational_fingerprint():
    from copy import deepcopy
    from ir_search.services.material_runs import _summary
    result = fixtures.search([fixtures.Source('source_a')])
    other = deepcopy(result)
    other.timing['elapsed_ms'] += 1
    assert result.items == other.items and result.coverage == other.coverage
    assert _summary(result)['result_fingerprint'] != _summary(other)['result_fingerprint']


def test_ci_annotation_replay(tmp_path):
    import yaml
    if os.name == 'nt': pytest.skip('Shell annotation is exercised by GitHub bash on Windows')
    workflow = Path(fixtures.__file__).resolve().parents[1] / '.github/workflows/standalone.yml'
    steps = yaml.safe_load(workflow.read_text(encoding="utf-8"))['jobs']['package']['steps']
    script = next(step['run'] for step in steps if step.get('name') == 'Publish failing tests as annotations')
    script = script.replace('${{ matrix.os }}', 'macos-latest').replace('${{ matrix.utf8 }}', '1')
    (tmp_path / 'pytest-report.log').write_bytes(b'FAILED tests/test_x.py::test_percent - 20% failure\nERROR tests/test_y.py::test_read\n')
    reply = subprocess.run(['/bin/bash', '-c', script], cwd=tmp_path, capture_output=True)
    assert reply.returncode == 0
    assert b'20%25 failure' in reply.stdout
    assert b'\r' not in reply.stdout, repr(reply.stdout)
    assert b'%0A' in reply.stdout


def test_windows_close_branch_releases_a_socket_with_makefile_owner(monkeypatch):
    import socket
    from types import SimpleNamespace
    from ir_search.infrastructure import _interrupt
    reader, writer = socket.socketpair()
    buffered = reader.makefile('rb')  # both HTTPResponse and PyMySQL do this
    monkeypatch.setattr(_interrupt, 'os', SimpleNamespace(name='nt'))
    try:
        _interrupt.wake_blocked_socket(reader)
        assert reader.fileno() == -1, 'close() deferred the actual OS close while _io_refs > 0'
    finally:
        buffered.close()
        reader.close()
        writer.close()


def test_archive_does_not_classify_disk_full_as_path_too_long(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace
    import errno
    import test_wechat_rich_articles as rich
    from ir_search.services import material_archive as archive
    material = rich.material(monkeypatch)
    # A root where text paths fit MAX_PATH; no images requested.
    root = tmp_path / ('x' * max(1, 145 - len(str(tmp_path))))
    @contextmanager
    def disk_full(*args):
        raise OSError(errno.ENOSPC, 'synthetic disk full')
        yield
    monkeypatch.setattr(archive, 'os', SimpleNamespace(name='nt'))
    monkeypatch.setattr(archive, '_directory_lock', disk_full)
    result = archive.export_material(material, root, download_images=False)
    assert result['diagnostics'] == ['archive_validation_or_io_failed']


def test_equal_share_skips_wechat_browser_even_with_parent_budget():
    from ir_search.context import SourceSlice
    import test_wechat_savings as wc
    options = dict(transport=wc.transport(wc.LOADING), client=wc.Client(), mode='auto',
                   renderer=lambda url, **kw: wc.RenderedPage(url, wc.HTML, wc.NOW, {}))
    parent = RequestContext(timeout_seconds=30)
    whole, full_details = wc.wc._fetch_uncached(wc.URL, context=parent, **options)
    with pytest.raises(DataAdapterError, match='wechat_browser_budget_insufficient'):
        wc.wc._fetch_uncached(wc.URL, context=SourceSlice(RequestContext(timeout_seconds=30), 10), **options)
    assert full_details['text_provider'] == 'wechat_browser' and full_details['vendor_body_calls'] == 0


def test_ci_crash_before_summary_produces_an_annotation(tmp_path):
    import yaml
    if os.name == 'nt': pytest.skip('Shell annotation is exercised by GitHub bash on Windows')
    workflow = Path(fixtures.__file__).resolve().parents[1] / '.github/workflows/standalone.yml'
    steps = yaml.safe_load(workflow.read_text(encoding="utf-8"))['jobs']['package']['steps']
    script = next(step['run'] for step in steps if step.get('name') == 'Publish failing tests as annotations')
    script = script.replace('${{ matrix.os }}', 'macos-latest').replace('${{ matrix.utf8 }}', '1')
    (tmp_path / 'pytest-report.log').write_text('Fatal Python error: Aborted\n')
    reply = subprocess.run(['/bin/bash', '-c', script], cwd=tmp_path, capture_output=True)
    assert reply.returncode == 0 and b'::error' in reply.stdout


@pytest.mark.parametrize('key,value', [('DAJIALA_KEY', ''), ('WECHAT_MAX_PAGES_PER_ACCOUNT', '4'),
                                     ('WECHAT_MAX_ACCOUNTS_PER_QUERY', 'not_a_number')])
def test_wechat_configuration_names_the_actual_bad_key(tmp_path, key, value):
    from ir_search.infrastructure.credentials import wechat_profile, SourceConfigError
    inventory = tmp_path / 'accounts.json'
    inventory.write_text('[{"name":"synthetic research account"}]')
    inventory.chmod(0o600)
    values = {'WECHAT_MATERIALS_ENABLED': 'true', 'WECHAT_ACCOUNTS_FILE': str(inventory),
              'DAJIALA_KEY': 'synthetic_key_123456', key: value}
    with pytest.raises(SourceConfigError) as caught:
        wechat_profile(values=values)
    assert caught.value.key == key
