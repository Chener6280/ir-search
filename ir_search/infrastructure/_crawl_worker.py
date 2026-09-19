"""Private, isolated Crawl4AI 0.9.3 renderer. Invoked only by web_browser.

Use Crawl4AI's rendering strategy, then ir_search's extractor; do not invoke its
scraping, pruning, HTTP fallback, model, cache, download or deep-crawl machinery.
"""
from __future__ import annotations

import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
from threading import Thread
from urllib.parse import urlsplit

from ir_search.context import RequestContext, RequestStopped
from ir_search.infrastructure._browser_network import BrowserNetwork
from ir_search.infrastructure.web_documents import _dynamic_directory
from ir_search.registry import DataAdapterError


class _DenyProxy(BaseHTTPRequestHandler):
    def do_CONNECT(self):
        self.send_error(403)

    do_GET = do_POST = do_OPTIONS = do_PUT = do_DELETE = do_CONNECT

    def log_message(self, *args):
        pass


def _launch_options(proxy):
    # Explicit audited options; never inherit Crawl4AI's certificate/no-sandbox flags.
    return {"headless": True, "chromium_sandbox": True,
            "proxy": {"server": proxy, "bypass": "<-loopback>"},
            "args": ["--disable-background-networking", "--disable-component-update", "--disable-quic",
                     "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                     "--disable-features=MediaRouter,OptimizationHints", "--js-flags=--max-old-space-size=512"]}


async def _render(payload, network, proxy):
    from crawl4ai import BrowserConfig, CrawlerRunConfig
    from crawl4ai.async_crawler_strategy import AsyncPlaywrightCrawlerStrategy
    from crawl4ai.browser_manager import BrowserManager

    class SecureBrowserManager(BrowserManager):
        def _build_browser_args(self):
            return _launch_options(proxy)

        async def create_browser_context(self, crawlerRunConfig=None):
            return await self.browser.new_context(ignore_https_errors=False, accept_downloads=False,
                service_workers="block", java_script_enabled=True, user_agent="ir-search/0.1",
                viewport={"width": 1280, "height": 900})

    config = BrowserConfig(headless=True, verbose=False, ignore_https_errors=False,
                           accept_downloads=False, use_persistent_context=False, enable_stealth=False)
    strategy = AsyncPlaywrightCrawlerStrategy(browser_config=config)
    strategy.browser_manager = SecureBrowserManager(browser_config=config, logger=strategy.logger)
    fatal = []
    lock = asyncio.Semaphore(4)

    async def setup(page, context, **kwargs):
        async def route_request(route):
            request = route.request
            main = request.is_navigation_request() and request.frame == page.main_frame
            if (request.resource_type not in {"document", "script", "stylesheet", "xhr", "fetch"}
                    or request.resource_type == "document" and not main):
                network.blocked += 1
                await route.abort()
                return
            try:
                async with lock:
                    status, headers, body = await asyncio.to_thread(network.fetch, request.url, method=request.method,
                        body=request.post_data_buffer, main=main)
                await route.fulfill(status=status, headers=headers, body=body)
            except (DataAdapterError, RequestStopped) as exc:
                network.blocked += 1
                network.warnings.add("subrequest_" + exc.code)
                failure = {"host": urlsplit(request.url).hostname, "method": request.method,
                           "resource_type": request.resource_type, "code": exc.code}
                if failure not in network.failures and len(network.failures) < 10:
                    network.failures.append(failure)
                if main or isinstance(exc, RequestStopped) or exc.code == "browser_budget_exhausted":
                    fatal.append(exc)
                    network.context.cancel()
                await route.abort()
            except Exception:
                network.blocked += 1
                network.warnings.add("subrequest_failed")
                if main:
                    fatal.append(DataAdapterError("browser_failed"))
                await route.abort()

        await context.route("**/*", route_request)
        await context.route_web_socket("**/*", lambda ws: ws.close())
        # Popups, frames, service workers and non-HTTP egress are not needed for reads.
        context.on("page", lambda other: asyncio.create_task(other.close()) if other != page else None)
        return page

    async def before_html(page, context, **kwargs):
        if _dynamic_directory(payload["url"]):
            try:
                await page.wait_for_selector('a[href*=".pdf"]', state="attached",
                    timeout=min(8000, max(1, int(network.context.remaining_seconds() * 1000))))
            except Exception:
                network.warnings.add("directory_wait_not_ready")
        elif urlsplit(payload['url']).hostname == 'mp.weixin.qq.com':
            try:
                await page.wait_for_selector('#js_content', state='visible',
                    timeout=min(4000, max(1, int(network.context.remaining_seconds() * 1000))))
            except Exception:
                network.warnings.add('wechat_article_wait_not_ready')
        return page

    strategy.set_hook("on_page_context_created", setup)
    strategy.set_hook("before_retrieve_html", before_html)
    try:
        async with strategy:
            response = await strategy.crawl(payload["url"], config=CrawlerRunConfig(
                verbose=False, page_timeout=max(1, int(network.context.remaining_seconds() * 1000)),
                wait_until="domcontentloaded", delay_before_return_html=1.0,
                remove_overlay_elements=False, wait_for_images=False, adjust_viewport_to_content=False,
                check_robots_txt=False, max_retries=0))
            if fatal:
                raise fatal[0]
            network.context.check_active()
            if not network.final_url or response.status_code != 200:
                raise DataAdapterError("browser_failed")
            html = response.html or ""
            if len(html.encode("utf-8")) > 8 * 1024 * 1024:
                raise DataAdapterError("response_too_large")
            # The final browser URL must also be a public, credential-free reference.
            network._validate(response.redirected_url or network.final_url, "GET", True)
            return {"html": html, "url": response.redirected_url or network.final_url,
                    "diagnostics": network.diagnostics()}
    except Exception:
        if fatal:
            raise fatal[0]
        raise


def main():
    payload = json.loads(sys.stdin.read(65536))
    context = RequestContext(timeout_seconds=payload["timeout"], max_operations=payload["operations"])
    network = BrowserNetwork(payload["url"], context, tuple(payload["allowed_domains"]))
    server = ThreadingHTTPServer(("127.0.0.1", 0), _DenyProxy)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = asyncio.run(_render(payload, network, "http://127.0.0.1:" + str(server.server_port)))
    except (DataAdapterError, RequestStopped) as exc:
        result = {"error": exc.code}
    except Exception as exc:
        # Do not serialize browser exceptions, source HTML, URLs or parameters in errors.
        message = str(exc).lower()
        result = {"error": "browser_unavailable" if "executable doesn't exist" in message
                  else "timeout" if "timeout" in message else "browser_failed"}
    finally:
        context.cancel()
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)
    result["operations"] = context.operations
    Path(payload["output"]).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
