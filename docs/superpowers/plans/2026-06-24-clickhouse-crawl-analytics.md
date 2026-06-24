# ClickHouse Crawl Analytics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Store large crawl result rows in ClickHouse so 250k URL crawls can be summarized and paged without loading everything into the browser or relying on SQLite scans.

**Architecture:** SQLite remains the control plane for jobs, auth, settings, and resume checkpoints. ClickHouse is an optional append-only analytics sink for crawled URLs, links, and issues; if unavailable, crawls continue and the app falls back to SQLite. The browser receives ClickHouse aggregate counts in crawl status responses.

**Tech Stack:** Flask, existing WebCrawler, SQLite, ClickHouse Docker service, `clickhouse-connect`.

---

### Task 1: Add ClickHouse Analytics Module

**Files:**
- Create: `src/crawl_clickhouse.py`
- Modify: `requirements.txt`
- Test: `tests/test_crawl_clickhouse.py`

- [x] Add an optional `clickhouse-connect` dependency.
- [x] Create schema helpers for `crawl_urls`, `crawl_links`, and `crawl_issues`.
- [x] Add batch insert helpers that serialize full original row payloads into `row_json` while storing scalar columns for summaries.
- [x] Add summary and paged-read helpers with safe fallback on disabled/unavailable ClickHouse.

### Task 2: Wire Crawler and API

**Files:**
- Modify: `src/crawler.py`
- Modify: `main.py`
- Modify: `web/static/js/app.js`

- [x] Mirror each existing SQLite batch save to ClickHouse after SQLite save.
- [x] Add ClickHouse analytics to `/api/crawls/<id>/status`.
- [x] Prefer ClickHouse for paged `/urls`, `/links`, and `/issues` reads when populated.
- [x] Let the UI render filter and status-code counts from aggregate analytics when available.

### Task 3: Add Coolify Compose Support

**Files:**
- Modify: `docker-compose.yml`

- [x] Add a `clickhouse` service using the official ClickHouse image.
- [x] Add persistent volumes for `/var/lib/clickhouse` and `/var/log/clickhouse-server`.
- [x] Add `CLICKHOUSE_*` env vars to `librecrawl`.

### Task 4: Verify and Deploy

**Commands:**
- `python3 -m compileall main.py src tests`
- `python3 -m unittest tests/test_crawl_jobs.py tests/test_crawl_clickhouse.py`
- `node --check web/static/js/app.js && node --check web/static/js/dashboard.js`

- [ ] Commit and push to `qubik89/LibreCrawl:server-crawl-jobs`.
- [ ] Switch Coolify app to Docker Compose only after confirming storage behavior.
- [ ] Trigger deploy and verify `/login` and `/api/crawls/<id>/status`.
