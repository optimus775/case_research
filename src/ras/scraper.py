# src/ras/scraper.py
import asyncio
from typing import List, Dict, Any
from playwright.async_api import Page
from .models import RasQuery, RasListingItem

class RasScraper:
    BASE = "https://ras.arbitr.ru"

    async def open_search(self, page: Page):
        await page.goto(self.BASE, wait_until="domcontentloaded")

    async def apply_filters(self, page: Page, q: RasQuery):
        # Примерный псевдокод; конкретные селекторы найдёте через DevTools
        # Текст/фабула
        if q.text:
            await page.fill("input[placeholder='Поиск по тексту']", q.text)
        # Даты
        if q.date_from:
            await page.fill("input[name='dateFrom']", q.date_from)
        if q.date_to:
            await page.fill("input[name='dateTo']", q.date_to)
        # Типы документов
        if q.doc_types:
            for dt in q.doc_types:
                await page.click(f"text={dt}")  # или селектор чекбокса
        # Регион/суд
        if q.court_region:
            await page.click("css=[data-testid='region-filter']")
            await page.fill("css=input[role='combobox']", q.court_region)
            await page.keyboard.press("Enter")
        if q.court_id:
            # аналогично — выбор конкретного суда
            pass
        # Запуск поиска
        await page.click("button:has-text('Найти')")

    async def collect_listings(self, page: Page, limit: int = 100) -> List[RasListingItem]:
        items: List[RasListingItem] = []

        # Попытка вытащить JSON через XHR перехват
        # Работаем с уже отобразившейся таблицей результатов как фоллбек
        await page.wait_for_selector("css=.search-result")  # контейнер результатов
        rows = await page.query_selector_all("css=.search-result .result-item")
        for r in rows[:limit]:
            title = await r.locator(".result-title").inner_text() if await r.locator(".result-title").count() else None
            meta = await r.locator(".result-meta").inner_text() if await r.locator(".result-meta").count() else ""
            link_el = r.locator("a.result-link")
            detail_url = await link_el.get_attribute("href") if await link_el.count() else None
            # Парсинг мета-строки по шаблонам (дата/суд/тип/№ дела)
            # В проде используйте строгие regex'ы + нормализацию
            items.append(RasListingItem(
                id=None,
                case_number=None,
                court=None,
                region=None,
                doc_type=None,
                date=None,
                title=title,
                parties=None,
                detail_url=detail_url if detail_url and detail_url.startswith("http") else (self.BASE + detail_url if detail_url else None),
                download_url=None
            ))
        return items
