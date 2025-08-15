# Example: Using the `ras` a gent to scrape ras.arbitr.ru

This example demonstrates how to use the `ras` agent to scrape court decisions from ras.arbitr.ru.

## 1. Import necessary libraries

```python
import asyncio
from src.ras import create_ras_graph, RasQuery

# 2. Set up an event loop
async def main():
    # 3. Create the `ras` graph
    ras_graph = create_ras_graph()

    # 4. Define a query
    query = RasQuery(text="интеллектуальная собственность", per_page=10)

    # 5. Run the graph
    result = await ras_graph.ainvoke({"query": query})

    # 6. Print the results
    print(result)

if __name__ == "__main__":
    asyncio.run(main())
```

## 7. To run this example:

1.  Make sure you have all the necessary dependencies installed (`playwright`, `httpx`, `pdfminer.six`, etc.).
2.  Save the code above as a Python file (e.g., `run_ras_agent.py`).
3.  Execute it from your terminal: `python run_ras_agent.py`