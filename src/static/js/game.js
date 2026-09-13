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
let companyInfo = {};
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
        button.dataset.stockTip = quote.ticker;
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
    badge.textContent = position ? `${Number(position.shares).toLocaleString()} owned` : 'none owned';
    badge.className = `rounded-full border px-3 py-1 text-xs ${position ? 'border-accent/50 bg-accent/10 text-accent-soft' : 'border-line text-muted'}`;

    el('trade-summary').innerHTML = position
        ? `You own <span class="font-semibold tabular-nums">${Number(position.shares).toLocaleString()}</span> shares,
           bought at ${money(position.avg_cost)} each on average. If you sold now, you would make
           <span class="font-semibold tabular-nums ${toneFor(position.unrealized_pl)}">${money(position.unrealized_pl)}</span>`
        : "You don't own this stock yet.";
}

function renderHoldings(state) {
    const body = el('holdings-body');
    const positions = state.portfolio.positions;
    el('positions').textContent = String(positions.length);
    if (!positions.length) {
        body.innerHTML = '<p class="py-2 text-sm text-dim">You don\'t own any stocks yet.</p>';
        return;
    }
    body.innerHTML = '';
    for (const position of positions) {
        const row = document.createElement('div');
        row.className = 'rounded-xl border border-line bg-ink-soft p-3';
        row.innerHTML = `
            <div class="flex items-baseline justify-between gap-2">
                <button type="button" data-stock-tip="${position.ticker}" class="focus-chip font-mono font-semibold transition hover:text-glow">${position.ticker}</button>
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
    if (!bills || !shows(state, 'bank')) return;

    const overdrawn = state.portfolio.overdrawn;
    el('overdrawn-banner').classList.toggle('hidden', !overdrawn);
    el('bills-panel').className = `rounded-3xl border p-6 ${
        overdrawn ? 'border-down/50 bg-down/5' : 'border-line bg-surface'}`;

    const runway = el('bills-runway');
    // Months of runway is the number that should change how much gets invested, so it is
    // coloured like a warning long before the balance actually goes red.
    const months = bills.months_of_runway;
    runway.textContent = months == null ? (bills.paycheck && bills.bill_count ? 'paycheck covers bills' : 'no bills')
        : months < 0 ? 'below zero'
            : `cash lasts ${months} months`;
    runway.className = 'rounded-full border px-3 py-1 text-xs ' + (
        months == null ? 'border-line text-muted'
            : months < 1 ? 'border-down/50 bg-down/10 text-down'
                : months < 3 ? 'border-warn/50 bg-warn/10 text-warn'
                    : 'border-line text-muted');

    el('bills-total').textContent = bills.bill_count
        ? `${bills.bill_count} bills · ${money(bills.monthly_total)} a month out of your cash`
        : '';

    const list = el('bills-list');
    const upcoming = bills.upcoming || [];
    if (!upcoming.length) {
        list.innerHTML = '<p class="text-xs text-dim">No bills on this account.</p>';
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
        ? `${totals.join(' · ')} so far. Your stocks alone have made ${signed(state.portfolio.trading_return_pct)}%.`
        : '';
    const dividends = state.dividends_paid || 0;
    el('dividends-paid').textContent = money(dividends);
    const dividendRows = state.dividends || [];
    el('dividend-history').innerHTML = dividendRows.length
        ? dividendRows.slice(-5).reverse().map((payment) => `
            <div class="flex items-center justify-between gap-3 rounded-xl border border-up/20 bg-ink-soft px-3 py-2">
                <span><span class="font-mono font-semibold">${esc(payment.ticker)}</span> · ${payment.ex_date}</span>
                <span class="tabular-nums text-up">+${money(payment.amount)}</span>
            </div>`).join('')
        : '<p class="text-dim">No dividends yet. Own a stock that pays them before its dividend date.</p>';
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
    el('bank-note').textContent = bills.enabled === false
        ? 'Stocks only: no paycheck or bills in this game.'
        : (bills.bill_count
            ? `${bills.bill_count} bills take ${money(bills.monthly_total)} a month out of this account`
            : 'No bills come out of this account')
            + (paycheck ? `, and your paycheck brings in about ${money(paycheck.monthly)}.` : '.')
            + " If you don't have enough cash for a bill, the bank sells some of your stock to pay it.";
    const dividendTotal = state.dividends_paid || 0;
    el('bank-dividend-note').textContent = dividendTotal
        ? `${money(dividendTotal)} in dividends has been added to your cash.`
        : 'Dividends are cash some companies pay to people who own their stock.';
}

// --- the daily ledger -----------------------------------------------------

const NEWS_READ_KEY = `tradingTeacher.newsRead.${sessionId}`;
let newsReadThrough = null;
let newsEdition = null;

const utcDate = (iso, options) => new Date(`${iso}T00:00:00Z`).toLocaleDateString('en-US', { timeZone: 'UTC', ...options });

function dayBefore(iso) {
    const day = new Date(`${iso}T00:00:00Z`);
    day.setUTCDate(day.getUTCDate() - 1);
    return day.toISOString().slice(0, 10);
}

function renderNewsBadge(state) {
    if (!shows(state, 'news')) return;
    if (newsReadThrough === null) {
        try { newsReadThrough = localStorage.getItem(NEWS_READ_KEY); } catch { /* private mode */ }
        newsReadThrough = newsReadThrough || dayBefore(state.sim_date);
    }
    const unread = (state.news || []).filter((story) => story.date > newsReadThrough).length;
    const badge = el('news-count');
    badge.textContent = unread > 9 ? '9+' : String(unread);
    badge.className = 'mx-auto mt-2 min-w-6 rounded-full bg-warn px-2 py-0.5 text-center text-xs font-semibold text-ink'
        + (unread ? '' : ' hidden');
}

function markNewsRead(day) {
    if (!day || day <= newsReadThrough) return;
    newsReadThrough = day;
    try { localStorage.setItem(NEWS_READ_KEY, day); } catch { /* private mode */ }
    if (latestState) renderNewsBadge(latestState);
}

const newsOpen = () => !el('news-overlay').classList.contains('hidden');

async function openNews() {
    const wasPlaying = Boolean(timer);
    pause();
    if (signalsOpen) setSignalsOpen(false);
    el('news-resume').classList.toggle('hidden', !wasPlaying);
    const overlay = el('news-overlay');
    overlay.classList.remove('hidden');
    overlay.classList.add('flex');
    await loadEdition(latestState?.sim_date);
}

function closeNews(resume) {
    const overlay = el('news-overlay');
    overlay.classList.add('hidden');
    overlay.classList.remove('flex');
    if (resume) play();
}

async function loadEdition(day) {
    const errorBox = el('news-error');
    errorBox.classList.add('hidden');
    el('news-body').classList.add('opacity-50');
    try {
        newsEdition = await call(`/api/session/${sessionId}/news${day ? `?date=${day}` : ''}`);
        renderEdition(newsEdition);
        if (newsEdition.is_latest) markNewsRead(newsEdition.date);
    } catch (error) {
        errorBox.textContent = error.message;
        errorBox.classList.remove('hidden');
    } finally {
        el('news-body').classList.remove('opacity-50');
    }
}

function storyMeta(story) {
    return `<span class="font-semibold text-muted">${esc(story.section)}</span><span>&middot;</span><span>${esc(story.source)}</span>`
        + (story.ticker ? `<span>&middot;</span><span class="font-mono normal-case tracking-normal text-muted">${esc(story.ticker)}</span>` : '');
}

function renderEdition(edition) {
    el('news-dateline').textContent = edition.date
        ? utcDate(edition.date, { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' })
        : 'No editions yet';
    el('news-date').innerHTML = edition.dates.map((day, index) => `
        <option value="${day}" ${day === edition.date ? 'selected' : ''}>
            ${utcDate(day, { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' })}${index === 0 ? ' (today)' : ''}
        </option>`).join('');
    el('news-prev').disabled = !edition.previous;
    el('news-next').disabled = !edition.next;

    const lead = edition.lead;
    el('news-lead').innerHTML = lead
        ? `<div class="flex flex-wrap items-center gap-2 text-[11px] uppercase tracking-widest text-dim">${storyMeta(lead)}</div>
           <h3 class="mt-2 font-serif text-3xl font-bold leading-tight sm:text-4xl">${esc(lead.headline)}</h3>
           ${lead.body ? `<p class="mt-3 text-base leading-relaxed text-muted">${esc(lead.body)}</p>` : ''}`
        : '<p class="text-muted">A quiet day. Nothing made the paper.</p>';

    el('news-stories').innerHTML = edition.stories.map((story) => `
        <article class="py-4">
            <div class="flex flex-wrap items-center gap-2 text-[11px] uppercase tracking-widest text-dim">${storyMeta(story)}</div>
            <h4 class="mt-1 text-lg font-semibold leading-snug">${esc(story.headline)}</h4>
            ${story.body ? `<p class="mt-1 text-sm leading-relaxed text-muted">${esc(story.body)}</p>` : ''}
        </article>`).join('');

    el('news-bell').innerHTML = edition.closing_bell.length
        ? edition.closing_bell.map((row) => `
            <div class="flex items-baseline justify-between gap-3">
                <span class="min-w-0 cursor-help" data-stock-tip="${esc(row.ticker)}">
                    <span class="block font-mono font-semibold">${esc(row.ticker)}</span>
                    <span class="block truncate text-xs text-dim">${esc(row.company)}</span>
                </span>
                <span class="shrink-0 text-right">
                    <span class="block tabular-nums">${money(row.close)}</span>
                    <span class="block text-xs tabular-nums ${toneFor(row.change_pct)}">${pct(row.change_pct)}</span>
                </span>
            </div>`).join('')
        : '<p class="text-xs text-dim">No trading on this day.</p>';
}

// --- training levels ------------------------------------------------------

const LEVELS_KEY = 'tradingTeacher.levels';
let levelApplied = false;

function shows(state, panel) {
    return !state?.level || state.level.panels.includes(panel);
}

const starText = (count) => '★'.repeat(count) + '☆'.repeat(Math.max(0, 3 - count));

function balanceRow(row) {
    const visible = [...row.children].filter((child) => !child.classList.contains('hidden'));
    row.classList.toggle('hidden', visible.length === 0);
    for (const child of row.children) {
        child.classList.toggle('lg:col-span-2', visible.length === 1 && child === visible[0]);
    }
}

// `excluded` keeps a switched-off series out of the legend, where clicking it would bring it back.
function setOverlay(chart, indexes, on) {
    for (const index of indexes) {
        chart.data.datasets[index].hidden = !on;
        chart.data.datasets[index].excluded = !on;
    }
    chart.update('none');
}

function applyLevel(state) {
    if (levelApplied) return;
    levelApplied = true;
    for (const node of document.querySelectorAll('[data-panel]')) {
        node.classList.toggle('hidden', !shows(state, node.dataset.panel));
    }
    balanceRow(el('indicator-row'));
    balanceRow(el('portfolio-row'));
    setOverlay(charts.price, [1, 2], shows(state, 'trend'));
    setOverlay(charts.price, [5], shows(state, 'volume'));

    const level = state.level;
    if (!level) return;
    document.title = `Level ${level.number} · ${level.title} · Trading Teacher`;
    el('level-panel').classList.remove('hidden');
    el('tutorial-label').textContent = 'Level briefing';
    teacherMode = true;
    el('teacher-mode').checked = true;
}

function goalItems(goals) {
    return goals.map((goal) => `
        <li class="flex items-start gap-3">
            <span class="text-lg leading-none ${goal.met ? 'text-warn' : 'text-dim'}">${goal.met ? '★' : '☆'}</span>
            <span>
                <span class="block ${goal.met ? 'text-white' : 'text-muted'}">${esc(goal.label)}</span>
                ${goal.progress ? `<span class="block text-xs text-dim">${esc(goal.progress)}</span>` : ''}
            </span>
        </li>`).join('');
}

function reviewRow(review) {
    return `
        <div class="flex items-start justify-between gap-3 rounded-xl border px-3 py-2 ${review.followed ? 'border-up/40 bg-up/5' : 'border-line bg-ink-soft'}">
            <span class="min-w-0">
                <span class="font-semibold ${review.side === 'BUY' ? 'text-up' : 'text-down'}">${review.side}</span>
                <span class="font-mono">${esc(review.ticker)}</span>
                <span class="text-xs text-dim">&middot; ${review.date} &middot; ${Number(review.shares).toLocaleString()} @ ${money(review.price)}</span>
                <span class="block text-xs text-muted">${esc(review.why)}</span>
            </span>
            ${review.followed
                ? '<span class="shrink-0 text-xs font-semibold text-up">✓ followed</span>'
                : '<span class="shrink-0 text-xs text-dim">✗ missed</span>'}
        </div>`;
}

function renderLevel(state) {
    const level = state.level;
    if (!level) return;
    el('level-kicker').textContent = `Training · Level ${level.number} of ${level.count}`;
    el('level-title').textContent = level.title;
    el('level-buy').textContent = level.buy_rule;
    el('level-sell').textContent = level.sell_rule;
    el('level-goals').innerHTML = goalItems(level.goals);
    el('level-days').textContent = `${level.days_played} of ${level.trading_days} days`;
    el('level-bar').style.width = `${Math.min(100, (level.days_played / Math.max(1, level.trading_days)) * 100)}%`;
    const last = level.trades.at(-1);
    el('level-last').innerHTML = last
        ? reviewRow(last)
        : '<p class="text-dim">No trades yet. Each trade gets checked against the rule right away.</p>';
}

function openBrief(level) {
    pause();
    el('brief-kicker').textContent = `Training level ${level.number} of ${level.count}`;
    el('brief-title').textContent = level.title;
    el('brief-tagline').textContent = level.tagline;
    el('brief-tools').innerHTML = level.tools
        .map((tool) => `<span class="rounded-full border border-brand/50 bg-brand/10 px-3 py-1 text-xs text-brand-soft">${esc(tool)}</span>`)
        .join('');
    el('brief-text').innerHTML = level.brief.map((line) => `<p>${esc(line)}</p>`).join('');
    el('brief-buy').textContent = level.buy_rule;
    el('brief-sell').textContent = level.sell_rule;
    el('brief-goals').innerHTML = goalItems(level.goals);
    const overlay = el('brief-overlay');
    overlay.classList.remove('hidden');
    overlay.classList.add('flex');
}

function closeBrief(resume) {
    const overlay = el('brief-overlay');
    overlay.classList.add('hidden');
    overlay.classList.remove('flex');
    if (resume) play();
}

function recordLevel(level) {
    let progress = {};
    try { progress = JSON.parse(localStorage.getItem(LEVELS_KEY) || '{}') || {}; } catch { /* private mode */ }
    const best = progress[level.number] || {};
    progress[level.number] = {
        stars: Math.max(best.stars || 0, level.stars),
        passed: Boolean(best.passed || level.passed),
    };
    try { localStorage.setItem(LEVELS_KEY, JSON.stringify(progress)); } catch { /* private mode */ }
}

async function startLevel(number, button) {
    const label = button.textContent;
    button.disabled = true;
    button.textContent = 'Loading…';
    try {
        const data = await call(`/api/levels/${number}/start`, { method: 'POST', body: '{}' });
        window.location.href = `/game/${data.session_id}`;
    } catch (error) {
        button.disabled = false;
        button.textContent = label;
        el('summary-pass').textContent = error.message;
    }
}

// --- stock hover card -----------------------------------------------------

// One floating card for every element tagged data-stock-tip. The watchlist and holdings are
// rebuilt on every tick, so the card remembers which list its anchor lived in and re-attaches
// to the fresh element for the same stock instead of pointing at one that is gone.
const TIP_DELAY_MS = 150;
let tipTimer = null;
let tipAnchor = null;
let tipZone = null;

function stockTipHtml(ticker) {
    const info = companyInfo[ticker] || {};
    const quote = latestState?.quotes?.find((row) => row.ticker === ticker);
    const position = held(ticker);
    return `
        <div class="flex items-baseline justify-between gap-3">
            <span class="font-mono text-lg font-semibold">${esc(ticker)}</span>
            ${quote?.close != null
                ? `<span class="tabular-nums">${money(quote.close)} <span class="text-xs ${toneFor(quote.change_pct)}">${pct(quote.change_pct)}</span></span>`
                : ''}
        </div>
        <p class="font-medium">${esc(info.company_name || ticker)}</p>
        ${info.sector ? `<span class="mt-1 inline-block rounded-full border border-line px-2 py-0.5 text-[10px] uppercase tracking-wide text-dim">${esc(info.sector)}</span>` : ''}
        <p class="mt-2 text-sm leading-relaxed text-muted">${esc(info.description || 'No description yet.')}</p>
        ${position ? `<p class="mt-2 text-xs text-accent-soft">You own ${Number(position.shares).toLocaleString()} shares, worth ${money(position.market_value)}.</p>` : ''}`;
}

function showStockTip(anchor) {
    tipAnchor = anchor;
    tipZone = anchor.parentElement?.closest('[id]')?.id || null;
    const tip = el('stock-tip');
    tip.innerHTML = stockTipHtml(anchor.dataset.stockTip);
    tip.classList.remove('hidden');
    const rect = anchor.getBoundingClientRect();
    const box = tip.getBoundingClientRect();
    const below = rect.bottom + 8;
    tip.style.top = `${below + box.height <= window.innerHeight - 8 ? below : Math.max(8, rect.top - box.height - 8)}px`;
    tip.style.left = `${Math.min(Math.max(8, rect.left), window.innerWidth - box.width - 8)}px`;
}

function hideStockTip() {
    clearTimeout(tipTimer);
    tipTimer = null;
    tipAnchor = null;
    el('stock-tip').classList.add('hidden');
}

function refreshStockTip() {
    if (!tipAnchor) return;
    const anchor = tipAnchor.isConnected
        ? tipAnchor
        : tipZone && document.querySelector(`#${tipZone} [data-stock-tip="${tipAnchor.dataset.stockTip}"]`);
    if (anchor) showStockTip(anchor);
    else hideStockTip();
}

function queueStockTip(anchor) {
    if (anchor && anchor === tipAnchor) return;
    clearTimeout(tipTimer);
    tipTimer = null;
    if (!anchor) {
        if (tipAnchor) hideStockTip();
        return;
    }
    tipTimer = setTimeout(() => {
        tipTimer = null;
        showStockTip(anchor);
    }, tipAnchor ? 0 : TIP_DELAY_MS);
}

document.addEventListener('mouseover', (event) => queueStockTip(event.target.closest?.('[data-stock-tip]') || null));
document.addEventListener('focusin', (event) => queueStockTip(event.target.closest?.('[data-stock-tip]') || null));
document.addEventListener('focusout', hideStockTip);
document.documentElement.addEventListener('mouseleave', hideStockTip);
document.addEventListener('scroll', hideStockTip, { capture: true, passive: true });

function renderBasketNote(payload) {
    const series = payload.series || [];
    const note = el('basket-note');
    el('basket-window').textContent = `${payload.window_days} sessions to ${payload.sim_date}`;
    if (series.length < 2) {
        note.textContent = 'Add more stocks to compare how they move.';
        return;
    }
    const changes = series.map((row) => row.change_pct).filter((value) => value != null);
    const up = changes.filter((value) => value > 0).length;
    const majority = Math.max(up, changes.length - up);
    const spread = Math.max(...changes) - Math.min(...changes);
    const together = majority === changes.length;
    note.innerHTML = together
        ? `All ${changes.length} stocks moved in the same direction. `
          + `<span class="text-warn">That's almost like owning one stock</span> - one piece of bad news could hit them all.`
        : `These stocks moved from ${Math.min(...changes).toFixed(1)}% to ${Math.max(...changes).toFixed(1)}%, `
          + 'so they are not all moving together. That helps spread out your risk.';
}

function render(state) {
    latestState = state;
    applyLevel(state);
    focus = state.focus;
    el('sim-date').textContent = state.sim_date;

    const simulated = state.simulation && state.simulation.mode === 'simulated';
    el('mode-label').textContent = simulated
        ? `Made-up future prices · real prices ended on ${state.simulation.fork_date}`
        : 'Using real past prices';

    el('focus-label').textContent = state.focus;
    el('focus-name').textContent = companyNames[state.focus] || '';
    el('focus-label').closest('h2').dataset.stockTip = state.focus;

    const quote = state.quotes.find((row) => row.ticker === state.focus) || {};
    el('current-price').textContent = money(quote.close);
    const change = el('current-change');
    change.textContent = pct(quote.change_pct);
    change.className = `text-lg font-semibold tabular-nums ${toneFor(quote.change_pct)}`;

    const portfolio = state.portfolio;
    el('cash').textContent = money(portfolio.cash_balance);
    el('net-worth').textContent = money(portfolio.net_worth);
    const returnEl = el('return-pct');
    returnEl.textContent = `${signed(portfolio.trading_return_pct)}%`;
    returnEl.className = `mt-1 text-3xl font-semibold tabular-nums ${toneFor(portfolio.trading_return_pct)}`;

    renderWatchlist(state);
    renderTradePanel(state);
    renderHoldings(state);
    renderBills(state);
    renderBank(state);
    updateCharts(charts, state);
    renderFeed(state.ai_feed);
    renderNewsBadge(state);
    renderLevel(state);
    renderPatterns(state.patterns);
    refreshStockTip();

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

const FAMILY_LABEL = { trend: 'trend', momentum: 'speed', participation: 'volume' };
const familyLabel = (family) => FAMILY_LABEL[family] || family || 'pattern';

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
                    <span class="shrink-0 rounded-full border px-2 py-0.5 text-[10px] uppercase tracking-wide ${tone}">${familyLabel(row.family)}</span>
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
                    <span class="shrink-0 rounded-full border border-line px-2 py-0.5 text-[10px] uppercase tracking-wide text-dim">${familyLabel(row.family)}</span>
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
        ['price', readings.close],
        ['RSI', readings.rsi14],
        ['20-day avg', readings.sma20],
        ['50-day avg', readings.sma50],
    ].filter(([, value]) => value != null && value !== undefined)
        .map(([label, value]) => `<span class="rounded-full border border-line px-2 py-0.5 font-mono text-xs text-muted">${label} ${Number(value).toFixed(2)}</span>`)
        .join('');
    node.innerHTML = `
        <div class="flex items-center justify-between mb-1 gap-2">
            <span class="text-xs font-semibold uppercase tracking-wide text-accent-soft">
                Pattern lesson · ${entry.ticker} · ${entry.sim_date}
            </span>
            <span class="shrink-0 rounded-full border px-2 py-0.5 text-[10px] uppercase tracking-wide ${FAMILY_TONE[e.family] || 'border-line text-muted'}">${familyLabel(e.family)}</span>
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
        <p class="mt-2 text-[11px] uppercase tracking-wide text-dim">Just a guess · not real financial advice</p>`;
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
        ? `Whole market · ${asOf}`
        : scope === 'sector'
            ? `Industry · ${e.sector || 'industry-wide'} · ${asOf}`
            : `News event · ${entry.ticker} · ${asOf}`;
    const hit = (e.affected_tickers || [entry.ticker]);
    const node = document.createElement('div');
    node.className = `rounded-xl border p-4 ${border}`;
    node.innerHTML = `
        <div class="flex items-center justify-between mb-1">
            <span class="text-xs font-semibold uppercase tracking-wide text-warn">${label}</span>
            <span class="text-xs ${toneFor(e.sentiment)} font-semibold">${e.sentiment > 0.15 ? 'good news' : e.sentiment < -0.15 ? 'bad news' : 'mixed news'} · size ${Number(e.magnitude ?? 0).toFixed(2)}</span>
        </div>
        <p class="text-white font-medium leading-snug">${e.headline ?? ''}</p>
        ${e.summary ? `<p class="mt-1 text-sm text-muted">${e.summary}</p>` : ''}
        ${hit.length > 1 ? `<div class="mt-2 flex flex-wrap gap-1">${hit.map((t) => `<span class="rounded-full border border-line px-2 py-0.5 font-mono text-xs text-muted">${t}</span>`).join('')}</div>` : ''}
        ${e.lesson ? `<p class="mt-2 text-xs text-dim">${e.lesson}</p>` : ''}
        <p class="mt-2 text-[11px] uppercase tracking-wide text-dim">
            Made-up story · changed ${e.bars_affected ?? 0} future days for ${hit.length} stock${hit.length === 1 ? '' : 's'}
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
        <p class="mt-2 text-[11px] uppercase tracking-wide text-dim">Made-up story · prices are still real</p>`;
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
    }
    signalTotal += signals.length;
    if (!signalsOpen) signalUnread += signals.length;
    updateSignalsBadge();
}

// A bill leaving the account is an event the player should feel, not something they
// discover later by noticing the cash number is smaller.
function logCharges() {
    // Charges remain visible in the Bills & dividends panel; no overlay notifications.
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
        summary.textContent = 'Every pattern that has shown up on your stocks.';
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
            ? `${money(last.market_value)} in stocks · ${money(last.cash)} in cash`
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
        logCharges();
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

const LINK_PRIMARY = 'inline-block rounded-full bg-brand px-8 py-3 font-semibold transition hover:bg-brand-soft';
const LINK_SECONDARY = 'inline-block rounded-full border border-line-strong px-6 py-3 font-semibold text-muted transition hover:border-muted hover:text-white';

function finish(state) {
    pause();
    el('summary-range').textContent = `${state.tickers.join(', ')} · ${state.start_date} to ${state.sim_date}`;
    const total = state.portfolio.trading_return_pct;
    const summary = el('summary-return');
    summary.textContent = `${signed(total)}%`;
    summary.className = `mb-3 text-6xl font-bold tabular-nums ${toneFor(total)}`;
    el('summary-net').textContent = money(state.portfolio.net_worth);
    el('summary-start').textContent = money(state.portfolio.starting_cash);

    const level = state.level;
    el('summary-card').classList.toggle('max-w-lg', !level);
    el('summary-card').classList.toggle('max-w-2xl', Boolean(level));
    for (const id of ['summary-level', 'summary-stars', 'summary-replay']) el(id).classList.toggle('hidden', !level);
    el('summary-next').classList.toggle('hidden', !(level?.passed && level.next_level));
    if (level) {
        recordLevel(level);
        el('summary-title').textContent = `Level ${level.number} ${level.passed ? 'passed' : 'complete'}`;
        el('summary-stars').textContent = starText(level.stars);
        const follow = level.goals.find((goal) => goal.id === 'follow');
        const pass = el('summary-pass');
        pass.textContent = !level.passed
            ? `Not passed yet. To unlock the next level: ${follow.label.toLowerCase()}.`
            : level.next_level
                ? `Nice! Level ${level.next_level} is unlocked.`
                : 'That was the last level. You\'re ready for the full game.';
        pass.className = `mb-5 rounded-2xl border p-4 text-center text-sm ${
            level.passed ? 'border-up/40 bg-up/10 text-up' : 'border-warn/40 bg-warn/10 text-warn'}`;
        el('summary-goals').innerHTML = goalItems(level.goals);
        el('summary-trades').innerHTML = level.trades.length
            ? level.trades.map(reviewRow).join('')
            : '<p class="text-dim">No trades this time.</p>';
        const finale = level.passed && !level.next_level;
        const link = el('summary-link');
        link.textContent = finale ? 'Start the full game' : 'All levels';
        link.href = finale ? '/#start-form' : '/#levels';
        link.className = finale ? LINK_PRIMARY : LINK_SECONDARY;
    }
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
    pill.textContent = active ? 'made-up prices on' : 'real prices only';
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
        ? `Also using made-up prices: ${others.join(', ')}. News about a whole industry can move all of them, not just ${info.focus}.`
        : 'Each of your stocks has its own made-up future prices.';
}

async function refreshSimulation() {
    if (!shows(latestState, 'sim')) return;
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
    family.textContent = familyLabel(lesson.family);
    family.className = 'shrink-0 rounded-full border px-3 py-1 text-[10px] uppercase tracking-wide '
        + (FAMILY_TONE[lesson.family] || 'border-line text-muted');

    const figures = [
        ['price', readings.close], ['RSI', readings.rsi14],
        ['20-day avg', readings.sma20], ['50-day avg', readings.sma50],
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
            The coach couldn't explain this one because the AI isn't available right now. The
            pattern is still on your chart, and the Signals drawer has a short note about it.
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
        title: 'Practice trading with fake money',
        body: 'You are trading real stocks at real past prices, but with fake money. You can\'t '
            + 'lose anything real, so try things out and see what happens.',
    },
    {
        title: 'Stock market basics',
        body: 'A stock is a small piece of a company, and one piece is called a share. Each stock '
            + 'has a short code called a ticker, like AAPL for Apple. Prices go up and down every '
            + 'day. You make money by buying shares and later selling them for more than you paid.',
    },
    {
        target: 'bank-panel',
        title: 'This is your bank account',
        body: (state) => {
            const name = state?.bank?.customer?.name;
            const account = state?.bank?.account;
            const paycheck = state?.expenses?.paycheck;
            return `${name ? `You are ${name}, a customer at a practice bank.` : 'This is your practice bank account.'} `
                + `You start with ${money(state?.portfolio?.starting_cash)} in your `
                + `${account?.nickname || 'checking'} account`
                + (paycheck
                    ? `, and you get paid ${money(paycheck.amount)}${paycheck.employer ? ` by ${paycheck.employer}` : ''} every two weeks.`
                    : '. You have no paycheck, so bills come straight out of this money.');
        },
    },
    {
        target: 'bills-panel',
        title: 'You have to pay these bills',
        body: (state) => {
            const bills = state?.expenses;
            const lead = bills?.bill_count
                ? `${bills.bill_count} bills (${money(bills.monthly_total)} a month) come out of your cash when they are due, `
                    + 'even if your money is in stocks.'
                : 'Any bills on this account come out of your cash when they are due.';
            return `${lead} If you don't have enough cash, the bank sells some of your stock to pay. `
                + 'Keep some cash on hand so you decide when to sell, not the bank.';
        },
    },
    {
        target: 'watchlist',
        title: 'The stocks you picked',
        body: 'Each box is a stock you are following. It shows the latest price and how much it '
            + 'moved today. Click one to see its chart. The green number is how many shares you own.',
    },
    {
        target: 'price-chart',
        title: 'The price chart',
        body: 'The bright line is the price at the end of each day. The yellow line is the average '
            + 'price over the last 20 days, and the blue line is the last 50 days. When they cross, '
            + 'the trend may be changing. The grey bars at the bottom show how many shares were traded.',
    },
    {
        target: 'rsi-chart',
        title: 'Two gauges under the chart',
        body: 'RSI is a number from 0 to 100. Above 70 means the stock went up fast. Below 30 means '
            + 'it went down fast. MACD shows if the stock is speeding up or slowing down. You don\'t '
            + 'need to memorize them - the coach explains each one the first time it shows up.',
    },
    {
        target: 'signals-toggle',
        title: 'Signals',
        body: 'When something important shows up on one of your stocks, like RSI going above 70, '
            + 'it goes into this drawer. The number on the tab shows how many you haven\'t read. '
            + 'Click it to read them.',
    },
    {
        target: 'buy-btn',
        title: 'Buying and selling',
        body: 'Type how many shares you want and press Buy or Sell. You pay the price at the end of '
            + 'the current day, and the money comes out of your cash. Max fills in the most you can afford.',
    },
    {
        target: 'play-btn',
        title: 'Moving the clock',
        body: 'Next day moves forward one day. Play keeps moving on its own, and the slider sets '
            + 'the speed. Start slow while you learn to read the charts.',
    },
    {
        target: 'teacher-row',
        title: 'Teacher mode',
        body: 'Keep this on. When a new pattern shows up on one of your stocks, the game pauses '
            + 'and explains what happened, what it usually means, and what to watch for next.',
    },
    {
        target: 'ai-feed',
        title: 'The AI coach',
        body: 'Tips, made-up headlines and lessons show up here. Press Ask for a read any time to '
            + 'get the coach\'s opinion on the stock you are looking at.',
    },
    {
        target: 'news-toggle',
        title: 'The Daily Ledger',
        body: 'The News tab opens today\'s newspaper and pauses the game. Most stories don\'t matter, '
            + 'like office moves or hype columns. A few do, like a big jump in price. Nothing is '
            + 'labeled, so part of the game is learning which is which.',
    },
    {
        target: 'pattern-list',
        title: 'Your pattern scorecard',
        body: 'There are seven patterns to learn. Each one gets checked off the first time it shows '
            + 'up in one of your stocks. Try to learn all seven without losing money.',
    },
];

let tourSteps = TOUR_STEPS;
let tourIndex = 0;
let tourTarget = null;
let tourSaved = null;
let tourRail = null;

function clearSpot() {
    if (tourTarget && tourSaved) {
        tourTarget.style.position = tourSaved.position;
        tourTarget.style.zIndex = tourSaved.zIndex;
        tourTarget.style.boxShadow = tourSaved.boxShadow;
        tourTarget.style.borderRadius = tourSaved.borderRadius;
    }
    if (tourRail) tourRail.style.zIndex = '';
    tourTarget = null;
    tourSaved = null;
    tourRail = null;
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
    // The edge tabs sit in a fixed rail with its own stacking context, so the rail is lifted
    // instead and each tab keeps its own shape.
    tourRail = element.closest('#edge-tabs');
    if (tourRail) tourRail.style.zIndex = '65';
    else if (getComputedStyle(element).position === 'static') {
        element.style.position = 'relative';
        element.style.borderRadius = '1.5rem';
    }
    element.style.zIndex = '65';
    element.style.boxShadow = '0 0 0 3px #3b82f6, 0 0 45px rgba(59,130,246,0.35)';
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
    const step = tourSteps[index];
    // The panel, not the canvas inside it: highlighting a bare canvas lights up a rectangle
    // floating inside its own card. A label is its own control, though - walking up from
    // the teacher switch would light the entire clock panel instead of the switch.
    const raw = step.target ? el(step.target) : null;
    const element = raw
        ? (raw.tagName === 'LABEL' ? raw : (raw.closest('.rounded-3xl') || raw))
        : null;

    el('tour-step').textContent = `Step ${index + 1} of ${tourSteps.length}`;
    el('tour-title').textContent = step.title;
    el('tour-body').textContent = typeof step.body === 'function' ? step.body(latestState) : step.body;
    el('tour-back').classList.toggle('invisible', index === 0);
    el('tour-next').textContent = index === tourSteps.length - 1 ? 'Start trading' : 'Next';

    if (element) element.scrollIntoView({ behavior: 'smooth', block: 'center' });
    spotlight(element);
    // Placed after the scroll settles, or the card lands on the element's old position.
    setTimeout(() => placeCard(element), element ? 320 : 0);
}

function startTour() {
    pause();
    tourSteps = TOUR_STEPS.filter((step) => !step.target || (el(step.target) && !el(step.target).closest('.hidden')));
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

el('tutorial-btn').addEventListener('click', () => (latestState?.level ? openBrief(latestState.level) : startTour()));
el('tour-skip').addEventListener('click', endTour);
el('tour-back').addEventListener('click', () => showTourStep(Math.max(0, tourIndex - 1)));
el('tour-next').addEventListener('click', () => {
    if (tourIndex >= tourSteps.length - 1) endTour();
    else showTourStep(tourIndex + 1);
});

el('news-toggle').addEventListener('click', openNews);
el('news-close').addEventListener('click', () => closeNews(false));
el('news-done').addEventListener('click', () => closeNews(false));
el('news-resume').addEventListener('click', () => closeNews(true));
el('news-prev').addEventListener('click', () => newsEdition?.previous && loadEdition(newsEdition.previous));
el('news-next').addEventListener('click', () => newsEdition?.next && loadEdition(newsEdition.next));
el('news-date').addEventListener('change', (event) => loadEdition(event.target.value));
el('news-overlay').addEventListener('click', (event) => {
    if (event.target === el('news-overlay')) closeNews(false);
});

el('brief-start').addEventListener('click', () => closeBrief(true));
el('brief-look').addEventListener('click', () => closeBrief(false));
el('level-brief-btn').addEventListener('click', () => latestState?.level && openBrief(latestState.level));
el('summary-replay').addEventListener('click', () => startLevel(latestState.level.number, el('summary-replay')));
el('summary-next').addEventListener('click', () => startLevel(latestState.level.next_level, el('summary-next')));

document.addEventListener('keydown', (event) => {
    if (newsOpen() && (event.key === 'ArrowLeft' || event.key === 'ArrowRight') && event.target.tagName !== 'SELECT') {
        const day = event.key === 'ArrowLeft' ? newsEdition?.previous : newsEdition?.next;
        if (day) loadEdition(day);
        return;
    }
    if (event.key !== 'Escape') return;
    if (!el('tour-card').classList.contains('hidden')) endTour();
    else if (!el('coach-overlay').classList.contains('hidden')) closeCoach(false);
    else if (!el('brief-overlay').classList.contains('hidden')) closeBrief(false);
    else if (newsOpen()) closeNews(false);
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
        button.textContent = 'Make up future prices';
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
    button.textContent = 'Adding…';
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
    } catch (error) {
        errorBox.textContent = error.message;
        errorBox.classList.remove('hidden');
    } finally {
        button.disabled = false;
        button.textContent = 'Add this story to the future';
    }
});

// --- load -----------------------------------------------------------------

async function loadCompanyNames() {
    try {
        const data = await call('/api/universe');
        companyNames = Object.fromEntries(data.tickers.map((row) => [row.ticker, row.company_name]));
        companyInfo = Object.fromEntries(data.tickers.map((row) => [row.ticker, row]));
    } catch (error) {
        console.error(error);
    }
}

async function checkAI() {
    try {
        const status = await call('/api/ai/status');
        if (!status.available) {
            const banner = el('ai-status');
            banner.textContent = `${status.detail} Everything else in the game still works without it.`;
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
    const state = await call(`/api/session/${sessionId}/state`);
    render(state);
    if (shows(state, 'ai')) checkAI();
    refreshSimulation();
    refreshBasket();

    // A fresh level opens on its briefing; the first full run gets walked through the screen.
    if (state.level) {
        if (state.status === 'active' && state.sim_date === state.start_date && !state.trades.length) openBrief(state.level);
    } else if (!tourDone) {
        startTour();
    }
})();
