"""Opt-in local parsing worker. No hosted OCR, browser, or LLM calls."""
from __future__ import annotations

from importlib.metadata import version
import json
from pathlib import Path
import sys
import time

PARSER_REVISION = 'local-parser-v1'
OFFICE_EXTENSIONS = {'.doc', '.docx', '.docm', '.ppt', '.pptx', '.xls', '.xlsx', '.xlsm', '.xlsb', '.rtf', '.epub', '.odt', '.ods', '.odp'}


def parse_bytes(raw, filename):
    """Return plain text plus accurate PDF page spans; never synthesize content."""
    if len(raw) > 200 * 1024 * 1024:
        return {'status': 'too_large', 'text': '', 'pages': []}
    before = time.monotonic()
    suffix = Path(filename).suffix.lower()
    try:
        if raw.startswith(b'%PDF-') or suffix == '.pdf':
            import fitz
            parts, pages, image_only = [], [], []
            offset = 0
            with fitz.open(stream=raw, filetype='pdf') as pdf:
                if pdf.needs_pass:
                    return {'status': 'encrypted', 'text': '', 'pages': []}
                if len(pdf) == 0:
                    return {'status': 'invalid_document', 'text': '', 'pages': []}
                for number, page in enumerate(pdf, 1):
                    text = page.get_text('text').strip()
                    if not text and page.get_images():
                        image_only.append(number)
                    piece = f'Page {number}\n{text}\n\n'
                    pages.append({'page': number, 'start': offset, 'end': offset + len(piece)})
                    parts.append(piece)
                    offset += len(piece)
                plain = ''.join(parts)
            return {'status': 'needs_ocr' if image_only else 'parsed', 'text': plain,
                    'pages': pages, 'unread_image_pages': image_only, 'parser': 'pymupdf',
                    'parser_version': version('PyMuPDF'), 'elapsed_ms': round((time.monotonic()-before)*1000, 2),
                    'warnings': ['pdf_charts_not_interpreted', 'tables_not_structurally_verified']}
        if suffix not in OFFICE_EXTENSIONS:
            return {'status': 'unsupported_format', 'text': '', 'pages': []}
        import anydoc
        text = anydoc.to_markdown_bytes(raw)
        return {'status': 'parsed' if text.strip() else 'empty', 'text': text, 'pages': [],
                'parser': 'anydoc', 'parser_version': version('firecrawl-anydoc'),
                'elapsed_ms': round((time.monotonic()-before)*1000, 2),
                'warnings': ['source_locator_unverified', 'embedded_images_not_ocr'] +
                            (['spreadsheet_formulas_not_recalculated'] if suffix in {'.xls', '.xlsx', '.xlsm', '.xlsb', '.ods'} else [])}
    except ImportError:
        return {'status': 'parser_missing', 'text': '', 'pages': []}
    except Exception as exc:
        code = 'needs_ocr' if type(exc).__name__ == 'NeedsOcrError' else 'parse_failed'
        return {'status': code, 'text': '', 'pages': [], 'error_type': type(exc).__name__}


def main():
    # Defense in depth for Python networking. The selected native APIs are
    # local-only; never pass anydoc's hosted OCR option or remote URLs.
    import socket
    def blocked(*args, **kwargs):
        raise RuntimeError('local_parser_network_disabled')
    socket.socket.connect = blocked
    socket.create_connection = blocked
    path, filename, output = sys.argv[1:4]
    result = parse_bytes(Path(path).read_bytes(), filename)
    if len(result.get('text', '')) > 8_000_000:
        result = {'status': 'text_size_exceeded', 'text': '', 'pages': []}
    Path(output).write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    main()
