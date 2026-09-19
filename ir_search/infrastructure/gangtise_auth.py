"""Private account-bound state and optional interactive Gangtise authentication."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time

from ir_search.context import RequestContext
from ir_search.registry import DataAdapterError
from .gangtise import GangtiseProfile, gangtise_profile
from .web_browser import _environment, _stop

_LIMIT = 32768


class _State:
    def __init__(self, profile):
        self.key = hashlib.sha256(('gangtise\0'+profile.phone+'\0'+profile.password).encode()).digest()
        base = Path(profile.state_dir).expanduser() if profile.state_dir else Path.home()/'.cache'/'ir-search'/'gangtise'
        self.root = base / hmac.new(self.key, b'account', hashlib.sha256).hexdigest()

    def prepare(self):
        try:
            for p in (self.root, *self.root.parents):
                if p.is_symlink(): raise ValueError()
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            info = self.root.stat()
            if not stat.S_ISDIR(info.st_mode) or (os.name == 'posix' and
                    (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077)): raise ValueError()
        except (OSError, ValueError): raise DataAdapterError('gangtise_state_unavailable') from None

    @staticmethod
    def _bytes(data):
        return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()

    def read(self):
        self.prepare(); fd = None
        try:
            fd = os.open(self.root/'session.json', os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > _LIMIT or (os.name == 'posix' and
                    (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077)): raise ValueError()
            with os.fdopen(fd, 'rb') as stream:
                fd = None; envelope = json.loads(stream.read(_LIMIT+1))
            data = envelope['record']
            if not isinstance(data, dict) or not hmac.compare_digest(envelope['hmac'], hmac.new(self.key, self._bytes(data), hashlib.sha256).hexdigest()): raise ValueError()
            return data
        except FileNotFoundError: return {}
        except (OSError, ValueError, KeyError, TypeError, RecursionError): raise DataAdapterError('gangtise_state_invalid') from None
        finally:
            if fd is not None: os.close(fd)

    def write(self, data):
        self.prepare(); temporary = None
        try:
            raw = self._bytes({'record': data, 'hmac': hmac.new(self.key, self._bytes(data), hashlib.sha256).hexdigest()})
            if len(raw) > _LIMIT: raise ValueError()
            fd, temporary = tempfile.mkstemp(dir=self.root, prefix='.write-')
            with os.fdopen(fd, 'wb') as stream: stream.write(raw)
            os.replace(temporary, self.root/'session.json'); temporary = None
        except (OSError, ValueError, TypeError): raise DataAdapterError('gangtise_state_unavailable') from None
        finally:
            if temporary:
                try: os.unlink(temporary)
                except OSError: pass

    @contextmanager
    def lock(self):
        self.prepare(); path = self.root/'login.lock'
        fd = None
        try:
            if os.name == 'posix':
                import fcntl
                fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
                    raise DataAdapterError('gangtise_state_invalid')
                try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError: raise DataAdapterError('gangtise_login_busy') from None
            else:
                try: fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                except FileExistsError: raise DataAdapterError('gangtise_login_busy') from None
        except OSError:
            if fd is not None: os.close(fd)
            raise DataAdapterError('gangtise_state_unavailable') from None
        except DataAdapterError:
            if fd is not None: os.close(fd)
            raise
        try: yield
        finally:
            os.close(fd)
            if os.name != 'posix':
                try: path.unlink()
                except OSError: pass


def _token(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9._~+/-]{8,16000}={0,2}', value):
        raise DataAdapterError('authentication_failed')
    return value


def _browser_login(profile, state, *, context, interactive):
    from importlib.util import find_spec
    if find_spec('playwright') is None: raise DataAdapterError('browser_dependency_missing')
    context.begin_operation()
    with state.lock(), tempfile.TemporaryDirectory(prefix='ir-search-gangtise-') as directory:
        os.chmod(directory, 0o700)
        output = Path(directory)/'result.json'
        package = str(Path(__file__).resolve().parents[2])
        boot = 'import sys; sys.path.insert(0,sys.argv.pop(1)); from ir_search.infrastructure._gangtise_auth_worker import main; main()'
        env = _environment(directory); env['TMPDIR'] = directory
        process = subprocess.Popen([sys.executable, '-I', '-c', boot, package], cwd=directory,
            env=env, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=os.name == 'posix', creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0)
        try:
            profile_dir = state.root/'browser'
            if profile_dir.is_symlink(): raise DataAdapterError('gangtise_state_invalid')
            process.stdin.write(json.dumps({'phone': profile.phone, 'password': profile.password,
                'executable': profile.browser_executable, 'profile_dir': str(profile_dir),
                'interactive': interactive, 'timeout': context.remaining_seconds(), 'output': str(output)}).encode())
            process.stdin.close()
            while process.poll() is None:
                context.check_active()
                if output.exists() and output.stat().st_size > _LIMIT: raise DataAdapterError('response_too_large')
                time.sleep(.05)
            context.check_active()
            if not output.exists() or output.stat().st_size > _LIMIT: raise DataAdapterError('browser_failed')
            reply = json.loads(output.read_text())
            code = reply.get('error')
            if code:
                if code == 'gangtise_login_challenge': state.write({'needs_verification': True})
                raise DataAdapterError(code if code in DataAdapterError.KINDS else 'browser_failed')
            token = _token(reply.get('token'))
            state.write({'token': token, 'saved_at': time.time()})
            return token
        except (DataAdapterError,): raise
        except (ValueError, TypeError, OSError): raise DataAdapterError('browser_failed') from None
        finally: _stop(process)


def _get_token(profile, *, context):
    context.check_active()
    state = _State(profile); data = state.read()
    if data.get('needs_verification'): raise DataAdapterError('gangtise_login_challenge')
    saved = data.get('saved_at')
    if type(saved) in (float, int) and 0 <= time.time()-saved <= 3600:
        return _token(data.get('token'))
    return _browser_login(profile, state, context=context, interactive=False)


def _invalidate_token(profile, token):
    state = _State(profile)
    try:
        with state.lock():
            if state.read().get('token') == token: state.write({'needs_verification': True})
    except DataAdapterError: pass


def authenticate_gangtise(*, profile=None, timeout=300):
    """Open a normal browser for user-completed verification; return no credentials."""
    if type(timeout) is not int or not 30 <= timeout <= 300: raise ValueError('Timeout must be 30..300 seconds')
    profile = profile if profile is not None else gangtise_profile()
    if profile is None: raise DataAdapterError('no_credential')
    if not isinstance(profile, GangtiseProfile): raise ValueError('GangtiseProfile required')
    _browser_login(profile, _State(profile), context=RequestContext(timeout_seconds=timeout), interactive=True)
    return {'provider': 'gangtise', 'authentication': 'completed', 'verification_basis': 'browser_login',
            'material_access_verified': False, 'credentials_returned': False}


def main():
    """Local authentication command, with sanitized JSON status only."""
    import argparse
    from .credentials import SourceConfigError
    from ir_search.context import RequestStopped
    parser = argparse.ArgumentParser(description='Complete Gangtise login locally; never paste a verification code into agent chat.')
    parser.add_argument('--timeout', type=int, default=300)
    args = parser.parse_args()
    try: result = authenticate_gangtise(timeout=args.timeout)
    except (DataAdapterError, SourceConfigError, RequestStopped) as exc:
        result = {'provider': 'gangtise', 'authentication': 'incomplete', 'code': exc.code}
    except Exception: result = {'provider': 'gangtise', 'authentication': 'incomplete', 'code': 'source_config_error'}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get('authentication') == 'completed' else 1


if __name__ == '__main__': sys.exit(main())
