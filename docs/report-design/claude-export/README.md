# LibreCrawl Report Suite 2.0 — prototype package

Evidence-first report system. Three products, one component library, one
derivation layer. Every displayed value binds to a field of an `AuditFactsV2`
snapshot; nothing is hardcoded and no business claim is invented.

## Files

```
Executive.dc.html      Informe ejecutivo    (6–8 A4)    → dirección
Commercial.dc.html     Informe comercial    (10–14 A4)  → cliente potencial / en activo
Technical.dc.html      Informe técnico      (20–35 A4)  → SEO, ingeniería, operaciones
Components.dc.html     Component gallery + white-label tokens + binding contract

report-data.js         Shared derivation layer (classic script → window.ReportData)

ModCover.dc.html          1. Portada (dos variantes) + metadatos + tres anclas
ModCoverage.dc.html       2. Evidence coverage strip
ModScorecard.dc.html      3. Cuadro temático transparente
ModBars.dc.html           4a. Barras: status / profundidad / grupos / percentiles
ModFunnel.dc.html         4b. Embudo de indexabilidad
ModFindings.dc.html       6. Fila de hallazgo
ModPlan.dc.html           7. Matriz impacto-esfuerzo + 30/60/90
ModBacklog.dc.html        8. Backlog con owner, dependencia, criterio, validación, KPI
ModEvidence.dc.html       9a. Tabla de evidencia — incidencias
ModEvidenceUrls.dc.html   9b. Tabla de evidencia — URLs
ModEvidenceLinks.dc.html  9c. Tabla de evidencia — enlaces
ModMethod.dc.html        10. Método, fuentes, límites, glosario

fixtures/ialovers.json     15 URLs · 42 incidencias · 12/15 noindex
fixtures/octav.json        55 URLs · 225 incidencias · links truncados a 500/932
fixtures/davidayala.json  434 URLs · 500/1412 incidencias · 13× 404 · sin grafo de enlaces
fixtures/english.json      Fixture mínimo en inglés para validar localización sin ampliar los nueve entregables
```

The three fixtures were generated from the raw v1.1 crawl exports by porting
`build_audit_facts` / `_facts_from_rows` / `stratified_samples` (SHA-256 stable
keys included) from the attached contract. They are contract-shaped: same keys,
same denominators, same limitation strings.

## Deviations from the brief, stated plainly

- **`report.css` / `report.js` are not separate files.** Styling is inline on
  every node so the design paints as it streams; the only global CSS is the
  `@page` rule, the token `:root` block and body resets, duplicated verbatim in
  each report's `<helmet>`. Lift that block into `report.css` and the token
  media queries carry the whole responsive system with them — no component rule
  needs to move. `report-data.js` is the `report.js` equivalent.
- **`discovered_urls`** is populated from the export's `stats.crawled`
  (fetch attempts), not from a true discovery counter, which the v1.1 export
  does not expose. Labelled in the UI as "Peticiones realizadas" and the ratio
  as `unique_urls ÷ discovered_urls`. Rename the key if the real pipeline has a
  better source.
- **`evidence.*` rows are projected** to the ~20 fields the reports read,
  instead of whole crawler rows, to keep fixture size sane.
- **Rendimiento de rastreo is deliberately unscored.** The contract exposes
  percentiles but not per-URL threshold counts, so there is no honest
  numerator/denominator. It renders as a distribution with an explicit
  "No puntuable" state rather than an invented rate.

## Binding contract

| Attribute | Values |
| --- | --- |
| `data-report-type` | `executive` · `commercial` · `technical` |
| `data-commercial-context` | `prospect` · `existing_client` |
| `data-module` | `cover` `cover-metrics` `evidence-coverage` `scorecard` `chart` `findings` `plan` `impact-effort` `roadmap` `backlog` `evidence-table` `methodology` `data-sources` `limitations` `glossary` `mechanisms` `unavailable` |
| `data-field` | dotted path into the snapshot, e.g. `coverage.denominators.html_2xx_urls` |
| `data-chart` | `status_codes` `depth` `issue_groups` `response_time_percentiles` `indexability_funnel` `sitemap_overlap` |
| `data-evidence` | `issues` · `urls` · `links` |

Charts are semantic HTML/CSS bars (no SVG, no library) and each carries a
textual equivalent as a sibling paragraph, so they survive Playwright PDF and
screen readers identically.

## White-label tokens

`--brand-primary` `--brand-secondary` `--brand-accent` `--brand-logo`
`--report-author` `--report-contact` `--report-confidentiality`

Declared in each report's `:root`. The prototype also exposes them as tweakable
props; the logic class writes the three colours onto `documentElement` so a
Jinja renderer can instead emit them server-side into the `:root` block.
Status is never colour-alone: every state carries a shape (`● ◐ ▲ ○ ◆`), a
label and its numeric value.

## Print

`@page { size: A4; margin: 20mm 15mm 18mm }` and
`<meta name="omelette-owns-print">`. The horizontal/vertical page margins are
sized to leave room for a Playwright `headerTemplate` / `footerTemplate` with
page numbers — Chrome does not support `@page` margin boxes, so running
header/footer belong to the PDF pipeline, not the CSS. Findings, actions,
chart figures and table rows are `break-inside: avoid`; long tables have a
`<thead>` so browsers repeat it per page; every URL cell breaks on any
character.

## Responsive

No component media queries. The `:root` token block re-declares the grid
templates and the type scale at 900 px and 560 px, and every component consumes
`var(--c2)`, `var(--find)`, `var(--act)`, `var(--h1)`… So one block governs
1440 / 1024 / 390 / A4, and components stay class-free and inline-styled.
