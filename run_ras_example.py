import asyncio
import logging
from dotenv import load_dotenv
import os
import sys
from pathlib import Path
import httpx
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
        # Больше НЕ используем HtmlDocument как referer — только корень
        referer = "https://ras.arbitr.ru/"

        out_dir = Path(os.getenv("RAS_SAVE_DIR") or "downloads/ras")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / ("stage2_" + filename)

        # Опции удержания окна открытым для ручной проверки
        keep_open = (os.getenv("RAS_KEEP_OPEN", "").lower() in ("1", "true", "yes")) or ("--keep-open" in sys.argv)
        try:
            keep_open_timeout = int(os.getenv("RAS_KEEP_OPEN_TIMEOUT", "300"))
        except Exception:
            keep_open_timeout = 300

        saved = False
        async with RasBrowser() as rb:
            ctx, page, ua = await rb.new_context()
            # Инициализация cookie на домене
            try:
                await page.goto("https://ras.arbitr.ru/", wait_until="domcontentloaded")
            except Exception:
                pass
            # HtmlDocument НЕ открываем; при необходимости серверные куки проставятся от корня

            # Отладка: запишем, какой URL берём из файла и какие URL реально уходит в Chromium
            debug_file = out_dir / "stage2_debug_links.txt"
            requests_seen: list[str] = []
            responses_seen: list[str] = []
            downloads_intercepted: list[str] = []
            try:
                def _on_request(req):
                    u = req.url
                    if ("/Document/Pdf/" in u) or ("/Kad/PdfDocument/" in u) or u.lower().endswith(".pdf"):
                        requests_seen.append(u)
                        print(f"[DEBUG] PDF request intercepted: {u}")
                
                def _on_response(resp):
                    nonlocal saved
                    try:
                        u = resp.url
                        ct = (resp.headers.get("content-type") or "").lower()
                        if ("/Document/Pdf/" in u) or (u.lower().endswith(".pdf")) or ("application/pdf" in ct):
                            responses_seen.append(f"{u} ct={ct} status={resp.status}")
                            print(f"[DEBUG] PDF response intercepted: {u} ct={ct} status={resp.status}")
                            
                            # If this is a successful PDF response, save it immediately
                            if resp.status == 200 and "application/pdf" in ct and not saved:
                                print(f"[DEBUG] SUCCESS: Capturing PDF response body immediately...")
                                try:
                                    # Use create_task to run the async response.body() in the current event loop
                                    import asyncio
                                    
                                    async def save_pdf_from_response():
                                        nonlocal saved
                                        try:
                                            body = await resp.body()
                                            
                                            # Enhanced diagnostic logging
                                            print(f"[DEBUG] Response body length: {len(body) if body else 0}")
                                            
                                            if body and len(body) > 0:
                                                # Show first 200 bytes as text (for error messages)
                                                try:
                                                    preview_text = body[:200].decode('utf-8', errors='ignore')
                                                    print(f"[DEBUG] Response body preview (text): {repr(preview_text)}")
                                                except Exception:
                                                    pass
                                                    
                                                # Show first 20 bytes as hex (for binary content)
                                                preview_hex = body[:20].hex() if len(body) >= 20 else body.hex()
                                                print(f"[DEBUG] Response body preview (hex): {preview_hex}")
                                                
                                                # Check if it looks like HTML error page
                                                if body.startswith(b'<!DOCTYPE') or body.startswith(b'<html'):
                                                    print(f"[DEBUG] Response appears to be HTML error page, not PDF")
                                                    return False
                                                
                                                # Check for redirect or error messages
                                                if b'redirect' in body.lower() or b'error' in body.lower():
                                                    print(f"[DEBUG] Response contains redirect/error indicators")
                                                    
                                            if body and len(body) > 1000 and body.startswith(b"%PDF"):
                                                out_path.write_bytes(body)
                                                print(f"[DEBUG] SUCCESS: PDF automatically saved from intercepted response to {out_path}")
                                                print(f"[DEBUG] Saved {len(body)} bytes")
                                                saved = True
                                                return True
                                            else:
                                                print(f"[DEBUG] Response body not valid PDF: len={len(body) if body else 0}")
                                        except Exception as e:
                                            print(f"[DEBUG] Failed to save PDF from response: {e}")
                                        return False
                                    
                                    # Schedule the task to run in the current event loop
                                    task = asyncio.create_task(save_pdf_from_response())
                                    print(f"[DEBUG] Scheduled PDF save task")
                                    
                                except Exception as e:
                                    print(f"[DEBUG] Error creating PDF save task: {e}")
                    except Exception:
                        pass
                
                def _on_download(download):
                    try:
                        url = download.url
                        suggested_filename = download.suggested_filename
                        downloads_intercepted.append(f"url={url} filename={suggested_filename}")
                        print(f"[DEBUG] Browser download intercepted: {url} -> {suggested_filename}")
                        # Save the download to our expected location
                        try:
                            download_path = out_dir / ("manual_" + suggested_filename)
                            download.save_as(download_path)
                            print(f"[DEBUG] Manual download saved to: {download_path}")
                            # Also save to the main expected path
                            main_path = out_path
                            download.save_as(main_path)
                            print(f"[DEBUG] Manual download also saved to: {main_path}")
                            saved = True  # Mark as successfully saved
                        except Exception as e:
                            print(f"[DEBUG] Failed to save download: {e}")
                            # Try alternative approach - wait and copy from default location
                            try:
                                import time
                                time.sleep(2)  # Wait for download to complete
                                # Try to find the file in the default downloads directory
                                default_path = download.path()
                                if default_path and Path(default_path).exists():
                                    import shutil
                                    shutil.copy2(default_path, out_path)
                                    print(f"[DEBUG] Copied from {default_path} to {out_path}")
                                    saved = True
                            except Exception as e2:
                                print(f"[DEBUG] Alternative download save failed: {e2}")
                    except Exception as e:
                        print(f"[DEBUG] Download handler error: {e}")
                
                # Setup Chromium CDP listener to capture binary PDF responses (if available)
                try:
                    cdp = await ctx.new_cdp_session(page)
                    await cdp.send("Network.enable")
                    def _cdp_response(params):
                        try:
                            request_id = params.get("requestId")
                            response = params.get("response", {}) or {}
                            mime = (response.get("mimeType") or "").lower()
                            url = response.get("url", "")
                            status = response.get("status")
                            if (("application/pdf" in mime) or (url.lower().endswith(".pdf"))) and status == 200:
                                print(f"[DEBUG-CDP] PDF response detected via CDP: {url} mime={mime} status={status}")
                                async def _fetch_response():
                                    nonlocal saved
                                    try:
                                        body_res = await cdp.send("Network.getResponseBody", {"requestId": request_id})
                                        body = body_res.get("body")
                                        if body_res.get("base64Encoded"):
                                            import base64
                                            raw = base64.b64decode(body)
                                        else:
                                            raw = body.encode("utf-8")
                                        print(f"[DEBUG-CDP] Got body len={len(raw)}")
                                        if raw and raw.startswith(b"%PDF"):
                                            out_path.write_bytes(raw)
                                            print(f"[DEBUG-CDP] Saved PDF from CDP to {out_path} ({len(raw)} bytes)")
                                            saved = True
                                    except Exception as e:
                                        print(f"[DEBUG-CDP] Failed to get response body via CDP: {e}")
                                asyncio.create_task(_fetch_response())
                        except Exception as e:
                            print(f"[DEBUG-CDP] CDP response handler error: {e}")
                    cdp.on("Network.responseReceived", _cdp_response)
                except Exception as e:
                    print(f"[DEBUG-CDP] CDP setup failed: {e}")
                
                page.on("request", _on_request)
                page.on("response", _on_response)
                page.on("download", _on_download)
            except Exception as e:
                print(f"[DEBUG] Failed to setup event handlers: {e}")
            # Финальная гибридная стратегия с последней отчаянной попыткой через JS
            try:
                print(f"[DEBUG] Last Ditch Effort: Hybrid approach with advanced JS injection.")
                
                await page.goto(pdf_url, wait_until="networkidle")

                # 1. Ждем, пока viewer будет готов, проверяя наличие внутреннего API
                try:
                    await page.wait_for_function("window.PDFViewerApplication && window.PDFViewerApplication.download", timeout=15000)
                    print("[DEBUG] PDF Viewer Application API is available.")
                except Exception:
                    raise Exception("PDF Viewer API (PDFViewerApplication.download) never became available.")

                # 2. Вызываем внутренний метод `download` самого viewer'а
                print("[DEBUG] Triggering viewer's internal download method.")
                # Ожидаем событие скачивания, которое должно быть инициировано кликом
                async with page.expect_download() as download_info:
                    await page.evaluate("window.PDFViewerApplication.download()")
                
                download = await download_info.value
                await download.save_as(out_path)

                # 3. Проверка
                if out_path.exists() and out_path.stat().st_size > 1000:
                    with open(out_path, "rb") as f:
                        if f.read(4) == b'%PDF':
                            print(f"[DEBUG] SUCCESS! PDF downloaded via internal API to {out_path}")
                            saved = True
                
                if not saved:
                    raise Exception("Internal API call did not result in a valid PDF download.")

            except Exception as e:
                print(f"[DEBUG] Last ditch effort failed: {e}")
            finally:
                print(f"[DEBUG] Final status: saved={saved}")
                if keep_open and not saved:
                    print("[DEBUG] All programmatic attempts have failed. Keeping page open.")
                    await asyncio.sleep(keep_open_timeout)
                await page.close()
                await ctx.close()

        if not saved:
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