let counterInterval;
let startTime;

// Mostrar el spinner y contador mientras se ejecuta la consulta
function showLoading() {
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
        .then(res => res.json())
        .then(data => {
            const historyList = document.getElementById("history-list");
            historyList.innerHTML = '';
            data.forEach((item, index) => {
                const li = document.createElement("li");
                const btn = document.createElement("button");
                btn.textContent = item.query;
                btn.onclick = () => showHistoryResult(index);
                li.appendChild(btn);
                historyList.appendChild(li);
            });
            window.historyData = data;
        })
        .catch(err => console.error('Error cargando historial:', err));
}

// Mostrar resultado del historial
function showHistoryResult(index) {
    document.getElementById('results-box').innerHTML = window.historyData[index].result;
}

// Reiniciar el formulario y limpiar resultados
function newChat() {
    document.querySelector('#input-text').value = '';
    document.getElementById('results-box').innerHTML = '';
    document.getElementById('final-counter').innerText = '';
    document.getElementById('loading-message').innerText = '';
    document.querySelectorAll('input[type=checkbox][name=tool]').forEach(cb => cb.checked = false);
    if (counterInterval) clearInterval(counterInterval);
}

// 🚀 Inicialización al cargar la página
document.addEventListener('DOMContentLoaded', () => {
    // Cargar historial inicial
    loadHistory();

    // Cargar herramientas desde API
    fetch('/api/tools')
        .then(res => res.json())
        .then(data => {
            const toolList = document.getElementById('tool-list');
            toolList.innerHTML = '';
            data.forEach(tool => {
                const label = document.createElement('label');
                label.className = 'tool-item';
                label.title = tool.desc;
                label.innerHTML = `<input type="checkbox" name="tool" value="${tool.name}"> ${tool.name}`;
                toolList.appendChild(label);
            });
        });

    // Manejar envío del formulario
    document.getElementById('search-form').addEventListener('submit', e => {
        e.preventDefault();
        const inputText = document.getElementById('input-text').value;
        const selectedTools = Array.from(document.querySelectorAll('input[name=tool]:checked')).map(el => el.value);

        if (selectedTools.length === 0) {
            alert('Select at least one tool before sending.');
            return;
        }

        showLoading();

        fetch('/api/run', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ input: inputText, tools: selectedTools })
        })
            .then(res => res.json())
            .then(data => {
                document.getElementById('results-box').innerHTML = data.result;
                document.getElementById('final-counter').innerText = `⏱ Execution time: ${data.exec_time}s`;
                clearInterval(counterInterval);
                document.getElementById('loading-message').innerHTML = '';
                loadHistory(); // 🔄 refresca historial automáticamente
            })
            .catch(err => {
                document.getElementById('results-box').innerHTML = `<pre>Error: ${err}</pre>`;
                clearInterval(counterInterval);
                document.getElementById('loading-message').innerHTML = '';
            });
    });
});
