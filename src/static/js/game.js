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
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

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
const BASKET_REFRESH_MS = 500;
let basketTimer = null;
let basketInFlight = false;
let basketQueued = false;
let basketSignature = null;

// AI work lands off the tick that queued it, so a paused clock has to go and collect it.
// A running clock does not: the next advance response already carries the whole feed.
const FEED_REFRESH_MS = 5000;
let feedTimer = null;

// Teacher mode: the lesson a tick queues arrives seconds later on a worker thread, so the
// page stops the clock immediately, opens the card in a waiting state, and polls until the
// lesson lands. `shownLessons` stops a lesson already taught from re-opening every time the
// feed is re-rendered.
const TEACHER_KEY = 'tradingTeacher.teacherMode';
const TOUR_KEY = 'tradingTeacher.tourDone';
const LESSON_POLL_MS = 1200;
// Past this the lesson is almost certainly not coming (no key, dead subprocess, Gemini
// rate-limited). The player is let go rather than left staring at a spinner.
const LESSON_WAIT_MS = 30000;
let teacherMode = true;
let awaitingLesson = null;
let lessonPollTimer = null;
let lessonWaitStarted = 0;
const shownLessons = new Set();

// Signals live behind a drawer, so the page has to remember how many have arrived and how
// many of those the player has actually looked at.
let signalsOpen = false;
let signalTotal = 0;
let signalUnread = 0;
const signalSymbols = new Set();

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

function renderBills(state) {
    const bills = state.expenses;
    if (!bills) return;

    const overdrawn = state.portfolio.overdrawn;
    el('overdrawn-banner').classList.toggle('hidden', !overdrawn);
    el('bills-panel').className = `rounded-3xl border p-6 ${
        overdrawn ? 'border-down/50 bg-down/5' : 'border-line bg-surface'}`;

    const runway = el('bills-runway');
    // Months of runway is the number that should change how much gets invested, so it is
    // coloured like a warning long before the balance actually goes red.
    const months = bills.months_of_runway;
    runway.textContent = months == null ? (bills.paycheck && bills.bill_count ? 'paycheck covers bills' : 'no bills')
        : months < 0 ? 'overdrawn'
            : `${months} months of runway`;
    runway.className = 'rounded-full border px-3 py-1 text-xs ' + (
        months == null ? 'border-line text-muted'
            : months < 1 ? 'border-down/50 bg-down/10 text-down'
                : months < 3 ? 'border-warn/50 bg-warn/10 text-warn'
                    : 'border-line text-muted');

    el('bills-total').textContent = bills.bill_count
        ? `${bills.bill_count} standing orders · ${money(bills.monthly_total)} a month out of your cash`
        : '';

    const list = el('bills-list');
    const upcoming = bills.upcoming || [];
    if (!upcoming.length) {
        list.innerHTML = '<p class="text-xs text-dim">No standing orders on this account.</p>';
    } else {
        list.innerHTML = upcoming.map((item) => {
            const income = item.kind === 'salary';
            const tone = income ? 'text-up' : item.days_away <= 7 ? 'text-warn' : 'text-muted';
            return `
            <div class="flex items-baseline justify-between gap-3 rounded-xl border border-line bg-ink-soft px-3 py-2">
                <span>
                    <span class="block font-medium">${esc(item.label)}</span>
                    <span class="block text-xs text-dim">${item.due_date}${item.days_away <= 7 ? ` · in ${item.days_away} days` : ''}</span>
                </span>
                <span class="shrink-0 tabular-nums ${tone}">${income ? '+' : ''}${money(item.amount)}</span>
            </div>`;
        }).join('');
    }

    const totals = [];
    if (bills.paid_to_date) totals.push(`${money(bills.paid_to_date)} paid in bills`);
    if (bills.earned_to_date) totals.push(`${money(bills.earned_to_date)} earned`);
    if (bills.missed_to_date) totals.push(`${money(bills.missed_to_date)} missed`);
    el('bills-paid').textContent = totals.length
        ? `${totals.join(' · ')} so far this run. Your trading is ${signed(state.portfolio.trading_return_pct)}% `
          + `on its own, ${signed(state.portfolio.total_return_pct)}% after bills and pay.`
        : '';
}

function renderBank(state) {
    const bank = state.bank;
    if (!bank) return;
    const customer = bank.customer;
    const account = bank.account;
    const bills = state.expenses || {};
    const paycheck = bills.paycheck;

    el('bank-source').textContent = bank.source;
    el('bank-holder').textContent = customer?.name || 'Bank customer';
    el('bank-account').textContent = [
        account && [account.nickname, account.type, account.number].filter(Boolean).join(' · '),
        customer?.city && `${customer.city}, ${customer.state}`,
    ].filter(Boolean).join(' · ');
    el('bank-start').textContent = money(state.portfolio.starting_cash);
    el('bank-paycheck').textContent = paycheck ? `${money(paycheck.amount)} / 2 weeks` : 'None';
    el('bank-note').textContent = (bills.bill_count
        ? `${bills.bill_count} bills take ${money(bills.monthly_total)} a month out of this account`
        : 'No bills come out of this account')
        + (paycheck ? `, and your paycheck brings in about ${money(paycheck.monthly)}.` : '.')
        + ' If your cash cannot cover a bill, the bank sells your shares to pay it.';
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
    renderBills(state);
    renderBank(state);
    updateCharts(charts, state);
    renderFeed(state.ai_feed);
    renderPatterns(state.patterns);

    if (state.status === 'finished') finish(state);
}

function renderFeed(feed) {
    if (!feed.length) return;
    noteLessons(feed);
    const container = el('ai-feed');
    container.innerHTML = '';
    for (const entry of feed) {
        container.appendChild(
            entry.type === 'PREDICTION' ? predictionCard(entry)
                : entry.type === 'MARKET_SHOCK' ? shockCard(entry)
                    : entry.type === 'PATTERN_LESSON' ? lessonCard(entry)
                        : newsCard(entry));
    }
}

// --- pattern school -------------------------------------------------------

const FAMILY_TONE = {
    trend: 'border-brand/50 bg-brand/10 text-brand-soft',
    momentum: 'border-accent/50 bg-accent/10 text-accent-soft',
    participation: 'border-warn/50 bg-warn/10 text-warn',
};

function renderPatterns(rows) {
    if (!rows) return;
    const list = el('pattern-list');
    const learned = rows.filter((row) => row.taught);
    el('pattern-progress').textContent = `${learned.length} of ${rows.length} learned`;
    // The next unlearned pattern is the one to be looking for, so it is called out.
    const next = rows.find((row) => !row.taught);

    list.innerHTML = '';
    for (const row of rows) {
        const node = document.createElement('div');
        const tone = FAMILY_TONE[row.family] || 'border-line text-muted';
        if (row.taught) {
            node.className = 'rounded-xl border border-line bg-ink-soft p-3';
            node.innerHTML = `
                <div class="flex items-center justify-between gap-2">
                    <span class="font-medium">${row.name}</span>
                    <span class="shrink-0 rounded-full border px-2 py-0.5 text-[10px] uppercase tracking-wide ${tone}">${row.family}</span>
                </div>
                <p class="mt-1 text-xs text-dim">
                    ${row.lessons} lesson${row.lessons === 1 ? '' : 's'} · first seen in
                    <span class="font-mono text-muted">${row.first_ticker || '—'}</span> on ${row.first_taught || '—'}
                </p>`;
        } else {
            const hint = row.slug === next?.slug ? 'Look out for this one next.' : 'Not seen yet.';
            node.className = 'rounded-xl border border-dashed border-line p-3 text-muted';
            node.innerHTML = `
                <div class="flex items-center justify-between gap-2">
                    <span class="font-medium">${row.name}</span>
                    <span class="shrink-0 rounded-full border border-line px-2 py-0.5 text-[10px] uppercase tracking-wide text-dim">${row.family}</span>
                </div>
                <p class="mt-1 text-xs text-dim">${hint}</p>`;
        }
        list.appendChild(node);
    }
}

function lessonCard(entry) {
    const e = entry.payload;
    const tone = FAMILY_TONE[e.family] || 'border-line bg-ink-soft';
    const readings = e.context || {};
    const steps = e.how_to_spot || [];
    const node = document.createElement('div');
    node.className = `rounded-xl border ${tone.split(' ')[0] || 'border-line'} bg-ink-soft p-4`;
    // The numbers the lesson was written against, so the card points at the chart on
    // screen rather than describing the pattern in the abstract.
    const figures = [
        ['close', readings.close],
        ['RSI', readings.rsi14],
        ['SMA20', readings.sma20],
        ['SMA50', readings.sma50],
    ].filter(([, value]) => value != null && value !== undefined)
        .map(([label, value]) => `<span class="rounded-full border border-line px-2 py-0.5 font-mono text-xs text-muted">${label} ${Number(value).toFixed(2)}</span>`)
        .join('');
    node.innerHTML = `
        <div class="flex items-center justify-between mb-1 gap-2">
            <span class="text-xs font-semibold uppercase tracking-wide text-accent-soft">
                Pattern lesson · ${entry.ticker} · ${entry.sim_date}
            </span>
            <span class="shrink-0 rounded-full border px-2 py-0.5 text-[10px] uppercase tracking-wide ${FAMILY_TONE[e.family] || 'border-line text-muted'}">${e.family || 'pattern'}</span>
        </div>
        <p class="font-semibold text-white">${e.name || e.title || 'Pattern'}</p>
        ${e.tension ? `<p class="mt-1 text-xs italic text-dim">${e.tension}</p>` : ''}
        <p class="mt-2 text-muted leading-relaxed">${e.what_it_is || ''}</p>
        ${steps.length ? `<ol class="mt-3 space-y-1 text-xs text-muted">${steps.map((step, i) => `<li class="flex gap-2"><span class="text-dim">${i + 1}.</span><span>${step}</span></li>`).join('')}</ol>` : ''}
        ${e.why_it_matters ? `<p class="mt-3 text-xs text-muted"><span class="text-dim uppercase tracking-wide">Why it matters</span><br>${e.why_it_matters}</p>` : ''}
        ${e.common_mistake ? `<p class="mt-2 text-xs text-warn"><span class="uppercase tracking-wide">Common mistake</span><br>${e.common_mistake}</p>` : ''}
        ${e.watch_next ? `<p class="mt-2 text-xs text-muted"><span class="text-dim uppercase tracking-wide">Watch next</span><br>${e.watch_next}</p>` : ''}
        ${figures ? `<div class="mt-3 flex flex-wrap gap-1">${figures}</div>` : ''}
        <p class="mt-3 text-[11px] uppercase tracking-wide text-dim">
            ${e.source === 'gemini' ? 'Gemini coach' : 'Built-in syllabus'} · taught from ${e.signal || e.name || 'the chart'}
        </p>`;
    return node;
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
    // The date a story broke lives on the feed entry; the payload carries it as `as_of`.
    // Reading `payload.sim_date` printed "undefined" in the card header.
    const asOf = entry.sim_date || e.as_of || '';
    const label = scope === 'market'
        ? `Market-wide · ${asOf}`
        : scope === 'sector'
            ? `Sector · ${e.sector || 'sector-wide'} · ${asOf}`
            : `Market event · ${entry.ticker} · ${asOf}`;
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
        signalSymbols.add(signal.ticker);
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
    signalTotal += signals.length;
    if (!signalsOpen) signalUnread += signals.length;
    updateSignalsBadge();
}

// A bill leaving the account is an event the player should feel, not something they
// discover later by noticing the cash number is smaller.
function logCharges(charges) {
    for (const charge of charges) {
        if (charge.kind === 'salary') {
            toast(
                `Paycheck · +${money(charge.amount)}`,
                `Paid in on ${charge.due_date}${charge.payee ? ` by ${esc(charge.payee)}` : ''}. ${money(charge.cash_after)} in cash.`,
                'bullish',
            );
            continue;
        }
        for (const sale of charge.sold || []) {
            toast(
                `Bank sold ${sale.shares} ${sale.ticker}`,
                `At ${money(sale.price)} on ${sale.date}, to cover ${esc(charge.label)}. You did not have the cash when it came due.`,
                'bearish',
            );
        }
        const missed = charge.shortfall > 0;
        toast(
            `${esc(charge.label)} · ${money(charge.amount + charge.shortfall)}`,
            missed
                ? `Due on ${charge.due_date}. Only ${money(charge.amount)} could be paid, even after selling everything - ${money(charge.shortfall)} missed.`
                : `Paid on ${charge.due_date}. ${money(charge.cash_after)} left in cash.`,
            missed ? 'bearish' : 'neutral',
        );
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

// --- the signal drawer ----------------------------------------------------

function setSignalsOpen(open) {
    signalsOpen = open;
    el('signal-drawer').classList.toggle('translate-x-full', !open);
    el('signal-drawer').setAttribute('aria-hidden', String(!open));
    el('signal-backdrop').classList.toggle('hidden', !open);
    const tab = el('signals-toggle');
    tab.setAttribute('aria-expanded', String(open));
    // The tab would otherwise sit underneath the panel it just opened.
    tab.classList.toggle('opacity-0', open);
    tab.classList.toggle('pointer-events-none', open);
    if (open) signalUnread = 0;
    updateSignalsBadge();
}

function updateSignalsBadge() {
    const badge = el('signals-count');
    // Closed, the badge counts what has not been read; open, it counts the session total,
    // because the drawer itself is now the place to read them.
    const count = signalsOpen ? signalTotal : signalUnread;
    badge.textContent = String(count);
    badge.className = 'mx-auto mt-2 min-w-6 rounded-full px-2 py-0.5 text-center text-xs font-semibold '
        + (signalsOpen ? 'bg-brand text-white' : 'bg-warn text-ink')
        + (count === 0 ? ' hidden' : '');
    const summary = el('signals-summary');
    if (!signalTotal) {
        summary.textContent = 'Every pattern the clock has printed on your own symbols.';
        return;
    }
    const symbols = signalSymbols.size;
    summary.textContent = `${signalTotal} signal${signalTotal === 1 ? '' : 's'} across `
        + `${symbols} symbol${symbols === 1 ? '' : 's'}.`;
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
    if (basketInFlight) {
        // A running clock asks far more often than these charts change. Coalesce instead
        // of stacking requests: one is already on its way with fresher numbers.
        basketQueued = true;
        return;
    }
    basketInFlight = true;
    try {
        const payload = await call(`/api/session/${sessionId}/basket`);
        // Nothing moved since the last draw, so the charts would only be re-parsed.
        const signature = `${payload.sim_date}|${payload.equity?.length ?? 0}|`
            + payload.series.map((row) => `${row.ticker}:${row.last_close}:${row.points.length}`).join(',');
        if (signature === basketSignature) return;
        basketSignature = signature;
        updateBasketChart(charts.basket, payload);
        updateEquityChart(charts.equity, payload);
        renderBasketNote(payload);
        const last = payload.equity?.at(-1);
        el('equity-note').textContent = last
            ? `${money(last.market_value)} at risk · ${money(last.cash)} idle`
            : '';
    } catch (error) {
        console.error(error);
    } finally {
        basketInFlight = false;
        if (basketQueued) {
            basketQueued = false;
            scheduleBasket(0);
        }
    }
}

// The basket and equity panels are read at a glance, not frame by frame, so a tick only
// schedules them: at 10x the clock ticks ten times a second and redrawing both charts for
// every one of those was most of the page's work.
function scheduleBasket(delay = BASKET_REFRESH_MS) {
    if (basketTimer !== null) return;
    basketTimer = setTimeout(() => {
        basketTimer = null;
        refreshBasket();
    }, delay);
}

function scheduleFeedRefresh(delay = FEED_REFRESH_MS) {
    if (feedTimer !== null) return;
    feedTimer = setTimeout(() => {
        feedTimer = null;
        refreshFeed();
    }, delay);
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
        logCharges(state.charged || []);
        scheduleBasket();
        // The lesson itself is still being written on a worker thread. Teacher mode stops
        // the clock now, on the tick that queued it, so the player is not three days past
        // the pattern by the time it can be explained.
        const lesson = (state.pending_ai || []).find((job) => job.kind === 'PATTERN_LESSON');
        if (teacherMode && lesson && !awaitingLesson) {
            pause();
            openCoach(lesson);
            return;
        }
        // AI runs in the background, so its output lands after the tick that queued it.
        // Nothing to poll for while the clock is running - the next advance already
        // returns the updated feed - whereas the two timers this used to arm on every
        // such tick were fetching the entire state twice per day advanced, and each of
        // those re-rendered all five charts.
        if ((state.pending_ai || []).length && !timer) scheduleFeedRefresh();
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
            // A new event may well be the lesson that teaches the next pattern, and the
            // panel is the visible half of the curriculum.
            renderPatterns(state.patterns);
            refreshSimulation();
            scheduleBasket(0);
        }
    } catch (error) {
        console.error(error);
    }
}

function play() {
    if (timer) return;
    const speed = SPEEDS[Number(el('speed').value)];
    // Self-scheduling rather than setInterval: a tick that takes 400ms of a 1000ms budget
    // waits the remaining 600ms, so "1 day/sec" means one day a second instead of drifting
    // a whole extra second behind every time a request runs long.
    const tick = async () => {
        if (!timer) return;
        const started = performance.now();
        await advance(speed.days);
        if (!timer) return;
        const spent = performance.now() - started;
        timer = setTimeout(tick, Math.max(0, speed.ms - spent));
    };
    timer = setTimeout(tick, speed.ms);
    el('play-btn').textContent = 'Pause';
    el('play-btn').className = PLAYING;
    setStatus('Playing', true);
}

function pause() {
    clearTimeout(timer);
    timer = null;
    el('play-btn').textContent = 'Play';
    el('play-btn').className = PRIMARY;
    setStatus('Paused', false);
    // Collect whatever the background workers finished while the clock was running.
    scheduleFeedRefresh(0);
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

// `explicitShares` is for callers that already know the size they want - "sell everything"
// means the whole position, not however many happen to be typed in the quantity box.
async function trade(side, ticker, explicitShares) {
    const errorBox = el('trade-error');
    errorBox.classList.add('hidden');
    const target = ticker || el('trade-ticker').value;
    const shares = explicitShares ?? Number(el('share-qty').value);
    try {
        render(await call(`/api/session/${sessionId}/trade`, {
            method: 'POST',
            body: JSON.stringify({ ticker: target, side, shares, focus: focus || target }),
        }));
        scheduleBasket(0);
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

// --- teacher mode ---------------------------------------------------------

function setTeacherMode(on) {
    teacherMode = on;
    el('teacher-mode').checked = on;
    try { localStorage.setItem(TEACHER_KEY, on ? '1' : '0'); } catch { /* private mode */ }
}

function openCoach(job) {
    awaitingLesson = job;
    lessonWaitStarted = Date.now();
    el('coach-title').textContent = job.pattern || 'A pattern just appeared';
    el('coach-subtitle').textContent = `${job.ticker}${job.date ? ` · ${job.date}` : ''}`;
    el('coach-family').textContent = 'analysing';
    el('coach-body').classList.add('hidden');
    el('coach-body').innerHTML = '';
    // Rebuilt every time: coachGaveUp() overwrites this block, so a second lesson would
    // otherwise open showing the previous one's failure message and no spinner.
    el('coach-loading').innerHTML = `
        <span class="h-5 w-5 animate-spin rounded-full border-2 border-line-strong border-t-accent"></span>
        <span class="text-sm text-muted">Reading your chart and writing the explanation&hellip;</span>`;
    el('coach-loading').classList.remove('hidden');
    const overlay = el('coach-overlay');
    overlay.classList.remove('hidden');
    overlay.classList.add('flex');
    pollLesson();
}

function fillCoach(entry) {
    const lesson = entry.payload || {};
    const readings = lesson.context || {};
    const steps = lesson.how_to_spot || [];
    el('coach-title').textContent = lesson.name || 'Pattern';
    el('coach-subtitle').textContent = `${entry.ticker} · ${entry.sim_date}`
        + (lesson.tension ? ` · ${lesson.tension}` : '');
    const family = el('coach-family');
    family.textContent = lesson.family || 'pattern';
    family.className = 'shrink-0 rounded-full border px-3 py-1 text-[10px] uppercase tracking-wide '
        + (FAMILY_TONE[lesson.family] || 'border-line text-muted');

    const figures = [
        ['close', readings.close], ['RSI', readings.rsi14],
        ['SMA20', readings.sma20], ['SMA50', readings.sma50],
    ].filter(([, value]) => value != null)
        .map(([label, value]) => `<span class="rounded-full border border-line px-3 py-1 font-mono text-xs text-muted">${label} ${Number(value).toFixed(2)}</span>`)
        .join('');

    el('coach-body').innerHTML = `
        <p class="text-base leading-relaxed text-white">${lesson.what_it_is || ''}</p>
        ${figures ? `<div class="flex flex-wrap gap-2">${figures}</div>` : ''}
        ${steps.length ? `
            <div>
                <p class="mb-2 text-xs uppercase tracking-wide text-dim">How to spot it on your chart</p>
                <ol class="space-y-2 text-sm text-muted">
                    ${steps.map((step, i) => `<li class="flex gap-3"><span class="text-accent-soft">${i + 1}.</span><span>${step}</span></li>`).join('')}
                </ol>
            </div>` : ''}
        ${lesson.why_it_matters ? `<div><p class="mb-1 text-xs uppercase tracking-wide text-dim">Why it matters</p><p class="text-sm text-muted">${lesson.why_it_matters}</p></div>` : ''}
        ${lesson.common_mistake ? `<div class="rounded-2xl border border-warn/40 bg-warn/10 p-4"><p class="mb-1 text-xs uppercase tracking-wide text-warn">Common mistake</p><p class="text-sm text-warn">${lesson.common_mistake}</p></div>` : ''}
        ${lesson.watch_next ? `<div><p class="mb-1 text-xs uppercase tracking-wide text-dim">Watch next</p><p class="text-sm text-muted">${lesson.watch_next}</p></div>` : ''}
        <p class="text-[11px] uppercase tracking-wide text-dim">
            ${lesson.source === 'gemini' ? 'Gemini coach' : 'Built-in syllabus'} · taught from ${lesson.signal || lesson.name || 'the chart'}
        </p>`;
    el('coach-loading').classList.add('hidden');
    el('coach-body').classList.remove('hidden');
}

function coachGaveUp() {
    el('coach-loading').innerHTML = `
        <p class="text-sm text-muted">
            The coach could not write this one up - the AI is unavailable right now. The pattern
            still fired on your chart, and the Signals drawer has the detector's own note on it.
        </p>`;
}

function closeCoach(resume) {
    clearTimeout(lessonPollTimer);
    lessonPollTimer = null;
    awaitingLesson = null;
    const overlay = el('coach-overlay');
    overlay.classList.add('hidden');
    overlay.classList.remove('flex');
    if (resume) play();
}

async function pollLesson() {
    if (!awaitingLesson) return;
    if (Date.now() - lessonWaitStarted > LESSON_WAIT_MS) {
        coachGaveUp();
        return;
    }
    try {
        const state = await call(`/api/session/${sessionId}/state?focus=${focus || ''}`);
        renderFeed(state.ai_feed);
        renderPatterns(state.patterns);
    } catch (error) {
        console.error(error);
    }
    // renderFeed fills the card and clears awaitingLesson the moment the lesson lands.
    if (awaitingLesson) lessonPollTimer = setTimeout(pollLesson, LESSON_POLL_MS);
}

// Every lesson already on screen counts as taught, so only one that arrives while the
// coach is waiting opens the card. Without this baseline, reloading a session would
// re-teach every lesson it had ever been given.
function noteLessons(feed) {
    const lessons = feed.filter((entry) => entry.type === 'PATTERN_LESSON');
    if (!awaitingLesson) {
        for (const entry of lessons) shownLessons.add(entry.id);
        return;
    }
    const fresh = lessons.find((entry) => !shownLessons.has(entry.id));
    if (!fresh) return;
    shownLessons.add(fresh.id);
    awaitingLesson = null;
    clearTimeout(lessonPollTimer);
    lessonPollTimer = null;
    fillCoach(fresh);
}

// --- the walkthrough ------------------------------------------------------

const TOUR_STEPS = [
    {
        title: 'This is a flight simulator for trading',
        body: 'You are standing on one day of real market history, with real market rules and '
            + 'fake money. Nothing here can cost you anything, so the only thing to do is try '
            + 'things and watch what happens.',
    },
    {
        target: 'bank-panel',
        title: 'This is your bank account',
        body: (state) => {
            const name = state?.bank?.customer?.name;
            const account = state?.bank?.account;
            const paycheck = state?.expenses?.paycheck;
            return `${name ? `You are ${name}, a customer at the Nessie sandbox bank.` : 'This is your Nessie bank account.'} `
                + `Your starting cash is the ${money(state?.portfolio?.starting_cash)} in your `
                + `${account?.nickname || 'checking'} account`
                + (paycheck
                    ? `, and a ${money(paycheck.amount)} paycheck${paycheck.employer ? ` from ${paycheck.employer}` : ''} lands every two weeks.`
                    : '. No paycheck is coming in, so every bill eats into it.');
        },
    },
    {
        target: 'bills-panel',
        title: 'You have to pay these bills',
        body: (state) => {
            const bills = state?.expenses;
            const lead = bills?.bill_count
                ? `${bills.bill_count} bills - ${money(bills.monthly_total)} a month - come out of your cash on their due dates, `
                    + 'whether or not your money is sitting in stocks.'
                : 'Any bills on this account come out of your cash on their due dates.';
            return `${lead} If you do not have the cash when one is due, the bank sells your shares at that `
                + "day's price to pay it. Keep enough cash on hand that you decide when to sell, not the bank.";
        },
    },
    {
        target: 'watchlist',
        title: 'The stocks you picked',
        body: 'Each tile is one company you are following, with its latest price and how much it '
            + 'moved. Click one to pull its chart up. The green badge is how many shares you own.',
    },
    {
        target: 'price-chart',
        title: 'The price chart',
        body: 'The bright line is the closing price each day. The two smoother lines are the '
            + 'average price over the last 20 and 50 days - when the fast one crosses the slow '
            + 'one, something has changed. The bars along the bottom are how many shares changed '
            + 'hands that day.',
    },
    {
        target: 'rsi-chart',
        title: 'Two gauges under the chart',
        body: 'RSI runs 0 to 100 and says how hard the stock has been bought lately: over 70 it '
            + 'has run hot, under 30 it has been dumped. MACD beside it tends to turn slightly '
            + 'before the price does. You do not need to memorise either - the coach explains '
            + 'each one the first time it shows up.',
    },
    {
        target: 'buy-btn',
        title: 'Buying and selling',
        body: 'Type a number of shares and press Buy or Sell. Trades fill at the closing price of '
            + 'the day you are standing on, and the money comes out of the cash shown at the top. '
            + 'Max works out the most you can afford right now.',
    },
    {
        target: 'play-btn',
        title: 'Moving the clock',
        body: 'Next day steps forward one trading day. Play runs the clock by itself and the '
            + 'slider sets the speed. Start slow - one day a second is plenty while you are still '
            + 'learning to read the chart.',
    },
    {
        target: 'teacher-row',
        title: 'Teacher mode',
        body: 'Leave this switched on. Whenever a new pattern appears on one of your stocks the '
            + 'clock stops and a card explains what just happened, what it usually means, and '
            + 'what to watch for next - before you trade through it.',
    },
    {
        target: 'ai-feed',
        title: 'The AI coach',
        body: 'Chart reads, invented headlines and pattern lessons all land here. Ask for a read '
            + 'at any time to get an opinion on the stock you are looking at, quoting the exact '
            + 'numbers it used.',
    },
    {
        target: 'pattern-list',
        title: 'Your pattern scorecard',
        body: 'Seven patterns worth knowing. Each is ticked off the first time it appears in a '
            + 'stock you actually hold, so every lesson is about your chart rather than a '
            + 'textbook. That is the game: collect all seven and keep the account green while '
            + 'you do it.',
    },
];

let tourIndex = 0;
let tourTarget = null;
let tourSaved = null;

function clearSpot() {
    if (tourTarget && tourSaved) {
        tourTarget.style.position = tourSaved.position;
        tourTarget.style.zIndex = tourSaved.zIndex;
        tourTarget.style.boxShadow = tourSaved.boxShadow;
        tourTarget.style.borderRadius = tourSaved.borderRadius;
    }
    tourTarget = null;
    tourSaved = null;
}

// Inline styles rather than a class: the highlight has to sit above the tour backdrop, and
// a class invented here would not exist in the compiled Tailwind build.
function spotlight(element) {
    clearSpot();
    if (!element) return;
    tourTarget = element;
    tourSaved = {
        position: element.style.position,
        zIndex: element.style.zIndex,
        boxShadow: element.style.boxShadow,
        borderRadius: element.style.borderRadius,
    };
    element.style.position = 'relative';
    element.style.zIndex = '65';
    element.style.boxShadow = '0 0 0 3px #3b82f6, 0 0 45px rgba(59,130,246,0.35)';
    element.style.borderRadius = '1.5rem';
}

function placeCard(element) {
    const card = el('tour-card');
    card.classList.remove('hidden');
    const box = card.getBoundingClientRect();
    if (!element) {
        card.style.top = `${Math.max(16, (window.innerHeight - box.height) / 2)}px`;
        card.style.left = `${Math.max(16, (window.innerWidth - box.width) / 2)}px`;
        return;
    }
    const rect = element.getBoundingClientRect();
    const below = rect.bottom + 16;
    const top = below + box.height < window.innerHeight
        ? below
        : Math.max(16, rect.top - box.height - 16);
    const left = Math.min(
        Math.max(16, rect.left + rect.width / 2 - box.width / 2),
        window.innerWidth - box.width - 16,
    );
    card.style.top = `${top}px`;
    card.style.left = `${left}px`;
}

function showTourStep(index) {
    tourIndex = index;
    const step = TOUR_STEPS[index];
    // The panel, not the canvas inside it: highlighting a bare canvas lights up a rectangle
    // floating inside its own card. A label is its own control, though - walking up from
    // the teacher switch would light the entire clock panel instead of the switch.
    const raw = step.target ? el(step.target) : null;
    const element = raw
        ? (raw.tagName === 'LABEL' ? raw : (raw.closest('.rounded-3xl') || raw))
        : null;

    el('tour-step').textContent = `Step ${index + 1} of ${TOUR_STEPS.length}`;
    el('tour-title').textContent = step.title;
    el('tour-body').textContent = typeof step.body === 'function' ? step.body(latestState) : step.body;
    el('tour-back').classList.toggle('invisible', index === 0);
    el('tour-next').textContent = index === TOUR_STEPS.length - 1 ? 'Start trading' : 'Next';

    if (element) element.scrollIntoView({ behavior: 'smooth', block: 'center' });
    spotlight(element);
    // Placed after the scroll settles, or the card lands on the element's old position.
    setTimeout(() => placeCard(element), element ? 320 : 0);
}

function startTour() {
    pause();
    el('tour-backdrop').classList.remove('hidden');
    showTourStep(0);
}

function endTour() {
    clearSpot();
    el('tour-backdrop').classList.add('hidden');
    el('tour-card').classList.add('hidden');
    try { localStorage.setItem(TOUR_KEY, '1'); } catch { /* private mode */ }
}

// --- wiring ---------------------------------------------------------------

el('signals-toggle').addEventListener('click', () => setSignalsOpen(true));
el('signals-close').addEventListener('click', () => setSignalsOpen(false));
el('signal-backdrop').addEventListener('click', () => setSignalsOpen(false));
document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && signalsOpen) setSignalsOpen(false);
});

el('play-btn').addEventListener('click', () => (timer ? pause() : play()));
el('step-btn').addEventListener('click', () => advance(1));

el('teacher-mode').addEventListener('change', (event) => setTeacherMode(event.target.checked));
el('coach-continue').addEventListener('click', () => closeCoach(false));
el('coach-resume').addEventListener('click', () => closeCoach(true));

el('tutorial-btn').addEventListener('click', startTour);
el('tour-skip').addEventListener('click', endTour);
el('tour-back').addEventListener('click', () => showTourStep(Math.max(0, tourIndex - 1)));
el('tour-next').addEventListener('click', () => {
    if (tourIndex >= TOUR_STEPS.length - 1) endTour();
    else showTourStep(tourIndex + 1);
});
document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    if (!el('tour-card').classList.contains('hidden')) endTour();
    else if (!el('coach-overlay').classList.contains('hidden')) closeCoach(false);
});
el('buy-btn').addEventListener('click', () => trade('BUY'));
el('sell-btn').addEventListener('click', () => trade('SELL'));
el('trade-ticker').addEventListener('change', (event) => setFocus(event.target.value));

for (const chip of document.querySelectorAll('.qty-chip')) {
    chip.addEventListener('click', () => {
        if (chip.dataset.qty === 'max') {
            const ticker = el('trade-ticker').value;
            const quote = latestState?.quotes?.find((row) => row.ticker === ticker);
            el('share-qty').value = quote?.close
                ? Math.max(0, Math.floor(latestState.portfolio.cash_balance / quote.close)) : 0;
        } else {
            el('share-qty').value = chip.dataset.qty;
        }
    });
}

el('sell-all-btn').addEventListener('click', async () => {
    const button = el('sell-all-btn');
    button.disabled = true;
    try {
        // Snapshot the positions first: each trade re-renders, so reading the live list
        // while selling out of it would let the loop skip or repeat a holding.
        for (const position of [...(latestState?.portfolio?.positions ?? [])]) {
            await trade('SELL', position.ticker, position.shares);
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
        scheduleBasket(0);
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
        scheduleBasket(0);
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
        scheduleBasket(0);
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
    let savedTeacher = null;
    let tourDone = null;
    try {
        savedTeacher = localStorage.getItem(TEACHER_KEY);
        tourDone = localStorage.getItem(TOUR_KEY);
    } catch { /* private mode */ }
    setTeacherMode(savedTeacher === null ? true : savedTeacher === '1');

    await loadCompanyNames();
    checkAI();
    render(await call(`/api/session/${sessionId}/state`));
    refreshSimulation();
    refreshBasket();

    // First visit gets walked through the screen before the clock ever moves.
    if (!tourDone) startTour();
})();
