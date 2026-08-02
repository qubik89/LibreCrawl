// ========================================
// Dashboard Functions
// ========================================

let activeReportCrawlId = null;
let reportPollTimer = null;
let reportActionsEnabled = false;
let dashboardRefreshTimer = null;
let reportWizardStep = 1;
let reportPreflight = null;
let reportClientLogoAssetId = '';
// null means "let the server recommend the latest compatible baseline";
// once the user touches the select, keep either an explicit crawl ID or the
// sentinel "none" so disabling history remains an intentional choice.
let reportBaselinePreference = null;

function crawlStatusLabel(status) {
    return {
        completed: 'completado',
        running: 'en curso',
        paused: 'en pausa',
        failed: 'fallido',
        stopped: 'detenido',
        archived: 'archivado',
        unknown: 'desconocido'
    }[status] || status;
}

async function openDashboard() {
    const modal = document.getElementById('dashboardModal');

    // Show modal
    modal.style.display = 'flex';

    reportActionsEnabled = await canUseReportActions();
    await loadDashboardCrawls();
    if (!dashboardRefreshTimer) {
        dashboardRefreshTimer = setInterval(loadDashboardCrawls, 10000);
    }
}

async function loadDashboardCrawls() {
    const content = document.getElementById('dashboardContent');

    // Load crawls
    try {
        const response = await fetch('/api/crawls/list');
        const data = await response.json();

        if (!data.success) {
            content.innerHTML = `<p style="color: #ef4444;">Error al cargar rastreos: ${data.error}</p>`;
            return;
        }

        const crawls = data.crawls || [];

        if (crawls.length === 0) {
            content.innerHTML = `<p style="text-align: center; color: #9ca3af;">No hay rastreos guardados.</p>`;
            return;
        }

        let html = `
            <table class="data-table" style="width: 100%; table-layout: fixed;">
                <thead>
                    <tr>
                        <th style="width: 180px;">Fecha</th>
                        <th style="width: 200px;">Dominio</th>
                        <th style="width: 80px;">URLs</th>
                        <th style="width: 80px;">Enlaces</th>
                        <th style="width: 80px;">Incidencias</th>
                        <th style="width: 160px;">Progreso</th>
                        <th style="width: 100px;">Estado</th>
                        <th style="width: 360px;">Acciones</th>
                    </tr>
                </thead>
                <tbody>
        `;

        crawls.forEach(crawl => {
            const date = new Date(crawl.started_at).toLocaleString();
            const domain = crawl.base_domain || crawl.base_url;
            const status = crawl.status || 'unknown';
            const statusColor = status === 'completed' ? '#10b981' : status === 'running' ? '#3b82f6' : status === 'paused' ? '#f59e0b' : '#6b7280';
            const running = crawl.is_active && status === 'running';
            const paused = crawl.is_active && status === 'paused';
            const completed = status === 'completed';
            const crawled = crawl.urls_crawled || 0;
            const discovered = Math.max(crawl.urls_discovered || 0, crawled);
            const progress = discovered ? Math.min(100, (crawled / discovered) * 100) : 0;
            const progressLabel = discovered ? `${progress.toFixed(1)}%` : '-';

            html += `
                <tr>
                    <td>${date}</td>
                    <td>${domain}</td>
                    <td>${formatNumber(crawled)}</td>
                    <td>${crawl.link_count ? formatNumber(crawl.link_count) : '-'}</td>
                    <td>${crawl.issue_count ? formatNumber(crawl.issue_count) : '-'}</td>
                    <td>
                        <div class="progress-bar" style="height: 6px;">
                            <div class="progress-fill" style="width: ${progress}%"></div>
                        </div>
                        <div style="font-size: 12px; color: #9ca3af; margin-top: 4px;">${progressLabel}</div>
                    </td>
                    <td><span style="color: ${statusColor};">${crawlStatusLabel(status)}</span></td>
                    <td style="white-space: nowrap;">
                        <button class="btn btn-primary" style="margin-right: 5px; padding: 6px 12px; font-size: 13px;" onclick="loadCrawlFromDashboard(${crawl.id})">Ver</button>
                        ${running ? `<button class="btn btn-secondary" style="margin-right: 5px; padding: 6px 12px; font-size: 13px;" onclick="pauseCrawlFromDashboard(${crawl.id})">Pausar</button>` : ''}
                        ${paused || ['paused', 'failed', 'stopped', 'running'].includes(status) ? `<button class="btn btn-secondary" style="margin-right: 5px; padding: 6px 12px; font-size: 13px;" onclick="resumeCrawlFromDashboard(${crawl.id})">Reanudar</button>` : ''}
                        ${completed ? `<button class="btn btn-secondary" style="margin-right: 5px; padding: 6px 12px; font-size: 13px;" onclick="exportData(${crawl.id})">Exportar</button>` : ''}
                        ${completed && reportActionsEnabled ? `<button class="btn btn-secondary" style="margin-right: 5px; padding: 6px 12px; font-size: 13px;" onclick="openReportModal(${crawl.id})">Informe</button>` : ''}
                        ${crawl.is_active ? `<button class="btn btn-danger" style="margin-right: 5px; padding: 6px 12px; font-size: 13px;" onclick="stopCrawlFromDashboard(${crawl.id})">Detener</button>` : ''}
                        <button class="btn btn-danger" style="padding: 6px 12px; font-size: 13px;" onclick="deleteCrawlFromDashboard(${crawl.id})">Eliminar</button>
                    </td>
                </tr>
            `;
        });

        html += `
                </tbody>
            </table>
        `;

        content.innerHTML = html;

    } catch (error) {
        console.error('Error loading dashboard:', error);
        content.innerHTML = `<p style="color: #ef4444;">Error al cargar rastreos.</p>`;
    }
}

function closeDashboard() {
    document.getElementById('dashboardModal').style.display = 'none';
    if (dashboardRefreshTimer) {
        clearInterval(dashboardRefreshTimer);
        dashboardRefreshTimer = null;
    }
}

async function canUseReportActions() {
    try {
        const response = await fetch('/api/user/info');
        const data = await response.json();
        return Boolean(data.success && data.user && data.user.tier === 'admin');
    } catch (error) {
        console.warn('Could not load report permissions:', error);
        return false;
    }
}

function reportDashboardNotice(message, type = 'info') {
    if (typeof showNotification === 'function') {
        showNotification(message, type);
    } else {
        alert(message);
    }
}

function reportToneLabel(tone) {
    return {
        executive: 'Ejecutivo',
        technical: 'Técnico',
        commercial: 'Comercial'
    }[tone] || tone || 'Informe';
}

function reportLanguageLabel(language) {
    return {
        'es-ES': 'Español',
        en: 'Inglés'
    }[language] || language || '';
}

function safeReportText(value) {
    if (typeof escapeHtml === 'function') return escapeHtml(value || '');
    return String(value || '').replace(/[&<>"']/g, char => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
    }[char]));
}

async function getReportSettingsForGeneration() {
    if (typeof window.loadReportSettings === 'function') {
        return await window.loadReportSettings();
    }

    const response = await fetch('/api/report-settings');
    const data = await response.json();
    if (!data.success) throw new Error(data.error || 'No se pudieron cargar los ajustes de informes');
    return data.settings || {};
}

async function getReportModelsForGeneration(settings) {
    if (typeof window.loadReportModels === 'function') {
        return await window.loadReportModels();
    }

    const response = await fetch('/api/report-models');
    const data = await response.json();
    if (!data.success) return [];
    return data.models || [];
}

async function openReportModal(crawlId) {
    activeReportCrawlId = crawlId;

    const modal = document.getElementById('reportModal');
    if (!modal) {
        await generateReportWithDefaults(crawlId);
        return;
    }

    try {
        const settings = await getReportSettingsForGeneration();
        await getReportModelsForGeneration(settings);
        const v2Enabled = settings.capabilities?.reports_v2_enabled === true;
        const keyConfigured = settings.has_openrouter_api_key === true;

        setReportFieldValue('reportGenerateLanguage', settings.default_language || 'es-ES');
        setReportMode(settings.default_report_mode || 'pack');
        setReportFieldValue('reportGenerateCommercialContext', settings.default_commercial_context || 'prospect');
        setReportFieldValue('reportGenerateClientName', '');
        setReportFieldValue('reportGenerateTitle', '');
        setReportFieldValue('reportGenerateMarket', '');
        setReportFieldValue('reportGeneratePrimaryColor', settings.appearance?.primary_color || '#1d4ed8');
        setReportFieldValue('reportGenerateSecondaryColor', settings.appearance?.secondary_color || '#334155');
        setReportFieldValue('reportGenerateAccentColor', settings.appearance?.accent_color || '#b45309');
        setReportFieldValue('reportGenerateAuthor', settings.issuer?.author || '');
        setReportFieldValue('reportGenerateContact', settings.issuer?.contact || '');
        setReportFieldValue('reportGenerateConfidentiality', settings.issuer?.confidentiality || '');
        setReportFieldValue('reportGenerateCta', settings.issuer?.cta || '');
        setReportFieldValue('reportGenerateGoals', '');
        setReportFieldValue('reportGenerateBaseline', '');
        setReportFieldValue('reportGenerateLogoChoice', settings.logo?.preview_url ? 'issuer' : 'none');
        reportClientLogoAssetId = '';
        const clientLogoGroup = document.getElementById('reportClientLogoGroup');
        if (clientLogoGroup) clientLogoGroup.hidden = true;
        reportWizardStep = 1;
        reportPreflight = null;
        reportBaselinePreference = null;
        setReportWizardStep(1);
        setReportStatus('');
        await loadReportDownloadButtons(crawlId);

        const downloadLink = document.getElementById('reportDownloadLink');
        if (downloadLink) downloadLink.style.display = 'none';

        const generateBtn = document.getElementById('reportGenerateBtn');
        if (generateBtn) {
            generateBtn.disabled = !v2Enabled || !keyConfigured;
            generateBtn.textContent = 'Generar informe';
        }
        if (!v2Enabled) {
            setReportStatus('Report Suite V2 está visible, pero aún no está activado en este entorno.', false);
        } else if (!keyConfigured) {
            setReportStatus('Configura una clave de OpenRouter en Ajustes del informe para generar.', true);
        }

        modal.style.display = 'flex';
        const firstFocusable = modal.querySelector('button:not([hidden]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href]');
        if (firstFocusable) setTimeout(() => firstFocusable.focus(), 0);
        // Load the factual preview immediately so the final-domain client
        // name and recommended historical crawl are ready before step 2/3.
        loadReportPreflight();
    } catch (error) {
        console.error('Error opening report modal:', error);
        reportDashboardNotice(error.message || 'No se pudieron cargar los ajustes de informes', 'error');
    }
}

function closeReportModal() {
    const modal = document.getElementById('reportModal');
    if (modal) modal.style.display = 'none';
    if (reportPollTimer) {
        clearTimeout(reportPollTimer);
        reportPollTimer = null;
    }
}

document.addEventListener('click', event => {
    const modal = document.getElementById('reportModal');
    if (modal && event.target === modal) closeReportModal();
});

document.addEventListener('keydown', event => {
    const modal = document.getElementById('reportModal');
    if (!modal || modal.style.display !== 'flex') return;
    if (event.key === 'Escape') {
        closeReportModal();
        return;
    }
    if (event.key !== 'Tab') return;
    const focusable = Array.from(modal.querySelectorAll('button:not([disabled]):not([hidden]), input:not([disabled]):not([hidden]), select:not([disabled]):not([hidden]), textarea:not([disabled]):not([hidden]), a[href]'))
        .filter(element => element.offsetParent !== null);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
    }
});

function setReportFieldValue(id, value) {
    const element = document.getElementById(id);
    if (element) element.value = value;
}

function setReportMode(mode) {
    document.querySelectorAll('input[name="reportGenerateReportMode"]').forEach(input => {
        input.checked = input.value === mode;
    });
}

function selectedReportMode() {
    return document.querySelector('input[name="reportGenerateReportMode"]:checked')?.value || 'pack';
}

function setReportWizardStep(step) {
    reportWizardStep = Math.max(1, Math.min(4, Number(step) || 1));
    document.querySelectorAll('[data-wizard-panel]').forEach(panel => {
        panel.classList.toggle('is-active', Number(panel.dataset.wizardPanel) === reportWizardStep);
    });
    document.querySelectorAll('[data-wizard-step]').forEach(button => {
        const current = Number(button.dataset.wizardStep);
        button.classList.toggle('is-active', current === reportWizardStep);
        button.classList.toggle('is-complete', current < reportWizardStep);
        if (current === reportWizardStep) button.setAttribute('aria-current', 'step');
        else button.removeAttribute('aria-current');
    });
    const previous = document.getElementById('reportWizardPrev');
    const next = document.getElementById('reportWizardNext');
    const generate = document.getElementById('reportGenerateBtn');
    if (previous) previous.hidden = reportWizardStep === 1;
    if (next) { next.hidden = reportWizardStep === 4; next.textContent = reportWizardStep === 3 ? 'Revisar' : 'Continuar'; }
    if (generate) generate.style.display = reportWizardStep === 4 ? 'inline-flex' : 'none';
    if (reportWizardStep === 3 && !reportPreflight) loadReportPreflight();
    if (reportWizardStep === 4) renderReportGenerationSummary();
}

async function advanceReportWizard() {
    if (reportWizardStep === 2) {
        setReportWizardStep(3);
        return;
    }
    if (reportWizardStep === 3) {
        if (!reportPreflight) await loadReportPreflight();
        if (!reportPreflight) return;
        setReportWizardStep(4);
        return;
    }
    setReportWizardStep(reportWizardStep + 1);
}

async function loadReportPreflight() {
    const panel = document.getElementById('reportPreflightPanel');
    if (panel) panel.innerHTML = '<span class="report-loading">Calculando cobertura…</span>';
    const types = selectedReportMode() === 'pack' ? 'executive,commercial,technical' : selectedReportMode();
    try {
        const model = reportSettings.manual_model || '';
        const baselineQuery = reportBaselinePreference === null
            ? ''
            : `&baseline_crawl_id=${encodeURIComponent(reportBaselinePreference)}`;
        const response = await fetch(`/api/crawls/${activeReportCrawlId}/report-preflight?report_types=${encodeURIComponent(types)}${model ? `&model=${encodeURIComponent(model)}` : ''}${baselineQuery}`);
        const data = await response.json();
        if (!data.success) throw new Error(data.error || 'No se pudo calcular la cobertura');
        reportPreflight = data;
        const clientName = document.getElementById('reportGenerateClientName');
        if (clientName && !clientName.value && data.crawl?.domain) clientName.value = data.crawl.domain;
        populateBaselineOptions(data.baseline);
        renderReportPreflight(data);
    } catch (error) {
        reportPreflight = null;
        if (panel) panel.innerHTML = `<p class="report-error">${safeReportText(error.message)}</p>`;
        reportDashboardNotice(error.message || 'No se pudo calcular la cobertura', 'error');
    }
}

function populateBaselineOptions(baseline) {
    const select = document.getElementById('reportGenerateBaseline');
    if (!select) return;
    const selected = baseline?.selected_id || '';
    select.innerHTML = '<option value="">Sin comparación histórica</option>';
    (baseline?.candidates || []).forEach(candidate => {
        const option = document.createElement('option');
        option.value = candidate.id;
        option.textContent = `${candidate.domain} · ${candidate.completed_at || 'Crawl anterior'}`;
        option.selected = String(candidate.id) === String(selected);
        select.appendChild(option);
    });
}

function renderReportPreflight(data) {
    const panel = document.getElementById('reportPreflightPanel');
    if (!panel) return;
    const denominators = data.coverage?.denominators || {};
    const coverageRatio = data.coverage?.coverage_ratio == null
        ? '—'
        : `${Number(data.coverage.coverage_ratio).toLocaleString('es-ES', { maximumFractionDigits: 1 })}%`;
    const sourceRows = (data.sources || []).map(source => `<div class="report-source-row"><span>${safeReportText(source.label)}</span><strong>${source.available ? 'Disponible' : 'No conectado'}</strong></div>`).join('');
    const limitationRows = (data.coverage?.limitation_items || []).map(item => `<li>${safeReportText(item)}</li>`).join('');
    const estimate = data.estimate || {};
    const cost = estimate.pricing_available ? `$${estimate.normal_cost_usd}–$${estimate.maximum_cost_usd}` : 'Precio no disponible';
    const historical = data.historical?.available ? 'Comparativa histórica disponible' : 'Sin comparativa histórica conectada';
    panel.innerHTML = `
        <div class="report-preflight-stats"><div><strong>${formatNumber(denominators.unique_urls || 0)}</strong><span>URLs únicas</span></div><div><strong>${formatNumber(denominators.html_2xx_urls || 0)}</strong><span>HTML 2xx</span></div><div><strong>${coverageRatio}</strong><span>Cobertura</span></div><div><strong>${data.coverage?.limitations || 0}</strong><span>Límites declarados</span></div></div>
        <div class="report-source-list">${sourceRows}</div>
        <div class="report-source-row"><span>Histórico</span><strong>${historical}</strong></div>
        ${limitationRows ? `<div class="report-preflight-limitations"><span>Límites y contexto</span><ul>${limitationRows}</ul></div>` : ''}
        <div class="report-preflight-estimate"><span>${estimate.normal_calls || 0} llamadas normales · ${estimate.maximum_calls || 0} máximo</span><strong>${cost}</strong></div>`;
}

function renderReportGenerationSummary() {
    const summary = document.getElementById('reportGenerationSummary');
    if (!summary) return;
    const mode = selectedReportMode();
    const estimate = reportPreflight?.estimate || {};
    const baseline = document.getElementById('reportGenerateBaseline')?.selectedOptions?.[0]?.textContent || 'Sin comparación histórica';
    summary.innerHTML = `<dl class="report-summary-list"><div><dt>Productos</dt><dd>${safeReportText(mode === 'pack' ? 'Ejecutivo · Comercial · Técnico' : reportToneLabel(mode))}</dd></div><div><dt>Dominio</dt><dd>${safeReportText(reportPreflight?.crawl?.domain || '')}</dd></div><div><dt>Histórico</dt><dd>${safeReportText(baseline)}</dd></div><div><dt>Modelo</dt><dd>${safeReportText(estimate.model || reportSettings.default_model || '')}</dd></div><div><dt>Uso previsto</dt><dd>${estimate.normal_calls || 0}–${estimate.maximum_calls || 0} llamadas · ${estimate.pricing_available ? `$${estimate.normal_cost_usd}–$${estimate.maximum_cost_usd}` : 'precio no disponible'}</dd></div></dl>`;
}

function reportGenerationPayloadFromModal() {
    const mode = selectedReportMode();
    const reportTypes = mode === 'pack' ? ['executive', 'commercial', 'technical'] : [mode];
    const logoChoice = document.getElementById('reportGenerateLogoChoice')?.value || 'none';
    return {
        language: document.getElementById('reportGenerateLanguage')?.value || 'es-ES',
        report_mode: mode,
        report_type: reportTypes[0],
        report_types: reportTypes,
        commercial_context: document.getElementById('reportGenerateCommercialContext')?.value || 'prospect',
        baseline_crawl_id: document.getElementById('reportGenerateBaseline')?.value ? Number(document.getElementById('reportGenerateBaseline').value) : null,
        use_v2: true,
        brand_kit: {
            client_name: document.getElementById('reportGenerateClientName')?.value || '',
            primary_color: document.getElementById('reportGeneratePrimaryColor')?.value || '#1d4ed8',
            secondary_color: document.getElementById('reportGenerateSecondaryColor')?.value || '#334155',
            accent_color: document.getElementById('reportGenerateAccentColor')?.value || '#b45309',
            logo_asset_id: logoChoice === 'issuer' ? (reportSettings.appearance?.logo_asset_id || '') : (logoChoice === 'client' ? reportClientLogoAssetId : '')
        },
        client_context: {
            client_name: document.getElementById('reportGenerateClientName')?.value || '',
            report_title: document.getElementById('reportGenerateTitle')?.value || '',
            market: document.getElementById('reportGenerateMarket')?.value || '',
            author: document.getElementById('reportGenerateAuthor')?.value || '',
            contact: document.getElementById('reportGenerateContact')?.value || '',
            confidentiality: document.getElementById('reportGenerateConfidentiality')?.value || '',
            cta: document.getElementById('reportGenerateCta')?.value || '',
            business_goals: document.getElementById('reportGenerateGoals')?.value || ''
        }
    };
}

async function uploadReportClientLogo(input) {
    const file = input?.files?.[0];
    if (!file) return;
    const body = new FormData();
    body.append('file', file);
    const status = document.getElementById('reportClientLogoStatus');
    try {
        const response = await fetch('/api/report-assets/logo', { method: 'POST', body });
        const data = await response.json();
        if (!data.success) throw new Error(data.error || 'No se pudo subir el logo');
        reportClientLogoAssetId = data.asset.asset_id;
        if (status) status.textContent = data.asset.original_name || 'Logo listo para este informe';
    } catch (error) {
        reportClientLogoAssetId = '';
        if (status) status.textContent = error.message;
        input.value = '';
    }
}

document.addEventListener('change', event => {
    if (event.target?.name === 'reportGenerateReportMode') {
        reportPreflight = null;
        if (reportWizardStep >= 3) loadReportPreflight();
    }
    if (event.target?.id === 'reportGenerateLogoChoice') {
        const group = document.getElementById('reportClientLogoGroup');
        if (group) group.hidden = event.target.value !== 'client';
    }
    if (event.target?.id === 'reportGenerateBaseline') {
        // Refresh the factual preview for the selected historical crawl. The
        // explicit `none` value lets the user disable comparison instead of
        // silently receiving the automatic recommendation again.
        reportBaselinePreference = event.target.value || 'none';
        reportPreflight = null;
        loadReportPreflight();
    }
});

async function loadReportDownloadButtons(crawlId) {
    const container = document.getElementById('reportDownloadButtons');
    if (!container || !crawlId) return;

    try {
        const response = await fetch(`/api/crawls/${crawlId}/reports`);
        const data = await response.json();
        if (!data.success) throw new Error(data.error || 'No se pudieron cargar los informes');
        renderReportDownloadButtons(data.reports || []);
    } catch (error) {
        console.warn('Could not load report downloads:', error);
        container.innerHTML = '';
    }
}

function renderReportDownloadButtons(reports) {
    const container = document.getElementById('reportDownloadButtons');
    if (!container) return;

    const visible = (reports || []).filter(report => report.status === 'completed' || report.status === 'failed');
    if (!visible.length) {
        container.innerHTML = '';
        return;
    }

    container.innerHTML = `
        <div class="report-downloads-title">Descargas generadas</div>
        <div class="report-downloads-grid">
            ${visible.map(report => `
                ${report.download_url ? `<a class="report-download-btn" href="${safeReportText(report.download_url)}" target="_blank">` : '<div class="report-download-btn report-download-failed">'}
                    <strong>${safeReportText(reportToneLabel(report.report_type || report.tone))}</strong>
                    <span>${safeReportText(reportLanguageLabel(report.language))}${report.quality_score != null ? ` · Calidad ${report.quality_score}/100` : ''}${report.cost_usd != null ? ` · $${report.cost_usd}` : ''}</span>
                ${report.download_url ? '</a>' : '</div>'}
                ${report.html_url ? `<a class="report-download-btn" href="${safeReportText(report.html_url)}" target="_blank"><strong>${safeReportText(reportToneLabel(report.report_type || report.tone))}</strong><span>HTML</span></a>` : ''}
                ${report.quality_url ? `<a class="report-download-btn" href="${safeReportText(report.quality_url)}" target="_blank"><strong>${safeReportText(reportToneLabel(report.report_type || report.tone))}</strong><span>${report.status === 'failed' ? 'Diagnóstico QA' : 'Revisión QA'}</span></a>` : ''}
            `).join('')}
        </div>
    `;
}

async function submitReportGeneration() {
    if (!activeReportCrawlId) return;
    if (reportSettings.capabilities?.reports_v2_enabled !== true) {
        setReportStatus('Report Suite V2 aún no está activado.', true);
        return;
    }
    if (reportSettings.has_openrouter_api_key !== true) {
        setReportStatus('Configura una clave de OpenRouter en Ajustes del informe.', true);
        return;
    }

    const generateBtn = document.getElementById('reportGenerateBtn');
    if (generateBtn) {
        generateBtn.disabled = true;
        generateBtn.textContent = 'Generando...';
    }

    await createReport(activeReportCrawlId, reportGenerationPayloadFromModal(), true);
}

async function generateReportWithDefaults(crawlId) {
    try {
        const settings = await getReportSettingsForGeneration();
        if (settings.capabilities?.reports_v2_enabled !== true) {
            reportDashboardNotice('Report Suite V2 aún no está activado.', 'info');
            return;
        }
        if (settings.has_openrouter_api_key !== true) {
            reportDashboardNotice('Configura una clave de OpenRouter en Ajustes del informe.', 'info');
            return;
        }
        const model = settings.manual_model || settings.default_model || 'modelo guardado';
        const language = settings.default_language || 'es-ES';
        const mode = settings.default_report_mode || 'pack';
        if (!confirm(`¿Generar ${mode === 'pack' ? 'el pack completo' : reportToneLabel(mode)} con ${model} en ${language}?`)) return;
        const types = mode === 'pack' ? ['executive', 'commercial', 'technical'] : [mode];
        await createReport(crawlId, { report_mode: mode, report_type: types[0], report_types: types, use_v2: true }, false);
    } catch (error) {
        console.error('Error generating report:', error);
        reportDashboardNotice(error.message || 'No se pudo generar el informe', 'error');
    }
}

async function createReport(crawlId, payload, useModal) {
    try {
        if (reportPollTimer) {
            clearTimeout(reportPollTimer);
            reportPollTimer = null;
        }
        setReportStatus('Iniciando informe...');
        const response = await fetch(`/api/crawls/${crawlId}/reports`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload || {})
        });
        const data = await response.json();
        if (!data.success) throw new Error(data.error || 'La generación del informe ha fallado');

        reportDashboardNotice('Generación del informe iniciada', 'success');
        pollReportStatuses(data.report_ids || [data.report_id], useModal);
    } catch (error) {
        console.error('Error creating report:', error);
        setReportStatus(error.message || 'La generación del informe ha fallado', true);
        reportDashboardNotice(error.message || 'La generación del informe ha fallado', 'error');

        const generateBtn = document.getElementById('reportGenerateBtn');
        if (generateBtn) {
            generateBtn.disabled = false;
            generateBtn.textContent = 'Generar informe';
        }
    }
}

async function pollReportStatus(reportId, useModal) {
    try {
        const response = await fetch(`/api/reports/${reportId}/status`);
        const data = await response.json();
        if (!data.success) throw new Error(data.error || 'El estado del informe ha fallado');

        const report = data.report || {};
        if (report.status === 'completed') {
            reportPollTimer = null;
            const href = `/api/reports/${reportId}/download`;
            setReportStatus('Informe completado.');
            showReportDownload(href);
            if (useModal) loadReportDownloadButtons(activeReportCrawlId);
            if (!useModal) window.location.href = href;
            return;
        }

        if (report.status === 'failed') {
            reportPollTimer = null;
            setReportStatus(report.error || 'La generación del informe ha fallado', true);
            reportDashboardNotice('La generación del informe ha fallado', 'error');
            if (useModal) loadReportDownloadButtons(activeReportCrawlId);
            const generateBtn = document.getElementById('reportGenerateBtn');
            if (generateBtn) {
                generateBtn.disabled = false;
                generateBtn.textContent = 'Generar informe';
            }
            return;
        }

        setReportStatus(`Informe ${reportStageLabel(report.stage || report.status)}...`);
        reportPollTimer = setTimeout(() => pollReportStatus(reportId, useModal), 2000);
    } catch (error) {
        reportPollTimer = null;
        console.error('Error polling report:', error);
        setReportStatus(error.message || 'El estado del informe ha fallado', true);
        reportDashboardNotice(error.message || 'El estado del informe ha fallado', 'error');
        const generateBtn = document.getElementById('reportGenerateBtn');
        if (generateBtn) {
            generateBtn.disabled = false;
            generateBtn.textContent = 'Generar informe';
        }
    }
}

async function pollReportStatuses(reportIds, useModal) {
    const ids = [...new Set((reportIds || []).filter(Boolean))];
    if (ids.length <= 1) return pollReportStatus(ids[0], useModal);
    try {
        const results = await Promise.all(ids.map(async id => {
            const response = await fetch(`/api/reports/${id}/status`);
            const data = await response.json();
            if (!data.success) throw new Error(data.error || 'No se pudo consultar un informe');
            return data.report || {};
        }));
        const terminal = results.every(report => ['completed', 'failed'].includes(report.status));
        if (terminal) {
            reportPollTimer = null;
            const failed = results.filter(report => report.status === 'failed');
            setReportStatus(failed.length ? `Pack terminado: ${failed.length} informe(s) requieren revisión.` : 'Pack de informes completado.', failed.length > 0);
            if (!failed.length && results[0].download_url) showReportDownload(results[0].download_url);
            if (useModal) loadReportDownloadButtons(activeReportCrawlId);
            return;
        }
        const current = results.map(report => `${reportToneLabel(report.report_type || report.tone)}: ${reportStageLabel(report.stage || report.status)}`).join(' · ');
        setReportStatus(current || 'Generando pack de informes...');
        reportPollTimer = setTimeout(() => pollReportStatuses(ids, useModal), 2000);
    } catch (error) {
        reportPollTimer = null;
        setReportStatus(error.message || 'La generación del pack ha fallado', true);
        const generateBtn = document.getElementById('reportGenerateBtn');
        if (generateBtn) { generateBtn.disabled = false; generateBtn.textContent = 'Generar informe'; }
    }
}

function reportStageLabel(stage) {
    return {
        queued: 'en cola', facts: 'preparando hechos', analysis: 'analizando', writing: 'redactando',
        quality: 'revisando calidad', repair: 'reparando', render: 'renderizando PDF', complete: 'completado',
        completed: 'completado', failed: 'fallido', running: 'en curso'
    }[stage] || stage || 'en cola';
}

function setReportStatus(message, isError = false) {
    const status = document.getElementById('reportGenerationStatus');
    if (!status) return;

    status.style.display = message ? 'block' : 'none';
    status.textContent = message;
    status.style.borderColor = isError ? 'rgba(239, 68, 68, 0.4)' : 'rgba(59, 130, 246, 0.4)';
    status.style.color = isError ? '#fecaca' : '#bfdbfe';
}

function showReportDownload(href) {
    const link = document.getElementById('reportDownloadLink');
    if (link) {
        link.href = href;
        link.style.display = 'inline-block';
    }

    const generateBtn = document.getElementById('reportGenerateBtn');
    if (generateBtn) {
        generateBtn.disabled = false;
        generateBtn.textContent = 'Generar de nuevo';
    }
}

async function loadCrawlFromDashboard(crawlId) {
    if (!confirm('¿Cargar este rastreo? Se perderán los datos actuales no guardados.')) return;

    try {
        const response = await fetch(`/api/crawls/${crawlId}/load`, {
            method: 'POST'
        });
        const data = await response.json();

        if (!data.success) {
            alert('Error: ' + (data.error || data.message));
            return;
        }

        closeDashboard();
        await attachToServerCrawl(crawlId);

        showNotification('Rastreo cargado correctamente', 'success');

    } catch (error) {
        console.error('Error loading crawl:', error);
        alert('Error al cargar el rastreo');
    }
}

async function resumeCrawlFromDashboard(crawlId) {
    if (!confirm('¿Reanudar este rastreo? Se perderán los datos actuales no guardados.')) return;

    try {
        const response = await fetch(`/api/crawls/${crawlId}/resume`, {
            method: 'POST'
        });
        const data = await response.json();

        if (!data.success) {
            alert('Error: ' + (data.error || data.message));
            return;
        }

        closeDashboard();
        await attachToServerCrawl(crawlId);

        showNotification('Rastreo reanudado correctamente', 'success');

    } catch (error) {
        console.error('Error resuming crawl:', error);
        alert('Error al reanudar el rastreo');
    }
}

async function pauseCrawlFromDashboard(crawlId) {
    try {
        const response = await fetch(`/api/crawls/${crawlId}/pause`, { method: 'POST' });
        const data = await response.json();
        if (!data.success) {
            alert('Error al pausar el rastreo: ' + (data.error || data.message));
            return;
        }
        showNotification('Rastreo pausado', 'success');
        openDashboard();
    } catch (error) {
        console.error('Error pausing crawl:', error);
        alert('Error al pausar el rastreo');
    }
}

async function stopCrawlFromDashboard(crawlId) {
    if (!confirm('¿Detener este rastreo?')) return;
    try {
        const response = await fetch(`/api/crawls/${crawlId}/stop`, { method: 'POST' });
        const data = await response.json();
        if (!data.success) {
            alert('Error al detener el rastreo: ' + (data.error || data.message));
            return;
        }
        showNotification('Rastreo detenido', 'success');
        openDashboard();
    } catch (error) {
        console.error('Error stopping crawl:', error);
        alert('Error al detener el rastreo');
    }
}

async function deleteCrawlFromDashboard(crawlId) {
    if (!confirm('¿Eliminar este rastreo permanentemente? Esta acción no se puede deshacer.')) return;

    try {
        const response = await fetch(`/api/crawls/${crawlId}/delete`, {
            method: 'DELETE'
        });
        const data = await response.json();

        if (data.success) {
            showNotification('Rastreo eliminado', 'success');
            // Reload dashboard
            openDashboard();
        } else {
            alert('Error al eliminar el rastreo: ' + data.error);
        }
    } catch (error) {
        console.error('Error deleting crawl:', error);
        alert('Error al eliminar el rastreo');
    }
}
