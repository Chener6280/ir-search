#!/usr/bin/env python3
"""Backward-compatible checkout CLI; implementation lives inside ir_search."""
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ir_search.clients import wechat_articles as _implementation

if __name__ == "__main__":
    _implementation.main()
else:
    # Legacy imports/monkeypatches refer to the same module, with no duplicate code.
    sys.modules[__name__] = _implementation
