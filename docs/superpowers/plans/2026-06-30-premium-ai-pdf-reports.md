# Premium AI PDF Reports Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade AI PDF reports to the approved option-C executive consulting layout using deterministic crawl metrics and simple built-in charts.

**Architecture:** Keep Playwright and the existing OpenRouter flow. Add a small report view model in `src/reporting_pdf.py` that derives safe metrics, distribution bars, and evidence rows from the existing audit packet, then render those sections in `web/templates/report_pdf.html` around the sanitized AI Markdown.

**Tech Stack:** Python stdlib, Jinja2, Markdown, Playwright, HTML/CSS. No new dependencies.

---

### Task 1: Report View Model

**Files:**
- Modify: `src/reporting_pdf.py`
- Test: `tests/test_reporting_pdf.py`

- [ ] **Step 1: Write failing tests**

Add tests for `build_report_view_model()`:

```python
def test_build_report_view_model_uses_audit_packet_metrics_and_distributions(self):
    packet = {
        'crawl_metadata': {'id': 42, 'base_url': 'https://example.com', 'base_domain': 'example.com'},
        'crawl_summary': {'counts': {'urls': 100, 'links': 250, 'issues': 30}},
        'analytics_summary': {
            'status_counts': [
                {'status_code': 200, 'count': 80},
                {'status_code': 301, 'count': 10},
                {'status_code': 404, 'count': 7},
                {'status_code': 500, 'count': 3},
            ],
            'issue_type_counts': {'error': 12, 'warning': 18},
        },
        'top_status_issues': [{'status_code': 404, 'count': 7}],
        'top_issue_groups': [{'type': 'error', 'issue': 'Missing title', 'count': 12}],
        'samples': {'issues': {'rows': [{'url': 'https://example.com/a', 'issue': 'Missing title'}]}},
    }

    model = reporting_pdf.build_report_view_model(packet)

    self.assertEqual(model['domain'], 'example.com')
    self.assertEqual(model['stats'][0], {'label': 'URLs rastreadas', 'value': '100'})
    self.assertEqual(model['status_distribution'][0]['label'], 'Correctas')
    self.assertEqual(model['status_distribution'][0]['percent'], 80)
    self.assertEqual(model['issue_distribution'][0]['label'], 'error')
    self.assertEqual(model['priority_rows'][0]['label'], 'HTTP 404')
    self.assertEqual(model['sample_issues'][0]['url'], 'https://example.com/a')
```

Add a degradation test:

```python
def test_build_report_view_model_omits_missing_groups(self):
    model = reporting_pdf.build_report_view_model({})

    self.assertEqual(model['stats'], [])
    self.assertEqual(model['status_distribution'], [])
    self.assertEqual(model['priority_rows'], [])
```

- [ ] **Step 2: Run tests red**

Run: `python3 -m unittest tests.test_reporting_pdf`

Expected: FAIL because `build_report_view_model` does not exist.

- [ ] **Step 3: Implement view model helpers**

Add `build_report_view_model(audit_packet)` and small private helpers in `src/reporting_pdf.py`. Keep them local and deterministic:

```python
def build_report_view_model(audit_packet):
    packet = audit_packet or {}
    metadata = packet.get('crawl_metadata') or {}
    summary = packet.get('crawl_summary') or {}
    counts = _first_dict(summary.get('counts'), (packet.get('analytics_summary') or {}).get('counts'))
    return {
        'domain': metadata.get('base_domain') or metadata.get('base_url') or 'Sitio analizado',
        'base_url': metadata.get('base_url'),
        'crawl_id': metadata.get('id') or packet.get('crawl_id'),
        'stats': _report_stats(counts),
        'status_distribution': _status_distribution((packet.get('analytics_summary') or {}).get('status_counts')),
        'issue_distribution': _issue_distribution((packet.get('analytics_summary') or {}).get('issue_type_counts')),
        'priority_rows': _priority_rows(packet.get('top_status_issues'), packet.get('top_issue_groups')),
        'sample_issues': ((packet.get('samples') or {}).get('issues') or {}).get('rows') or [],
    }
```

- [ ] **Step 4: Run tests green**

Run: `python3 -m unittest tests.test_reporting_pdf`

Expected: OK.

### Task 2: Pass Audit Packet Into HTML Rendering

**Files:**
- Modify: `src/reporting_pdf.py`
- Modify: `main.py`
- Test: `tests/test_reporting_api.py`
- Test: `tests/test_reporting_pdf.py`

- [ ] **Step 1: Write failing tests**

Update `tests/test_reporting_api.py::test_run_report_job_generates_files_and_marks_completed_with_mocks` so `render_report_html` must be called with the audit packet:

```python
html.assert_called_once_with(
    '# Report',
    {'agency_name': 'Agency'},
    {'base_url': 'https://example.test'},
    audit_packet={'crawl_metadata': {'base_url': 'https://example.test'}},
)
```

Add an HTML test asserting report model sections render when an audit packet is passed:

```python
html = reporting_pdf.render_report_html('# Audit', {}, {'base_domain': 'example.com'}, audit_packet={
    'crawl_metadata': {'base_domain': 'example.com'},
    'crawl_summary': {'counts': {'urls': 10, 'links': 20, 'issues': 3}},
})
self.assertIn('class="executive-brief"', html)
self.assertIn('URLs rastreadas', html)
```

- [ ] **Step 2: Run tests red**

Run: `python3 -m unittest tests.test_reporting_pdf tests.test_reporting_api`

Expected: FAIL because `render_report_html` does not accept `audit_packet` and `main.run_report_job` does not pass it.

- [ ] **Step 3: Implement passing**

Change `render_report_html(markdown_text, branding, crawl_metadata, audit_packet=None)` and add `report=build_report_view_model(audit_packet or {'crawl_metadata': crawl_metadata or {}})` to `template.render(...)`.

Change `main.run_report_job()` to call:

```python
html = render_report_html(
    markdown_text,
    options.get('branding'),
    audit_packet.get('crawl_metadata') or {},
    audit_packet=audit_packet,
)
```

- [ ] **Step 4: Run tests green**

Run: `python3 -m unittest tests.test_reporting_pdf tests.test_reporting_api`

Expected: OK.

### Task 3: Premium Option-C Template

**Files:**
- Modify: `web/templates/report_pdf.html`
- Test: `tests/test_reporting_pdf.py`

- [ ] **Step 1: Write failing section assertions**

In the existing render HTML test, assert new deterministic sections:

```python
self.assertIn('class="cover-metrics"', html)
self.assertIn('class="executive-brief"', html)
self.assertIn('class="evidence-panels"', html)
self.assertIn('class="technical-appendix"', html)
```

- [ ] **Step 2: Run tests red**

Run: `python3 -m unittest tests.test_reporting_pdf`

Expected: FAIL until the template includes these sections.

- [ ] **Step 3: Replace template body/style**

Keep one template file. Render:
- cover header with domain and stat strip,
- `executive-brief` containing `report_html`,
- `evidence-panels` with status/issue bars only when present,
- `technical-appendix` with sample issues only when present.

Use normal containers, subdued colors, simple borders, and no new dependencies.

- [ ] **Step 4: Run tests green**

Run: `python3 -m unittest tests.test_reporting_pdf`

Expected: OK.

### Task 4: Visual Smoke Verification

**Files:**
- No committed files unless tests expose a defect.

- [ ] **Step 1: Run reporting tests**

Run: `python3 -m unittest tests.test_reporting_pdf tests.test_reporting_api tests.test_reporting_jobs`

Expected: OK.

- [ ] **Step 2: Render representative preview**

Use a short Python command to render `/tmp/librecrawl-premium-report.html` with representative audit packet data.

- [ ] **Step 3: Capture screenshot and PDF**

Run:

```bash
python3 -m playwright screenshot file:///tmp/librecrawl-premium-report.html /tmp/librecrawl-premium-report.png
```

Then render a PDF through `render_report_pdf()` to `/tmp/librecrawl-reports/42/es-es/executive/report-premium.pdf`.

Expected: screenshot and PDF files exist and have nonzero size.
