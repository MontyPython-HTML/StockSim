const GRID = 'rgba(148, 163, 184, 0.12)';
const TICK = '#94a3b8';

Chart.defaults.color = TICK;
Chart.defaults.font.family = 'ui-sans-serif, system-ui, sans-serif';
Chart.defaults.animation = false;

function baseScales(extra = {}) {
    return {
        x: { grid: { color: GRID }, ticks: { maxTicksLimit: 8, maxRotation: 0 } },
        y: { grid: { color: GRID }, ticks: { maxTicksLimit: 6 } },
        ...extra,
    };
}

function createPriceChart(canvas) {
    return new Chart(canvas, {
        type: 'line',
        data: {
            labels: [],
            datasets: [
                {
                    label: 'Close', data: [], borderColor: '#38bdf8', backgroundColor: 'rgba(56,189,248,0.08)',
                    borderWidth: 2, pointRadius: 0, fill: true, tension: 0.1, order: 3,
                },
                { label: 'SMA 20', data: [], borderColor: '#fbbf24', borderWidth: 1.5, pointRadius: 0, tension: 0.1, order: 2 },
                { label: 'SMA 50', data: [], borderColor: '#a78bfa', borderWidth: 1.5, pointRadius: 0, tension: 0.1, order: 2 },
                {
                    label: 'Buys', data: [], type: 'scatter', borderColor: '#34d399', backgroundColor: '#34d399',
                    pointStyle: 'triangle', pointRadius: 9, order: 1,
                },
                {
                    label: 'Sells', data: [], type: 'scatter', borderColor: '#fb7185', backgroundColor: '#fb7185',
                    pointStyle: 'triangle', pointRadius: 9, rotation: 180, order: 1,
                },
                {
                    label: 'Volume', data: [], type: 'bar', backgroundColor: 'rgba(148,163,184,0.25)',
                    yAxisID: 'volume', order: 4,
                },
            ],
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: { labels: { boxWidth: 12, usePointStyle: true, filter: (i) => i.text !== 'Volume' } },
            },
            scales: baseScales({
                volume: { display: false, beginAtZero: true, max: 0 },
            }),
        },
    });
}

function createRsiChart(canvas) {
    return new Chart(canvas, {
        type: 'line',
        data: {
            labels: [],
            datasets: [
                { label: 'RSI', data: [], borderColor: '#22d3ee', borderWidth: 2, pointRadius: 0, tension: 0.1 },
                { label: 'Overbought', data: [], borderColor: 'rgba(251,113,133,0.5)', borderWidth: 1, pointRadius: 0, borderDash: [4, 4] },
                { label: 'Oversold', data: [], borderColor: 'rgba(52,211,153,0.5)', borderWidth: 1, pointRadius: 0, borderDash: [4, 4] },
            ],
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: baseScales({ y: { min: 0, max: 100, grid: { color: GRID }, ticks: { stepSize: 25 } } }),
        },
    });
}

function createMacdChart(canvas) {
    return new Chart(canvas, {
        type: 'bar',
        data: {
            labels: [],
            datasets: [
                { label: 'Histogram', data: [], backgroundColor: [], order: 3 },
                { label: 'MACD', data: [], type: 'line', borderColor: '#38bdf8', borderWidth: 2, pointRadius: 0, tension: 0.1, order: 1 },
                { label: 'Signal', data: [], type: 'line', borderColor: '#fbbf24', borderWidth: 2, pointRadius: 0, tension: 0.1, order: 2 },
            ],
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: baseScales(),
        },
    });
}

function updateCharts(charts, state) {
    const rows = state.chart;
    const labels = rows.map((r) => r.date);
    const index = new Map(labels.map((date, i) => [date, i]));

    const price = charts.price;
    price.data.labels = labels;
    price.data.datasets[0].data = rows.map((r) => r.close);
    price.data.datasets[1].data = rows.map((r) => r.sma20);
    price.data.datasets[2].data = rows.map((r) => r.sma50);

    const toMarkers = (side) => state.trades
        .filter((t) => t.side === side && index.has(t.date))
        .map((t) => ({ x: index.get(t.date), y: t.price }));
    price.data.datasets[3].data = toMarkers('BUY');
    price.data.datasets[4].data = toMarkers('SELL');

    const volumes = rows.map((r) => r.volume);
    price.data.datasets[5].data = volumes;
    // Keep volume bars in the bottom quarter of the plot so they never fight the price line.
    price.options.scales.volume.max = Math.max(...volumes, 1) * 4;
    price.update();

    const rsi = charts.rsi;
    rsi.data.labels = labels;
    rsi.data.datasets[0].data = rows.map((r) => r.rsi14);
    rsi.data.datasets[1].data = labels.map(() => 70);
    rsi.data.datasets[2].data = labels.map(() => 30);
    rsi.update();

    const macd = charts.macd;
    macd.data.labels = labels;
    macd.data.datasets[0].data = rows.map((r) => r.macd_hist);
    macd.data.datasets[0].backgroundColor = rows.map((r) =>
        (r.macd_hist ?? 0) >= 0 ? 'rgba(52,211,153,0.6)' : 'rgba(251,113,133,0.6)');
    macd.data.datasets[1].data = rows.map((r) => r.macd);
    macd.data.datasets[2].data = rows.map((r) => r.macd_signal);
    macd.update();
}
