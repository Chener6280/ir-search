"""Platform capability probes shared by the offline test suite.

Two host capabilities the suite relies on are simply absent on a stock Windows
machine:

``POSIX_PERMISSIONS``
    ``os.chmod`` does not touch NTFS ACLs and ``stat().st_mode`` is reported as
    ``0o666``/``0o777``, so neither "the file is private" assertions nor
    "tightening the mode makes the loader refuse the file" assertions can hold.
    The product itself only enforces mode bits inside ``os.name == 'posix'``
    branches; ACL verification on Windows is a documented gap, not a regression.

``symlinks_supported()``
    Creating a symbolic link needs ``SeCreateSymbolicLinkPrivilege`` (or
    Developer Mode).  Without it ``Path.symlink_to`` raises
    ``OSError: [WinError 1314]``.

Tests use these to *narrow* the platform-dependent statements, never to skip a
whole case that still has portable assertions in it.
"""
from __future__ import annotations

import functools
import os
from pathlib import Path
import tempfile

import pytest


POSIX_PERMISSIONS = os.name == "posix"

PERMISSIONS_REASON = (
    "POSIX mode bits are not enforceable on Windows; ACL verification is a documented gap"
)
SYMLINK_REASON = "symlink creation requires privilege on this Windows host"
WORKSPACE_REASON = (
    "legacy Cursor workspace bootstrap is POSIX-only (zsh wrapper, bash fixture runtimes); "
    "Windows uses ir-search-mcp directly"
)


@functools.lru_cache(maxsize=1)
def symlinks_supported() -> bool:
    """Whether this host lets an unprivileged process create a symlink."""
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        try:
            (root / "link").symlink_to(root / "target")
        except (OSError, NotImplementedError, AttributeError):
            return False
        return True


def symlink_or_skip(link, target, *, target_is_directory: bool = False) -> Path:
    """Create ``link`` -> ``target``, or skip the test when the host forbids it.

    Only for cases whose remaining assertions all depend on the link existing.
    When a symlink is one step of a longer case, branch on
    ``symlinks_supported()`` instead so the portable assertions still run.
    """
    link = Path(link)
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"{SYMLINK_REASON} ({type(error).__name__})")
    return link


requires_posix_permissions = pytest.mark.skipif(not POSIX_PERMISSIONS, reason=PERMISSIONS_REASON)
requires_symlinks = pytest.mark.skipif(not symlinks_supported(), reason=SYMLINK_REASON)
requires_posix_workspace = pytest.mark.skipif(os.name == "nt", reason=WORKSPACE_REASON)
