# src/ras/browser.py
from __future__ import annotations
import asyncio
import os
import random
import logging
from typing import Tuple, List
from urllib.parse import urlparse
from playwright.async_api import async_playwright, Browser, BrowserContext, Page


USER_AGENTS = [
    # A small rotating pool; replace/extend as needed.
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
]


class RasBrowser:
    """Playwright browser manager with sane defaults (proxy disabled by default)."""

    def __init__(self, headless: bool | None = None, proxy: str | None = None):
        self._pw = None
        self._browser: Browser | None = None
        if headless is None:
            headless = os.getenv("RAS_HEADLESS", "true").lower() == "true"
        self.headless = headless
        # Disable implicit proxy via env; only use explicit argument
        self.proxy = proxy

    def _proxy_args(self) -> dict | None:
        if not self.proxy:
            return None
        try:
            p = urlparse(self.proxy)
            server = f"{p.scheme}://{p.hostname}:{p.port}"
            out = {"server": server}
            if p.username:
                out["username"] = p.username
            if p.password:
                out["password"] = p.password
            return out
        except Exception:
            return {"server": self.proxy}

    async def __aenter__(self) -> "RasBrowser":
        logging.getLogger(__name__).debug("Starting Playwright (headless=%s, proxy=%s)", self.headless, bool(self.proxy))
        self._pw = await async_playwright().start()
        downloads_dir = os.path.join(os.getcwd(), 'downloads', 'ras')
        os.makedirs(downloads_dir, exist_ok=True)
        launch_args = dict(
            headless=self.headless,
            downloads_path=downloads_dir,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        proxy_args = self._proxy_args()
        if proxy_args:
            launch_args["proxy"] = proxy_args
            logging.getLogger(__name__).debug("Using proxy args: %s", {k: v if k != 'password' else '***' for k, v in proxy_args.items()})
        # Optional: use Chrome channel to reduce headless fingerprinting
        chrome_channel = os.getenv("RAS_CHROME_CHANNEL")
        if chrome_channel:
            launch_args["channel"] = chrome_channel
        try:
            self._browser = await self._pw.chromium.launch(**launch_args)
        except Exception as e:
            msg = str(e).lower()
            if chrome_channel and ("distribution" in msg or "not found" in msg):
                # Fallback: Chrome channel is unavailable on this OS; retry without channel
                logging.getLogger(__name__).warning(
                    "Chrome channel '%s' not available; falling back to default Chromium", chrome_channel
                )
                launch_args.pop("channel", None)
                self._browser = await self._pw.chromium.launch(**launch_args)
            else:
                raise
        logging.getLogger(__name__).debug("Chromium launched")
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    async def new_context(self) -> Tuple[BrowserContext, Page, str]:
        assert self._browser is not None
        ua = random.choice(USER_AGENTS)
        context = await self._browser.new_context(
            locale="ru-RU",
            user_agent=ua,
            viewport={"width": 1400, "height": 900},
            accept_downloads=True,
        )
        page = await context.new_page()
        page.set_default_navigation_timeout(60000)
        # Conservative accept-language headers
        await context.set_extra_http_headers({
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        })
        logging.getLogger(__name__).debug("New context created with UA: %s", ua)
        return context, page, ua

    @staticmethod
    async def cookies_header(context: BrowserContext, domain_filter: str | None = None) -> str:
        cookies = await context.cookies()
        parts: List[str] = []
        for c in cookies:
            if domain_filter and domain_filter not in (c.get("domain") or ""):
                continue
            name, value = c.get("name"), c.get("value")
            if name and value:
                parts.append(f"{name}={value}")
        return "; ".join(parts)
