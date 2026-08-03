// Settings Management
let currentSettings = {};
let defaultSettings = {
    // Crawler settings
    maxDepth: 3,
    maxUrls: 5000000,
    crawlDelay: 0,
    followRedirects: true,
    crawlExternalLinks: false,

    // Request settings
    userAgent: 'MitmoreSEOCrawl/1.0 (Web Crawler)',
    timeout: 10,
    retries: 1,
    acceptLanguage: 'en-US,en;q=0.9',
    respectRobotsTxt: true,
    allowCookies: true,
    discoverSitemaps: true,
    enablePageSpeed: false,
    googleApiKey: '',

    // Filter settings
    includeExtensions: 'html,htm,php,asp,aspx,jsp',
    excludeExtensions: 'pdf,doc,docx,zip,exe,dmg',
    includePatterns: '',
    excludePatterns: '',
    maxFileSize: 50,

    // Duplication detection settings
    enableDuplicationCheck: true,
    duplicationThreshold: 0.85,

    // Export settings
    exportFormat: 'csv',
    exportFields: ['url', 'status_code', 'title', 'meta_description', 'h1', 'word_count', 'response_time', 'analytics', 'og_tags', 'json_ld', 'internal_links', 'external_links', 'images'],

    // Advanced settings
    concurrency: 20,
    memoryLimit: 512,
    logLevel: 'INFO',
    saveSession: false,
    enableProxy: false,
    proxyUrl: '',
    customHeaders: '',

    // JavaScript rendering settings
    enableJavaScript: false,
    jsWaitTime: 3,
    jsTimeout: 30,
    jsBrowser: 'chromium',
    jsHeadless: true,
    jsUserAgent: 'MitmoreSEOCrawl/1.0 (Web Crawler with JavaScript)',
    jsViewportWidth: 1920,
    jsViewportHeight: 1080,
    jsMaxConcurrentPages: 3,

    // Custom CSS styling
    customCSS: '',

    // Issue exclusion patterns
    issueExclusionPatterns: `# WordPress admin & system paths
/wp-admin/*
/wp-content/plugins/*
/wp-content/themes/*
/wp-content/uploads/*
/wp-includes/*
/wp-login.php
/wp-cron.php
/xmlrpc.php
/wp-json/*
/wp-activate.php
/wp-signup.php
/wp-trackback.php

# Auth & user management pages
/login*
/signin*
/sign-in*
/log-in*
/auth/*
/authenticate/*
/register*
/signup*
/sign-up*
/registration/*
/logout*
/signout*
/sign-out*
/log-out*
/forgot-password*
/reset-password*
/password-reset*
/recover-password*
/change-password*
/account/password/*
/user/password/*
/activate/*
/verification/*
/verify/*
/confirm/*

# Admin panels & dashboards
/admin/*
/administrator/*
/_admin/*
/backend/*
/dashboard/*
/cpanel/*
/phpmyadmin/*
/pma/*
/webmail/*
/plesk/*
/control-panel/*
/manage/*
/manager/*

# E-commerce checkout & cart
/checkout/*
/cart/*
/basket/*
/payment/*
/billing/*
/order/*
/orders/*
/purchase/*

# User account pages
/account/*
/profile/*
/settings/*
/preferences/*
/my-account/*
/user/*
/member/*
/members/*

# CGI & server scripts
/cgi-bin/*
/cgi/*
/fcgi-bin/*

# Version control & config
/.git/*
/.svn/*
/.hg/*
/.bzr/*
/.cvs/*
/.env
/.env.*
/.htaccess
/.htpasswd
/web.config
/app.config
/composer.json
/package.json

# Development & build artifacts
/node_modules/*
/vendor/*
/bower_components/*
/jspm_packages/*
/includes/*
/lib/*
/libs/*
/src/*
/dist/*
/build/*
/builds/*
/_next/*
/.next/*
/out/*
/_nuxt/*
/.nuxt/*

# Testing & development
/test/*
/tests/*
/spec/*
/specs/*
/__tests__/*
/debug/*
/dev/*
/development/*
/staging/*

# API internal endpoints
/api/internal/*
/api/admin/*
/api/private/*

# System & internal
/private/*
/system/*
/core/*
/internal/*
/tmp/*
/temp/*
/cache/*
/logs/*
/log/*
/backup/*
/backups/*
/old/*
/archive/*
/archives/*
/config/*
/configs/*
/configuration/*

# Media upload forms
/upload/*
/uploads/*
/uploader/*
/file-upload/*

# Search & filtering (often noisy for SEO)
/search*
*/search/*
?s=*
?search=*
*/filter/*
?filter=*
*/sort/*
?sort=*

# Printer-friendly & special views
/print/*
?print=*
/preview/*
?preview=*
/embed/*
?embed=*
/amp/*
/amp

# Feed URLs
/feed/*
/feeds/*
/rss/*
*.rss
/atom/*
*.atom

# Common file types to exclude from issues
*.json
*.xml
*.yaml
*.yml
*.toml
*.ini
*.conf
*.log
*.txt
*.csv
*.sql
*.db
*.bak
*.backup
*.old
*.orig
*.tmp
*.swp
*.map
*.min.js
*.min.css`
};

let reportSettings = {};
let reportModels = [];
let currentSettingsTier = 'guest';
let reportSettingsLoadError = '';
const defaultReportSettings = {
    default_model: 'anthropic/claude-opus-4.7',
    manual_model: '',
    default_language: 'es-ES',
    default_report_mode: 'pack',
    default_commercial_context: 'prospect',
    issuer: { name: '', author: '', contact: '', confidentiality: '', cta: '' },
    appearance: { primary_color: '#1d4ed8', secondary_color: '#334155', accent_color: '#b45309', logo_asset_id: '' },
    capabilities: { reports_v2_enabled: false, quality_threshold: 90 },
    logo: null,
    has_openrouter_api_key: false,
    masked_openrouter_api_key: ''
};

// Initialize settings when page loads
document.addEventListener('DOMContentLoaded', function() {
    loadSettings();
    setupSettingsEventHandlers();
    applyCustomCSS();
});

function setupSettingsEventHandlers() {
    // Proxy checkbox handler
    const enableProxyCheckbox = document.getElementById('enableProxy');
    if (enableProxyCheckbox) {
        enableProxyCheckbox.addEventListener('change', function() {
            const proxySettings = document.getElementById('proxySettings');
            if (proxySettings) {
                proxySettings.style.display = this.checked ? 'block' : 'none';
            }
        });
    }

    // JavaScript checkbox handler
    const enableJavaScriptCheckbox = document.getElementById('enableJavaScript');
    if (enableJavaScriptCheckbox) {
        enableJavaScriptCheckbox.addEventListener('change', function() {
            const jsSettingsGroups = [
                'jsSettings', 'jsTimeoutGroup', 'jsBrowserGroup', 'jsHeadlessGroup',
                'jsUserAgentGroup', 'jsViewportGroup', 'jsConcurrencyGroup', 'jsWarning'
            ];

            jsSettingsGroups.forEach(groupId => {
                const group = document.getElementById(groupId);
                if (group) {
                    group.style.display = this.checked ? 'block' : 'none';
                }
            });
        });
    }
}

function resetIssueExclusions() {
    // Always use the hardcoded defaults, not current settings
    document.getElementById('issueExclusionPatterns').value = defaultSettings.issueExclusionPatterns;
    alert('Los patrones de exclusión de incidencias se han restablecido por defecto');
}

async function openSettings() {
    // Get user tier info
    let userTier = 'guest';
    try {
        const response = await fetch('/api/user/info');
        const data = await response.json();
        if (data.success) {
            userTier = data.user.tier;
            currentSettingsTier = userTier;
        }
    } catch (error) {
        console.error('Failed to get user tier:', error);
    }

    // Block guests from accessing settings
    if (userTier === 'guest') {
        alert('Los ajustes no están disponibles para usuarios invitados.\n\nRegístrate gratis para personalizar ajustes del rastreador, filtros y más.\n\nHaz clic en "Cerrar sesión" y luego en "Regístrate aquí" para crear una cuenta.');
        return;
    }

    // Hide tabs based on tier
    applyTierRestrictions(userTier);

    // Load current settings into form
    if (userTier === 'admin') {
        await loadReportSettings();
        await loadReportModels();
    }
    populateSettingsForm();
    if (userTier === 'admin') {
        populateReportSettingsForm();
    }

    // Show modal
    document.getElementById('settingsModal').style.display = 'flex';

    // Focus first input
    const firstInput = document.querySelector('.settings-tab-content.active input, .settings-tab-content.active select');
    if (firstInput) {
        setTimeout(() => firstInput.focus(), 100);
    }
}

function applyTierRestrictions(tier) {
    // Define which tabs each tier can see - MUST MATCH HTML TAB NAMES
    const tierTabs = {
        'guest': [],  // No settings tabs for guests
        'user': ['crawler', 'export', 'issues'],
        'extra': ['crawler', 'export', 'issues', 'filters', 'requests', 'customcss', 'javascript'],
        'admin': ['crawler', 'requests', 'filters', 'export', 'javascript', 'issues', 'customcss', 'reports', 'advanced']
    };

    const allowedTabs = tierTabs[tier] || [];

    // Hide/show tab buttons based on tier
    const allTabButtons = document.querySelectorAll('.settings-tab-btn');
    allTabButtons.forEach(btn => {
        const tabName = btn.getAttribute('onclick').match(/switchSettingsTab\('(.+?)'\)/)[1];
        if (allowedTabs.includes(tabName)) {
            btn.style.display = 'inline-block';
        } else {
            btn.style.display = 'none';
        }
    });

    // If current active tab is not allowed, switch to first allowed tab
    const activeTab = document.querySelector('.settings-tab-btn.active');
    if (activeTab && activeTab.style.display === 'none' && allowedTabs.length > 0) {
        // Click the first visible tab
        const firstVisibleTab = document.querySelector('.settings-tab-btn[style*="inline-block"]');
        if (firstVisibleTab) {
            firstVisibleTab.click();
        }
    }

    // Show message for guests
    if (tier === 'guest') {
        const settingsContent = document.querySelector('.settings-tabs');
        if (settingsContent) {
            const message = document.createElement('div');
            message.style.cssText = 'padding: 40px; text-align: center; color: #9ca3af; font-size: 16px;';
            message.innerHTML = `
                <h3 style="color: #f3f4f6; margin-bottom: 16px;">Acceso a ajustes restringido</h3>
                <p>Las cuentas de invitado no pueden modificar ajustes.</p>
                <p style="margin-top: 8px; font-size: 14px;">Mejora tu cuenta para acceder a los ajustes.</p>
            `;
            settingsContent.innerHTML = '';
            settingsContent.appendChild(message);
        }
    }
}

function closeSettings() {
    document.getElementById('settingsModal').style.display = 'none';
}

function switchSettingsTab(tabName) {
    // Remove active class from all tabs and content
    document.querySelectorAll('.settings-tab-btn').forEach(btn => {
        btn.classList.remove('active');
    });
    document.querySelectorAll('.settings-tab-content').forEach(content => {
        content.classList.remove('active');
    });

    // Add active class to selected tab and content
    event.target.classList.add('active');
    document.getElementById(tabName + '-settings').classList.add('active');
}

function populateSettingsForm() {
    // Populate all form fields with current settings
    Object.keys(currentSettings).forEach(key => {
        const element = document.getElementById(key);
        if (element) {
            if (element.type === 'checkbox') {
                element.checked = currentSettings[key];
            } else {
                element.value = currentSettings[key];
            }
        }
    });

    // Handle export fields checkboxes
    const exportFieldsCheckboxes = document.querySelectorAll('input[name="exportFields"]');
    exportFieldsCheckboxes.forEach(checkbox => {
        checkbox.checked = currentSettings.exportFields.includes(checkbox.value);
    });

    // Show/hide proxy settings
    const enableProxy = currentSettings.enableProxy;
    const proxySettings = document.getElementById('proxySettings');
    if (proxySettings) {
        proxySettings.style.display = enableProxy ? 'block' : 'none';
    }

    // Show/hide JavaScript settings
    const enableJavaScript = currentSettings.enableJavaScript;
    const jsSettingsGroups = [
        'jsSettings', 'jsTimeoutGroup', 'jsBrowserGroup', 'jsHeadlessGroup',
        'jsUserAgentGroup', 'jsViewportGroup', 'jsConcurrencyGroup', 'jsWarning'
    ];

    jsSettingsGroups.forEach(groupId => {
        const group = document.getElementById(groupId);
        if (group) {
            group.style.display = enableJavaScript ? 'block' : 'none';
        }
    });
}

function collectSettingsFromForm() {
    const settings = {};

    // Collect regular form fields
    const formFields = [
        'maxDepth', 'maxUrls', 'crawlDelay', 'followRedirects', 'crawlExternalLinks',
        'userAgent', 'timeout', 'retries', 'acceptLanguage', 'respectRobotsTxt', 'allowCookies', 'discoverSitemaps', 'enablePageSpeed', 'googleApiKey',
        'includeExtensions', 'excludeExtensions', 'includePatterns', 'excludePatterns', 'maxFileSize',
        'enableDuplicationCheck', 'duplicationThreshold',
        'exportFormat', 'concurrency', 'memoryLimit', 'logLevel', 'saveSession',
        'enableProxy', 'proxyUrl', 'customHeaders',
        'enableJavaScript', 'jsWaitTime', 'jsTimeout', 'jsBrowser', 'jsHeadless', 'jsUserAgent', 'jsViewportWidth', 'jsViewportHeight', 'jsMaxConcurrentPages',
        'customCSS', 'issueExclusionPatterns'
    ];

    formFields.forEach(fieldId => {
        const element = document.getElementById(fieldId);
        if (element) {
            if (element.type === 'checkbox') {
                settings[fieldId] = element.checked;
            } else if (element.type === 'number') {
                settings[fieldId] = parseFloat(element.value) || 0;
            } else {
                settings[fieldId] = element.value;
            }
        }
    });

    // Collect export fields
    const exportFieldsCheckboxes = document.querySelectorAll('input[name="exportFields"]:checked');
    settings.exportFields = Array.from(exportFieldsCheckboxes).map(cb => cb.value);

    return settings;
}

async function saveSettings() {
    // Collect settings from form
    const newSettings = collectSettingsFromForm();

    // Validate settings
    const validation = validateSettings(newSettings);
    if (!validation.valid) {
        alert('La validación de ajustes ha fallado: ' + validation.errors.join(', '));
        return;
    }

    // Save to localStorage first (primary storage for persistence)
    try {
        localStorage.setItem('mitmore_seo_crawl_settings', JSON.stringify(newSettings));
        console.log('Settings saved to localStorage');
    } catch (error) {
        console.error('Failed to save to localStorage:', error);
        showNotification('Aviso: puede que los ajustes no se conserven', 'warning');
    }

    // Update current settings
    currentSettings = { ...newSettings };

    // Apply custom CSS immediately
    applyCustomCSS();

    const reportSaveOk = await saveReportSettings();

    // Close settings modal
    closeSettings();
    showNotification(reportSaveOk ? 'Ajustes guardados correctamente' : 'Ajustes del rastreador guardados; los ajustes de informes han fallado', reportSaveOk ? 'success' : 'warning');

    // Sync to backend for crawler configuration
    fetch('/api/save_settings', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify(newSettings)
    })
    .then(response => response.json())
    .then(data => {
        if (!data.success) {
            console.warn('Backend sync failed:', data.error);
        }

        // Update crawler with new settings if it's running
        if (window.crawlState && window.crawlState.isRunning) {
            updateCrawlerSettings();
        }
    })
    .catch(error => {
        console.error('Error syncing settings to backend:', error);
    });
}

function resetSettings() {
    if (confirm('¿Seguro que quieres restablecer todos los ajustes a sus valores por defecto?')) {
        currentSettings = { ...defaultSettings };

        // Clear localStorage
        try {
            localStorage.removeItem('mitmore_seo_crawl_settings');
            console.log('Settings cleared from localStorage');
        } catch (error) {
            console.error('Failed to clear localStorage:', error);
        }

        populateSettingsForm();
        applyCustomCSS(); // Remove any custom CSS
        showNotification('Ajustes restablecidos por defecto', 'info');

        // Sync reset to backend
        syncSettingsToBackend();
    }
}

function validateSettings(settings) {
    const errors = [];

    // Validate numeric ranges
    if (settings.maxDepth < 1 || settings.maxDepth > 10) {
        errors.push('La profundidad máxima debe estar entre 1 y 10');
    }

    if (settings.maxUrls < 1 || settings.maxUrls > 5000000) {
        errors.push('El máximo de URLs debe estar entre 1 y 5.000.000');
    }

    if (settings.crawlDelay < 0 || settings.crawlDelay > 60) {
        errors.push('La pausa de rastreo debe estar entre 0 y 60 segundos');
    }

    if (settings.timeout < 1 || settings.timeout > 120) {
        errors.push('El tiempo de espera debe estar entre 1 y 120 segundos');
    }

    if (settings.retries < 0 || settings.retries > 10) {
        errors.push('Los reintentos deben estar entre 0 y 10');
    }

    if (settings.maxFileSize < 1 || settings.maxFileSize > 1000) {
        errors.push('El tamaño máximo de archivo debe estar entre 1 y 1000 MB');
    }

    if (settings.concurrency < 1 || settings.concurrency > 50) {
        errors.push('La concurrencia debe estar entre 1 y 50');
    }

    if (settings.memoryLimit < 64 || settings.memoryLimit > 4096) {
        errors.push('El límite de memoria debe estar entre 64 y 4096 MB');
    }

    // Validate duplication detection settings
    if (settings.duplicationThreshold < 0 || settings.duplicationThreshold > 1) {
        errors.push('El umbral de duplicación debe estar entre 0,0 y 1,0');
    }

    // Validate JavaScript settings if enabled
    if (settings.enableJavaScript) {
        if (settings.jsWaitTime < 0 || settings.jsWaitTime > 30) {
            errors.push('La espera de JavaScript debe estar entre 0 y 30 segundos');
        }

        if (settings.jsTimeout < 5 || settings.jsTimeout > 120) {
            errors.push('El tiempo de espera de JavaScript debe estar entre 5 y 120 segundos');
        }

        if (settings.jsViewportWidth < 800 || settings.jsViewportWidth > 4000) {
            errors.push('El ancho del viewport de JavaScript debe estar entre 800 y 4000 píxeles');
        }

        if (settings.jsViewportHeight < 600 || settings.jsViewportHeight > 3000) {
            errors.push('El alto del viewport de JavaScript debe estar entre 600 y 3000 píxeles');
        }

        if (settings.jsMaxConcurrentPages < 1 || settings.jsMaxConcurrentPages > 10) {
            errors.push('Las páginas concurrentes de JavaScript deben estar entre 1 y 10');
        }

        if (!settings.jsUserAgent.trim()) {
            errors.push('El User Agent de JavaScript no puede estar vacío');
        }
    }

    // Validate proxy URL if proxy is enabled
    if (settings.enableProxy && settings.proxyUrl) {
        try {
            new URL(settings.proxyUrl);
        } catch (e) {
            errors.push('Formato de URL de proxy no válido');
        }
    }

    // Validate user agent
    if (!settings.userAgent.trim()) {
        errors.push('El User Agent no puede estar vacío');
    }

    // Validate export fields
    if (settings.exportFields.length === 0) {
        errors.push('Debe seleccionarse al menos un campo de exportación');
    }

    return {
        valid: errors.length === 0,
        errors: errors
    };
}

function loadSettings() {
    // Try to load from localStorage first (browser-specific persistence)
    try {
        const savedSettings = localStorage.getItem('mitmore_seo_crawl_settings');
        if (savedSettings) {
            const parsed = JSON.parse(savedSettings);
            currentSettings = { ...defaultSettings, ...parsed };
            console.log('Settings loaded from localStorage');

            // Apply custom CSS after loading settings
            applyCustomCSS();

            // Sync to backend for crawler configuration
            syncSettingsToBackend();
            return;
        }
    } catch (error) {
        console.warn('Failed to load settings from localStorage:', error);
    }

    // Fallback: Load from backend (legacy support)
    fetch('/api/get_settings')
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                currentSettings = { ...defaultSettings, ...data.settings };
                // Save to localStorage for future loads
                localStorage.setItem('mitmore_seo_crawl_settings', JSON.stringify(currentSettings));
                // Apply custom CSS after loading settings
                applyCustomCSS();
            } else {
                console.warn('Failed to load settings, using defaults');
                currentSettings = { ...defaultSettings };
            }
        })
        .catch(error => {
            console.error('Error loading settings:', error);
            currentSettings = { ...defaultSettings };
        });
}

function syncSettingsToBackend() {
    // Send settings to backend without waiting for response
    // This ensures crawler gets the right config
    fetch('/api/save_settings', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify(currentSettings)
    }).catch(error => {
        console.warn('Failed to sync settings to backend:', error);
    });
}

async function loadReportSettings() {
    try {
        const response = await fetch('/api/report-settings');
        const data = await response.json();
        if (!response.ok || !data.success) {
            throw new Error(data.error || 'No se pudieron cargar los ajustes de informes');
        }
        reportSettings = { ...defaultReportSettings, ...data.settings };
        reportSettingsLoadError = '';
        populateReportSettingsForm();
    } catch (error) {
        console.warn('Failed to load report settings:', error);
        reportSettingsLoadError = error.message || 'No se pudieron cargar los ajustes de informes';
        reportNotice(reportSettingsLoadError, 'error');
    }
    return reportSettings;
}

async function loadReportModels() {
    try {
        const response = await fetch('/api/report-models');
        const data = await response.json();
        if (data.success) {
            reportModels = data.models || [];
            populateReportModelSelect('reportModelSelect', reportSettings.default_model);
        }
    } catch (error) {
        console.warn('Failed to load report models:', error);
    }
    return reportModels;
}

function reportNotice(message, type = 'info') {
    if (typeof showNotification === 'function') {
        showNotification(message, type);
    } else {
        alert(message);
    }
}

function populateReportSettingsForm() {
    const keyInput = document.getElementById('reportOpenRouterApiKey');
    if (!keyInput) return;

    keyInput.value = '';
    keyInput.placeholder = reportSettingsLoadError
        ? 'No se pudo comprobar la clave guardada'
        : reportSettings.has_openrouter_api_key
        ? `Guardada: ${reportSettings.masked_openrouter_api_key}`
        : 'Pega la clave API de OpenRouter';

    const keyStatus = document.getElementById('reportOpenRouterKeyStatus');
    if (keyStatus) {
        keyStatus.textContent = reportSettingsLoadError
            ? 'No se pudieron cargar los ajustes. Inténtalo de nuevo.'
            : reportSettings.has_openrouter_api_key
            ? `Clave guardada: ${reportSettings.masked_openrouter_api_key}`
            : 'No hay clave guardada';
        keyStatus.classList.toggle('is-error', Boolean(reportSettingsLoadError));
    }

    populateReportModelSelect('reportModelSelect', reportSettings.default_model);
    setElementValue('reportManualModel', reportSettings.manual_model);
    setElementValue('reportDefaultLanguage', reportSettings.default_language || 'es-ES');
    setElementValue('reportDefaultMode', reportSettings.default_report_mode || 'pack');
    setElementValue('reportDefaultCommercialContext', reportSettings.default_commercial_context || 'prospect');
    const issuer = reportSettings.issuer || {};
    const appearance = reportSettings.appearance || {};
    setElementValue('reportIssuerName', issuer.name || '');
    setElementValue('reportDefaultAuthor', issuer.author || '');
    setElementValue('reportDefaultContact', issuer.contact || '');
    setElementValue('reportDefaultConfidentiality', issuer.confidentiality || '');
    setElementValue('reportDefaultCta', issuer.cta || '');
    setElementValue('reportPrimaryColor', appearance.primary_color || '#1d4ed8');
    setElementValue('reportSecondaryColor', appearance.secondary_color || '#334155');
    setElementValue('reportAccentColor', appearance.accent_color || '#b45309');
    const capability = document.getElementById('reportV2Capability');
    if (capability) capability.textContent = reportSettings.capabilities?.reports_v2_enabled ? 'V2 activa' : 'V2 pendiente de activación';
    renderReportLogo(reportSettings.logo);
}

function setElementValue(id, value) {
    const element = document.getElementById(id);
    if (element) {
        element.value = value;
    }
}

function collectReportSettingsFromForm() {
    return {
        openrouter_api_key: document.getElementById('reportOpenRouterApiKey')?.value || '',
        default_model: document.getElementById('reportModelSelect')?.value || reportSettings.default_model || 'anthropic/claude-opus-4.7',
        manual_model: document.getElementById('reportManualModel')?.value || '',
        default_language: document.getElementById('reportDefaultLanguage')?.value || 'es-ES',
        default_report_mode: document.getElementById('reportDefaultMode')?.value || 'pack',
        default_commercial_context: document.getElementById('reportDefaultCommercialContext')?.value || 'prospect',
        issuer: {
            name: document.getElementById('reportIssuerName')?.value || '',
            author: document.getElementById('reportDefaultAuthor')?.value || '',
            contact: document.getElementById('reportDefaultContact')?.value || '',
            confidentiality: document.getElementById('reportDefaultConfidentiality')?.value || '',
            cta: document.getElementById('reportDefaultCta')?.value || ''
        },
        appearance: {
            primary_color: document.getElementById('reportPrimaryColor')?.value || '#1d4ed8',
            secondary_color: document.getElementById('reportSecondaryColor')?.value || '#334155',
            accent_color: document.getElementById('reportAccentColor')?.value || '#b45309',
            logo_asset_id: reportSettings.appearance?.logo_asset_id || ''
        }
    };
}

function renderReportLogo(logo) {
    const preview = document.getElementById('reportLogoPreview');
    const image = preview?.querySelector('img');
    if (!preview || !image) return;
    if (!logo?.preview_url) {
        preview.hidden = true;
        image.removeAttribute('src');
        return;
    }
    image.src = logo.preview_url;
    preview.hidden = false;
    const status = document.getElementById('reportLogoStatus');
    if (status) status.textContent = logo.original_name || 'Logo guardado';
}

async function uploadReportLogo(input) {
    const file = input?.files?.[0];
    if (!file) return;
    const body = new FormData();
    body.append('file', file);
    try {
        const response = await fetch('/api/report-assets/logo', { method: 'POST', body });
        const data = await response.json();
        if (!data.success) throw new Error(data.error || 'No se pudo subir el logo');
        reportSettings.logo = data.asset;
        reportSettings.appearance = { ...(reportSettings.appearance || {}), logo_asset_id: data.asset.asset_id };
        renderReportLogo(data.asset);
        await saveReportSettings();
        reportNotice('Logo guardado', 'success');
    } catch (error) {
        reportNotice(error.message || 'No se pudo subir el logo', 'error');
        input.value = '';
    }
}

async function removeReportLogo() {
    const assetId = reportSettings.appearance?.logo_asset_id;
    if (!assetId) return;
    try {
        const response = await fetch(`/api/report-assets/logo/${encodeURIComponent(assetId)}`, { method: 'DELETE' });
        const data = await response.json();
        if (!data.success) throw new Error(data.error || 'No se pudo quitar el logo');
        reportSettings.appearance.logo_asset_id = '';
        reportSettings.logo = null;
        renderReportLogo(null);
        await saveReportSettings();
    } catch (error) {
        reportNotice(error.message || 'No se pudo quitar el logo', 'error');
    }
}

async function saveReportSettings() {
    if (currentSettingsTier !== 'admin') return true;
    if (!document.getElementById('reportOpenRouterApiKey')) return true;

    try {
        const response = await fetch('/api/report-settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(collectReportSettingsFromForm())
        });
        const data = await response.json();
        if (!data.success) {
            reportNotice('Los ajustes de informes han fallado: ' + (data.error || 'Error desconocido'), 'error');
            return false;
        }
        reportSettings = { ...defaultReportSettings, ...data.settings };
        populateReportSettingsForm();
        return true;
    } catch (error) {
        console.error('Error saving report settings:', error);
        reportNotice('Los ajustes de informes han fallado', 'error');
        return false;
    }
}

async function refreshReportModels() {
    const button = document.getElementById('reportRefreshModelsBtn');
    if (button) button.disabled = true;

    try {
        const openrouterApiKey = document.getElementById('reportOpenRouterApiKey')?.value || '';
        const response = await fetch('/api/report-models/refresh', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ openrouter_api_key: openrouterApiKey })
        });
        const data = await response.json();
        if (!data.success) {
            reportNotice('La actualización de modelos ha fallado: ' + (data.error || 'Error desconocido'), 'error');
            return;
        }
        reportModels = data.models || [];
        populateReportModelSelect('reportModelSelect', reportSettings.default_model);
        reportNotice('Modelos de informe actualizados', 'success');
    } catch (error) {
        console.error('Error refreshing report models:', error);
        reportNotice('La actualización de modelos ha fallado', 'error');
    } finally {
        if (button) button.disabled = false;
    }
}

function populateReportModelSelect(selectId, selectedModel, includeSavedDefault = false) {
    const select = document.getElementById(selectId);
    if (!select) return;

    select.innerHTML = '';
    const emptyOption = document.createElement('option');
    emptyOption.value = '';
    emptyOption.textContent = includeSavedDefault ? 'Usar valor guardado' : 'Selecciona un modelo';
    select.appendChild(emptyOption);

    const groups = {};
    reportModels.forEach(model => {
        const provider = model.provider || (model.id || '').split('/')[0] || 'Otros';
        if (!groups[provider]) {
            groups[provider] = document.createElement('optgroup');
            groups[provider].label = provider.charAt(0).toUpperCase() + provider.slice(1);
            select.appendChild(groups[provider]);
        }

        const option = document.createElement('option');
        option.value = model.id;
        option.textContent = model.name ? `${model.name} (${model.id})` : model.id;
        groups[provider].appendChild(option);
    });

    if (selectedModel && !Array.from(select.options).some(option => option.value === selectedModel)) {
        const savedOption = document.createElement('option');
        savedOption.value = selectedModel;
        savedOption.textContent = `${selectedModel} (guardado)`;
        select.appendChild(savedOption);
    }

    if (selectedModel) {
        select.value = selectedModel;
    }
}

function updateCrawlerSettings() {
    // Send updated settings to crawler
    fetch('/api/update_crawler_settings', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify(currentSettings)
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            console.log('Crawler settings updated');
        } else {
            console.warn('Failed to update crawler settings:', data.error);
        }
    })
    .catch(error => {
        console.error('Error updating crawler settings:', error);
    });
}

function exportSettings() {
    // Create downloadable settings file
    const settingsBlob = new Blob([JSON.stringify(currentSettings, null, 2)], {
        type: 'application/json'
    });

    const url = URL.createObjectURL(settingsBlob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'mitmore-seo-crawl-settings.json';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

function importSettings(event) {
    const file = event.target.files[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = function(e) {
        try {
            const importedSettings = JSON.parse(e.target.result);

            // Validate imported settings
            const validation = validateSettings(importedSettings);
            if (!validation.valid) {
                alert('Archivo de ajustes no válido: ' + validation.errors.join(', '));
                return;
            }

            // Merge with defaults to ensure all fields are present
            currentSettings = { ...defaultSettings, ...importedSettings };
            populateSettingsForm();
            showNotification('Ajustes importados correctamente', 'success');

        } catch (error) {
            alert('Formato de archivo de ajustes no válido');
        }
    };
    reader.readAsText(file);
}

function showNotification(message, type = 'info') {
    // Create notification element
    const notification = document.createElement('div');
    notification.className = `notification notification-${type}`;
    notification.textContent = message;

    // Style the notification
    notification.style.cssText = `
        position: fixed;
        top: 20px;
        right: 20px;
        padding: 12px 20px;
        border-radius: 6px;
        color: white;
        font-weight: 500;
        z-index: 1001;
        max-width: 300px;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
        transition: all 0.3s ease;
    `;

    // Set background color based on type
    switch (type) {
        case 'success':
            notification.style.background = 'linear-gradient(135deg, #10b981, #059669)';
            break;
        case 'error':
            notification.style.background = 'linear-gradient(135deg, #ef4444, #dc2626)';
            break;
        case 'warning':
            notification.style.background = 'linear-gradient(135deg, #f59e0b, #d97706)';
            break;
        default:
            notification.style.background = 'linear-gradient(135deg, #8b5cf6, #7c3aed)';
    }

    // Add to page
    document.body.appendChild(notification);

    // Remove after 3 seconds
    setTimeout(() => {
        notification.style.opacity = '0';
        notification.style.transform = 'translateX(100%)';
        setTimeout(() => {
            if (notification.parentNode) {
                notification.parentNode.removeChild(notification);
            }
        }, 300);
    }, 3000);
}

// Close modal when clicking outside
document.addEventListener('click', function(event) {
    const modal = document.getElementById('settingsModal');
    if (event.target === modal) {
        closeSettings();
    }
});

// Close modal with Escape key
document.addEventListener('keydown', function(event) {
    if (event.key === 'Escape') {
        const modal = document.getElementById('settingsModal');
        if (modal.style.display === 'flex') {
            closeSettings();
        }
    }
});

// Export current settings object for use by other modules
window.getCurrentSettings = function() {
    return currentSettings;
};

window.getReportSettings = function() {
    return reportSettings;
};

window.loadReportSettings = loadReportSettings;
window.loadReportModels = loadReportModels;
window.populateReportModelSelect = populateReportModelSelect;
window.uploadReportLogo = uploadReportLogo;
window.removeReportLogo = removeReportLogo;

// Apply custom CSS to the page
function applyCustomCSS() {
    // Remove existing custom CSS if present
    const existingStyle = document.getElementById('custom-user-styles');
    if (existingStyle) {
        existingStyle.remove();
    }

    // Get custom CSS from settings
    const customCSS = currentSettings.customCSS || '';

    // Only inject if there's CSS to apply
    if (customCSS.trim()) {
        const styleElement = document.createElement('style');
        styleElement.id = 'custom-user-styles';
        styleElement.textContent = customCSS;
        document.head.appendChild(styleElement);
        console.log('Custom CSS applied');
    }
}
