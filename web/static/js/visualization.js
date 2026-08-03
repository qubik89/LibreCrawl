/**
 * Site Structure Visualization
 * Uses Cytoscape.js for interactive graph visualization
 */

let cy = null;  // Cytoscape instance
let graphData = { nodes: [], edges: [], total_links: 0, visualized_links: 0 };  // Current graph data
let currentLayout = 'cose';  // Current layout algorithm
let currentFilter = 'all';  // Current filter

function setVisualizationState(title, message) {
    const container = document.getElementById('cy');
    if (!container) return;

    const placeholder = container.querySelector('.graph-placeholder');
    if (!placeholder) return;

    const heading = placeholder.querySelector('h3');
    const description = placeholder.querySelector('p');
    if (heading) heading.textContent = title;
    if (description) description.textContent = message;
    placeholder.style.display = 'block';
}

function hideVisualizationState() {
    const container = document.getElementById('cy');
    const placeholder = container && container.querySelector('.graph-placeholder');
    if (placeholder) placeholder.style.display = 'none';
}

/**
 * Initialize the visualization when tab is opened
 */
function initVisualization() {
    if (cy) {
        cy.resize();
        if (graphData.nodes.length === 0) loadVisualizationData();
        return; // Already initialized
    }

    const container = document.getElementById('cy');
    if (!container) {
        console.error('Graph container not found');
        return;
    }

    if (typeof cytoscape !== 'function') {
        setVisualizationState('No se pudo cargar el mapa', 'La librería de visualización no está disponible.');
        console.error('Cytoscape.js is not available');
        return;
    }

    setVisualizationState('Cargando mapa...', 'Obteniendo las relaciones del rastreo.');

    // Initialize Cytoscape
    cy = cytoscape({
        container: container,
        elements: [],
        style: [
            {
                selector: 'node',
                style: {
                    'background-color': 'data(color)',
                    'label': 'data(label)',
                    'width': 'data(size)',
                    'height': 'data(size)',
                    'font-size': '12px',
                    'color': '#e5e7eb',
                    'text-outline-color': '#1f2937',
                    'text-outline-width': 2,
                    'text-valign': 'bottom',
                    'text-halign': 'center',
                    'text-margin-y': 5,
                    'overlay-opacity': 0,
                    'border-width': 2,
                    'border-color': '#374151'
                }
            },
            {
                selector: 'node:selected',
                style: {
                    'border-color': '#8b5cf6',
                    'border-width': 3,
                    'overlay-opacity': 0
                }
            },
            {
                selector: 'edge',
                style: {
                    'width': 2,
                    'line-color': '#4b5563',
                    'target-arrow-color': '#4b5563',
                    'target-arrow-shape': 'triangle',
                    'curve-style': 'bezier',
                    'arrow-scale': 1,
                    'opacity': 0.6
                }
            },
            {
                selector: 'edge:selected',
                style: {
                    'line-color': '#8b5cf6',
                    'target-arrow-color': '#8b5cf6',
                    'width': 3,
                    'opacity': 1
                }
            }
        ],
        layout: {
            name: 'preset'  // Use preset (no auto-layout on init)
        },
        wheelSensitivity: 0.2,
        minZoom: 0.1,
        maxZoom: 3
    });
    cy.resize();

    // Add interaction handlers
    setupInteractions();

    // Use already-loaded data if available, otherwise fetch from backend
    if (graphData.nodes.length > 0) {
        updateGraph();
    } else {
        loadVisualizationData();
    }
}

/**
 * Setup mouse interactions and tooltips
 */
function setupInteractions() {
    if (!cy) return;

    let tooltip = null;

    // Create tooltip element
    function createTooltip() {
        if (!tooltip) {
            tooltip = document.createElement('div');
            tooltip.className = 'cy-tooltip';
            tooltip.style.display = 'none';
            document.body.appendChild(tooltip);
        }
        return tooltip;
    }

    // Show tooltip on hover
    cy.on('mouseover', 'node', function(event) {
        const node = event.target;
        const data = node.data();
        const tooltip = createTooltip();

        // Build tooltip content
        const statusClass = getStatusClass(data.status_code);
        tooltip.innerHTML = `
            <div class="tooltip-url">${truncateUrl(data.url)}</div>
            <div class="tooltip-info">
                <div><strong>Título:</strong> ${data.title || 'N/A'}</div>
                <div class="tooltip-status ${statusClass}">Estado: ${data.status_code}</div>
            </div>
        `;

        tooltip.style.display = 'block';
    });

    // Move tooltip with mouse
    cy.on('mousemove', 'node', function(event) {
        const tooltip = createTooltip();
        tooltip.style.left = (event.originalEvent.pageX + 10) + 'px';
        tooltip.style.top = (event.originalEvent.pageY + 10) + 'px';
    });

    // Hide tooltip when mouse leaves
    cy.on('mouseout', 'node', function() {
        const tooltip = createTooltip();
        tooltip.style.display = 'none';
    });

    // Double-click to open URL in new tab
    cy.on('dblclick', 'node', function(event) {
        const node = event.target;
        const url = node.data('url');
        if (url) {
            window.open(url, '_blank');
        }
    });

    // Click to highlight connected nodes
    cy.on('tap', 'node', function(event) {
        const node = event.target;

        // Reset all nodes and edges
        cy.elements().removeClass('highlighted').removeClass('dimmed');

        // Highlight the selected node and its neighbors
        const neighborhood = node.neighborhood().add(node);
        neighborhood.addClass('highlighted');

        // Dim everything else
        cy.elements().not(neighborhood).addClass('dimmed');
    });

    // Click on background to reset
    cy.on('tap', function(event) {
        if (event.target === cy) {
            cy.elements().removeClass('highlighted').removeClass('dimmed');
        }
    });
}

/**
 * Load visualization data from backend
 */
async function loadVisualizationData() {
    try {
        const response = await fetch('/api/visualization_data');
        let data;
        try {
            data = await response.json();
        } catch (parseError) {
            throw new Error(`Respuesta no válida del servidor (${response.status})`);
        }

        if (!response.ok || !data.success) {
            const message = data.error || `El servidor respondió con ${response.status}`;
            setVisualizationState('No se pudo cargar el mapa', message);
            console.error('Failed to load visualization data:', message);
            return;
        }

        graphData = {
            nodes: data.nodes || [],
            edges: data.edges || [],
            total_links: Number.isFinite(Number(data.total_links))
                ? Number(data.total_links)
                : (data.edges || []).length,
            visualized_links: Number.isFinite(Number(data.visualized_links))
                ? Number(data.visualized_links)
                : (data.edges || []).length,
        };

        if (graphData.nodes.length === 0) {
            if (cy) cy.elements().remove();
            setVisualizationState('Sin datos para visualizar', 'Inicia o carga un rastreo para ver sus relaciones.');
            return;
        }

        // Show warning if data was truncated
        if (data.truncated) {
            console.warn(`Showing ${data.visualized_pages} of ${data.total_pages} pages for performance`);
        }

        // Update the graph
        updateGraph();

    } catch (error) {
        setVisualizationState('No se pudo cargar el mapa', error.message || 'Error inesperado al obtener los datos.');
        console.error('Error loading visualization data:', error);
    }
}

/**
 * Update the graph with current data and filters
 */
function updateGraph() {
    if (!cy) {
        initVisualization();
        return;
    }

    // Filter data based on current filter
    let filteredNodes = graphData.nodes;
    let filteredEdges = graphData.edges;

    if (currentFilter !== 'all') {
        filteredNodes = graphData.nodes.filter(node => {
            const statusCode = node.data.status_code;

            switch (currentFilter) {
                case 'html':
                    // Only show HTML pages (typically 2xx with no file extension or .html)
                    const url = node.data.url;
                    return statusCode >= 200 && statusCode < 300 &&
                           (url.endsWith('/') || url.endsWith('.html') || url.endsWith('.htm') || !url.includes('.'));
                case '2xx':
                    return statusCode >= 200 && statusCode < 300;
                case '3xx':
                    return statusCode >= 300 && statusCode < 400;
                case '4xx':
                    return statusCode >= 400 && statusCode < 500;
                case '5xx':
                    return statusCode >= 500 && statusCode < 600;
                default:
                    return true;
            }
        });

        // Filter edges to only include edges where both nodes are present
        const nodeIds = new Set(filteredNodes.map(n => n.data.id));
        filteredEdges = graphData.edges.filter(edge =>
            nodeIds.has(edge.data.source) && nodeIds.has(edge.data.target)
        );
    }

    if (filteredNodes.length === 0) {
        cy.elements().remove();
        setVisualizationState('Sin resultados', 'No hay páginas que coincidan con este filtro.');
        return;
    }

    if (graphData.total_links === 0) {
        cy.elements().remove();
        setVisualizationState('Sin relaciones guardadas', 'Este rastreo no guardó enlaces internos para visualizar.');
        return;
    }

    // Update graph
    hideVisualizationState();
    cy.elements().remove();
    cy.add([...filteredNodes, ...filteredEdges]);

    // Apply layout
    applyLayout(currentLayout);
}

/**
 * Apply a layout algorithm to the graph
 */
function applyLayout(layoutName) {
    if (!cy) return;

    const layoutConfig = {
        name: layoutName,
        animate: 'end',  // Animate to end result, not during iterations
        animationDuration: 500,
        fit: true,
        padding: 50,
        boundingBox: undefined,
        avoidOverlap: true
    };

    // Add specific config for certain layouts
    if (layoutName === 'cose') {
        // More balanced values that won't cause numerical instability
        layoutConfig.nodeRepulsion = 400000;
        layoutConfig.nodeOverlap = 20;
        layoutConfig.idealEdgeLength = 100;
        layoutConfig.edgeElasticity = 100;
        layoutConfig.nestingFactor = 5;
        layoutConfig.gravity = 80;
        layoutConfig.numIter = 1000;
        layoutConfig.initialTemp = 200;
        layoutConfig.coolingFactor = 0.95;
        layoutConfig.minTemp = 1.0;
        layoutConfig.randomize = true;
        layoutConfig.componentSpacing = 200;      // Space between disconnected components
    } else if (layoutName === 'breadthfirst') {
        // Use crawl depth data for hierarchy instead of BFS graph distance,
        // since nav links often make every page 1 hop from root in the graph.
        // We use concentric layout with depth as the ranking to achieve this.
        layoutConfig.name = 'concentric';
        layoutConfig.minNodeSpacing = 50;
        layoutConfig.concentric = function(node) {
            // Higher value = closer to center; root (depth 0) should be center
            const maxDepth = cy.nodes().max(function(n) { return n.data('depth') || 0; }).value || 1;
            return maxDepth - (node.data('depth') || 0);
        };
        layoutConfig.levelWidth = function() {
            return 1;
        };
        layoutConfig.sweep = Math.PI * 2;
    } else if (layoutName === 'concentric') {
        layoutConfig.minNodeSpacing = 100;
        layoutConfig.concentric = function(node) {
            return node.degree();
        };
        layoutConfig.levelWidth = function() {
            return 2;
        };
    }

    const layout = cy.layout(layoutConfig);
    layout.run();
}

/**
 * Change the layout algorithm
 */
function changeLayout(layoutName) {
    currentLayout = layoutName;
    applyLayout(layoutName);
}

/**
 * Filter visualization by criteria
 */
function filterVisualization(filter) {
    currentFilter = filter;
    updateGraph();
}

/**
 * Reset the view to show all nodes
 */
function resetVisualization() {
    if (!cy) return;

    cy.elements().removeClass('highlighted').removeClass('dimmed');
    cy.fit(50);
    cy.zoom(1);
}

/**
 * Export visualization as PNG image
 */
function exportVisualizationImage() {
    if (!cy) return;

    const png = cy.png({
        output: 'blob',
        bg: '#1a1d29',
        full: true,
        scale: 2
    });

    const url = URL.createObjectURL(png);
    const link = document.createElement('a');
    link.href = url;
    link.download = 'visualizacion-estructura-sitio.png';
    link.click();
    URL.revokeObjectURL(url);
}

/**
 * Helper: Get status class for tooltip
 */
function getStatusClass(statusCode) {
    if (statusCode >= 200 && statusCode < 300) return 'status-2xx';
    if (statusCode >= 300 && statusCode < 400) return 'status-3xx';
    if (statusCode >= 400 && statusCode < 500) return 'status-4xx';
    if (statusCode >= 500 && statusCode < 600) return 'status-5xx';
    return 'status-other';
}

/**
 * Helper: Truncate URL for display
 */
function truncateUrl(url, maxLength = 60) {
    if (url.length <= maxLength) return url;
    return url.substring(0, maxLength - 3) + '...';
}

/**
 * Clear the visualization
 */
function clearVisualization() {
    // Clear graph data
    graphData = { nodes: [], edges: [], total_links: 0, visualized_links: 0 };

    // Clear the cytoscape graph if it exists
    if (cy) {
        cy.elements().remove();
        cy.fit();
    }

    // Show placeholder
    setVisualizationState('Sin datos para visualizar', 'Inicia o carga un rastreo para ver sus relaciones.');

    console.log('Visualization cleared');
}

function visualizationUrlKey(url) {
    const value = String(url || '').trim();
    try {
        const parsed = new URL(value);
        let path = parsed.pathname || '/';
        if (path !== '/') path = path.replace(/\/+$/, '') || '/';
        return `${parsed.protocol.toLowerCase()}//${parsed.host.toLowerCase()}${path}${parsed.search}`;
    } catch (_error) {
        return value;
    }
}

function buildLocalVisualizationGraph(urls, links, maxNodes = 500, maxLinks = 2000) {
    const pagesByKey = new Map();
    const pageOrder = [];
    for (const page of urls || []) {
        const url = String(page.url || '').trim();
        const key = visualizationUrlKey(url);
        if (!url || pagesByKey.has(key)) continue;
        pagesByKey.set(key, page);
        pageOrder.push(key);
    }

    const linkRows = [];
    const linkKeys = new Set();
    for (const link of links || []) {
        if (!link.is_internal) continue;
        const sourceKey = visualizationUrlKey(link.source_url);
        const targetKey = visualizationUrlKey(link.target_url);
        if (!sourceKey || !targetKey || sourceKey === targetKey) continue;
        const edgeKey = `${sourceKey}->${targetKey}`;
        if (linkKeys.has(edgeKey)) continue;
        linkKeys.add(edgeKey);
        linkRows.push([sourceKey, targetKey]);
    }

    const configuredRoot = window.crawlState && window.crawlState.baseUrl;
    const firstDepthRoot = (urls || []).find(page => page && page.depth === 0 && page.url);
    const rootKey = visualizationUrlKey(configuredRoot || (firstDepthRoot && firstDepthRoot.url));
    const orderedKeys = pageOrder.slice();
    if (pagesByKey.has(rootKey)) {
        orderedKeys.splice(orderedKeys.indexOf(rootKey), 1);
        orderedKeys.unshift(rootKey);
    }

    const selectedKeys = [];
    const selectedSet = new Set();
    const addPage = key => {
        if (pagesByKey.has(key) && !selectedSet.has(key) && selectedKeys.length < maxNodes) {
            selectedSet.add(key);
            selectedKeys.push(key);
        }
    };
    addPage(rootKey);
    if (orderedKeys.length > maxNodes) {
        for (const [sourceKey, targetKey] of linkRows) {
            addPage(sourceKey);
            addPage(targetKey);
            if (selectedKeys.length >= maxNodes) break;
        }
    }
    for (const key of orderedKeys) {
        addPage(key);
        if (selectedKeys.length >= maxNodes) break;
    }

    const nodes = [];
    const nodeIds = new Map();
    selectedKeys.forEach((key, idx) => {
        const page = pagesByKey.get(key);
        const url = String(page.url || '').trim();
        const statusCode = Number(page.status_code || 0);
        let color = '#6b7280';
        if (statusCode >= 200 && statusCode < 300) color = '#10b981';
        else if (statusCode >= 300 && statusCode < 400) color = '#3b82f6';
        else if (statusCode >= 400 && statusCode < 500) color = '#f59e0b';
        else if (statusCode >= 500 && statusCode < 600) color = '#ef4444';
        const id = `node-${idx}`;
        nodeIds.set(key, id);
        nodes.push({
            data: {
                id,
                label: url.split('/').pop() || url.split('//').pop() || url,
                url,
                status_code: statusCode,
                title: page.title || '',
                color,
                size: rootKey && key === rootKey ? 30 : 20,
                depth: page.depth || 0,
            },
        });
    });

    const edges = [];
    for (const [sourceKey, targetKey] of linkRows) {
        const source = nodeIds.get(sourceKey);
        const target = nodeIds.get(targetKey);
        if (!source || !target || source === target) continue;
        edges.push({
            data: {
                id: `edge-${source}-${target}`,
                source,
                target,
            },
        });
        if (edges.length >= maxLinks) break;
    }

    return { nodes, edges };
}

/**
 * Update visualization from loaded crawl data (not from backend)
 */
function updateVisualizationFromLoadedData(urls, links) {
    if (!urls || urls.length === 0) {
        graphData = { nodes: [], edges: [], total_links: 0, visualized_links: 0 };
        if (cy) cy.elements().remove();
        setVisualizationState('Sin datos para visualizar', 'Inicia un rastreo para visualizar la estructura del sitio.');
        console.log('No URL data to visualize');
        return;
    }

    console.log(`Building visualization from ${urls.length} URLs and ${links ? links.length : 0} links`);
    const { nodes, edges } = buildLocalVisualizationGraph(urls, links);
    console.log(`Built ${nodes.length} nodes and ${edges.length} edges from loaded data`);

    graphData = {
        nodes,
        edges,
        total_links: edges.length,
        visualized_links: edges.length,
    };

    hideVisualizationState();
    if (cy) updateGraph();
}

// Export functions to global scope
window.initVisualization = initVisualization;
window.changeLayout = changeLayout;
window.filterVisualization = filterVisualization;
window.resetVisualization = resetVisualization;
window.exportVisualizationImage = exportVisualizationImage;
window.loadVisualizationData = loadVisualizationData;
window.updateVisualizationFromLoadedData = updateVisualizationFromLoadedData;
window.clearVisualization = clearVisualization;
