# Report Suite 2.0 - AI prompt architecture

This document describes the production prompt chain. The executable source of
truth is `src/reporting_prompts.py`; contracts and deterministic checks live in
`src/reporting_documents.py`.

## Generation chain

One report pack uses a shared evidence snapshot and shared analysis:

1. `AuditFactsV2` exposes coverage, explicit denominators, a stable
   `metric_catalog`, evidence IDs, samples, enrichments and limitations.
2. The audit prompt produces one evidence-led `analysis` JSON object for the
   whole pack.
3. A product writer prompt composes executive, commercial or technical
   `document` JSON. It never writes HTML, CSS or new figures.
4. Deterministic validation checks metric values, denominators, evidence and
   finding references, recommendation completeness, white-label constraints
   and prohibited causal promises.
5. An independent reviewer scores factual integrity, clarity, usefulness,
   audience fit, language and white-label compliance. Passing requires at
   least 90/100 and no critical issue.
6. When either gate fails, the repair prompt gets immutable facts and a closed
   issue list. It may make one minimal repair. Both validators then run once
   more; there is no repair loop.
7. Only a passing package is rendered by Jinja into HTML and PDF. A failed
   package retains its facts, analysis, document, quality review and manifest
   for diagnosis, but is not published as a report.

## Prompt families

All families have complete Spanish (`es-ES`) and English (`en`) variants.

| Prompt | Responsibility | Output |
| --- | --- | --- |
| Audit analysis | Separate observation, inference and hypothesis; derive findings and recommendations from exact references. | `analysis` JSON |
| Product writer | Select the narrative, sections, actions and supported chart modules for one audience. | `document` JSON |
| Independent quality review | Find unsupported claims, contradictions, weak actionability, language and audience problems without rewriting. | `quality` JSON |
| Single repair | Correct only the closed issue list without altering facts or inventing outcomes. | repaired `analysis` + `document` JSON |

## Product contracts

The executive writer targets 6-8 A4 pages, up to six concise sections, no more
than five priorities, a 30/60/90 roadmap, KPIs and explicit decisions.

The commercial writer targets 10-14 pages and has separate narrative rules for
`prospect` and `existing_client`. Expected value must remain a testable
hypothesis; ROI, sales and ranking guarantees are forbidden.

The technical writer targets 20-35 pages plus appendices, up to sixteen
thematic sections, evidence samples, implementation instructions, owners,
dependencies, acceptance criteria, validation and KPIs.

## Hard rules

- The model may cite only exact IDs from `metric_catalog` and `evidence`.
- Missing GSC, GA4, CrUX or PageSpeed is `not_connected`, never zero.
- Crawl evidence cannot establish Google index presence, traffic, conversions,
  revenue or a Core Web Vital.
- `noindex`, missing schema and title/meta length are context-dependent, not
  automatic errors.
- LibreCrawl and Mitmore are prohibited inside white-label report content.
- HTML, CSS, charts and all arithmetic are deterministic application concerns.
- Prompt, facts, template, model, quality and usage versions are recorded in
  report artifacts.

## Model calls and cost envelope

For a three-report pack, the normal path is one shared analysis call, three
writer calls and three independent review calls. A report that fails a gate
adds exactly one repair and one final review. Token budgets are product-aware:
the technical document and repair receive the largest context allowance.

