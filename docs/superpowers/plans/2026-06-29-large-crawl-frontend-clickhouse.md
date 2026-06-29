# Large Crawl Frontend ClickHouse Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the crawl UI usable for very large crawls by polling small ClickHouse summaries, loading rows only on demand, and showing live samples without keeping the whole crawl in browser memory.

**Architecture:** Keep the current Flask + vanilla JS frontend. Add cursor/filter/sample support to existing ClickHouse-backed endpoints, then update `app.js` to poll summary data separately from row data. Do not add a frontend framework or new runtime dependency.

**Tech Stack:** Flask, vanilla JavaScript, ClickHouse, unittest/pytest-compatible tests.

---

## File Structure

- Modify `src/crawl_clickhouse.py`: add cursor-based page reads, lightweight filters, and latest sample helpers.
- Modify `main.py`: expose cursor/filter query parameters on existing row endpoints and add one small realtime sample endpoint.
- Modify `web/static/js/app.js`: stop fetching URL/link/issue pages every poll; add active-tab row loading, bounded client arrays, live sample refresh, and safer export request behavior.
- Modify `web/templates/index.html`: add minimal "Load more" buttons and a compact live sample section.
- Modify `tests/test_crawl_clickhouse.py`: cover ClickHouse SQL construction for cursor/filter/latest helper behavior without requiring a real ClickHouse server.

---

### Task 1: Backend ClickHouse Cursor, Filters, And Samples

**Files:**
- Modify: `src/crawl_clickhouse.py`
- Modify: `main.py`
- Test: `tests/test_crawl_clickhouse.py`

- [ ] **Step 1: Add failing tests for ClickHouse page SQL**

Append tests to `tests/test_crawl_clickhouse.py` using a fake client that records SQL. The tests must verify:
- `load_urls(crawl_id=7, limit=50, after=100, filters={'kind': 'internal'})` uses `row_order > 100`, `is_internal = 1`, `ORDER BY row_order ASC`, and returns `_row_order`.
- `load_issues(crawl_id=7, limit=10, filters={'issue_type': 'warning'})` includes `type = 'warning'`.
- `load_recent_urls(crawl_id=7, limit=5)` uses `ORDER BY row_order DESC`.

Run:
```bash
python -m unittest tests.test_crawl_clickhouse -v
```
Expected: FAIL because helper signatures do not exist yet.

- [ ] **Step 2: Implement minimal ClickHouse helpers**

In `src/crawl_clickhouse.py`:
- Extend `_query_json_rows(table, crawl_id, limit=500, offset=0, after=None, filters=None, descending=False)`.
- Keep existing `offset` behavior for old callers.
- If `after` is provided, use `row_order > after` and do not use `OFFSET`.
- Return each decoded JSON row with `'_row_order': int(row_order)`.
- Add allowlisted filters only:
  - URLs: `kind` values `internal`, `external`, `2xx`, `3xx`, `4xx`, `5xx`, `no_response`, `html`, `css`, `js`, `images`.
  - Links: `kind` values `internal`, `external`, `2xx`, `3xx`, `4xx`, `5xx`.
  - Issues: `issue_type` values `error`, `warning`, `info`.
- Add `load_recent_urls(crawl_id, limit=20)` and `load_recent_issues(crawl_id, limit=20)` using descending order.
- Keep SQL values numeric or from fixed allowlists only; no raw user strings.

Run:
```bash
python -m unittest tests.test_crawl_clickhouse -v
```
Expected: PASS.

- [ ] **Step 3: Wire API query params**

In `main.py`, update:
- `/api/crawls/<crawl_id>/urls`
- `/api/crawls/<crawl_id>/links`
- `/api/crawls/<crawl_id>/issues`

Accept query params:
- `after` integer cursor.
- `kind` for URL/link filters.
- `issue_type` for issue severity.

Return `next_cursor` as the last row's `_row_order`, or the current `after`/`offset` when no rows are returned.

Add:
```python
@app.route('/api/crawls/<int:crawl_id>/samples')
```
Return `recent_urls`, `recent_issues`, `analytics`, and `stats` from existing `build_crawl_summary()` plus ClickHouse latest helpers. Keep auth checks identical to the other crawl-by-id endpoints.

- [ ] **Step 4: Run targeted tests**

Run:
```bash
python -m unittest tests.test_crawl_clickhouse -v
```
Expected: PASS.

---

### Task 2: Frontend Polling, Active-Tab Loading, And Bounded Memory

**Files:**
- Modify: `web/static/js/app.js`
- Modify: `web/templates/index.html`

- [ ] **Step 1: Stop row-page fetches in the 1s poll**

In `pollCrawlProgress()` remove the call that fetches URLs, links, and issues every tick. The 1s loop should fetch only `/api/crawls/<id>/status` and update counters/progress.

- [ ] **Step 2: Add active-tab page loading**

In `web/static/js/app.js`:
- Replace `serverPageOffsets` with per-kind cursor state:
```js
let serverPageState = {
    urls: { nextCursor: null, loaded: 0, hasMore: true, filter: 'all' },
    links: { nextCursor: null, loaded: 0, hasMore: true, filter: 'all' },
    issues: { nextCursor: null, loaded: 0, hasMore: true, filter: 'all' }
};
```
- Add `loadActiveTabPage(reset = false)` and `loadServerRows(kind, reset = false)`.
- Load:
  - Overview/Internal/External from `/api/crawls/<id>/urls`.
  - Links from `/api/crawls/<id>/links`.
  - Issues from `/api/crawls/<id>/issues`.
- Keep `SERVER_PAGE_SIZE = 500`.
- Only append rows for the currently relevant dataset.
- Keep existing virtual scrollers.

- [ ] **Step 3: Bound client arrays**

Add:
```js
const CLIENT_ROW_LIMIT = 2000;
```
When appending rows for live browsing, keep only the newest 2000 rows per dataset. Use totals from `data.analytics.counts` for actual counts; do not imply the table contains every row.

- [ ] **Step 4: Load rows on attach/start/tab switch/filter change**

Call `loadActiveTabPage(true)`:
- after attaching to a server crawl,
- after starting a crawl when `crawl_id` is known,
- when `switchTab()` changes to overview/internal/external/links/issues,
- when a filter changes.

Add small "Load more" buttons in `web/templates/index.html` for overview, internal, external, links, and issues tabs:
```html
<button class="btn btn-secondary btn-small" onclick="loadActiveTabPage(false)">Load more</button>
```

- [ ] **Step 5: Safer export request**

In `exportData()`, when `crawlState.currentCrawlId` exists, do not fetch `/api/crawl_status` and do not send `localData` arrays. Send only `crawlId`, `format`, and `fields` to `/api/export_data`.

---

### Task 3: Live ClickHouse Sample UX

**Files:**
- Modify: `web/templates/index.html`
- Modify: `web/static/js/app.js`

- [ ] **Step 1: Add compact live sample HTML**

Add a small section near the stat cards:
```html
<section class="live-sample-panel" id="liveSamplePanel" style="display: none;">
    <div>
        <strong>Live sample</strong>
        <span id="liveSampleMeta">Waiting for crawl data...</span>
    </div>
    <div id="liveSampleUrls"></div>
    <div id="liveSampleIssues"></div>
</section>
```

- [ ] **Step 2: Fetch samples every 5 seconds for active server crawls**

In `web/static/js/app.js`:
- Add `let sampleRefreshTimer = null;`.
- Add `startSampleRefresh()`, `stopSampleRefresh()`, and `refreshLiveSamples()`.
- `refreshLiveSamples()` calls `/api/crawls/<id>/samples?limit=10`.
- Render the last 10 URLs and last 10 issues as short text rows.
- Stop timer when crawl stops or data is cleared.

- [ ] **Step 3: Keep sample display honest**

The sample panel must say it is a sample by label, and counts must come from ClickHouse summary/analytics, not from the sampled rows.

---

### Task 4: Verification And Production Safety

**Files:**
- No planned source changes unless tests expose a defect.

- [ ] **Step 1: Run targeted unit tests**

Run:
```bash
python -m unittest tests.test_crawl_clickhouse -v
```
Expected: PASS.

- [ ] **Step 2: Run broader relevant tests**

Run:
```bash
python -m unittest tests.test_crawl_clickhouse tests.test_crawl_storage tests.test_reporting_data -v
```
Expected: PASS.

- [ ] **Step 3: Review frontend fetch behavior**

Run:
```bash
rg -n "fetchServerPageUpdates|/api/crawl_status|/api/crawls/\\$\\{crawlId\\}/(urls|links|issues)" web/static/js/app.js
```
Expected:
- No polling path fetches URL/link/issue pages every second.
- `/api/crawl_status` is not used for export when `currentCrawlId` exists.

- [ ] **Step 4: Do not restart production automatically**

Because a production crawl is running, do not restart Coolify unless explicitly approved. Commit and push are acceptable only after tests pass; deployment/restart must be called out separately.

---

## Self-Review

- Spec coverage: covers lightweight polling, active-tab pagination, bounded browser memory, ClickHouse live sampling, cursor pagination, and safer export behavior.
- Placeholder scan: no TBD/TODO/implement-later placeholders.
- YAGNI check: no new frontend framework, no new dependencies, no async export job in this pass. The frontend no longer roundtrips huge local arrays; full streaming export can be added when exports become the bottleneck.
