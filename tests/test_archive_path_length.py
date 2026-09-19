"""Archive paths must stay usable under the 260-character Windows limit, and say so when they cannot."""
import os
from pathlib import Path

import pytest

import test_wechat_rich_articles as rich
from ir_search.services.material_archive import export_material


def prepared(monkeypatch):
    html = rich.HTML.replace('</div><p>推荐', '<img src="' + rich.IMAGE + '"></div><p>推荐')
    material = rich.material(monkeypatch, html)

    def image(url, **kw):
        kw['context'].begin_operation()
        return rich._Reply(200, 'image/png', '', b'\x89PNG\r\n\x1a\nfixture', rich.NOW)
    monkeypatch.setattr(rich.archive, '_request', image)
    return material


def test_new_image_names_are_short_and_the_manifest_keeps_the_full_digest(tmp_path, monkeypatch):
    import json
    result = export_material(prepared(monkeypatch), tmp_path / 'a', download_images=True)
    assert result['status'] == 'ok'
    manifest = json.loads((Path(result['directory']) / 'manifest.json').read_text(encoding='utf-8'))
    (record,) = [image for image in manifest['images'] if image['status'] == 'downloaded']
    name = Path(record['path']).stem
    assert len(name) == 32 and record['sha256'].startswith(name) and len(record['sha256']) == 64
    assert manifest['files'][record['path']] == record['sha256']
    assert export_material(prepared(monkeypatch), tmp_path / 'a', download_images=True)['status'] == 'reused'


@pytest.mark.skipif(os.name != 'nt', reason='MAX_PATH is a Windows limit')
def test_a_root_that_cannot_fit_the_archive_is_reported_as_such(tmp_path, monkeypatch):
    deep = tmp_path
    while len(str(deep)) < 200:
        deep = deep / ('d' * 20)
    try:
        deep.mkdir(parents=True)
    except OSError:
        pytest.skip('cannot create a long directory on this host')
    result = export_material(prepared(monkeypatch), deep / 'archive', download_images=True)
    if result['status'] == 'ok':
        pytest.skip('this host has OS long-path support enabled')
    assert result['diagnostics'] == ['archive_path_too_long']
