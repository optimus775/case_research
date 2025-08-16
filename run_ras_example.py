import asyncio
import logging
from dotenv import load_dotenv
import os
import sys
from pathlib import Path
from src.ras import create_ras_graph, RasQuery
from src.ras.scraper import RasScraper
from src.ras.browser import RasBrowser
from urllib.parse import urlparse, unquote

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

    # Режим 2a: скачать один PDF из файла ссылок (для отладки скачивания)
    download_one = (os.getenv("RAS_DOWNLOAD_ONE", "").lower() in ("1", "true", "yes")) or ("--download-one" in sys.argv)
    if download_one:
        links_file = Path(os.getenv("RAS_LINKS_FILE") or "downloads/ras/pdf_links.txt")
        if not links_file.exists():
            print({"error": "links_file_not_found", "file": str(links_file)})
            return
        # индекс ссылки можно передать через env или аргумент
        idx = 0
        for i, a in enumerate(sys.argv):
            if a.startswith("--index="):
                try:
                    idx = int(a.split("=", 1)[1])
                except Exception:
                    idx = 0
                break
        raw = links_file.read_text(encoding="utf-8").strip().splitlines()
        # линии разделены пустыми строками; фильтруем
        links = [ln.strip() for ln in raw if ln.strip()]
        if not links:
            print({"error": "no_links_in_file", "file": str(links_file)})
            return
        if idx < 0 or idx >= len(links):
            idx = 0
        pdf_url = links[idx]

        # Вытаскиваем имя файла и doc_guid для реферера
        def parse_pdf_url(u: str):
            pu = urlparse(u)
            parts = pu.path.split("/")
            # ['', 'Document', 'Pdf', case_guid, doc_guid, filename]
            doc_guid = parts[4] if len(parts) >= 5 else None
            filename = unquote(parts[-1]) if parts else "document.pdf"
            if not filename.lower().endswith(".pdf"):
                filename += ".pdf"
            return doc_guid, filename

        doc_guid, filename = parse_pdf_url(pdf_url)
        referer = f"https://ras.arbitr.ru/Ras/HtmlDocument/{doc_guid}" if doc_guid else "https://ras.arbitr.ru/"

        out_dir = Path(os.getenv("RAS_SAVE_DIR") or "downloads/ras")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / ("stage2_" + filename)

        async with RasBrowser() as rb:
            ctx, page, ua = await rb.new_context()
            # Инициализация cookie на домене
            try:
                await page.goto("https://ras.arbitr.ru/", wait_until="domcontentloaded")
            except Exception:
                pass
            # Переходим на HtmlDocument (реферер), чтобы у сервера были все контекстные куки
            try:
                await page.goto(referer, wait_until="domcontentloaded")
            except Exception:
                pass

            # Отладка: запишем, какой URL берём из файла и какие URL реально уходит в Chromium
            debug_file = out_dir / "stage2_debug_links.txt"
            requests_seen: list[str] = []
            responses_seen: list[str] = []
            try:
                def _on_request(req):
                    u = req.url
                    if ("/Document/Pdf/" in u) or ("/Kad/PdfDocument/" in u) or u.lower().endswith(".pdf"):
                        requests_seen.append(u)
                def _on_response(resp):
                    try:
                        u = resp.url
                        ct = (resp.headers.get("content-type") or "").lower()
                        if ("/Document/Pdf/" in u) or (u.lower().endswith(".pdf")) or ("application/pdf" in ct):
                            responses_seen.append(f"{u} ct={ct}")
                    except Exception:
                        pass
                page.on("request", _on_request)
                page.on("response", _on_response)
            except Exception:
                pass
            # Сразу пишем входной URL в файл
            try:
                with open(debug_file, "a", encoding="utf-8") as f:
                    f.write(f"input={pdf_url}\nreferer={referer}\n")
            except Exception:
                pass

            # Попытка 1: Нажать "Скачать" и перехватить download (надежнее всего для одного файла)
            try:
                # Подождём немного, чтобы кнопка/ссылка скачивания появилась
                try:
                    await page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass
                sels = [
                    "a:has-text('Скачать')",
                    "button:has-text('Скачать')",
                    "a[href*='/Document/Pdf/']",
                    "a[href*='/Kad/PdfDocument/']",
                    "a[href$='.pdf']",
                ]
                dl_link = None
                for sel in sels:
                    try:
                        loc = page.locator(sel)
                        if await loc.count() > 0:
                            dl_link = loc.first
                            break
                    except Exception:
                        continue
                if dl_link is not None:
                    try:
                        async with page.expect_download(timeout=30000) as dl_info:
                            await dl_link.click()
                        d = await dl_info.value
                        suggested = d.suggested_filename or filename
                        save_path = out_dir / ("stage2_" + suggested)
                        await d.save_as(str(save_path))
                        print({"stage": "click_download", "saved": str(save_path), "suggested": suggested})
                        # Финальная запись в отладочный файл: какие URL реально ушли
                        try:
                            with open(debug_file, "a", encoding="utf-8") as f:
                                if requests_seen:
                                    f.write("requests:\n" + "\n".join(requests_seen) + "\n")
                                if responses_seen:
                                    f.write("responses:\n" + "\n".join(responses_seen) + "\n")
                                f.write("----\n")
                        except Exception:
                            pass
                        try:
                            await page.close()
                        except Exception:
                            pass
                        await ctx.close()
                        return
                    except Exception as e:
                        print({"stage": "click_download_error", "error": str(e)})

                # Попытка 2: Навигация напрямую на PDF и чтение ответа
                try:
                    def _pred(resp):
                        u = resp.url
                        return ("/Document/Pdf/" in u) or u.lower().endswith(".pdf")
                    fut = page.wait_for_event("response", timeout=20000, predicate=_pred)
                    await page.goto(pdf_url, wait_until="domcontentloaded")
                    resp = await fut
                    ct = (resp.headers.get("content-type") or "").lower()
                    body = await resp.body()
                    print({"stage": "page_goto_pdf", "status": resp.status, "ct": ct, "len": len(body)})
                    # Запишем, что реально запросили
                    try:
                        with open(debug_file, "a", encoding="utf-8") as f:
                            if requests_seen:
                                f.write("requests:\n" + "\n".join(requests_seen) + "\n")
                            f.write(f"response_url={resp.url} ct={ct}\n")
                            if responses_seen:
                                f.write("responses:\n" + "\n".join(responses_seen) + "\n")
                            f.write("----\n")
                    except Exception:
                        pass
                    if ("application/pdf" in ct) or body.startswith(b"%PDF"):
                        out_path.write_bytes(body)
                        print({"saved": str(out_path), "bytes": len(body)})
                        try:
                            await page.close()
                        except Exception:
                            pass
                        await ctx.close()
                        return
                except Exception as e:
                    print({"stage": "page_goto_pdf_error", "error": str(e)})

            finally:
                try:
                    await page.close()
                except Exception:
                    pass
                await ctx.close()
        print({"error": "download_failed", "url": pdf_url, "referer": referer})
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
