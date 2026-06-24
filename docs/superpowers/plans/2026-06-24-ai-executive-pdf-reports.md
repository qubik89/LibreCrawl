# AI Executive PDF Reports Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add manual OpenRouter-powered executive PDF report generation for completed crawls.

**Architecture:** SQLite stores report jobs, report settings, model cache, and generated report metadata. ClickHouse provides compact crawl summaries and examples. OpenRouter produces structured findings and final Markdown, then Playwright renders branded HTML to PDF.

**Tech Stack:** Flask, SQLite, ClickHouse, OpenRouter Chat Completions API, Markdown, Playwright PDF.

---

### Task 1: Report Settings and Model Cache

**Files:**
- Create: `src/reporting_settings.py`
- Create: `tests/test_reporting_settings.py`
- Modify: `requirements.txt` only if strictly needed; do not add a PDF library.

- [ ] Add SQLite-backed settings helpers for OpenRouter API key, default model, manual model, default tone, default language, agency name, primary color, footer text, and logo path.
- [ ] Add SQLite-backed model cache table storing `id`, `provider`, `name`, `context_length`, `pricing_json`, `supported_parameters_json`, and `fetched_at`.
- [ ] Add `filter_openrouter_models(models)` that keeps only `openai/`, `anthropic/`, and `deepseek/`.
- [ ] Add `safe_generation_params(model_metadata, desired_params)` that only returns params supported by the selected model; if metadata is missing, return only `max_tokens`.
- [ ] Add stdlib `unittest` coverage for provider filtering and parameter filtering.

### Task 2: Report Prompts and Audit Packet

**Files:**
- Create: `src/reporting_prompts.py`
- Create: `src/reporting_data.py`
- Create: `tests/test_reporting_prompts.py`
- Create: `tests/test_reporting_data.py`

- [ ] Add prompt constants for audit analysis and report writing.
- [ ] Add tone prompt mapping for `executive`, `technical`, and `commercial`.
- [ ] Add language handling for `es-ES` and `en`.
- [ ] Add `build_audit_packet(crawl_id)` that combines `build_crawl_summary`, ClickHouse summary, top status issues, top issue groups, and representative URL/link/issue rows.
- [ ] Keep audit packet bounded; no raw full-crawl dumps.
- [ ] Add tests for prompt selection and packet shape using stubbed dependencies.

### Task 3: Report Jobs and OpenRouter Client

**Files:**
- Create: `src/reporting_jobs.py`
- Create: `src/openrouter_client.py`
- Create: `tests/test_reporting_jobs.py`
- Create: `tests/test_openrouter_client.py`

- [ ] Add `report_jobs` SQLite table with `id`, `crawl_id`, `status`, `language`, `tone`, `model`, `markdown_path`, `html_path`, `pdf_path`, `error`, timestamps, and usage JSON.
- [ ] Add create/get/update helpers.
- [ ] Add `OpenRouterClient` using `requests.post` to `/api/v1/chat/completions`.
- [ ] Add `refresh_models(api_key)` using `/api/v1/models`, filtered through Task 1.
- [ ] Add `generate_structured_findings(...)` and `generate_report_markdown(...)`.
- [ ] Add tests using fake responses; no real API calls in tests.

### Task 4: PDF Renderer

**Files:**
- Create: `src/reporting_pdf.py`
- Create: `web/templates/report_pdf.html`
- Create: `tests/test_reporting_pdf.py`

- [ ] Add `render_report_html(markdown_text, branding, crawl_metadata)` using existing `markdown` package and Jinja template.
- [ ] Add `render_report_pdf(html, output_path)` using Playwright Chromium `page.pdf`.
- [ ] Store output under `/app/data/reports/<crawl_id>/`.
- [ ] Add tests for safe output paths and HTML contains branding fields.

### Task 5: API Endpoints

**Files:**
- Modify: `main.py`
- Create: `tests/test_reporting_api.py` if endpoint helpers can be tested without full Flask auth; otherwise add helper-level tests only.

- [ ] Add `GET /api/report-settings`.
- [ ] Add `POST /api/report-settings`.
- [ ] Add `POST /api/report-models/refresh`.
- [ ] Add `GET /api/report-models`.
- [ ] Add `POST /api/crawls/<id>/reports`.
- [ ] Add `GET /api/reports/<report_id>/status`.
- [ ] Add `GET /api/reports/<report_id>/download`.
- [ ] Reuse crawl ownership checks for report creation and downloads.

### Task 6: Frontend UI

**Files:**
- Modify: `web/templates/index.html`
- Modify: `web/templates/dashboard.html`
- Modify: `web/static/js/app.js`
- Modify: `web/static/js/settings.js`
- Modify: `web/static/css/style.css` if existing CSS file exists; otherwise keep inline with current patterns.

- [ ] Add Reports settings section with OpenRouter API key, refresh models, default provider/model, manual model, language, tone, and branding fields.
- [ ] Add completed-crawl "Generate PDF report" button.
- [ ] Add modal to confirm model, tone, language, and branding before generation.
- [ ] Poll report status.
- [ ] Show download button when complete.
- [ ] Keep large crawl data out of browser state.

### Task 7: Verification and Deployment

**Files:**
- Modify: `docker-compose.yml` only if env var documentation is needed.

- [ ] Run `python3 -m compileall main.py src tests`.
- [ ] Run all reporting tests plus existing crawl job/storage/clickhouse tests.
- [ ] Run `node --check web/static/js/app.js && node --check web/static/js/dashboard.js && node --check web/static/js/settings.js`.
- [ ] Run `docker compose config`.
- [ ] Commit and push to `mine server-crawl-jobs`.
- [ ] Deploy via Coolify and verify `/login` returns 200 and protected report endpoints return 401 without auth.
