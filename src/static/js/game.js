const sessionId = document.body.dataset.sessionId;

// Past 2x the browser batches days per request instead of ticking faster, because a
// round trip to the cloud database costs more than the interval would allow.
const SPEEDS = [
    { label: '0.5x · 1 day/2s', ms: 2000, days: 1 },
    { label: '1x · 1 day/sec', ms: 1000, days: 1 },
    { label: '2x · 2 days/sec', ms: 500, days: 1 },
    { label: '5x · 5 days/sec', ms: 500, days: 3 },
    { label: '10x · 10 days/sec', ms: 500, days: 5 },
];

const charts = {
    price: createPriceChart(document.getElementById('price-chart')),
    rsi: createRsiChart(document.getElementById('rsi-chart')),
    macd: createMacdChart(document.getElementById('macd-chart')),
};

const el = (id) => document.getElementById(id);
const money = (value) => value == null ? '—' : `$${Number(value).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

let timer = null;
let inFlight = false;
let seenEventIds = new Set();
let latestState = null;

async function call(path, options = {}) {
    const response = await fetch(path, {
        headers: { 'Content-Type': 'application/json' },
        ...options,
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Request failed');
    return data;
}

function render(state) {
    latestState = state;
    el('sim-date').textContent = state.sim_date;
    el('current-price').textContent = money(state.today.close);

    const portfolio = state.portfolio;
    const position = portfolio.positions[0];
    el('cash').textContent = money(portfolio.cash_balance);
    el('net-worth').textContent = money(portfolio.net_worth);
    el('shares').textContent = position ? Number(position.shares).toLocaleString() : '0';
    el('avg-cost').textContent = position ? money(position.avg_cost) : '—';

    const unrealized = position ? Number(position.unrealized_pl) : null;
    const unrealizedEl = el('unrealized');
    unrealizedEl.textContent = unrealized == null ? '—' : money(unrealized);
    unrealizedEl.className = `tabular-nums ${unrealized > 0 ? 'text-emerald-400' : unrealized < 0 ? 'text-rose-400' : ''}`;

    const pct = portfolio.total_return_pct;
    const returnEl = el('return-pct');
    returnEl.textContent = `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%`;
    returnEl.className = `text-xl font-semibold tabular-nums ${pct >= 0 ? 'text-emerald-400' : 'text-rose-400'}`;

    updateCharts(charts, state);
    renderFeed(state.ai_feed);

    if (state.status === 'finished') finish(state);
}

function renderFeed(feed) {
    if (!feed.length) return;
    const container = el('ai-feed');
    container.innerHTML = '';
    for (const entry of feed) {
        seenEventIds.add(entry.id);
        container.appendChild(
            entry.type === 'PREDICTION' ? predictionCard(entry) : newsCard(entry));
    }
}

function predictionCard(entry) {
    const p = entry.payload;
    const tone = { up: 'text-emerald-400', down: 'text-rose-400' }[p.direction] || 'text-slate-300';
    const node = document.createElement('div');
    node.className = 'border border-violet-900/60 bg-violet-950/20 rounded-lg p-3';
    node.innerHTML = `
        <div class="flex items-center justify-between mb-1">
            <span class="text-xs font-semibold uppercase tracking-wide text-violet-400">AI read · ${entry.sim_date}</span>
            <span class="text-xs ${tone} font-semibold">${p.direction} ${Math.round((p.confidence ?? 0) * 100)}%</span>
        </div>
        <p class="text-slate-300 text-xs leading-relaxed">${p.rationale ?? ''}</p>
        ${(p.referenced_indicators || []).length ? `<div class="mt-2 flex flex-wrap gap-1">${p.referenced_indicators.map((i) => `<span class="text-[10px] bg-slate-800 rounded px-1.5 py-0.5 text-slate-400">${i}</span>`).join('')}</div>` : ''}
        ${p.what_to_watch ? `<p class="mt-2 text-[11px] text-slate-500">Watch: ${p.what_to_watch}</p>` : ''}
        <p class="mt-2 text-[10px] uppercase tracking-wide text-slate-600">Speculative · not financial advice</p>`;
    return node;
}

function newsCard(entry) {
    const e = entry.payload;
    const tone = e.sentiment > 0.15 ? 'text-emerald-400' : e.sentiment < -0.15 ? 'text-rose-400' : 'text-slate-400';
    const node = document.createElement('div');
    node.className = 'border border-slate-800 bg-slate-950/60 rounded-lg p-3';
    node.innerHTML = `
        <div class="flex items-center justify-between mb-1">
            <span class="text-xs font-semibold uppercase tracking-wide text-amber-500">Headline · ${entry.sim_date}</span>
            <span class="text-xs ${tone}">${e.sentiment > 0 ? '+' : ''}${Number(e.sentiment).toFixed(2)}</span>
        </div>
        <p class="text-slate-200 text-xs font-medium leading-snug">${e.headline ?? ''}</p>
        ${e.lesson ? `<p class="mt-2 text-[11px] text-slate-500">${e.lesson}</p>` : ''}
        <p class="mt-2 text-[10px] uppercase tracking-wide text-slate-600">Fictional · prices stay real</p>`;
    return node;
}

function logSignals(signals) {
    if (!signals.length) return;
    const log = el('signal-log');
    if (log.querySelector('p.text-slate-500')) log.innerHTML = '';
    for (const signal of signals) {
        const tone = signal.direction === 'bullish' ? 'border-emerald-800 bg-emerald-950/30'
            : signal.direction === 'bearish' ? 'border-rose-900 bg-rose-950/30'
                : 'border-slate-800 bg-slate-950/50';
        const node = document.createElement('div');
        node.className = `border rounded-lg p-2.5 ${tone}`;
        node.innerHTML = `
            <div class="flex justify-between items-baseline">
                <span class="text-xs font-semibold">${signal.name}</span>
                <span class="text-[10px] text-slate-500">${signal.date}</span>
            </div>
            <p class="text-[11px] text-slate-400 mt-1 leading-relaxed">${signal.message}</p>`;
        log.prepend(node);
        toast(signal);
    }
}

function toast(signal) {
    const tone = signal.direction === 'bullish' ? 'border-emerald-600' :
        signal.direction === 'bearish' ? 'border-rose-600' : 'border-slate-600';
    const node = document.createElement('div');
    node.className = `bg-slate-900 border-l-4 ${tone} border border-slate-800 rounded-lg p-3 shadow-xl`;
    node.innerHTML = `
        <div class="text-sm font-semibold mb-1">${signal.name}</div>
        <p class="text-xs text-slate-400 leading-relaxed">${signal.message}</p>`;
    el('toasts').appendChild(node);
    setTimeout(() => node.remove(), 9000);
}

function setStatus(text, active) {
    const pill = el('status-pill');
    pill.textContent = text;
    pill.className = `text-xs px-3 py-1 rounded-full ${active ? 'bg-emerald-950 text-emerald-400' : 'bg-slate-800 text-slate-400'}`;
}

async function advance(days = 1) {
    if (inFlight) return;
    inFlight = true;
    try {
        const state = await call(`/api/session/${sessionId}/advance`, {
            method: 'POST',
            body: JSON.stringify({ days }),
        });
        render(state);
        logSignals(state.signals || []);
        // AI runs in the background; pick the result up once it has landed.
        if ((state.pending_ai || []).length) setTimeout(refreshFeed, 6000);
    } catch (error) {
        console.error(error);
    } finally {
        inFlight = false;
    }
}

async function refreshFeed() {
    try {
        const state = await call(`/api/session/${sessionId}/state`);
        if (state.ai_feed.length !== (latestState?.ai_feed.length ?? 0)) renderFeed(state.ai_feed);
    } catch (error) {
        console.error(error);
    }
}

function play() {
    if (timer) return;
    const speed = SPEEDS[Number(el('speed').value)];
    timer = setInterval(() => advance(speed.days), speed.ms);
    el('play-btn').textContent = 'Pause';
    el('play-btn').className = 'bg-amber-600 hover:bg-amber-500 transition font-semibold rounded-lg px-5 py-2';
    setStatus('Playing', true);
}

function pause() {
    clearInterval(timer);
    timer = null;
    el('play-btn').textContent = 'Play';
    el('play-btn').className = 'bg-emerald-600 hover:bg-emerald-500 transition font-semibold rounded-lg px-5 py-2';
    setStatus('Paused', false);
}

function finish(state) {
    pause();
    el('summary-range').textContent = `${state.ticker} · ${state.start_date} to ${state.sim_date}`;
    const pct = state.portfolio.total_return_pct;
    const summary = el('summary-return');
    summary.textContent = `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%`;
    summary.className = `text-5xl font-bold tabular-nums mb-2 ${pct >= 0 ? 'text-emerald-400' : 'text-rose-400'}`;
    el('summary-net').textContent = money(state.portfolio.net_worth);
    el('summary-start').textContent = money(state.portfolio.starting_cash);
    const overlay = el('summary-overlay');
    overlay.classList.remove('hidden');
    overlay.classList.add('flex');
}

async function trade(side) {
    const errorBox = el('trade-error');
    errorBox.classList.add('hidden');
    try {
        const state = await call(`/api/session/${sessionId}/trade`, {
            method: 'POST',
            body: JSON.stringify({ side, shares: Number(el('share-qty').value) }),
        });
        render(state);
    } catch (error) {
        errorBox.textContent = error.message;
        errorBox.classList.remove('hidden');
    }
}

el('play-btn').addEventListener('click', () => (timer ? pause() : play()));
el('step-btn').addEventListener('click', () => advance(1));
el('buy-btn').addEventListener('click', () => trade('BUY'));
el('sell-btn').addEventListener('click', () => trade('SELL'));

el('speed').addEventListener('input', (event) => {
    el('speed-label').textContent = SPEEDS[Number(event.target.value)].label;
    if (timer) { pause(); play(); }
});

el('predict-btn').addEventListener('click', async () => {
    const button = el('predict-btn');
    button.disabled = true;
    button.textContent = 'Thinking…';
    try {
        await call(`/api/session/${sessionId}/predict`, { method: 'POST' });
        render(await call(`/api/session/${sessionId}/state`));
    } catch (error) {
        console.error(error);
    } finally {
        button.disabled = false;
        button.textContent = 'Ask for a read';
    }
});

call(`/api/session/${sessionId}/state`).then(render);
