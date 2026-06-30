# Premium AI PDF Reports Design

## Decision

Use option C from the visual companion: a premium consulting report that opens with a strong executive brief, then uses restrained quantitative panels and ends with a clean technical appendix.

The report should feel like a board-ready SEO audit from a senior consulting team, not a dashboard export. It should prioritize decisions, evidence, and next actions over decorative charts.

## Design Context

### Users

Primary readers are agency clients, executives, marketing leads, and technical stakeholders reviewing an SEO crawl after it has completed. They need to understand business impact quickly, then inspect enough evidence to trust the recommendations.

### Use Cases

- Send a client-ready PDF after a completed crawl.
- Present the same crawl in different tones: executive, technical, or commercial.
- Compare generated report variants by language and tone.
- Download a polished report that does not require manual cleanup before forwarding.

### Brand Personality

Modern, executive, direct. The report should look expensive because it is precise, well-spaced, and selective. It should avoid generic dark SaaS styling, glossy dashboards, artificial gradients, and decorative filler.

## Research Summary

Playwright remains the PDF engine for the next iteration. It already supports print media, A4 output, millimeter margins, background printing, and CSS-controlled rendering, so replacing it now would add surface area without solving the core design problem.

Grafana is useful as a layout reference: panels should appear only when a query/data group has something meaningful to show, and panel density should be controlled by rows. We should borrow the discipline, not embed Grafana.

Vega and Plotly remain candidates for a later charting phase. Vega can render static SVG server-side from specs. Plotly can export SVG/PDF through Kaleido, but Kaleido depends on Chrome/Chromium discovery. Both add dependency and operational weight, so they are not first-cut choices while simple SVG charts can be generated safely in Python/HTML.

References:
- Playwright `page.pdf()` supports print CSS, unit-based margins, A4 format, and background printing: https://playwright.dev/python/docs/api/class-page
- Grafana dashboards organize useful data into panels, rows, and conditional visibility: https://grafana.com/docs/grafana/latest/visualizations/dashboards/build-dashboards/create-dashboard/
- Vega supports server-side SVG rendering with `view.toSVG()`: https://vega.github.io/vega/usage/
- Plotly static export requires Kaleido and Kaleido v1 relies on an installed Chrome/Chromium: https://plotly.com/python/static-image-export/

## Scope

### In

- Replace the current PDF template with a complete option-C design system.
- Add a report view model derived from the existing audit packet so the template can render metrics, risks, issue summaries, and evidence without parsing Markdown.
- Keep the AI-generated Markdown as the narrative body, but surround it with deterministic executive sections.
- Add simple SVG/HTML charts generated from existing crawl summary data:
  - Crawl health composition.
  - Issue severity/type distribution.
  - Top HTTP problem groups.
  - Impact/effort priority bars when enough data exists.
- Add print-specific page structure:
  - Cover/brief page.
  - Executive summary section.
  - Evidence panels.
  - Recommendations.
  - Technical appendix.
- Preserve current report variant paths by language and tone.

### Out

- Installing Grafana.
- Adding Plotly/Kaleido or Vega in this pass.
- Building a report template editor.
- Letting the AI generate raw HTML or chart code.
- Inventing metrics not present in the crawl packet.

## Architecture

Keep the current flow:

1. `build_audit_packet(crawl_id)` gathers bounded crawl evidence.
2. OpenRouter generates structured findings and final Markdown.
3. A new deterministic report view model converts audit packet + report job options into display data.
4. `render_report_html()` renders the template with:
   - sanitized Markdown narrative,
   - branding,
   - crawl metadata,
   - deterministic report metrics/charts.
5. Playwright renders PDF.

This keeps AI responsible for narrative and keeps layout, metrics, and safety in application code.

## Report View Model

Create a small helper in `src/reporting_pdf.py` or a nearby module if the file becomes crowded. It should use only data already present in the audit packet:

- `crawl_metadata`: domain, base URL, crawl id, status, started/completed dates.
- `crawl_summary.counts`: URLs, links, issues when available.
- `analytics_summary.status_counts`: status code distribution.
- `analytics_summary.issue_type_counts`: issue distribution.
- `top_status_issues`: HTTP failures/timeouts.
- `top_issue_groups`: issue categories/types.
- `samples`: representative URL/link/issue rows.

The helper should degrade quietly: if a data group is missing, omit that panel instead of showing fake zeros.

## Visual System

Use a restrained executive palette:

- Paper: warm off-white.
- Ink: near-black graphite.
- Accent: brand color for one or two functional highlights.
- Risk colors: muted amber/red/green only where they encode status.

Typography should use one strong sans-serif stack with clear weights. No serif shortcut, no uppercase eyebrow labels, no pill badges, no oversized rounded cards, no gradients as polish.

Layout should use:

- Fixed A4 print rhythm.
- Normal 6-10px radius only where containers need edges.
- Strong table styling for evidence.
- Data panels that read like consulting exhibits, not app cards.
- `break-inside: avoid` for tables, chart panels, and recommendation blocks.

## Template Structure

The new PDF template should render:

1. Cover/brief:
   - Report title.
   - Domain and crawl metadata.
   - Three deterministic stats when present.
   - One concise report framing line.

2. Executive brief:
   - AI Markdown opening content.
   - Key risk table from top issue/status groups.

3. Evidence panels:
   - Status distribution.
   - Issue distribution.
   - Top problem groups.
   - Sample affected URLs.

4. Recommendations:
   - AI narrative recommendations.
   - Deterministic priority/action table derived from top status issues, top issue groups, and representative samples.

5. Technical appendix:
   - Representative URLs, links, issues.
   - Crawl limits and source metadata.

## Charting

First pass uses inline SVG generated from safe numeric data. That is enough for bars, stacked bars, and simple distribution charts.

Add a dedicated charting dependency only if we need:

- time series with axes and legends,
- multi-series comparisons,
- scatter/bubble plots,
- client-specific theming across many reports.

If that happens, prefer Vega SVG before Plotly/Kaleido because SVG output fits PDF rendering and avoids Python-side browser discovery duplication.

## Error Handling

- Missing summary data: omit the affected panel.
- Empty report model: render the AI narrative and metadata only.
- Unsafe or malformed chart values: clamp to zero and omit impossible percentages.
- Long URLs: wrap aggressively in tables.

## Testing

Add focused tests for:

- Report view model generation from a representative audit packet.
- Missing-data degradation.
- HTML output includes the new premium sections.
- SVG/chart helpers clamp invalid values.
- Playwright PDF call still uses A4, print backgrounds, and margins.

Manual verification:

- Render a representative HTML preview.
- Capture a screenshot with Playwright.
- Generate one PDF smoke file.

## Open Questions

- Whether to store AI structured findings separately from Markdown for richer deterministic recommendation tables. This is useful, but not required for the first visual upgrade.
- Whether a future "agency theme" setting should expose a full palette. For now, keep one brand color and derive the rest.
