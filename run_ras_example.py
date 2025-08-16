import asyncio
import logging
from dotenv import load_dotenv
import os
import sys
from pathlib import Path
from src.ras import create_ras_graph, RasQuery
from src.ras.scraper import RasScraper
from src.ras.browser import RasBrowser

async def main():
    # Режим 1: только собрать первые 10 прямых PDF-ссылок и выйти
    only_collect = (os.getenv("RAS_ONLY_COLLECT", "").lower() in ("1", "true", "yes")) or ("--collect-links" in sys.argv)
    query_text = os.getenv("RAS_QUERY_TEXT") or "возмещение ущерба арендатору при затоплении помещения"
    per_page = int(os.getenv("RAS_PER_PAGE", "25"))
    query = RasQuery(text=query_text, per_page=per_page)

    if only_collect:
        out_dir = Path(os.getenv("RAS_SAVE_DIR") or "downloads/ras")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / "pdf_links.txt"
        scraper = RasScraper()
        async with RasBrowser() as rb:
            ctx, page, ua = await rb.new_context()
            await scraper.open_search(page)
            # Быстрый сбор: XHR JSON без DOM/API фоллбеков
            listings = await scraper.collect_listings_fast(page, query, limit=query.per_page, wait_after_ms=3000)
            # Для скорости: используем только те PDF-ссылки, что уже получены/выведены из JSON (без переходов по деталям)
            pdf_links = [it.download_url for it in listings if it.download_url][:10]
            out_file.write_text("\n\n".join(pdf_links), encoding="utf-8")
            # Закрываем страницу и контекст сразу после сохранения ссылок
            try:
                await page.close()
            except Exception:
                pass
            await ctx.close()
        print({
            "mode": "collect_links",
            "query": query_text,
            "count": len(pdf_links),
            "file": str(out_file),
        })
        for i, link in enumerate(pdf_links):
            print(f"pdf[{i}]: {link}")
        return

    # Режим 2: полный граф с загрузкой документов
    ras_graph = create_ras_graph()
    result = await ras_graph.ainvoke({"query": query})
    # Summarize results
    listings = result.get("ras_listings", [])
    docs = result.get("ras_docs", [])
    summary = {
        "listings_count": len(listings),
        "docs_count": len(docs),
        "first_listing": listings[0].model_dump() if listings else None,
    }
    print(summary)
    # Show a short preview for the first few docs
    max_preview = 3
    for i, d in enumerate(docs[:max_preview]):
        meta = getattr(d, "meta", {}) or {}
        saved_path = meta.get("saved_path")
        print(f"doc[{i}]: bytes={d.bytes_len}, saved={saved_path}, url={meta.get('download_url')}")
        if d.text:
            snippet = d.text[:400].replace("\n", " ")
            print(f"doc[{i}] text: {snippet}...")
        else:
            print(f"doc[{i}] text: <empty>")

if __name__ == "__main__":
    # Load environment variables from .env (proxy, headless toggle, etc.)
    load_dotenv()
    logging.basicConfig(stream=sys.stdout, level=logging.DEBUG, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
    asyncio.run(main())
