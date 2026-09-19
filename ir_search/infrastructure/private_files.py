"""Small private-directory primitives for explicit local material exports."""
from contextlib import contextmanager
import os
from pathlib import Path
import stat
import tempfile
import time


def _private_dir(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or (os.name == 'posix' and
            (info.st_uid != os.getuid() or info.st_mode & 0o077)):
        raise OSError('unsafe_private_directory')
    return path


@contextmanager
def _directory_lock(root, context):
    root = _private_dir(root)
    fd = os.open(root/'.lock', os.O_CREAT | os.O_RDWR | getattr(os,'O_NOFOLLOW',0), 0o600)
    locked = False
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or (os.name == 'posix' and
                (info.st_uid != os.getuid() or info.st_mode & 0o077)):
            raise OSError('unsafe_lock')
        if os.name == 'nt':
            import msvcrt
            if not info.st_size: os.write(fd, b'0')
        else: import fcntl
        while not locked:
            context.check_active()
            try:
                if os.name == 'nt':
                    os.lseek(fd, 0, os.SEEK_SET); msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except (BlockingIOError, PermissionError): time.sleep(min(.05, context.remaining_seconds()))
        yield root
    finally:
        if locked:
            if os.name == 'nt':
                os.lseek(fd, 0, os.SEEK_SET); msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else: fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _private_read(path, limit=8*1024*1024):
    fd = os.open(path, os.O_RDONLY | getattr(os,'O_NOFOLLOW',0) | getattr(os,'O_NONBLOCK',0))
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit or (os.name == 'posix' and
                (info.st_uid != os.getuid() or info.st_mode & 0o077)):
            raise OSError('unsafe_private_file')
        with os.fdopen(fd, 'rb') as stream:
            fd = None
            return stream.read(limit+1)
    finally:
        if fd is not None: os.close(fd)


def _private_write(path, data):
    # Never follow a caller-controlled symlink, even though replace is atomic.
    if path.is_symlink(): raise OSError('unsafe_private_file')
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.write-')
    try:
        with os.fdopen(fd, 'wb') as stream: stream.write(data)
        os.replace(temporary, path)
    finally:
        try: os.unlink(temporary)
        except FileNotFoundError: pass
