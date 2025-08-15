import asyncio
import logging
from dotenv import load_dotenv
import sys
from src.ras import create_ras_graph, RasQuery

async def main():
    ras_graph = create_ras_graph()
    # Пример: явно задаём текст запроса в поле "Текст документа"
    query = RasQuery(text="возмещение ущерба арендатору при затоплении помещения", per_page=10)
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
