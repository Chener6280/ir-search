"""Local archive CLI; audio ASR requires an explicit --allow-network flag."""
from __future__ import annotations

import argparse
import json
import os
import re

from .services.local_archive import (index_local_archive, local_archive_status,
    search_local_archive, read_local_archive, parse_local_archive)
from .services.local_audio import list_local_audio, prepare_local_audio, transcribe_local_audio


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError('invalid_arguments')


def main(argv=None):
    parser = Parser(description=__doc__)
    parser.add_argument('--root', default=os.environ.get('IR_SEARCH_LOCAL_ARCHIVE_ROOT'))
    commands = parser.add_subparsers(dest='command', required=True, parser_class=Parser)
    commands.add_parser('status')
    commands.add_parser('index')
    search = commands.add_parser('search')
    search.add_argument('--query', default='')
    search.add_argument('--source', default='')
    search.add_argument('--collection-id', default='')
    search.add_argument('--kind', choices=['record', 'attachment', 'audio'], default='')
    search.add_argument('--start')
    search.add_argument('--end')
    search.add_argument('--limit', type=int, default=30)
    search.add_argument('--offset', type=int, default=0)
    search.add_argument('--include-unknown-dates', action='store_true')
    read = commands.add_parser('read')
    read.add_argument('document_id')
    read.add_argument('--offset', type=int, default=0)
    read.add_argument('--max-chars', type=int, default=20000)
    parse = commands.add_parser('parse')
    parse.add_argument('--limit', type=int, default=1)
    parse.add_argument('--id', dest='document_ids', action='append', default=[])
    audio_list = commands.add_parser('audio-list')
    audio_list.add_argument('--limit', type=int, default=30)
    audio_list.add_argument('--offset', type=int, default=0)
    audio_prepare = commands.add_parser('audio-prepare')
    audio_prepare.add_argument('document_id')
    audio_transcribe = commands.add_parser('audio-transcribe')
    audio_transcribe.add_argument('document_id')
    audio_transcribe.add_argument('--max-seconds', type=int, default=60)
    audio_transcribe.add_argument('--allow-network', action='store_true')
    audio_transcribe.add_argument('--retry-failed', action='store_true')
    try:
        args = vars(parser.parse_args(argv))
        root, action = args.pop('root'), args.pop('command')
        if not root:
            raise ValueError('archive_root_required')
        operation = {'status': local_archive_status, 'index': index_local_archive,
                     'search': search_local_archive, 'read': read_local_archive,
                     'parse': parse_local_archive, 'audio-list': list_local_audio,
                     'audio-prepare': prepare_local_audio, 'audio-transcribe': transcribe_local_audio}[action]
        result = operation(root, **args)
        status = 'partial' if result.get('status') == 'partial' else 'ok'
        result = {'schema_version': 1, 'status': status, 'result': result}
        code = 3 if status == 'partial' else 0
    except Exception as exc:
        message = getattr(exc, 'code', str(exc))
        safe_code = message if re.fullmatch('[a-z_]{3,80}', message) else 'local_archive_failure'
        result = {'schema_version': 1, 'status': 'error', 'code': safe_code, 'error_type': type(exc).__name__,
                  'next_action': 'stop_and_report', 'retry_automatically': False}
        code = 2
    print(json.dumps(result, ensure_ascii=False))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
