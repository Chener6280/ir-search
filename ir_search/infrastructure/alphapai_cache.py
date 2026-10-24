"""Authenticated private snapshots, isolated by account and credential rotation."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import stat
import tempfile

from ir_search.registry import DataAdapterError
from .private_files import _oldest_first, _stamp_written


class _DetailCache:
    def __init__(self, profile):
        self.profile = profile
        self._key = hashlib.sha256((profile.phone+'\0'+profile.password).encode()).digest()
        self.root = Path(profile.cache_dir).expanduser() if profile.cache_dir else Path.home()/'.cache'/'ir-search'/'alphapai'

    def _path(self, identifier):
        return self.root/(hmac.new(self._key, identifier.encode(), hashlib.sha256).hexdigest()+'.json')

    def _root(self):
        try:
            root = self.root.absolute()
            if any(p.is_symlink() for p in (root, *root.parents)): raise ValueError()
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
            info = root.stat()
            if not stat.S_ISDIR(info.st_mode): raise ValueError()
            if os.name == 'posix' and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077): raise ValueError()
        except (OSError, ValueError): raise DataAdapterError('alphapai_cache_unavailable') from None

    @staticmethod
    def _bytes(record):
        return json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()

    def get(self, identifier):
        from .alphapai import AlphapaiResponse, MAX_BYTES
        if not self.profile.cache_ttl_seconds: return None
        self._root(); fd = None
        try:
            fd = os.open(self._path(identifier), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES
                    or (os.name == 'posix' and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077))): raise ValueError()
            with os.fdopen(fd, 'rb') as file:
                fd = None; envelope = json.loads(file.read(MAX_BYTES+1))
            record, tag = envelope['record'], envelope['hmac']
            if not hmac.compare_digest(tag, hmac.new(self._key, self._bytes(record), hashlib.sha256).hexdigest()): raise ValueError()
            fetched = datetime.fromisoformat(record['fetched_at'])
            if fetched.utcoffset() is None or record['data'].get('_requested_id', record['data'].get('id')) != identifier: raise ValueError()
            age = (datetime.now(timezone.utc)-fetched).total_seconds()
            if age < 0 or age > self.profile.cache_ttl_seconds: return None
            return AlphapaiResponse(record['data'], fetched, 'hit')
        except FileNotFoundError: return None
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            raise DataAdapterError('alphapai_cache_invalid') from None
        finally:
            if fd is not None: os.close(fd)

    def put(self, identifier, response):
        from .alphapai import MAX_BYTES
        if not self.profile.cache_ttl_seconds: return
        self._root(); temporary = None
        try:
            # Bound storage. Evict oldest snapshots only; never follow symlinks.
            entries = _oldest_first(self.root.glob('*.json'))
            for path in entries[:max(0, len(entries)-199)]:
                if stat.S_ISREG(path.lstat().st_mode): path.unlink()
            record = {'fetched_at': response.fetched_at.isoformat(), 'data': response.data}
            raw = self._bytes({'record': record, 'hmac': hmac.new(self._key, self._bytes(record), hashlib.sha256).hexdigest()})
            if len(raw) > MAX_BYTES: raise ValueError()
            fd, temporary = tempfile.mkstemp(dir=self.root, prefix='.write-')
            with os.fdopen(fd, 'wb') as file: file.write(raw)
            os.replace(temporary, self._path(identifier)); temporary = None
            _stamp_written(self._path(identifier))
        except (OSError, ValueError, TypeError): raise DataAdapterError('alphapai_cache_unavailable') from None
        finally:
            if temporary:
                try: os.unlink(temporary)
                except OSError: pass
