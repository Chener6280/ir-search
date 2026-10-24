"""The doctor states which private-file checks this platform actually performs."""
import os

import ir_search
from ir_search.services import source_diagnostics
from ir_search.services.source_diagnostics import diagnose_sources


def test_report_says_whether_owner_and_mode_bits_are_verified():
    note = diagnose_sources(["web"])["private_file_protection"]
    assert note["platform"] == os.name and note["owner_and_mode_bits_verified"] is (os.name == "posix")
    windows = source_diagnostics._private_file_protection("nt")
    assert windows["code"] == "permissions_not_verified_on_this_platform" and "icacls" in windows["next_action"]
    assert source_diagnostics._private_file_protection("posix") == {"platform": "posix", "owner_and_mode_bits_verified": True}


def test_package_exposes_its_installed_version():
    from importlib.metadata import version
    assert ir_search.__version__ == version("ir-search") and ir_search.__version__[0].isdigit()
