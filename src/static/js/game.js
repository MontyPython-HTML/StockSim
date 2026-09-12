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
    basket: createBasketChart(document.getElementById('basket-chart')),
    equity: createEquityChart(document.getElementById('equity-chart')),
};

const el = (id) => document.getElementById(id);
const money = (value) => value == null ? '—' : `$${Number(value).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const signed = (value, digits = 2) => value == null ? '—' : `${value >= 0 ? '+' : ''}${Number(value).toFixed(digits)}`;
const pct = (value) => value == null ? '—' : `${signed(value)}%`;
const toneFor = (value) => value == null ? 'text-muted' : value > 0 ? 'text-up' : value < 0 ? 'text-down' : 'text-muted';

// The Play button is the one control that changes colour while running, so both of its
// states live here rather than being assembled out of string surgery in play()/pause().
const PRIMARY = 'rounded-full bg-brand px-8 py-3 text-lg font-semibold transition hover:bg-brand-soft';
const PLAYING = 'rounded-full bg-accent px-8 py-3 text-lg font-semibold text-ink transition hover:bg-accent-soft';

let timer = null;
let inFlight = false;
let latestState = null;
let latestSimulation = null;
let focus = null;
let companyNames = {};

async function call(path, options = {}) {
    const response = await fetch(path, {
        headers: { 'Content-Type': 'application/json' },
        ...options,
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Request failed');
    return data;
}

// --- rendering ------------------------------------------------------------

function held(position) {
    return latestState?.portfolio?.positions?.find((row) => row.ticker === position);
}

function renderWatchlist(state) {
    const list = el('watchlist');
    list.innerHTML = '';
    for (const quote of state.quotes) {
        const active = quote.ticker === state.focus;
        const position = held(quote.ticker);
        const button = document.createElement('button');
        button.type = 'button';
        // Outlined by default, blue only for the focused symbol: the same treatment the
        // mock gives its list rows.
        button.className = `flex items-center gap-4 rounded-2xl border px-5 py-3 text-left transition ${
            active ? 'border-brand bg-brand/10' : 'border-line-strong bg-transparent hover:border-muted'}`;
        button.innerHTML = `
            <span>
                <span class="block font-mono text-lg font-semibold">${quote.ticker}</span>
                <span class="block text-xs text-dim">${companyNames[quote.ticker] || ''}</span>
            </span>
            <span class="text-right">
                <span class="block tabular-nums">${money(quote.close)}</span>
                <span class="block text-xs tabular-nums ${toneFor(quote.change_pct)}">${pct(quote.change_pct)}</span>
            </span>
            ${position ? `<span class="rounded-full bg-accent/20 px-3 py-1 text-xs font-semibold tabular-nums text-accent-soft">${Number(position.shares).toLocaleString()}</span>` : ''}
            ${quote.simulated ? '<span class="rounded-full border border-warn/50 px-2 py-0.5 text-[10px] uppercase tracking-wide text-warn">sim</span>' : ''}`;
        button.addEventListener('click', () => setFocus(quote.ticker));
        list.appendChild(button);
    }
}

function renderTradePanel(state) {
    const select = el('trade-ticker');
    if ([...select.options].map((option) => option.value).join() !== state.tickers.join()) {
        select.innerHTML = state.tickers
            .map((ticker) => `<option value="${ticker}">${ticker}</option>`).join('');
    }
    select.value = state.focus;

    const quote = state.quotes.find((row) => row.ticker === state.focus) || {};
    el('trade-price').textContent = money(quote.close);

    const position = state.portfolio.positions.find((row) => row.ticker === state.focus);
    const badge = el('trade-position');
    badge.textContent = position ? `${Number(position.shares).toLocaleString()} held` : 'no position';
    badge.className = `rounded-full border px-3 py-1 text-xs ${position ? 'border-accent/50 bg-accent/10 text-accent-soft' : 'border-line text-muted'}`;

    el('trade-summary').innerHTML = position
        ? `Holding <span class="font-semibold tabular-nums">${Number(position.shares).toLocaleString()}</span> shares
           at ${money(position.avg_cost)} &middot; unrealized
           <span class="font-semibold tabular-nums ${toneFor(position.unrealized_pl)}">${money(position.unrealized_pl)}</span>`
        : 'You do not hold this symbol yet.';
}

function renderHoldings(state) {
    const body = el('holdings-body');
    const positions = state.portfolio.positions;
    el('positions').textContent = String(positions.length);
    if (!positions.length) {
        body.innerHTML = '<p class="py-2 text-sm text-dim">No open positions.</p>';
        return;
    }
    body.innerHTML = '';
    for (const position of positions) {
        const row = document.createElement('div');
        row.className = 'rounded-xl border border-line bg-ink-soft p-3';
        row.innerHTML = `
            <div class="flex items-baseline justify-between gap-2">
                <button type="button" class="focus-chip font-mono font-semibold transition hover:text-glow">${position.ticker}</button>
                <span class="text-sm tabular-nums text-muted">${Number(position.shares).toLocaleString()} sh</span>
            </div>
            <div class="mt-1 flex items-baseline justify-between gap-2">
                <span class="text-sm tabular-nums">${money(position.market_value)}</span>
                <span class="text-sm tabular-nums ${toneFor(position.unrealized_pl)}">${money(position.unrealized_pl)}</span>
            </div>
            <div class="mt-2 grid grid-cols-2 gap-2">
                <button type="button" data-side="BUY" class="row-trade rounded-lg border border-line py-1 text-xs text-muted transition hover:border-up/60 hover:text-up">Buy</button>
                <button type="button" data-side="SELL" class="row-trade rounded-lg border border-line py-1 text-xs text-muted transition hover:border-down/60 hover:text-down">Sell</button>
            </div>`;
        row.querySelector('.focus-chip').addEventListener('click', () => setFocus(position.ticker));
        // Trading straight from a holding is the point of a multi-position panel: the
        // form's symbol dropdown is one more thing to get wrong before you can act.
        for (const button of row.querySelectorAll('.row-trade')) {
            button.addEventListener('click', () => trade(button.dataset.side, position.ticker));
        }
        body.appendChild(row);
    }
}

function renderBasketNote(payload) {
    const series = payload.series || [];
    const note = el('basket-note');
    el('basket-window').textContent = `${payload.window_days} sessions to ${payload.sim_date}`;
    if (series.length < 2) {
        note.textContent = 'Add more symbols to compare how the basket moves together.';
        return;
    }
    const changes = series.map((row) => row.change_pct).filter((value) => value != null);
    const up = changes.filter((value) => value > 0).length;
    const majority = Math.max(up, changes.length - up);
    const spread = Math.max(...changes) - Math.min(...changes);
    const together = majority === changes.length;
    note.innerHTML = together
        ? `All ${changes.length} holdings moved the same way over this window (${spread.toFixed(1)} points apart). `
          + `<span class="text-warn">A basket like that is closer to one bet than a portfolio</span> — a sector-wide headline hits all of it at once.`
        : `Window moves range from ${Math.min(...changes).toFixed(1)}% to ${Math.max(...changes).toFixed(1)}% `
          + `(${spread.toFixed(1)} points apart), so these names are not moving as one.`;
}

function render(state) {
    latestState = state;
    focus = state.focus;
    el('sim-date').textContent = state.sim_date;

    const simulated = state.simulation && state.simulation.mode === 'simulated';
    el('mode-label').textContent = simulated
        ? `Simulated future · forked from real history on ${state.simulation.fork_date}`
        : 'Replaying real market history';

    el('focus-label').textContent = state.focus;
    el('focus-name').textContent = companyNames[state.focus] || '';

    const quote = state.quotes.find((row) => row.ticker === state.focus) || {};
    el('current-price').textContent = money(quote.close);
    const change = el('current-change');
    change.textContent = pct(quote.change_pct);
    change.className = `text-lg font-semibold tabular-nums ${toneFor(quote.change_pct)}`;

    const portfolio = state.portfolio;
    el('cash').textContent = money(portfolio.cash_balance);
    el('net-worth').textContent = money(portfolio.net_worth);
    const returnEl = el('return-pct');
    returnEl.textContent = `${signed(portfolio.total_return_pct)}%`;
    returnEl.className = `mt-1 text-3xl font-semibold tabular-nums ${toneFor(portfolio.total_return_pct)}`;

    renderWatchlist(state);
    renderTradePanel(state);
    renderHoldings(state);
    updateCharts(charts, state);
    renderFeed(state.ai_feed);

    if (state.status === 'finished') finish(state);
}

function renderFeed(feed) {
    if (!feed.length) return;
    const container = el('ai-feed');
    container.innerHTML = '';
    for (const entry of feed) {
        container.appendChild(
            entry.type === 'PREDICTION' ? predictionCard(entry)
                : entry.type === 'MARKET_SHOCK' ? shockCard(entry)
                    : newsCard(entry));
    }
}

function predictionCard(entry) {
    const p = entry.payload;
    const tone = { up: 'text-up', down: 'text-down' }[p.direction] || 'text-muted';
    const node = document.createElement('div');
    node.className = 'rounded-xl border border-brand/40 bg-brand/10 p-4';
    node.innerHTML = `
        <div class="flex items-center justify-between mb-1">
            <span class="text-xs font-semibold uppercase tracking-wide text-brand-soft">AI read · ${entry.ticker} · ${entry.sim_date}</span>
            <span class="text-xs ${tone} font-semibold">${p.direction} ${Math.round((p.confidence ?? 0) * 100)}%</span>
        </div>
        <p class="text-muted leading-relaxed">${p.rationale ?? ''}</p>
        ${(p.referenced_indicators || []).length ? `<div class="mt-2 flex flex-wrap gap-1">${p.referenced_indicators.map((i) => `<span class="text-xs border border-line rounded-full px-2 py-0.5 text-dim">${i}</span>`).join('')}</div>` : ''}
        ${p.what_to_watch ? `<p class="mt-2 text-xs text-dim">Watch: ${p.what_to_watch}</p>` : ''}
        <p class="mt-2 text-[11px] uppercase tracking-wide text-dim">Speculative · not financial advice</p>`;
    return node;
}

function shockCard(entry) {
    const e = entry.payload;
    const bullish = e.sentiment > 0.15;
    const bearish = e.sentiment < -0.15;
    const border = bullish ? 'border-up/50 bg-up/10'
        : bearish ? 'border-down/50 bg-down/10' : 'border-line bg-ink-soft';
    const scope = (e.scope || 'ticker').toLowerCase();
    // When a story is wider than one company, say so and name the names it hit: that is
    // the whole teaching point of a sector event.
    const label = scope === 'market'
        ? `Market-wide · ${e.sim_date}`
        : scope === 'sector'
            ? `Sector · ${e.sector || 'sector-wide'} · ${e.sim_date}`
            : `Market event · ${entry.ticker} · ${e.sim_date}`;
    const hit = (e.affected_tickers || [entry.ticker]);
    const node = document.createElement('div');
    node.className = `rounded-xl border p-4 ${border}`;
    node.innerHTML = `
        <div class="flex items-center justify-between mb-1">
            <span class="text-xs font-semibold uppercase tracking-wide text-warn">${label}</span>
            <span class="text-xs ${toneFor(e.sentiment)} font-semibold">${signed(e.sentiment)} · mag ${Number(e.magnitude ?? 0).toFixed(2)}</span>
        </div>
        <p class="text-white font-medium leading-snug">${e.headline ?? ''}</p>
        ${e.summary ? `<p class="mt-1 text-sm text-muted">${e.summary}</p>` : ''}
        ${hit.length > 1 ? `<div class="mt-2 flex flex-wrap gap-1">${hit.map((t) => `<span class="rounded-full border border-line px-2 py-0.5 font-mono text-xs text-muted">${t}</span>`).join('')}</div>` : ''}
        ${e.lesson ? `<p class="mt-2 text-xs text-dim">${e.lesson}</p>` : ''}
        <p class="mt-2 text-[11px] uppercase tracking-wide text-dim">
            ${e.source === 'gemini' ? 'Gemini-invented' : 'Simulator-invented'} · moved ${e.bars_affected ?? 0} generated sessions across ${hit.length} symbol${hit.length === 1 ? '' : 's'}
        </p>`;
    return node;
}

function newsCard(entry) {
    const e = entry.payload;
    const node = document.createElement('div');
    node.className = 'rounded-xl border border-line bg-ink-soft p-4';
    node.innerHTML = `
        <div class="flex items-center justify-between mb-1">
            <span class="text-xs font-semibold uppercase tracking-wide text-warn">Headline · ${entry.ticker} · ${entry.sim_date}</span>
            <span class="text-xs ${toneFor(e.sentiment)}">${signed(e.sentiment)}</span>
        </div>
        <p class="text-white font-medium leading-snug">${e.headline ?? ''}</p>
        ${e.lesson ? `<p class="mt-2 text-xs text-dim">${e.lesson}</p>` : ''}
        <p class="mt-2 text-[11px] uppercase tracking-wide text-dim">Fictional · prices stay real</p>`;
    return node;
}

function logSignals(signals) {
    if (!signals.length) return;
    const log = el('signal-log');
    if (log.querySelector('p.text-dim')) log.innerHTML = '';
    for (const signal of signals) {
        const tone = signal.direction === 'bullish' ? 'border-up/40 bg-up/10'
            : signal.direction === 'bearish' ? 'border-down/40 bg-down/10'
                : 'border-line bg-ink-soft';
        const node = document.createElement('div');
        node.className = `rounded-xl border p-3 ${tone}`;
        node.innerHTML = `
            <div class="flex justify-between items-baseline gap-2">
                <span class="font-mono text-xs text-muted">${signal.ticker}</span>
                <span class="flex-1 text-sm font-semibold">${signal.name}</span>
                <span class="text-xs text-dim">${signal.date}</span>
            </div>
            <p class="text-xs text-muted mt-1 leading-relaxed">${signal.message}</p>`;
        log.prepend(node);
        toast(`${signal.ticker} · ${signal.name}`, signal.message, signal.direction);
    }
}

function toast(title, message, direction) {
    const tone = direction === 'bullish' ? 'border-up' :
        direction === 'bearish' ? 'border-down' : 'border-line-strong';
    const node = document.createElement('div');
    node.className = `rounded-xl border border-line border-l-4 ${tone} bg-surface p-4 shadow-xl`;
    node.innerHTML = `<div class="font-semibold mb-1">${title}</div><p class="text-sm text-muted leading-relaxed">${message}</p>`;
    el('toasts').appendChild(node);
    setTimeout(() => node.remove(), 9000);
}

function setStatus(text, active) {
    const pill = el('status-pill');
    pill.textContent = text;
    pill.className = `rounded-full border px-4 py-1.5 text-sm ${active ? 'border-accent/50 bg-accent/10 text-accent-soft' : 'border-line text-muted'}`;
}

// --- interaction ----------------------------------------------------------

async function setFocus(ticker) {
    focus = ticker;
    try {
        render(await call(`/api/session/${sessionId}/state?focus=${ticker}`));
        refreshSimulation();
    } catch (error) {
        console.error(error);
    }
}

async function refreshBasket() {
    try {
        const payload = await call(`/api/session/${sessionId}/basket`);
        updateBasketChart(charts.basket, payload);
        updateEquityChart(charts.equity, payload);
        renderBasketNote(payload);
        const last = payload.equity?.at(-1);
        el('equity-note').textContent = last
            ? `${money(last.market_value)} at risk · ${money(last.cash)} idle`
            : '';
    } catch (error) {
        console.error(error);
    }
}

async function advance(days = 1) {
    if (inFlight) return;
    inFlight = true;
    try {
        const state = await call(`/api/session/${sessionId}/advance`, {
            method: 'POST',
            body: JSON.stringify({ days, focus }),
        });
        render(state);
        logSignals(state.signals || []);
        refreshBasket();
        // AI runs in the background; pick the result up once it has landed, and again a
        // little later because a scheduled shock shifts the forward curve too.
        if ((state.pending_ai || []).length) {
            setTimeout(refreshFeed, 6000);
            setTimeout(refreshFeed, 14000);
        }
    } catch (error) {
        console.error(error);
    } finally {
        inFlight = false;
    }
}

async function refreshFeed() {
    try {
        const state = await call(`/api/session/${sessionId}/state?focus=${focus}`);
        if (state.ai_feed.length !== (latestState?.ai_feed.length ?? 0)) {
            renderFeed(state.ai_feed);
            refreshSimulation();
            refreshBasket();
        }
    } catch (error) {
        console.error(error);
    }
}

function play() {
    if (timer) return;
    const speed = SPEEDS[Number(el('speed').value)];
    timer = setInterval(() => advance(speed.days), speed.ms);
    el('play-btn').textContent = 'Pause';
    el('play-btn').className = PLAYING;
    setStatus('Playing', true);
}

function pause() {
    clearInterval(timer);
    timer = null;
    el('play-btn').textContent = 'Play';
    el('play-btn').className = PRIMARY;
    setStatus('Paused', false);
}

function finish(state) {
    pause();
    el('summary-range').textContent = `${state.tickers.join(', ')} · ${state.start_date} to ${state.sim_date}`;
    const total = state.portfolio.total_return_pct;
    const summary = el('summary-return');
    summary.textContent = `${signed(total)}%`;
    summary.className = `mb-3 text-6xl font-bold tabular-nums ${toneFor(total)}`;
    el('summary-net').textContent = money(state.portfolio.net_worth);
    el('summary-start').textContent = money(state.portfolio.starting_cash);
    const overlay = el('summary-overlay');
    overlay.classList.remove('hidden');
    overlay.classList.add('flex');
}

async function trade(side, ticker) {
    const errorBox = el('trade-error');
    errorBox.classList.add('hidden');
    const target = ticker || el('trade-ticker').value;
    const shares = Number(el('share-qty').value);
    try {
        render(await call(`/api/session/${sessionId}/trade`, {
            method: 'POST',
            body: JSON.stringify({ ticker: target, side, shares, focus: focus || target }),
        }));
        refreshBasket();
        toast(`${side} ${shares} ${target}`, `Filled at the close on ${latestState.sim_date}.`, 'neutral');
    } catch (error) {
        errorBox.textContent = error.message;
        errorBox.classList.remove('hidden');
    }
}

// --- simulated future -----------------------------------------------------

function showSimulation(info) {
    latestSimulation = info;
    const active = !!info.active;
    el('sim-off').classList.toggle('hidden', active);
    el('sim-on').classList.toggle('hidden', !active);
    const pill = el('sim-state');
    pill.textContent = active ? 'generated' : 'real data only';
    pill.className = active
        ? 'rounded-full border border-accent/50 bg-accent/10 px-3 py-1 text-xs text-accent-soft'
        : 'rounded-full border border-line px-3 py-1 text-xs text-muted';
    if (!active) return;

    const config = info.config;
    el('sim-fork').textContent = config.fork_date;
    el('sim-bars').textContent = String(info.generated_bars);
    el('sim-drift').textContent = `${signed(config.annualized_drift_pct)}%`;
    el('sim-vol').textContent = `${config.annualized_volatility_pct}%`;
    el('shock-ticker').textContent = config.ticker;
    const others = info.tickers.filter((ticker) => ticker !== info.focus);
    el('sim-others').textContent = others.length
        ? `Also forked: ${others.join(', ')} — a sector-wide event will reach all of them, not just ${info.focus}.`
        : 'Every symbol in the basket has its own generated future.';
}

async function refreshSimulation() {
    try {
        showSimulation(await call(`/api/session/${sessionId}/simulation?focus=${focus || ''}`));
    } catch (error) {
        console.error(error);
    }
}

// --- wiring ---------------------------------------------------------------

el('play-btn').addEventListener('click', () => (timer ? pause() : play()));
el('step-btn').addEventListener('click', () => advance(1));
el('buy-btn').addEventListener('click', () => trade('BUY'));
el('sell-btn').addEventListener('click', () => trade('SELL'));
el('trade-ticker').addEventListener('change', (event) => setFocus(event.target.value));

for (const chip of document.querySelectorAll('.qty-chip')) {
    chip.addEventListener('click', () => {
        if (chip.dataset.qty === 'max') {
            const ticker = el('trade-ticker').value;
            const quote = latestState?.quotes?.find((row) => row.ticker === ticker);
            el('share-qty').value = quote?.close
                ? Math.floor(latestState.portfolio.cash_balance / quote.close) : 0;
        } else {
            el('share-qty').value = chip.dataset.qty;
        }
    });
}

el('sell-all-btn').addEventListener('click', async () => {
    const button = el('sell-all-btn');
    button.disabled = true;
    try {
        for (const position of latestState?.portfolio?.positions ?? []) {
            await trade('SELL', position.ticker);
        }
    } finally {
        button.disabled = false;
    }
});

el('speed').addEventListener('input', (event) => {
    el('speed-label').textContent = SPEEDS[Number(event.target.value)].label;
    if (timer) { pause(); play(); }
});

el('predict-btn').addEventListener('click', async () => {
    const button = el('predict-btn');
    const errorBox = el('ai-error');
    errorBox.classList.add('hidden');
    button.disabled = true;
    button.textContent = 'Thinking…';
    try {
        await call(`/api/session/${sessionId}/predict`, {
            method: 'POST',
            body: JSON.stringify({ ticker: focus }),
        });
        render(await call(`/api/session/${sessionId}/state?focus=${focus}`));
    } catch (error) {
        // Previously this only reached the console, which made the button look dead.
        errorBox.textContent = error.message;
        errorBox.classList.remove('hidden');
    } finally {
        button.disabled = false;
        button.textContent = 'Ask for a read';
    }
});

el('sim-fork-btn').addEventListener('click', async () => {
    const button = el('sim-fork-btn');
    const errorBox = el('sim-error');
    errorBox.classList.add('hidden');
    button.disabled = true;
    button.textContent = 'Generating…';
    try {
        render(await call(`/api/session/${sessionId}/simulate`, {
            method: 'POST', body: JSON.stringify({ focus }),
        }));
        await refreshSimulation();
        refreshBasket();
    } catch (error) {
        errorBox.textContent = error.message;
        errorBox.classList.remove('hidden');
    } finally {
        button.disabled = false;
        button.textContent = 'Generate a future';
    }
});

el('sim-extend-btn').addEventListener('click', async () => {
    const button = el('sim-extend-btn');
    button.disabled = true;
    button.textContent = 'Generating…';
    try {
        // Only the horizon changes: the seed and anchor are kept, so the bars already
        // on screen come back identical and only new days are appended.
        const horizon = (latestSimulation?.config?.horizon_days ?? 252) + 252;
        render(await call(`/api/session/${sessionId}/simulate`, {
            method: 'POST',
            body: JSON.stringify({ horizon_days: horizon, focus }),
        }));
        await refreshSimulation();
        refreshBasket();
    } catch (error) {
        console.error(error);
    } finally {
        button.disabled = false;
        button.textContent = 'Add another 252 days';
    }
});

el('shock-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = el('shock-btn');
    const errorBox = el('shock-error');
    errorBox.classList.add('hidden');
    button.disabled = true;
    button.textContent = 'Pushing…';
    try {
        const shock = await call(`/api/session/${sessionId}/shock`, {
            method: 'POST',
            body: JSON.stringify({
                ticker: latestSimulation?.config?.ticker || focus,
                scope: el('shock-scope').value,
                headline: el('shock-headline').value || 'Simulator-injected market event',
                sentiment: Number(el('shock-sentiment').value),
                magnitude: Number(el('shock-magnitude').value),
            }),
        });
        el('shock-headline').value = '';
        render(await call(`/api/session/${sessionId}/state?focus=${focus}`));
        await refreshSimulation();
        refreshBasket();
        const hit = shock.affected_tickers || [shock.ticker];
        toast(
            `${shock.scope === 'ticker' ? shock.ticker : `${hit.length} symbols`} · moved ${shock.bars_affected} sessions`,
            shock.headline,
            shock.sentiment > 0 ? 'bullish' : 'bearish',
        );
    } catch (error) {
        errorBox.textContent = error.message;
        errorBox.classList.remove('hidden');
    } finally {
        button.disabled = false;
        button.textContent = 'Push event into the future';
    }
});

// --- load -----------------------------------------------------------------

async function loadCompanyNames() {
    try {
        const data = await call('/api/universe');
        companyNames = Object.fromEntries(data.tickers.map((row) => [row.ticker, row.company_name]));
    } catch (error) {
        console.error(error);
    }
}

async function checkAI() {
    try {
        const status = await call('/api/ai/status');
        if (!status.available) {
            const banner = el('ai-status');
            banner.textContent = `${status.detail} Everything else - charts, trades, basket views and the simulated market - works without it.`;
            banner.classList.remove('hidden');
            el('predict-btn').disabled = true;
            el('predict-btn').className = 'cursor-not-allowed rounded-full border border-line px-4 py-2 text-sm font-semibold text-dim';
        }
    } catch (error) {
        console.error(error);
    }
}

(async function start() {
    await loadCompanyNames();
    checkAI();
    render(await call(`/api/session/${sessionId}/state`));
    refreshSimulation();
    refreshBasket();
})();
