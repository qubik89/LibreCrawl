// Application State
let crawlState = {
    currentCrawlId: null,
    isRunning: false,
    isPaused: false,
    startTime: null,
    baseUrl: null,
    urls: [],
    links: [],
    issues: [],
    analytics: null,
    stats: {
        discovered: 0,
        crawled: 0,
        depth: 0,
        speed: 0
    },
    filters: {
        active: null,
        issueFilter: 'all',
        linksFilter: {
            internalStatusCode: 'all',
            externalStatusCode: 'all',
            internalSearch: '',
            externalSearch: ''
        }
    },
    dashboardDrilldown: { urls: null, links: null, issues: null }
};
window.crawlState = crawlState;

let serverPageState = {
    urls: { nextCursor: null, loaded: 0, hasMore: true, filter: 'all' },
    links: { nextCursor: null, loaded: 0, hasMore: true, filter: 'all' },
    issues: { nextCursor: null, loaded: 0, hasMore: true, filter: 'all' }
};

const SERVER_PAGE_SIZE = 500;
const CLIENT_ROW_LIMIT = 2000;
const URL_SERVER_KINDS = ['internal', 'external', '2xx', '3xx', '4xx', '5xx', 'no_response', 'html', 'css', 'js', 'images'];
const LINK_SERVER_KINDS = ['internal', 'external', '2xx', '3xx', '4xx', '5xx'];

function formatNumber(value, options = {}) {
    return Number(value || 0).toLocaleString(undefined, options);
}

// Incremental polling instance
let incrementalPoller = null;

// Virtual Scrollers
let virtualScrollers = {
    overview: null,
    internal: null,
    external: null,
    internalLinks: null,
    externalLinks: null,
    issues: null
};

// Initialize application
document.addEventListener('DOMContentLoaded', async function() {
    await initializeApp();
});

async function initializeApp() {
    // Load plugins first (before tabs are initialized)
    if (window.MitmoreSEOCrawlPlugin && window.MitmoreSEOCrawlPlugin.loader) {
        await window.MitmoreSEOCrawlPlugin.loader.loadAllPlugins();
        window.MitmoreSEOCrawlPlugin.loader.initializePlugins();
    }

    // Setup event listeners
    setupEventListeners();

    // Initialize tables
    initializeTables();

    // Load user info
    loadUserInfo();

    // DEBUG: Check sessionStorage
    console.log('DEBUG: Checking sessionStorage force_ui_refresh:', sessionStorage.getItem('force_ui_refresh'));

    // Check if we just attached to a crawl from dashboard
    const loadedCrawlId = sessionStorage.getItem('loaded_crawl_id');
    if (sessionStorage.getItem('force_ui_refresh') === 'true' && loadedCrawlId) {
        console.log('DEBUG: Found force_ui_refresh flag, loading crawl data...');
        sessionStorage.removeItem('force_ui_refresh');
        sessionStorage.removeItem('loaded_crawl_id');

        try {
            await attachToServerCrawl(parseInt(loadedCrawlId, 10));
        } catch (error) {
            console.error('Error loading crawl data:', error);
            updateStatus('Error al cargar datos del rastreo');
        }
    }

    const reportCrawlId = sessionStorage.getItem('open_report_modal_crawl_id');
    if (reportCrawlId) {
        sessionStorage.removeItem('open_report_modal_crawl_id');
        setTimeout(() => {
            if (typeof openReportModal === 'function') {
                openReportModal(parseInt(reportCrawlId, 10));
            }
        }, 250);
    }

    // Set initial focus
    document.getElementById('urlInput').focus();

    console.log('Mitmore SEO Crawl initialized');
}

function setupEventListeners() {
    // URL input enter key
    document.getElementById('urlInput').addEventListener('keypress', handleUrlKeypress);

    // Update timer every second when crawling
    setInterval(updateTimer, 1000);
}

function handleUrlKeypress(event) {
    if (event.key === 'Enter' && !crawlState.isRunning) {
        toggleCrawl();
    }
}

function toggleCrawl() {
    if (!crawlState.isRunning) {
        startCrawl();
    } else if (crawlState.isPaused) {
        resumeCrawl();
    } else {
        pauseCrawl();
    }
}

function startCrawl() {
    const urlInput = document.getElementById('urlInput');
    let url = urlInput.value.trim();

    if (!url) {
        alert('Introduce una URL para rastrear');
        urlInput.focus();
        return;
    }

    // Normalize the URL - add protocol if missing
    url = normalizeUrl(url);

    if (!isValidUrl(url)) {
        alert('Introduce una URL o dominio válido');
        urlInput.focus();
        return;
    }

    // Update the input field with the normalized URL
    urlInput.value = url;

    crawlState.isRunning = true;
    crawlState.isPaused = false;
    crawlState.startTime = new Date();
    crawlState.baseUrl = url;
    crawlState.currentCrawlId = null;
    resetServerPageState();

    // Initialize incremental poller for new crawl
    if (!incrementalPoller) {
        incrementalPoller = new IncrementalPoller();
    }
    incrementalPoller.reset();

    // Update UI
    updateCrawlButtons();
    showProgress();
    updateStatus('Iniciando rastreo...');

    // Clear previous data
    clearAllTables();
    resetStats();

    // Start the actual crawling via Python backend
    startPythonCrawl(url);
}

function pauseCrawl() {
    crawlState.isPaused = true;
    updateCrawlButtons();
    updateStatus('Rastreo en pausa');

    // Pause Python crawler
    fetch(crawlState.currentCrawlId ? `/api/crawls/${crawlState.currentCrawlId}/pause` : '/api/pause_crawl', {
        method: 'POST'
    }).catch(error => {
        console.error('Error pausing crawl:', error);
    });
}

function resumeCrawl() {
    crawlState.isPaused = false;
    updateCrawlButtons();
    updateStatus('Reanudando rastreo...');

    // Resume Python crawler
    fetch(crawlState.currentCrawlId ? `/api/crawls/${crawlState.currentCrawlId}/resume` : '/api/resume_crawl', {
        method: 'POST'
    }).catch(error => {
        console.error('Error resuming crawl:', error);
    });
}

function stopCrawl() {
    crawlState.isRunning = false;
    crawlState.isPaused = false;

    // Update UI
    updateCrawlButtons();
    hideProgress();
    updateStatus('Rastreo detenido');

    // Stop Python crawler
    stopPythonCrawl();
}

function clearCrawlData() {
    if (crawlState.isRunning) {
        if (!confirm('Hay un rastreo en curso. ¿Detenerlo y borrar todos los datos?')) {
            return;
        }
        stopCrawl();
    }

    // Clear all data
    clearAllTables();
    resetStats();
    crawlState.urls = [];
    crawlState.links = [];
    crawlState.issues = [];
    crawlState.analytics = null;
    crawlState.baseUrl = null;
    crawlState.currentCrawlId = null;
    crawlState.dashboardDrilldown = { urls: null, links: null, issues: null };
    resetServerPageState();
    crawlState.filters.active = null;
    crawlState.pendingLinks = null;
    crawlState.pendingIssues = null;
    updateStatusCodesTable();

    // Clear issues and reset badge
    window.currentIssues = [];
    updateIssuesTable([]);  // This will also clear the badge

    // Reset issue filter counts
    document.getElementById('issues-all-count').textContent = '(0)';
    document.getElementById('issues-error-count').textContent = '(0)';
    document.getElementById('issues-warning-count').textContent = '(0)';
    document.getElementById('issues-info-count').textContent = '(0)';

    // Clear visualization
    if (typeof window.clearVisualization === 'function') {
        window.clearVisualization();
    }

    // Notify plugins of data clear (send empty data)
    if (window.MitmoreSEOCrawlPlugin && window.MitmoreSEOCrawlPlugin.loader) {
        window.MitmoreSEOCrawlPlugin.loader.notifyDataUpdate({
            urls: [],
            links: [],
            issues: [],
            stats: { discovered: 0, crawled: 0, depth: 0, speed: 0 }
        });
    }

    // Clear filter states
    document.querySelectorAll('.filter-item').forEach(item => {
        item.classList.remove('active');
    });

    // Reset the "All Issues" filter to active
    document.querySelector('[data-filter="all"]')?.classList.add('active');

    // Update UI
    updateStatus('Datos borrados');
    hideProgress();
    updateCrawlButtons(); // Update save/load button states
    if (window.CrawlOverviewDashboard) window.CrawlOverviewDashboard.reset();

    // Reset URL input
    document.getElementById('urlInput').value = '';
    document.getElementById('urlInput').focus();
}

function startPythonCrawl(url) {
    // Call Python backend to start crawling
    fetch('/api/start_crawl', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ url: url })
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            crawlState.currentCrawlId = data.crawl_id;
            sessionStorage.setItem('current_crawl_id', data.crawl_id);
            updateStatus('Rastreo en curso...');
            // Refresh user info to update crawl count
            loadUserInfo();
            loadActiveTabPage(true);
            // Start polling for updates
            pollCrawlProgress();
        } else {
            updateStatus('Error: ' + data.error);
            stopCrawl();
        }
    })
    .catch(error => {
        console.error('Error starting crawl:', error);
        updateStatus('Error al iniciar el rastreo');
        stopCrawl();
    });
}

function stopPythonCrawl() {
    fetch(crawlState.currentCrawlId ? `/api/crawls/${crawlState.currentCrawlId}/stop` : '/api/stop_crawl', {
        method: 'POST'
    })
    .then(response => response.json())
    .then(data => {
        console.log('Crawl stopped:', data);
    })
    .catch(error => {
        console.error('Error stopping crawl:', error);
    });
}

async function pollCrawlProgress() {
    if (!crawlState.isRunning) return;

    try {
        const crawlId = crawlState.currentCrawlId;
        const response = await fetch(crawlId ? `/api/crawls/${crawlId}/status` : '/api/crawl_status');
        const data = await response.json();

        updateCrawlData(data);

        if (data.is_running_pagespeed) {
            updateStatus('Ejecutando análisis PageSpeed...');
        } else if (data.status === 'running') {
            updateStatus('Rastreo en curso...');
        }

        if (data.status === 'demo_stopped' || data.demo_stopped) {
            crawlState.isRunning = false;
            updateCrawlButtons();
            updateStatus('Límite de demo alcanzado: datos del rastreo guardados');
            showDemoLimitNotification();
            refreshVisualizationAfterCrawl();
        } else if (crawlState.isRunning && data.status !== 'completed' && data.status !== 'failed' && data.status !== 'stopped') {
            setTimeout(pollCrawlProgress, 1000);
        } else if (data.status === 'completed') {
            crawlState.isRunning = false;
            updateCrawlButtons();
            hideProgress();
            updateStatus('Rastreo completado');
            refreshVisualizationAfterCrawl();
            if (window.MitmoreSEOCrawlPlugin && window.MitmoreSEOCrawlPlugin.loader) {
                window.MitmoreSEOCrawlPlugin.loader.notifyCrawlComplete({
                    urls: crawlState.urls,
                    links: crawlState.links,
                    issues: crawlState.issues,
                    stats: crawlState.stats
                });
            }
        } else if (data.status === 'failed' || data.status === 'stopped') {
            crawlState.isRunning = false;
            updateCrawlButtons();
            hideProgress();
            updateStatus(data.status === 'failed' ? 'Rastreo fallido' : 'Rastreo detenido');
        }
    } catch (error) {
        console.error('Error polling crawl status:', error);
        if (crawlState.isRunning) {
            setTimeout(pollCrawlProgress, 1000);
        }
    }
}

function refreshVisualizationAfterCrawl() {
    const vizTab = document.getElementById('visualization-tab');
    if (vizTab && vizTab.classList.contains('active') && typeof window.loadVisualizationData === 'function') {
        window.loadVisualizationData();
    }
}

function resetServerPageState(kind = null) {
    const empty = filter => ({ nextCursor: null, loaded: 0, hasMore: true, filter });
    if (kind) {
        serverPageState[kind] = empty(serverPageState[kind]?.filter || 'all');
        return;
    }
    serverPageState = {
        urls: empty('all'),
        links: empty('all'),
        issues: empty('all')
    };
}

function getActiveTabName() {
    const pane = document.querySelector('.tab-pane.active');
    return pane ? pane.id.replace(/-tab$/, '') : 'overview';
}

function getActiveServerTarget() {
    const tabName = getActiveTabName();
    if (['internal', 'external'].includes(tabName)) {
        const drilldown = crawlState.dashboardDrilldown.urls;
        if (drilldown) {
            return {
                kind: 'urls',
                filter: `dashboard:${JSON.stringify(drilldown)}:${tabName}`,
                params: { scope: tabName, ...drilldown }
            };
        }
        const activeFilter = crawlState.filters.active || 'all';
        const serverKind = URL_SERVER_KINDS.includes(activeFilter)
            ? activeFilter
            : tabName;
        return {
            kind: 'urls',
            filter: `${tabName}:${activeFilter}`,
            params: serverKind === 'all' ? {} : { kind: serverKind }
        };
    }
    if (tabName === 'links') {
        const drilldown = crawlState.dashboardDrilldown.links;
        if (drilldown) {
            return {
                kind: 'links',
                filter: `dashboard:${JSON.stringify(drilldown)}`,
                params: drilldown
            };
        }
        const linkKind = getActiveLinkServerKind();
        return {
            kind: 'links',
            filter: JSON.stringify(crawlState.filters.linksFilter),
            params: linkKind ? { kind: linkKind } : {}
        };
    }
    if (tabName === 'issues') {
        const drilldown = crawlState.dashboardDrilldown.issues;
        if (drilldown) {
            const params = { ...drilldown };
            if (Array.isArray(params.issues)) {
                params.issue = params.issues;
                delete params.issues;
            }
            return {
                kind: 'issues',
                filter: `dashboard:${JSON.stringify(params)}`,
                params
            };
        }
        const issueType = crawlState.filters.issueFilter || 'all';
        return {
            kind: 'issues',
            filter: issueType,
            params: issueType === 'all' ? {} : { issue_type: issueType }
        };
    }
    return null;
}

function getActiveLinkServerKind() {
    const filters = crawlState.filters.linksFilter;
    const internalStatus = filters.internalStatusCode;
    const externalStatus = filters.externalStatusCode;
    if (internalStatus !== 'all' && externalStatus !== 'all' && internalStatus !== externalStatus) return null;
    if (LINK_SERVER_KINDS.includes(internalStatus) && internalStatus !== 'all') return internalStatus;
    if (LINK_SERVER_KINDS.includes(externalStatus) && externalStatus !== 'all') return externalStatus;
    return null;
}

async function loadActiveTabPage(reset = false) {
    const target = getActiveServerTarget();
    if (!target) return;
    return loadServerRows(target.kind, reset);
}

async function loadServerRows(kind, reset = false) {
    const crawlId = crawlState.currentCrawlId;
    const target = getActiveServerTarget();
    if (!crawlId || !target || target.kind !== kind) return;

    const state = serverPageState[kind];
    const filterChanged = state.filter !== target.filter;
    if (reset || filterChanged) {
        state.nextCursor = null;
        state.loaded = 0;
        state.hasMore = true;
        state.filter = target.filter;
        clearServerRows(kind);
    }
    if (!state.hasMore) return;

    const params = new URLSearchParams({ limit: SERVER_PAGE_SIZE });
    if (state.nextCursor !== null && state.nextCursor !== undefined) {
        params.set('after', state.nextCursor);
    }
    Object.entries(target.params).forEach(([key, value]) => {
        if (Array.isArray(value)) value.forEach(item => params.append(key, item));
        else params.set(key, value);
    });

    try {
        const response = await fetch(`/api/crawls/${crawlId}/${kind}?${params.toString()}`);
        const data = await response.json();
        if (!data.success) throw new Error(data.error || `Failed to load ${kind}`);

        const rows = data[kind] || [];
        appendServerRows(kind, rows);

        const inferredCursor = rows.length ? rows[rows.length - 1]._row_order : null;
        const nextCursor = data.next_cursor ?? inferredCursor;
        state.nextCursor = nextCursor ?? state.nextCursor;
        state.loaded += rows.length;
        state.hasMore = typeof data.has_more === 'boolean'
            ? data.has_more
            : Boolean(nextCursor) && rows.length >= SERVER_PAGE_SIZE;
    } catch (error) {
        console.error(`Error loading ${kind}:`, error);
    }
}

function clearServerRows(kind) {
    if (kind === 'urls') {
        crawlState.urls = [];
        refreshUrlTables();
    } else if (kind === 'links') {
        crawlState.links = [];
        updateLinksTable([]);
    } else if (kind === 'issues') {
        crawlState.issues = [];
        updateIssuesTable([]);
    }
}

function mergeRows(existing, rows, keyFn) {
    const byKey = new Map();
    existing.concat(rows).forEach(row => {
        const key = keyFn(row);
        if (key) byKey.set(key, row);
    });
    return Array.from(byKey.values()).slice(-CLIENT_ROW_LIMIT);
}

function issueRowKey(row) {
    const rowOrder = row && row._row_order;
    if (typeof rowOrder === 'string' && rowOrder.length > 0) {
        return `row-order:${rowOrder}`;
    }
    if (Number.isSafeInteger(rowOrder)) {
        return `row-order:${rowOrder}`;
    }

    // Older servers sent UInt64 cursors as unsafe JS numbers. Fall back to
    // the issue payload instead of collapsing unrelated rows in that case.
    return `issue:${row?.url || ''}|${row?.type || ''}|${row?.category || ''}|${row?.issue || ''}|${row?.details || ''}`;
}

function appendServerRows(kind, rows) {
    if (!rows.length) return;

    if (kind === 'urls') {
        crawlState.urls = mergeRows(crawlState.urls, rows, row => row.url);
        refreshUrlTables();
    } else if (kind === 'links') {
        crawlState.links = mergeRows(
            crawlState.links,
            rows,
            row => `${row.source_url}|${row.target_url}|${row.anchor_text || ''}`
        );
        updateLinksTable(crawlState.links);
    } else if (kind === 'issues') {
        crawlState.issues = mergeRows(
            crawlState.issues,
            rows,
            issueRowKey
        );
        updateIssuesTable(crawlState.issues);
        if (crawlState.filters.issueFilter !== 'all') {
            applyIssueFilterToScroller();
        }
    }
}

function refreshUrlTables() {
    if (crawlState.filters.active) {
        filterVirtualScrollerData('overview', crawlState.filters.active);
        filterVirtualScrollerData('internal', crawlState.filters.active);
        filterVirtualScrollerData('external', crawlState.filters.active);
    } else {
        if (virtualScrollers.overview) virtualScrollers.overview.setData(crawlState.urls);
        if (virtualScrollers.internal) virtualScrollers.internal.setData(crawlState.urls.filter(url => url.is_internal));
        if (virtualScrollers.external) virtualScrollers.external.setData(crawlState.urls.filter(url => !url.is_internal));
    }
    updateStatusCodesTable(crawlState.filters.active);
}

async function attachToServerCrawl(crawlId) {
    crawlState.currentCrawlId = crawlId;
    resetServerPageState();
    clearAllTables();
    resetStats();

    const response = await fetch(`/api/crawls/${crawlId}/status`);
    const data = await response.json();
    if (!data.success) throw new Error(data.error || 'No se pudo adjuntar el rastreo');

    crawlState.baseUrl = data.stats?.baseUrl || '';
    if (crawlState.baseUrl) document.getElementById('urlInput').value = crawlState.baseUrl;

    crawlState.isRunning = ['running', 'paused'].includes(data.status);
    crawlState.isPaused = data.status === 'paused';
    crawlState.startTime = crawlState.isRunning ? new Date() : null;
    if (crawlState.isRunning) showProgress();
    updateCrawlData(data);
    loadActiveTabPage(true);
    updateCrawlButtons();
    updateStatus(crawlState.isRunning ? 'Adjuntado a rastreo en curso' : `Rastreo cargado: ${data.stats?.crawled || 0} URLs`);

    if (crawlState.isRunning) pollCrawlProgress();
}

function updateCrawlData(data) {
    // Update statistics
    crawlState.stats = data.stats || crawlState.stats;
    crawlState.analytics = data.analytics || crawlState.analytics;
    updateStatsDisplay();

    // Update memory statistics
    if (data.memory && data.memory_data) {
        updateMemoryDisplay(data.memory, data.memory_data);
    }

    // Update tables with new URLs
    if (data.urls) {
        data.urls.forEach(url => {
            addUrlToTable(url);
        });
    }

    // Update links tables only if Links tab is active to improve performance
    if (data.links && data.links.length > 0) {
        const existingLinks = new Set(crawlState.links.map(link => `${link.source_url}|${link.target_url}`));
        data.links.forEach(link => {
            const key = `${link.source_url}|${link.target_url}`;
            if (!existingLinks.has(key)) {
                existingLinks.add(key);
                crawlState.links.push(link);
            }
        });
        if (isLinksTabActive()) {
            updateLinksTable(crawlState.links);
        } else {
            // Store in pendingLinks for lazy loading when switching to tab
            crawlState.pendingLinks = crawlState.links;
        }
    }

    // Update issues table only if Issues tab is active
    if (data.issues && data.issues.length > 0) {
        crawlState.issues.push(...data.issues);
        if (isIssuesTabActive()) {
            updateIssuesTable(crawlState.issues);
        } else {
            // Store in pendingIssues for lazy loading when switching to tab
            crawlState.pendingIssues = crawlState.issues;
        }
    }

    // Update filter counts
    updateFilterCounts();

    // Update status codes table (respecting active filter)
    updateStatusCodesTable(crawlState.filters.active);

    // Update progress and status text
    updateProgress(data.progress || 0);
    updateProgressText(data);

    // Update PageSpeed results if available
    if (data.stats && data.stats.pagespeed_results) {
        displayPageSpeedResults(data.stats.pagespeed_results);
    }

    // Notify plugins of data update
    if (window.MitmoreSEOCrawlPlugin && window.MitmoreSEOCrawlPlugin.loader) {
        window.MitmoreSEOCrawlPlugin.loader.notifyDataUpdate({
            urls: crawlState.urls,
            links: crawlState.links,
            issues: crawlState.issues,
            stats: crawlState.stats
        });
    }
}

function updateProgressText(data) {
    const progressText = document.getElementById('progressText');
    if (!progressText) return;

    if (data.is_running_pagespeed) {
        progressText.textContent = 'Ejecutando análisis PageSpeed...';
    } else if (data.status === 'completed') {
        progressText.textContent = 'Rastreo completado';
    } else if (data.status === 'paused') {
        progressText.textContent = 'Rastreo en pausa';
    } else if (data.status === 'running') {
        const stats = data.stats || crawlState.stats;
        if (stats.crawled === 0) {
            progressText.textContent = 'Iniciando rastreo...';
        } else if (stats.discovered > stats.crawled) {
            progressText.textContent = `Rastreando... (${formatNumber(stats.crawled)}/${formatNumber(stats.discovered)} URLs)`;
        } else {
            progressText.textContent = `Terminando... (${formatNumber(stats.crawled)} URLs rastreadas)`;
        }
    } else {
        progressText.textContent = 'Inicializando...';
    }
}

function updateStatsDisplay() {
    document.getElementById('discoveredCount').textContent = formatNumber(crawlState.stats.discovered);
    document.getElementById('crawledCount').textContent = formatNumber(crawlState.stats.crawled);
    document.getElementById('crawlDepth').textContent = formatNumber(crawlState.stats.depth);
    document.getElementById('crawlSpeed').textContent = formatNumber(crawlState.stats.speed, { maximumFractionDigits: 2 }) + ' URLs/sec';
}

function updateMemoryDisplay(memoryData, memoryDataSizes) {
    if (!memoryData || !memoryDataSizes) return;

    // Actual data size (deep measurement)
    const dataMB = memoryDataSizes.total_deep_mb || 0;
    document.getElementById('memCurrent').textContent = dataMB.toFixed(1) + ' MB';

    // KB per URL (actual data)
    const kbPerUrl = memoryDataSizes.avg_per_url_kb || 0;
    document.getElementById('memPeak').textContent = kbPerUrl.toFixed(1) + ' KB/URL';

    // Estimate for 1M URLs (data only)
    const estimate1M = (kbPerUrl * 1000000) / 1024; // Convert to MB
    const estimate1MDisplay = estimate1M > 1024
        ? (estimate1M / 1024).toFixed(1) + ' GB'
        : estimate1M.toFixed(0) + ' MB';
    document.getElementById('memEstimate1M').textContent = estimate1MDisplay;

    // System available
    const availableMB = memoryData.system?.available_mb || 0;
    document.getElementById('memAvailable').textContent = availableMB.toFixed(0) + ' MB';
}

function updateCrawlButtons() {
    const startBtn = document.getElementById('startBtn');
    const stopBtn = document.getElementById('stopBtn');
    const clearBtn = document.getElementById('clearBtn');
    const saveCrawlBtn = document.getElementById('saveCrawlBtn');
    const loadCrawlBtn = document.getElementById('loadCrawlBtn');

    if (crawlState.isRunning) {
        if (crawlState.isPaused) {
            startBtn.innerHTML = `
                <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                    <path d="M8 5v14l11-7z"/>
                </svg>
                Reanudar
            `;
        } else {
            startBtn.innerHTML = `
                <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                    <rect x="6" y="4" width="4" height="16"/>
                    <rect x="14" y="4" width="4" height="16"/>
                </svg>
                Pausar
            `;
        }
        startBtn.disabled = false;
        stopBtn.disabled = false;
        clearBtn.disabled = false;
        saveCrawlBtn.disabled = true; // Disable during crawl
        loadCrawlBtn.disabled = true; // Disable during crawl
    } else {
        startBtn.innerHTML = `
            <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                <path d="M8 5v14l11-7z"/>
            </svg>
            Iniciar
        `;
        startBtn.disabled = false;
        stopBtn.disabled = true;
        clearBtn.disabled = false;

        // Save button: only enabled if crawl is completed and has data
        const hasData = crawlState.stats.crawled > 0;
        saveCrawlBtn.disabled = !hasData;

        // Load button: only enabled if no current crawl data
        loadCrawlBtn.disabled = hasData;
    }
}

function showProgress() {
    document.getElementById('progressContainer').style.display = 'flex';
}

function hideProgress() {
    document.getElementById('progressContainer').style.display = 'none';
}

function updateProgress(percentage) {
    document.getElementById('progressFill').style.width = percentage + '%';
}

function updateStatus(message) {
    document.getElementById('statusText').textContent = message;
}

function showDemoLimitNotification() {
    // Remove existing demo notification if any
    const existing = document.getElementById('demoLimitNotification');
    if (existing) existing.remove();

    const overlay = document.createElement('div');
    overlay.id = 'demoLimitNotification';
    overlay.style.cssText = `
        position: fixed; top: 0; left: 0; right: 0; bottom: 0;
        background: rgba(0,0,0,0.6); z-index: 10000;
        display: flex; align-items: center; justify-content: center;
    `;

    const box = document.createElement('div');
    box.style.cssText = `
        background: #1a1a2e; border: 1px solid #f59e0b; border-radius: 12px;
        padding: 32px 40px; max-width: 480px; text-align: center;
        color: #e0e0e0; box-shadow: 0 8px 32px rgba(0,0,0,0.5);
    `;
    box.innerHTML = `
        <div style="font-size: 28px; margin-bottom: 12px;">&#9888;</div>
        <h2 style="color: #f59e0b; margin: 0 0 12px; font-size: 18px;">Límite de memoria de demo alcanzado</h2>
        <p style="margin: 0 0 16px; line-height: 1.5; font-size: 14px;">
            Este usuario ha alcanzado el límite de memoria de 1,5 GB por usuario.<br>
            Los datos del rastreo se han guardado automáticamente.<br><br>
            <strong>Esta es una demo gratuita y no está pensada para uso en producción.</strong>
        </p>
        <button id="demoLimitDismiss" style="
            background: #f59e0b; color: #000; border: none; padding: 10px 28px;
            border-radius: 6px; font-weight: 600; cursor: pointer; font-size: 14px;
        ">OK</button>
    `;

    overlay.appendChild(box);
    document.body.appendChild(overlay);

    document.getElementById('demoLimitDismiss').addEventListener('click', function() {
        overlay.remove();
    });
}

function updateTimer() {
    if (crawlState.isRunning && crawlState.startTime) {
        const elapsed = new Date() - crawlState.startTime;
        const minutes = Math.floor(elapsed / 60000);
        const seconds = Math.floor((elapsed % 60000) / 1000);
        document.getElementById('crawlTime').textContent =
            `${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
    }
}

// Table Management
function initializeTables() {
    // Clear any existing data first
    clearAllTables();
    // Initialize virtual scrollers for all tables
    initializeVirtualScrollers();
    // Initialize column resizers after virtual scrollers
    setTimeout(() => {
        if (window.initializeColumnResizers) {
            initializeColumnResizers();
        }
    }, 100);
}

function initializeVirtualScrollers() {
    try {
        // Overview table
        const overviewContainer = document.querySelector('#overview-tab .table-container');
        if (overviewContainer && overviewContainer.querySelector('tbody')) {
            virtualScrollers.overview = new VirtualScroller(overviewContainer, {
                rowHeight: 100,
                buffer: 25,
                renderRow: renderOverviewRow
            });
            console.log('Overview virtual scroller initialized');
        }

        // Internal URLs table
        const internalContainer = document.querySelector('#internal-tab .table-container');
        if (internalContainer && internalContainer.querySelector('tbody')) {
            virtualScrollers.internal = new VirtualScroller(internalContainer, {
                rowHeight: 80,
                buffer: 25,
                renderRow: renderInternalRow
            });
            console.log('Internal virtual scroller initialized');
        }

        // External URLs table
        const externalContainer = document.querySelector('#external-tab .table-container');
        if (externalContainer && externalContainer.querySelector('tbody')) {
            virtualScrollers.external = new VirtualScroller(externalContainer, {
                rowHeight: 80,
                buffer: 25,
                renderRow: renderExternalRow
            });
            console.log('External virtual scroller initialized');
        }

        // Internal Links table
        const internalLinksContainer = document.querySelector('#links-tab .internal-links-container');
        if (internalLinksContainer && internalLinksContainer.querySelector('tbody')) {
            virtualScrollers.internalLinks = new VirtualScroller(internalLinksContainer, {
                rowHeight: 80,
                buffer: 25,
                renderRow: renderInternalLinkRow
            });
            console.log('Internal links virtual scroller initialized');
        }

        // External Links table
        const externalLinksContainer = document.querySelector('#links-tab .external-links-container');
        if (externalLinksContainer && externalLinksContainer.querySelector('tbody')) {
            virtualScrollers.externalLinks = new VirtualScroller(externalLinksContainer, {
                rowHeight: 80,
                buffer: 25,
                renderRow: renderExternalLinkRow
            });
            console.log('External links virtual scroller initialized');
        }

        // Issues table
        const issuesContainer = document.querySelector('#issues-tab .table-container');
        if (issuesContainer && issuesContainer.querySelector('tbody')) {
            virtualScrollers.issues = new VirtualScroller(issuesContainer, {
                rowHeight: 80,
                buffer: 25,
                renderRow: renderIssueRow
            });
            console.log('Issues virtual scroller initialized');
        }
    } catch (error) {
        console.error('Error initializing virtual scrollers:', error);
    }
}

function isLinksTabActive() {
    const linksTab = document.getElementById('links-tab');
    return linksTab && linksTab.classList.contains('active');
}

function isIssuesTabActive() {
    const issuesTab = document.getElementById('issues-tab');
    return issuesTab && issuesTab.classList.contains('active');
}

function updateLinksTable(links) {
    // Create a lookup map of URL statuses from crawled URLs
    const urlStatusMap = new Map();
    if (crawlState.urls && crawlState.urls.length > 0) {
        crawlState.urls.forEach(url => {
            urlStatusMap.set(url.url, url.status_code);
        });
    }

    // Remove duplicates from links array (extra safety check)
    const uniqueLinks = [];
    const seenLinks = new Set();
    links.forEach(link => {
        const key = `${link.source_url}|${link.target_url}`;
        if (!seenLinks.has(key)) {
            seenLinks.add(key);

            // Update target status with actual crawled status if available
            const crawledStatus = urlStatusMap.get(link.target_url);
            if (crawledStatus) {
                link.target_status = crawledStatus;
            }

            uniqueLinks.push(link);
        }
    });

    // Store unfiltered links in crawlState
    crawlState.links = uniqueLinks;

    // Apply filters and update virtual scrollers
    applyLinksFilter();

    console.log(`Links loaded: ${crawlState.links.filter(l => l.is_internal).length} internal, ${crawlState.links.filter(l => !l.is_internal).length} external`);
}

function applyLinksFilter() {
    if (!crawlState.links || crawlState.links.length === 0) {
        if (virtualScrollers.internalLinks) virtualScrollers.internalLinks.setData([]);
        if (virtualScrollers.externalLinks) virtualScrollers.externalLinks.setData([]);
        return;
    }

    // Separate internal and external links
    let internalLinks = crawlState.links.filter(link => link.is_internal);
    let externalLinks = crawlState.links.filter(link => !link.is_internal);

    // Apply status code filter for internal links
    const internalStatusFilter = crawlState.filters.linksFilter.internalStatusCode;
    if (internalStatusFilter && internalStatusFilter !== 'all') {
        internalLinks = internalLinks.filter(link => {
            if (!link.target_status) return false;
            const status = parseInt(link.target_status);
            switch (internalStatusFilter) {
                case '2xx': return status >= 200 && status < 300;
                case '3xx': return status >= 300 && status < 400;
                case '4xx': return status >= 400 && status < 500;
                case '5xx': return status >= 500;
                default: return true;
            }
        });
    }

    // Apply search filter for internal links
    const internalSearch = crawlState.filters.linksFilter.internalSearch.toLowerCase();
    if (internalSearch) {
        internalLinks = internalLinks.filter(link =>
            link.source_url.toLowerCase().includes(internalSearch) ||
            link.target_url.toLowerCase().includes(internalSearch) ||
            (link.anchor_text && link.anchor_text.toLowerCase().includes(internalSearch))
        );
    }

    // Apply status code filter for external links
    const externalStatusFilter = crawlState.filters.linksFilter.externalStatusCode;
    if (externalStatusFilter && externalStatusFilter !== 'all') {
        externalLinks = externalLinks.filter(link => {
            if (!link.target_status) return false;
            const status = parseInt(link.target_status);
            switch (externalStatusFilter) {
                case '2xx': return status >= 200 && status < 300;
                case '3xx': return status >= 300 && status < 400;
                case '4xx': return status >= 400 && status < 500;
                case '5xx': return status >= 500;
                default: return true;
            }
        });
    }

    // Apply search filter for external links
    const externalSearch = crawlState.filters.linksFilter.externalSearch.toLowerCase();
    if (externalSearch) {
        externalLinks = externalLinks.filter(link =>
            link.source_url.toLowerCase().includes(externalSearch) ||
            link.target_url.toLowerCase().includes(externalSearch) ||
            (link.target_domain && link.target_domain.toLowerCase().includes(externalSearch))
        );
    }

    // Update virtual scrollers with filtered data
    if (virtualScrollers.internalLinks) {
        virtualScrollers.internalLinks.setData(internalLinks);
    }

    if (virtualScrollers.externalLinks) {
        virtualScrollers.externalLinks.setData(externalLinks);
    }
}

function filterInternalLinks(filterType) {
    crawlState.filters.linksFilter.internalStatusCode = filterType;
    applyLinksFilter();
    loadActiveTabPage(true);
}

function filterExternalLinks(filterType) {
    crawlState.filters.linksFilter.externalStatusCode = filterType;
    applyLinksFilter();
    loadActiveTabPage(true);
}

function searchInternalLinks(searchText) {
    crawlState.filters.linksFilter.internalSearch = searchText;
    applyLinksFilter();
}

function searchExternalLinks(searchText) {
    crawlState.filters.linksFilter.externalSearch = searchText;
    applyLinksFilter();
}

function updateIssuesTable(issues) {
    if (!issues || !Array.isArray(issues)) {
        issues = [];
    }

    // Store issues globally for filtering
    window.currentIssues = issues;

    const emptyState = document.getElementById('issuesEmptyState');
    const issuesTable = document.getElementById('issuesTable');

    // Count by type. Prefer server aggregates for large crawls.
    const issueCounts = crawlState.analytics?.issue_type_counts || null;
    let errorCount = issueCounts ? (issueCounts.error || 0) : 0;
    let warningCount = issueCounts ? (issueCounts.warning || 0) : 0;
    let infoCount = issueCounts ? (issueCounts.info || 0) : 0;
    let totalIssues = issueCounts ? Object.values(issueCounts).reduce((sum, count) => sum + count, 0) : issues.length;

    if (!issueCounts) {
        issues.forEach(issue => {
            if (issue.type === 'error') errorCount++;
            else if (issue.type === 'warning') warningCount++;
            else if (issue.type === 'info') infoCount++;
        });
    }

    // Update filter counts
    document.getElementById('issues-all-count').textContent = `(${formatNumber(totalIssues)})`;
    document.getElementById('issues-error-count').textContent = `(${formatNumber(errorCount)})`;
    document.getElementById('issues-warning-count').textContent = `(${formatNumber(warningCount)})`;
    document.getElementById('issues-info-count').textContent = `(${formatNumber(infoCount)})`;

    // Show/hide empty state
    if (issues.length === 0) {
        if (emptyState) emptyState.style.display = 'block';
        if (issuesTable) issuesTable.style.display = 'none';
    } else {
        if (emptyState) emptyState.style.display = 'none';
        if (issuesTable) issuesTable.style.display = 'table';

        // Use virtual scroller for issues
        if (virtualScrollers.issues) {
            virtualScrollers.issues.setData(issues);
        }
    }

    // Update issue count in tab button (find the button, not the tab content)
    const issuesTabButton = Array.from(document.querySelectorAll('.tab-btn')).find(btn => btn.textContent.includes('Incidencias'));
    if (issuesTabButton) {
        if (totalIssues > 0) {
            let badgeColor = '#3b82f6';
            if (errorCount > 0) badgeColor = '#ef4444';
            else if (warningCount > 0) badgeColor = '#f59e0b';

            issuesTabButton.innerHTML = `Incidencias <span style="background: ${badgeColor}; color: white; padding: 2px 6px; border-radius: 12px; font-size: 12px;">${formatNumber(totalIssues)}</span>`;
        } else {
            issuesTabButton.innerHTML = 'Incidencias';
        }
    }
}

function clearAllTables() {
    // Clear virtual scrollers if they exist
    if (virtualScrollers.overview) {
        virtualScrollers.overview.clear();
    }
    if (virtualScrollers.internal) {
        virtualScrollers.internal.clear();
    }
    if (virtualScrollers.external) {
        virtualScrollers.external.clear();
    }
    if (virtualScrollers.internalLinks) {
        virtualScrollers.internalLinks.clear();
    }
    if (virtualScrollers.externalLinks) {
        virtualScrollers.externalLinks.clear();
    }
    if (virtualScrollers.issues) {
        virtualScrollers.issues.clear();
    }

    // Clear status codes table (not virtualized)
    const statusCodesBody = document.getElementById('statusCodesTableBody');
    if (statusCodesBody) statusCodesBody.innerHTML = '';

    crawlState.urls = [];
    crawlState.links = [];
    crawlState.issues = [];
    crawlState.analytics = null;

    console.log('All tables cleared');
}

function formatAnalyticsInfo(analytics) {
    const detected = [];
    if (analytics.gtag || analytics.ga4_id) detected.push('GA4');
    if (analytics.google_analytics) detected.push('GA');
    if (analytics.gtm_id) detected.push('GTM');
    if (analytics.facebook_pixel) detected.push('FB');
    if (analytics.hotjar) detected.push('HJ');
    if (analytics.mixpanel) detected.push('MP');

    return detected.length > 0 ? detected.join(', ') : '';
}

function addUrlToTable(urlData) {
    // Check if URL already exists to prevent duplicates
    const existingUrl = crawlState.urls.find(u => u.url === urlData.url);
    if (existingUrl) {
        return; // Skip duplicate
    }

    crawlState.urls.push(urlData);

    // Update virtual scrollers with new data
    if (virtualScrollers.overview) {
        virtualScrollers.overview.appendData([urlData]);
    }

    if (urlData.is_internal && virtualScrollers.internal) {
        virtualScrollers.internal.appendData([urlData]);
    } else if (!urlData.is_internal && virtualScrollers.external) {
        virtualScrollers.external.appendData([urlData]);
    }

    // Reapply current filter if one is active
    if (crawlState.filters.active) {
        applyFilter(crawlState.filters.active);
    }
}

function addRowToTable(tableBodyId, rowData) {
    const tbody = document.getElementById(tableBodyId);
    const row = tbody.insertRow();

    rowData.forEach(cellData => {
        const cell = row.insertCell();
        // Check if cellData contains HTML (specifically our button)
        if (typeof cellData === 'string' && cellData.includes('<button')) {
            cell.innerHTML = cellData;
        } else {
            cell.textContent = cellData;
        }
    });
}

// Tab Management
function switchTab(tabName, trigger = null) {
    // Remove active class from all tabs and panes
    document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
    document.querySelectorAll('.tab-pane').forEach(pane => pane.classList.remove('active'));

    // Add active class to selected tab and pane
    const tabButton = trigger || document.querySelector(`.tab-btn[onclick*="'${tabName}'"]`);
    if (tabButton) tabButton.classList.add('active');
    document.getElementById(tabName + '-tab').classList.add('active');

    // Load pending links data if switching to Links tab
    if (tabName === 'links' && crawlState.pendingLinks) {
        updateLinksTable(crawlState.pendingLinks);
        crawlState.pendingLinks = null; // Clear pending data
    }

    // Load pending issues data if switching to Issues tab
    if (tabName === 'issues' && crawlState.pendingIssues) {
        updateIssuesTable(crawlState.pendingIssues);
        crawlState.pendingIssues = null; // Clear pending data
    }

    // Initialize visualization if switching to Visualization tab
    if (tabName === 'visualization' && typeof initVisualization === 'function') {
        // Small delay to ensure the tab is visible before initializing
        setTimeout(() => {
            initVisualization();
        }, 100);
    }

    // Handle plugin tabs
    const pluginTab = document.getElementById(`${tabName}-tab`);
    if (pluginTab && pluginTab.classList.contains('plugin-tab')) {
        handlePluginTabSwitch(tabName);
    }

    renderDashboardDrilldownContext(tabName);
    if (tabName === 'overview') {
        if (window.CrawlOverviewDashboard) {
            window.CrawlOverviewDashboard.refresh();
            window.CrawlOverviewDashboard.renderLocal();
        }
    } else if (['internal', 'external', 'links', 'issues'].includes(tabName)) {
        loadActiveTabPage(true);
    }
}

function renderDashboardDrilldownContext(tabName) {
    const pane = document.getElementById(`${tabName}-tab`);
    if (!pane) return;
    pane.querySelector('.dashboard-drilldown-context')?.remove();
    const key = tabName === 'issues' ? 'issues' : tabName === 'links' ? 'links' : 'urls';
    const drilldown = crawlState.dashboardDrilldown[key];
    if (!drilldown) return;
    const label = tabName === 'issues' ? 'Incidencias filtradas desde Resumen' : tabName === 'links' ? 'Enlaces filtrados desde Resumen' : 'URLs filtradas desde Resumen';
    const context = document.createElement('div');
    context.className = 'dashboard-drilldown-context';
    context.innerHTML = `<span>${label}</span><button type="button" aria-label="Quitar filtro">Quitar filtro</button>`;
    context.querySelector('button').addEventListener('click', () => clearDashboardDrilldown(key));
    pane.prepend(context);
}

function clearDashboardDrilldown(key) {
    crawlState.dashboardDrilldown[key] = null;
    const activeTab = getActiveTabName();
    renderDashboardDrilldownContext(activeTab);
    loadActiveTabPage(true);
}

window.openDashboardUrlDrilldown = function(filters) {
    crawlState.dashboardDrilldown.urls = filters;
    crawlState.filters.active = null;
    switchTab('internal');
};

window.openDashboardIssueDrilldown = function(filters) {
    crawlState.dashboardDrilldown.issues = filters;
    crawlState.filters.issueFilter = 'all';
    switchTab('issues');
};

window.openDashboardLinkDrilldown = function(filters) {
    crawlState.dashboardDrilldown.links = filters;
    switchTab('links');
};

// Handle plugin tab activation
function handlePluginTabSwitch(tabName) {
    if (!window.MitmoreSEOCrawlPlugin || !window.MitmoreSEOCrawlPlugin.loader) {
        return;
    }

    const loader = window.MitmoreSEOCrawlPlugin.loader;

    // Deactivate previously active plugin
    if (loader.activePluginId && loader.activePluginId !== tabName) {
        loader.deactivatePlugin(loader.activePluginId);
    }

    // Activate the new plugin
    loader.activatePlugin(tabName, {
        urls: crawlState.urls,
        links: crawlState.links,
        issues: crawlState.issues,
        stats: crawlState.stats
    });
}

// Issue Filtering
function filterIssues(filterType) {
    // Store the active filter
    crawlState.filters.issueFilter = filterType;

    // Update active button state and colors
    document.querySelectorAll('#issues-tab .filter-item').forEach(btn => {
        btn.classList.remove('active');
        const filter = btn.getAttribute('data-filter');

        if (filter === filterType) {
            btn.classList.add('active');
            // Set active state colors
            if (filter === 'all') {
                btn.style.background = '#374151';
                btn.style.borderColor = '#4b5563';
                btn.style.color = 'white';
            } else if (filter === 'error') {
                btn.style.background = 'rgba(239, 68, 68, 0.2)';
                btn.style.borderColor = 'rgba(239, 68, 68, 0.5)';
            } else if (filter === 'warning') {
                btn.style.background = 'rgba(245, 158, 11, 0.2)';
                btn.style.borderColor = 'rgba(245, 158, 11, 0.5)';
            } else if (filter === 'info') {
                btn.style.background = 'rgba(59, 130, 246, 0.2)';
                btn.style.borderColor = 'rgba(59, 130, 246, 0.5)';
            }
        } else {
            // Reset inactive state colors
            if (filter === 'all') {
                btn.style.background = 'transparent';
                btn.style.borderColor = '#4b5563';
                btn.style.color = '#9ca3af';
            } else if (filter === 'error') {
                btn.style.background = 'rgba(239, 68, 68, 0.1)';
                btn.style.borderColor = 'rgba(239, 68, 68, 0.3)';
            } else if (filter === 'warning') {
                btn.style.background = 'rgba(245, 158, 11, 0.1)';
                btn.style.borderColor = 'rgba(245, 158, 11, 0.3)';
            } else if (filter === 'info') {
                btn.style.background = 'rgba(59, 130, 246, 0.1)';
                btn.style.borderColor = 'rgba(59, 130, 246, 0.3)';
            }
        }
    });

    applyIssueFilterToScroller();
    loadActiveTabPage(true);
}

function applyIssueFilterToScroller() {
    const filterType = crawlState.filters.issueFilter;

    // Filter issues data and update virtual scroller
    if (window.currentIssues && virtualScrollers.issues) {
        let filteredIssues = window.currentIssues;

        if (filterType !== 'all') {
            filteredIssues = window.currentIssues.filter(issue => issue.type === filterType);
        }

        virtualScrollers.issues.setData(filteredIssues);
    }
}

// Filter Management
function toggleFilter(filterType) {
    const filterItems = document.querySelectorAll('.filter-item');
    filterItems.forEach(item => item.classList.remove('active'));

    event.currentTarget.classList.add('active');
    crawlState.filters.active = filterType;

    // Apply filter to tables
    applyFilter(filterType);
    loadActiveTabPage(true);
}

function applyFilter(filterType) {
    // Set current filter as active
    crawlState.filters.active = filterType;

    // Filter the data arrays and update virtual scrollers
    filterVirtualScrollerData('overview', filterType);
    filterVirtualScrollerData('internal', filterType);
    filterVirtualScrollerData('external', filterType);

    // Update Status Codes table with filtered data
    updateStatusCodesTable(filterType);

    console.log('Applied filter:', filterType);
}

function clearActiveFilters() {
    crawlState.filters.active = null;

    // Reset all virtual scrollers to show full data
    if (virtualScrollers.overview) {
        virtualScrollers.overview.setData(crawlState.urls);
    }
    if (virtualScrollers.internal) {
        const internalUrls = crawlState.urls.filter(url => url.is_internal);
        virtualScrollers.internal.setData(internalUrls);
    }
    if (virtualScrollers.external) {
        const externalUrls = crawlState.urls.filter(url => !url.is_internal);
        virtualScrollers.external.setData(externalUrls);
    }

    // Reset Status Codes table to show all data
    updateStatusCodesTable();
}

function filterVirtualScrollerData(scrollerName, filterType) {
    const scroller = virtualScrollers[scrollerName];
    if (!scroller) return;

    let filteredData = crawlState.urls;

    // Apply base filter for internal/external tables
    if (scrollerName === 'internal') {
        filteredData = filteredData.filter(url => url.is_internal);
    } else if (scrollerName === 'external') {
        filteredData = filteredData.filter(url => !url.is_internal);
    }

    // Apply user-selected filter
    if (filterType) {
        filteredData = filteredData.filter(url => {
            switch (filterType) {
                case 'internal':
                    return isInternalURL(url.url);
                case 'external':
                    return !isInternalURL(url.url);
                case '2xx':
                    return url.status_code >= 200 && url.status_code < 300;
                case '3xx':
                    return url.status_code >= 300 && url.status_code < 400;
                case '4xx':
                    return url.status_code >= 400 && url.status_code < 500;
                case '5xx':
                    return url.status_code >= 500;
                case 'no_response':
                    return (url.status_code === 0 || url.status_code === null || url.status_code === undefined)
                        && url.error_type !== 'file_too_large';
                case 'html':
                    return (url.content_type || '').toLowerCase().includes('html');
                case 'css':
                    return (url.content_type || '').toLowerCase().includes('css');
                case 'js':
                    return (url.content_type || '').toLowerCase().includes('javascript');
                case 'images':
                    return (url.content_type || '').toLowerCase().includes('image');
                default:
                    return true;
            }
        });
    }

    scroller.setData(filteredData);
}

// Legacy function - kept for compatibility but no longer used
function filterTable(tableBodyId, filterType) {
    // This function is deprecated in favor of filterVirtualScrollerData
    // Kept for backwards compatibility only
}

function isInternalURL(url) {
    if (!url || !crawlState.baseUrl) return false;
    try {
        const urlObj = new URL(url);
        const baseObj = new URL(crawlState.baseUrl);

        // Normalize domains by removing www prefix for comparison
        const urlDomain = urlObj.hostname.replace('www.', '');
        const baseDomain = baseObj.hostname.replace('www.', '');

        return urlDomain === baseDomain;
    } catch (e) {
        return false;
    }
}

function isStatusCodeRange(statusText, min, max) {
    const status = parseInt(statusText);
    return status >= min && status <= max;
}

function isContentType(contentType, type) {
    if (!contentType) return false;
    return contentType.toLowerCase().includes(type.toLowerCase());
}

function updateFilterCounts() {
    if (crawlState.analytics && crawlState.analytics.counts) {
        const aggregateCounts = crawlState.analytics.counts;
        [
            'internal', 'external', '2xx', '3xx', '4xx', '5xx',
            'no_response', 'html', 'css', 'js', 'images'
        ].forEach(key => {
            const element = document.getElementById(key + '-count');
            if (element) {
                element.textContent = formatNumber(aggregateCounts[key]);
            }
        });
        return;
    }

    // Count URLs by type and update filter counts
    const counts = {
        internal: 0,
        external: 0,
        '2xx': 0,
        '3xx': 0,
        '4xx': 0,
        '5xx': 0,
        no_response: 0,
        html: 0,
        css: 0,
        js: 0,
        images: 0
    };

    crawlState.urls.forEach(url => {
        // Count by internal/external using corrected logic
        if (isInternalURL(url.url)) counts.internal++;
        else counts.external++;

        // Count by status code
        const statusCode = parseInt(url.status_code);
        if (statusCode >= 200 && statusCode < 300) counts['2xx']++;
        else if (statusCode >= 300 && statusCode < 400) counts['3xx']++;
        else if (statusCode >= 400 && statusCode < 500) counts['4xx']++;
        else if (statusCode >= 500) counts['5xx']++;
        else if ((url.status_code === 0 || isNaN(statusCode)) && url.error_type !== 'file_too_large') counts.no_response++;

        // Count by content type
        const contentType = url.content_type || '';
        if (contentType.includes('html')) counts.html++;
        else if (contentType.includes('css')) counts.css++;
        else if (contentType.includes('javascript')) counts.js++;
        else if (contentType.includes('image')) counts.images++;
    });

    // Update DOM
    Object.keys(counts).forEach(key => {
        const element = document.getElementById(key + '-count');
        if (element) {
            element.textContent = formatNumber(counts[key]);
        }
    });
}

function getAggregateStatusCounts(filterType) {
    if (!crawlState.analytics || !Array.isArray(crawlState.analytics.status_counts)) {
        return null;
    }

    const rows = crawlState.analytics.status_counts.filter(row => {
        const status = parseInt(row.status_code);
        if (!filterType) return true;
        if (filterType === '2xx') return status >= 200 && status < 300;
        if (filterType === '3xx') return status >= 300 && status < 400;
        if (filterType === '4xx') return status >= 400 && status < 500;
        if (filterType === '5xx') return status >= 500;
        if (filterType === 'no_response') return status === 0 && row.error_type !== 'file_too_large';
        return false;
    });

    if (filterType && !['2xx', '3xx', '4xx', '5xx', 'no_response'].includes(filterType)) {
        return null;
    }

    const counts = {};
    rows.forEach(row => {
        const status = parseInt(row.status_code);
        const key = status === 0 ? `0|${row.error_type || 'unknown'}` : String(status);
        counts[key] = (counts[key] || 0) + (parseInt(row.count) || 0);
    });
    return counts;
}

function updateStatusCodesTable(filterType = null) {
    const tbody = document.getElementById('statusCodesTableBody');
    if (!tbody) return;

    // Count status codes, respecting current filter
    let statusCounts = getAggregateStatusCounts(filterType);
    let filteredUrls = crawlState.urls;
    let totalUrls = 0;

    if (statusCounts) {
        totalUrls = Object.values(statusCounts).reduce((sum, count) => sum + count, 0);
    } else {
        statusCounts = {};

        // Apply filter if specified
        if (filterType === 'internal') {
            filteredUrls = crawlState.urls.filter(url => isInternalURL(url.url));
        } else if (filterType === 'external') {
            filteredUrls = crawlState.urls.filter(url => !isInternalURL(url.url));
        } else if (filterType === '2xx') {
            filteredUrls = crawlState.urls.filter(url => {
                const status = parseInt(url.status_code);
                return status >= 200 && status < 300;
            });
        } else if (filterType === '3xx') {
            filteredUrls = crawlState.urls.filter(url => {
                const status = parseInt(url.status_code);
                return status >= 300 && status < 400;
            });
        } else if (filterType === '4xx') {
            filteredUrls = crawlState.urls.filter(url => {
                const status = parseInt(url.status_code);
                return status >= 400 && status < 500;
            });
        } else if (filterType === '5xx') {
            filteredUrls = crawlState.urls.filter(url => {
                const status = parseInt(url.status_code);
                return status >= 500;
            });
        } else if (filterType === 'no_response') {
            filteredUrls = crawlState.urls.filter(url =>
                (url.status_code === 0 || url.status_code === null || url.status_code === undefined)
                && url.error_type !== 'file_too_large'
            );
        } else if (filterType === 'html') {
            filteredUrls = crawlState.urls.filter(url => (url.content_type || '').includes('html'));
        } else if (filterType === 'css') {
            filteredUrls = crawlState.urls.filter(url => (url.content_type || '').includes('css'));
        } else if (filterType === 'js') {
            filteredUrls = crawlState.urls.filter(url => (url.content_type || '').includes('javascript'));
        } else if (filterType === 'images') {
            filteredUrls = crawlState.urls.filter(url => (url.content_type || '').includes('image'));
        }

        totalUrls = filteredUrls.length;

        filteredUrls.forEach(url => {
            // Group status_code=0 rows by error_type so DNS / timeout / refused
            // show as distinct rows instead of collapsing into a single "0".
            const isZero = url.status_code === 0 || url.status_code === null || url.status_code === undefined;
            const key = isZero ? `0|${url.error_type || 'unknown'}` : String(url.status_code);
            statusCounts[key] = (statusCounts[key] || 0) + 1;
        });
    }

    // Clear existing rows
    tbody.innerHTML = '';

    // Sort: real HTTP codes first (numeric), then no-response variants
    const keys = Object.keys(statusCounts).sort((a, b) => {
        const aIsZero = a.startsWith('0|');
        const bIsZero = b.startsWith('0|');
        if (aIsZero && !bIsZero) return 1;
        if (!aIsZero && bIsZero) return -1;
        if (aIsZero && bIsZero) return a.localeCompare(b);
        return parseInt(a) - parseInt(b);
    });

    keys.forEach(key => {
        const count = statusCounts[key];
        const percentage = totalUrls > 0 ? ((count / totalUrls) * 100).toFixed(1) : 0;
        let displayCode, statusText;
        if (key.startsWith('0|')) {
            const errorType = key.slice(2);
            displayCode = '0';
            statusText = getStatusCodeText(0, errorType);
        } else {
            displayCode = key;
            statusText = getStatusCodeText(parseInt(key));
        }

        addRowToTable('statusCodesTableBody', [
            displayCode,
            statusText,
            formatNumber(count),
            percentage + '%'
        ]);
    });
}

function getStatusCodeText(statusCode, errorType) {
    if (statusCode >= 200 && statusCode < 300) {
        return 'Correcta';
    } else if (statusCode >= 300 && statusCode < 400) {
        return 'Redirección';
    } else if (statusCode >= 400 && statusCode < 500) {
        return 'Error de cliente';
    } else if (statusCode >= 500) {
        return 'Error de servidor';
    } else if (statusCode === 0) {
        switch (errorType) {
            case 'dns_not_found':       return 'DNS no encontrado';
            case 'connection_refused':  return 'Conexión rechazada';
            case 'timeout':             return 'Tiempo de espera agotado';
            case 'ssl_error':           return 'Error SSL/TLS';
            case 'connection_error':    return 'Error de conexión';
            case 'file_too_large':      return 'Omitido (archivo demasiado grande)';
            default:                    return 'Sin respuesta';
        }
    } else {
        return 'Desconocido';
    }
}

function resetStats() {
    crawlState.stats = {
        discovered: 0,
        crawled: 0,
        depth: 0,
        speed: 0
    };
    updateStatsDisplay();
}

// Utility Functions
function normalizeUrl(input) {
    // Remove any whitespace
    input = input.trim();

    // If it already has a protocol, return as-is
    if (input.match(/^https?:\/\//i)) {
        return input;
    }

    // If it looks like a domain or IP, add https://
    if (input.match(/^[a-zA-Z0-9][a-zA-Z0-9-]*[a-zA-Z0-9]*\.([a-zA-Z]{2,}|[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3})/) ||
        input.match(/^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}/) ||
        input.match(/^localhost(:[0-9]+)?$/i) ||
        input.match(/^[a-zA-Z0-9-]+\.(com|org|net|edu|gov|mil|int|co|io|dev|app|tech|info|biz|name|pro|museum|aero|coop|travel|jobs|mobi|tel|asia|cat|post|xxx|local|test)$/i)) {
        return 'https://' + input;
    }

    // If it doesn't match common patterns, try adding https:// anyway
    return 'https://' + input;
}

function isValidUrl(string) {
    try {
        const url = new URL(string);
        // Check if it has a valid protocol and hostname
        return (url.protocol === 'http:' || url.protocol === 'https:') && url.hostname.length > 0;
    } catch (_) {
        return false;
    }
}

// This is defined in settings.js - no need to redefine here

async function logout() {
    try {
        const response = await fetch('/api/logout', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            }
        });

        const data = await response.json();

        if (data.success) {
            // Redirect to login page
            window.location.href = '/login';
        } else {
            console.error('Logout failed:', data.message);
            // Still redirect even if logout fails
            window.location.href = '/login';
        }
    } catch (error) {
        console.error('Logout error:', error);
        // Redirect anyway
        window.location.href = '/login';
    }
}

async function loadUserInfo() {
    try {
        const response = await fetch('/api/user/info');
        const data = await response.json();

        if (data.success && data.user) {
            const user = data.user;
            const userInfoElement = document.getElementById('userInfo');

            if (user.tier === 'guest') {
                // Show crawls remaining for guests
                const remaining = user.crawls_remaining;
                userInfoElement.textContent = `Invitado (${remaining}/3 rastreos restantes)`;
                userInfoElement.style.color = remaining === 0 ? '#dc2626' : '#6b7280';
            } else {
                // Show username and tier for registered users
                userInfoElement.textContent = `${user.username} (${user.tier})`;
                userInfoElement.style.color = '#6b7280';
            }
        }
    } catch (error) {
        console.error('Error loading user info:', error);
    }
}

async function exportData(crawlIdOverride = null) {
    try {
        const exportCrawlId = crawlIdOverride || crawlState.currentCrawlId;

        // Get current settings to determine export format and fields
        const settingsResponse = await fetch('/api/get_settings');
        const settingsData = await settingsResponse.json();

        if (!settingsData.success) {
            showNotification('No se pudieron obtener los ajustes de exportación', 'error');
            return;
        }

        const settings = settingsData.settings;
        const exportFormat = settings.exportFormat || 'csv';
        const exportFields = settings.exportFields || ['url', 'status_code', 'title', 'meta_description', 'h1'];

        // Check if there's data to export
        let hasData = false;
        let exportUrls = [];
        let exportLinks = [];
        let exportIssues = [];

        if (exportCrawlId) {
            hasData = true;
        } else {
            // Always fetch from backend to ensure we have the latest data including links
            const status = await fetch('/api/crawl_status');
            const statusData = await status.json();

            if (statusData.urls && statusData.urls.length > 0) {
                hasData = true;
                exportUrls = statusData.urls;
                exportLinks = statusData.links || [];
                exportIssues = statusData.issues || [];
            } else if (crawlState.urls && crawlState.urls.length > 0) {
                // Fallback to local state if backend has no data (e.g., loaded crawl)
                hasData = true;
                exportUrls = crawlState.urls;
                // Get links and issues from stored state
                exportLinks = crawlState.links || [];
                exportIssues = crawlState.issues || window.currentIssues || [];
            }
        }

        if (!hasData) {
            showNotification('No hay datos de rastreo para exportar', 'error');
            return;
        }

        showNotification('Preparando exportación...', 'info');

        const exportPayload = {
            format: exportFormat,
            fields: exportFields
        };
        if (exportCrawlId) {
            exportPayload.crawlId = exportCrawlId;
        } else {
            exportPayload.localData = {
                urls: exportUrls,
                links: exportLinks,
                issues: exportIssues
            };
        }

        // Request export from backend
        const exportResponse = await fetch('/api/export_data', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(exportPayload)
        });

        const exportData = await exportResponse.json();

        if (!exportData.success) {
            showNotification(exportData.error || 'La exportación ha fallado', 'error');
            return;
        }

        if (Array.isArray(exportData.downloads) && exportData.downloads.length > 0) {
            exportData.downloads.forEach((download, index) => {
                setTimeout(() => {
                    const a = document.createElement('a');
                    a.style.display = 'none';
                    a.href = download.url;
                    a.target = '_blank';
                    a.rel = 'noopener';
                    document.body.appendChild(a);
                    a.click();
                    document.body.removeChild(a);
                }, index * 500);
            });
            const fileLabel = exportData.downloads.length === 1 ? 'archivo' : 'archivos';
            showNotification(`Descargando ${exportData.downloads.length} ${fileLabel}...`, 'success');
            return;
        }

        // Check if we have multiple files to download
        if (exportData.multiple_files && exportData.files) {
            // Download each file separately
            exportData.files.forEach((file, index) => {
                setTimeout(() => {
                    const blob = new Blob([file.content], { type: file.mimetype });
                    const url = window.URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.style.display = 'none';
                    a.href = url;
                    a.download = file.filename;
                    document.body.appendChild(a);
                    a.click();
                    window.URL.revokeObjectURL(url);
                    document.body.removeChild(a);
                }, index * 500); // Delay between downloads to avoid browser blocking
            });

            showNotification(`Exportando ${exportData.files.length} archivos...`, 'success');
        } else {
            // Single file download (original logic)
            const blob = new Blob([exportData.content], { type: exportData.mimetype });
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.style.display = 'none';
            a.href = url;
            a.download = exportData.filename;
            document.body.appendChild(a);
            a.click();
            window.URL.revokeObjectURL(url);
            document.body.removeChild(a);

            showNotification(`Exportación completada: ${exportData.filename}`, 'success');
        }

    } catch (error) {
        console.error('Export error:', error);
        showNotification('La exportación ha fallado', 'error');
    }
}

// Helper function to escape HTML for safe display
function escapeHtml(text) {
    if (!text) return text;
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function showUrlDetails(url) {
    // Find the URL data
    const urlData = crawlState.urls.find(u => u.url === url);
    if (!urlData) {
        showNotification('No se encontraron datos de la URL', 'error');
        return;
    }

    // Escape all user-controlled text fields to prevent HTML injection
    const safeUrl = escapeHtml(url);
    const safeTitle = escapeHtml(urlData.title) || 'N/A';
    const safeH1 = escapeHtml(urlData.h1) || 'N/A';
    const safeMetaDesc = escapeHtml(urlData.meta_description) || 'N/A';
    const safeLang = escapeHtml(urlData.lang) || 'N/A';
    const safeCharset = escapeHtml(urlData.charset) || 'N/A';
    const safeCanonical = escapeHtml(urlData.canonical_url) || 'N/A';
    const safeRobots = escapeHtml(urlData.robots) || 'N/A';
    const safeContentType = escapeHtml(urlData.content_type) || 'N/A';
    const safeGa4Id = escapeHtml(urlData.analytics?.ga4_id) || 'N/A';
    const safeGtmId = escapeHtml(urlData.analytics?.gtm_id) || 'N/A';

    // Create modal content
    const modalContent = `
        <div class="details-modal-overlay" onclick="closeUrlDetails()">
            <div class="details-modal" onclick="event.stopPropagation()">
                <div class="details-header">
                    <h3>Análisis completo de URL</h3>
                    <button class="close-btn" onclick="closeUrlDetails()">×</button>
                </div>
                <div class="details-content">
                    <div class="details-url">${safeUrl}</div>

                    <div class="details-sections">
                        <div class="details-section">
                            <h4>🔍 SEO básico</h4>
                            <div class="details-grid">
                                <div><strong>Título:</strong> ${safeTitle}</div>
                                <div><strong>H1:</strong> ${safeH1}</div>
                                <div><strong>Meta Description:</strong> ${safeMetaDesc}</div>
                                <div><strong>Palabras:</strong> ${urlData.word_count || 0}</div>
                                <div><strong>Idioma:</strong> ${safeLang}</div>
                                <div><strong>Charset:</strong> ${safeCharset}</div>
                                <div><strong>Canonical URL:</strong> ${safeCanonical}</div>
                                <div><strong>Robots Meta:</strong> ${safeRobots}</div>
                            </div>
                        </div>

                        <div class="details-section">
                            <h4>📊 Analytics y seguimiento</h4>
                            <div class="details-grid">
                                <div><strong>Google Analytics:</strong> ${urlData.analytics?.google_analytics ? '✅ Sí' : '❌ No'}</div>
                                <div><strong>GA4/Gtag:</strong> ${urlData.analytics?.gtag ? '✅ Sí' : '❌ No'}</div>
                                <div><strong>GA4 ID:</strong> ${safeGa4Id}</div>
                                <div><strong>GTM ID:</strong> ${safeGtmId}</div>
                                <div><strong>Facebook Pixel:</strong> ${urlData.analytics?.facebook_pixel ? '✅ Sí' : '❌ No'}</div>
                                <div><strong>Hotjar:</strong> ${urlData.analytics?.hotjar ? '✅ Sí' : '❌ No'}</div>
                                <div><strong>Mixpanel:</strong> ${urlData.analytics?.mixpanel ? '✅ Sí' : '❌ No'}</div>
                            </div>
                        </div>

                        <div class="details-section">
                            <h4>📱 Redes sociales</h4>
                            <div class="details-grid">
                                <div><strong>Etiquetas OpenGraph:</strong> ${Object.keys(urlData.og_tags || {}).length} encontradas</div>
                                <div><strong>Twitter Cards:</strong> ${Object.keys(urlData.twitter_tags || {}).length} encontradas</div>
                            </div>
                            ${Object.keys(urlData.og_tags || {}).length > 0 ? `
                                <div class="details-subsection">
                                    <h5>Etiquetas OpenGraph:</h5>
                                    ${Object.entries(urlData.og_tags || {}).map(([key, value]) =>
                                        `<div><strong>og:${escapeHtml(key)}:</strong> ${escapeHtml(value)}</div>`
                                    ).join('')}
                                </div>
                            ` : ''}
                            ${Object.keys(urlData.twitter_tags || {}).length > 0 ? `
                                <div class="details-subsection">
                                    <h5>Twitter Cards:</h5>
                                    ${Object.entries(urlData.twitter_tags || {}).map(([key, value]) =>
                                        `<div><strong>twitter:${escapeHtml(key)}:</strong> ${escapeHtml(value)}</div>`
                                    ).join('')}
                                </div>
                            ` : ''}
                        </div>

                        <div class="details-section">
                            <h4>🔗 Enlaces y estructura</h4>
                            <div class="details-grid">
                                <div><strong>Enlaces internos:</strong> ${urlData.internal_links || 0}</div>
                                <div><strong>Enlaces externos:</strong> ${urlData.external_links || 0}</div>
                                <div><strong>Imágenes:</strong> ${(urlData.images || []).length}</div>
                                <div><strong>Etiquetas H2:</strong> ${(urlData.h2 || []).length}</div>
                                <div><strong>Etiquetas H3:</strong> ${(urlData.h3 || []).length}</div>
                            </div>
                        </div>

                        <div class="details-section">
                            <h4>⚡ Performance</h4>
                            <div class="details-grid">
                                <div><strong>Código de estado:</strong> ${urlData.status_code}${urlData.error_type ? ' (' + escapeHtml(getStatusCodeText(parseInt(urlData.status_code) || 0, urlData.error_type)) + ')' : ''}</div>
                                <div><strong>Tiempo de respuesta:</strong> ${urlData.response_time || 0}ms</div>
                                <div><strong>Tipo de contenido:</strong> ${safeContentType}</div>
                                <div><strong>Tamaño:</strong> ${urlData.size || 0} bytes</div>
                                ${urlData.error ? `<div><strong>Error:</strong> ${escapeHtml(urlData.error)}</div>` : ''}
                            </div>
                        </div>

                        ${(urlData.linked_from && urlData.linked_from.length > 0) ? `
                        <div class="details-section">
                            <h4>🔗 Enlazada desde</h4>
                            <div class="details-grid">
                                <div><strong>Encontrada en ${urlData.linked_from.length} página${urlData.linked_from.length !== 1 ? 's' : ''}:</strong></div>
                            </div>
                            <div class="details-subsection">
                                <ul style="list-style: none; padding: 0; margin: 10px 0;">
                                    ${urlData.linked_from.slice(0, 20).map(sourceUrl => {
                                        const escapedUrl = escapeHtml(sourceUrl);
                                        return `<li style="padding: 5px 0; word-break: break-all;"><a href="${escapedUrl}" target="_blank" style="color: #8b5cf6; text-decoration: none;">${escapedUrl}</a></li>`;
                                    }).join('')}
                                    ${urlData.linked_from.length > 20 ? `<li style="padding: 5px 0; font-style: italic; color: #9ca3af;">... y ${urlData.linked_from.length - 20} más</li>` : ''}
                                </ul>
                            </div>
                        </div>
                        ` : ''}

                        <div class="details-section">
                            <h4>🏗️ Datos estructurados</h4>
                            <div class="details-grid">
                                <div><strong>Scripts JSON-LD:</strong> ${(urlData.json_ld || []).length}</div>
                                <div><strong>Elementos Schema.org:</strong> ${(urlData.schema_org || []).length}</div>
                            </div>
                            ${(urlData.json_ld || []).length > 0 ? `
                                <div class="details-subsection">
                                    <h5>Datos JSON-LD:</h5>
                                    <pre class="json-preview">${escapeHtml(JSON.stringify(urlData.json_ld, null, 2))}</pre>
                                </div>
                            ` : ''}
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `;

    // Add modal to page
    document.body.insertAdjacentHTML('beforeend', modalContent);
}

function closeUrlDetails() {
    const modal = document.querySelector('.details-modal-overlay');
    if (modal) {
        modal.remove();
    }
}

function displayPageSpeedResults(results) {
    const container = document.getElementById('pagespeedResults');
    if (!container || !results || results.length === 0) {
        return;
    }

    container.innerHTML = '';

    results.forEach(pageResult => {
        const pageCard = document.createElement('div');
        pageCard.className = 'pagespeed-page-card';

        const mobile = pageResult.mobile || {};
        const desktop = pageResult.desktop || {};

        pageCard.innerHTML = `
            <div class="pagespeed-page-header">
                <h4 class="pagespeed-page-url">${pageResult.url}</h4>
                <span class="pagespeed-analysis-date">Analizado: ${pageResult.analysis_date}</span>
            </div>

            <div class="pagespeed-results-grid">
                <div class="pagespeed-device-result">
                    <h5>📱 Móvil</h5>
                    ${mobile.success ? `
                        <div class="pagespeed-score ${getScoreClass(mobile.performance_score)}">
                            ${mobile.performance_score || 'N/A'}
                        </div>
                        <div class="pagespeed-metrics">
                            <div class="metric">
                                <span class="metric-label">FCP:</span>
                                <span class="metric-value">${mobile.metrics?.first_contentful_paint || 'N/A'}s</span>
                            </div>
                            <div class="metric">
                                <span class="metric-label">LCP:</span>
                                <span class="metric-value">${mobile.metrics?.largest_contentful_paint || 'N/A'}s</span>
                            </div>
                            <div class="metric">
                                <span class="metric-label">CLS:</span>
                                <span class="metric-value">${mobile.metrics?.cumulative_layout_shift || 'N/A'}</span>
                            </div>
                            <div class="metric">
                                <span class="metric-label">SI:</span>
                                <span class="metric-value">${mobile.metrics?.speed_index || 'N/A'}s</span>
                            </div>
                            <div class="metric">
                                <span class="metric-label">TTI:</span>
                                <span class="metric-value">${mobile.metrics?.time_to_interactive || 'N/A'}s</span>
                            </div>
                        </div>
                    ` : `
                        <div class="pagespeed-error">
                            Error: ${mobile.error || 'El análisis ha fallado'}
                        </div>
                    `}
                </div>

                <div class="pagespeed-device-result">
                    <h5>🖥️ Escritorio</h5>
                    ${desktop.success ? `
                        <div class="pagespeed-score ${getScoreClass(desktop.performance_score)}">
                            ${desktop.performance_score || 'N/A'}
                        </div>
                        <div class="pagespeed-metrics">
                            <div class="metric">
                                <span class="metric-label">FCP:</span>
                                <span class="metric-value">${desktop.metrics?.first_contentful_paint || 'N/A'}s</span>
                            </div>
                            <div class="metric">
                                <span class="metric-label">LCP:</span>
                                <span class="metric-value">${desktop.metrics?.largest_contentful_paint || 'N/A'}s</span>
                            </div>
                            <div class="metric">
                                <span class="metric-label">CLS:</span>
                                <span class="metric-value">${desktop.metrics?.cumulative_layout_shift || 'N/A'}</span>
                            </div>
                            <div class="metric">
                                <span class="metric-label">SI:</span>
                                <span class="metric-value">${desktop.metrics?.speed_index || 'N/A'}s</span>
                            </div>
                            <div class="metric">
                                <span class="metric-label">TTI:</span>
                                <span class="metric-value">${desktop.metrics?.time_to_interactive || 'N/A'}s</span>
                            </div>
                        </div>
                    ` : `
                        <div class="pagespeed-error">
                            Error: ${desktop.error || 'El análisis ha fallado'}
                        </div>
                    `}
                </div>
            </div>
        `;

        container.appendChild(pageCard);
    });
}

function getScoreClass(score) {
    if (!score) return 'score-unknown';
    if (score >= 90) return 'score-good';
    if (score >= 50) return 'score-needs-improvement';
    return 'score-poor';
}

// Save/Load Crawl Functions
async function saveCrawl() {
    try {
        if (crawlState.stats.crawled === 0) {
            showNotification('No hay datos de rastreo para guardar', 'error');
            return;
        }

        // Get current crawl data from backend or use local state
        let urls = crawlState.urls;
        let links = crawlState.links;
        let issues = crawlState.issues;
        let stats = crawlState.stats;

        // Try to get fresh data from backend if available
        try {
            const status = await fetch('/api/crawl_status');
            const crawlData = await status.json();
            if (crawlData.urls && crawlData.urls.length > 0) {
                urls = crawlData.urls;
                links = crawlData.links || links;
                issues = crawlData.issues || issues;
                // Update stats to include latest PageSpeed results if available
                if (crawlData.stats) {
                    stats = crawlData.stats;
                }
            }
        } catch (e) {
            console.log('Using local state for save:', e);
        }

        // Add metadata
        const saveData = {
            timestamp: new Date().toISOString(),
            baseUrl: crawlState.baseUrl,
            stats: stats,
            urls: urls,
            links: links,
            issues: issues,
            version: '1.1'
        };

        // Create and download the file
        const blob = new Blob([JSON.stringify(saveData, null, 2)], { type: 'application/json' });
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.style.display = 'none';
        a.href = url;

        // Generate filename with domain and timestamp
        const domain = crawlState.baseUrl ? new URL(crawlState.baseUrl).hostname : 'crawl';
        const timestamp = new Date().toISOString().slice(0, 19).replace(/:/g, '-');
        a.download = `mitmore_seo_crawl_${domain}_${timestamp}.json`;

        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        document.body.removeChild(a);

        showNotification('Rastreo guardado correctamente', 'success');

    } catch (error) {
        console.error('Save error:', error);
        showNotification('No se pudo guardar el rastreo', 'error');
    }
}

function loadCrawl() {
    // Create file input
    const fileInput = document.createElement('input');
    fileInput.type = 'file';
    fileInput.accept = '.json';
    fileInput.style.display = 'none';

    fileInput.addEventListener('change', async function(event) {
        const file = event.target.files[0];
        if (!file) return;

        try {
            const text = await file.text();
            const saveData = JSON.parse(text);

            // Validate save data
            if (!saveData.version || !saveData.urls || !saveData.stats) {
                showNotification('Formato de archivo de rastreo no válido', 'error');
                return;
            }

            // Clear current data
            clearAllTables();
            resetStats();
            // Imported JSON is self-contained and must not poll the previously selected server crawl.
            crawlState.currentCrawlId = null;
            crawlState.isRunning = false;
            crawlState.isPaused = false;
            crawlState.dashboardDrilldown = { urls: null, links: null, issues: null };
            resetServerPageState();

            // Load the data
            crawlState.baseUrl = saveData.baseUrl;
            crawlState.stats = saveData.stats;
            crawlState.urls = [];
            crawlState.links = saveData.links || [];
            crawlState.issues = saveData.issues || [];

            // Update UI
            document.getElementById('urlInput').value = saveData.baseUrl || '';
            updateStatsDisplay();

            // Populate tables with loaded data
            if (saveData.urls && saveData.urls.length > 0) {
                console.log(`Loading ${saveData.urls.length} URLs...`);

                // Clear crawlState.urls first to avoid duplicate check issues
                crawlState.urls = [];

                // Add URLs to tables (addUrlToTable will handle adding to crawlState.urls)
                saveData.urls.forEach(url => {
                    // Debug: check if url has is_internal flag
                    if (url.is_internal === undefined) {
                        console.warn('URL missing is_internal flag:', url.url);
                        // Try to determine is_internal based on domain
                        if (crawlState.baseUrl) {
                            try {
                                const urlDomain = new URL(url.url).hostname.replace('www.', '');
                                const baseDomain = new URL(crawlState.baseUrl).hostname.replace('www.', '');
                                url.is_internal = urlDomain === baseDomain;
                            } catch (e) {
                                url.is_internal = false;
                            }
                        }
                    }
                    addUrlToTable(url);
                });

                console.log(`Added ${crawlState.urls.length} URLs to state`);
                console.log('Sample URL data:', crawlState.urls[0]);
            }

            // Load links data
            if (saveData.links && saveData.links.length > 0) {
                console.log(`Loading ${saveData.links.length} links...`);
                crawlState.pendingLinks = saveData.links;
                // If Links tab is currently active, load them immediately
                if (isLinksTabActive()) {
                    updateLinksTable(saveData.links);
                }
            }

            // Load issues data if present - filter them based on current exclusion settings
            if (saveData.issues && saveData.issues.length > 0) {
                console.log(`Loading ${saveData.issues.length} issues...`);

                // Filter issues using current exclusion patterns
                try {
                    const filterResponse = await fetch('/api/filter_issues', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ issues: saveData.issues })
                    });
                    const filterData = await filterResponse.json();

                    const filteredIssues = filterData.success ? filterData.issues : saveData.issues;
                    console.log(`Filtered to ${filteredIssues.length} issues after exclusions`);

                    crawlState.issues = filteredIssues;
                    crawlState.pendingIssues = filteredIssues;

                    // If Issues tab is currently active, load them immediately
                    if (isIssuesTabActive()) {
                        updateIssuesTable(filteredIssues);
                    } else {
                        // Update the badge count even if tab is not active
                        const issuesTabButton = Array.from(document.querySelectorAll('.tab-btn')).find(btn => btn.textContent.includes('Incidencias'));
                        if (issuesTabButton) {
                            const errorCount = filteredIssues.filter(i => i.type === 'error').length;
                            const warningCount = filteredIssues.filter(i => i.type === 'warning').length;
                            let badgeColor = '#3b82f6';
                            if (errorCount > 0) badgeColor = '#ef4444';
                            else if (warningCount > 0) badgeColor = '#f59e0b';
                            issuesTabButton.innerHTML = `Incidencias <span style="background: ${badgeColor}; color: white; padding: 2px 6px; border-radius: 12px; font-size: 12px;">${formatNumber(filteredIssues.length)}</span>`;
                        }
                    }
                } catch (error) {
                    console.error('Failed to filter issues:', error);
                    // Fall back to unfiltered issues if filtering fails
                    crawlState.issues = saveData.issues;
                    crawlState.pendingIssues = saveData.issues;
                    if (isIssuesTabActive()) {
                        updateIssuesTable(saveData.issues);
                    }
                }
            }

            // Update all secondary data
            updateFilterCounts();
            updateStatusCodesTable();
            updateCrawlButtons();

            // Display PageSpeed results if available
            if (saveData.stats && saveData.stats.pagespeed_results) {
                console.log(`Loading ${saveData.stats.pagespeed_results.length} PageSpeed results...`);
                displayPageSpeedResults(saveData.stats.pagespeed_results);
            }

            // Force refresh of all tables
            setTimeout(() => {
                console.log('Force refreshing tables...');
                const overviewCount = document.getElementById('overviewTableBody').children.length;
                const internalCount = document.getElementById('internalTableBody').children.length;
                const externalCount = document.getElementById('externalTableBody').children.length;
                console.log(`Table counts - Overview: ${overviewCount}, Internal: ${internalCount}, External: ${externalCount}`);
            }, 100);

            // Update visualization if it exists and has been initialized
            if (typeof window.updateVisualizationFromLoadedData === 'function') {
                window.updateVisualizationFromLoadedData(saveData.urls, saveData.links);
            }

            // Notify plugins of loaded data
            if (window.MitmoreSEOCrawlPlugin && window.MitmoreSEOCrawlPlugin.loader) {
                window.MitmoreSEOCrawlPlugin.loader.notifyDataUpdate({
                    urls: crawlState.urls,
                    links: crawlState.links,
                    issues: crawlState.issues,
                    stats: crawlState.stats
                });
            }

            showNotification(`Rastreo cargado: ${saveData.stats.crawled} URLs de ${new Date(saveData.timestamp).toLocaleDateString()}`, 'success');
            if (window.CrawlOverviewDashboard) window.CrawlOverviewDashboard.renderLocal();

        } catch (error) {
            console.error('Load error:', error);
            showNotification('No se pudo cargar el archivo de rastreo', 'error');
        }
    });

    // Trigger file selection
    document.body.appendChild(fileInput);
    fileInput.click();
    document.body.removeChild(fileInput);
}

// ========================================
// Virtual Scroller Render Functions
// ========================================

function renderOverviewRow(row, urlData, index) {
    const analyticsInfo = formatAnalyticsInfo(urlData.analytics || {});
    const ogTagsCount = Object.keys(urlData.og_tags || {}).length;
    const jsonLdCount = (urlData.json_ld || []).length;
    const linksInfo = `${urlData.internal_links || 0}/${urlData.external_links || 0}`;
    const imagesCount = (urlData.images || []).length;
    const jsRendered = urlData.javascript_rendered ? '✅ JS' : '';

    const cells = [
        urlData.url,
        urlData.status_code,
        urlData.title || '',
        (urlData.meta_description || '').substring(0, 50) + (urlData.meta_description && urlData.meta_description.length > 50 ? '...' : ''),
        urlData.h1 || '',
        urlData.word_count || 0,
        urlData.response_time || 0,
        analyticsInfo,
        ogTagsCount > 0 ? `${ogTagsCount} etiquetas` : '',
        jsonLdCount > 0 ? `${jsonLdCount} scripts` : '',
        linksInfo,
        imagesCount > 0 ? `${imagesCount} imágenes` : '',
        jsRendered,
        `<button class="details-btn" onclick="showUrlDetails('${urlData.url.replace(/'/g, "\\'")}')">📊 Detalles</button>`
    ];

    cells.forEach(cellData => {
        const cell = document.createElement('td');
        if (typeof cellData === 'string' && cellData.includes('<button')) {
            cell.innerHTML = cellData;
        } else {
            cell.textContent = cellData;
        }
        row.appendChild(cell);
    });
}

function renderInternalRow(row, urlData, index) {
    const cells = [
        urlData.url,
        urlData.status_code,
        urlData.content_type || '',
        urlData.size || 0,
        urlData.title || ''
    ];

    cells.forEach(cellData => {
        const cell = document.createElement('td');
        cell.textContent = cellData;
        row.appendChild(cell);
    });
}

function renderExternalRow(row, urlData, index) {
    const cells = [
        urlData.url,
        urlData.status_code,
        urlData.content_type || '',
        urlData.size || 0,
        urlData.title || ''
    ];

    cells.forEach(cellData => {
        const cell = document.createElement('td');
        cell.textContent = cellData;
        row.appendChild(cell);
    });
}

function renderInternalLinkRow(row, link, index) {
    const statusBadge = link.target_status ? `<span class="status-badge status-${Math.floor(link.target_status / 100)}xx">${link.target_status}</span>` : '';
    const placement = link.placement ? link.placement.charAt(0).toUpperCase() + link.placement.slice(1) : 'Desconocida';

    row.innerHTML = `
        <td style="word-break: break-all;">${link.source_url}</td>
        <td style="word-break: break-all;">${link.target_url}</td>
        <td>${statusBadge}</td>
        <td>${link.anchor_text || ''}</td>
        <td>${placement}</td>
    `;
}

function renderExternalLinkRow(row, link, index) {
    const statusBadge = link.target_status ? `<span class="status-badge status-${Math.floor(link.target_status / 100)}xx">${link.target_status}</span>` : '';
    const placement = link.placement ? link.placement.charAt(0).toUpperCase() + link.placement.slice(1) : 'Desconocida';

    row.innerHTML = `
        <td style="word-break: break-all;">${link.source_url}</td>
        <td style="word-break: break-all;">${link.target_url}</td>
        <td>${statusBadge}</td>
        <td>${link.target_domain || ''}</td>
        <td>${placement}</td>
    `;
}

function renderIssueRow(row, issue, index) {
    row.setAttribute('data-issue-type', issue.type);

    // Set row style based on issue type
    if (issue.type === 'error') {
        row.style.backgroundColor = 'rgba(239, 68, 68, 0.1)';
    } else if (issue.type === 'warning') {
        row.style.backgroundColor = 'rgba(245, 158, 11, 0.1)';
    } else {
        row.style.backgroundColor = 'rgba(59, 130, 246, 0.1)';
    }

    // Create type indicator
    let typeIcon = '';
    let typeColor = '';
    if (issue.type === 'error') {
        typeIcon = '❌';
        typeColor = '#ef4444';
    } else if (issue.type === 'warning') {
        typeIcon = '⚠️';
        typeColor = '#f59e0b';
    } else {
        typeIcon = 'ℹ️';
        typeColor = '#3b82f6';
    }

    const typeLabel = issue.type === 'error' ? 'Error' : issue.type === 'warning' ? 'Aviso' : 'Info';

    row.innerHTML = `
        <td style="word-break: break-all;" title="${issue.url}">${issue.url}</td>
        <td><span style="color: ${typeColor};">${typeIcon}</span> ${typeLabel}</td>
        <td>${issue.category}</td>
        <td>${issue.issue}</td>
        <td style="word-break: break-word;" title="${issue.details}">${issue.details}</td>
    `;
}
