// ========================================
// Dashboard Functions
// ========================================

let activeReportCrawlId = null;
let reportPollTimer = null;
let reportActionsEnabled = false;
let dashboardRefreshTimer = null;

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

        if (typeof window.populateReportModelSelect === 'function') {
            window.populateReportModelSelect('reportGenerateModelSelect', settings.default_model || '', true);
        }

        setReportFieldValue('reportGenerateModelSelect', settings.default_model || '');
        setReportFieldValue('reportGenerateManualModel', settings.manual_model || '');
        setReportFieldValue('reportGenerateLanguage', settings.default_language || 'es-ES');
        setReportFieldValue('reportGenerateTone', settings.default_tone || 'executive');
        setReportFieldValue('reportGenerateAgencyName', settings.agency_name || 'Mitmore SEO Crawl');
        setReportFieldValue('reportGeneratePrimaryColor', settings.primary_color || '#2563eb');
        setReportFieldValue('reportGenerateFooterText', settings.footer_text || '');
        setReportFieldValue('reportGenerateLogoPath', settings.logo_path || '');
        setReportStatus('');
        await loadReportDownloadButtons(crawlId);

        const downloadLink = document.getElementById('reportDownloadLink');
        if (downloadLink) downloadLink.style.display = 'none';

        const generateBtn = document.getElementById('reportGenerateBtn');
        if (generateBtn) {
            generateBtn.disabled = false;
            generateBtn.textContent = 'Generar PDF';
        }

        modal.style.display = 'flex';
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

function setReportFieldValue(id, value) {
    const element = document.getElementById(id);
    if (element) element.value = value;
}

function reportGenerationPayloadFromModal() {
    return {
        model: document.getElementById('reportGenerateModelSelect')?.value || '',
        manual_model: document.getElementById('reportGenerateManualModel')?.value || '',
        language: document.getElementById('reportGenerateLanguage')?.value || 'es-ES',
        tone: document.getElementById('reportGenerateTone')?.value || 'executive',
        agency_name: document.getElementById('reportGenerateAgencyName')?.value || '',
        primary_color: document.getElementById('reportGeneratePrimaryColor')?.value || '#2563eb',
        footer_text: document.getElementById('reportGenerateFooterText')?.value || '',
        logo_path: document.getElementById('reportGenerateLogoPath')?.value || ''
    };
}

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

    const completed = (reports || []).filter(report => report.status === 'completed' && report.download_url);
    if (!completed.length) {
        container.innerHTML = '';
        return;
    }

    container.innerHTML = `
        <div class="report-downloads-title">Descargas generadas</div>
        <div class="report-downloads-grid">
            ${completed.map(report => `
                <a class="report-download-btn" href="${safeReportText(report.download_url)}" target="_blank">
                    <strong>${safeReportText(reportToneLabel(report.tone))}</strong>
                    <span>${safeReportText(reportLanguageLabel(report.language))}</span>
                </a>
            `).join('')}
        </div>
    `;
}

async function submitReportGeneration() {
    if (!activeReportCrawlId) return;

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
        const model = settings.manual_model || settings.default_model || 'modelo guardado';
        const language = settings.default_language || 'es-ES';
        const tone = settings.default_tone || 'executive';
        if (!confirm(`¿Generar informe PDF con ${model}, ${language}, ${tone}?`)) return;
        await createReport(crawlId, {}, false);
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
        pollReportStatus(data.report_id, useModal);
    } catch (error) {
        console.error('Error creating report:', error);
        setReportStatus(error.message || 'La generación del informe ha fallado', true);
        reportDashboardNotice(error.message || 'La generación del informe ha fallado', 'error');

        const generateBtn = document.getElementById('reportGenerateBtn');
        if (generateBtn) {
            generateBtn.disabled = false;
            generateBtn.textContent = 'Generar PDF';
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
            const generateBtn = document.getElementById('reportGenerateBtn');
            if (generateBtn) {
                generateBtn.disabled = false;
                generateBtn.textContent = 'Generar PDF';
            }
            return;
        }

        setReportStatus(`Informe ${report.status || 'en cola'}...`);
        reportPollTimer = setTimeout(() => pollReportStatus(reportId, useModal), 2000);
    } catch (error) {
        reportPollTimer = null;
        console.error('Error polling report:', error);
        setReportStatus(error.message || 'El estado del informe ha fallado', true);
        reportDashboardNotice(error.message || 'El estado del informe ha fallado', 'error');
        const generateBtn = document.getElementById('reportGenerateBtn');
        if (generateBtn) {
            generateBtn.disabled = false;
            generateBtn.textContent = 'Generar PDF';
        }
    }
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
