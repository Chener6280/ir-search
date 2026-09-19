"""Optional renderer lifecycle, isolated environment and bounded worker protocol."""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

def _browser_status():
    try:
        installed = version("crawl4ai")
    except PackageNotFoundError:
        installed = None
    return {"installed_version": installed, "supported_version": "0.9.3", "python_supported": sys.version_info >= (3, 10),
            "dependency_ready": installed == "0.9.3" and sys.version_info >= (3, 10),
            "runtime_verified": False, "verification_basis": "local_metadata_only_not_browser_or_network_probe"}

from ir_search.context import RequestStopped
from ir_search.documents.html import extract_html_document
from ir_search.infrastructure.public_web import _url, _allowed_domain
from ir_search.registry import DataAdapterError


def _environment(directory):
    # Allowlist; no dotenv, provider/model credentials, proxy settings or Python hooks.
    keep = {"PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "WINDIR", "LANG", "LC_ALL", "PLAYWRIGHT_BROWSERS_PATH"}
    env = {key: value for key, value in os.environ.items() if key.upper() in keep}
    env.update(PYTHON_DOTENV_DISABLED="1", LITELLM_LOCAL_MODEL_COST_MAP="True",
               CRAWL4_AI_BASE_DIRECTORY=directory, PYTHONIOENCODING="utf-8")
    return env


def _stop(process):
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif process.poll() is None:
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
    process.wait(timeout=5)


def _run_worker(url, *, context, allowed_domains):
    context.begin_operation()
    remaining = context.max_operations - context.operations
    if remaining < 1:
        raise RequestStopped("operation_budget_exhausted")
    # This path derives from the installed package, never a personal checkout/skill.
    package_root = str(Path(__file__).resolve().parents[2])
    bootstrap = "import sys; sys.path.insert(0, sys.argv.pop(1)); from ir_search.infrastructure._crawl_worker import main; main()"
    with tempfile.TemporaryDirectory(prefix="ir-search-browser-") as directory:
        output = Path(directory) / "result.json"
        payload = {"url": url, "allowed_domains": list(allowed_domains), "timeout": context.remaining_seconds(),
                   "operations": remaining, "output": str(output)}
        process = subprocess.Popen([sys.executable, "-I", "-c", bootstrap, package_root], cwd=directory,
            env=_environment(directory), stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=os.name == "posix", creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
        accounted = False
        try:
            process.stdin.write(json.dumps(payload).encode())
            process.stdin.close()
            while process.poll() is None:
                context.check_active()
                if output.exists() and output.stat().st_size > 52 * 1024 * 1024:
                    raise DataAdapterError("response_too_large")
                time.sleep(0.05)
            context.check_active()
            if not output.exists() or output.stat().st_size > 52 * 1024 * 1024:
                raise DataAdapterError("browser_failed")
            data = json.loads(output.read_text(encoding="utf-8"))
            operations = data.get("operations")
            if type(operations) is not int or not 0 <= operations <= remaining:
                raise DataAdapterError("browser_failed")
            context.operations += operations
            accounted = True
            code = data.get("error")
            if code in {"cancelled", "deadline_exceeded", "operation_budget_exhausted"}:
                raise RequestStopped(code)
            if code:
                raise DataAdapterError(code if code in DataAdapterError.KINDS else "browser_failed")
            return data
        finally:
            _stop(process)
            if not accounted:
                # Unknown child consumption after interruption conservatively consumes reservation.
                context.operations += remaining


@dataclass(frozen=True)
class RenderedPage:
    url: str
    html: str = field(repr=False)
    fetched_at: datetime
    diagnostics: dict = field(default_factory=dict)


def render_public_html(url: str, *, context, allowed_domains=()):
    """Render public HTML for a source-specific parser with the same network controls."""
    parsed, host, _ = _url(url)
    if not isinstance(allowed_domains, tuple) or any(not isinstance(d, str) or not d for d in allowed_domains):
        raise ValueError("Invalid domain restriction")
    if parsed.scheme != "https" or not _allowed_domain(host, allowed_domains):
        raise DataAdapterError("blocked_url")
    context.check_active()
    try:
        installed = version("crawl4ai")
    except PackageNotFoundError:
        raise DataAdapterError("browser_dependency_missing") from None
    if sys.version_info < (3, 10) or installed != "0.9.3":
        raise DataAdapterError("browser_version_unsupported")
    try:
        data = _run_worker(url, context=context, allowed_domains=allowed_domains)
        final_url = data["url"]
        final, final_host, _ = _url(final_url)
        if final.scheme != "https" or not _allowed_domain(final_host, allowed_domains):
            raise DataAdapterError("blocked_url")
        html = data["html"].encode("utf-8")
        if len(html) > 8 * 1024 * 1024:
            raise DataAdapterError("response_too_large")
        diagnostics = data.get("diagnostics", {})
        if not isinstance(diagnostics, dict): raise DataAdapterError("browser_failed")
        context.check_active()
        return RenderedPage(final_url, data["html"], datetime.now(timezone.utc), diagnostics)
    except (DataAdapterError, RequestStopped):
        raise
    except Exception:
        raise DataAdapterError("browser_failed") from None


def render_public_document(url: str, *, context, allowed_domains=()):
    """Render one public HTTPS document in a disposable, credential-free worker."""
    page = render_public_html(url, context=context, allowed_domains=allowed_domains)
    try:
        document = extract_html_document(page.html.encode('utf-8'), page.url, max_chars=100000)
    except (DataAdapterError, RequestStopped):
        raise
    except Exception:
        raise DataAdapterError('browser_failed') from None
    document.extraction_method = "crawl4ai_render_stdlib_html_parser"
    document.extra.update(adapter_mode="live", browser_diagnostics=page.diagnostics)
    if page.diagnostics.get("blocked_requests"):
        document.warnings.append("web_browser_requests_blocked")
    if page.diagnostics.get("warnings"):
        document.warnings.append("web_browser_has_diagnostics")
    return document
