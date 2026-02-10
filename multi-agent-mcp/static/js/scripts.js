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
    showNotification('History cleared successfully');
    console.log('✅ History cleared');
}

// History search/filter functionality
function setupHistorySearch() {
    const searchInput = document.getElementById('history-search');
    if (!searchInput) return;
    
    searchInput.addEventListener('input', (e) => {
        const searchTerm = e.target.value.toLowerCase();
        const historyItems = document.querySelectorAll('#history-list li');
        
        historyItems.forEach(item => {
            const text = item.textContent.toLowerCase();
            if (text.includes(searchTerm)) {
                item.style.display = '';
            } else {
                item.style.display = 'none';
            }
        });
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
            📖 Expand
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
    // Expand any collapsible sections in results
    const details = document.querySelectorAll('#results-box details');
    details.forEach(detail => {
        detail.open = true;
    });
    showNotification('All sections expanded');
}

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
function showLoading() {
    // Show overlay
    showLoadingOverlay();
    
    // Also show inline spinner for backward compatibility
    document.getElementById('loading-message').innerHTML =
        '<span class="spinner"></span><span class="counter" id="counter">0s</span>';
    document.getElementById('results-box').innerHTML = '';
    document.getElementById('final-counter').innerText = '';
    startTime = Date.now();
    if (counterInterval) clearInterval(counterInterval);
    counterInterval = setInterval(() => {
        const elapsed = Math.floor((Date.now() - startTime) / 1000);
        document.getElementById('counter').innerText = elapsed + 's';
    }, 1000);
}

// 🔄 Cargar historial desde API
function loadHistory() {
    fetch('/api/history')
        .then(res => {
            if (!res.ok) throw new Error('Error al cargar historial');
            return res.json();
        })
        .then(data => {
            const historyList = document.getElementById("history-list");
            historyList.innerHTML = '';
            
            if (data.length === 0) {
                historyList.innerHTML = '<li style="color: #666; font-size: 12px; padding: 10px;">No history yet</li>';
                window.historyData = [];
                return;
            }
            
            data.forEach((item, index) => {
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
            });
            window.historyData = data;
        })
        .catch(err => {
            console.error('Error cargando historial:', err);
            const historyList = document.getElementById("history-list");
            historyList.innerHTML = '<li style="color: #f56565; font-size: 12px; padding: 10px;">⚠️ Error loading history</li>';
        });
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
    console.log('🚀 Initializing Arlo_GenAI v2.0...');
    
    // Load saved theme
    loadSavedTheme();
    
    // Cargar historial inicial
    loadHistory();
    
    // Setup history search
    setTimeout(setupHistorySearch, 1000);
    
    // Load status monitor immediately
    loadStatusMonitor();
    
    // Auto-refresh status every 3 minutes (180000ms)
    setInterval(loadStatusMonitor, 180000);
    
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

        showLoading();

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
                setTimeout(() => addResultActions(resultsBox), 500);
                
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
