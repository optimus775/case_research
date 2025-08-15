import asyncio
import logging
from dotenv import load_dotenv
import sys
from src.ras import create_ras_graph, RasQuery

async def main():
    ras_graph = create_ras_graph()
    query = RasQuery(text="интеллектуальная собственность", per_page=10)
    result = await ras_graph.ainvoke({"query": query})
    # Summarize results
    listings = result.get("ras_listings", [])
    docs = result.get("ras_docs", [])
    print({
        "listings_count": len(listings),
        "docs_count": len(docs),
        "first_listing": listings[0].model_dump() if listings else None,
        "first_doc_preview": (docs[0].text[:200] if docs and docs[0].text else None),
    })

if __name__ == "__main__":
    # Load environment variables from .env (proxy, headless toggle, etc.)
    load_dotenv()
    logging.basicConfig(stream=sys.stdout, level=logging.DEBUG, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
    asyncio.run(main())
