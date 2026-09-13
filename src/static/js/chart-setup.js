/* TradingView Lightweight Charts setup and update helpers.

   Colours mirror the design tokens in static/input/input.css: near-black panels, a cyan
   price line, yellow for the 20-day average and MACD signal, blue for the 50-day average
   and the MACD line - the colours the lessons name. Every tick replaces the whole window
   with setData instead of tweening between arrays, so a sliding window steps cleanly
   instead of wobbling every point into its neighbour's place. */

const LWC = LightweightCharts;

const TICK = '#6b7280';
const GRID = 'rgba(154, 163, 176, 0.06)';
const CROSSHAIR = 'rgba(154, 163, 176, 0.45)';
const LABEL_BG = '#262a30';
const FONT = 'ui-sans-serif, system-ui, sans-serif';
// Price, RSI and MACD share a crosshair, so their plot areas have to line up day for day.
const AXIS_WIDTH = 72;

const C = {
    glow: '#22d3ee',
    brand: '#3b82f6',
    warn: '#f0b429',
    up: '#2dd4bf',
    down: '#fb7185',
    muted: '#9aa3b0',
};

const SERIES_COLORS = [
    '#3b82f6', '#2dd4bf', '#22d3ee', '#f0b429', '#8b5cf6',
    '#fb7185', '#34d399', '#f472b6', '#60a5fa', '#a3e635',
];

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function fade(hex, alpha) {
    const n = parseInt(hex.slice(1), 16);
    return `rgba(${n >> 16}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

function dayKey(time) {
    if (typeof time === 'string') return time.slice(0, 10);
    if (typeof time === 'number') return new Date(time * 1000).toISOString().slice(0, 10);
    if (time?.year) return `${time.year}-${String(time.month).padStart(2, '0')}-${String(time.day).padStart(2, '0')}`;
    return null;
}

function formatDay(key) {
    const [year, month, day] = key.split('-');
    return `${MONTHS[Number(month) - 1]} ${Number(day)}, ${year}`;
}

// A missing value becomes a whitespace point, which keeps every chart on the same days.
const toPoint = (time, value) => (value == null ? { time } : { time, value });

const usd = (value) => (value == null ? '—'
    : `$${Number(value).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`);

// Whole dollars: "$10.0k" rounding repeated the same label down a narrow account axis.
const wholeDollars = (value) => (Number.isFinite(Number(value)) ? `$${Math.round(value).toLocaleString('en-US')}` : '');

function compactNumber(value) {
    const amount = Number(value);
    if (!Number.isFinite(amount)) return '—';
    if (amount >= 1e9) return `${(amount / 1e9).toFixed(1)}B`;
    if (amount >= 1e6) return `${(amount / 1e6).toFixed(1)}M`;
    if (amount >= 1e3) return `${(amount / 1e3).toFixed(1)}K`;
    return String(Math.round(amount));
}

const fixed = (value, digits) => (value == null ? '—' : Number(value).toFixed(digits));
const QUIET = { priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false };

function createView(container, { magnet = true } = {}) {
    const crosshairLine = { color: CROSSHAIR, style: LWC.LineStyle.Dashed, labelBackgroundColor: LABEL_BG };
    // Magnet snaps the horizontal line to whichever series is nearest, often an average rather
    // than the price, so only the vertical line is drawn and the readout carries the values.
    const hiddenLine = { ...crosshairLine, visible: false, labelVisible: false };
    const chart = LWC.createChart(container, {
        autoSize: true,
        layout: {
            background: { type: LWC.ColorType.Solid, color: 'transparent' },
            textColor: TICK, fontFamily: FONT, fontSize: 11,
            // Credited once in text under the price chart instead of a badge on all five.
            attributionLogo: false,
        },
        grid: { vertLines: { visible: false }, horzLines: { color: GRID } },
        rightPriceScale: {
            borderVisible: false, entireTextOnly: true, minimumWidth: AXIS_WIDTH,
            scaleMargins: { top: 0.16, bottom: 0.06 },
        },
        timeScale: {
            borderVisible: false, rightOffset: 2, lockVisibleTimeRangeOnResize: true,
            tickMarkFormatter: (time, type) => {
                const [year, month, day] = dayKey(time).split('-');
                if (type === LWC.TickMarkType.Year) return year;
                if (type === LWC.TickMarkType.Month) return MONTHS[Number(month) - 1];
                return `${MONTHS[Number(month) - 1]} ${Number(day)}`;
            },
        },
        crosshair: {
            mode: magnet ? LWC.CrosshairMode.Magnet : LWC.CrosshairMode.Normal,
            vertLine: crosshairLine,
            horzLine: hiddenLine,
        },
        // The window slides every tick, so a drag or zoom would be thrown away a second
        // later; leaving these off also lets the mouse wheel scroll the page.
        handleScroll: false,
        handleScale: false,
        localization: { dateFormat: 'MMM dd, yyyy' },
    });

    const readout = document.createElement('div');
    readout.className = 'pointer-events-none absolute left-1 top-0 z-10 flex max-w-[calc(100%-5rem)] '
        + 'flex-wrap items-center gap-x-4 gap-y-1 text-xs tabular-nums';
    container.appendChild(readout);

    const view = {
        chart, container, readout, group: null,
        rows: new Map(), last: null, hovered: null,
        describe: () => [], anchor: () => null, paint: null,
    };
    chart.subscribeCrosshairMove((param) => {
        if (view.group && view.group.active !== view) return;
        view.hovered = param.point ? dayKey(param.time) : null;
        paintReadout(view);
    });
    return view;
}

function readoutItem({ label, value, color, strong }) {
    const item = document.createElement('span');
    item.className = 'flex items-center gap-1.5 whitespace-nowrap';
    if (color) {
        const dot = document.createElement('span');
        dot.className = 'h-2 w-2 rounded-full';
        dot.style.background = color;
        item.appendChild(dot);
    }
    if (label) {
        const name = document.createElement('span');
        name.className = 'text-dim';
        name.textContent = label;
        item.appendChild(name);
    }
    const text = document.createElement('span');
    text.className = strong ? 'font-medium text-white' : 'text-muted';
    text.textContent = value;
    item.appendChild(text);
    return item;
}

// Hovering shows that day's numbers; otherwise the readout sits on the latest day.
function paintReadout(view) {
    const key = view.hovered && view.rows.has(view.hovered) ? view.hovered : view.last;
    const row = key ? view.rows.get(key) : null;
    view.readout.replaceChildren(...(row ? view.describe(row, key) : []).map(readoutItem));
    view.paint?.(key);
}

function setRows(view, entries) {
    view.rows = new Map(entries);
    view.last = entries.length ? entries[entries.length - 1][0] : null;
    view.chart.timeScale().fitContent();
    paintReadout(view);
}

function linkCrosshairs(views) {
    const group = { active: null };
    for (const view of views) {
        view.group = group;
        view.container.addEventListener('pointerenter', () => { group.active = view; });
        view.container.addEventListener('pointerleave', () => {
            if (group.active !== view) return;
            group.active = null;
            for (const other of views) {
                if (other !== view) other.chart.clearCrosshairPosition();
                other.hovered = null;
                paintReadout(other);
            }
        });
        view.chart.subscribeCrosshairMove((param) => {
            if (group.active !== view) return;
            const key = param.point ? dayKey(param.time) : null;
            for (const other of views) {
                if (other === view) continue;
                const anchor = key && other.anchor(key);
                if (anchor) other.chart.setCrosshairPosition(anchor.value, key, anchor.series);
                else other.chart.clearCrosshairPosition();
                other.hovered = key;
                paintReadout(other);
            }
        });
    }
}

function createPriceChart(container) {
    const view = createView(container);
    const { chart } = view;
    const close = chart.addAreaSeries({
        lineColor: C.glow, topColor: fade(C.glow, 0.22), bottomColor: fade(C.glow, 0), lineWidth: 2,
        priceLineColor: fade(C.glow, 0.5), priceLineStyle: LWC.LineStyle.Dotted,
        crosshairMarkerRadius: 4, crosshairMarkerBorderColor: '#16181c', crosshairMarkerBorderWidth: 2,
        lastPriceAnimation: LWC.LastPriceAnimationMode.Continuous,
    });
    close.priceScale().applyOptions({ scaleMargins: { top: 0.14, bottom: 0.24 } });
    const sma20 = chart.addLineSeries({ ...QUIET, color: fade(C.warn, 0.9), lineWidth: 2 });
    const sma50 = chart.addLineSeries({ ...QUIET, color: fade(C.brand, 0.9), lineWidth: 2 });
    const volume = chart.addHistogramSeries({
        priceScaleId: 'volume', priceFormat: { type: 'volume' }, priceLineVisible: false, lastValueVisible: false,
    });
    chart.priceScale('volume').applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });

    Object.assign(view, {
        series: { close, sma20, sma50, volume },
        shown: { trend: true, volume: true },
        fills: new Map(),
        anchor: (key) => {
            const row = view.rows.get(key);
            return row?.close == null ? null : { series: close, value: row.close };
        },
        describe: (row, key) => [
            { value: formatDay(key), strong: true },
            { label: 'Price', value: usd(row.close), color: C.glow },
            ...(view.shown.trend ? [
                { label: '20-day avg', value: usd(row.sma20), color: C.warn },
                { label: '50-day avg', value: usd(row.sma50), color: C.brand },
            ] : []),
            ...(view.shown.volume ? [{ label: 'Volume', value: compactNumber(row.volume) }] : []),
            ...(view.fills.get(key) || []).map((fill) => ({
                label: fill.side === 'BUY' ? 'You bought at' : 'You sold at',
                value: usd(fill.price),
                color: fill.side === 'BUY' ? C.up : C.down,
            })),
        ],
    });
    return view;
}

function setPriceOverlays(view, shown) {
    Object.assign(view.shown, shown);
    view.series.sma20.applyOptions({ visible: view.shown.trend });
    view.series.sma50.applyOptions({ visible: view.shown.trend });
    view.series.volume.applyOptions({ visible: view.shown.volume });
    paintReadout(view);
}

function createRsiChart(container) {
    const view = createView(container);
    const line = view.chart.addLineSeries({
        color: C.glow, lineWidth: 2, priceLineVisible: false, crosshairMarkerRadius: 3,
        priceFormat: { type: 'price', precision: 1, minMove: 0.1 },
        autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 100 } }),
    });
    line.priceScale().applyOptions({ scaleMargins: { top: 0.14, bottom: 0.04 } });
    for (const [price, color, title] of [[70, C.down, 'Overbought'], [30, C.up, 'Oversold']]) {
        line.createPriceLine({
            price, color: fade(color, 0.55), lineWidth: 1, lineStyle: LWC.LineStyle.Dashed,
            axisLabelVisible: true, title,
        });
    }

    Object.assign(view, {
        series: { line },
        anchor: (key) => {
            const row = view.rows.get(key);
            return row?.rsi14 == null ? null : { series: line, value: row.rsi14 };
        },
        describe: (row, key) => {
            const zone = row.rsi14 >= 70 ? 'went up fast' : row.rsi14 <= 30 ? 'went down fast' : null;
            return [
                { label: 'RSI', value: fixed(row.rsi14, 1), color: C.glow },
                ...(zone ? [{ value: zone }] : []),
            ];
        },
    });
    return view;
}

function createMacdChart(container) {
    const view = createView(container);
    const { chart } = view;
    const bars = chart.addHistogramSeries({ ...QUIET, priceFormat: { type: 'price', precision: 2, minMove: 0.01 } });
    bars.createPriceLine({
        price: 0, color: 'rgba(154, 163, 176, 0.25)', lineWidth: 1,
        lineStyle: LWC.LineStyle.Solid, axisLabelVisible: false,
    });
    const macd = chart.addLineSeries({ color: C.brand, lineWidth: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerRadius: 3 });
    const signal = chart.addLineSeries({ ...QUIET, color: C.warn, lineWidth: 2 });

    Object.assign(view, {
        series: { bars, macd, signal },
        anchor: (key) => {
            const row = view.rows.get(key);
            return row?.macd == null ? null : { series: macd, value: row.macd };
        },
        describe: (row) => [
            { label: 'MACD', value: fixed(row.macd, 2), color: C.brand },
            { label: 'Signal', value: fixed(row.macd_signal, 2), color: C.warn },
            { label: 'Bars', value: fixed(row.macd_hist, 2), color: (row.macd_hist ?? 0) >= 0 ? C.up : C.down },
        ],
    });
    return view;
}

function createBasketChart(container) {
    const view = createView(container, { magnet: false });
    const legend = document.createElement('div');
    legend.className = 'mt-3 flex flex-wrap gap-2 text-xs tabular-nums';
    container.after(legend);

    Object.assign(view, {
        legend,
        lines: new Map(),
        values: new Map(),
        muted: new Set(),
        signature: null,
        describe: (row, key) => [{ value: formatDay(key), strong: true }],
        paint: (key) => {
            for (const button of legend.children) {
                const value = key ? view.values.get(button.dataset.ticker)?.get(key) : null;
                button.querySelector('[data-value]').textContent = fixed(value, 1);
            }
        },
    });
    return view;
}

function basketLegendButton(view, ticker, label, color) {
    const button = document.createElement('button');
    button.type = 'button';
    button.dataset.ticker = ticker;
    button.className = 'flex items-center gap-1.5 rounded-full border border-line px-2.5 py-1 transition hover:border-line-strong';
    button.innerHTML = '<span class="h-2 w-2 rounded-full"></span><span class="font-mono text-muted"></span>'
        + '<span data-value class="text-dim"></span>';
    button.children[0].style.background = color;
    button.children[1].textContent = label;
    const sync = () => {
        const muted = view.muted.has(ticker);
        button.classList.toggle('opacity-40', muted);
        button.title = muted ? `Show ${ticker}` : `Hide ${ticker}`;
        view.lines.get(ticker)?.applyOptions({ visible: !muted });
    };
    button.addEventListener('click', () => {
        if (view.muted.has(ticker)) view.muted.delete(ticker);
        else view.muted.add(ticker);
        sync();
    });
    sync();
    return button;
}

function updateBasketChart(view, payload) {
    const series = payload.series || [];
    // Every symbol trades on its own calendar, so the axis is the union of their dates.
    const times = [...new Set(series.flatMap((s) => s.points.map((p) => dayKey(p.date))))].sort();

    const signature = series.map((row) => `${row.ticker}${row.simulated ? '~' : ''}`).join(',');
    if (signature !== view.signature) {
        for (const line of view.lines.values()) view.chart.removeSeries(line);
        view.lines.clear();
        view.legend.replaceChildren();
        series.forEach((row, i) => {
            const color = SERIES_COLORS[i % SERIES_COLORS.length];
            const line = view.chart.addLineSeries({
                color, lineWidth: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerRadius: 3,
                priceFormat: { type: 'price', precision: 1, minMove: 0.1 },
            });
            if (i === 0) {
                line.createPriceLine({
                    price: 100, color: 'rgba(154, 163, 176, 0.35)', lineWidth: 1,
                    lineStyle: LWC.LineStyle.Dashed, axisLabelVisible: false,
                });
            }
            view.lines.set(row.ticker, line);
            view.legend.appendChild(basketLegendButton(view, row.ticker, row.simulated ? `${row.ticker} ~` : row.ticker, color));
        });
        view.muted = new Set([...view.muted].filter((ticker) => view.lines.has(ticker)));
        view.signature = signature;
    }

    view.values = new Map(series.map((row) => [row.ticker, new Map(row.points.map((p) => [dayKey(p.date), p.index]))]));
    for (const row of series) {
        const byDate = view.values.get(row.ticker);
        view.lines.get(row.ticker).setData(times.map((time) => toPoint(time, byDate.get(time))));
    }
    setRows(view, times.map((time) => [time, time]));
}

function createEquityChart(container) {
    const view = createView(container);
    const line = view.chart.addBaselineSeries({
        baseValue: { type: 'price', price: 0 }, lineWidth: 2,
        topLineColor: C.up, topFillColor1: fade(C.up, 0.26), topFillColor2: fade(C.up, 0.02),
        bottomLineColor: C.down, bottomFillColor1: fade(C.down, 0.02), bottomFillColor2: fade(C.down, 0.26),
        priceLineVisible: false, crosshairMarkerRadius: 4, crosshairMarkerBorderColor: '#16181c',
        priceFormat: { type: 'custom', formatter: wholeDollars, minMove: 0.01 },
    });

    Object.assign(view, {
        series: { line },
        start: null,
        startLine: null,
        describe: (row, key) => {
            const change = view.start ? ((row.net_worth - view.start) / view.start) * 100 : null;
            const tone = change == null || change >= 0 ? C.up : C.down;
            return [
                { value: formatDay(key), strong: true },
                { label: 'Worth', value: usd(row.net_worth), color: tone },
                ...(change == null ? [] : [{ value: `${change >= 0 ? '+' : ''}${change.toFixed(2)}%` }]),
            ];
        },
    });
    return view;
}

function updateEquityChart(view, payload) {
    const points = payload.equity || [];
    const start = Number(payload.starting_cash);
    if (Number.isFinite(start) && start !== view.start) {
        view.start = start;
        // A hair under the start, so an untouched account reads as even rather than red.
        view.series.line.applyOptions({ baseValue: { type: 'price', price: start - 0.01 } });
        const options = {
            price: start, color: 'rgba(154, 163, 176, 0.45)', lineWidth: 1,
            lineStyle: LWC.LineStyle.Dashed, axisLabelVisible: false, title: 'Start',
        };
        if (view.startLine) view.startLine.applyOptions(options);
        else view.startLine = view.series.line.createPriceLine(options);
    }
    const entries = points.map((p) => [dayKey(p.date), p]);
    view.series.line.setData(entries.map(([time, p]) => toPoint(time, p.net_worth)));
    setRows(view, entries);
}

function updateCharts(charts, state) {
    const entries = state.chart.map((row) => [dayKey(row.date), row]);
    const times = entries.map(([time]) => time);
    const series = (view, name, pick) => view.series[name].setData(entries.map(([time, row]) => toPoint(time, pick(row))));

    const price = charts.price;
    series(price, 'close', (row) => row.close);
    series(price, 'sma20', (row) => row.sma20);
    series(price, 'sma50', (row) => row.sma50);
    price.series.volume.setData(entries.map(([time, row], i) => {
        if (row.volume == null) return { time };
        const prev = i ? entries[i - 1][1].close : null;
        const down = prev != null && row.close != null && row.close < prev;
        return { time, value: row.volume, color: fade(down ? C.down : C.up, 0.22) };
    }));

    // Only this symbol's fills belong on this symbol's chart.
    const onChart = new Set(times);
    const fills = state.trades
        .filter((t) => t.ticker === state.focus && onChart.has(dayKey(t.date)))
        .map((t) => ({ ...t, time: dayKey(t.date) }))
        .sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0));
    price.series.close.setMarkers(fills.map((t) => (t.side === 'BUY'
        ? { time: t.time, position: 'belowBar', shape: 'arrowUp', color: C.up }
        : { time: t.time, position: 'aboveBar', shape: 'arrowDown', color: C.down })));
    price.fills = new Map();
    for (const fill of fills) price.fills.set(fill.time, [...(price.fills.get(fill.time) || []), fill]);
    setRows(price, entries);

    const rsi = charts.rsi;
    series(rsi, 'line', (row) => row.rsi14);
    setRows(rsi, entries);

    const macd = charts.macd;
    macd.series.bars.setData(entries.map(([time, row]) => (row.macd_hist == null ? { time }
        : { time, value: row.macd_hist, color: fade(row.macd_hist >= 0 ? C.up : C.down, 0.55) })));
    series(macd, 'macd', (row) => row.macd);
    series(macd, 'signal', (row) => row.macd_signal);
    setRows(macd, entries);
}
