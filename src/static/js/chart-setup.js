/* Chart.js setup and update helpers.

   Colours mirror the design tokens in static/input/input.css: near-black panels, a teal
   and a cyan for data, amber only as a second series, and blue reserved for the account
   line so "my portfolio" always reads as the same colour. */

const GRID = 'rgba(154, 163, 176, 0.10)';
const TICK = '#6b7280';

const C = {
    glow: '#22d3ee',
    accent: '#2dd4bf',
    brand: '#3b82f6',
    warn: '#f0b429',
    up: '#2dd4bf',
    down: '#fb7185',
    volume: 'rgba(154, 163, 176, 0.22)',
};

// A basket can be ten symbols wide; these are distinct enough at 2px to tell apart, and
// stay inside the mock's cold palette rather than inventing a rainbow.
const SERIES_COLORS = [
    '#3b82f6', '#2dd4bf', '#22d3ee', '#f0b429', '#8b5cf6',
    '#fb7185', '#34d399', '#f472b6', '#60a5fa', '#a3e635',
];

Chart.defaults.color = TICK;
Chart.defaults.font.family = 'ui-sans-serif, system-ui, sans-serif';
Chart.defaults.animation = false;

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

// Full ISO dates are ~10 characters and collide on a 300px panel once the y-axis takes
// its share of the width. "Nov 21" carries the same information and never overlaps.
function shortDate(value) {
    const raw = String(this.getLabelForValue ? this.getLabelForValue(value) : value);
    const iso = /^(\d{4})-(\d{2})-(\d{2})$/.exec(raw);
    return iso ? `${MONTHS[Number(iso[2]) - 1]} ${iso[1].slice(2)}` : raw;
}

function compactMoney(value) {
    const amount = Number(value);
    if (!Number.isFinite(amount)) return '';
    if (Math.abs(amount) >= 10000) return `${Math.round(amount / 1000)}k`;
    if (Math.abs(amount) >= 1000) return `${(amount / 1000).toFixed(1)}k`;
    return amount.toFixed(0);
}

function baseScales(extra = {}) {
    return {
        x: {
            grid: { color: GRID },
            ticks: { maxTicksLimit: 7, maxRotation: 0, autoSkip: true, callback: shortDate },
        },
        y: { grid: { color: GRID }, ticks: { maxTicksLimit: 6 } },
        ...extra,
    };
}

const legendBox = { boxWidth: 12, usePointStyle: true };

function createPriceChart(canvas) {
    return new Chart(canvas, {
        type: 'line',
        data: {
            labels: [],
            datasets: [
                {
                    label: 'Close', data: [], borderColor: C.glow, backgroundColor: 'rgba(34,211,238,0.07)',
                    borderWidth: 2, pointRadius: 0, fill: true, tension: 0.1, order: 3,
                },
                { label: 'SMA 20', data: [], borderColor: C.warn, borderWidth: 1.5, pointRadius: 0, tension: 0.1, order: 2 },
                { label: 'SMA 50', data: [], borderColor: C.brand, borderWidth: 1.5, pointRadius: 0, tension: 0.1, order: 2 },
                {
                    label: 'Buys', data: [], type: 'scatter', borderColor: C.up, backgroundColor: C.up,
                    pointStyle: 'triangle', pointRadius: 9, order: 1,
                },
                {
                    label: 'Sells', data: [], type: 'scatter', borderColor: C.down, backgroundColor: C.down,
                    pointStyle: 'triangle', pointRadius: 9, rotation: 180, order: 1,
                },
                {
                    label: 'Volume', data: [], type: 'bar', backgroundColor: C.volume,
                    yAxisID: 'volume', order: 4,
                },
            ],
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: { labels: { ...legendBox, filter: (i) => i.text !== 'Volume' } },
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
                { label: 'RSI', data: [], borderColor: C.glow, borderWidth: 2, pointRadius: 0, tension: 0.1 },
                { label: 'Overbought', data: [], borderColor: 'rgba(251,113,133,0.45)', borderWidth: 1, pointRadius: 0, borderDash: [4, 4] },
                { label: 'Oversold', data: [], borderColor: 'rgba(45,212,191,0.45)', borderWidth: 1, pointRadius: 0, borderDash: [4, 4] },
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
                { label: 'MACD', data: [], type: 'line', borderColor: C.glow, borderWidth: 2, pointRadius: 0, tension: 0.1, order: 1 },
                { label: 'Signal', data: [], type: 'line', borderColor: C.warn, borderWidth: 2, pointRadius: 0, tension: 0.1, order: 2 },
            ],
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: baseScales(),
        },
    });
}

function createBasketChart(canvas) {
    return new Chart(canvas, {
        type: 'line',
        data: { labels: [], datasets: [] },
        options: {
            responsive: true, maintainAspectRatio: false,
            interaction: { mode: 'nearest', intersect: false },
            plugins: {
                legend: { position: 'bottom', labels: { ...legendBox, padding: 12 } },
                tooltip: { callbacks: { label: (item) => `${item.dataset.label}: ${item.parsed.y?.toFixed(1)}` } },
            },
            scales: baseScales(),
        },
    });
}

function createEquityChart(canvas) {
    return new Chart(canvas, {
        type: 'line',
        data: {
            labels: [],
            datasets: [
                {
                    label: 'Net worth', data: [], borderColor: C.brand,
                    backgroundColor: 'rgba(59,130,246,0.10)', borderWidth: 2.5,
                    pointRadius: 0, fill: true, tension: 0.1, order: 1,
                },
                {
                    label: 'Starting cash', data: [], borderColor: 'rgba(154,163,176,0.5)',
                    borderWidth: 1, borderDash: [5, 5], pointRadius: 0, order: 2,
                },
            ],
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: { display: false },
                tooltip: { callbacks: { label: (item) => `${item.dataset.label}: ${item.parsed.y?.toLocaleString()}` } },
            },
            // Dollar values are five or six digits wide, which eats the axis room the date
            // labels need; "10.0k" keeps both readable.
            scales: baseScales({ y: { grid: { color: GRID }, ticks: { maxTicksLimit: 5, callback: compactMoney } } }),
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
        (r.macd_hist ?? 0) >= 0 ? 'rgba(45,212,191,0.6)' : 'rgba(251,113,133,0.6)');
    macd.data.datasets[1].data = rows.map((r) => r.macd);
    macd.data.datasets[2].data = rows.map((r) => r.macd_signal);
    macd.update();
}

function updateBasketChart(chart, payload) {
    const series = payload.series || [];
    // Every symbol trades on its own calendar, so the axis is the union of their dates and
    // a symbol simply has a gap on a day it did not trade.
    const labels = [...new Set(series.flatMap((s) => s.points.map((p) => p.date)))].sort();

    chart.data.labels = labels;
    chart.data.datasets = series.map((row, i) => {
        const byDate = new Map(row.points.map((p) => [p.date, p.index]));
        const color = SERIES_COLORS[i % SERIES_COLORS.length];
        return {
            label: row.simulated ? `${row.ticker} ~` : row.ticker,
            data: labels.map((date) => (byDate.has(date) ? byDate.get(date) : null)),
            borderColor: color,
            backgroundColor: color,
            borderWidth: 2,
            pointRadius: 0,
            spanGaps: true,
            tension: 0.1,
        };
    });
    chart.update();
}

function updateEquityChart(chart, payload) {
    const points = payload.equity || [];
    const labels = points.map((p) => p.date);
    chart.data.labels = labels;
    chart.data.datasets[0].data = points.map((p) => p.net_worth);
    chart.data.datasets[1].data = labels.map(() => payload.starting_cash);
    chart.update();
}
