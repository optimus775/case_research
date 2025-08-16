# ─────────────────────────────────────────────────────────────────────────────
# File: ras/ras_nodes.py
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations
import os
import asyncio
import logging
from typing import List
from .models import RasQuery, RasListingItem, RasRawDoc
from .browser import RasBrowser
from .scraper import RasScraper
from .downloader import RasDownloader
from .net import RateLimiter


# LangGraph state typing is expected to include these keys
# (align with your project-wide state definition):
#   state["queries"]: List[RasQuery]
#   state["ras_listings"]: List[RasListingItem]
#   state["ras_docs"]: List[RasRawDoc]


async def ras_listings_node(state):
    # Accept either 'queries' (list) or 'query' (single RasQuery) in state for compatibility
    def _normalize(raw) -> List[RasQuery]:
        if raw is None:
            return []
        if isinstance(raw, list):
            out: List[RasQuery] = []
            for q in raw:
                if isinstance(q, RasQuery):
                    out.append(q)
                elif isinstance(q, dict):
                    out.append(RasQuery(**q))
                else:
                    out.append(q)
            return out
        if isinstance(raw, RasQuery):
            return [raw]
        if isinstance(raw, dict):
            return [RasQuery(**raw)]
        return [raw]

    queries: List[RasQuery] = _normalize(state.get("queries") or state.get("query"))
    logger = logging.getLogger(__name__)
    logger.debug("ras_listings_node: %d queries", len(queries))

    if not queries:
        state["ras_listings"] = []
        return state

    scraper = RasScraper()
    listings_acc: List[RasListingItem] = []

    async with RasBrowser() as rb:
        ctx, page, ua = await rb.new_context()
        # Optional: open once, apply filters per query sequentially
        for q in queries:
            try:
                await scraper.open_search(page)
                batch = await scraper.collect_listings(page, q, limit=q.per_page)
                listings_acc.extend(batch)
                logger.debug("Listings batch for '%s': %d", q.text or q.case_number, len(batch))
            except Exception as e:
                # continue on error per query
                logger.exception("ras_listings_node error for query %s: %s", q.model_dump(), e)
        await ctx.close()

    # Deduplicate
    seen, out = set(), []
    for it in listings_acc:
        key = (it.case_number or it.act_id or it.title, it.detail_url or it.download_url)
        if key not in seen:
            seen.add(key)
            out.append(it)

    logger.debug("ras_listings_node: total unique listings=%d", len(out))
    state["ras_listings"] = out
    return state


async def ras_fetch_docs_node(state):
    listings: List[RasListingItem] = state.get("ras_listings", [])
    logger = logging.getLogger(__name__)
    logger.debug("ras_fetch_docs_node: input listings=%d", len(listings))
    if not listings:
        state["ras_docs"] = []
        return state

    max_conc = int(os.getenv("RAS_MAX_CONCURRENCY", "2"))

    async with RasBrowser() as rb:
        ctx, page, ua = await rb.new_context()
        # First, try to enrich with direct download links (for top N)
        scraper = RasScraper()
        listings = await scraper.enrich_with_downloads(page, listings, max_items=50)
        # Keep only items with a direct PDF URL (strict policy)
        before = len(listings)
        listings = [it for it in listings if it.download_url]
        logger.debug("ras_fetch_docs_node: items with direct PDF URL=%d (from %d)", len(listings), before)
        cookies_hdr = await rb.cookies_header(ctx, domain_filter="vectorp.ru")
        dl = RasDownloader(user_agent=ua, cookies_header=cookies_hdr, page=page)

        limiter = RateLimiter(max_conc)
        docs: List[RasRawDoc] = []

        async def task(it: RasListingItem):
            async with limiter:
                try:
                    logger.debug("Downloading PDF for case=%s url=%s", it.case_number or it.act_id, it.download_url)
                    doc = await dl.fetch_and_parse(it)
                    if doc:
                        if not doc.text:
                            logger.debug("Empty text for %s; adding doc anyway", it.detail_url or it.download_url)
                        docs.append(doc)
                    await asyncio.sleep(0.2)
                except Exception as e:
                    logger.warning("Download/parse failed for %s: %s", it.detail_url or it.download_url, e)

        await asyncio.gather(*(task(it) for it in listings[:50]))
        await ctx.close()

    logger.debug("ras_fetch_docs_node: docs fetched=%d", len(docs))
    state["ras_docs"] = docs
    return state


# Convenience for graph builder

def build_ras_nodes():
    return {
        "ras_listings": ras_listings_node,
        "ras_fetch_docs": ras_fetch_docs_node,
    }
