import asyncio
from src.ras import create_ras_graph, RasQuery

async def main():
    ras_graph = create_ras_graph()
    query = RasQuery(text="интеллектуальная собственность", per_page=10)
    result = await ras_graph.ainvoke({"query": query})
    print(result)

if __name__ == "__main__":
    asyncio.run(main())