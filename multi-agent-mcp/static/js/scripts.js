// Scripts.js — OneView GOC AI (main chat UI)
let counterInterval;
let startTime;

const CHART_JS_URL = 'https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js';
const HTML2CANVAS_URL = 'https://cdn.jsdelivr.net/npm/html2canvas@1.4.1/dist/html2canvas.min.js';

let _chartJsPromise = null;
/** Load Chart.js only when a result needs charts (faster first paint). */
function ensureChartJs() {
    if (typeof Chart !== 'undefined') {
        return Promise.resolve();
    }
    if (_chartJsPromise) {
        return _chartJsPromise;
    }
    _chartJsPromise = new Promise((resolve, reject) => {
        const s = document.createElement('script');
        s.src = CHART_JS_URL;
        s.async = true;
        s.onload = () => resolve();
        s.onerror = () => reject(new Error('Chart.js failed to load'));
        document.head.appendChild(s);
    });
    return _chartJsPromise;
}

let _html2canvasPromise = null;
/** Chart.js: canvases inside display:none tabs get zero size — resize when tab becomes visible or after scripts run. */
function resizeSplunkChartsIn(root) {
    if (!root || typeof Chart === 'undefined' || !Chart.getChart) {
        return;
    }
    try {
        root.querySelectorAll('canvas').forEach(function (canvas) {
            try {
                const ch = Chart.getChart(canvas);
                if (ch) {
                    ch.resize();
                }
            } catch (_e) {
                /* ignore */
            }
        });
    } catch (_e) {
        /* ignore */
    }
}

function ensureHtml2Canvas() {
    if (typeof html2canvas === 'function') {
        return Promise.resolve();
    }
    if (_html2canvasPromise) {
        return _html2canvasPromise;
    }
    _html2canvasPromise = new Promise((resolve, reject) => {
        const s = document.createElement('script');
        s.src = HTML2CANVAS_URL;
        s.async = true;
        s.onload = () => resolve();
        s.onerror = () => reject(new Error('html2canvas failed to load'));
        document.head.appendChild(s);
    });
    return _html2canvasPromise;
}

/** Home hub / env tiles: WebKit throws on anchor.href = malformed URL (\"string did not match expected pattern\"). */
function sanitizeHttpHrefForDom(href) {
    if (href == null || href === '') return null;
    const s = String(href).replace(/[\r\n\0\u2028\u2029]/g, '').trim();
    if (!s || s === '#') return null;
    try {
        const u = new URL(s);
        if (u.protocol !== 'http:' && u.protocol !== 'https:') return null;
        return u.href;
    } catch (_e) {
        return null;
    }
}

// ============================================
// THEME TOGGLE
// ============================================
function toggleTheme() {
    const html = document.documentElement;
    const currentTheme = html.getAttribute('data-theme');
    const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
    
    html.setAttribute('data-theme', newTheme);
    localStorage.setItem('theme', newTheme);
    
    console.log(`🎨 Theme switched to: ${newTheme}`);
    showNotification(`Theme switched to ${newTheme} mode`);
}

// Load saved theme on page load
function loadSavedTheme() {
    const savedTheme = localStorage.getItem('theme') || 'light';
    document.documentElement.setAttribute('data-theme', savedTheme);
}

// ============================================
// HISTORY MANAGEMENT
// ============================================
function toggleHistory() {
    const content = document.getElementById('history-content');
    const arrow = document.getElementById('history-arrow');
    
    if (content.style.display === 'none') {
        content.style.display = 'block';
        arrow.style.transform = 'rotate(90deg)';
    } else {
        content.style.display = 'none';
        arrow.style.transform = 'rotate(0deg)';
    }
}

function clearHistory() {
    if (!confirm('Are you sure you want to clear all history?')) {
        return;
    }
    
    // Clear from UI
    const historyList = document.getElementById('history-list');
    if (historyList) {
        historyList.innerHTML = '<li style="color: #666; font-size: 12px; padding: 10px;">No history yet</li>';
    }
    
    window.historyData = [];
    historyExpanded = false; // Reset expanded state
    showNotification('History cleared successfully');
    console.log('✅ History cleared');
}

// History search/filter functionality
function setupHistorySearch() {
    const searchInput = document.getElementById('history-search');
    if (!searchInput) return;
    
    searchInput.addEventListener('input', (e) => {
        const searchTerm = e.target.value.toLowerCase().trim();
        
        if (searchTerm === '') {
            // If search is empty, restore to collapsed view
            renderHistory(historyExpanded);
            return;
        }
        
        // When searching, show all matching results
        const data = window.historyData || [];
        const historyList = document.getElementById("history-list");
        historyList.innerHTML = '';
        
        let matchCount = 0;
        
        data.forEach((item, index) => {
            const queryText = (item.query && item.query.trim()) ? item.query : 'Query ' + (index + 1);
            
            if (queryText.toLowerCase().includes(searchTerm)) {
                matchCount++;
                const li = document.createElement("li");
                const btn = document.createElement("button");
                
                const maxLength = 30;
                const displayText = queryText.length > maxLength 
                    ? queryText.substring(0, maxLength) + '...' 
                    : queryText;
                
                btn.textContent = displayText;
                btn.onclick = () => showHistoryResult(index);
                btn.title = queryText;
                li.appendChild(btn);
                historyList.appendChild(li);
            }
        });
        
        if (matchCount === 0) {
            historyList.innerHTML = '<li style="color: #999; font-size: 12px; padding: 10px;">No matches found</li>';
        }
    });
}

// ============================================
// NOTIFICATIONS
// ============================================
function showNotification(message, duration = 3000) {
    const notification = document.createElement('div');
    notification.className = 'copy-notification';
    notification.textContent = message;
    document.body.appendChild(notification);
    
    setTimeout(() => {
        notification.remove();
    }, duration);
}

// ============================================
// AI AUTO-SELECT TOOLS
// ============================================
async function autoSelectTools() {
    const inputText = document.getElementById('input-text').value.trim();
    
    if (!inputText) {
        alert('⚠️ Please enter a question first');
        return;
    }
    
    const autoSelectBtn = document.getElementById('auto-select-btn');
    const originalText = autoSelectBtn.textContent;
    
    // Create a more visible loading overlay
    const loadingDiv = document.createElement('div');
    loadingDiv.id = 'ai-select-loading';
    loadingDiv.style.cssText = `
        position: fixed;
        top: 50%;
        left: 50%;
        transform: translate(-50%, -50%);
        background: linear-gradient(135deg, #8b5cf6 0%, #6d28d9 100%);
        color: white;
        padding: 30px 50px;
        border-radius: 12px;
        box-shadow: 0 8px 32px rgba(139, 92, 246, 0.4);
        z-index: 10000;
        text-align: center;
        font-size: 18px;
        font-weight: bold;
    `;
    loadingDiv.innerHTML = `
        <div style="margin-bottom: 10px;">🤖 AI Analyzing...</div>
        <div style="font-size: 14px; font-weight: normal; opacity: 0.9;">Finding the best tools for your question</div>
    `;
    document.body.appendChild(loadingDiv);
    
    try {
        // Show loading state on button
        autoSelectBtn.disabled = true;
        autoSelectBtn.textContent = '🤖 Analyzing...';
        autoSelectBtn.style.opacity = '0.7';
        
        console.log('🤖 Calling /api/suggest-tools with query:', inputText);
        
        // Call the suggest-tools endpoint
        const response = await fetch('/api/suggest-tools', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query: inputText })
        });
        
        console.log('🤖 Response status:', response.status);
        
        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.error || `Server error: ${response.status}`);
        }
        
        const data = await response.json();
        const suggestedTools = data.suggested_tools || [];
        
        console.log('🤖 Suggested tools:', suggestedTools);
        
        if (suggestedTools.length === 0) {
            alert('⚠️ No tools suggested by AI. Please select manually.');
            return;
        }
        
        // Uncheck all tools first
        const allCheckboxes = document.querySelectorAll('input[name=tool]');
        allCheckboxes.forEach(checkbox => {
            checkbox.checked = false;
        });
        
        // Check only the suggested tools with visual feedback
        let checkedCount = 0;
        suggestedTools.forEach(toolName => {
            const checkbox = document.querySelector(`input[name=tool][value="${toolName}"]`);
            if (checkbox) {
                checkbox.checked = true;
                checkedCount++;
                
                // Highlight the selected tool briefly
                const label = checkbox.closest('label');
                if (label) {
                    label.style.backgroundColor = '#e9d5ff';
                    label.style.transition = 'background-color 0.3s';
                    setTimeout(() => {
                        label.style.backgroundColor = '';
                    }, 2000);
                }
            }
        });
        
        // Remove loading overlay
        loadingDiv.remove();
        
        // Show prominent success message
        const toolNames = suggestedTools.join(', ');
        const successDiv = document.createElement('div');
        successDiv.style.cssText = `
            position: fixed;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            background: linear-gradient(135deg, #10b981 0%, #059669 100%);
            color: white;
            padding: 30px 50px;
            border-radius: 12px;
            box-shadow: 0 8px 32px rgba(16, 185, 129, 0.4);
            z-index: 10000;
            text-align: center;
            font-size: 18px;
            font-weight: bold;
        `;
        successDiv.innerHTML = `
            <div style="margin-bottom: 10px;">✅ Tools Selected!</div>
            <div style="font-size: 14px; font-weight: normal; opacity: 0.9;">${toolNames}</div>
            <div style="margin-top: 15px; font-size: 13px; font-weight: normal; opacity: 0.8;">Executing automatically...</div>
        `;
        document.body.appendChild(successDiv);
        
        console.log(`✅ Selected ${checkedCount} tool(s): ${toolNames}`);
        
        // Auto-execute after 1.5 seconds to show the success message
        setTimeout(() => {
            successDiv.remove();
            console.log('🚀 Auto-executing selected tools...');
            
            // Trigger form submission programmatically
            const form = document.getElementById('search-form');
            if (form) {
                // Manually trigger the submit handler
                const submitEvent = new Event('submit', { 
                    bubbles: true, 
                    cancelable: true 
                });
                form.dispatchEvent(submitEvent);
            }
        }, 1500);
        
    } catch (error) {
        console.error('❌ Error auto-selecting tools:', error);
        
        // Remove loading overlay if still present
        const loadingDiv = document.getElementById('ai-select-loading');
        if (loadingDiv) loadingDiv.remove();
        
        // Show error
        alert(`⚠️ Error: ${error.message}`);
    } finally {
        // Reset button state
        autoSelectBtn.disabled = false;
        autoSelectBtn.textContent = originalText;
        autoSelectBtn.style.opacity = '1';
    }
}

// Make autoSelectTools available globally
window.autoSelectTools = autoSelectTools;

// ============================================
// COPY TO CLIPBOARD
// ============================================
function copyToClipboard(text) {
    navigator.clipboard.writeText(text).then(() => {
        showNotification('✅ Copied to clipboard!');
    }).catch(err => {
        console.error('Failed to copy:', err);
        showNotification('❌ Failed to copy');
    });
}

// Add copy button to results
function addResultActions(resultsBox) {
    if (!resultsBox || resultsBox.innerHTML === '') return;
    
    // Check if actions already exist
    if (resultsBox.querySelector('.result-actions')) return;
    
    const actionsDiv = document.createElement('div');
    actionsDiv.className = 'result-actions';
    actionsDiv.innerHTML = `
        <button type="button" class="result-action-btn" onclick="copyResultsToClipboard()" title="Copy results">
            📋 Copy
        </button>
        <button type="button" class="result-action-btn result-action-btn--slack" onclick="sendResultsToSlack()" title="Bedrock summary of this result block only → Slack">
            📤 Enviar a Slack
        </button>
        <button type="button" class="result-action-btn" onclick="expandAllSections()" title="Expand all sections">
            📖 Expand All
        </button>
        <button type="button" class="result-action-btn" onclick="collapseAllSections()" title="Collapse all sections">
            📕 Collapse All
        </button>
    `;
    
    resultsBox.style.position = 'relative';
    resultsBox.insertBefore(actionsDiv, resultsBox.firstChild);
}

function getResultsTextExcludingActions() {
    const resultsBox = document.getElementById('results-box');
    if (!resultsBox) return '';
    const clone = resultsBox.cloneNode(true);
    const actions = clone.querySelector('.result-actions');
    if (actions) actions.remove();
    return clone.innerText.trim();
}

function copyResultsToClipboard() {
    const text = getResultsTextExcludingActions();
    if (text) {
        copyToClipboard(text);
    }
}

function sendResultsToSlack() {
    if (typeof window.SlackShare === 'undefined') {
        showNotification('⚠️ Slack no disponible (falta slack_share.js)', 6000);
        return;
    }
    const box = document.getElementById('results-box');
    if (!box || !box.innerText || !box.innerText.trim()) {
        showNotification('⚠️ No content to summarize');
        return;
    }
    window.SlackShare.sendSummaryFromElement(box, {
        page_title: 'OneView — resultado de consulta',
    });
}

window.sendResultsToSlack = sendResultsToSlack;

/** Barra superior: resumen Bedrock de toda la UI (sidebar + columna principal). */
function sendMainPageSlackSummary() {
    if (typeof window.SlackShare === 'undefined') {
        showNotification('⚠️ Slack no disponible (falta slack_share.js)', 6000);
        return;
    }
    window.SlackShare.sendSummaryFromHomePage();
}

window.sendMainPageSlackSummary = sendMainPageSlackSummary;

function expandAllSections() {
    // Expand all subsections in the active tab
    const activeTab = document.querySelector('.tab-content[style*="display: block"]');
    if (!activeTab) return;
    
    const subsections = activeTab.querySelectorAll('.subsection-collapsible');
    subsections.forEach(section => {
        const content = section.querySelector('.subsection-content');
        const btn = section.querySelector('.subsection-toggle-btn');
        if (content && btn) {
            content.style.display = 'block';
            btn.textContent = '▼';
        }
    });
    
    if (subsections.length > 0) {
        showNotification(`Expanded ${subsections.length} subsection(s)`);
    } else {
        showNotification('No collapsible sections in current tab');
    }
}

function collapseAllSections() {
    // Collapse all subsections in the active tab
    const activeTab = document.querySelector('.tab-content[style*="display: block"]');
    if (!activeTab) return;
    
    const subsections = activeTab.querySelectorAll('.subsection-collapsible');
    subsections.forEach(section => {
        const content = section.querySelector('.subsection-content');
        const btn = section.querySelector('.subsection-toggle-btn');
        if (content && btn) {
            content.style.display = 'none';
            btn.textContent = '▶';
        }
    });
    
    if (subsections.length > 0) {
        showNotification(`Collapsed ${subsections.length} subsection(s)`);
    } else {
        showNotification('No collapsible sections in current tab');
    }
}

function toggleToolSection(toolId) {
    const section = document.getElementById(toolId);
    if (!section) return;
    
    const content = section.querySelector('.tool-content');
    const btn = section.querySelector('.tool-toggle-btn');
    
    if (!content || !btn) return;
    
    const isExpanded = content.style.display !== 'none';
    
    if (isExpanded) {
        content.style.display = 'none';
        btn.textContent = '▶';
    } else {
        content.style.display = 'block';
        btn.textContent = '▼';
    }
}

// Make toggleToolSection available globally
window.toggleToolSection = toggleToolSection;

function toggleSubsection(subsectionId) {
    const section = document.getElementById(subsectionId);
    if (!section) return;
    
    const content = section.querySelector('.subsection-content');
    const btn = section.querySelector('.subsection-toggle-btn');
    
    if (!content || !btn) return;
    
    const isExpanded = content.style.display !== 'none';
    
    if (isExpanded) {
        content.style.display = 'none';
        btn.textContent = '▶';
    } else {
        content.style.display = 'block';
        btn.textContent = '▼';
    }
}

// Make toggleSubsection available globally
window.toggleSubsection = toggleSubsection;

// Toggle tool dropdown
function toggleToolDropdown(dropdownId, event) {
    if (event) event.stopPropagation();
    
    const content = document.getElementById(dropdownId);
    const header = content.previousElementSibling;
    const toggle = header.querySelector('.tool-dropdown-toggle');
    
    if (content.classList.contains('active')) {
        content.classList.remove('active');
        header.classList.remove('active');
        toggle.textContent = '▼';
    } else {
        content.classList.add('active');
        header.classList.add('active');
        toggle.textContent = '▲';
    }
}

// Make toggleToolDropdown available globally
window.toggleToolDropdown = toggleToolDropdown;

// Toggle select all tools in a category
function toggleSelectAll(category, checked) {
    const dropdownId = `dropdown-${category}`;
    const content = document.getElementById(dropdownId);
    
    if (!content) return;
    
    // Get all checkboxes within this dropdown (excluding disabled ones)
    const checkboxes = content.querySelectorAll('input[type="checkbox"][name="tool"]:not(:disabled)');
    checkboxes.forEach(cb => {
        cb.checked = checked;
    });
    
    console.log(`${checked ? '✅' : '❌'} ${category}: ${checked ? 'Selected' : 'Deselected'} ${checkboxes.length} tools`);
}

// Make toggleSelectAll available globally
window.toggleSelectAll = toggleSelectAll;

// Tab switching function
function switchTab(contentId, btnElement) {
    // Hide all tab contents
    const allContents = document.querySelectorAll('.tab-content');
    allContents.forEach(content => {
        content.style.display = 'none';
    });
    
    // Remove active class from all buttons
    const allButtons = document.querySelectorAll('.tab-btn');
    allButtons.forEach(btn => {
        btn.classList.remove('active');
    });
    
    // Show selected content
    const selectedContent = document.getElementById(contentId);
    if (selectedContent) {
        selectedContent.style.display = 'block';
        // Splunk P0 tabs: charts were often laid out at 0×0 while this panel was hidden
        requestAnimationFrame(function () {
            requestAnimationFrame(function () {
                resizeSplunkChartsIn(selectedContent);
            });
        });
    }
    
    // Add active class to clicked button
    if (btnElement) {
        btnElement.classList.add('active');
    }
}

// Make switchTab available globally
window.switchTab = switchTab;

// ============================================
// ENHANCED LOADING
// ============================================
function showLoadingOverlay() {
    const overlay = document.getElementById('loading-overlay');
    if (overlay) {
        overlay.style.display = 'flex';
    }
}

function hideLoadingOverlay() {
    const overlay = document.getElementById('loading-overlay');
    if (overlay) {
        overlay.style.display = 'none';
    }
    
    // Clear the counter interval
    if (counterInterval) {
        clearInterval(counterInterval);
        counterInterval = null;
    }
    
    // Reset loading overlay content
    const loadingToolsList = document.getElementById('loading-tools-list');
    const loadingTimeCounter = document.getElementById('loading-time-counter');
    if (loadingToolsList) loadingToolsList.textContent = '-';
    if (loadingTimeCounter) loadingTimeCounter.textContent = '0s';
}

// ============================================
// UPDATE LAST UPDATE TIMESTAMP
// ============================================
function updateLastUpdateTime() {
    const lastUpdateElement = document.getElementById('last-update');
    if (lastUpdateElement) {
        const now = new Date();
        const timeString = now.toLocaleTimeString('en-US', { 
            hour: '2-digit', 
            minute: '2-digit',
            second: '2-digit'
        });
        lastUpdateElement.textContent = `Last update: ${timeString}`;
    }
}

// P0 Splunk tools default to 24h in the Time Range selector when only these are selected.
const SPLUNK_P0_TOOL_NAMES = new Set([
    'P0_Streaming',
    'P0_CVR_Streaming',
    'P0_ADT_Streaming',
    'P0_Streaming_US',
]);
let _lastSplunkP0OnlySelection = false;

function splunkP0OnlyToolSelection(selectedTools) {
    return (
        selectedTools.length > 0 &&
        selectedTools.every(function (t) {
            return SPLUNK_P0_TOOL_NAMES.has(t);
        })
    );
}

// Function to show/hide timerange selector based on selected tools
function setupTimeRangeSelector() {
    const timerangeContainer = document.getElementById('timerange-container');
    
    function updateTimeRangeVisibility() {
        const checkboxes = document.querySelectorAll('input[type=checkbox][name=tool]:checked');
        const selectedTools = Array.from(checkboxes).map(cb => cb.value);
        
        // Show timerange if any Datadog or Splunk tool is selected
        const showTimeRange = selectedTools.includes('DD_Red_Metrics') || 
                              selectedTools.includes('DD_Errors') ||
                              selectedTools.includes('DD_Red_ADT') ||
                              selectedTools.includes('DD_Red_Samsung') ||
                              selectedTools.includes('DD_Red_Metrics_US') ||
                              selectedTools.includes('DD_Samsung_Errors') ||
                              selectedTools.includes('DD_Failed_Pods') ||
                              selectedTools.includes('DD_403_Errors') ||
                              selectedTools.includes('P0_Streaming') ||
                              selectedTools.includes('P0_CVR_Streaming') ||
                              selectedTools.includes('P0_ADT_Streaming') ||
                              selectedTools.includes('P0_Streaming_US');
        
        if (timerangeContainer) {
            timerangeContainer.style.display = showTimeRange ? 'block' : 'none';
        }

        const p0Only = splunkP0OnlyToolSelection(selectedTools);
        const trSel = document.getElementById('timerange-select');
        if (showTimeRange && trSel && p0Only && !_lastSplunkP0OnlySelection) {
            trSel.value = '24';
        }
        _lastSplunkP0OnlySelection = p0Only;
    }
    
    // Add event listeners to all tool checkboxes
    document.querySelectorAll('input[type=checkbox][name=tool]').forEach(checkbox => {
        checkbox.addEventListener('change', updateTimeRangeVisibility);
    });
    
    // Initial check
    updateTimeRangeVisibility();
}

// Show spinner and counter while the query runs
function showLoading(selectedTools = []) {
    // Show overlay
    showLoadingOverlay();
    
    // Update loading overlay with selected tools
    const loadingToolsList = document.getElementById('loading-tools-list');
    if (loadingToolsList && selectedTools.length > 0) {
        loadingToolsList.textContent = selectedTools.join(', ');
    }
    
    // Also show inline spinner for backward compatibility
    document.getElementById('loading-message').innerHTML =
        '<span class="spinner"></span><span class="counter" id="counter">0s</span>';
    document.getElementById('results-box').innerHTML = '';
    document.getElementById('final-counter').innerText = '';
    startTime = Date.now();
    if (counterInterval) clearInterval(counterInterval);
    counterInterval = setInterval(() => {
        const elapsed = Math.floor((Date.now() - startTime) / 1000);
        const counterEl = document.getElementById('counter');
        const loadingTimeCounter = document.getElementById('loading-time-counter');
        
        if (counterEl) counterEl.innerText = elapsed + 's';
        if (loadingTimeCounter) loadingTimeCounter.textContent = elapsed + 's';
    }, 1000);
}

// 🔄 Cargar historial desde API
// State to track if history is expanded
let historyExpanded = false;
const HISTORY_PREVIEW_COUNT = 3;

function loadHistory() {
    fetch('/api/history')
        .then(res => {
            if (!res.ok) throw new Error('Error al cargar historial');
            return res.json();
        })
        .then(data => {
            window.historyData = data;
            renderHistory(historyExpanded);
        })
        .catch(err => {
            console.error('Error cargando historial:', err);
            const historyList = document.getElementById("history-list");
            historyList.innerHTML = '<li style="color: #f56565; font-size: 12px; padding: 10px;">⚠️ Error loading history</li>';
        });
}

function renderHistory(showAll = false) {
    const historyList = document.getElementById("history-list");
    const data = window.historyData || [];
    
    historyList.innerHTML = '';
    
    if (data.length === 0) {
        historyList.innerHTML = '<li style="color: #666; font-size: 12px; padding: 10px;">No history yet</li>';
        return;
    }
    
    // Determine how many items to show
    const itemsToShow = showAll ? data.length : Math.min(HISTORY_PREVIEW_COUNT, data.length);
    
    // Render history items
    for (let index = 0; index < itemsToShow; index++) {
        const item = data[index];
        const li = document.createElement("li");
        const btn = document.createElement("button");
        
        // Use query text, or fallback to generic name
        const queryText = (item.query && item.query.trim()) ? item.query : 'Query ' + (index + 1);
        
        // Truncate long queries for display
        const maxLength = 30;
        const displayText = queryText.length > maxLength 
            ? queryText.substring(0, maxLength) + '...' 
            : queryText;
        
        btn.textContent = displayText;
        btn.onclick = () => showHistoryResult(index);
        btn.title = queryText; // Tooltip with full query
        li.appendChild(btn);
        historyList.appendChild(li);
    }
    
    // Add "Show more" / "Show less" button if needed
    if (data.length > HISTORY_PREVIEW_COUNT) {
        const li = document.createElement("li");
        const btn = document.createElement("button");
        btn.style.cssText = 'background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; font-weight: bold; border: none; margin-top: 8px;';
        btn.textContent = showAll ? '▲ Show less' : `▼ Show ${data.length - HISTORY_PREVIEW_COUNT} more`;
        btn.onclick = () => {
            historyExpanded = !historyExpanded;
            renderHistory(historyExpanded);
        };
        li.appendChild(btn);
        historyList.appendChild(li);
    }
}

function toggleHistoryExpanded() {
    historyExpanded = !historyExpanded;
    renderHistory(historyExpanded);
}

// Show history result
async function showHistoryResult(index) {
    if (!window.historyData || !window.historyData[index]) {
        console.error('No history data available for index:', index);
        return;
    }
    
    // Limpiar otros contenedores
    document.getElementById('loading-message').innerHTML = '';
    document.getElementById('final-counter').innerText = '';
    
    // Clear history-result to avoid duplication
    const historyResult = document.getElementById('history-result');
    if (historyResult) {
        historyResult.innerHTML = '';
    }
    
    // Show result in results-box only
    const resultsBox = document.getElementById('results-box');
    const htmlContent = window.historyData[index].result || '<p>No result available</p>';
    try {
        await ensureChartJs();
    } catch (e) {
        console.warn('Chart.js preload (history):', e);
    }
    resultsBox.innerHTML = htmlContent;
    
    // Re-run scripts to load Chart.js charts
    const scripts = resultsBox.querySelectorAll('script');
    
    let scriptIndex = 0;
    function executeNextScript() {
        if (scriptIndex >= scripts.length) {
            requestAnimationFrame(function () {
                requestAnimationFrame(function () {
                    resizeSplunkChartsIn(resultsBox);
                });
            });
            // Add result actions after scripts are executed
            setTimeout(() => addResultActions(resultsBox), 100);
            return;
        }
        
        const oldScript = scripts[scriptIndex];
        const newScript = document.createElement('script');
        
        // Copy attributes
        Array.from(oldScript.attributes).forEach(attr => {
            newScript.setAttribute(attr.name, attr.value);
        });
        
        // Copy content for inline scripts
        if (oldScript.textContent) {
            newScript.textContent = oldScript.textContent;
        }
        
        // Handle script loading
        if (newScript.src) {
            // External script - wait for it to load
            newScript.onload = () => {
                scriptIndex++;
                executeNextScript();
            };
            newScript.onerror = () => {
                console.error(`Failed to load script ${scriptIndex + 1}`);
                scriptIndex++;
                executeNextScript();
            };
        } else {
            // Inline script - executes immediately
            scriptIndex++;
            setTimeout(executeNextScript, 50); // Small delay for DOM updates
        }
        
        // Add to document to execute
        document.body.appendChild(newScript);
        
        // Remove old script
        oldScript.remove();
    }
    
    executeNextScript();
    
    // Scroll to results
    resultsBox.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    
    console.log('✅ History result loaded with scripts re-executed');
}

// Reiniciar el formulario y limpiar resultados
function newChat() {
    // Limpiar input de texto
    const inputText = document.querySelector('#input-text');
    if (inputText) inputText.value = '';
    
    // Limpiar todos los contenedores de resultados
    const resultsBox = document.getElementById('results-box');
    if (resultsBox) resultsBox.innerHTML = '';
    
    const historyResult = document.getElementById('history-result');
    if (historyResult) historyResult.innerHTML = '';
    
    const finalCounter = document.getElementById('final-counter');
    if (finalCounter) finalCounter.innerText = '';
    
    const loadingMessage = document.getElementById('loading-message');
    if (loadingMessage) loadingMessage.innerHTML = '';
    
    const counter = document.getElementById('counter');
    if (counter) counter.innerText = '';
    
    // Desmarcar todos los checkboxes
    document.querySelectorAll('input[type=checkbox][name=tool]').forEach(cb => {
        cb.checked = false;
    });
    
    // Limpiar intervalo del contador
    if (counterInterval) {
        clearInterval(counterInterval);
        counterInterval = null;
    }
    
    // Focus textarea so the user can type immediately
    if (inputText) {
        inputText.focus();
    }
    
    console.log('✅ New chat started - all fields cleared');
}

/** Home page environment strip — same API and card layout as /statusmonitor hub */
const HOME_ENV_HUB_TIMERANGE = 1;
const HOME_ENV_HUB_REFRESH_MS = 360000; // 6 minutes
const HUB_SUMMARY_CACHE_TTL_MS = 350000; // slightly under 6 min home refresh
/** Home index only: which hub cards to show (order preserved). */
const HOME_ENV_HUB_SLUGS = ['production', 'samsung', 'adt'];

function filterHomeEnvironmentHubEnvironments(envs) {
    const order = HOME_ENV_HUB_SLUGS;
    const bySlug = {};
    (envs || []).forEach(e => {
        const s = String((e && e.slug) || '').trim().toLowerCase();
        if (s) bySlug[s] = e;
    });
    return order.map(s => bySlug[s]).filter(Boolean);
}

function escEnvHubText(t) {
    if (t == null) return '';
    const d = document.createElement('div');
    d.textContent = String(t);
    return d.innerHTML;
}

/** Same card markup as statusmonitor.html applyHubSummaryData */
function buildEnvHubCardHtml(e) {
    const cls =
        e.overall === 'critical'
            ? 'sm-hub-card--critical'
            : e.overall === 'warning'
              ? 'sm-hub-card--warning'
              : 'sm-hub-card--healthy';
    const ov = e.overall || 'healthy';
    const gOn = ov === 'healthy' ? ' is-on' : '';
    const yOn = ov === 'warning' ? ' is-on' : '';
    const rOn = ov === 'critical' ? ' is-on' : '';
    const ddt = e.dd_monitor_alerts_total != null ? Number(e.dd_monitor_alerts_total) : 0;
    const dds = e.dd_monitor_alerts_services != null ? Number(e.dd_monitor_alerts_services) : 0;
    const ddtSafe = Number.isFinite(ddt) ? ddt : 0;
    const ddsSafe = Number.isFinite(dds) ? dds : 0;
    const metaS =
        ddsSafe > 0
            ? '<span class="sm-hub-dd-rollup__meta">' +
              ddsSafe +
              ' ' +
              (ddsSafe === 1 ? 'service' : 'services') +
              '</span>'
            : '';
    const ambRow =
        '<div class="sm-hub-ambrow">' +
        '<div class="sm-hub-sema" role="img" aria-label="Environment status: ' +
        escEnvHubText(ov) +
        '">' +
        '<span class="sm-hub-sd sm-hub-sd--g' +
        gOn +
        '"></span>' +
        '<span class="sm-hub-sd sm-hub-sd--y' +
        yOn +
        '"></span>' +
        '<span class="sm-hub-sd sm-hub-sd--r' +
        rOn +
        '"></span></div>' +
        '<div class="sm-hub-dd-rollup" title="Datadog monitors in Alert (sum of service+env; same as status tiles)">' +
        '<span class="sm-hub-dd-rollup__n">' +
        ddtSafe +
        '</span>' +
        '<span class="sm-hub-dd-rollup__lbl">monitors in Alert</span>' +
        metaS +
        '</div></div>';
    let br = '';
    if (e.service_breakdown && e.service_breakdown.length) {
        br =
            '<div class="sm-hub-svc-breakdown">' +
            e.service_breakdown
                .map(function (r) {
                    const stCls = String(r.status || 'unknown').replace(/[^a-z0-9_-]/gi, '');
                    return (
                        '<div class="sm-hub-svc-line sm-hub-svc--' +
                        stCls +
                        '">' +
                        '<span class="sm-hub-svc-n">' +
                        escEnvHubText(r.service) +
                        '</span>' +
                        '<span class="sm-hub-svc-st">' +
                        escEnvHubText(r.status) +
                        '</span></div>'
                    );
                })
                .join('') +
            '</div>';
    }
    const hubHref = sanitizeHttpHrefForDom(e.href) || '#';
    const op =
        e.operational != null
            ? e.operational
            : (Number(e.healthy || 0) + Number(e.warning || 0) + Number(e.critical || 0));
    const cfg = e.configured != null ? e.configured : e.monitored;
    const cardHtml =
        '<a class="sm-hub-card ' +
        cls +
        '" href="' +
        escEnvHubText(hubHref) +
        '" target="_blank" rel="noopener">' +
        '<div class="sm-hub-card-title">' +
        escEnvHubText(e.label || e.slug || '—') +
        '</div>' +
        ambRow +
        '<div class="sm-hub-stats">' +
        '<span>Healthy</span><span class="sm-hub-stat-val">' +
        (e.healthy != null ? e.healthy : 0) +
        '</span>' +
        '<span>Warning</span><span class="sm-hub-stat-val">' +
        (e.warning != null ? e.warning : 0) +
        '</span>' +
        '<span>Critical</span><span class="sm-hub-stat-val">' +
        (e.critical != null ? e.critical : 0) +
        '</span>' +
        '<span>Inactive</span><span class="sm-hub-stat-val">' +
        (e.inactive != null ? e.inactive : 0) +
        '</span>' +
        '<span>Unknown</span><span class="sm-hub-stat-val">' +
        (e.unknown != null ? e.unknown : 0) +
        '</span>' +
        '<span>Operational</span><span class="sm-hub-stat-val">' +
        op +
        '</span>' +
        '<span>Configured</span><span class="sm-hub-stat-val">' +
        (cfg != null ? cfg : 0) +
        '</span>' +
        '</div>' +
        br +
        '</a>';
    let reasonHtml = '';
    const lines = e.status_reason_lines;
    if ((e.overall === 'warning' || e.overall === 'critical') && lines && lines.length) {
        const rcls =
            e.overall === 'critical' ? 'sm-hub-tile-reason--critical' : 'sm-hub-tile-reason--warning';
        reasonHtml =
            '<div class="sm-hub-tile-reason ' +
            rcls +
            '" role="note"><ul>' +
            lines.map(function (line) {
                return '<li>' + escEnvHubText(line) + '</li>';
            }).join('') +
            '</ul></div>';
    }
    return '<div class="sm-hub-cell">' + cardHtml + reasonHtml + '</div>';
}

function renderHomeEnvironmentHubFromPayload(data, grid, meta, fromSessionCache) {
    if (data.success === false || (data.error && !data.environments)) {
        const msg = data && data.error ? String(data.error) : 'Unknown error';
        grid.innerHTML = '';
        const errEl = document.createElement('div');
        errEl.className = 'env-hub-home-error';
        errEl.textContent = 'Could not load environment summary: ' + msg;
        grid.appendChild(errEl);
        return;
    }
    const envs = filterHomeEnvironmentHubEnvironments(data.environments || []);
    grid.innerHTML = envs.map(buildEnvHubCardHtml).join('');
    if (meta) {
        const now = new Date();
        const ts = now.toLocaleTimeString(undefined, {
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit'
        });
        if (fromSessionCache) {
            meta.textContent =
                'Session cache (fewer API calls) · ' +
                ts +
                ' · auto-refresh every 6 min';
        } else {
            meta.textContent = 'Last updated: ' + ts + ' · auto-refresh every 6 min';
        }
    }
}

function loadHomeEnvironmentHub(isManual) {
    const grid = document.getElementById('env-hub-home-grid');
    const meta = document.getElementById('env-hub-home-updated');
    if (!grid) return;

    const C = typeof SessionDataCache !== 'undefined' ? SessionDataCache : null;
    const ck = 'hub_summary_' + HOME_ENV_HUB_TIMERANGE;
    if (isManual !== true && C) {
        const hit = C.get(ck);
        if (hit) {
            renderHomeEnvironmentHubFromPayload(hit, grid, meta, true);
            return;
        }
    }

    if (isManual === true) {
        grid.innerHTML = '<div class="env-hub-home-loading">Refreshing…</div>';
    }

    fetch('/api/statusmonitor/hub-summary', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ timerange: HOME_ENV_HUB_TIMERANGE })
    })
        .then(res => res.json())
        .then(data => {
            if (C && data.success !== false && data.environments) {
                C.set(ck, data, HUB_SUMMARY_CACHE_TTL_MS);
            }
            renderHomeEnvironmentHubFromPayload(data, grid, meta, false);
        })
        .catch(err => {
            console.error('Home environment hub:', err);
            grid.innerHTML = '';
            const errEl = document.createElement('div');
            errEl.className = 'env-hub-home-error';
            errEl.textContent = 'Could not load environment summary (network error).';
            grid.appendChild(errEl);
        });
}

// 🔄 Auto-refresh Status Monitor
// Load PagerDuty Monitor
function applyPagerDutyMonitorPayload(data) {
            // Update timestamp
            const timeElement = document.getElementById('pd-time');
            if (timeElement) {
                const now = new Date();
                const timeString = now.toLocaleTimeString('en-US', {
                    hour: '2-digit',
                    minute: '2-digit',
                    second: '2-digit'
                });
                timeElement.textContent = `Last updated: ${timeString}`;
            }

            const boardLinks = document.getElementById('pd-board-links');
            if (boardLinks) {
                const esc = (s) =>
                    String(s == null ? '' : s)
                        .replace(/&/g, '&amp;')
                        .replace(/</g, '&lt;')
                        .replace(/"/g, '&quot;');
                const aOpen = (href, label) =>
                    '<a href="' +
                    esc(href) +
                    '" target="_blank" rel="noopener noreferrer" style="color: var(--link-color, #2563eb); font-weight: 600;">' +
                    esc(label) +
                    '</a>';
                if (data.error) {
                    boardLinks.textContent = '';
                } else {
                    const id = data.status_dashboard_id || 'PRBJIO4';
                    const sub = 'arlo';
                    const base =
                        data.status_dashboard_url ||
                        'https://' + sub + '.pagerduty.com/external-status-dashboard/' + id + '/incidents';
                    const basePath = base.split('?')[0];
                    const uOngoing = data.status_dashboard_url_active || basePath + '?tab=active';
                    const uRes = data.status_dashboard_url_resolved || basePath + '?tab=resolved';
                    const uPend = data.status_dashboard_url_pending || basePath + '?tab=pending';
                    boardLinks.innerHTML =
                        '<span style="opacity:0.85;font-weight:700;">External status</span> · board <code style="font-size:10px;">' +
                        esc(id) +
                        '</code><br>' +
                        aOpen(uOngoing, 'Ongoing') +
                        ' · ' +
                        aOpen(uRes, 'Resolved') +
                        ' · ' +
                        aOpen(uPend, 'Pending') +
                        ' · ' +
                        aOpen(base, 'All incidents');
                    const pdBarWrap = document.getElementById('pd-summary-wrap');
                    if (pdBarWrap) {
                        pdBarWrap.href = base;
                        pdBarWrap.title = 'Open PagerDuty incidents (board ' + id + ')';
                    }
                }
            }
            
            const triggered = data.triggered || 0;
            const acknowledged = data.acknowledged || 0;
            const resolved = data.resolved || 0;
            
            // Update counter numbers
            const triggeredCountEl = document.getElementById('pd-triggered-count');
            const ackCountEl = document.getElementById('pd-ack-count-number');
            const resolvedCountEl = document.getElementById('pd-resolved-count-number');
            
            if (triggeredCountEl) triggeredCountEl.textContent = triggered;
            if (ackCountEl) ackCountEl.textContent = acknowledged;
            if (resolvedCountEl) resolvedCountEl.textContent = resolved;
            
            // Update summary background color and blink based on status
            const summaryElement = document.getElementById('pd-summary');
            if (summaryElement) {
                if (data.error) {
                    summaryElement.style.background = '#dc2626';
                    summaryElement.classList.add('pd-status-blink');
                } else if (triggered > 0) {
                    // Red + blink for triggered incidents
                    summaryElement.style.background = '#dc2626';
                    summaryElement.classList.add('pd-status-blink');
                } else if (acknowledged > 0) {
                    // Yellow/Orange + blink for acknowledged incidents
                    summaryElement.style.background = '#f59e0b';
                    summaryElement.classList.add('pd-status-blink');
                } else {
                    // Green + no blink for healthy
                    summaryElement.style.background = '#10b981';
                    summaryElement.classList.remove('pd-status-blink');
                }
            }
            
            // Update active incidents
            const activeElement = document.getElementById('pd-active');
            if (activeElement) {
                if (data.error) {
                    activeElement.innerHTML = '<li style="color: #f56565; border-left-color: #f56565;">⚠️ Unable to load</li>';
                } else if (!data.active || data.active.length === 0) {
                    activeElement.innerHTML = '<li style="color: #48bb78; border-left-color: #48bb78;">✅ No active incidents</li>';
                } else {
                    activeElement.innerHTML = data.active.map(inc => {
                        const st = (inc && inc.status) ? String(inc.status).toLowerCase() : 'unknown';
                        const statusClass = st;
                        const icon = st === 'triggered' ? '🔴' : '🟡';
                        const url = (inc && inc.url) ? inc.url : '#';
                        const title = inc && inc.title != null ? inc.title : '';
                        const num = inc && inc.number != null ? inc.number : '—';
                        const svc = inc && inc.service != null ? inc.service : '';
                        return `
                            <li class="${statusClass}" title="${String(title).replace(/"/g, '&quot;')}" onclick="window.open('${String(url).replace(/'/g, '%27')}', '_blank')" style="cursor: pointer;">
                                <strong>${icon} #${num}</strong>
                                <div style="color: var(--text-secondary); font-size: 10px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-top: 2px;">
                                    ${svc}
                                </div>
                            </li>
                        `;
                    }).join('');
                }
            }
            
            // Update resolved incidents
            const resolvedElement = document.getElementById('pd-resolved');
            if (resolvedElement) {
                if (data.error) {
                    resolvedElement.innerHTML = '<li style="color: #f56565; border-left-color: #f56565;">⚠️ Unable to load</li>';
                } else if (!data.recently_resolved || data.recently_resolved.length === 0) {
                    resolvedElement.innerHTML = '<li style="color: #999;">No recent resolutions</li>';
                } else {
                    const resolvedList = (data.recently_resolved || []).slice(0, 3);
                    resolvedElement.innerHTML = resolvedList.map(inc => {
                        const url = inc.url || '#';
                        return `
                            <li class="resolved" title="${inc.title}" onclick="window.open('${url}', '_blank')" style="cursor: pointer;">
                                <strong>🟢 #${inc.number}</strong>
                                <div style="color: var(--text-secondary); font-size: 10px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-top: 2px;">
                                    ${inc.service}
                                </div>
                            </li>
                        `;
                    }).join('');
                }
            }
}

const SIDEBAR_WIDGET_CACHE_TTL_MS = 350000;
/** Confluence Team Calendar API is often slow; cache deployments longer to avoid repeated waits. */
const DEPLOYMENTS_WIDGET_CACHE_TTL_MS = 350000;

function setDeploymentsLoading(loading) {
    const summary = document.getElementById('deployments-summary');
    const list = document.getElementById('deployments-list');
    const btn = document.getElementById('deployments-refresh-btn');
    if (btn) {
        btn.disabled = !!loading;
        btn.style.opacity = loading ? '0.55' : '1';
        btn.style.cursor = loading ? 'wait' : 'pointer';
    }
    if (!loading) {
        return;
    }
    if (summary) {
        summary.innerHTML =
            '<div style="font-size:11px;line-height:1.45;opacity:0.95;">⏳ Loading GRM calendar from Confluence…<br><span style="font-size:9px;opacity:0.88;">This can take 10–30s when Atlassian is busy.</span></div>';
    }
    if (list) {
        list.innerHTML =
            '<li style="padding:8px;color:var(--text-secondary);font-size:10px;line-height:1.4;">Waiting for Team Calendar API…</li>';
    }
}

function _splunkOutlierUiColors() {
    const dark = document.documentElement.getAttribute('data-theme') === 'dark';
    if (dark) {
        return {
            metricLine: '#f8fafc',
            metricMuted: '#e2e8f0',
            zoneOk: '#bbf7d0',
            zoneBad: '#fecaca',
            summaryStrong: '#ffffff',
        };
    }
    return {
        metricLine: '#0f172a',
        metricMuted: '#334155',
        zoneOk: '#14532d',
        zoneBad: '#991b1b',
        summaryStrong: '#ffffff',
    };
}

function _splunkEscAttr(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/"/g, '&quot;');
}

function _splunkZoneHoverTitle(label, t) {
    const zones = (t && t.zones) ? t.zones : [];
    const zmap = {};
    zones.forEach(function (z) {
        if (z && z.zone) zmap[z.zone] = z;
    });
    const lines = [label + ' — outliers by zone (LLP predict):'];
    ['z1', 'z2', 'z3', 'z4'].forEach(function (zn) {
        const z = zmap[zn] || {};
        const o = Number(z.outliers) || 0;
        if (z.error) {
            lines.push('  ' + zn.toUpperCase() + ': data unavailable');
        } else {
            lines.push('  ' + zn.toUpperCase() + ': ' + o + ' outlier(s)');
        }
    });
    return lines.join('\n');
}

function buildSplunkSemaphoreRowHtml(data) {
    const tools = data.tools || [];
    const byId = {};
    tools.forEach(function (t) {
        if (t && t.id) byId[t.id] = t;
    });
    function dashUrl(id, fallback) {
        const tt = byId[id];
        return (tt && tt.dashboard_url) ? tt.dashboard_url : fallback;
    }
    function light(shortLabel, longLabel, id, fallbackUrl) {
        const t = byId[id];
        const url = dashUrl(id, fallbackUrl);
        const tot = (t && t.total_outliers != null) ? Number(t.total_outliers) : 0;
        const title = _splunkZoneHoverTitle(longLabel, t);
        const dotBg = tot > 0 ? '#ef4444' : '#22c55e';
        const dotSh = tot > 0 ? 'rgba(239,68,68,0.45)' : 'rgba(34,197,94,0.45)';
        return (
            '<a href="' + url + '" target="_blank" rel="noopener" title="' + _splunkEscAttr(title) + '" ' +
            'style="display:inline-flex;flex-direction:column;align-items:center;gap:2px;text-decoration:none;color:inherit;min-width:38px;">' +
            '<span style="width:11px;height:11px;border-radius:50%;background:' + dotBg + ';box-shadow:0 0 6px ' + dotSh + ';"></span>' +
            '<span style="font-size:8px;font-weight:800;opacity:0.9;line-height:1;">' + _splunkEscAttr(shortLabel) + '</span>' +
            '<span style="font-size:10px;font-weight:900;">' + tot + '</span></a>'
        );
    }
    const sep = '<span style="opacity:0.35;font-size:10px;padding:0 2px;">|</span>';
    return (
        '<div style="display:flex;flex-wrap:wrap;align-items:flex-end;justify-content:center;gap:4px 8px;padding:4px 2px;">' +
        light('Str', 'Streaming', 'p0_streaming', 'https://arlo.splunkcloud.com/en-US/app/arlo_sre/p0_streaming_dashboard') + sep +
        light('CVR', 'CVR', 'p0_cvr', 'https://arlo.splunkcloud.com/en-US/app/arlo_sre/p0_cvr_dashboard') + sep +
        light('ADT', 'ADT', 'p0_adt', 'https://arlo.splunkcloud.com/en-US/app/search/p0_streaming_dashboard_pp') + sep +
        light('US', 'US infra', 'p0_streaming_us_infra', 'https://arlo.splunkcloud.com/en-US/app/arlo_sre/p0_streaming_dashboard__us_infra') +
        '</div>'
    );
}

function applySplunkOutliersMonitorPayload(data) {
    const splUi = _splunkOutlierUiColors();
    const timeEl = document.getElementById('spl-time');
    if (timeEl) {
        const now = new Date();
        timeEl.textContent = 'Last updated: ' + now.toLocaleTimeString('en-US', {
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit'
        });
    }
    const summary = document.getElementById('spl-summary');
    const summaryBody = document.getElementById('spl-summary-body');
    const listEl = document.getElementById('spl-tools');
    if (!listEl) return;

    if (!data || data.success === false) {
        if (summary) summary.style.background = '#7f1d1d';
        if (summaryBody) {
            summaryBody.textContent = (data && data.error) ? data.error : 'Unable to load Splunk data';
        }
        listEl.innerHTML = '<li style="padding:6px;color:#f87171;">⚠️ Check SPLUNK_TOKEN / network</li>';
        return;
    }

    const tools = data.tools || [];
    let grand = 0;
    tools.forEach((t) => { grand += Number(t.total_outliers) || 0; });
    if (summary) {
        summary.style.background = grand > 0 ? '#7f1d1d' : '#14532d';
    }
    if (summaryBody) {
        const tr = data.timerange_hours != null ? data.timerange_hours : '—';
        summaryBody.innerHTML =
            '<div style="display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap;">' +
            '<span>Σ total: <strong style="color:' + splUi.summaryStrong + ';font-size:1.2em;">' + grand + '</strong></span>' +
            '<span style="font-size:9px;opacity:0.9;">' + tr + 'h LLP</span></div>';
    }

    if (!tools.length) {
        listEl.innerHTML = '<li style="padding:6px;color:#999;">No tools configured</li>';
        return;
    }

    listEl.innerHTML = '<li style="padding:0;margin:0;list-style:none;">' + buildSplunkSemaphoreRowHtml(data) + '</li>';
}

function loadSplunkOutliersMonitor(forceRefresh) {
    const C = typeof SessionDataCache !== 'undefined' ? SessionDataCache : null;
    const ck = 'splunk_outliers_monitor';
    if (!forceRefresh && C) {
        const hit = C.get(ck);
        if (hit) {
            applySplunkOutliersMonitorPayload(hit);
            return;
        }
    }
    fetch('/api/splunk/monitor')
        .then(function (res) {
            if (!res.ok) throw new Error('Splunk monitor HTTP ' + res.status);
            return res.json();
        })
        .then(function (data) {
            if (C) {
                C.set(ck, data, SIDEBAR_WIDGET_CACHE_TTL_MS);
            }
            applySplunkOutliersMonitorPayload(data);
        })
        .catch(function (err) {
            console.error('Splunk outliers monitor:', err);
            applySplunkOutliersMonitorPayload({ success: false, error: err.message || String(err) });
        });
}

function loadPagerDutyMonitor(forceRefresh) {
    const C = typeof SessionDataCache !== 'undefined' ? SessionDataCache : null;
    const ck = 'pagerduty_monitor';
    if (!forceRefresh && C) {
        const hit = C.get(ck);
        if (hit) {
            applyPagerDutyMonitorPayload(hit);
            return;
        }
    }
    fetch('/api/pagerduty/monitor')
        .then(res => {
            if (!res.ok) throw new Error('Error loading PagerDuty data');
            return res.json();
        })
        .then(data => {
            if (C) {
                C.set(ck, data, SIDEBAR_WIDGET_CACHE_TTL_MS);
            }
            applyPagerDutyMonitorPayload(data);
        })
        .catch(err => {
            console.error('Error loading PagerDuty monitor:', err);
            const summaryElement = document.getElementById('pd-summary');
            if (summaryElement) {
                summaryElement.innerHTML = '<span style="color: #fee;">⚠️ Connection error</span>';
            }
        });
}

function applyDeploymentsPayload(data) {
            function deploymentEnd(deployment) {
                if (deployment.end_timestamp) {
                    return new Date(deployment.end_timestamp);
                }
                return new Date(new Date(deployment.timestamp).getTime() + (2 * 60 * 60 * 1000));
            }
            const deploymentsList = Array.isArray(data.deployments) ? data.deployments : [];
            // Update timestamp
            const timeElement = document.getElementById('deployments-time');
            if (timeElement) {
                const now = new Date();
                const timeString = now.toLocaleTimeString('en-US', {
                    hour: '2-digit',
                    minute: '2-digit',
                    second: '2-digit'
                });
                timeElement.textContent = `Last updated: ${timeString}`;
            }
            
            // In progress = now inside [start, end] from API (or default 2h window)
            const now = new Date();
            let currentDeployment = null;
            
            if (deploymentsList.length > 0) {
                for (const deployment of deploymentsList) {
                    const deployTime = new Date(deployment.timestamp);
                    const deployEndTime = deploymentEnd(deployment);
                    
                    if (now >= deployTime && now <= deployEndTime) {
                        currentDeployment = deployment;
                        break;
                    }
                }
            }
            
            // Update current deployment banner
            const currentElement = document.getElementById('deployments-current');
            const currentNameElement = document.getElementById('current-deployment-name');
            if (currentElement && currentNameElement) {
                if (currentDeployment) {
                    currentNameElement.textContent = currentDeployment.service;
                    currentElement.style.display = 'block';
                } else {
                    currentElement.style.display = 'none';
                }
            }
            
            // Update summary
            const summaryElement = document.getElementById('deployments-summary');
            if (summaryElement) {
                if (data.error) {
                    summaryElement.innerHTML = `<span style="color: #fee;">⚠️ ${data.error}</span>`;
                } else {
                    // Past = window ended. Upcoming = start time still in the future (not in progress).
                    const pastCount = deploymentsList.filter(d => deploymentEnd(d) < now).length;
                    const upcomingCount = deploymentsList.filter(d => {
                        const start = new Date(d.timestamp);
                        return start > now;
                    }).length;
                    const warnColor = data.source === 'no_credentials' ? '#f87171' : '#f59e0b';
                    const warn = data.warning
                        ? `<div style="font-size: 9px; color: ${warnColor}; margin-top: 4px; text-align: center; line-height: 1.3;">${data.source === 'mock' ? '🧪 Demo data' : '⚠️ ' + data.warning}</div>`
                        : (data.source === 'mock' ? '<div style="font-size: 9px; color: #f59e0b;">🧪 Demo data</div>' : '');
                    
                    summaryElement.innerHTML = `
                        <div style="display: flex; justify-content: center; align-items: center; gap: 8px;">
                            <div style="font-weight: bold; font-size: 20px;">${upcomingCount}</div>
                            <div style="font-size: 11px; opacity: 0.9;">upcoming</div>
                            ${pastCount > 0 ? `<div style="font-size: 10px; opacity: 0.7;"> | ${pastCount} past</div>` : ''}
                        </div>
                        ${warn}
                    `;
                }
            }
            
            // Update deployments list
            const listElement = document.getElementById('deployments-list');
            if (listElement) {
                if (data.error) {
                    listElement.innerHTML = '<li style="color: #f56565; border-left-color: #f56565;">⚠️ Unable to load</li>';
                } else if (deploymentsList.length === 0) {
                    listElement.innerHTML = '<li style="color: #999;">No deployments found</li>';
                } else {
                    listElement.innerHTML = deploymentsList.map(deployment => {
                        const deployTime = new Date(deployment.timestamp);
                        const deployEndTime = deploymentEnd(deployment);
                        const isActive = currentDeployment && currentDeployment.timestamp === deployment.timestamp;
                        const isPast = deployEndTime < now;
                        
                        // Color scheme based on status
                        let borderColor, bgColor, textColor, opacity;
                        
                        if (isPast) {
                            // Past deployments in gray
                            borderColor = '#9ca3af';
                            bgColor = 'rgba(156, 163, 175, 0.1)';
                            textColor = '#6b7280';
                            opacity = '0.7';
                        } else if (isActive) {
                            // Active deployment in orange
                            borderColor = '#f59e0b';
                            bgColor = 'rgba(245, 158, 11, 0.1)';
                            textColor = '#f59e0b';
                            opacity = '1';
                        } else {
                            // Future deployments in blue
                            borderColor = '#3b82f6';
                            bgColor = 'var(--bg-tertiary)';
                            textColor = '#3b82f6';
                            opacity = '1';
                        }
                        
                        
                        // Format times (America/Chicago for deploy schedule)
                        const startTimeStr = deployTime.toLocaleTimeString('en-US', {
                            timeZone: 'America/Chicago',
                            hour: '2-digit',
                            minute: '2-digit',
                            hour12: true
                        });
                        const endTimeStr = deployEndTime.toLocaleTimeString('en-US', {
                            timeZone: 'America/Chicago',
                            hour: '2-digit',
                            minute: '2-digit',
                            hour12: true
                        });
                        
                        // Check if deployment is today or tomorrow (same TZ as schedule)
                        const cstNow = new Date(now.toLocaleString('en-US', {timeZone: 'America/Chicago'}));
                        const nowDate = cstNow.toDateString();
                        const deployDate = new Date(deployTime.toLocaleString('en-US', {timeZone: 'America/Chicago'})).toDateString();
                        const tomorrow = new Date(cstNow);
                        tomorrow.setDate(tomorrow.getDate() + 1);
                        const tomorrowDate = tomorrow.toDateString();
                        
                        let dateLabel;
                        if (deployDate === nowDate) {
                            dateLabel = (isPast ? '✓ ' : '') + 'Today ' + startTimeStr + ' - ' + endTimeStr;
                        } else if (deployDate === tomorrowDate) {
                            dateLabel = 'Tomorrow ' + startTimeStr + ' - ' + endTimeStr;
                        } else {
                            const dateStr = deployTime.toLocaleDateString('en-US', {
                                timeZone: 'America/Chicago',
                                month: 'short',
                                day: 'numeric'
                            });
                            dateLabel = (isPast ? '✓ ' : '') + dateStr + ' ' + startTimeStr + ' - ' + endTimeStr;
                        }
                        
                        return `
                            <li style="padding: 6px; margin-bottom: 4px; background: ${bgColor}; border-radius: 4px; border-left: 3px solid ${borderColor}; opacity: ${opacity};">
                                <div style="font-weight: bold; color: ${textColor}; margin-bottom: 2px; font-size: 11px;">
                                    ${isActive ? '🔴 ' : ''}${dateLabel}
                                </div>
                                <div style="color: ${isPast ? '#9ca3af' : 'var(--text-secondary)'}; font-size: 10px; line-height: 1.4; ${isPast ? 'text-decoration: line-through;' : ''}">
                                    ${deployment.service}
                                </div>
                            </li>
                        `;
                    }).join('');
                }
            }
}

function loadUpcomingDeployments(forceRefresh) {
    const C = typeof SessionDataCache !== 'undefined' ? SessionDataCache : null;
    const ck = 'deployments_upcoming_v4_24h';
    if (!forceRefresh && C) {
        const hit = C.get(ck);
        if (hit) {
            applyDeploymentsPayload(hit);
            return;
        }
    }
    setDeploymentsLoading(true);
    const ac = new AbortController();
    const abortTimer = setTimeout(function () {
        ac.abort();
    }, 55000);
    fetch('/api/deployments/upcoming', { signal: ac.signal })
        .then(res => {
            if (!res.ok) throw new Error('Error loading deployments data');
            return res.json();
        })
        .then(data => {
            if (C) {
                C.set(ck, data, DEPLOYMENTS_WIDGET_CACHE_TTL_MS);
            }
            applyDeploymentsPayload(data);
        })
        .catch(err => {
            console.error('Error loading deployments:', err);
            const summaryElement = document.getElementById('deployments-summary');
            if (summaryElement) {
                const msg =
                    err && err.name === 'AbortError'
                        ? 'Request timed out — Confluence calendar took too long. Try again.'
                        : 'Connection error — check network or try again.';
                summaryElement.innerHTML = `<span style="color: #fee;">⚠️ ${msg}</span>`;
            }
            const listElement = document.getElementById('deployments-list');
            if (listElement) {
                listElement.innerHTML =
                    '<li style="color:#f56565;font-size:10px;">Could not load deployments.</li>';
            }
        })
        .finally(() => {
            clearTimeout(abortTimer);
            const btn = document.getElementById('deployments-refresh-btn');
            if (btn) {
                btn.disabled = false;
                btn.style.opacity = '1';
                btn.style.cursor = 'pointer';
            }
        });
}

function applyArloStatusMonitorPayload(data) {
            // Update timestamp
            const timeElement = document.getElementById('status-time');
            if (timeElement) {
                timeElement.textContent = `(${data.timestamp || ''})`;
            }
            
            // Update summary
            const summaryElement = document.getElementById('status-summary');
            if (summaryElement) {
                if (data.error) {
                    summaryElement.innerHTML = `<span style="color: #f56565;">⚠️ ${data.error}</span>`;
                } else {
                    const isOperational = data.summary.toLowerCase().includes('operational');
                    const color = isOperational ? '#48bb78' : '#f56565';
                    const icon = isOperational ? '✅' : '⚠️';
                    summaryElement.innerHTML = `<span style="color: ${color};">${icon} ${data.summary}</span>`;
                }
            }
            
            // Update core services (compact boxes like status monitor)
            const servicesElement = document.getElementById('status-services');
            let servicesDown = 0;
            
            if (servicesElement) {
                if (data.error) {
                    servicesElement.innerHTML = '<div style="grid-column: 1 / -1; text-align: center; padding: 12px; color: #f56565; font-size: 11px;">Unable to load</div>';
                } else if (!data.services || data.services.length === 0) {
                    servicesElement.innerHTML = '<div style="grid-column: 1 / -1; text-align: center; padding: 12px; color: #6b7280; font-size: 11px;">No services data</div>';
                } else {
                    servicesElement.innerHTML = data.services.map(svc => {
                        const isAllGood = svc.status.trim().toLowerCase() === 'all good';
                        if (!isAllGood) {
                            servicesDown++;
                        }
                        const bgColor = isAllGood ? '#10b981' : '#dc2626';
                        const shortName = svc.service.replace('Live ', '').replace('Video ', '');
                        return `
                            <div style="background: ${bgColor}; padding: 7px 8px; border-radius: 5px; text-align: center;">
                                <div style="font-size: 10px; color: white; font-weight: 700; line-height: 1.2; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; letter-spacing: -0.01em;">${shortName}</div>
                            </div>
                        `;
                    }).join('');
                }
            }
            
            // Note: Arlo status is now displayed separately in its own widget
            // No longer affects PagerDuty display
            
            // Update incidents
            const incidentsElement = document.getElementById('status-incidents');
            if (incidentsElement) {
                if (data.error) {
                    incidentsElement.innerHTML = '<li style="color: #f56565;">Unable to load</li>';
                } else if (!data.incidents || data.incidents.length === 0) {
                    incidentsElement.innerHTML = '<li style="color: #48bb78;">✅ No recent incidents</li>';
                } else {
                    incidentsElement.innerHTML = data.incidents.map(inc => `
                        <li>
                            <strong>${inc.date}</strong>
                            <span style="color: #bbb;">${inc.detail}</span>
                        </li>
                    `).join('');
                }
            }
            
            console.log('✅ Status monitor updated');
            updateLastUpdateTime();
}

function loadStatusMonitor(forceRefresh) {
    const C = typeof SessionDataCache !== 'undefined' ? SessionDataCache : null;
    const ck = 'arlo_status_monitor';
    if (!forceRefresh && C) {
        const hit = C.get(ck);
        if (hit) {
            applyArloStatusMonitorPayload(hit);
            return;
        }
    }
    fetch('/api/status/monitor')
        .then(res => {
            if (!res.ok) throw new Error('Error loading status');
            return res.json();
        })
        .then(data => {
            if (C) {
                C.set(ck, data, SIDEBAR_WIDGET_CACHE_TTL_MS);
            }
            applyArloStatusMonitorPayload(data);
        })
        .catch(err => {
            console.error('Error loading status monitor:', err);
            const summaryElement = document.getElementById('status-summary');
            if (summaryElement) {
                summaryElement.innerHTML = '<span style="color: #f56565;">⚠️ Connection error</span>';
            }
        });
}

// Page load initialization
document.addEventListener('DOMContentLoaded', () => {
    console.log('🚀 Initializing OneView GOC AI v2.0...');
    
    // Load saved theme
    loadSavedTheme();
    
    // Defer history fetch so first paint + /api/tools are not competing for the network
    const bootHistory = () => loadHistory();
    if (typeof requestIdleCallback === 'function') {
        requestIdleCallback(bootHistory, { timeout: 800 });
    } else {
        setTimeout(bootHistory, 100);
    }
    
    // Setup history search
    setTimeout(setupHistorySearch, 1000);
    
    // Environment hub on main chat (above How to use)
    if (document.getElementById('env-hub-home-grid')) {
        const bootEnvHub = () => loadHomeEnvironmentHub(false);
        if (typeof requestIdleCallback === 'function') {
            requestIdleCallback(bootEnvHub, { timeout: 1200 });
        } else {
            setTimeout(bootEnvHub, 150);
        }
        setInterval(() => loadHomeEnvironmentHub(false), HOME_ENV_HUB_REFRESH_MS);
    }

    // Sidebar widgets: defer until idle so first paint + /api/tools are not blocked
    const runSidebarMonitors = () => {
        loadStatusMonitor();
        loadPagerDutyMonitor();
        loadSplunkOutliersMonitor();
        loadUpcomingDeployments();
    };
    if (typeof requestIdleCallback === 'function') {
        requestIdleCallback(runSidebarMonitors, { timeout: 2000 });
    } else {
        setTimeout(runSidebarMonitors, 0);
    }
    
    // Auto-refresh status widgets every 6 minutes
    setInterval(loadStatusMonitor, 360000);
    setInterval(loadPagerDutyMonitor, 360000);
    setInterval(loadSplunkOutliersMonitor, 360000);
    setInterval(loadUpcomingDeployments, 360000);
    
    // Update timestamp initially
    updateLastUpdateTime();

    // Cargar herramientas desde API
    fetch('/api/tools')
        .then(res => {
            if (!res.ok) throw new Error('Error loading tools');
            return res.json();
        })
        .then(data => {
            const toolList = document.getElementById('tool-list');
            if (!toolList) {
                console.error('Tool list container not found');
                return;
            }
            
            toolList.innerHTML = '';
            
            if (!data || data.length === 0) {
                toolList.innerHTML = '<p style="color: #666; padding: 10px;">No tools available</p>';
                return;
            }
            
            // Function to get logo for section
            function getSectionLogo(sectionKey) {
                const logos = {
                    'datadog': '<img src="/static/images/logos/datadog.svg" class="section-logo" alt="Datadog">',
                    'splunk': '<img src="/static/images/logos/splunk.svg" class="section-logo" alt="Splunk">',
                    'pagerduty': '<img src="/static/images/logos/pagerduty.svg" class="section-logo" alt="PagerDuty">',
                    'confluence': '<img src="/static/images/logos/confluence.svg" class="section-logo" alt="Confluence">',
                    'slack': '<img src="/static/images/logos/slack.svg" class="section-logo" alt="Slack">',
                    'other': '🔧'
                };
                return logos[sectionKey] || '🔧';
            }
            
            // Function to categorize tools
            function categorizeTool(toolName) {
                if (toolName.startsWith('DD_') || toolName.includes('Datadog')) {
                    return 'datadog';
                } else if (toolName.startsWith('P0_') || toolName.includes('Splunk')) {
                    return 'splunk';
                } else if (toolName.includes('PagerDuty')) {
                    return 'pagerduty';
                } else if (
                    toolName === 'Wiki' ||
                    toolName === 'Owners' ||
                    toolName === 'Arlo_Versions' ||
                    toolName === 'Deployed_FW_Versions' ||
                    toolName === 'Holiday_Oncall'
                ) {
                    return 'confluence';
                } else if (toolName.includes('Slack')) {
                    return 'slack';
                } else if (toolName === 'Ask_ARLOCHAT') {
                    return 'other';
                }
                return 'other';
            }
            
            // Group tools by category
            const groupedTools = {
                confluence: [],
                datadog: [],
                pagerduty: [],
                splunk: [],
                slack: [],
                other: []
            };
            
            data.forEach(tool => {
                const category = categorizeTool(tool.name);
                groupedTools[category].push(tool);
            });
            
            // Create sections with icons and names
            const sections = [
                { key: 'confluence', title: 'Confluence', color: '#0052CC' },
                { key: 'datadog', title: 'Datadog', color: '#632CA6' },
                { key: 'pagerduty', title: 'PagerDuty', color: '#06AC38' },
                { key: 'splunk', title: 'Splunk', color: '#000000' },
                { key: 'slack', title: 'Slack', color: '#4A154B' },
                { key: 'other', title: 'Others', color: '#6b7280' }
            ];
            
            sections.forEach(section => {
                const tools = groupedTools[section.key];
                if (tools.length === 0) return; // Skip empty sections
                
                // Create dropdown container
                const dropdownDiv = document.createElement('div');
                dropdownDiv.className = 'tool-dropdown';
                
                // Create dropdown header
                const header = document.createElement('div');
                header.className = 'tool-dropdown-header';
                header.style.borderLeftColor = section.color;
                const dropdownId = `dropdown-${section.key}`;
                const selectAllId = `select-all-${section.key}`;
                const logo = getSectionLogo(section.key);
                header.innerHTML = `
                    <span class="tool-dropdown-icon" onclick="toggleToolDropdown('${dropdownId}', event)">${logo}</span>
                    <span class="tool-dropdown-title" onclick="toggleToolDropdown('${dropdownId}', event)">${section.title}</span>
                    <span class="tool-dropdown-toggle" onclick="toggleToolDropdown('${dropdownId}', event)">
                        ▼
                    </span>
                `;
                dropdownDiv.appendChild(header);
                
                // Collapsed by default
                const content = document.createElement('div');
                content.className = 'tool-dropdown-content';
                content.id = dropdownId;
                
                // Create tools container
                const toolsContainer = document.createElement('div');
                toolsContainer.className = 'tool-dropdown-items';
                
                // Add "Select All" checkbox at the top with main indentation
                const selectAllLabel = document.createElement('label');
                selectAllLabel.className = 'tool-item tool-item-main';
                selectAllLabel.innerHTML = `
                    <input type="checkbox" id="${selectAllId}" onchange="toggleSelectAll('${section.key}', this.checked)">
                    <span class="tool-item-text" style="font-weight: 700;">Select All</span>
                `;
                toolsContainer.appendChild(selectAllLabel);

                function createToolCheckboxLabel(tool) {
                    const label = document.createElement('label');
                    label.className = 'tool-item tool-item-sub';
                    label.title = tool.desc || tool.name || '';
                    const displayName = tool.name === 'Ask_ARLOCHAT' ? 'MCP_ARLO' : tool.name;
                    const input = document.createElement('input');
                    input.type = 'checkbox';
                    input.name = 'tool';
                    input.value = tool.name || '';
                    const span = document.createElement('span');
                    span.className = 'tool-item-text';
                    span.textContent = displayName;
                    label.appendChild(input);
                    label.appendChild(span);
                    return label;
                }

                // List every tool in this section (no "Show N more" fold) so nothing looks missing.
                const previewWrap = document.createElement('div');
                previewWrap.className = 'tool-items-preview';
                tools.forEach(tool => {
                    previewWrap.appendChild(createToolCheckboxLabel(tool));
                });
                toolsContainer.appendChild(previewWrap);
                
                content.appendChild(toolsContainer);
                dropdownDiv.appendChild(content);
                toolList.appendChild(dropdownDiv);
            });
            
            console.log(`✅ Loaded ${data.length} tools in ${sections.filter(s => groupedTools[s.key].length > 0).length} sections`);
            
            // Add event listeners to show/hide timerange selector
            setupTimeRangeSelector();
            
        })
        .catch(err => {
            console.error('Error loading tools:', err);
            const toolList = document.getElementById('tool-list');
            if (toolList) {
                toolList.innerHTML = '<p style="color: #f56565; padding: 10px;">⚠️ Error loading tools. Please refresh the page.</p>';
            }
        });

    // Add event listener for AI auto-select button
    const autoSelectBtn = document.getElementById('auto-select-btn');
    if (autoSelectBtn) {
        autoSelectBtn.addEventListener('click', autoSelectTools);
        console.log('✅ AI Auto-Select button initialized');
    }

    // Form submit handler
    document.getElementById('search-form').addEventListener('submit', e => {
        e.preventDefault();
        const inputText = document.getElementById('input-text').value;
        const selectedTools = Array.from(document.querySelectorAll('input[name=tool]:checked')).map(el => el.value);
        console.log(selectedTools); // mejor que print()

        if (selectedTools.length === 0) {
            alert('⚠️ Please select at least one tool before submitting.');
            return; // Stop execution
        }

        showLoading(selectedTools);

        // Get timerange value if visible
        const timerangeSelect = document.getElementById('timerange-select');
        const timerangeContainer = document.getElementById('timerange-container');
        let timerange = 4; // default 4 hours
        if (timerangeContainer && timerangeContainer.style.display !== 'none' && timerangeSelect) {
            timerange = parseInt(timerangeSelect.value);
        }

        fetch('/api/run', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ input: inputText, tools: selectedTools, timerange: timerange })
        })
            .then(res => {
                if (!res.ok) throw new Error(`Server error: ${res.status}`);
                return res.json();
            })
            .then(async (data) => {
                // Limpiar mensajes de carga
                clearInterval(counterInterval);
                hideLoadingOverlay();
                document.getElementById('loading-message').innerHTML = '';
                
                // Show results
                const resultsBox = document.getElementById('results-box');
                const htmlContent = data.result || '<p>No results returned</p>';
                
                try {
                    await ensureChartJs();
                } catch (e) {
                    console.warn('Chart.js preload:', e);
                }
                resultsBox.innerHTML = htmlContent;
                
                // Add action buttons to results
                setTimeout(() => {
                    addResultActions(resultsBox);
                }, 500);
                
                // Run scripts inserted via innerHTML
                const scripts = resultsBox.querySelectorAll('script');
                
                // Execute scripts sequentially (wait for external scripts to load)
                let scriptIndex = 0;
                function executeNextScript() {
                    if (scriptIndex >= scripts.length) {
                        requestAnimationFrame(function () {
                            requestAnimationFrame(function () {
                                resizeSplunkChartsIn(resultsBox);
                            });
                        });
                        return;
                    }
                    
                    const oldScript = scripts[scriptIndex];
                    const newScript = document.createElement('script');
                    
                    // Copy attributes (especially src for external scripts)
                    Array.from(oldScript.attributes).forEach(attr => {
                        newScript.setAttribute(attr.name, attr.value);
                    });
                    
                    // Copy inline script content
                    if (oldScript.textContent) {
                        newScript.textContent = oldScript.textContent;
                    }
                    
                    // Handle script loading
                    if (newScript.src) {
                        // External script - wait for it to load
                        newScript.onload = () => {
                            scriptIndex++;
                            executeNextScript();
                        };
                        newScript.onerror = () => {
                            console.error(`Failed to load script ${scriptIndex + 1}`);
                            scriptIndex++;
                            executeNextScript();
                        };
                    } else {
                        // Inline script - executes immediately
                        scriptIndex++;
                        setTimeout(executeNextScript, 30); // Delay for DOM updates
                    }
                    
                    // Append to document so it executes
                    document.body.appendChild(newScript);
                    
                    // Remover el script viejo
                    oldScript.remove();
                }
                
                executeNextScript();
                
                // Show execution time
                const execTime = data.exec_time || '0';
                document.getElementById('final-counter').innerText = `⏱ Execution time: ${execTime}s`;
                
                // Reload history after a short delay so the backend can persist
                setTimeout(() => {
                    loadHistory(); // refresh history
                    console.log('✅ Query completed, history refreshed');
                }, 500);
                
                // Show download button
                const downloadContainer = document.getElementById('download-container');
                if (downloadContainer) {
                    downloadContainer.style.display = 'block';
                }
                
                // Scroll to results
                resultsBox.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            })
            .catch(err => {
                console.error('Error executing query:', err);
                clearInterval(counterInterval);
                hideLoadingOverlay();
                document.getElementById('loading-message').innerHTML = '';
                document.getElementById('results-box').innerHTML = `
                    <div style="padding: 20px; background-color: #fff3cd; border-left: 4px solid #ffc107; border-radius: 4px;">
                        <h4 style="color: #856404; margin-top: 0;">⚠️ Error</h4>
                        <p style="color: #856404;">${err.message || err}</p>
                        <p style="color: #666; font-size: 12px;">Please check your connection and try again.</p>
                    </div>
                `;
            });
    });
    
    console.log('✅ Event listeners attached');
});

// Download results as Word document
async function downloadResults() {
    const resultsBox = document.getElementById('results-box');
    if (!resultsBox || resultsBox.innerHTML === '') {
        showNotification('No results to download');
        return;
    }
    
    try {
        showNotification('📸 Capturing screenshot...');
        await ensureHtml2Canvas();
        const h2c = typeof window.html2canvas === 'function' ? window.html2canvas : null;
        if (!h2c) {
            showNotification('❌ html2canvas failed to load.');
            return;
        }
        const canvas = await h2c(resultsBox, {
            backgroundColor: '#ffffff',
            scale: 2, // Higher quality (2x resolution)
            logging: false,
            useCORS: true,
            allowTaint: true,
            windowWidth: resultsBox.scrollWidth,
            windowHeight: resultsBox.scrollHeight
        });
        
        // Convert canvas to base64 image
        const imageData = canvas.toDataURL('image/png');
        
        showNotification('📄 Generating document...');
        
        // Send to backend for document generation
        const response = await fetch('/api/download/docx', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                screenshot_image: imageData
            })
        });
        
        if (!response.ok) {
            throw new Error('Failed to generate document');
        }
        
        const blob = await response.blob();
        
        // Create download link
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.style.display = 'none';
        a.href = url;
        
        // Generate filename with timestamp
        const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, -5);
        a.download = `arlo_agenticai_results_${timestamp}.docx`;
        
        document.body.appendChild(a);
        a.click();
        
        window.URL.revokeObjectURL(url);
        document.body.removeChild(a);
        
        showNotification('✅ Document downloaded successfully!');
    } catch (err) {
        console.error('Error downloading document:', err);
        showNotification('❌ Error downloading document: ' + err.message);
    }
}
