(function () {
    const REFRESH_MS = 5000;
    let refreshTimer = null;
    let requestController = null;
    let lastSnapshot = null;

    const priorityLabels = {
        critical: 'Critica',
        high: 'Alta',
        medium: 'Media',
        opportunity: 'Oportunidad'
    };

    const statusLabels = {
        '2xx': 'Correctas',
        '3xx': 'Redirecciones',
        '4xx': 'Cliente',
        '5xx': 'Servidor',
        no_response: 'Sin respuesta'
    };

    function escape(value) {
        if (typeof window.escapeHtml === 'function') return window.escapeHtml(String(value || ''));
        return String(value || '').replace(/[&<>'"]/g, character => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
        })[character]);
    }

    function format(value, options) {
        return Number(value || 0).toLocaleString(undefined, options || {});
    }

    function percentage(value) {
        return value === null || value === undefined ? 'Sin datos' : `${Number(value).toFixed(1)}%`;
    }

    function ratio(numerator, denominator) {
        return denominator ? (Number(numerator || 0) / denominator) * 100 : null;
    }

    function statusText(status) {
        return ({ running: 'Rastreo en curso', paused: 'Rastreo en pausa', completed: 'Rastreo completado', failed: 'Rastreo fallido', stopped: 'Rastreo detenido' })[status] || 'Rastreo disponible';
    }

    function updatedAt(value) {
        if (!value) return 'Actualizacion no disponible';
        const date = new Date(value);
        return Number.isNaN(date.getTime()) ? 'Actualizacion no disponible' : `Actualizado ${date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`;
    }

    function render(snapshot, stale) {
        const container = document.getElementById('crawlOverviewDashboard');
        if (!container) return;
        if (!snapshot) {
            container.innerHTML = '<div class="overview-empty-state"><h2>Resumen de auditoria</h2><p>Inicia o carga un rastreo para ver la salud tecnica del sitio.</p></div>';
            return;
        }

        const crawl = snapshot.crawl || {};
        const coverage = snapshot.coverage || {};
        const http = snapshot.http || {};
        const issues = snapshot.issues || {};
        const performance = snapshot.performance || {};
        const links = snapshot.links || {};
        const internal = Number(coverage.internal_urls || 0);
        const html = Number(coverage.html_2xx_urls || 0);
        const indexable = Number(coverage.indexable_html_urls || 0);
        const successRate = ratio(http['2xx'], internal);
        const indexability = ratio(indexable, html);
        const severity = Object.fromEntries((issues.by_severity || []).map(row => [row.type, row.count]));
        const partial = Boolean(crawl.partial);

        container.innerHTML = `
            <header class="overview-header">
                <div>
                    <p class="overview-kicker">Auditoria tecnica</p>
                    <h2>${escape(crawl.base_url || 'Rastreo sin dominio')}</h2>
                    <p class="overview-meta">${escape(statusText(crawl.status))}${partial ? ' · Datos parciales' : ''}</p>
                </div>
                <div class="overview-status">
                    <strong>${format(crawl.crawled)} / ${format(crawl.discovered)}</strong>
                    <span>${percentage(crawl.progress)}</span>
                    <small>${updatedAt(snapshot.generated_at)}</small>
                    ${stale ? '<span class="overview-stale">Lectura anterior</span>' : ''}
                </div>
            </header>

            <section class="overview-kpis" aria-label="Indicadores principales">
                ${metricCard('URLs internas', format(internal), `${format(coverage.external_urls)} externas`, 'neutral')}
                ${metricCard('HTML indexables', format(indexable), `${percentage(indexability)} de HTML 2xx`, 'good')}
                ${metricCard('Respuestas correctas', percentage(successRate), `${format(http['2xx'])} URLs 2xx`, successRate !== null && successRate < 90 ? 'warning' : 'good', 'http')}
                ${metricCard('Incidencias', format(issues.total), `${format(severity.error)} errores · ${format(severity.warning)} avisos`, issues.total ? 'warning' : 'good', 'issues')}
                ${metricCard('Respuesta p75', performance.p75_ms === null || performance.p75_ms === undefined ? 'Sin datos' : `${format(performance.p75_ms)} ms`, 'Medicion del rastreador', performance.p75_ms > 3000 ? 'critical' : 'neutral')}
            </section>

            <section class="overview-main-grid">
                <div class="overview-panel priority-panel">
                    <div class="overview-panel-heading"><div><p class="overview-kicker">Orden de trabajo</p><h3>Problemas prioritarios</h3></div><span>Impacto y alcance</span></div>
                    <div class="priority-list">${renderPriorities(issues.top || [])}</div>
                </div>
                <div class="overview-panel issue-chart-panel">
                    <div class="overview-panel-heading"><div><p class="overview-kicker">Incidencias</p><h3>Por categoria</h3></div></div>
                    <div class="category-bars">${renderCategories(issues.by_category || [], issues.total || 0)}</div>
                </div>
            </section>

            <section class="overview-chart-grid">
                <div class="overview-panel">
                    <div class="overview-panel-heading"><div><p class="overview-kicker">Cobertura tecnica</p><h3>Distribucion HTTP</h3></div></div>
                    <div class="http-stack" aria-label="Distribucion de estados HTTP">${renderHttp(http, internal)}</div>
                    <div class="http-legend">${renderHttpLegend(http, internal)}</div>
                </div>
                <div class="overview-panel">
                    <div class="overview-panel-heading"><div><p class="overview-kicker">Arquitectura</p><h3>Profundidad de rastreo</h3></div></div>
                    <div class="depth-chart">${renderDepth(snapshot.depth || [])}</div>
                </div>
            </section>

            <section class="overview-panel onpage-panel">
                <div class="overview-panel-heading"><div><p class="overview-kicker">SEO on-page</p><h3>Senales semanticas y de indexacion</h3></div><span>Base: HTML interno 2xx</span></div>
                <div class="onpage-grid">${renderOnPage(snapshot.on_page || [])}</div>
            </section>

            <section class="overview-support-grid">
                <div class="overview-panel support-panel">
                    <p class="overview-kicker">Enlaces internos</p>
                    <h3>${format(links.broken_internal_edges)} enlaces rotos</h3>
                    <p>${format(links.internal_edges)} enlaces internos analizados</p>
                    <button class="overview-text-action" data-drill="links" data-status="4xx">Ver enlaces afectados</button>
                </div>
                <div class="overview-panel support-panel">
                    <p class="overview-kicker">Rendimiento</p>
                    <h3>${performance.p95_ms === null || performance.p95_ms === undefined ? 'Sin datos' : `${format(performance.p95_ms)} ms p95`}</h3>
                    <p>Tiempo de solicitud del crawler, no Core Web Vitals.</p>
                </div>
            </section>

            <section class="overview-panel sources-panel">
                <div class="overview-panel-heading"><div><p class="overview-kicker">Fuentes conectadas</p><h3>Contexto de negocio y experiencia</h3></div></div>
                <div class="source-strip">${renderSources(snapshot.enrichments || {})}</div>
            </section>
        `;
        bindActions(container);
    }

    function metricCard(label, value, detail, tone, drill) {
        const action = drill ? ` data-drill="${drill}"` : '';
        return `<button class="overview-metric tone-${tone}"${action}><span>${label}</span><strong>${value}</strong><small>${detail}</small></button>`;
    }

    function renderPriorities(rows) {
        if (!rows.length) return '<div class="overview-no-data">No hay incidencias priorizadas con los datos actuales.</div>';
        return rows.map(row => `
            <button class="priority-row" data-drill="issue" data-category="${escape(row.category)}" data-issue="${escape(row.issue)}" data-type="${escape(row.type)}">
                <span class="priority-badge priority-${escape(row.priority)}">${priorityLabels[row.priority] || 'Media'}</span>
                <span class="priority-copy"><strong>${escape(row.label || row.issue)}</strong><small>${escape(row.impact || '')}</small></span>
                <span class="priority-count"><strong>${format(row.affected)}</strong><small>${percentage(row.percentage)}</small></span>
            </button>
        `).join('');
    }

    function renderCategories(rows, total) {
        if (!rows.length) return '<div class="overview-no-data">Aun no hay incidencias agrupadas.</div>';
        return rows.slice(0, 8).map(row => {
            const share = ratio(row.count, total) || 0;
            return `<button class="category-row" data-drill="category" data-category="${escape(row.category)}"><span>${escape(row.category)}</span><span class="category-track"><i style="width:${Math.max(2, share)}%"></i></span><strong>${format(row.count)}</strong></button>`;
        }).join('');
    }

    function renderHttp(http, total) {
        return Object.keys(statusLabels).map(key => {
            const count = Number(http[key] || 0);
            const share = ratio(count, total) || 0;
            return `<button class="http-segment http-${key}" style="width:${share}%" data-drill="http" data-status="${key}" aria-label="${statusLabels[key]}: ${format(count)} URLs"></button>`;
        }).join('');
    }

    function renderHttpLegend(http, total) {
        return Object.keys(statusLabels).map(key => `<button data-drill="http" data-status="${key}" class="http-legend-item"><i class="http-${key}"></i><span>${statusLabels[key]}</span><strong>${format(http[key])}</strong><small>${percentage(ratio(http[key], total))}</small></button>`).join('');
    }

    function renderDepth(rows) {
        if (!rows.length) return '<div class="overview-no-data">No hay profundidad disponible.</div>';
        const max = Math.max(...rows.map(row => Number(row.count || 0)), 1);
        return rows.slice(0, 12).map(row => `<button class="depth-column" data-drill="depth" data-depth="${Number(row.depth)}" aria-label="Profundidad ${Number(row.depth)}: ${format(row.count)} URLs"><i style="height:${Math.max(6, (Number(row.count || 0) / max) * 100)}%"></i><span>${Number(row.depth)}</span><strong>${format(row.count)}</strong></button>`).join('');
    }

    function renderOnPage(rows) {
        if (!rows.length) return '<div class="overview-no-data">No hay HTML interno suficiente para calcular cobertura.</div>';
        return rows.map(row => `<button class="onpage-row" data-drill="onpage" data-issues="${escape((row.issues || []).join('|'))}"><span>${escape(row.label)}</span><strong>${percentage(row.coverage)}</strong><small>${format(row.affected)} afectadas</small><i><b style="width:${row.coverage || 0}%"></b></i></button>`).join('');
    }

    function renderSources(enrichments) {
        const labels = { gsc: 'Search Console', ga4: 'Google Analytics', crux: 'CrUX', pagespeed: 'PageSpeed', semantic: 'Semantica avanzada' };
        return Object.keys(labels).map(key => {
            const source = enrichments[key] || { status: 'not_connected', data: {} };
            const data = source.data || {};
            if (source.status !== 'available') {
                return `<div class="source-item"><strong>${labels[key]}</strong><span>${source.status === 'error' ? 'Sin datos' : 'No conectada'}</span></div>`;
            }
            return `<div class="source-item"><strong>${labels[key]}</strong>${sourceDetails(key, data)}</div>`;
        }).join('');
    }

    function sourceDetails(key, data) {
        if (key === 'gsc') {
            return `<span>${escape(data.period || 'Periodo no indicado')}</span><span>${format(data.clicks)} clics · ${format(data.impressions)} impresiones</span><span>CTR ${percentage(data.ctr)} · Pos. ${data.avg_position ?? 'Sin datos'}</span>${sourceTopLine(data.top_pages, 'Paginas')}${sourceTopLine(data.top_queries, 'Consultas')}`;
        }
        if (key === 'ga4') {
            return `<span>${escape(data.period || 'Periodo no indicado')}</span><span>${format(data.organic_sessions)} sesiones organicas</span><span>Engagement ${percentage(data.engagement_rate)} · ${format(data.conversions)} conversiones</span>${sourceTopLine(data.top_landing_pages, 'Landings')}`;
        }
        if (key === 'crux') {
            return `<span>${escape(data.period || data.scope || 'Periodo no indicado')}</span>${webVitalLine('LCP', data.lcp)}${webVitalLine('INP', data.inp)}${webVitalLine('CLS', data.cls)}`;
        }
        if (key === 'pagespeed') {
            const pages = Array.isArray(data.pages) ? data.pages : [];
            const mobile = pages.filter(item => String(item.strategy || item.device || '').toLowerCase().includes('mobile')).length;
            const desktop = pages.filter(item => String(item.strategy || item.device || '').toLowerCase().includes('desktop')).length;
            return `<span>${format(pages.length)} muestras disponibles</span><span>${format(mobile)} movil · ${format(desktop)} escritorio</span>`;
        }
        return `<span>${format(data.analyzed_urls)} URLs analizadas</span><span>${format((data.topics || []).length)} temas · ${format((data.entities || []).length)} entidades</span><span>${format((data.clusters || []).length)} clusters · intencion preparada</span>`;
    }

    function sourceTopLine(items, label) {
        const first = Array.isArray(items) ? items[0] : null;
        const name = first && (first.url || first.page || first.query || first.name);
        return name ? `<span>${label}: ${escape(name)}</span>` : '';
    }

    function webVitalLine(label, metric) {
        if (metric === null || metric === undefined) return `<span>${label}: Sin datos</span>`;
        const value = typeof metric === 'object' ? (metric.p75 ?? metric.value) : metric;
        const classification = typeof metric === 'object' ? (metric.classification || metric.rating || '') : '';
        return `<span>${label} p75 ${escape(value)}${classification ? ` · ${escape(classification)}` : ''}</span>`;
    }

    function bindActions(container) {
        container.querySelectorAll('[data-drill]').forEach(button => {
            button.addEventListener('click', () => drilldown(button.dataset));
        });
    }

    function drilldown(data) {
        if (data.drill === 'http') return window.openDashboardUrlDrilldown && window.openDashboardUrlDrilldown({ scope: 'internal', status_family: data.status });
        if (data.drill === 'depth') return window.openDashboardUrlDrilldown && window.openDashboardUrlDrilldown({ scope: 'internal', depth: data.depth });
        if (data.drill === 'issue') return window.openDashboardIssueDrilldown && window.openDashboardIssueDrilldown({ category: data.category, issues: [data.issue], issue_type: data.type });
        if (data.drill === 'category') return window.openDashboardIssueDrilldown && window.openDashboardIssueDrilldown({ category: data.category });
        if (data.drill === 'onpage') return window.openDashboardIssueDrilldown && window.openDashboardIssueDrilldown({ issues: (data.issues || '').split('|').filter(Boolean) });
        if (data.drill === 'links') return window.openDashboardLinkDrilldown && window.openDashboardLinkDrilldown({ scope: 'internal', status_family: data.status });
        if (data.drill === 'issues') return window.openDashboardIssueDrilldown && window.openDashboardIssueDrilldown({});
    }

    async function refresh() {
        const crawlId = window.crawlState && window.crawlState.currentCrawlId;
        const overview = document.getElementById('overview-tab');
        if (!crawlId || !overview || !overview.classList.contains('active')) return;
        if (requestController) requestController.abort();
        requestController = new AbortController();
        try {
            const response = await fetch(`/api/crawls/${crawlId}/dashboard`, { signal: requestController.signal });
            const payload = await response.json();
            if (!payload.success) throw new Error(payload.error || 'No se pudo cargar el resumen');
            lastSnapshot = payload;
            render(payload, false);
        } catch (error) {
            if (error.name !== 'AbortError') {
                console.warn('Dashboard overview refresh failed:', error);
                render(lastSnapshot, true);
            }
        } finally {
            requestController = null;
        }
    }

    function renderLocal() {
        const state = window.crawlState;
        if (!state || state.currentCrawlId || !state.urls.length) return;
        const urls = state.urls;
        const latestUrls = Array.from(new Map(urls.map(row => [String(row.url || '').replace(/#.*$/, '').replace(/\/$/, '') || '/', row])).values());
        const internal = latestUrls.filter(row => row.is_internal);
        const html = internal.filter(row => Number(row.status_code) >= 200 && Number(row.status_code) < 300 && String(row.content_type || '').includes('html'));
        const urlByKey = new Map(latestUrls.map(row => [String(row.url || '').replace(/#.*$/, '').replace(/\/$/, '') || '/', row]));
        const issues = Array.from(new Map((state.issues || []).map(issue => [
            `${String(issue.url || '').replace(/#.*$/, '').replace(/\/$/, '')}|${issue.category || ''}|${issue.issue || ''}`,
            issue
        ])).values()).filter(issue => {
            const row = urlByKey.get(String(issue.url || '').replace(/#.*$/, '').replace(/\/$/, ''));
            return row && row.is_internal && (['Technical', 'Performance'].includes(issue.category) || (Number(row.status_code) >= 200 && Number(row.status_code) < 300 && String(row.content_type || '').includes('html')));
        });
        const links = Array.from(new Map((state.links || []).map(link => [
            `${link.source_url || ''}|${link.target_url || ''}|${link.anchor_text || ''}|${link.placement || 'body'}`,
            link
        ])).values());
        const countStatus = key => internal.filter(row => {
            const status = Number(row.status_code || 0);
            return key === 'no_response' ? status === 0 : status >= Number(key[0]) * 100 && status < Number(key[0]) * 100 + 100;
        }).length;
        const groupMap = new Map();
        issues.forEach(issue => {
            const key = `${issue.category}|${issue.issue}|${issue.type}`;
            groupMap.set(key, (groupMap.get(key) || 0) + 1);
        });
        const groups = Array.from(groupMap, ([key, count]) => {
            const [category, issue, type] = key.split('|');
            const critical = /server error|dns|ssl|connection|timeout|no response/i.test(issue);
            const high = /client error|missing title|missing h1|noindex|broken image/i.test(issue);
            const opportunity = /structured data|open.?graph|twitter|alt text|language/i.test(issue) || ['Social', 'Accessibility', 'Structured Data'].includes(category);
            const priority = critical ? 'critical' : high ? 'high' : opportunity ? 'opportunity' : type === 'error' ? 'high' : type === 'warning' ? 'medium' : 'opportunity';
            const denominator = ['Technical', 'Performance'].includes(category) ? internal.length : html.length;
            return { category, issue, type, label: issue, impact: 'Requiere revision tecnica en las URLs afectadas', priority, affected: count, denominator, percentage: ratio(count, denominator) };
        }).sort((a, b) => (['critical', 'high', 'medium', 'opportunity'].indexOf(a.priority) - ['critical', 'high', 'medium', 'opportunity'].indexOf(b.priority)) || b.affected - a.affected);
        const categoryMap = new Map();
        const severityMap = new Map();
        groups.forEach(row => {
            categoryMap.set(row.category, (categoryMap.get(row.category) || 0) + row.affected);
            severityMap.set(row.type, (severityMap.get(row.type) || 0) + row.affected);
        });
        const depthMap = new Map();
        internal.forEach(row => {
            const depth = Number(row.depth || 0);
            depthMap.set(depth, (depthMap.get(depth) || 0) + 1);
        });
        const onPageGroups = [
            ['title', 'Titulos', ['Missing Title Tag', 'Title Too Long', 'Title Too Short']],
            ['meta', 'Meta descripciones', ['Missing Meta Description', 'Meta Description Too Long', 'Meta Description Too Short']],
            ['h1', 'H1', ['Missing H1 Tag']],
            ['canonical', 'Canonical', ['Missing Canonical URL', 'Canonical URL Different']],
            ['thin_content', 'Contenido >=300 palabras', ['Thin Content']],
            ['duplicate_content', 'Contenido duplicado', ['Duplicate Content Detected']],
            ['structured_data', 'Datos estructurados', ['No Structured Data', 'Structured Data Opportunity']]
        ].map(([key, label, names]) => {
            const affected = groups.filter(row => names.includes(row.issue)).reduce((total, row) => total + row.affected, 0);
            return { key, label, issues: names, affected, denominator: html.length, healthy: Math.max(0, html.length - affected), coverage: ratio(Math.max(0, html.length - affected), html.length) };
        });
        lastSnapshot = {
            generated_at: new Date().toISOString(),
            crawl: { base_url: state.baseUrl, status: 'completed', crawled: state.stats.crawled, discovered: state.stats.discovered, progress: 100, partial: false },
            coverage: { internal_urls: internal.length, external_urls: latestUrls.length - internal.length, html_2xx_urls: html.length, indexable_html_urls: html.filter(row => !String(row.robots || '').includes('noindex')).length },
            http: { '2xx': countStatus('2xx'), '3xx': countStatus('3xx'), '4xx': countStatus('4xx'), '5xx': countStatus('5xx'), no_response: countStatus('no_response') },
            performance: {}, links: {
                unique_edges: links.length,
                internal_edges: links.filter(link => link.is_internal).length,
                broken_internal_edges: links.filter(link => link.is_internal && Number(link.target_status || 0) >= 400).length
            }, depth: Array.from(depthMap, ([depth, count]) => ({ depth, count })).sort((a, b) => a.depth - b.depth),
            issues: { total: issues.length, by_severity: Array.from(severityMap, ([type, count]) => ({ type, count })), by_category: Array.from(categoryMap, ([category, count]) => ({ category, count })).sort((a, b) => b.count - a.count), top: groups.slice(0, 8) }, on_page: onPageGroups,
            enrichments: { pagespeed: { status: state.stats?.pagespeed_results?.length ? 'available' : 'not_connected', data: { pages: state.stats?.pagespeed_results || [] } } }
        };
        render(lastSnapshot, false);
    }

    function start() {
        if (refreshTimer) return;
        refreshTimer = setInterval(refresh, REFRESH_MS);
        refresh();
    }

    function stop() {
        if (refreshTimer) clearInterval(refreshTimer);
        refreshTimer = null;
        if (requestController) requestController.abort();
    }

    window.CrawlOverviewDashboard = { refresh, renderLocal, start, stop, reset: () => { lastSnapshot = null; render(null); } };
    document.addEventListener('DOMContentLoaded', start);
}());
