# src/ras/browser.py
import asyncio, os
from playwright.async_api import async_playwright

class RasBrowser:
    def __init__(self, headless: bool = True, proxy: str | None = None):
        self._pw = None
        self._browser = None
        self.headless = headless
        self.proxy = proxy

    async def __aenter__(self):
        self._pw = await async_playwright().start()
        launch_args = dict(headless=self.headless, args=["--disable-blink-features=AutomationControlled"])
        if self.proxy:
            launch_args["proxy"] = {"server": self.proxy}
        self._browser = await self._pw.chromium.launch(**launch_args)
        return self

    async def __aexit__(self, *exc):
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    async def new_context(self):
        context = await self._browser.new_context(locale="ru-RU", user_agent=None)
        page = await context.new_page()
        return context, page
