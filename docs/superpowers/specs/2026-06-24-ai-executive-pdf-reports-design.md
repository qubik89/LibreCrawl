# AI Executive PDF Reports Design

## Goal

Add a manual "Generate PDF report" workflow for completed crawls. The report uses ClickHouse audit summaries, OpenRouter model selection, tone/language prompts, and minimal agency branding to produce one client-ready PDF per generation.

## Scope

V1 includes:
- Manual report generation only.
- One PDF in the selected language: Spanish from Spain (`es-ES`) or English (`en`).
- Tone selector: executive, technical, commercial.
- OpenRouter models from OpenAI, Anthropic, and DeepSeek, refreshed from `/api/v1/models`.
- Optional manual model override.
- Safe request payload construction using each model's `supported_parameters`.
- Branding settings: logo, agency name, primary color, footer/contact text.
- Server-side report job with status polling and downloadable PDF output.

V1 does not include:
- Automatic report generation after every crawl.
- Multi-language batch generation.
- Full template editor.
- Sending full 250k URL payloads to the model.

## Architecture

SQLite remains the control plane for report jobs, settings, model cache, and report metadata. ClickHouse provides aggregated crawl evidence and representative examples. OpenRouter generates two text artifacts: structured audit findings first, then report Markdown. Playwright renders the final branded HTML to PDF.

## Data Flow

1. User opens a completed crawl and clicks "Generate PDF report".
2. UI shows model, tone, language, and branding summary.
3. Server creates a report job.
4. Server builds a compact audit packet from ClickHouse and SQLite crawl metadata.
5. OpenRouter call 1 generates structured findings by audit category.
6. OpenRouter call 2 generates final Markdown using tone and language prompts.
7. Server converts Markdown to branded HTML.
8. Playwright renders the HTML to PDF.
9. UI polls status and exposes a download link.

## OpenRouter Model Handling

The model refresh endpoint fetches `https://openrouter.ai/api/v1/models` and keeps only model IDs starting with:
- `openai/`
- `anthropic/`
- `deepseek/`

The cache stores `id`, `name`, `provider`, `context_length`, `pricing`, `supported_parameters`, and `fetched_at`.

When generating, LibreCrawl constructs a base request with `model` and `messages`, then only includes optional parameters present in `supported_parameters`. If the user provides a manual model and there is no cached metadata, the request uses the minimum payload: `model`, `messages`, and `max_tokens`.

Reasoning is sent only when the model supports `reasoning`. Temperature is sent only when the model supports `temperature`. Structured JSON is requested only when `response_format` or `structured_outputs` is supported.

## Prompts

There are two prompt layers:
- Audit analysis prompt: converts crawl data into structured findings by category.
- Report writing prompt: converts structured findings into client-facing Markdown using the selected tone and language.

The analysis prompt evaluates crawlability, HTTP health, indexability, information architecture, metadata, headings, content duplication, technical SEO, performance, images, accessibility basics, structured data, social metadata, security/trust signals, and prioritization.

Each finding includes severity, impact area, confidence, affected URL count, examples, probable template/pattern, business impact, technical explanation, recommended action, effort, and priority order.

## PDF Rendering

LibreCrawl already ships Playwright and browser dependencies. V1 uses Playwright Chromium PDF generation instead of adding WeasyPrint or ReportLab.

The PDF renderer:
- Converts Markdown to HTML using the existing `markdown` dependency.
- Injects branding settings into a report template.
- Renders with Playwright.
- Saves PDFs under `/app/data/reports/<crawl_id>/`.

## UI

Settings gains an "Reports" section with:
- OpenRouter API key.
- Refresh models button.
- Default provider/model.
- Manual model override.
- Default tone.
- Default language.
- Logo upload or URL/path.
- Agency name.
- Primary color.
- Footer/contact text.

Completed crawl views gain:
- "Generate PDF report" button.
- Report modal with language, tone, model, manual override, and current branding summary.
- Report status polling.
- Download PDF button when complete.

## Error Handling

If ClickHouse has no crawl data, report generation fails with a clear message. If OpenRouter returns an error, the report job stores the provider error summary. If Playwright PDF rendering fails, the generated Markdown/HTML remains stored for debugging and retry.

## Security

The OpenRouter API key is stored server-side only and never sent to the browser. Report prompts must not include secrets. User-visible download endpoints enforce the same crawl ownership checks as crawl data endpoints.

## Testing

V1 gets focused stdlib tests for:
- Model filtering by provider.
- Safe parameter filtering using `supported_parameters`.
- Prompt selection by language and tone.
- Report job status transitions.
- PDF filename/path construction.
