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
// Chart.js animates every update; the default easing is long enough that a fast clock feels
// laggy, so it is tuned down to a slide that keeps up with a day a second. Tune the shipped
// animation object in place - do not replace it. The animator reads `fn`/`type`/`from`/`to`
// off this object to pick an interpolator, and a replacement object missing those keys makes
// every update throw "this._fn is not a function" and the chart stop repainting entirely.
Chart.defaults.animation.duration = 320;
Chart.defaults.animation.easing = 'easeOutCubic';

// How a plot moves should follow how fast the clock is going, and only the clock decides when
// anything is drawn - there is no redraw timer of its own in here. `tickMs` is the gap between
// simulated days while the clock runs, and 0 when it is stopped.
let tickMs = 0;
// Every tick moves the window one slot. Easing that slide across the whole gap between ticks is
// what reads as continuous motion, rather than a hop once a second with a pause after it.
// Below this gap a tick would land before an animation could finish, and five canvases
// redrawn every frame of every tick is what made a fast clock feel heavy - at two days a
// second nobody can follow an eased slide anyway, so those speeds step without one.
const CONTINUOUS_MIN_MS = 700;
const STEP_DURATION = 320;

function setChartCadence(ms) {
    tickMs = Number(ms) || 0;
}

// `moved` says whether anything already on the plot has to move: a day appended to the end
// needs no animation, whereas a window that rolled or an axis that rescaled does.
function draw(chart, moved) {
    const animation = Chart.defaults.animation;
    if (!moved || (tickMs > 0 && tickMs < CONTINUOUS_MIN_MS)) {
        // Back to the stepped default, so the next thing that does animate - a pause, a single
        // day stepped by hand - is not left easing over the last fast tick's duration.
        animation.duration = STEP_DURATION;
        animation.easing = 'easeOutCubic';
        chart.update('none');
        return;
    }
    if (tickMs >= CONTINUOUS_MIN_MS) {
        // Fill the entire gap between one day's data and the next. That gap is the tick, not
        // the tick minus the request time: the request's own latency falls inside the
        // interval, before the pan can start, so the following day's prices do not arrive
        // until a whole tick after these ones did. Stopping short of that - this used to run
        // at 85% - parks the curve for the tail of every second, which is what made new data
        // arriving read as a hop rather than as the shape scrolling.
        animation.duration = tickMs;
        animation.easing = 'linear';
    } else {
        animation.duration = STEP_DURATION;
        animation.easing = 'easeOutCubic';
    }
    chart.update();
}

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

// --- drawing only what changed --------------------------------------------
//
// Every tick used to hand Chart.js a brand new array for every series. A fresh array has
// nothing to attach to, so the whole curve was re-parsed and re-interpolated once per
// simulated day: the plot visibly redrew itself instead of growing a point at the end.
// These helpers keep the arrays that are already on screen and move only what actually
// changed - pushing the new day on, or sliding the window when the backend's rolling
// window drops its oldest day. An update that only appended runs without animation, so
// nothing already drawn moves at all.

const drawn = new WeakMap();    // chart -> { labels: [...], columns: [{ index, values }] }
const pinned = new WeakMap();   // chart -> { axisId: { min, max } }
// A tick adds a day or two; this only needs to cover a burst large enough to include a
// deliberately skipped stretch, not an unbounded window.
const MAX_SHIFT = 32;

const blank = (value) => value === null || value === undefined
    || (typeof value === 'number' && Number.isNaN(value));

function sameNumber(a, b) {
    return (blank(a) && blank(b)) || a === b;
}

// The incoming window is the drawn one with its first `drop` days fallen off and any new
// days appended. Returns null when that is not the case, which means start again from the
// new data - a different symbol, a restarted session, a window that shrank.
function overlapOf(held, labels, columns) {
    const oldCount = held.labels.length;
    if (!oldCount) return null;
    const limit = Math.min(oldCount, MAX_SHIFT);
    for (let drop = 0; drop <= limit; drop += 1) {
        const kept = oldCount - drop;
        if (kept > labels.length) continue;
        let same = true;
        for (let i = 0; i < kept && same; i += 1) {
            same = held.labels[drop + i] === labels[i];
        }
        if (!same) continue;
        // Values, not just dates: focusing a different symbol keeps the same calendar, so
        // the dates alone would say "unchanged" while every price on the chart did change.
        for (const column of held.columns) {
            const now = columns.find((c) => c.index === column.index);
            if (!now || now.values.length !== labels.length) { same = false; break; }
            for (let i = 0; i < kept; i += 1) {
                if (!sameNumber(column.values[drop + i], now.values[i])) { same = false; break; }
            }
            if (!same) break;
        }
        if (same) return { drop, kept };
    }
    return null;
}

function applySeries(chart, labels, columns) {
    const held = drawn.get(chart);
    const step = held ? overlapOf(held, labels, columns) : null;
    let appended = labels.length;
    let reloaded = true;
    let shifted = 0;

    if (step) {
        // Edit the arrays the chart already holds rather than handing over new ones: those
        // are the arrays Chart.js built its elements from.
        appended = labels.length - step.kept;
        shifted = step.drop;
        reloaded = false;
        if (step.drop === appended) {
            // The usual tick: the window slid by exactly the number of days it gained, so
            // the length is unchanged and the old days are moved up with copyWithin. A
            // splice would instead change the length, and Chart.js answers that by
            // discarding every element it has and rebuilding the curve - which is the flicker
            // this is here to avoid. Rotating keeps the elements, so each point just glides
            // one slot left.
            for (const column of columns) {
                const target = chart.data.datasets[column.index].data;
                if (step.drop) target.copyWithin(0, step.drop);
                for (let i = step.kept; i < labels.length; i += 1) target[i] = column.values[i];
            }
            if (step.drop) chart.data.labels.copyWithin(0, step.drop);
            for (let i = step.kept; i < labels.length; i += 1) chart.data.labels[i] = labels[i];
        } else {
            chart.data.labels.splice(0, step.drop);
            for (const column of columns) {
                const target = chart.data.datasets[column.index].data;
                target.splice(0, step.drop);
                for (let i = step.kept; i < labels.length; i += 1) target.push(column.values[i]);
            }
            for (let i = step.kept; i < labels.length; i += 1) chart.data.labels.push(labels[i]);
        }
    } else {
        chart.data.labels = labels.slice();
        for (const column of columns) {
            chart.data.datasets[column.index].data = column.values.slice();
        }
        // A different window deserves a fresh fit rather than the old axis stretched to
        // cover two price levels at once.
        pinned.delete(chart);
    }

    drawn.set(chart, {
        labels: labels.slice(),
        columns: columns.map((c) => ({ index: c.index, values: c.values.slice() })),
    });
    return { appended, reloaded, shifted };
}

// Axis ends rounded out to a readable step. That rounding is also what keeps the plot still:
// a tick that nudges the day's high rarely crosses into the next step, so the line does not
// get rescaled - and shifted bodily up or down - once a second.
function niceBounds(lo, hi) {
    const range = Math.max(hi - lo, Math.abs(hi) * 0.02, 1e-6);
    const rough = range / 5;
    const magnitude = 10 ** Math.floor(Math.log10(rough));
    const step = ([1, 2, 2.5, 5, 10].find((m) => rough <= m * magnitude) ?? 10) * magnitude;
    const at = (value) => Number((value / step).toFixed(6));
    const bounds = {
        min: Number((Math.floor(at(lo)) * step).toFixed(6)),
        max: Number((Math.ceil(at(hi)) * step).toFixed(6)),
    };
    if (bounds.min === bounds.max) {
        const padding = Math.max(Math.abs(bounds.max), 1);
        bounds.min -= padding;
        bounds.max += padding;
    }
    return bounds;
}

// Returns true when the axis actually had to move, which is the signal that the update is
// not a plain append and should be animated after all.
function pinAxis(chart, id, values, { zero = false, scale = 1, slack = 0.5 } = {}) {
    const finite = values.filter((value) => Number.isFinite(value)).map((value) => value * scale);
    if (!finite.length) return false;
    const lo = zero ? 0 : Math.min(...finite);
    const hi = Math.max(...finite);
    const wanted = niceBounds(lo, hi);
    const held = (pinned.get(chart) || {})[id];
    let next = wanted;

    if (held) {
        const grows = wanted.min < held.min || wanted.max > held.max;
        const uses = (hi - lo) / Math.max(held.max - held.min, 1e-9);
        if (grows) {
            // Widen, but only on the side that needs it, so the rest of the plot stays put.
            next = { min: Math.min(held.min, wanted.min), max: Math.max(held.max, wanted.max) };
        } else if (uses > slack) {
            return false;
        }
    }

    const axis = chart.options.scales[id];
    if (axis.min === next.min && axis.max === next.max) return false;
    axis.min = next.min;
    axis.max = next.max;
    pinned.set(chart, { ...(pinned.get(chart) || {}), [id]: next });
    return true;
}

// Markers are keyed by position on the axis rather than by date, and a trade can land on a
// day that is already drawn, so they are handed over whole - but only when they differ.
function replacePoints(dataset, points) {
    const current = dataset.data;
    if (current.length === points.length
        && current.every((p, i) => p.x === points[i].x && p.y === points[i].y)) return false;
    dataset.data = points;
    return true;
}

function updateCharts(charts, state) {
    const rows = state.chart;
    const labels = rows.map((r) => r.date);
    const index = new Map(labels.map((date, i) => [date, i]));

    // Only this symbol's fills belong on this symbol's chart: a marker is drawn at the raw
    // trade price, so a $120 PEP fill on a $42 AAPL axis would drag the scale to fit it and
    // squash the candles the player is actually reading.
    const toMarkers = (side) => state.trades
        .filter((t) => t.side === side && t.ticker === state.focus && index.has(t.date))
        .map((t) => ({ x: index.get(t.date), y: t.price }));

    const price = charts.price;
    const closes = rows.map((r) => r.close);
    const sma20 = rows.map((r) => r.sma20);
    const sma50 = rows.map((r) => r.sma50);
    const volumes = rows.map((r) => r.volume);
    const buys = toMarkers('BUY');
    const sells = toMarkers('SELL');

    const result = applySeries(price, labels, [
        { index: 0, values: closes },
        { index: 1, values: sma20 },
        { index: 2, values: sma50 },
        { index: 5, values: volumes },
    ]);
    const marked = replacePoints(price.data.datasets[3], buys)
        + replacePoints(price.data.datasets[4], sells);
    // Every price on the plot decides the axis, markers included: a fill away from the
    // recent range would otherwise sit off the top or bottom of the panel.
    const lifted = pinAxis(price, 'y', [...closes, ...sma20, ...sma50, ...buys.map((b) => b.y), ...sells.map((s) => s.y)]);
    // Volume bars are read against each other, so they only need a ceiling - and one that
    // holds still: recomputing it every tick resized every bar in the plot for one new bar.
    const rescaled = pinAxis(price, 'volume', volumes, { zero: true, scale: 4, slack: 0.45 });
    draw(price, result.reloaded || result.shifted || lifted || rescaled || marked);

    const rsi = charts.rsi;
    const rsiOut = applySeries(rsi, labels, [
        { index: 0, values: rows.map((r) => r.rsi14) },
        { index: 1, values: labels.map(() => 70) },
        { index: 2, values: labels.map(() => 30) },
    ]);
    draw(rsi, rsiOut.reloaded || rsiOut.shifted);

    const macd = charts.macd;
    const hist = rows.map((r) => r.macd_hist);
    const macdOut = applySeries(macd, labels, [
        { index: 0, values: hist },
        { index: 1, values: rows.map((r) => r.macd) },
        { index: 2, values: rows.map((r) => r.macd_signal) },
    ]);
    macd.data.datasets[0].backgroundColor = hist.map((value) =>
        (value ?? 0) >= 0 ? 'rgba(45,212,191,0.6)' : 'rgba(251,113,133,0.6)');
    const macdAxis = pinAxis(macd, 'y', hist.concat(
        rows.map((r) => r.macd), rows.map((r) => r.macd_signal)));
    draw(macd, macdOut.reloaded || macdOut.shifted || macdAxis);
}

function updateBasketChart(chart, payload) {
    const series = payload.series || [];
    // Every symbol trades on its own calendar, so the axis is the union of their dates and
    // a symbol simply has a gap on a day it did not trade.
    const labels = [...new Set(series.flatMap((s) => s.points.map((p) => p.date)))].sort();
    const nameOf = (row) => (row.simulated ? `${row.ticker} ~` : row.ticker);

    // Rebuilding the dataset objects made Chart.js treat every line as brand new, so the
    // whole panel faded back in on each refresh. They are reused by label instead, and only
    // rebuilt when the set of symbols itself changes.
    const same = chart.data.datasets.length === series.length
        && series.every((row, i) => chart.data.datasets[i].label === nameOf(row));
    if (!same) {
        chart.data.datasets = series.map((row, i) => {
            const color = SERIES_COLORS[i % SERIES_COLORS.length];
            return {
                label: nameOf(row),
                data: [],
                borderColor: color,
                backgroundColor: color,
                borderWidth: 2,
                pointRadius: 0,
                spanGaps: true,
                tension: 0.1,
            };
        });
        chart.data.labels = [];
        drawn.delete(chart);
        pinned.delete(chart);
    }

    const columns = series.map((row, i) => {
        const byDate = new Map(row.points.map((p) => [p.date, p.index]));
        return { index: i, values: labels.map((date) => (byDate.has(date) ? byDate.get(date) : null)) };
    });
    const result = applySeries(chart, labels, columns);
    const lifted = pinAxis(chart, 'y', columns.flatMap((c) => c.values), { slack: 0.45 });
    draw(chart, result.reloaded || result.shifted || lifted);
}

function updateEquityChart(chart, payload) {
    const points = payload.equity || [];
    const labels = points.map((p) => p.date);
    const net = points.map((p) => p.net_worth);
    const result = applySeries(chart, labels, [
        { index: 0, values: net },
        { index: 1, values: labels.map(() => payload.starting_cash) },
    ]);
    const lifted = pinAxis(chart, 'y', net.concat(payload.starting_cash));
    draw(chart, result.reloaded || result.shifted || lifted);
}
