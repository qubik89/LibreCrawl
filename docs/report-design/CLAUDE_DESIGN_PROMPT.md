# LibreCrawl Report Suite 2.0 - Claude Design Brief

Use the attached `AuditFactsV2` contract and the three fixtures in this folder.
Create a premium, fully white-label report system for a technical SEO audit.
The work must look like an evidence-led consulting deliverable, not a SaaS
dashboard or a marketing landing page.

## Goal

Design a single component system that composes three distinct products:

| Product | Reader | Print target | Primary outcome |
| --- | --- | --- | --- |
| Executive | CEO, CMO, leadership | 6-8 A4 pages | Decide priorities, owners and sequence. |
| Commercial | Prospect or existing client | 10-14 A4 pages | Understand opportunity and agree the next workstream. |
| Technical | SEO, engineering and operations | 20-35 A4 pages plus appendices | Implement and verify changes. |

Do not use the LibreCrawl or Mitmore name, logo, watermark, default agency copy,
placeholder customer, invented score, invented conversion impact or invented
revenue. Every displayed metric must bind to a fixture field.

## Required Deliverables

Return a self-contained prototype package with these files:

```text
executive.html
commercial.html
technical.html
components.html
report.css
report.js
fixtures/ialovers.json
fixtures/octav.json
fixtures/davidayala.json
fixtures/english.json (localisation smoke fixture; do not add it to the nine production renders)
```

Use semantic HTML, local CSS and vanilla JavaScript only. Do not use a CDN,
external font request, chart library, image service or hardcoded metrics. Mark
all bindable elements with `data-module`, `data-field`, `data-chart` or
`data-evidence` attributes so a Jinja renderer can replace the fixture data.

## Visual Direction

Use an evidence-first editorial direction:

- Bright white paper, near-black ink, restrained cool neutral rules and the
  client brand color only as an accent.
- A precise A4 grid, high information density, generous but purposeful spacing,
  a compact editorial sans-serif with a local fallback stack and tabular numbers.
- Strong hierarchy through type, rules, spacing and alignment. Avoid gradients,
  glass, 3D charts, decorative blobs, oversized empty covers, nested cards and
  generic dashboard tiles.
- A cover may be expressive but must expose the client name, domain, report
  type and three factual metrics above the fold. Produce two alternative covers
  in `components.html`; make the first the default in each report.
- White-label tokens must include `--brand-primary`, `--brand-secondary`,
  `--brand-accent`, `--brand-logo`, `--report-author`, `--report-contact` and
  `--report-confidentiality`.
- Status must never rely on color alone. Add labels, values, patterns or shape
  differences. Meet WCAG 2.2 AA contrast.

## Shared Modules

Build these reusable modules once and compose them differently per report:

1. Cover, report metadata, logo, confidentiality and three metric anchors.
2. Evidence coverage strip with source, denominator, crawl coverage and limits.
3. Transparent thematic scorecard; show formula and denominator, never a black-box health score.
4. HTTP/status bars, indexability funnel, sitemap overlap, depth histogram and response-time percentiles.
5. Issue heatmap or ranked issue groups, always with count and denominator.
6. Finding row: severity, confidence, observation, optional inference/hypothesis and limitation.
7. Impact/effort matrix and 30/60/90 roadmap.
8. Recommendation backlog with owner, dependency, acceptance criterion, validation and KPI.
9. Representative evidence table that handles long URLs without overflowing.
10. Methodology, data-source status and glossary.

Use inline SVG or semantic HTML/CSS bars for charts so they render identically
in web HTML and a Playwright PDF. Charts must have a textual equivalent.

## Product Composition

### Executive

Show decision brief, data confidence, five priorities at most, opportunity and
risk, impact/effort, 30/60/90 roadmap, KPIs and required decisions. Keep the
technical evidence available but secondary. Never describe crawl response time
as LCP, INP or a Google ranking signal.

### Commercial

Support `data-commercial-context="prospect"` and `"existing_client"`.
Show evidence, acquisition/trust/conversion mechanisms, quick wins, workstreams,
scope and next step. Do not fabricate pricing, ROI, traffic, revenue or ranking
outcomes. When GA4/GSC are not connected, use an explicit unavailable state.

### Technical

Show methodology, crawl coverage, thematic chapters, issue definitions, exact
counts and denominators, examples, template/pattern implications, prioritised
backlog, implementation dependencies, acceptance criteria and re-crawl checks.
Use appendix patterns that can grow to many rows without layout failure.

## Print And Responsive Rules

Design for 1440 px, 1024 px, 390 px and A4 print. Use `@page`, avoid orphaned
headings, repeat table headers, keep finding/action rows intact, provide room
for running header/footer and page numbers, and ensure long domains/URLs wrap.
The HTML version may expose expandable evidence; the printed version must show a
deterministic complete summary.

## Acceptance Checklist

- All three reports use the same tokens and components but clearly serve their reader.
- All content comes from fixture data; no lorem ipsum or invented business claim.
- The system still looks deliberate when a chart, enrichment or logo is absent.
- The technical report remains readable with 20 issue groups and 60 evidence rows.
- Desktop, mobile and print avoid overlap, clipping and horizontal scrolling.
- Export screenshots of the executive cover, commercial opportunity spread and technical evidence page at desktop and A4.
