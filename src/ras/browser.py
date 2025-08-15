# src/ras/browser.py
from __future__ import annotations
import asyncio
import os
import random
from typing import Tuple, List
from playwright.async_api import async_playwright, Browser, BrowserContext, Page


USER_AGENTS = [
    # A small rotating pool; replace/extend as needed.
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
]


class RasBrowser:
    """Playwright browser manager with sane defaults and proxy support."""

    def __init__(self, headless: bool | None = None, proxy: str | None = None):
        self._pw = None
        self._browser: Browser | None = None
        if headless is None:
            headless = os.getenv("RAS_HEADLESS", "true").lower() == "true"
        self.headless = headless
        self.proxy = proxy or os.getenv("RAS_PROXY")

    async def __aenter__(self) -> "RasBrowser":
        self._pw = await async_playwright().start()
        launch_args = dict(
            headless=self.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        if self.proxy:
            launch_args["proxy"] = {"server": self.proxy}
        self._browser = await self._pw.chromium.launch(**launch_args)
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
        )
        page = await context.new_page()
        # Conservative accept-language headers
        await context.set_extra_http_headers({
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        })
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

