from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from langgraph.graph import StateGraph, END, START
from .ras_nodes import ras_listings_node, ras_fetch_docs_node
from .models import RasQuery, RasListingItem, RasRawDoc


class RasState(TypedDict):
    query: RasQuery
    ras_listings: Annotated[list[RasListingItem], operator.add]
    ras_docs: Annotated[list[RasRawDoc], operator.add]


def create_ras_graph():
    graph_builder = StateGraph(RasState)
    graph_builder.add_node("ras_listings", ras_listings_node)
    graph_builder.add_node("ras_fetch_docs", ras_fetch_docs_node)

    graph_builder.add_edge(START, "ras_listings")
    graph_builder.add_edge("ras_listings", "ras_fetch_docs")
    graph_builder.add_edge("ras_fetch_docs", END)

    return graph_builder.compile()