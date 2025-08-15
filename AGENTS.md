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

---

## RAS (ras.arbitr.ru) Retriever: Implementation Notes and Debug Log

This section documents the recent work to make the RAS retriever robust against anti‑bot measures and frontend changes, and how to run and debug it locally.

### What was added/changed
- Added detailed DEBUG logging across `src/ras/browser.py`, `src/ras/scraper.py`, `src/ras/ras_nodes.py`, and `src/ras/downloader.py`.
- Fixed `RasDownloader` imports and updated `httpx` usage for v0.28 via `HTTPTransport(proxy=...)` (and `AsyncHTTPTransport` in async paths).
- Improved selectors in `RasScraper` to align with live markup:
  - Use `textarea[placeholder*="текст документа"]` for text queries.
  - Keep find button and results container waits, with additional waits for `networkidle` and visibility (`#results` hidden class removal).
- Implemented XHR interception and JSON parsing that supports `{ Result: { Items: [...] } }`.
- Implemented a direct API fallback path when XHR and DOM parsing are empty:
  1) Attempt to obtain `RecaptchaToken` via in‑page `Common.executePravocaptcha`.
  2) Build payload from `returnRequestInfo(1,false)` if available, else synthesize from `RasQuery`.
  3) POST to `https://ras.arbitr.ru/Ras/Search` using page cookies + UA, and parse JSON.
- If JSON doesn’t include a `detail_url`, we now derive it as `https://ras.arbitr.ru/Ras/HtmlDocument/{ActId}`.
- `run_ras_example.py` now loads `.env` automatically and prints concise run summary (`listings_count`, `docs_count`, etc.).
- Added `RAS_CHROME_CHANNEL` support (optional) to run Chromium with the Chrome channel when available.
- Added ignoring of local debug artifacts: `debug_*.html`, `debug_*.js`.

### Why results were empty initially
- Headless navigation + no RU proxy caused ras.arbitr.ru to withhold RecaptchaToken and block `/Ras/Search` (HTTP 451 by DDoS‑Guard). With no XHR JSON and empty DOM fallback, the result set was empty. The API fallback also needs a token to pass server‑side checks.

### How to run locally and get results
1) Ensure RU proxy is configured in `.env`:
   - `RAS_PROXY=http://user:pass@host:port`
   - `RAS_HTTP_PROXY=http://user:pass@host:port`
2) Disable headless (anti‑bot is stricter in headless):
   - `RAS_HEADLESS=false`
   - Optional: `RAS_CHROME_CHANNEL=chrome` (if the Chrome channel is installed and supported on your OS)
   - Optional: `RAS_DEBUG_NET=true` for request/response logging
3) Install browser: `python -m playwright install chromium`
4) Run: `python run_ras_example.py`

Expected logs:
- `doSearchRequest present: True`
- XHR capture: `XHR records captured: N; parsed items: M` (>0), else API fallback will log `RecaptchaToken acquired` and a 200 status.
- Final summary printed by the example with non‑zero `listings_count`.

### Docs extraction (docs_count)
- We currently add a `RasRawDoc` only if non‑empty text is extracted by `pdfminer.six`.
- Many RAS PDFs are scans; OCR is required. We can:
  - Add a flag `RAS_ACCEPT_EMPTY_TEXT=1` to accept docs without text.
  - Wire up the OCR fallback in `ocr.py` (e.g. Tesseract) for scanned PDFs.

### Env vars used
- `RAS_PROXY`, `RAS_HTTP_PROXY`: HTTP(S) proxy (RU IP strongly recommended).
- `RAS_HEADLESS`: `true|false` (default true). For production scraping, headful improves success rate.
- `RAS_CHROME_CHANNEL`: optional Playwright channel (e.g., `chrome`).
- `RAS_MAX_CONCURRENCY`: concurrent downloads limit (default 4).
- `RAS_DEBUG_NET`: `1|true|yes` to log page network requests.

### Files affected
- `src/ras/browser.py`: proxy/launch handling + logging + optional Chrome channel.
- `src/ras/scraper.py`: selectors, waits, XHR parsing, API fallback to `/Ras/Search`, detail URL derivation.
- `src/ras/ras_nodes.py`: structured logging for node I/O and exceptions.
- `src/ras/downloader.py`: proxy for httpx 0.28, logging around PDF fetch and extract.
- `run_ras_example.py`: `.env` autoload + friendly output.
- `.gitignore`: ignore local debug snapshots.

### Known limitations / next steps
- Recaptcha/anti‑bot: Headless often fails; use headful + RU proxy.
- PDF text: add OCR fallback or accept empty text via a flag.
- Optional: add `RAS_FORCE_API_FALLBACK=1` to skip UI and immediately try the tokenized API.
