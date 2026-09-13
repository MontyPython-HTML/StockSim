const sessionId = document.body.dataset.sessionId;

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
linkCrosshairs([charts.price, charts.rsi, charts.macd]);

const el = (id) => document.getElementById(id);
const money = (value) => value == null ? '—' : `$${Number(value).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const signed = (value, digits = 2) => value == null ? '—' : `${value >= 0 ? '+' : ''}${Number(value).toFixed(digits)}`;
const pct = (value) => value == null ? '—' : `${signed(value)}%`;
const toneFor = (value) => value == null ? 'text-muted' : value > 0 ? 'text-up' : value < 0 ? 'text-down' : 'text-muted';
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const formatShares = (value) => {
    const shares = Number(value) || 0;
    const whole = Math.abs(shares - Math.round(shares)) < 0.0005;
    return shares.toLocaleString('en-US', {
        minimumFractionDigits: whole ? 0 : 2,
        maximumFractionDigits: whole ? 0 : 4,
    });
};

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

const FEED_REFRESH_MS = 5000;
let feedTimer = null;

const TEACHER_KEY = 'tradingTeacher.teacherMode';
const TOUR_KEY = 'tradingTeacher.tourDone';
const LESSON_POLL_MS = 1200;
const LESSON_WAIT_MS = 30000;
let teacherMode = true;
let awaitingLesson = null;
let lessonPollTimer = null;
let lessonWaitStarted = 0;
const shownLessons = new Set();

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
        button.className = `flex items-center gap-4 rounded-2xl border px-5 py-3 text-left transition ${
            active ? 'border-brand bg-brand/10' : 'border-line-strong bg-transparent hover:border-muted'}`;
        button.innerHTML = `
            <span>
                <span class="block font-mono text-lg font-semibold">${quote.ticker}</span>
                <span class="block cursor-help text-xs text-dim" data-stock-tip="${esc(quote.ticker)}">${companyNames[quote.ticker] || ''}</span>
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
                <button type="button" class="focus-chip font-mono font-semibold transition hover:text-glow" data-stock-tip="${esc(position.ticker)}">${position.ticker}</button>
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
    const panel = el('bills-panel');
    panel.classList.toggle('border-down/50', overdrawn);
    panel.classList.toggle('bg-down/5', overdrawn);
    panel.classList.toggle('border-line', !overdrawn);
    panel.classList.toggle('bg-surface', !overdrawn);

    const runway = el('bills-runway');
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
        ? `${totals.join(' · ')} so far this run. Stock performance is ${signed(state.portfolio.trading_return_pct)}%.`
        : '';
    const dividends = state.dividends_paid || 0;
    el('dividends-paid').textContent = money(dividends);

    const schedule = state.dividend_schedule || [];
    const shown = schedule.filter((row) => row.ex_date).slice(0, 6);
    const quiet = schedule.length - shown.length;
    el('dividend-schedule').innerHTML = shown.length
        ? shown.map((row) => {
            const soon = row.days_away <= 7;
            return `
            <div class="flex items-center justify-between gap-3 rounded-xl border ${soon ? 'border-up/40 bg-up/5' : 'border-line bg-ink-soft'} px-3 py-2">
                <span>
                    <span class="font-mono font-semibold">${esc(row.ticker)}</span>
                    <span class="text-dim">· ${formatShares(row.shares)} shares</span>
                    <span class="block text-[11px] text-dim">ex-date ${row.ex_date} · in ${row.days_away} day${row.days_away === 1 ? '' : 's'}</span>
                </span>
                <span class="shrink-0 text-right">
                    <span class="block tabular-nums text-up">est ${money(row.estimated)}</span>
                    <span class="block text-[11px] text-dim">${money(row.per_share)}/share</span>
                </span>
            </div>`;
        }).join('')
            + (quiet > 0
                ? `<p class="pt-1 text-dim">${quiet} other holding${quiet === 1 ? '' : 's'} pay no dividend in the next few months.</p>`
                : '')
        : '<p class="text-dim">Buy a dividend-paying stock to see its ex-dates here.</p>';

    const dividendRows = state.dividends || [];
    el('dividend-history').innerHTML = dividendRows.length
        ? dividendRows.slice(-5).reverse().map((payment) => {
            const bought = Number(payment.reinvested_shares || 0);
            return `
            <div class="flex items-center justify-between gap-3 rounded-xl border border-up/20 bg-ink-soft px-3 py-2">
                <span><span class="font-mono font-semibold">${esc(payment.ticker)}</span> · ${payment.ex_date}</span>
                ${bought > 0
                    ? `<span class="shrink-0 text-right"><span class="block tabular-nums text-up">+${money(payment.amount)}</span>
                        <span class="block text-[11px] text-dim">bought ${formatShares(bought)} @ ${money(payment.reinvest_price)}</span></span>`
                    : `<span class="tabular-nums text-up">+${money(payment.amount)}</span>`}
            </div>`;
        }).join('')
        : '<p class="text-dim">No dividends received yet. Hold a dividend-paying stock through its ex-date.</p>';

    el('reinvest-toggle').checked = Boolean(state.reinvest_dividends);
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
        ? 'Pure stock simulation: Nessie income and bills are disabled.'
        : (bills.bill_count
            ? `${bills.bill_count} bills take ${money(bills.monthly_total)} a month out of this account`
            : 'No bills come out of this account')
            + (paycheck ? `, and your paycheck brings in about ${money(paycheck.monthly)}.` : '.')
            + ' If your cash cannot cover a bill, the bank sells your shares to pay it.';
    const dividendTotal = state.dividends_paid || 0;
    el('bank-dividend-note').textContent = dividendTotal
        ? `${money(dividendTotal)} in investment dividends has been added to cash.`
        : 'Dividends are separate from Nessie paychecks and are paid by the stocks you own.';
}

let newsSignature = null;
let newsStories = [];

const NEWSWIRE_SECONDS_PER_STORY = 6;
const NEWSWIRE_MIN_SECONDS = 45;
const NEWSWIRE_MAX_FRAME_SECONDS = 0.1;

let newsOffset = 0;
let newsPitch = 0;
let newsSpeed = 0;
let newsRaf = null;
let newsFrameTime = 0;
let newsHovered = false;
let newsIds = [];

const newsTrack = () => el('news-track');
const newsRuns = () => [...newsTrack().children]
    .filter((node) => node.classList.contains('newswire-run'));
const newsReducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;

function newsGap() {
    const styles = getComputedStyle(newsTrack());
    const gap = parseFloat(styles.columnGap || styles.gap);
    return Number.isFinite(gap) ? gap : 0;
}

const newsRunWidth = () => newsRuns()[0]?.getBoundingClientRect().width || 0;

const newsPaused = () => newsHovered || newsOpen() || newsReducedMotion();

function newsDrift(now) {
    newsRaf = requestAnimationFrame(newsDrift);
    const elapsed = newsFrameTime
        ? Math.min((now - newsFrameTime) / 1000, NEWSWIRE_MAX_FRAME_SECONDS)
        : 0;
    newsFrameTime = now;
    if (!newsSpeed || !newsPitch || newsPaused()) return;
    newsOffset = (newsOffset + newsSpeed * elapsed) % newsPitch;
    newsTrack().style.transform = `translate3d(${-newsOffset}px, 0, 0)`;
}

function startNewsDrift() {
    if (newsRaf !== null || !newsPitch || newsReducedMotion()) return;
    newsFrameTime = 0;
    newsRaf = requestAnimationFrame(newsDrift);
}

function measureNews() {
    newsPitch = newsRunWidth() + newsGap();
    newsSpeed = newsPitch
        ? newsPitch / Math.max(NEWSWIRE_MIN_SECONDS, newsIds.length * NEWSWIRE_SECONDS_PER_STORY)
        : 0;
    if (newsPitch) newsOffset = ((newsOffset % newsPitch) + newsPitch) % newsPitch;
    newsTrack().style.transform = `translate3d(${-newsOffset}px, 0, 0)`;
    startNewsDrift();
}

function storyChip(story, decorative = false) {
    const holder = document.createElement('div');
    holder.innerHTML = `<button type="button" data-story="${esc(story.id)}"
        class="news-chip flex shrink-0 items-center gap-3 rounded-2xl border border-line bg-ink-soft px-4 py-2 text-left transition hover:border-brand hover:bg-brand/10">
        <span class="font-mono text-xs text-muted">${esc(story.ticker)}</span>
        <span class="max-w-2xl truncate text-sm text-white">${esc(story.headline)}</span>
        <span class="shrink-0 text-xs tabular-nums text-dim">${esc(story.date)}</span>
    </button>`;
    const chip = holder.firstElementChild;
    if (decorative) {
        chip.tabIndex = -1;
        chip.setAttribute('aria-hidden', 'true');
    }
    return chip;
}

function buildNewsRuns(stories) {
    const track = newsTrack();
    track.innerHTML = '';
    const ordered = [...stories].reverse();
    const lead = document.createElement('div');
    lead.className = 'newswire-run';
    lead.dataset.role = 'lead';
    for (const story of ordered) lead.appendChild(storyChip(story));
    track.appendChild(lead);

    const gap = newsGap();
    const width = el('news-viewport').clientWidth;
    const pitch = lead.getBoundingClientRect().width + gap;
    const copies = pitch > 0 ? Math.max(2, Math.ceil((width + gap) / pitch) + 1) : 2;
    for (let i = 1; i < copies; i += 1) {
        const echo = lead.cloneNode(true);
        echo.dataset.role = 'echo';
        echo.setAttribute('aria-hidden', 'true');
        for (const chip of echo.children) chip.tabIndex = -1;
        track.appendChild(echo);
    }

    newsIds = ordered.map((story) => story.id);
    newsOffset = Math.max(0, pitch - width);
    measureNews();
}

function renderNews(state) {
    const stories = state.news || [];
    const ids = stories.map((story) => story.id);
    const signature = ids.join('|');
    if (signature === newsSignature) return;
    newsSignature = signature;
    newsStories = stories;

    el('news-count').textContent = `${stories.length} ${stories.length === 1 ? 'story' : 'stories'}`;
    if (!stories.length) {
        newsTrack().innerHTML = '<p class="text-xs text-dim">Headlines show up as the clock moves.</p>';
        newsIds = [];
        newsPitch = 0;
        newsSpeed = 0;
        return;
    }

    const wanted = new Set(ids);
    const departed = newsIds.filter((id) => !wanted.has(id));
    const agesOut = departed.length < newsIds.length
        && departed.every((id, index) => newsIds[index] === id);
    if (!newsIds.length || !agesOut) {
        buildNewsRuns(stories);
        return;
    }

    const known = new Set(newsIds);
    const arrivals = stories.filter((story) => !known.has(story.id)).reverse();

    const runs = newsRuns();
    const widthBefore = newsRunWidth();
    for (const run of runs) {
        for (let i = 0; i < departed.length; i += 1) run.firstElementChild?.remove();
    }
    const widthAfter = newsRunWidth();
    newsPitch = widthAfter + newsGap();
    if (newsPitch) {
        newsOffset = ((newsOffset - (widthBefore - widthAfter)) % newsPitch + newsPitch) % newsPitch;
    }

    for (const run of runs) {
        const decorative = run.dataset.role === 'echo';
        for (const story of arrivals) run.appendChild(storyChip(story, decorative));
    }
    newsIds = newsIds.slice(departed.length).concat(arrivals.map((story) => story.id));
    measureNews();
    const viewport = el('news-viewport').clientWidth;
    if ((runs.length - 1) * newsPitch < viewport) buildNewsRuns(stories);
}


let newsEdition = null;

const utcDate = (iso, options) => new Date(`${iso}T00:00:00Z`).toLocaleDateString('en-US', { timeZone: 'UTC', ...options });
const newsOpen = () => !el('news-overlay').classList.contains('hidden');

async function openNews(day) {
    const wasPlaying = Boolean(timer);
    pause();
    if (signalsOpen) setSignalsOpen(false);
    el('news-resume').classList.toggle('hidden', !wasPlaying);
    const overlay = el('news-overlay');
    overlay.classList.remove('hidden');
    overlay.classList.add('flex');
    await loadEdition(day || latestState?.sim_date);
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
                <span class="min-w-0">
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
    applyLevel(state);
    el('sim-date').textContent = state.sim_date;

    const simulated = state.simulation && state.simulation.mode === 'simulated';
    el('mode-label').textContent = simulated
        ? `Simulated future · forked from real history on ${state.simulation.fork_date}`
        : 'Replaying real market history';

    el('focus-label').textContent = state.focus;
    el('focus-name').textContent = companyNames[state.focus] || '';
    if (priceHint) priceHint.setAttribute('aria-label', `Show the ${state.focus} price chart large`);

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
    refreshStockTip();
    renderFeed(state.ai_feed);
    renderNews(state);
    renderPatterns(state.patterns);
    renderLevel(state);

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
    }
    signalTotal += signals.length;
    if (!signalsOpen) signalUnread += signals.length;
    updateSignalsBadge();
}

function setStatus(text, active) {
    const pill = el('status-pill');
    pill.textContent = text;
    pill.className = `rounded-full border px-4 py-1.5 text-sm ${active ? 'border-accent/50 bg-accent/10 text-accent-soft' : 'border-line text-muted'}`;
}


function setSignalsOpen(open) {
    signalsOpen = open;
    el('signal-drawer').classList.toggle('translate-x-full', !open);
    el('signal-drawer').setAttribute('aria-hidden', String(!open));
    el('signal-backdrop').classList.toggle('hidden', !open);
    const tab = el('signals-toggle');
    tab.setAttribute('aria-expanded', String(open));
    tab.classList.toggle('opacity-0', open);
    tab.classList.toggle('pointer-events-none', open);
    if (open) signalUnread = 0;
    updateSignalsBadge();
}

function updateSignalsBadge() {
    const count = signalsOpen ? signalTotal : signalUnread;
    const unread = !signalsOpen && signalUnread > 0;

    const badge = el('signals-count');
    badge.textContent = String(count);
    badge.className = 'rounded-full bg-warn px-2 py-0.5 text-xs font-bold leading-none text-ink'
        + (count === 0 ? ' hidden' : '');

    const headerBadge = el('signals-open-count');
    headerBadge.textContent = String(count);
    headerBadge.className = 'rounded-full bg-warn px-1.5 py-0.5 text-[11px] font-bold leading-none text-ink'
        + (count === 0 ? ' hidden' : '');

    for (const node of [el('signals-toggle'), el('signals-open-btn')]) {
        node.classList.toggle('signals-attention', unread);
    }

    const summary = el('signals-summary');
    if (!signalTotal) {
        summary.textContent = 'Every pattern the clock has printed on your own symbols.';
        return;
    }
    const symbols = signalSymbols.size;
    summary.textContent = `${signalTotal} signal${signalTotal === 1 ? '' : 's'} across `
        + `${symbols} symbol${symbols === 1 ? '' : 's'}.`;
}


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
        basketQueued = true;
        return;
    }
    basketInFlight = true;
    try {
        const payload = await call(`/api/session/${sessionId}/basket`);
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
        scheduleBasket(0);
        const lesson = (state.pending_ai || []).find((job) => job.kind === 'PATTERN_LESSON');
        if (teacherMode && lesson && !awaitingLesson) {
            pause();
            openCoach(lesson);
            return;
        }
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
    scheduleFeedRefresh(0);
}

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
    if (level) {
        recordLevel(level);
        el('summary-card').classList.remove('max-w-lg');
        el('summary-card').classList.add('max-w-2xl');
        for (const id of ['summary-level', 'summary-stars', 'summary-replay']) {
            el(id).classList.remove('hidden');
        }
        el('summary-next').classList.toggle('hidden', !(level.passed && level.next_level));
        el('summary-title').textContent = `Level ${level.number} ${level.passed ? 'passed' : 'complete'}`;
        el('summary-stars').textContent = starText(level.stars);
        const follow = level.goals.find((goal) => goal.id === 'follow');
        const pass = el('summary-pass');
        pass.textContent = !level.passed
            ? `Not passed yet. To unlock the next level: ${(follow?.label || 'follow the rule').toLowerCase()}.`
            : level.next_level
                ? `Level ${level.next_level} is unlocked.`
                : "That was the last level. You're ready for the full game.";
        pass.className = `mb-5 rounded-2xl border p-4 text-center text-sm ${
            level.passed ? 'border-up/40 bg-up/10 text-up' : 'border-warn/40 bg-warn/10 text-warn'}`;
        el('summary-goals').innerHTML = goalItems(level.goals);
        el('summary-trades').innerHTML = level.trades.length
            ? level.trades.map(reviewRow).join('')
            : '<p class="text-dim">No trades this time.</p>';
        const finale = level.passed && !level.next_level;
        el('summary-link').textContent = finale ? 'Start the full game' : 'All levels';
        el('summary-link').href = finale ? '/' : '/#levels';
    }

    const overlay = el('summary-overlay');
    overlay.classList.remove('hidden');
    overlay.classList.add('flex');
}

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


function setTeacherMode(on) {
    teacherMode = on;
    el('teacher-mode').checked = on;
    try { localStorage.setItem(TEACHER_KEY, on ? '1' : '0'); } catch {  }
}

function openCoach(job) {
    awaitingLesson = job;
    lessonWaitStarted = Date.now();
    el('coach-title').textContent = job.pattern || 'A pattern just appeared';
    el('coach-subtitle').textContent = `${job.ticker}${job.date ? ` · ${job.date}` : ''}`;
    el('coach-family').textContent = 'analysing';
    el('coach-body').classList.add('hidden');
    el('coach-body').innerHTML = '';
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
    if (awaitingLesson) lessonPollTimer = setTimeout(pollLesson, LESSON_POLL_MS);
}

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

function applyLevel(state) {
    if (levelApplied) return;
    levelApplied = true;
    for (const node of document.querySelectorAll('[data-panel]')) {
        node.classList.toggle('hidden', !shows(state, node.dataset.panel));
    }
    for (const id of ['indicator-row', 'portfolio-row']) {
        if (el(id)) balanceRow(el(id));
    }
    ensureVisibleHero();
    if (charts.price && typeof setPriceOverlays === 'function') {
        setPriceOverlays(charts.price, {
            trend: shows(state, 'trend'),
            volume: shows(state, 'volume'),
        });
    }

    const level = state.level;
    if (!level) return;
    document.title = `Level ${level.number} · ${level.title} · StockSim`;
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
    try { progress = JSON.parse(localStorage.getItem(LEVELS_KEY) || '{}') || {}; } catch {  }
    const best = progress[level.number] || {};
    progress[level.number] = {
        stars: Math.max(best.stars || 0, level.stars),
        passed: Boolean(best.passed || level.passed),
    };
    try { localStorage.setItem(LEVELS_KEY, JSON.stringify(progress)); } catch {  }
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


const COLLAPSE_KEY = 'tradingTeacher.collapsed';

function collapsedNames() {
    try {
        return new Set(JSON.parse(localStorage.getItem(COLLAPSE_KEY) || '[]'));
    } catch {
        return new Set();
    }
}

function rememberCollapsed(name, collapsed) {
    const names = collapsedNames();
    if (collapsed) names.add(name);
    else names.delete(name);
    try {
        localStorage.setItem(COLLAPSE_KEY, JSON.stringify([...names]));
    } catch {  }
}

function buildCardHead(card) {
    const first = card.firstElementChild;
    const heading = card.querySelector('h2');
    if (!first || !heading) return first;

    const head = document.createElement('div');
    head.className = 'card-head mb-4';
    const top = document.createElement('div');
    top.className = 'flex items-center gap-3';
    const controls = document.createElement('div');
    controls.className = 'card-controls ml-auto flex shrink-0 items-center gap-1';
    card.insertBefore(head, first);
    heading.classList.add('min-w-0');
    top.append(heading, controls);
    head.appendChild(top);

    if (first !== heading) {
        const extra = document.createElement('div');
        extra.className = 'card-head-extra mt-2 flex flex-wrap items-center gap-2';
        for (const node of [...first.children]) {
            if (node.id || node.children.length || node.textContent.trim()) extra.appendChild(node);
        }
        first.remove();
        if (extra.children.length) head.appendChild(extra);
    }
    return head;
}

function setupCollapsibles() {
    const remembered = collapsedNames();
    for (const card of document.querySelectorAll('[data-collapse]')) {
        const name = card.dataset.collapse;
        const header = card.querySelector('[data-chart-controls]') ? card.firstElementChild : buildCardHead(card);
        if (!header) continue;

        const body = document.createElement('div');
        body.className = 'collapse-body';
        while (header.nextSibling) body.appendChild(header.nextSibling);
        card.appendChild(body);

        const toggle = document.createElement('button');
        toggle.type = 'button';
        toggle.className = 'collapse-toggle ml-auto flex shrink-0 items-center gap-1 rounded-full border border-line px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide text-dim transition hover:border-muted hover:text-white';
        toggle.innerHTML = '<span class="collapse-label">Hide</span>'
            + '<svg viewBox="0 0 16 16" class="h-3 w-3" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 6.5 8 10.5l4-4" /></svg>';
        (card.querySelector('[data-chart-controls], .card-controls') || header).appendChild(toggle);

        const setCollapsed = (collapsed) => {
            card.classList.toggle('card-collapsed', collapsed);
            toggle.setAttribute('aria-expanded', String(!collapsed));
            toggle.querySelector('.collapse-label').textContent = collapsed ? 'Show' : 'Hide';
            toggle.title = collapsed ? 'Show this panel' : 'Hide this panel';
        };
        setCollapsed(remembered.has(name) || card.dataset.collapseDefault === 'closed');

        toggle.addEventListener('click', () => {
            const collapsed = !card.classList.contains('card-collapsed');
            setCollapsed(collapsed);
            rememberCollapsed(name, collapsed);
        });
    }
}


const ORDER_KEY = 'tradingTeacher.cardOrder';

function gripButton(label) {
    const grip = document.createElement('button');
    grip.type = 'button';
    grip.draggable = true;
    grip.className = 'card-grip flex h-7 items-center rounded-lg px-1 text-dim transition hover:text-white';
    grip.title = 'Drag to move (Alt + arrows also works)';
    grip.setAttribute('aria-label', label);
    grip.innerHTML = '<svg viewBox="0 0 16 16" class="h-3.5 w-3.5" fill="currentColor" aria-hidden="true">'
        + '<circle cx="6" cy="4" r="1.2"/><circle cx="10" cy="4" r="1.2"/>'
        + '<circle cx="6" cy="8" r="1.2"/><circle cx="10" cy="8" r="1.2"/>'
        + '<circle cx="6" cy="12" r="1.2"/><circle cx="10" cy="12" r="1.2"/></svg>';
    return grip;
}

function placeGrip(card, grip, fallback) {
    const fold = card.querySelector('.collapse-toggle');
    (fold?.parentElement || fallback).insertBefore(grip, fold || null);
}

function storedOrder() {
    try {
        const order = JSON.parse(localStorage.getItem(ORDER_KEY) || '[]');
        return Array.isArray(order) ? order : [];
    } catch {
        return [];
    }
}

function rememberOrder(container) {
    try {
        const ids = [...container.children].map((card) => card.dataset.collapse).filter(Boolean);
        localStorage.setItem(ORDER_KEY, JSON.stringify(ids));
    } catch {  }
}

function applyStoredOrder(container) {
    const order = storedOrder();
    if (!order.length) return;
    const rank = new Map(order.map((id, index) => [id, index]));
    const cards = [...container.children];
    cards
        .sort((a, b) => (rank.get(a.dataset.collapse) ?? order.length) - (rank.get(b.dataset.collapse) ?? order.length))
        .forEach((card) => container.appendChild(card));
}

function setupReorder() {
    const container = document.querySelector('.card-flow');
    if (!container) return;
    applyStoredOrder(container);

    let dragging = null;

    const nearest = (x, y) => {
        let best = null;
        let bestScore = Infinity;
        for (const card of container.children) {
            if (card === dragging) continue;
            const box = card.getBoundingClientRect();
            const score = Math.abs(x - (box.left + box.width / 2)) * 2 + Math.abs(y - (box.top + box.height / 2));
            if (score < bestScore) {
                bestScore = score;
                best = card;
            }
        }
        return best;
    };

    container.addEventListener('dragover', (event) => {
        if (!dragging) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = 'move';
        const target = nearest(event.clientX, event.clientY);
        if (!target || target === dragging) return;
        const box = target.getBoundingClientRect();
        const after = event.clientY > box.top + box.height / 2;
        container.insertBefore(dragging, after ? target.nextSibling : target);
    });

    container.addEventListener('drop', (event) => {
        if (dragging) event.preventDefault();
    });

    for (const card of container.querySelectorAll('[data-collapse]')) {
        const header = card.firstElementChild;
        if (!header) continue;
        const grip = gripButton(`Reorder the ${card.dataset.collapse} panel`);
        placeGrip(card, grip, header);

        grip.addEventListener('dragstart', (event) => {
            dragging = card;
            card.classList.add('is-dragging');
            event.dataTransfer.effectAllowed = 'move';
            event.dataTransfer.setData('text/plain', card.dataset.collapse || '');
            const box = card.getBoundingClientRect();
            event.dataTransfer.setDragImage(card, event.clientX - box.left, event.clientY - box.top);
        });

        grip.addEventListener('dragend', () => {
            card.classList.remove('is-dragging');
            dragging = null;
            rememberOrder(container);
        });

        grip.addEventListener('keydown', (event) => {
            const step = event.key === 'ArrowUp' ? -1 : event.key === 'ArrowDown' ? 1 : 0;
            if (!event.altKey || !step) return;
            event.preventDefault();
            const cards = [...container.children];
            const index = cards.indexOf(card) + step;
            if (index < 0 || index >= cards.length) return;
            container.insertBefore(card, step < 0 ? cards[index] : cards[index].nextSibling);
            rememberOrder(container);
            grip.focus();
        });
    }
}


const HERO_KEY = 'tradingTeacher.heroChart';
const CHART_KEYS = ['price', 'rsi', 'macd', 'basket', 'equity'];
const CHART_LABELS = { price: 'price', basket: 'basket', equity: 'account equity' };
let priceHint = null;

const CHART_ORDER_KEY = 'tradingTeacher.chartOrder';
let draggingChart = null;

const chartCards = () => [...document.querySelectorAll('.chart-card')];

function heroCard() {
    return chartCards()[0];
}

function cardForChart(name) {
    return document.querySelector(`.chart-card[data-chart="${name}"]`);
}

function swapCharts(a, b, { remember = true } = {}) {
    if (!a || !b || a === b) return;
    const marker = document.createComment('chart-swap');
    a.replaceWith(marker);
    b.replaceWith(a);
    marker.replaceWith(b);

    const cards = chartCards();
    cards.forEach((card, index) => card.classList.toggle('is-hero', index === 0));
    const fold = cards[0].querySelector('.collapse-toggle');
    if (cards[0].classList.contains('card-collapsed') && fold) fold.click();
    for (const id of ['indicator-row', 'portfolio-row']) {
        if (el(id)) balanceRow(el(id));
    }
    if (!remember) return;
    try {
        localStorage.setItem(CHART_ORDER_KEY, JSON.stringify(cards.map((card) => card.dataset.chart)));
        localStorage.setItem(HERO_KEY, cards[0].dataset.chart);
    } catch {  }
}

function storedChartOrder() {
    try {
        const order = JSON.parse(localStorage.getItem(CHART_ORDER_KEY) || 'null');
        return Array.isArray(order) ? order.filter((name) => CHART_KEYS.includes(name)) : null;
    } catch {
        return null;
    }
}

function ensureVisibleHero() {
    const cards = chartCards();
    if (!cards[0]?.classList.contains('hidden')) return;
    const visible = cards.find((card) => !card.classList.contains('hidden'));
    if (visible) swapCharts(visible, cards[0], { remember: false });
}

function setupChartMoves(card, name, label, controls) {
    const grip = gripButton(`Move the ${label} chart`);
    placeGrip(card, grip, controls);

    grip.addEventListener('dragstart', (event) => {
        draggingChart = card;
        card.classList.add('is-dragging');
        event.dataTransfer.effectAllowed = 'move';
        event.dataTransfer.setData('text/plain', name);
        const box = card.getBoundingClientRect();
        event.dataTransfer.setDragImage(card, event.clientX - box.left, event.clientY - box.top);
    });
    grip.addEventListener('dragend', () => {
        for (const other of chartCards()) other.classList.remove('is-dragging', 'is-drop-target');
        draggingChart = null;
    });

    card.addEventListener('dragover', (event) => {
        if (!draggingChart || draggingChart === card) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = 'move';
        card.classList.add('is-drop-target');
    });
    card.addEventListener('dragleave', (event) => {
        if (!card.contains(event.relatedTarget)) card.classList.remove('is-drop-target');
    });
    card.addEventListener('drop', (event) => {
        if (!draggingChart || draggingChart === card) return;
        event.preventDefault();
        card.classList.remove('is-drop-target');
        swapCharts(draggingChart, card);
    });

    grip.addEventListener('keydown', (event) => {
        const step = ['ArrowUp', 'ArrowLeft'].includes(event.key) ? -1
            : ['ArrowDown', 'ArrowRight'].includes(event.key) ? 1 : 0;
        if (!event.altKey || !step) return;
        event.preventDefault();
        const visible = chartCards().filter((other) => !other.classList.contains('hidden'));
        const target = visible[visible.indexOf(card) + step];
        if (!target) return;
        swapCharts(card, target);
        grip.focus();
    });
}

function storedHero() {
    try {
        const name = localStorage.getItem(HERO_KEY);
        return CHART_KEYS.includes(name) ? name : CHART_KEYS[0];
    } catch {
        return CHART_KEYS[0];
    }
}

function promoteChart(name) {
    swapCharts(cardForChart(name), heroCard());
}

function setupHeroChart() {
    for (const card of document.querySelectorAll('.chart-card')) {
        const name = card.dataset.chart;
        if (!name || !charts[name]) continue;

        const title = card.querySelector('h2')?.cloneNode(true);
        if (title) for (const control of title.querySelectorAll('button')) control.remove();
        const label = CHART_LABELS[name]
            || (title?.textContent || '').replace(/\s+/g, ' ').trim()
            || name;
        const hint = document.createElement('button');
        hint.type = 'button';
        hint.className = 'chart-zoom-hint shrink-0 items-center gap-1 rounded-full border border-line px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide text-dim transition hover:border-muted hover:text-white';
        hint.innerHTML = '<span>Enlarge</span>'
            + '<svg viewBox="0 0 16 16" class="h-3 w-3" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6.5 2.5h-4v4M9.5 13.5h4v-4M2.5 2.5l4.5 4.5M13.5 13.5 9 9"/></svg>';
        hint.setAttribute('aria-label', `Show the ${label} chart large`);
        const header = card.firstElementChild;
        const controls = card.querySelector('[data-chart-controls]') || header;
        if (controls) controls.insertBefore(hint, controls.querySelector('.collapse-toggle'));
        if (name === 'price') priceHint = hint;
        if (controls) setupChartMoves(card, name, label, controls);

        card.addEventListener('click', (event) => {
            if (!event.target.closest('.chart-stage, .chart-zoom-hint')) return;
            promoteChart(name);
        });
    }

    const order = storedChartOrder();
    if (order) {
        order.forEach((chart, index) => swapCharts(cardForChart(chart), chartCards()[index], { remember: false }));
    } else {
        const remembered = storedHero();
        if (remembered !== CHART_KEYS[0]) promoteChart(remembered);
    }
}


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
        target: 'signals-toggle',
        title: 'Signals',
        body: 'When a pattern prints on one of your stocks - a moving-average cross, RSI running '
            + 'hot, a volume spike - it lands in this drawer quietly instead of popping up over the '
            + 'chart. The blue tab on the right edge, and the Signal log button in the header, both '
            + 'count the ones you have not read yet.',
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
        body: 'The clock sits in the bar at the top of the page and stays there as you scroll '
            + 'down. Next day steps forward one trading day, Play runs the clock by itself and the '
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
        target: 'news-panel',
        title: 'The newswire',
        body: 'Headlines about the stocks you picked drift along the top of the page as the clock '
            + 'runs. Most of it is noise - conference talks, office moves, routine filings - and a '
            + 'few stories actually matter. Nothing is labelled, so telling them apart is part of '
            + 'the game. Click the bar and the clock stops while you read the Daily Ledger - click a '
            + 'headline to open the paper from that day.',
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
    if (getComputedStyle(element).position === 'static') {
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
    const step = TOUR_STEPS[index];
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
    try { localStorage.setItem(TOUR_KEY, '1'); } catch {  }
}


el('signals-toggle').addEventListener('click', () => setSignalsOpen(true));
el('signals-open-btn').addEventListener('click', () => setSignalsOpen(true));
el('news-panel').addEventListener('click', (event) => {
    const chip = event.target.closest('.news-chip');
    const story = chip && newsStories.find((row) => row.id === chip.dataset.story);
    openNews(story?.date);
});
el('news-close').addEventListener('click', () => closeNews(false));
el('news-done').addEventListener('click', () => closeNews(false));
el('news-resume').addEventListener('click', () => closeNews(true));
el('news-prev').addEventListener('click', () => newsEdition?.previous && loadEdition(newsEdition.previous));
el('news-next').addEventListener('click', () => newsEdition?.next && loadEdition(newsEdition.next));
el('news-date').addEventListener('change', (event) => loadEdition(event.target.value));
el('news-overlay').addEventListener('click', (event) => {
    if (event.target === el('news-overlay')) closeNews(false);
});
el('news-viewport').addEventListener('pointerenter', (event) => {
    if (event.pointerType !== 'touch') newsHovered = true;
});
el('news-viewport').addEventListener('pointerleave', () => { newsHovered = false; });
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
el('brief-start').addEventListener('click', () => closeBrief(true));
el('brief-look').addEventListener('click', () => closeBrief(false));
el('level-brief-btn').addEventListener('click', () => latestState?.level && openBrief(latestState.level));
el('summary-replay').addEventListener('click', () => startLevel(latestState.level.number, el('summary-replay')));
el('summary-next').addEventListener('click', () => startLevel(latestState.level.next_level, el('summary-next')));
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
    else if (newsOpen()) closeNews(false);
});
document.addEventListener('keydown', (event) => {
    if (!newsOpen() || event.target.tagName === 'SELECT') return;
    const day = event.key === 'ArrowLeft' ? newsEdition?.previous
        : event.key === 'ArrowRight' ? newsEdition?.next : null;
    if (day) loadEdition(day);
});
el('reinvest-toggle').addEventListener('change', async (event) => {
    const wanted = event.target.checked;
    event.target.disabled = true;
    try {
        render(await call(`/api/session/${sessionId}/dividends`, {
            method: 'POST',
            body: JSON.stringify({ reinvest: wanted, focus }),
        }));
    } catch (error) {
        event.target.checked = !wanted;
        console.error(error);
    } finally {
        event.target.disabled = false;
    }
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
    } catch (error) {
        errorBox.textContent = error.message;
        errorBox.classList.remove('hidden');
    } finally {
        button.disabled = false;
        button.textContent = 'Push event into the future';
    }
});


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
    } catch {  }
    setTeacherMode(savedTeacher === null ? true : savedTeacher === '1');

    setupCollapsibles();
    setupReorder();
    setupHeroChart();
    await loadCompanyNames();
    checkAI();
    render(await call(`/api/session/${sessionId}/state`));
    refreshSimulation();
    refreshBasket();

    if (!tourDone) startTour();
})();
