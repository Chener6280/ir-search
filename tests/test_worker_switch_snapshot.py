"""Browser workers get a one-line snapshot of the fake-ip opt-in, never the real credentials file.

No network and no browser: real child interpreters are started exactly like the workers are, but
they only report which file they would read.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from _platform import POSIX_PERMISSIONS
from ir_search import diagnose_sources
from ir_search.infrastructure import credentials, public_web, web_browser as browser
from ir_search.services import source_diagnostics

KEY = "PUBLIC_WEB_FAKE_IP_HTTPS"
SECRETS = "XUEQIU_COOKIE=synthetic_private_value\nEXA_API_KEY=synthetic_private_key\n"
KEEP = {"PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "WINDIR", "LANG", "LC_ALL", "PLAYWRIGHT_BROWSERS_PATH"}
ADDED = {"PYTHON_DOTENV_DISABLED", "LITELLM_LOCAL_MODEL_COST_MAP", "CRAWL4_AI_BASE_DIRECTORY", "PYTHONIOENCODING",
         "IR_SEARCH_CREDENTIALS_FILE"}


def parent_file(text):
    """Rewrite the parent's private credentials file (the per-test file made by conftest)."""
    path = Path(os.environ["IR_SEARCH_CREDENTIALS_FILE"])
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)
    return path


def worker_directory(tmp_path, name="worker"):
    directory = tmp_path / name
    directory.mkdir()
    return str(directory)


def snapshot_of(env):
    return Path(env["IR_SEARCH_CREDENTIALS_FILE"]).read_text(encoding="ascii")


# --- what the snapshot holds ---

def test_snapshot_is_one_boolean_line_in_the_worker_directory_and_nothing_else(tmp_path):
    real = parent_file(SECRETS + f"{KEY}=true\n")
    directory = worker_directory(tmp_path)
    env = browser._environment(directory)
    snapshot = Path(env["IR_SEARCH_CREDENTIALS_FILE"])
    assert snapshot.parent == Path(directory) and snapshot.name == browser._SWITCH_SNAPSHOT
    assert snapshot != real and str(real) not in env.values()
    assert snapshot.read_bytes() == f"{KEY}=true\n".encode("ascii")
    assert "synthetic_private" not in json.dumps(env) and os.listdir(directory) == [browser._SWITCH_SNAPSHOT]
    if POSIX_PERMISSIONS:
        assert snapshot.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("text", ["", f"{KEY}=false\n", f"{KEY}=TRUE\n", f"{KEY}=1\n", f"{KEY}=\n", f"{KEY}=yes\n"])
def test_absent_false_and_invalid_values_all_freeze_as_false(tmp_path, text):
    parent_file(SECRETS + text)
    assert snapshot_of(browser._environment(worker_directory(tmp_path))) == f"{KEY}=false\n"


def test_unreadable_parent_credentials_freeze_as_false_without_failing_the_worker(tmp_path, monkeypatch):
    monkeypatch.setenv("IR_SEARCH_CREDENTIALS_FILE", str(tmp_path / "missing.env"))
    assert snapshot_of(browser._environment(worker_directory(tmp_path))) == f"{KEY}=false\n"


def test_the_process_environment_cannot_put_true_into_a_snapshot(tmp_path, monkeypatch):
    parent_file(SECRETS)
    monkeypatch.setenv(KEY, "true")
    env = browser._environment(worker_directory(tmp_path))
    assert snapshot_of(env) == f"{KEY}=false\n" and KEY not in env


# --- frozen when the worker starts ---

def test_value_is_frozen_at_start_and_only_the_next_worker_sees_an_edit(tmp_path):
    parent_file(f"{KEY}=true\n")
    first = browser._environment(worker_directory(tmp_path, "first"))
    parent_file(f"{KEY}=false\n")  # the user edits credentials.env while the first worker runs
    assert snapshot_of(first) == f"{KEY}=true\n"
    second = browser._environment(worker_directory(tmp_path, "second"))
    assert snapshot_of(second) == f"{KEY}=false\n"
    parent_file(f"{KEY}=true\n")
    assert snapshot_of(first) == f"{KEY}=true\n" and snapshot_of(second) == f"{KEY}=false\n"
    assert snapshot_of(browser._environment(worker_directory(tmp_path, "third"))) == f"{KEY}=true\n"


def test_an_entry_already_in_the_worker_directory_is_never_reused(tmp_path):
    parent_file(f"{KEY}=false\n")
    directory = worker_directory(tmp_path)
    planted = Path(directory) / browser._SWITCH_SNAPSHOT
    planted.write_text(f"{KEY}=true\n", encoding="ascii")
    with pytest.raises(FileExistsError):
        browser._environment(directory)
    assert planted.read_text(encoding="ascii") == f"{KEY}=true\n"  # left alone, and no worker was given it


# --- the allow-list grew by exactly one key ---

def test_only_the_credentials_pointer_was_added_to_the_worker_environment(tmp_path, monkeypatch):
    for key in ("WIND_PASSWORD", "OPENAI_API_KEY", "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY", "PYTHONPATH",
                "PYTHONSTARTUP", "NODE_OPTIONS", "IR_SEARCH_OUTPUT_ROOT", "IR_SEARCH_MCP_MODE", KEY):
        monkeypatch.setenv(key, "must_not_escape")
    env = browser._environment(worker_directory(tmp_path))
    assert "must_not_escape" not in json.dumps(env)
    inherited = set(env) - ADDED
    assert ADDED <= set(env) and {key.upper() for key in inherited} <= KEEP
    assert all(os.environ[key] == env[key] for key in inherited)


# --- a real child, started like the workers, in both deployment forms ---

CHILD = ("import sys, json; sys.path.insert(0, sys.argv.pop(1)); "
         "from ir_search.infrastructure.credentials import credentials_path, read_credentials; "
         "from ir_search.infrastructure.public_web import _fake_ip_https_enabled; "
         "values = read_credentials(); "
         "print(json.dumps({'path': str(credentials_path()), 'count': len(values), "
         f"'only_the_switch': list(values) == ['{KEY}'], 'enabled': _fake_ip_https_enabled()}}))")


def start_child(package_root, directory):
    env = browser._environment(directory)
    done = subprocess.run([sys.executable, "-I", "-c", CHILD, str(package_root)], cwd=directory, env=env,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]
    return env, json.loads(done.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def installed_copy(tmp_path_factory):
    """The package copied next to no pyproject.toml, like a copy in site-packages."""
    root = tmp_path_factory.mktemp("site")
    checkout = Path(credentials.__file__).resolve().parents[2]
    shutil.copytree(checkout / "ir_search", root / "ir_search", ignore=shutil.ignore_patterns("__pycache__"))
    assert not (root / "pyproject.toml").exists()
    return root


@pytest.mark.parametrize("form", ["source_checkout", "installed_copy"])
@pytest.mark.parametrize("value", ["true", "false"])
def test_a_real_worker_reads_the_snapshot_in_both_deployment_forms(tmp_path, installed_copy, form, value):
    parent_file(SECRETS + f"{KEY}={value}\n")
    root = Path(credentials.__file__).resolve().parents[2] if form == "source_checkout" else installed_copy
    if form == "source_checkout" and not (root / "pyproject.toml").is_file():
        pytest.skip("the package under test is not running from a source checkout")
    env, seen = start_child(root, worker_directory(tmp_path))
    assert seen == {"path": env["IR_SEARCH_CREDENTIALS_FILE"], "count": 1, "only_the_switch": True,
                    "enabled": value == "true"}


def test_a_running_worker_keeps_its_value_while_a_new_one_gets_the_edit(tmp_path, installed_copy):
    parent_file(f"{KEY}=true\n")
    first_directory = worker_directory(tmp_path, "first")
    first_env, first = start_child(installed_copy, first_directory)
    parent_file(f"{KEY}=false\n")
    # Ask the first worker's environment again: what it reads has not moved.
    again = subprocess.run([sys.executable, "-I", "-c", CHILD, str(installed_copy)], cwd=first_directory,
                           env=first_env, capture_output=True, text=True, timeout=120)
    assert first["enabled"] is True and json.loads(again.stdout.strip().splitlines()[-1])["enabled"] is True
    assert start_child(installed_copy, worker_directory(tmp_path, "second"))[1]["enabled"] is False


# --- doctor: prints the resolution, changes nothing ---

def test_doctor_reports_this_process_and_the_worker_without_writing_anything(tmp_path, monkeypatch):
    real = parent_file(SECRETS + f"{KEY}=true\n")
    monkeypatch.setattr(browser, "_switch_snapshot", lambda directory: pytest.fail("the doctor starts no worker"))
    before = sorted(p.name for p in tmp_path.iterdir())
    report = diagnose_sources(["web"])["credentials_resolution"]
    assert report["this_process"] == {"path": str(real), "selected_by": "IR_SEARCH_CREDENTIALS_FILE", "exists": True}
    assert report["this_process"]["path"] == str(credentials.credentials_path())
    worker = report["browser_worker"]
    assert worker["path"].endswith(browser._SWITCH_SNAPSHOT) and "<worker temporary directory>" in worker["path"]
    assert worker["selected_by"] == "IR_SEARCH_CREDENTIALS_FILE" and worker["reads_real_credentials_file"] is False
    assert worker["snapshot_keys"] == [KEY] and worker["snapshot_value_if_started_now"] == "true"
    assert worker["frozen_at"] == "worker_start" and "code" not in worker
    assert "synthetic_private" not in json.dumps(report) and sorted(p.name for p in tmp_path.iterdir()) == before


def test_doctor_agrees_with_what_a_snapshot_taken_now_would_hold(tmp_path):
    for text, expected in ((f"{KEY}=true\n", "true"), (f"{KEY}=false\n", "false"), ("", "false"), (f"{KEY}=TRUE\n", "false")):
        parent_file(text)
        told = source_diagnostics._credentials_resolution()["browser_worker"]["snapshot_value_if_started_now"]
        held = snapshot_of(browser._environment(worker_directory(tmp_path, "w" + str(len(list(tmp_path.iterdir()))))))
        assert held == f"{KEY}={told}\n" and told == expected


def test_doctor_names_the_key_only_when_the_value_is_the_problem(tmp_path, monkeypatch):
    parent_file(f"{KEY}=TRUE\n")
    worker = source_diagnostics._credentials_resolution()["browser_worker"]
    assert (worker["snapshot_value_if_started_now"], worker["code"], worker["key"]) == ("false", "source_config_error", KEY)
    monkeypatch.setenv("IR_SEARCH_CREDENTIALS_FILE", str(tmp_path / "missing.env"))
    report = source_diagnostics._credentials_resolution()
    worker = report["browser_worker"]
    assert worker["snapshot_value_if_started_now"] == "false" and worker["code"] == "source_config_error"
    assert worker["detail_code"] == "credentials_file_missing" and "key" not in worker
    assert report["this_process"]["exists"] is False


def test_doctor_names_the_lookup_rule_the_same_way_the_lookup_itself_decides(monkeypatch):
    monkeypatch.delenv("IR_SEARCH_CREDENTIALS_FILE")
    # Without the variable the lookup points at the checkout's own file: report its path, never open it.
    monkeypatch.setattr(public_web, "_fake_ip_https_enabled", lambda: False)
    this_process = source_diagnostics._credentials_resolution()["this_process"]
    checkout = Path(credentials.__file__).resolve().parents[2]
    assert this_process["path"] == str(credentials.credentials_path())
    expected = "source_checkout" if (checkout / "pyproject.toml").is_file() else "working_directory"
    assert this_process["selected_by"] == expected


def test_doctor_status_is_not_changed_by_the_new_section():
    parent_file("")
    clean = diagnose_sources(["web"])
    parent_file(f"{KEY}=TRUE\n")
    invalid = diagnose_sources(["web"])
    assert clean["status"] == invalid["status"] and clean["diagnostics"] == invalid["diagnostics"]
    assert invalid["automatic_configuration_changes"] is False
