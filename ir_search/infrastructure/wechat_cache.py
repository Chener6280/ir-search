"""Private, bounded WeChat snapshots; no credentials or raw vendor envelopes.

One cooperative cross-process lock coalesces concurrent reads on each computer.
Snapshots are never substituted for failed refreshes or presented as fresh reads.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import time

from .credentials import credentials_path, read_credentials
from .private_files import _oldest_first, _stamp_written


@dataclass
class _WechatCache:
    root: Path = field(repr=False)
    clock: object = field(default=time.time, repr=False)
    warning: str | None = field(default=None, init=False)

    def _ready(self):
        try:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            info = self.root.lstat()
            if not stat.S_ISDIR(info.st_mode) or (os.name == 'posix' and
                    (info.st_uid != os.getuid() or info.st_mode & 0o077)):
                raise OSError()
            return True
        except OSError:
            self.warning = 'wechat_cache_unavailable'
            return False

    def _path(self, kind, key):
        digest = hashlib.sha256((kind + ':' + key).encode()).hexdigest()
        return self.root / (digest + '.json')

    @contextmanager
    def _guard(self, context):
        context.check_active()
        fd = None
        locked = False
        try:
            if self._ready():
                fd = os.open(self.root / '.lock', os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or (os.name == 'posix' and
                        (info.st_uid != os.getuid() or info.st_mode & 0o077)):
                    raise OSError()
                if os.name == 'nt':
                    import msvcrt
                    if not info.st_size:
                        # A concurrent holder may already have written and locked this byte;
                        # Windows then refuses the write. That is contention, not a broken cache.
                        try: os.write(fd, b'0')
                        except PermissionError: pass
                while not locked:
                    context.check_active()
                    try:
                        if os.name == 'nt':
                            os.lseek(fd, 0, os.SEEK_SET)
                            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        locked = True
                    except (BlockingIOError, PermissionError):
                        time.sleep(min(0.05, context.remaining_seconds()))
        except OSError:
            self.warning = 'wechat_cache_unavailable'
        except BaseException:
            if fd is not None: os.close(fd)
            raise
        try:
            yield locked
        finally:
            if fd is not None:
                if locked:
                    if os.name == 'nt':
                        os.lseek(fd, 0, os.SEEK_SET)
                        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)

    def _get(self, kind, key, ttl):
        fd = None
        try:
            fd = os.open(self._path(kind, key), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > 8 * 1024 * 1024 or (os.name == 'posix' and
                    (info.st_uid != os.getuid() or info.st_mode & 0o077)):
                raise ValueError()
            with os.fdopen(fd, 'r', encoding='utf-8') as stream:
                fd = None
                record = json.load(stream)
            if record['version'] != 1 or not 0 <= self.clock() - record['stored_at'] < ttl:
                return None
            return record['value']
        except FileNotFoundError:
            return None
        except (OSError, ValueError, TypeError, KeyError, UnicodeError):
            self.warning = 'wechat_cache_invalid'
            return None
        finally:
            if fd is not None: os.close(fd)

    def _put(self, kind, key, value):
        temporary = None
        try:
            raw = json.dumps({'version': 1, 'stored_at': self.clock(), 'value': value}, ensure_ascii=False).encode()
            if len(raw) > 8 * 1024 * 1024: raise ValueError()
            fd, temporary = tempfile.mkstemp(dir=self.root, prefix='.write-')
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw)
            os.replace(temporary, self._path(kind, key))
            _stamp_written(self._path(kind, key))
            # Bound local storage. Missing old entries simply cause normal fresh reads.
            entries = _oldest_first(self.root.glob('*.json'))
            sizes = [path.lstat().st_size for path in entries]
            total = sum(sizes)
            for index, path in enumerate(entries):
                if len(entries) - index <= 512 and total <= 128 * 1024 * 1024: break
                path.unlink()
                total -= sizes[index]
        except (OSError, ValueError, TypeError):
            self.warning = 'wechat_cache_unavailable'
        finally:
            if temporary:
                try: os.unlink(temporary)
                except OSError: pass


def _default_cache(root=None):
    if root is None:
        values = read_credentials()
        root = values.get('WECHAT_CACHE_DIR') or '.local/wechat-cache'
    path = Path(root).expanduser()
    if not path.is_absolute(): path = credentials_path().absolute().parent / path
    return _WechatCache(path)
