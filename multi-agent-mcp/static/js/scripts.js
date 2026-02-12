// Scripts.js loaded - Version 20260209-v20 (Enhanced UI with theme toggle, tooltips, search, etc.)
let counterInterval;
let startTime;

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
    const savedTheme = localStorage.getItem('theme') || 'dark';
    document.documentElement.setAttribute('data-theme', savedTheme);
}

// ============================================
// HISTORY MANAGEMENT
// ============================================
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
        <button class="result-action-btn" onclick="copyResultsToClipboard()" title="Copy results">
            📋 Copy
        </button>
        <button class="result-action-btn" onclick="expandAllSections()" title="Expand all sections">
            📖 Expand All
        </button>
        <button class="result-action-btn" onclick="collapseAllSections()" title="Collapse all sections">
            📕 Collapse All
        </button>
    `;
    
    resultsBox.style.position = 'relative';
    resultsBox.insertBefore(actionsDiv, resultsBox.firstChild);
}

function copyResultsToClipboard() {
    const resultsBox = document.getElementById('results-box');
    if (resultsBox) {
        const text = resultsBox.innerText;
        copyToClipboard(text);
    }
}

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
                              selectedTools.includes('P0_Streaming') ||
                              selectedTools.includes('P0_CVR_Streaming') ||
                              selectedTools.includes('P0_ADT_Streaming');
        
        if (timerangeContainer) {
            timerangeContainer.style.display = showTimeRange ? 'block' : 'none';
        }
    }
    
    // Add event listeners to all tool checkboxes
    document.querySelectorAll('input[type=checkbox][name=tool]').forEach(checkbox => {
        checkbox.addEventListener('change', updateTimeRangeVisibility);
    });
    
    // Initial check
    updateTimeRangeVisibility();
}

// Mostrar el spinner y contador mientras se ejecuta la consulta
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

// Mostrar resultado del historial
function showHistoryResult(index) {
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
    
    // Mostrar resultado en results-box SOLAMENTE
    const resultsBox = document.getElementById('results-box');
    const htmlContent = window.historyData[index].result || '<p>No result available</p>';
    resultsBox.innerHTML = htmlContent;
    
    // Re-ejecutar scripts para cargar gráficos de Chart.js
    const scripts = resultsBox.querySelectorAll('script');
    
    let scriptIndex = 0;
    function executeNextScript() {
        if (scriptIndex >= scripts.length) {
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
    
    // Focus en el textarea para empezar a escribir inmediatamente
    if (inputText) {
        inputText.focus();
    }
    
    console.log('✅ New chat started - all fields cleared');
}

// 🔄 Auto-refresh Status Monitor
// Load PagerDuty Monitor
function loadPagerDutyMonitor() {
    fetch('/api/pagerduty/monitor')
        .then(res => {
            if (!res.ok) throw new Error('Error loading PagerDuty data');
            return res.json();
        })
        .then(data => {
            // Update timestamp
            const timeElement = document.getElementById('pd-time');
            if (timeElement) {
                const now = new Date();
                timeElement.textContent = `Last updated: ${now.toLocaleTimeString()}`;
            }
            
            // Update summary
            const summaryElement = document.getElementById('pd-summary');
            if (summaryElement) {
                if (data.error) {
                    summaryElement.innerHTML = `<span style="color: #fee;">⚠️ ${data.error}</span>`;
                } else {
                    const triggered = data.triggered || 0;
                    const acknowledged = data.acknowledged || 0;
                    const resolved = data.resolved || 0;
                    summaryElement.innerHTML = `
                        <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; font-size: 11px;">
                            <div style="text-align: center;">
                                <div style="font-weight: bold; font-size: 18px;">${triggered}</div>
                                <div style="font-size: 10px; opacity: 0.9;">🔴 Triggered</div>
                            </div>
                            <div style="text-align: center;">
                                <div style="font-weight: bold; font-size: 18px;">${acknowledged}</div>
                                <div style="font-size: 10px; opacity: 0.9;">🟡 Acknowledged</div>
                            </div>
                            <div style="text-align: center;">
                                <div style="font-weight: bold; font-size: 18px;">${resolved}</div>
                                <div style="font-size: 10px; opacity: 0.9;">🟢 Resolved</div>
                            </div>
                        </div>
                    `;
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
                        const statusClass = inc.status.toLowerCase();
                        const icon = inc.status === 'triggered' ? '🔴' : '🟡';
                        const url = inc.url || '#';
                        return `
                            <li class="${statusClass}" title="${inc.title}" onclick="window.open('${url}', '_blank')" style="cursor: pointer;">
                                <strong>${icon} #${inc.number}</strong>
                                <div style="color: var(--text-secondary); font-size: 10px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-top: 2px;">
                                    ${inc.service}
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
                    resolvedElement.innerHTML = data.recently_resolved.map(inc => {
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
        })
        .catch(err => {
            console.error('Error loading PagerDuty monitor:', err);
            const summaryElement = document.getElementById('pd-summary');
            if (summaryElement) {
                summaryElement.innerHTML = '<span style="color: #fee;">⚠️ Connection error</span>';
            }
        });
}

function loadStatusMonitor() {
    fetch('/api/status/monitor')
        .then(res => {
            if (!res.ok) throw new Error('Error loading status');
            return res.json();
        })
        .then(data => {
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
            
            // Update core services
            const servicesElement = document.getElementById('status-services');
            if (servicesElement) {
                if (data.error) {
                    servicesElement.innerHTML = '<li style="color: #f56565;">Unable to load</li>';
                } else if (!data.services || data.services.length === 0) {
                    servicesElement.innerHTML = '<li style="color: #999;">No services data</li>';
                } else {
                    servicesElement.innerHTML = data.services.map(svc => {
                        const isAllGood = svc.status.trim().toLowerCase() === 'all good';
                        const icon = isAllGood ? '✅' : '⚠️';
                        const color = isAllGood ? '#48bb78' : '#f56565';
                        return `
                            <li style="display: flex; justify-content: space-between; align-items: center; padding: 3px 6px; background: #333; border-radius: 3px; margin-bottom: 3px;">
                                <span style="color: #e0e0e0; font-size: 10px;">${svc.service}</span>
                                <span style="color: ${color}; font-size: 11px;">${icon}</span>
                            </li>
                        `;
                    }).join('');
                }
            }
            
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
        })
        .catch(err => {
            console.error('Error loading status monitor:', err);
            const summaryElement = document.getElementById('status-summary');
            if (summaryElement) {
                summaryElement.innerHTML = '<span style="color: #f56565;">⚠️ Connection error</span>';
            }
        });
}

// 🚀 Inicialización al cargar la página
document.addEventListener('DOMContentLoaded', () => {
    console.log('🚀 Initializing GOC_AgenticAI v2.0...');
    
    // Load saved theme
    loadSavedTheme();
    
    // Cargar historial inicial
    loadHistory();
    
    // Setup history search
    setTimeout(setupHistorySearch, 1000);
    
    // Load status monitor immediately
    loadStatusMonitor();
    loadPagerDutyMonitor();
    
    // Auto-refresh status every 3 minutes (180000ms)
    setInterval(loadStatusMonitor, 180000);
    setInterval(loadPagerDutyMonitor, 180000);
    
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
            
            data.forEach(tool => {
                const label = document.createElement('label');
                label.className = 'tool-item';
                label.title = tool.desc || tool.name;
                label.innerHTML = `<input type="checkbox" name="tool" value="${tool.name}"> ${tool.name}`;
                toolList.appendChild(label);
            });
            
            console.log(`✅ Loaded ${data.length} tools`);
            
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

    // Manejar envío del formulario
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
            .then(data => {
                // Limpiar mensajes de carga
                clearInterval(counterInterval);
                hideLoadingOverlay();
                document.getElementById('loading-message').innerHTML = '';
                
                // Mostrar resultados
                const resultsBox = document.getElementById('results-box');
                const htmlContent = data.result || '<p>No results returned</p>';
                
                // Insertar HTML
                resultsBox.innerHTML = htmlContent;
                
                // Add action buttons to results
                setTimeout(() => {
                    addResultActions(resultsBox);
                }, 500);
                
                // Ejecutar scripts que fueron insertados via innerHTML
                const scripts = resultsBox.querySelectorAll('script');
                
                // Execute scripts sequentially (wait for external scripts to load)
                let scriptIndex = 0;
                function executeNextScript() {
                    if (scriptIndex >= scripts.length) {
                        return;
                    }
                    
                    const oldScript = scripts[scriptIndex];
                    const newScript = document.createElement('script');
                    
                    // Copiar atributos (especialmente src para scripts externos)
                    Array.from(oldScript.attributes).forEach(attr => {
                        newScript.setAttribute(attr.name, attr.value);
                    });
                    
                    // Copiar contenido para scripts inline
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
                    
                    // Agregar al documento para que se ejecute
                    document.body.appendChild(newScript);
                    
                    // Remover el script viejo
                    oldScript.remove();
                }
                
                executeNextScript();
                
                // Mostrar tiempo de ejecución
                const execTime = data.exec_time || '0';
                document.getElementById('final-counter').innerText = `⏱ Execution time: ${execTime}s`;
                
                // Recargar historial después de un pequeño delay para asegurar que el backend guardó
                setTimeout(() => {
                    loadHistory(); // 🔄 refresca historial automáticamente
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
function downloadResults() {
    const resultsBox = document.getElementById('results-box');
    if (!resultsBox || resultsBox.innerHTML === '') {
        showNotification('No results to download');
        return;
    }
    
    // Get the HTML content
    const htmlContent = resultsBox.innerHTML;
    
    // Show loading notification
    showNotification('Generating document...');
    
    // Send to backend for document generation
    fetch('/api/download/docx', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            html_content: htmlContent
        })
    })
    .then(response => {
        if (!response.ok) {
            throw new Error('Failed to generate document');
        }
        return response.blob();
    })
    .then(blob => {
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
    })
    .catch(err => {
        console.error('Error downloading document:', err);
        showNotification('❌ Error downloading document: ' + err.message);
    });
}
