# Судебный ИИ-ассистент Repository Overview

## Project Description
Судебный ИИ-ассистент - это настраиваемый, полностью открытый исследовательский агент, который работает с несколькими поставщиками моделей, инструментами поиска и серверами MCP (Model Context Protocol). Он обеспечивает автоматизированное исследование с параллельной обработкой и созданием исчерпывающих отчетов для юристов.

## Repository Structure

### Root Directory
- `README.md` - Comprehensive project documentation with quickstart guide
- `pyproject.toml` - Python project configuration and dependencies
- `langgraph.json` - LangGraph configuration defining the main graph entry point
- `uv.lock` - UV package manager lock file
- `LICENSE` - MIT license
- `.env.example` - Environment variables template (not tracked)

### Core Implementation (`src/`)
- `open_deep_research/` - The original deep research agent implementation.
- `ras/` - A module for scraping court decisions from ras.arbitr.ru.
  - `browser.py` - Manages Playwright browser automation.
  - `downloader.py` - Downloads and parses PDF documents.
  - `models.py` - Pydantic models for queries, listings, and documents.
  - `net.py` - Network utilities, including a rate limiter.
  - `ocr.py` - Optional OCR functionality.
  - `ras_nodes.py` - LangGraph nodes for the `ras` module.
  - `scraper.py` - The core scraping logic.
- `security/` - Security-related components.

### Legacy Implementations (`src/legacy/`)
Contains two earlier research implementations:
- `graph.py` - Plan-and-execute workflow with human-in-the-loop
- `multi_agent.py` - Supervisor-researcher multi-agent architecture
- `legacy.md` - Documentation for legacy implementations
- `CLAUDE.md` - Legacy-specific Claude instructions
- `tests/` - Legacy-specific tests

### Testing (`tests/`)
- `run_evaluate.py` - Main evaluation script configured to run on deep research bench
- `evaluators.py` - Specialized evaluation functions  
- `prompts.py` - Evaluation prompts and criteria
- `pairwise_evaluation.py` - Comparative evaluation tools
- `supervisor_parallel_evaluation.py` - Multi-threaded evaluation

### Examples (`examples/`)
- `arxiv.md` - ArXiv research example
- `pubmed.md` - PubMed research example
- `inference-market.md` - Inference market analysis examples
- `ras_example.md` - An example of using the `ras` module.

## Key Technologies
- **LangGraph** - Workflow orchestration and graph execution
- **LangChain** - LLM integration and tool calling
- **Multiple LLM Providers** - OpenAI, Anthropic, Google, Groq, DeepSeek support
- **Search APIs** - Tavily, OpenAI/Anthropic native search, DuckDuckGo, Exa, ras.arbitr.ru
- **MCP Servers** - Model Context Protocol for extended capabilities

## Development Commands
- `uvx langgraph dev` - Start development server with LangGraph Studio
- `python tests/run_evaluate.py` - Run comprehensive evaluations
- `ruff check` - Code linting
- `mypy` - Type checking

## Configuration
All settings configurable via:
- Environment variables (`.env` file)
- Web UI in LangGraph Studio
- Direct configuration modification

Key settings include model selection, search API choice, concurrency limits, and MCP server configurations.