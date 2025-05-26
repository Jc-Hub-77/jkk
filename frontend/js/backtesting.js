// frontend/js/backtesting.js
console.log("backtesting.js loaded");

document.addEventListener('DOMContentLoaded', () => {
    const authToken = localStorage.getItem('authToken');
    const userId = localStorage.getItem('userId');

    // DOM Elements
    const backtestSetupForm = document.getElementById('backtestSetupForm');
    const strategySelect = document.getElementById('backtestStrategySelect');
    // ... (other DOM elements as before) ...
    const resultsSection = document.getElementById('backtestResultsSection');
    const resultsLoading = document.getElementById('resultsLoading');
    const resultsContent = document.getElementById('resultsContent');
    const metricsSummaryContainer = document.getElementById('metricsSummaryContainer');
    const priceChartContainer = document.getElementById('priceChartContainer');
    const equityChartContainer = document.getElementById('equityChartContainer');
    const tradesLogTableBody = document.getElementById('tradesLogTableBody');

    let priceChart = null;
    let equityChart = null;
    let candlestickSeries = null;
    let equitySeries = null;

    async function initializeBacktestPage() {
        await populateStrategySelect();
        setDefaultDates();
        strategySelect.addEventListener('change', loadStrategyParameters);
    }

    function setDefaultDates() {
        const endDateInput = document.getElementById('backtestEndDate');
        const startDateInput = document.getElementById('backtestStartDate');
        const now = new Date();
        const defaultEndDate = new Date(now.getFullYear(), now.getMonth(), now.getDate() -1, 23, 59); // Yesterday end of day
        const defaultStartDate = new Date(defaultEndDate.getFullYear(), defaultEndDate.getMonth() - 1, defaultEndDate.getDate()); // Approx 1 month before

        endDateInput.value = defaultEndDate.toISOString().slice(0,16);
        startDateInput.value = defaultStartDate.toISOString().slice(0,16);
    }

    async function populateStrategySelect() {
        const strategySelect = document.getElementById('backtestStrategySelect');
        if (!strategySelect) return;
        strategySelect.innerHTML = '<option value="">Loading strategies...</option>';
        try {
            // Conceptual API: GET /api/strategies
            const response = await fetch('/api/v1/admin/strategies', { headers: { 'Authorization': `Bearer ${authToken}` } });
            if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
            const data = await response.json(); // Expects { status, strategies: [...] }

            if (data.status === "success" && data.strategies) {
                strategySelect.innerHTML = '<option value="">Select Strategy</option>';
                data.strategies.forEach(strategy => {
                    const option = document.createElement('option');
                    option.value = strategy.id;
                    option.textContent = strategy.name;
                    strategySelect.appendChild(option);
                });
            } else { throw new Error(data.message || "Failed to parse strategies."); }
        } catch (error) {
            console.error("Failed to load strategies for backtest:", error);
            strategySelect.innerHTML = '<option value="">Error loading strategies</option>';
        }
    }

    async function loadStrategyParameters() {
        const strategyId = document.getElementById('backtestStrategySelect').value;
        const paramsContainer = document.getElementById('backtestStrategyParamsContainer');
        if (!strategyId) {
            paramsContainer.innerHTML = '<p><em>Select a strategy to see its parameters.</em></p>';
            return;
        }
        paramsContainer.innerHTML = '<p><em>Loading parameters...</em></p>';
        try {
            // Conceptual API: GET /api/strategies/{strategyId}
            const response = await fetch(`/api/v1/admin/strategies/${strategyId}`, { headers: { 'Authorization': `Bearer ${authToken}` } });
            if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
            const data = await response.json(); // Expects { status: "success", details: {...} }

            if (data.status === "success" && data.details && data.details.parameters_definition) {
                paramsContainer.innerHTML = '<h3>Strategy Parameters:</h3>';
                const paramsGrid = document.createElement('div');
                paramsGrid.className = 'form-grid';
                for (const paramName in data.details.parameters_definition) {
                    const paramDef = data.details.parameters_definition[paramName];
                    const group = document.createElement('div');
                    group.className = 'form-group';
                    const label = document.createElement('label');
                    label.setAttribute('for', `param_${paramName}`);
                    label.textContent = paramDef.label;
                    const input = document.createElement('input');
                    input.type = paramDef.type === "int" || paramDef.type === "float" ? "number" : "text";
                    input.id = `param_${paramName}`;
                    input.name = `custom_params_${paramName}`;
                    input.value = paramDef.default;
                    if (paramDef.min !== undefined) input.min = paramDef.min;
                    if (paramDef.max !== undefined) input.max = paramDef.max;
                    if (paramDef.type === "float") input.step = paramDef.step || "any";
                    group.appendChild(label); paramGroup.appendChild(input);
                    paramsGrid.appendChild(paramGroup);
                }
                paramsContainer.appendChild(paramsGrid);
            } else { throw new Error(data.message || "Could not load parameters."); }
        } catch (error) {
            console.error("Error loading strategy parameters:", error);
            paramsContainer.innerHTML = `<p class="error-message">Error loading parameters: ${error.message}</p>`;
        }
    }

    if (backtestSetupForm) {
        backtestSetupForm.addEventListener('submit', async (event) => {
            event.preventDefault();
            resultsSection.style.display = 'block';
            resultsContent.style.display = 'none';
            resultsLoading.style.display = 'block';

            const formData = new FormData(backtestSetupForm);
            const payload = {
                // user_id: userId, // Backend gets from session/token
                strategy_id_str: formData.get('strategyId'),
                exchange_id: formData.get('exchangeId'), // This is for data source
                symbol: formData.get('symbol'),
                timeframe: formData.get('timeframe'),
                start_date_str: new Date(formData.get('startDate')).toISOString(),
                end_date_str: new Date(formData.get('endDate')).toISOString(),
                initial_capital: parseFloat(formData.get('initialCapital')),
                custom_parameters: {}
            };
            for (const [key, value] of formData.entries()) {
                if (key.startsWith('custom_params_')) {
                    const paramName = key.replace('custom_params_', '');
                    payload.custom_parameters[paramName] = isNaN(parseFloat(value)) ? value : parseFloat(value);
                }
            }

            console.log("Running backtest with payload:", payload);
            try {
                // Conceptual API: POST /api/backtests/run
                const response = await fetch('/api/v1/backtests/run', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${authToken}` },
                    body: JSON.stringify(payload)
                });
                if (!response.ok) {
                    const errData = await response.json().catch(() => ({message: "Unknown error running backtest"}));
                    throw new Error(errData.message || `HTTP error! status: ${response.status}`);
                }
                const results = await response.json();

                resultsLoading.style.display = 'none';
                if (results.status === "success") {
                    resultsContent.style.display = 'block';
                    displayBacktestResults(results);
                } else {
                    throw new Error(results.message || "Backtest failed with an unknown error.");
                }
            } catch (error) {
                console.error("Backtest execution error:", error);
                resultsLoading.style.display = 'none';
                resultsContent.style.display = 'block';
                metricsSummaryContainer.innerHTML = `<p class="error-message">Backtest failed: ${error.message}</p>`;
                if(priceChart) priceChart.remove(); priceChart = null;
                if(equityChart) equityChart.remove(); equityChart = null;
                tradesLogTableBody.innerHTML = '';
            }
        });
    }

    function displayBacktestResults(results) {
        metricsSummaryContainer.innerHTML = `
            <div class="metric-item"><strong>Strategy:</strong> ${results.strategy_id}</div>
            <div class="metric-item"><strong>Symbol:</strong> ${results.symbol} (${results.timeframe})</div>
            <div class="metric-item"><strong>Period:</strong> ${results.period}</div>
            <div class="metric-item"><strong>Initial Capital:</strong> $${results.initial_capital.toFixed(2)}</div>
            <div class="metric-item"><strong>Final Equity:</strong> $${results.final_equity.toFixed(2)}</div>
            <div class="metric-item"><strong>PnL:</strong> $${results.pnl.toFixed(2)} (${results.pnl_percentage.toFixed(2)}%)</div>
            <div class="metric-item"><strong>Sharpe Ratio:</strong> ${results.sharpe_ratio.toFixed(2)}</div>
            <div class="metric-item"><strong>Max Drawdown:</strong> ${results.max_drawdown.toFixed(2)}%</div>
            <div class="metric-item"><strong>Total Trades:</strong> ${results.total_trades}</div>
            <div class="metric-item"><strong>Win Rate:</strong> ${results.win_rate.toFixed(2)}%</div>`;

        if (priceChart) priceChart.remove();
        priceChart = LightweightCharts.createChart(priceChartContainer, { width: priceChartContainer.clientWidth, height: 400, layout: { textColor: getComputedStyle(document.body).getPropertyValue('--dm-text-color') || '#000', background: { type: 'solid', color: getComputedStyle(document.body).getPropertyValue('--dm-surface-color') || '#fff' } } });
        candlestickSeries = priceChart.addCandlestickSeries();
        if (results.ohlcv_data) candlestickSeries.setData(results.ohlcv_data);
        else console.warn("OHLCV data not provided for price chart.");

        const tradeMarkers = [];
        results.trades_log.forEach(trade => {
            const entryText = 'Entry ' + (trade.type ? String(trade.type).toUpperCase() : 'N/A');
            // Using quoted keys as a diagnostic step for potential linter issues.
            const entryMarker = {
                "time": trade.entry_time,
                "position": 'belowBar',
                "color": trade.type === 'long' ? 'green' : 'red',
                "shape": 'arrowUp',
                "text": entryText
            };
            tradeMarkers.push(entryMarker);
            if (trade.exit_time) {
                const exitMarker = {
                    "time": trade.exit_time,
                    "position": 'aboveBar',
                    "color": 'blue',
                    "shape": 'arrowDown',
                    "text": 'Exit'
                };
                tradeMarkers.push(exitMarker);
            }
        });
        if(candlestickSeries) candlestickSeries.setMarkers(tradeMarkers);
        priceChart.timeScale().fitContent();

        if (equityChart) equityChart.remove();
        equityChart = LightweightCharts.createChart(equityChartContainer, { width: equityChartContainer.clientWidth, height: 400, layout: { textColor: getComputedStyle(document.body).getPropertyValue('--dm-text-color') || '#000', background: { type: 'solid', color: getComputedStyle(document.body).getPropertyValue('--dm-surface-color') || '#fff' } } });
        equitySeries = equityChart.addLineSeries({ color: document.body.classList.contains('dark-mode') ? '#42a5f5' : '#007bff' }); // Adjust color for dark/light
        if (results.equity_curve) equitySeries.setData(results.equity_curve);
        equityChart.timeScale().fitContent();

        tradesLogTableBody.innerHTML = '';
        results.trades_log.forEach(trade => {
            const row = tradesLogTableBody.insertRow();
            row.insertCell().textContent = trade.type;
            row.insertCell().textContent = new Date(trade.entry_time * 1000).toLocaleString();
            row.insertCell().textContent = trade.entry_price.toFixed(2);
            row.insertCell().textContent = trade.exit_time ? new Date(trade.exit_time * 1000).toLocaleString() : 'N/A';
            row.insertCell().textContent = trade.exit_price ? trade.exit_price.toFixed(2) : 'N/A';
            row.insertCell().textContent = trade.size || 'N/A';
            row.insertCell().textContent = trade.pnl.toFixed(2);
        });
    }

    // Update chart themes if dark mode changes
    const themeToggle = document.getElementById('themeSwitch');
    if (themeToggle) {
        themeToggle.addEventListener('change', function() {
            const isDarkMode = document.body.classList.contains('dark-mode');
            const chartOptions = {
                layout: {
                    textColor: isDarkMode ? getComputedStyle(document.body).getPropertyValue('--dm-text-color') : getComputedStyle(document.body).getPropertyValue('--text-color'),
                    background: { type: 'solid', color: isDarkMode ? getComputedStyle(document.body).getPropertyValue('--dm-surface-color') : getComputedStyle(document.body).getPropertyValue('--background-color') }
                }
            };
            if (priceChart) priceChart.applyOptions(chartOptions);
            if (equityChart) equityChart.applyOptions(chartOptions);
            if (equitySeries) equitySeries.applyOptions({ color: isDarkMode ? '#42a5f5' : '#007bff' });
        });
    }

    initializeBacktestPage();
});
