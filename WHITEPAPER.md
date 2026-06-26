# Morning Digest — Technical White Paper

**Author:** Andrea Panizzut
**Version:** 1.0
**License:** MIT
**Repository:** https://github.com/andreapanizzut/morning-digest

---

## Table of contents

1. [Overview](#1-overview)
2. [Motivation and design goals](#2-motivation-and-design-goals)
3. [System architecture](#3-system-architecture)
4. [Component breakdown](#4-component-breakdown)
5. [Data flow](#5-data-flow)
6. [Prompt engineering](#6-prompt-engineering)
7. [Deduplication system](#7-deduplication-system)
8. [Configuration resolution](#8-configuration-resolution)
9. [Error handling strategy](#9-error-handling-strategy)
10. [Dependency analysis](#10-dependency-analysis)
11. [Known limitations and trade-offs](#11-known-limitations-and-trade-offs)
12. [Extension points](#12-extension-points)
13. [License notices](#13-license-notices)

---

## 1. Overview

Morning Digest is a scheduled Python agent that automates daily research briefings. It combines retrieval from the open web (DuckDuckGo) with local language model inference (Ollama) to produce structured, deduplicated digests delivered by email.

The project is deliberately dependency-light and infrastructure-free: no vector database, no message broker, no cloud services. The entire pipeline runs on a single machine or a LAN, with zero per-run cost beyond electricity.

---

## 2. Motivation and design goals

### Problem

Staying current with fast-moving fields (AI research, digital ethics, computer science) requires monitoring many sources daily. Manual curation is time-consuming; existing automated services rely on cloud LLM APIs that incur token costs at scale.

### Design goals

| Goal | Design decision |
|---|---|
| Zero ongoing cost | Local inference via Ollama; DuckDuckGo requires no API key |
| No data exfiltration | All processing stays on-device or on-LAN |
| Scheduler-compatible | No interactive input; exits with code 0 (success) or 1 (error) |
| Configurable without code changes | Three-tier config: defaults → `.env` → JSON file |
| Graceful degradation | On any failure, sends a short error email instead of silently failing |
| Idempotency-friendly | Archive prevents duplicate processing across runs |

---

## 3. System architecture

```
┌─────────────────────────────────────────────────────────┐
│                    morning_digest.py                    │
│                                                         │
│  ┌──────────┐   ┌──────────┐   ┌──────────────────┐   │
│  │  Config  │   │  Search  │   │  Archive (JSON)  │   │
│  │  loader  │   │  (DDGS)  │   │  deduplication   │   │
│  └────┬─────┘   └────┬─────┘   └────────┬─────────┘   │
│       │              │                   │              │
│       └──────────────┴───────────────────┘             │
│                       │                                 │
│              ┌────────▼────────┐                        │
│              │  Prompt builder │                        │
│              └────────┬────────┘                        │
│                       │                                 │
│              ┌────────▼────────┐                        │
│              │  Ollama client  │◄── LAN / localhost     │
│              └────────┬────────┘                        │
│                       │                                 │
│          ┌────────────┼────────────┐                    │
│          │            │            │                    │
│   ┌──────▼──┐  ┌──────▼──┐  ┌────▼──────┐             │
│   │Markdown │  │ Archive │  │  Email    │             │
│   │  save   │  │ update  │  │  sender   │             │
│   └─────────┘  └─────────┘  └───────────┘             │
└─────────────────────────────────────────────────────────┘
```

The script is intentionally structured as a **linear pipeline** rather than a graph or DAG. Each stage produces a value consumed by the next; there is no branching concurrency. This keeps the code readable and the failure surface small.

---

## 4. Component breakdown

### 4.1 Configuration loader (`build_config`)

Implements a three-tier merge with explicit precedence:

```
DEFAULTS (hardcoded) → load_env_config() → load_json_prefs() → final cfg dict
```

Each tier calls `dict.update()` on the previous result, so later tiers win. Unknown JSON keys are logged as warnings but do not halt execution — this allows forward compatibility when new fields are added.

### 4.2 Web search (`search_articles`)

Wraps the `ddgs` library's `DDGS.text()` method. Key decisions:

- **`timelimit=f"d{days}"`** — constrains results to the last N days at the DuckDuckGo query level, reducing noise before it reaches the model.
- **In-run URL deduplication** via a `seen_urls` set — prevents the same article appearing twice when it matches multiple topics.
- **Per-topic budget** — `max_results_per_topic` is computed as `max(3, 20 // n_topics)`, distributing a fixed total budget across topics rather than letting popular topics crowd out niche ones.
- Empty or whitespace-only topic strings are filtered before the search loop.

### 4.3 Article archive (`load_archive`, `save_archive`, `filter_new_articles`, `add_to_archive`)

Persistent cross-run deduplication. The archive is a flat JSON object keyed by URL:

```json
{
  "https://example.com/article": {
    "title": "Article title",
    "date_found": "2025-06-19",
    "topic": "AI policy"
  }
}
```

**Windowed loading:** the full archive is read from disk on every run, but only entries within the last `window_days` (default: 15) are loaded into the in-memory comparison set. This keeps RAM usage constant regardless of archive age, while preserving full history on disk for auditing.

**Write timing:** the archive is updated only after Ollama returns a successful response. If the model call fails, the articles remain unarchived and will be eligible for re-processing on the next run.

### 4.4 Prompt builder (`build_context_block`, `build_prefs_block`, `build_full_prompt`)

Constructs a two-part user message:

1. **Context block** — numbered list of articles with title, URL, search topic, and snippet.
2. **Preferences block** — serialised user preferences injected as a plain-language section headed *"Specific preferences for today:"*.

The system prompt is generated from `SYSTEM_PROMPT_TEMPLATE` with `digest_language` substituted at call time, keeping the template language-agnostic.

### 4.5 Ollama client (`call_ollama`)

Uses the `ollama` Python library's synchronous `Client.chat()` interface. The client is instantiated with the configured host, supporting remote Ollama instances over LAN.

Temperature is fixed at `0.7` — high enough for natural prose, low enough for factual consistency. This is not exposed as a preference field intentionally; it is a model-behaviour constant, not a user-facing setting.

### 4.6 Markdown to HTML converter (`md_to_html`)

A minimal hand-written converter with no external dependencies. It handles the subset of Markdown produced by the model: headings (h1–h3), unordered lists, horizontal rules, bold, italic, inline code, and links. It does not aim to be a general-purpose Markdown parser.

Rationale for not using a library (e.g. `markdown2`, `mistune`): the output format is predictable and bounded; adding a parser would introduce a dependency purely to convert 10–15 patterns.

### 4.7 Email sender (`send_email`, `send_error_email`)

Standard `smtplib` with STARTTLS on port 587. If any credential field is missing, the function logs a warning and returns without raising — this allows the script to complete a full dry run or file-only run without email configuration.

`send_error_email` is called from the top-level exception handler and wraps its own `send_email` call in a try/except to prevent an email failure from masking the original error in the log.

---

## 5. Data flow

```
prefs.json / .env / defaults
        │
        ▼
   build_config()
        │
        ├──► cfg["topics"] ──► search_articles()
        │                           │
        │                      raw articles (title, url, snippet, topic)
        │                           │
        │         load_archive() ◄──┤
        │              │            │
        │         filter_new_articles(articles, archive)
        │                           │
        │                      new_articles
        │                           │
        ├──► cfg["*"] ──────► build_full_prompt(new_articles, cfg)
        │                           │
        │                      user_prompt (str)
        │                           │
        ├──► cfg["ollama_*"] ► call_ollama(cfg, user_prompt)
        │                           │
        │                      digest_md (str)
        │                           │
        │         add_to_archive() ─┤
        │         save_archive()    │
        │                           ├──► save_markdown()  → ~/morning_digest/digest_YYYY-MM-DD.md
        │                           │
        └──► cfg["email_*"] ──► send_email()             → inbox
```

---

## 6. Prompt engineering

### System prompt

The system prompt defines the model's role and output contract. It is parameterised by `{digest_language}` only, keeping it stable across runs:

```
You are an interdisciplinary researcher. You have received a list of recent
articles and news found on the web. Your task is to select the 6-8 most
relevant ones at the intersection of: technology, computer science, AI,
ethics, philosophy, and sociology.
...
```

Key constraints encoded in the prompt:
- **Selection criteria:** prefer papers, analyses, op-eds over press releases and product announcements.
- **Output schema:** per-article block (title + URL in original language, N-line summary, thematic tag) followed by a Common Thread section.
- **Language separation:** titles and URLs stay in their original language; summaries are written in `digest_language`.

### User message structure

```
## Articles found on the web (last 3 days)

### [1] <title>
**URL:** <url>
**Search topic:** <topic>
**Snippet:** <snippet>

### [2] ...

## Specific preferences for today:
- Topics of interest: ...
- Tone: ...
- ...

---
Now produce the digest in Markdown format...
```

This is a RAG pattern: the retrieval (DuckDuckGo) is handled by the script; the augmentation is the context block; the generation is the model call. No vector embeddings are used — keyword search is sufficient for recent web content.

### Why no tool use / function calling?

Open-weight models served via Ollama have inconsistent support for structured tool use. Implementing search as a Python function called before the model — rather than as a tool the model invokes — gives deterministic retrieval behaviour independent of model version.

---

## 7. Deduplication system

### In-run deduplication

A `seen_urls: set` is maintained during `search_articles()`. Since multiple topics may surface the same article, this prevents duplicate entries in the list passed to the model within a single run.

### Cross-run deduplication (archive)

```
Run N:   fetch URLs → filter against archive → process → write to archive
Run N+1: fetch URLs → filter against archive (now includes Run N URLs) → ...
```

The 15-day window means an article that stops appearing in search results within 15 days will eventually be eligible again — appropriate for evergreen content that resurfaces. Articles from high-frequency sources that publish daily do not recur within the window.

### Archive integrity

If the archive JSON is corrupted (truncated file, encoding error), `load_archive()` catches the exception, logs a warning, and returns an empty dict. The run continues with no deduplication for that day, then writes a fresh archive from the day's results.

---

## 8. Configuration resolution

```python
cfg = dict(DEFAULTS)          # tier 1: hardcoded defaults
cfg.update(load_env_config()) # tier 2: .env file via python-dotenv
cfg.update(load_json_prefs()) # tier 3: --prefs JSON file
```

`KNOWN_PREF_FIELDS` is a static set of all recognised keys. Unknown keys in the JSON file trigger a `log.warning()` but do not raise — allowing users to add comments-like fields or forward-compatible extensions without breaking existing runs.

Type coercion is minimal: `smtp_port` is cast to `int` when read from `.env` (environment variables are always strings); all other fields are passed through as-is from their source.

---

## 9. Error handling strategy

The main pipeline runs inside a single `try/except Exception` block. Any unhandled exception:

1. Is logged with full traceback via `log.error(..., exc_info=True)`.
2. Triggers `send_error_email()` with the exception message and traceback.
3. Causes the process to exit with code 1.

Exit code 1 is meaningful for schedulers: cron will log it; Task Scheduler can be configured to alert on non-zero exit. This ensures silent failures are surfaced even when no one is watching the terminal.

`send_error_email()` itself is wrapped in a try/except to prevent an SMTP failure from suppressing the original error in the log.

---

## 10. Dependency analysis

| Library | Version constraint | Purpose | License |
|---|---|---|---|
| `ollama` | `>=0.3.0` | Ollama Python client | MIT |
| `ddgs` | `>=0.1.0` | DuckDuckGo Search | MIT |
| `python-dotenv` | `>=1.0.0` | `.env` file loader | BSD-3-Clause |

All other functionality uses the Python standard library (`argparse`, `smtplib`, `json`, `logging`, `pathlib`, `email`, `re`, `datetime`).

### Why these specific libraries

**`ollama`** (official client): provides typed request/response objects and handles streaming, connection pooling, and host configuration cleanly. The alternative — raw `httpx` calls to the Ollama REST API — would require reimplementing the same logic.

**`ddgs`**: the successor to `duckduckgo-search` (same author), it provides a simple `DDGS.text()` interface with built-in time filtering. No API key required; no rate-limit headers to manage at reasonable usage volumes.

**`python-dotenv`**: the de facto standard for `.env` loading in Python. Alternatives (`decouple`, manual `os.environ` reads) offer no meaningful advantage for this use case.

---

## 11. Known limitations and trade-offs

### Search quality

DuckDuckGo's `timelimit` parameter filters by crawl date, not publication date. Some freshly crawled old articles may appear in results. The model's selection step mitigates this by evaluating content relevance rather than accepting all results.

### Model response format

The model is instructed to produce Markdown, but open-weight models do not guarantee strict schema compliance. The Markdown-to-HTML converter is tolerant of minor format variations (extra blank lines, slightly different heading levels), but a heavily malformed response may produce degraded HTML.

### Single-threaded search

Topics are searched sequentially. For large topic lists, total search time grows linearly. Parallelising with `asyncio` or `ThreadPoolExecutor` is straightforward but was omitted to keep the code simple and avoid triggering DuckDuckGo rate limits.

### No content fetching

The script uses snippets only — it does not fetch or parse full article text. This keeps the context window small and the runtime fast, but the model works with limited information per article. For use cases requiring full-text analysis, a content fetcher (e.g. `newspaper3k`, `trafilatura`) could be inserted between search and prompt construction.

### Email as the only delivery channel

The current version supports only SMTP email delivery. Telegram, Slack, RSS, and local file-only modes are natural extension points (see §12).

---

## 12. Extension points

The pipeline is designed so that each stage can be replaced or augmented independently.

### Alternative delivery channels

`send_email()` is called at the end of `main()` with `(subject, html_body, cfg)`. Adding a Telegram sender requires:

1. A `send_telegram(digest_md, cfg)` function using `requests.post()` to the Bot API.
2. A call alongside or instead of `send_email()` in `main()`.
3. `telegram_token` and `telegram_chat_id` added to `KNOWN_PREF_FIELDS` and `DEFAULTS`.

### Alternative search backends

`search_articles()` returns `list[dict]` with keys `title`, `url`, `snippet`, `topic`. Any function with this signature can replace DuckDuckGo — Brave Search, SerpAPI, a local RSS aggregator, or an Atom feed parser.

### Full-text retrieval (RAG upgrade)

Between `search_articles()` and `build_full_prompt()`, a content-fetching step could replace snippets with full article text, then apply chunking and embedding-based selection to fit the context window. The rest of the pipeline is unchanged.

### Multiple output formats

`save_markdown()` and `md_to_html()` are independent. A PDF export step (e.g. via `weasyprint`), an RSS feed writer, or a static site generator could be added after `save_markdown()`.

---

## 13. License notices

### This project

MIT License — Copyright (c) 2025 Andrea Panizzut
Full text: [LICENSE](LICENSE)

### Third-party dependencies

**ollama** (ollama-python)
MIT License — Copyright (c) Ollama, Inc.
Source: https://github.com/ollama/ollama-python

**ddgs**
MIT License — Copyright (c) deedy5
Source: https://github.com/deedy5/ddgs

**python-dotenv**
BSD-3-Clause License — Copyright (c) Saurabh Kumar
Source: https://github.com/theskumar/python-dotenv

**Python standard library**
Python Software Foundation License Version 2
Source: https://docs.python.org/3/license.html

---

## Author

**Andrea Panizzut**
- Website: [andreapanizzut.it](https://www.andreapanizzut.it)
- LinkedIn: [linkedin.com/in/andreapanizzut](https://www.linkedin.com/in/andreapanizzut/)
- Email: [andrea.panizzut@gmail.com](mailto:andrea.panizzut@gmail.com)
