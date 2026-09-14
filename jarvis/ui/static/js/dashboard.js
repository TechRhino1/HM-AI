/* ===========================================================================
   JARVIS — Trading Terminal controller (dashboard.js)

   DESIGN NOTES
   ------------
   1. NO HARD-CODED MARKET DATA. Every value rendered here comes from an API
      response. Where a field is absent the UI shows an explicit state
      (loading / empty / stale / error) — never a plausible-looking default.
      A trading screen that invents a price is worse than one that shows none.

   2. EVERY REQUEST IS BOUNDED. fetch() has no default timeout, so an endpoint
      that never responds leaves a panel spinning forever. apiGet() wraps every
      call in an AbortController with a per-endpoint budget, and the caller
      renders a real error state when it trips.

   3. STALENESS IS VISIBLE. Cached values are useful, but a cached value shown
      as live is a trap. Every payload carries its own timestamp where the API
      provides one, and the header shows the age once it exceeds the cadence.

   4. NO innerHTML WITH SERVER DATA. Symbol names, strategies, gate reasons and
      error strings all originate outside this process. Every one is written
      with textContent, or escaped through esc() when it must be interpolated
      into a template string.

   5. POLLING IS TIERED. Telemetry drives the account strip and the watchlist,
      so it polls fast. Candles are heavier. Reliability and job lists change
      slowly. Polling everything at the fastest rate burns CPU for no gain.
   =========================================================================== */
(function () {
  'use strict';

  /* ── Configuration ────────────────────────────────────────────────────── */
  var POLL = {
    telemetry: 3000,
    jobs: 4000,
    pending: 15000,
    chart: 20000,
    analytics: 20000
  };

  // Per-request budgets in ms. Chosen from measured behaviour: local endpoints
  // answer in well under a second, but the external-provider routes (stocks,
  // india) hang indefinitely without a ceiling.
  var TIMEOUT = {
    fast: 8000,
    normal: 15000,
    slow: 60000
  };

  var VIEWS = ['trade', 'analytics', 'backtest'];
  var PANES = ['watchlist', 'chart', 'ticket'];

  var state = {
    view: 'trade',
    pane: 'watchlist',
    symbol: null,
    timeframe: 'H1',
    decisions: {},        // symbol -> decision object (from telemetry)
    marketStatuses: {},   // symbol -> session status
    positions: [],
    history: [],
    account: null,
    services: {},
    executionMode: null,
    safeMode: null,
    telemetryAt: null,    // client clock when telemetry last arrived
    serverTimestamp: null,
    reliability: [],
    jobs: [],
    activeJob: null,
    selection: null,
    radar: [],            // orchestrator's ranked candidates (telemetry)
    radarFilter: 'ALL',   // style filter for the scanner radar
    pending: [],          // working orders from /api/pending_orders
    chart: null,          // {host, chart, candles, volume, lines, tradeLines}
    chartCandles: [],     // bars currently drawn — source for level maths
    chartLevels: null,    // {r1,r2,s1,s2}; null when no swing pivot exists
    chartSymbol: null,    // symbol the drawn series belongs to
    chartTimeframe: null, // timeframe the drawn series belongs to
    chartPainted: false,  // false until the first setData() has run
    showLevels: true,     // support/resistance + trade overlays on the chart
    lastValues: {}        // for flash-on-change
  };

  var timers = {};

  /* ── DOM helpers ──────────────────────────────────────────────────────── */
  function $(id) { return document.getElementById(id); }

  function esc(value) {
    return String(value === null || value === undefined ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function setText(el, value) {
    if (!el) return;
    el.textContent = (value === null || value === undefined || value === '') ? '—' : String(value);
  }

  /* Set the explicit state of a container. `data-state` drives the stylesheet. */
  function setState(el, name, message, detail) {
    if (!el) return;
    el.setAttribute('data-state', name);
    if (message === undefined) return;
    el.innerHTML = '';
    var wrap = document.createElement('div');
    wrap.className = 'tt-state tt-state--' + name;
    var title = document.createElement('span');
    title.className = 'tt-state__title';
    title.textContent = message;
    wrap.appendChild(title);
    if (detail) {
      var d = document.createElement('span');
      d.textContent = detail;
      wrap.appendChild(d);
    }
    el.appendChild(wrap);
  }

  /* ── Formatters ───────────────────────────────────────────────────────── */
  function num(value, digits) {
    if (value === null || value === undefined || value === '' || isNaN(value)) return '—';
    var v = Number(value);
    if (!isFinite(v)) return '—';
    return v.toLocaleString('en-US', {
      minimumFractionDigits: digits === undefined ? 2 : digits,
      maximumFractionDigits: digits === undefined ? 2 : digits
    });
  }

  /* Price precision is a property of the instrument, not of the number's
     magnitude. A JPY pair quotes to 3 decimals and gold to 2 whatever the
     value; inferring from size alone renders USDJPY as 151.23400 and disagrees
     with the backend, which resolves precision per symbol. */
  var PRICE_DIGITS = [
    [/^(XAU|GOLD)/, 2],
    [/^(XAG|SILVER)/, 2],
    [/^(BTC|ETH)/, 2],
    [/(NAS100|US30|US500|US100|GER40|UK40|UK100|JP225|HK50|SPX|DAX)/, 1],
    [/JPY$/, 3]
  ];

  function priceDigits(symbol, value) {
    var s = String(symbol || '').toUpperCase();
    for (var i = 0; i < PRICE_DIGITS.length; i++) {
      if (PRICE_DIGITS[i][0].test(s)) return PRICE_DIGITS[i][1];
    }
    var v = Math.abs(Number(value));
    if (!isFinite(v) || v === 0) return 5;
    if (v >= 100) return 2;
    if (v >= 10) return 3;
    return 5;
  }

  /* A price of 0 means "not set" for a stop or target, so it renders as a dash
     rather than as a real level at zero. */
  function formatPrice(value, symbol) {
    if (value === null || value === undefined || value === '') return '—';
    var v = Number(value);
    if (!isFinite(v) || v === 0) return '—';
    return num(v, priceDigits(symbol, v));
  }

  function pct(value, digits) {
    if (value === null || value === undefined || isNaN(value)) return '—';
    return num(Number(value) * (Math.abs(Number(value)) <= 1.5 ? 100 : 1), digits === undefined ? 1 : digits) + '%';
  }

  function signClass(value) {
    var v = Number(value);
    if (!isFinite(v) || v === 0) return 'tt-flat';
    return v > 0 ? 'tt-up' : 'tt-down';
  }

  function clockTime(value) {
    if (!value) return '—';
    var d = value instanceof Date ? value : new Date(value);
    if (isNaN(d.getTime())) return String(value);
    return d.toLocaleTimeString('en-GB', { hour12: false });
  }

  /* Relative age of a timestamp, in seconds. */
  function ageSeconds(ts) {
    if (!ts) return null;
    var d = new Date(String(ts).replace(' ', 'T'));
    if (isNaN(d.getTime())) return null;
    return Math.max(0, Math.round((Date.now() - d.getTime()) / 1000));
  }

  function agoText(seconds) {
    if (seconds === null) return '—';
    if (seconds < 60) return seconds + 's ago';
    if (seconds < 3600) return Math.round(seconds / 60) + 'm ago';
    return Math.round(seconds / 3600) + 'h ago';
  }

  /* Flash a cell when its value changes, so movement is visible without
     reading every digit. Removed immediately afterwards. */
  function flash(el, value) {
    if (!el) return;
    var key = el.id || '';
    var prev = state.lastValues[key];
    state.lastValues[key] = value;
    if (prev === undefined || prev === null || value === null) return;
    if (Number(prev) === Number(value)) return;
    if (!isFinite(Number(prev)) || !isFinite(Number(value))) return;
    var cls = Number(value) > Number(prev) ? 'tt-flash-up' : 'tt-flash-down';
    el.classList.remove('tt-flash-up', 'tt-flash-down');
    void el.offsetWidth;               // restart the animation
    el.classList.add(cls);
    setTimeout(function () { el.classList.remove(cls); }, 700);
  }

  /* ── Transport ────────────────────────────────────────────────────────── */
  function authHeaders() {
    var h = { 'Content-Type': 'application/json' };
    try {
      var token = (typeof window.getAuthToken === 'function' && window.getAuthToken()) || null;
      if (token) h['Authorization'] = 'Bearer ' + token;
    } catch (e) { /* auth is optional for public reads */ }
    return h;
  }

  /* fetch with a hard ceiling. Returns {ok, status, data, error}. */
  function apiRequest(path, options) {
    options = options || {};
    var budget = options.timeout || TIMEOUT.normal;
    var controller = (typeof AbortController !== 'undefined') ? new AbortController() : null;
    var timer = null;

    if (controller) {
      timer = setTimeout(function () { controller.abort(); }, budget);
    }

    return fetch(path, {
      method: options.method || 'GET',
      headers: authHeaders(),
      body: options.body ? JSON.stringify(options.body) : undefined,
      signal: controller ? controller.signal : undefined,
      cache: 'no-store'
    }).then(function (resp) {
      if (timer) clearTimeout(timer);
      return resp.text().then(function (text) {
        var data = null;
        try { data = text ? JSON.parse(text) : null; } catch (e) { data = null; }
        return { ok: resp.ok, status: resp.status, data: data, raw: text };
      });
    }).catch(function (err) {
      if (timer) clearTimeout(timer);
      var aborted = err && (err.name === 'AbortError' || /abort/i.test(String(err.message || '')));
      return {
        ok: false,
        status: 0,
        data: null,
        error: aborted
          ? 'Request timed out after ' + Math.round(budget / 1000) + 's'
          : String((err && err.message) || err)
      };
    });
  }

  function apiGet(path, timeout) {
    return apiRequest(path, { timeout: timeout });
  }

  function apiPost(path, body, timeout) {
    return apiRequest(path, { method: 'POST', body: body || {}, timeout: timeout });
  }

  /* ── Toasts ───────────────────────────────────────────────────────────── */
  function toast(message, kind) {
    var host = $('toasts');
    if (!host) return;
    var el = document.createElement('div');
    el.className = 'tt-toast tt-toast--' + (kind || 'ok');
    el.setAttribute('role', kind === 'error' ? 'alert' : 'status');
    el.textContent = String(message);
    host.appendChild(el);
    setTimeout(function () {
      el.style.transition = 'opacity 200ms';
      el.style.opacity = '0';
      setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, 220);
    }, kind === 'error' ? 7000 : 4000);
  }

  /* ── View / pane switching ────────────────────────────────────────────── */
  function setView(view) {
    if (VIEWS.indexOf(view) === -1) view = 'trade';
    state.view = view;
    document.body.setAttribute('data-view', view);

    Array.prototype.forEach.call(document.querySelectorAll('[data-view-btn]'), function (btn) {
      btn.setAttribute('aria-selected', String(btn.getAttribute('data-view-btn') === view));
    });
    Array.prototype.forEach.call(document.querySelectorAll('[data-view-panel]'), function (panel) {
      var active = panel.getAttribute('data-view-panel') === view;
      panel.setAttribute('data-active', String(active));
      if (active) panel.removeAttribute('hidden');
      else panel.setAttribute('hidden', '');
    });

    if (view === 'analytics') { loadAnalytics(); }
    if (view === 'backtest') { loadBacktestMeta(); loadJobs(); }
    if (view === 'trade') {
      loadChart();
      // The chart container was display:none while another view was active, so
      // its canvas kept whatever width it had when it was hidden. Resize once
      // the layout has flushed rather than measuring a zero-width element.
      if (typeof requestAnimationFrame === 'function') requestAnimationFrame(resizeChart);
    }
  }

  function setPane(pane) {
    if (PANES.indexOf(pane) === -1) pane = 'watchlist';
    state.pane = pane;
    document.body.setAttribute('data-pane', pane);
    Array.prototype.forEach.call(document.querySelectorAll('[data-pane-btn]'), function (btn) {
      btn.setAttribute('aria-selected', String(btn.getAttribute('data-pane-btn') === pane));
    });
    if (pane === 'chart') {
      loadChart();
      if (typeof requestAnimationFrame === 'function') requestAnimationFrame(resizeChart);
    }
  }

  /* ── Account strip ────────────────────────────────────────────────────── */
  function renderAccount() {
    var acc = state.account;
    var eq = $('acc-equity'), pf = $('acc-profit'),
        mg = $('acc-margin'), rk = $('acc-risk');

    if (!acc) {
      [eq, pf, mg, rk].forEach(function (el) {
        if (el) { el.textContent = '—'; el.setAttribute('data-state', 'empty'); }
      });
      return;
    }

    setText(eq, num(acc.equity, 2) + ' ' + (acc.currency || ''));
    flash(eq, acc.equity);

    var profit = Number(acc.profit || 0);
    setText(pf, (profit > 0 ? '+' : '') + num(profit, 2));
    if (pf) pf.className = 'tt-metric__value tt-num ' + signClass(profit);
    flash(pf, profit);

    setText(mg, num(acc.free_margin, 2));

    // Margin level is only meaningful with open exposure; 0 means "no positions".
    var ml = Number(acc.margin_level || 0);
    setText(rk, ml > 0 ? num(ml, 1) + '%' : 'flat');
    if (rk) rk.className = 'tt-metric__value ' + (ml > 0 && ml < 200 ? 'tt-down' : 'tt-flat');

    [eq, pf, mg, rk].forEach(function (el) { if (el) el.setAttribute('data-state', 'ready'); });
  }

  /* ── Watchlist ────────────────────────────────────────────────────────── */
  function symbolList() {
    return Object.keys(state.decisions).sort();
  }

  function renderWatchlist() {
    var body = $('watch-body');
    if (!body) return;
    var symbols = symbolList();

    if (!symbols.length) {
      setState(body, 'empty', 'No instruments reporting',
        'Telemetry has not published any decisions yet.');
      setText($('watch-count'), '0');
      return;
    }

    body.removeAttribute('data-state');
    body.innerHTML = '';
    var frag = document.createDocumentFragment();

    symbols.forEach(function (sym) {
      var d = state.decisions[sym] || {};
      var ms = state.marketStatuses[sym] || {};
      var bias = String(d.bias || 'HOLD').toUpperCase();
      var conf = Number(d.model_confidence);
      var rr = Number(d.risk_reward_ratio);

      var tr = document.createElement('tr');
      tr.setAttribute('data-symbol', sym);
      tr.setAttribute('data-clickable', 'true');
      tr.setAttribute('tabindex', '0');
      if (sym === state.symbol) tr.setAttribute('aria-selected', 'true');

      var dirCls = bias === 'BUY' ? 'tt-dir--buy' : (bias === 'SELL' ? 'tt-dir--sell' : 'tt-dir--flat');

      tr.innerHTML =
        '<td><span class="tt-symbol">' + esc(sym) + '</span></td>' +
        '<td><span class="tt-dir ' + dirCls + '">' + esc(bias) + '</span></td>' +
        '<td class="tt-num">' + (isFinite(conf) ? num(conf * 100, 0) + '%' : '—') + '</td>' +
        '<td class="tt-num">' + formatPrice(d.entry_price, sym) + '</td>' +
        '<td class="tt-num">' + (isFinite(rr) ? num(rr, 2) + 'R' : '—') + '</td>' +
        '<td><span class="tt-chip ' + sessionChip(ms.status) + '">' + esc(ms.status || '—') + '</span></td>';

      tr.addEventListener('click', function () { selectSymbol(sym); });
      tr.addEventListener('keydown', function (ev) {
        if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); selectSymbol(sym); }
      });
      frag.appendChild(tr);
    });

    body.appendChild(frag);
    setText($('watch-count'), String(symbols.length));
  }

  function sessionChip(status) {
    var s = String(status || '').toUpperCase();
    if (s === 'OPEN') return 'tt-chip--buy';
    if (s === 'CLOSED') return 'tt-chip--none';
    return 'tt-chip--low';
  }

  function selectSymbol(sym) {
    state.symbol = sym;
    Array.prototype.forEach.call(document.querySelectorAll('#watch-body tr'), function (tr) {
      tr.setAttribute('aria-selected', String(tr.getAttribute('data-symbol') === sym));
    });
    var input = $('ticket-symbol');
    if (input) input.value = sym;
    var title = $('chart-title');
    if (title) title.textContent = sym + ' · ' + state.timeframe;
    renderReasoning();
    prefillTicket();
    loadChart();
    loadSelection();
  }

  /* ── Auto-selection ───────────────────────────────────────────────────── */
  function loadSelection() {
    var host = $('selection-body');
    apiGet('/api/intelligence/auto-selection?refresh=1', TIMEOUT.slow).then(function (res) {
      if (!res.ok || !res.data || res.data.status !== 'OK') {
        state.selection = null;
        setText($('selection-count'), '0');
        setText($('selection-tier'), '—');
        var reason = (res.data && (res.data.error || res.data.status)) ||
                     res.error || ('HTTP ' + res.status);
        // A 503 here means the engine is not attached — an honest, expected state.
        setState(host, res.status === 503 ? 'stale' : 'error',
          res.status === 503 ? 'Selection engine not attached' : 'Selection unavailable',
          String(reason).slice(0, 160));
        return;
      }
      state.selection = res.data;
      renderSelection();
    });
  }

  function renderSelection() {
    var host = $('selection-body');
    var data = state.selection || {};
    var decisions = data.decisions || [];
    var tradeable = decisions.filter(function (d) { return d.is_tradeable; });

    setText($('selection-count'), String(tradeable.length));
    var best = tradeable[0];
    setText($('selection-tier'), best ? (best.confidence_tier || '—') : 'none');
    var tierEl = $('selection-tier');
    if (tierEl) {
      var t = best ? String(best.confidence_tier || '').toLowerCase() : 'none';
      tierEl.className = 'tt-chip tt-chip--' + (['high', 'medium', 'low'].indexOf(t) >= 0 ? t : 'none');
    }

    if (!tradeable.length) {
      setState(host, 'empty', 'No consensus setup',
        decisions.length
          ? decisions.length + ' symbol(s) evaluated, none reached cross-style agreement.'
          : 'No candidates were produced by the scan.');
      return;
    }

    host.removeAttribute('data-state');
    host.innerHTML = '';
    tradeable.slice(0, 8).forEach(function (d) {
      var card = document.createElement('div');
      card.className = 'tt-pad';
      card.style.borderBottom = '1px solid var(--hm-border-subtle)';
      card.style.cursor = 'pointer';

      var dirCls = String(d.direction).toUpperCase() === 'BUY' ? 'tt-dir--buy' : 'tt-dir--sell';
      var tier = String(d.confidence_tier || '').toLowerCase();
      var tierCls = ['high', 'medium', 'low'].indexOf(tier) >= 0 ? tier : 'none';

      card.innerHTML =
        '<div class="tt-row">' +
          '<span class="tt-symbol">' + esc(d.symbol) + '</span>' +
          '<span class="tt-dir ' + dirCls + '">' + esc(d.direction) + '</span>' +
          '<span class="tt-chip tt-chip--' + tierCls + '">' + esc(d.confidence_tier || '—') + '</span>' +
          '<span class="tt-rail__spacer"></span>' +
          '<span class="tt-num">' + num(d.consensus_score, 1) + '</span>' +
        '</div>' +
        '<div class="tt-row" style="margin-top:4px">' +
          '<span class="tt-hint">' + esc(d.rationale || '') + '</span>' +
        '</div>';

      card.addEventListener('click', function () { selectSymbol(d.symbol); });
      host.appendChild(card);
    });
  }

  /* ── Scanner radar ────────────────────────────────────────────────────── */
  /* Rows come from telemetry's radar_opportunities — the orchestrator's ranked
     candidate list. A candidate the engine considers actionable is marked, so
     the list does not imply that everything in it is tradeable. */
  function renderRadar() {
    var host = $('radar-body');
    if (!host) return;

    var all = state.radar || [];
    var filter = String(state.radarFilter || 'ALL').toUpperCase();
    var rows = all.filter(function (o) {
      if (filter === 'ALL') return true;
      var style = String(o.trade_style || '').toUpperCase();
      if (filter === 'DAY_TRADING') return style === 'DAY_TRADING' || style === 'DAY' || style === 'INTRADAY';
      return style === filter;
    });

    setText($('radar-count'), String(rows.length));

    if (!rows.length) {
      setState(host, 'empty',
        all.length ? 'No setups in this style' : 'No scan published',
        all.length
          ? all.length + ' candidate(s) scanned, none matching the filter.'
          : 'The radar has not published a scan yet.');
      return;
    }

    host.removeAttribute('data-state');
    host.innerHTML = '';
    var frag = document.createDocumentFragment();

    rows.slice(0, 20).forEach(function (o) {
      var sym = o.symbol || '';
      var actionable = o.is_actionable === true;
      var label = String(o.status_label || o.action || o.decision || '—').toUpperCase();
      var win = Number(o.win_prob !== undefined && o.win_prob !== null ? o.win_prob : o.score);
      var ev = Number(o.ev);

      var card = document.createElement('div');
      card.className = 'tt-radar' + (actionable ? ' tt-radar--live' : '');
      card.setAttribute('data-clickable', 'true');
      card.setAttribute('tabindex', '0');
      card.innerHTML =
        '<div class="tt-radar__top">' +
          '<span class="tt-symbol">' + esc(sym) + '</span>' +
          '<span class="tt-radar__action' + (actionable ? ' is-live' : '') + '">' + esc(label) + '</span>' +
          '<span class="tt-rail__spacer"></span>' +
          '<span class="tt-chip tt-chip--muted">' + esc(o.trade_style || '—') + '</span>' +
        '</div>' +
        '<div class="tt-radar__nums">' +
          '<span>Entry <b>' + formatPrice(o.entry_price, sym) + '</b></span>' +
          '<span>SL <b>' + formatPrice(o.stop_loss, sym) + '</b></span>' +
          '<span>TP <b>' + formatPrice(o.take_profit, sym) + '</b></span>' +
          '<span>R:R <b>' + num(o.risk_reward_ratio, 2) + '</b></span>' +
          '<span>Win <b>' + (isFinite(win) ? num(win, 0) + '%' : '—') + '</b></span>' +
          '<span>EV <b class="' + signClass(ev) + '">' + (isFinite(ev) && ev > 0 ? '+' : '') + num(ev, 2) + 'R</b></span>' +
        '</div>' +
        '<div class="tt-radar__meta">' +
          esc(o.regime || '—') + ' · ' + esc(o.strategy || '—') +
          (o.confluence_tier ? ' · ' + esc(o.confluence_tier) : '') +
          (o.setup_grade ? ' · ' + esc(o.setup_grade) : '') +
        '</div>';

      card.addEventListener('click', function () { selectSymbol(sym); });
      card.addEventListener('keydown', function (ev) {
        if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); selectSymbol(sym); }
      });
      frag.appendChild(card);
    });

    host.appendChild(frag);
  }

  /* ── Pending orders ───────────────────────────────────────────────────── */
  /* MT5 reports the order type as a numeric enum; rendering "2" in a column
     headed Type tells the user nothing. */
  var PENDING_TYPES = {
    0: 'BUY LIMIT', 1: 'SELL LIMIT', 2: 'BUY STOP',
    3: 'SELL STOP', 4: 'BUY STOP LIMIT', 5: 'SELL STOP LIMIT'
  };

  function pendingTypeName(t) {
    var s = String(t === null || t === undefined ? '' : t);
    if (/^\d+$/.test(s) && PENDING_TYPES[Number(s)]) return PENDING_TYPES[Number(s)];
    return s || '—';
  }

  function loadPendingOrders() {
    var body = $('pending-body');
    apiGet('/api/pending_orders', TIMEOUT.normal).then(function (res) {
      if (!res.ok) {
        setText($('pending-count'), '0');
        setState(body, 'stale', 'Pending orders unavailable',
          String(res.error || ('HTTP ' + res.status)).slice(0, 120));
        return;
      }
      // The route returns a bare array; tolerate a wrapped one as well.
      var list = Array.isArray(res.data) ? res.data : ((res.data && res.data.orders) || []);
      state.pending = list;
      renderPendingOrders();
    });
  }

  function renderPendingOrders() {
    var body = $('pending-body');
    var list = state.pending || [];
    setText($('pending-count'), String(list.length));

    if (!list.length) {
      setState(body, 'empty', 'No working orders', null);
      return;
    }

    body.removeAttribute('data-state');
    body.innerHTML = list.map(function (o) {
      var sym = o.symbol || '';
      return '<tr>' +
        '<td><span class="tt-symbol">' + esc(sym) + '</span></td>' +
        '<td class="tt-muted">' + esc(pendingTypeName(o.type)) + '</td>' +
        '<td class="tt-num">' + num(o.volume, 2) + '</td>' +
        '<td class="tt-num">' + formatPrice(o.price, sym) + '</td>' +
        '<td class="tt-num">' + (Number(o.sl) > 0 ? formatPrice(o.sl, sym) : '—') + '</td>' +
        '<td class="tt-num">' + (Number(o.tp) > 0 ? formatPrice(o.tp, sym) : '—') + '</td>' +
        '</tr>';
    }).join('');
  }

  /* ── Positions ────────────────────────────────────────────────────────── */
  function renderPositions() {
    var body = $('pos-body');
    var positions = state.positions || [];
    setText($('pos-count'), String(positions.length));

    if (!positions.length) {
      setState(body, 'empty', 'No open positions', null);
      setText($('pos-total'), '—');
      // The chart's trade overlays belong to this list, so a flat book must
      // clear them rather than leaving stale entry and stop lines on screen.
      refreshChartDecorations();
      return;
    }

    body.removeAttribute('data-state');
    body.innerHTML = '';
    var total = 0;
    var frag = document.createDocumentFragment();

    positions.forEach(function (p) {
      var profit = Number(p.profit || 0);
      total += profit;
      var tr = document.createElement('tr');
      var side = String(p.type || p.side || '').toUpperCase();
      var dirCls = /BUY|LONG/.test(side) ? 'tt-dir--buy' : 'tt-dir--sell';
      tr.innerHTML =
        '<td><span class="tt-symbol">' + esc(p.symbol) + '</span></td>' +
        '<td><span class="tt-dir ' + dirCls + '">' + esc(side || '—') + '</span></td>' +
        '<td class="tt-num">' + num(p.volume, 2) + '</td>' +
        '<td class="tt-num">' + formatPrice(p.open_price, p.symbol) + '</td>' +
        '<td class="tt-num">' + formatPrice(p.current_price, p.symbol) + '</td>' +
        '<td class="tt-num ' + signClass(profit) + '">' + (profit > 0 ? '+' : '') + num(profit, 2) + '</td>';
      frag.appendChild(tr);
    });

    body.appendChild(frag);
    var tot = $('pos-total');
    setText(tot, (total > 0 ? '+' : '') + num(total, 2));
    if (tot) tot.className = 'tt-num ' + signClass(total);

    // Overlays follow the position list, not only the candle poll, so a fill or
    // a close redraws immediately instead of at the next chart refresh.
    refreshChartDecorations();
  }

  /* ── Reasoning ("why this trade") ─────────────────────────────────────── */
  function renderReasoning() {
    var host = $('reason-body');
    var sym = state.symbol;
    var d = sym ? state.decisions[sym] : null;

    if (!d) {
      setText($('reason-tier'), '—');
      setState(host, 'empty', 'No setup selected',
        'Pick an instrument to see the engine\u2019s reasoning.');
      return;
    }

    var tier = String(d.master_confluence_tier || '').toLowerCase();
    var tierCls = ['high', 'medium', 'low'].indexOf(tier) >= 0 ? tier : 'none';
    var chip = $('reason-tier');
    if (chip) {
      chip.className = 'tt-chip tt-chip--' + tierCls;
      chip.textContent = (d.master_confluence_tier || '—') + ' · ' + (d.master_confluence_score !== undefined ? d.master_confluence_score : '—');
    }

    var rows = [
      ['Decision', String(d.decision || '—') + ' · ' + String(d.bias || '—')],
      ['Strategy', d.strategy || '—'],
      ['Regime', (d.regime && d.regime.primary) || '—'],
      ['Regime conf', d.regime && d.regime.confidence !== undefined ? pct(d.regime.confidence, 0) : '—'],
      ['Entry', d.entry_price !== undefined ? num(d.entry_price, 5) : '—'],
      ['Stop', d.stop_loss !== undefined ? num(d.stop_loss, 5) : '—'],
      ['Target', d.take_profit !== undefined ? num(d.take_profit, 5) : '—'],
      ['R:R', d.risk_reward_ratio !== undefined ? num(d.risk_reward_ratio, 2) + 'R' : '—'],
      ['Risk', d.calculated_risk_percent !== undefined ? num(d.calculated_risk_percent, 2) + '%' : '—'],
      ['Expected value', d.expected_value !== undefined ? num(d.expected_value, 2) : '—'],
      ['Model confidence', d.model_confidence !== undefined ? pct(d.model_confidence, 1) : '—'],
      ['Dissection', String(d.dissection_tier || '—') + ' (' + (d.dissection_score !== undefined ? d.dissection_score : '—') + ')'],
      ['Adversarial penalty', d.adversarial_penalty !== undefined ? num(d.adversarial_penalty, 1) : '—'],
      ['Gate policy', d.gate_policy_decision || '—'],
      ['Authorised', d.execution_authorized ? 'yes' : 'no'],
      ['Sample size', d.pattern_sample_size !== undefined ? d.pattern_sample_size : '—'],
      ['Updated', d.timestamp || '—']
    ];

    host.removeAttribute('data-state');
    host.innerHTML = '';
    var ul = document.createElement('ul');
    ul.className = 'tt-reasons';
    rows.forEach(function (pair) {
      var li = document.createElement('li');
      var k = document.createElement('span');
      k.className = 'tt-reasons__key';
      k.textContent = pair[0];
      var v = document.createElement('span');
      v.className = 'tt-reasons__val';
      v.textContent = pair[1];
      li.appendChild(k);
      li.appendChild(v);
      ul.appendChild(li);
    });
    host.appendChild(ul);

    // Probability split, when the engine published one.
    var probs = d.probabilities;
    if (probs && typeof probs === 'object') {
      var bar = document.createElement('div');
      bar.className = 'tt-pad';
      bar.innerHTML =
        '<div class="tt-row" style="justify-content:space-between">' +
          '<span class="tt-hint">BUY ' + pct(probs.buy, 0) + '</span>' +
          '<span class="tt-hint">NO TRADE ' + pct(probs.no_trade, 0) + '</span>' +
          '<span class="tt-hint">SELL ' + pct(probs.sell, 0) + '</span>' +
        '</div>';
      host.appendChild(bar);
    }
  }

  /* ── Ticket prefill ───────────────────────────────────────────────────── */
  function prefillTicket() {
    var d = state.symbol ? state.decisions[state.symbol] : null;
    var src = $('ticket-source');
    if (!d) {
      if (src) src.textContent = 'manual';
      return;
    }
    if (src) src.textContent = 'from setup';
    var set = function (id, val) {
      var el = $(id);
      if (el && val !== undefined && val !== null && val !== '') el.value = val;
    };
    set('ticket-price', d.entry_price);
    set('ticket-sl', d.stop_loss);
    set('ticket-tp', d.take_profit);

    var hint = $('ticket-hint');
    if (hint) {
      hint.textContent = d.execution_authorized
        ? 'Engine authorised this setup. Review before sending.'
        : 'Engine has NOT authorised this setup (' + (d.gate_policy_decision || 'blocked') + '). Manual entry only.';
      hint.className = d.execution_authorized ? 'tt-hint' : 'tt-hint tt-down';
    }
  }

  /* ── Chart ────────────────────────────────────────────────────────────────
     One lightweight-charts instance per session, rebuilt only when the
     container it was built into goes away.

     Four rules keep it honest and usable:

     1. LIVE TICKS UPDATE, THEY DO NOT RELOAD. setData() resets the viewport, so
        a trader who scrolled back to a level is thrown forward on every poll.
        The first paint uses setData(); later ticks call update() on the forming
        bar, which leaves scroll and zoom exactly where the user put them.

     2. LEVELS ARE DERIVED, NEVER INVENTED. Support and resistance come from
        swing pivots in the candles actually on screen. When the series is too
        short to contain a pivot on a side, that level is simply not drawn and
        the legend says so — a synthetic level reads as a real price and someone
        will trade against it.

     3. OVERLAYS ARE THE TRADE. Entry, stop and target are drawn as price lines
        whose axis labels carry the side, size and price, so the geometry of an
        open position is legible on the chart itself rather than only in the
        table beneath it.

     4. THE CHART IS THE SAME DATA AS THE TABLE. Candle precision is resolved
        per symbol by the same rule the backend uses, so a price read off the
        axis and the same price read off a row agree digit for digit.
     ────────────────────────────────────────────────────────────────────────── */

  var CHART_COLORS = {
    up: '#00f59b',
    down: '#ff3b5c',
    resistance1: '#ff2a5f',
    resistance2: '#f43f5e',
    support1: '#00f59b',
    support2: '#10b981',
    entryBuy: '#00d4ff',
    entrySell: '#c084fc',
    stopAtRisk: '#ff0055',
    stopLocked: '#fbbf24',
    target: '#00ff88'
  };

  /* Build the chart once. Returns null when the library or the container is
     missing, so every caller can bail rather than throw into a poll loop. */
  function ensureChart() {
    var host = $('chart');
    if (!host) return null;
    if (typeof LightweightCharts === 'undefined') return null;
    if (state.chart && state.chart.host === host && host.contains(state.chart.chart.chartElement())) {
      return state.chart;
    }

    host.innerHTML = '';
    var chart = LightweightCharts.createChart(host, {
      width: host.clientWidth || 600,
      height: host.clientHeight || 320,
      layout: {
        background: { color: 'transparent' },
        textColor: '#8494ab',
        fontSize: 11,
        fontFamily: "'JetBrains Mono', 'Roboto Mono', Consolas, monospace"
      },
      grid: {
        vertLines: { color: 'rgba(148,163,184,0.06)' },
        horzLines: { color: 'rgba(148,163,184,0.06)' }
      },
      rightPriceScale: {
        borderColor: 'rgba(148,163,184,0.15)',
        scaleMargins: { top: 0.08, bottom: 0.24 }
      },
      timeScale: {
        borderColor: 'rgba(148,163,184,0.15)',
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 4
      },
      crosshair: {
        mode: 1,
        vertLine: { color: 'rgba(56,189,248,0.5)', width: 1, style: 3, labelBackgroundColor: '#1a2438' },
        horzLine: { color: 'rgba(56,189,248,0.5)', width: 1, style: 3, labelBackgroundColor: '#1a2438' }
      },
      localization: { priceFormatter: function (p) { return p.toFixed(priceDigits(state.chartSymbol || state.symbol, p)); } }
    });

    // v4 exposes addCandlestickSeries(); v5 replaced it with addSeries(type).
    // Support both so a vendored-library bump does not silently blank the chart.
    var candleOpts = {
      upColor: CHART_COLORS.up, downColor: CHART_COLORS.down,
      borderUpColor: CHART_COLORS.up, borderDownColor: CHART_COLORS.down,
      wickUpColor: CHART_COLORS.up, wickDownColor: CHART_COLORS.down
    };
    var candleSeries = (typeof chart.addCandlestickSeries === 'function')
      ? chart.addCandlestickSeries(candleOpts)
      : chart.addSeries(LightweightCharts.CandlestickSeries, candleOpts);

    // Volume shares the price pane on its own overlay scale pinned to the
    // bottom fifth. lightweight-charts v4 has no pane API, and a second chart
    // instance would need its own time axis kept in sync by hand.
    var volumeSeries = null;
    if (typeof chart.addHistogramSeries === 'function') {
      volumeSeries = chart.addHistogramSeries({
        priceFormat: { type: 'volume' },
        priceScaleId: 'volume',
        lastValueVisible: false,
        priceLineVisible: false
      });
      chart.priceScale('volume').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    }

    state.chart = {
      host: host,
      chart: chart,
      candles: candleSeries,
      volume: volumeSeries,
      lines: [],
      tradeLines: []
    };

    // ResizeObserver is what the layout actually drives: the window listener
    // alone misses pane switches and view changes, which resize without a
    // window event and leave the canvas at its old width.
    if (typeof ResizeObserver !== 'undefined') {
      var ro = new ResizeObserver(function () {
        resizeChart();
      });
      ro.observe(host);
    }

    subscribeCrosshair(chart, candleSeries);
    return state.chart;
  }

  function resizeChart() {
    if (!state.chart) return;
    var host = state.chart.host;
    var w = host.clientWidth || 600;
    var h = host.clientHeight || 320;
    state.chart.chart.applyOptions({ width: w, height: h });
  }

  /* Crosshair readout. Beyond OHLC this names the level the bar is testing,
     which is the reason the levels are drawn at all. */
  function subscribeCrosshair(chart, candleSeries) {
    chart.subscribeCrosshairMove(function (param) {
      var tip = $('chart-tooltip');
      if (!tip) return;
      if (!param || !param.time || !param.seriesData || !param.point) {
        tip.hidden = true;
        return;
      }
      var bar = param.seriesData.get(candleSeries);
      if (!bar) { tip.hidden = true; return; }

      var sym = state.chartSymbol || state.symbol || '';
      var digits = priceDigits(sym, bar.close);
      var lv = state.chartLevels;

      var tag = 'Mid-range';
      var tagCls = ' tt-tip__tag--flat';
      if (lv) {
        if (lv.r1 !== null && bar.high >= lv.r1) { tag = 'Testing R1 resistance'; tagCls = ' tt-tip__tag--down'; }
        else if (lv.r2 !== null && bar.high >= lv.r2) { tag = 'Testing R2 resistance'; tagCls = ' tt-tip__tag--down'; }
        else if (lv.s1 !== null && bar.low <= lv.s1) { tag = 'Testing S1 support'; tagCls = ' tt-tip__tag--up'; }
        else if (lv.s2 !== null && bar.low <= lv.s2) { tag = 'Testing S2 support'; tagCls = ' tt-tip__tag--up'; }
      }

      var when = typeof param.time === 'number'
        ? new Date(param.time * 1000).toISOString().replace('T', ' ').slice(0, 16)
        : String(param.time);

      tip.innerHTML =
        '<div class="tt-tip__head"><span>' + esc(sym) + ' · ' + esc(state.timeframe) + '</span><span>' + esc(when) + '</span></div>' +
        '<div class="tt-tip__row"><span>Open</span><b>' + num(bar.open, digits) + '</b></div>' +
        '<div class="tt-tip__row"><span>High</span><b>' + num(bar.high, digits) + '</b></div>' +
        '<div class="tt-tip__row"><span>Low</span><b>' + num(bar.low, digits) + '</b></div>' +
        '<div class="tt-tip__row"><span>Close</span><b class="' + (bar.close >= bar.open ? 'tt-up' : 'tt-down') + '">' + num(bar.close, digits) + '</b></div>' +
        '<div class="tt-tip__tag' + tagCls + '">' + esc(tag) + '</div>';

      tip.hidden = false;
      // Flip the card to the left of the cursor when it would overflow the
      // panel, and clamp vertically so it never leaves the chart area.
      var host = state.chart ? state.chart.host : null;
      var hw = host ? host.clientWidth : 600;
      var x = param.point.x;
      var left = x > hw - 210 ? Math.max(4, x - 206) : x + 14;
      tip.style.left = left + 'px';
      tip.style.top = Math.max(4, param.point.y - 48) + 'px';
    });
  }

  /* Swing-pivot support and resistance.

     A pivot high is a bar whose high exceeds the two bars either side of it; a
     pivot low is the mirror. The two nearest pivots above the last close become
     R1 and R2, the two nearest below become S1 and S2 — the same definition the
     terminal used before the redesign, so a level a trader remembers still
     lands in the same place.

     Returns null rather than a synthesised level when the series is too short
     or has no pivot on either side. */
  function computeLevels(candles) {
    if (!candles || candles.length < 12) return null;

    var highs = [];
    var lows = [];
    for (var i = 2; i < candles.length - 2; i++) {
      var c = candles[i];
      if (c.high > candles[i - 1].high && c.high > candles[i - 2].high &&
          c.high > candles[i + 1].high && c.high > candles[i + 2].high) highs.push(c.high);
      if (c.low < candles[i - 1].low && c.low < candles[i - 2].low &&
          c.low < candles[i + 1].low && c.low < candles[i + 2].low) lows.push(c.low);
    }

    var last = candles[candles.length - 1].close;
    var above = highs.filter(function (h) { return h > last; }).sort(function (a, b) { return a - b; });
    var below = lows.filter(function (l) { return l < last; }).sort(function (a, b) { return b - a; });
    if (!above.length && !below.length) return null;

    return {
      r1: above.length > 0 ? above[0] : null,
      r2: above.length > 1 ? above[1] : null,
      s1: below.length > 0 ? below[0] : null,
      s2: below.length > 1 ? below[1] : null
    };
  }

  /* Remove one bucket of price lines. removePriceLine throws if the line was
     already detached (which happens when the series is reset), so each call is
     individually guarded rather than aborting the sweep. */
  function clearLines(bucket) {
    if (!state.chart || !state.chart[bucket]) return;
    var series = state.chart.candles;
    state.chart[bucket].forEach(function (line) {
      try { series.removePriceLine(line); } catch (e) { /* already detached */ }
    });
    state.chart[bucket] = [];
  }

  function drawLevels(sym, digits) {
    if (!state.chart) return;
    clearLines('lines');

    var legend = $('chart-legend');
    var lv = state.chartLevels;

    if (!lv || !state.showLevels) {
      if (legend) legend.innerHTML = '';
      return;
    }

    var defs = [
      ['r1', CHART_COLORS.resistance1, 2.5, 'Solid', 'R1'],
      ['r2', CHART_COLORS.resistance2, 1.5, 'Dashed', 'R2'],
      ['s1', CHART_COLORS.support1, 2.5, 'Solid', 'S1'],
      ['s2', CHART_COLORS.support2, 1.5, 'Dashed', 'S2']
    ];

    var chips = [];
    defs.forEach(function (d) {
      var price = lv[d[0]];
      if (price === null || price === undefined) return;
      state.chart.lines.push(state.chart.candles.createPriceLine({
        price: price,
        color: d[1],
        lineWidth: d[2],
        lineStyle: d[3] === 'Solid' ? LightweightCharts.LineStyle.Solid : LightweightCharts.LineStyle.Dashed,
        axisLabelVisible: true,
        title: d[4] + ': ' + num(price, digits)
      }));
      chips.push('<span class="tt-level tt-level--' + d[4].charAt(0).toLowerCase() + '">' +
                 '<i aria-hidden="true"></i>' + d[4] + ' <b>' + num(price, digits) + '</b></span>');
    });

    if (legend) {
      legend.innerHTML = chips.length
        ? chips.join('')
        : '<span class="tt-hint">No swing pivot in range</span>';
    }
  }

  /* Entry, stop and target for the open positions on this symbol.

     The stop is drawn amber and labelled "locked" once it has moved past entry,
     because a stop in profit is a different fact from a stop at risk and the
     colour is the fastest way to tell them apart. */
  function drawTradeOverlays(sym, digits) {
    if (!state.chart) return;
    clearLines('tradeLines');

    var hud = $('chart-hud');
    var mine = (state.positions || []).filter(function (p) {
      return String(p.symbol || '').toUpperCase() === String(sym || '').toUpperCase();
    });

    if (!state.showLevels || !mine.length) {
      if (hud) hud.hidden = true;
      return;
    }

    mine.forEach(function (pos) {
      var side = String(pos.type || pos.side || '').toUpperCase();
      var isBuy = side === 'BUY' || side === 'LONG';
      var entry = Number(pos.open_price || 0);
      var sl = Number(pos.sl || 0);
      var tp = Number(pos.tp || 0);
      var lots = Number(pos.volume || 0);
      var lotText = isFinite(lots) ? lots.toFixed(2) : '—';
      var series = state.chart.candles;

      if (entry > 0) {
        state.chart.tradeLines.push(series.createPriceLine({
          price: entry,
          color: isBuy ? CHART_COLORS.entryBuy : CHART_COLORS.entrySell,
          lineWidth: 2,
          lineStyle: LightweightCharts.LineStyle.Solid,
          axisLabelVisible: true,
          title: (isBuy ? 'BUY' : 'SELL') + ' ' + lotText + 'L @ ' + num(entry, digits)
        }));
      }
      if (sl > 0) {
        var locked = isBuy ? sl >= entry : sl <= entry;
        state.chart.tradeLines.push(series.createPriceLine({
          price: sl,
          color: locked ? CHART_COLORS.stopLocked : CHART_COLORS.stopAtRisk,
          lineWidth: 2,
          lineStyle: LightweightCharts.LineStyle.Dashed,
          axisLabelVisible: true,
          title: (locked ? 'SL locked' : 'SL') + ': ' + num(sl, digits)
        }));
      }
      if (tp > 0) {
        state.chart.tradeLines.push(series.createPriceLine({
          price: tp,
          color: CHART_COLORS.target,
          lineWidth: 2,
          lineStyle: LightweightCharts.LineStyle.Dashed,
          axisLabelVisible: true,
          title: 'TP: ' + num(tp, digits)
        }));
      }
    });

    if (!hud) return;
    var p = mine[0];
    var pSide = String(p.type || p.side || '').toUpperCase();
    var pBuy = pSide === 'BUY' || pSide === 'LONG';
    var pnl = Number(p.profit || 0);
    var lots2 = Number(p.volume || 0);

    hud.hidden = false;
    hud.className = 'tt-chart__hud ' + (pBuy ? 'tt-chart__hud--buy' : 'tt-chart__hud--sell');
    hud.innerHTML =
      '<div class="tt-chart__hud-top">' +
        '<span class="tt-chart__hud-side">' + esc(pSide || '—') + '</span>' +
        '<span class="tt-num">' + (isFinite(lots2) ? lots2.toFixed(2) : '—') + 'L</span>' +
        (p.ticket !== undefined && p.ticket !== null ? '<span class="tt-chart__hud-ticket">#' + esc(p.ticket) + '</span>' : '') +
      '</div>' +
      '<div class="tt-chart__hud-grid">' +
        '<span>Entry</span><b class="tt-num">' + formatPrice(p.open_price, p.symbol) + '</b>' +
        '<span>Stop</span><b class="tt-num">' + (Number(p.sl || 0) > 0 ? formatPrice(p.sl, p.symbol) : 'none') + '</b>' +
        '<span>Target</span><b class="tt-num">' + (Number(p.tp || 0) > 0 ? formatPrice(p.tp, p.symbol) : 'none') + '</b>' +
        '<span>P&amp;L</span><b class="tt-num ' + signClass(pnl) + '">' + (pnl > 0 ? '+' : '') + num(pnl, 2) + '</b>' +
      '</div>' +
      (mine.length > 1
        ? '<div class="tt-chart__hud-more">+' + (mine.length - 1) + ' more on ' + esc(sym) + '</div>'
        : '');
  }

  function updateChartHeader(sym, candles) {
    var title = $('chart-title');
    if (title) title.textContent = sym + ' · ' + state.timeframe;

    var el = $('chart-live-price');
    var last = candles[candles.length - 1];
    if (!el || !last) return;
    el.textContent = num(last.close, priceDigits(sym, last.close));
    el.className = 'tt-num tt-num--lg tt-chart__price ' + (last.close >= last.open ? 'tt-up' : 'tt-down');
    el.setAttribute('data-state', 'ready');
  }

  /* Redraw the decorations against the candles already on screen. Called after
     the position list changes so an overlay follows a fill or a close without
     waiting for the next candle poll. */
  function refreshChartDecorations() {
    if (!state.chart || !state.chartCandles || !state.chartCandles.length) return;
    var sym = state.chartSymbol || state.symbol;
    var last = state.chartCandles[state.chartCandles.length - 1];
    var digits = priceDigits(sym, last.close);
    drawLevels(sym, digits);
    drawTradeOverlays(sym, digits);
  }

  function renderChart(sym, candles) {
    var c = ensureChart();
    if (!c) return;

    // De-duplicate by timestamp and sort ascending: the library rejects a
    // series that is out of order or repeats a time.
    var seen = {};
    var bars = [];
    var vols = [];
    candles.forEach(function (raw) {
      var t = Number(raw.time);
      if (!isFinite(t) || seen[t]) return;
      var o = Number(raw.open), h = Number(raw.high), l = Number(raw.low), cl = Number(raw.close);
      if (!isFinite(o) || !isFinite(h) || !isFinite(l) || !isFinite(cl)) return;
      seen[t] = true;
      bars.push({ time: t, open: o, high: h, low: l, close: cl });
      vols.push({
        time: t,
        value: Number(raw.volume || 0),
        color: cl >= o ? 'rgba(0,245,155,0.30)' : 'rgba(255,59,92,0.30)'
      });
    });
    if (!bars.length) return;

    var order = function (a, b) { return a.time - b.time; };
    bars.sort(order);
    vols.sort(order);

    var digits = priceDigits(sym, bars[bars.length - 1].close);
    var firstPaint = (state.chartSymbol !== sym) ||
                     (state.chartTimeframe !== state.timeframe) ||
                     !state.chartPainted;

    c.candles.applyOptions({
      priceFormat: { type: 'price', precision: digits, minMove: Math.pow(10, -digits) }
    });

    if (firstPaint) {
      c.candles.setData(bars);
      if (c.volume) c.volume.setData(vols);
      c.chart.timeScale().fitContent();
      state.chartPainted = true;
    } else {
      // Update the forming bar only. setData() here would reset the viewport on
      // every poll and throw the user out of wherever they had scrolled to.
      try {
        c.candles.update(bars[bars.length - 1]);
        if (c.volume && vols.length) c.volume.update(vols[vols.length - 1]);
      } catch (e) {
        c.candles.setData(bars);
        if (c.volume) c.volume.setData(vols);
      }
    }

    state.chartSymbol = sym;
    state.chartTimeframe = state.timeframe;
    state.chartCandles = bars;
    state.chartLevels = computeLevels(bars);

    drawLevels(sym, digits);
    drawTradeOverlays(sym, digits);
    updateChartHeader(sym, bars);
  }

  function loadChart() {
    var sym = state.symbol;
    var overlay = $('chart-overlay');
    if (!sym) {
      if (overlay) setState(overlay, 'empty', 'No instrument selected', null);
      return;
    }

    // The query key is `tf`, not `timeframe`. console.js and terminal.js both
    // send `tf`; this call sent `timeframe`, which the handler never read, so
    // the selector silently returned H1 whatever the user picked.
    apiGet('/api/candles?symbol=' + encodeURIComponent(sym) +
           '&tf=' + encodeURIComponent(state.timeframe), TIMEOUT.normal)
      .then(function (res) {
        var candles = (res.data && res.data.candles) || [];
        if (!res.ok || !candles.length) {
          if (overlay) {
            setState(overlay, res.ok ? 'empty' : 'error',
              res.ok ? 'No candles for ' + sym : 'Chart unavailable',
              res.ok ? 'The feed returned an empty series.' : (res.error || ('HTTP ' + res.status)));
          }
          return;
        }
        if (typeof LightweightCharts === 'undefined') {
          if (overlay) setState(overlay, 'error', 'Chart library unavailable', 'lightweight-charts did not load.');
          return;
        }
        if (overlay) overlay.innerHTML = '';
        renderChart(sym, candles);
      });
  }

  /* ── Status bar ───────────────────────────────────────────────────────── */
  function renderStatus() {
    var services = state.services || {};
    var feed = services.DATA_FEED || '—';
    var mt5 = services.MT5 || '—';

    setText($('status-feed'), mt5 === 'CONNECTED' ? 'live' : String(feed).toLowerCase());

    var age = state.telemetryAt ? Math.round((Date.now() - state.telemetryAt) / 1000) : null;
    setText($('status-tick'), age === null ? '—' : agoText(age));
    setText($('status-symbols'), String(symbolList().length));
    setText($('status-positions'), String((state.positions || []).length));

    var conn = $('conn-chip');
    if (conn) {
      var online = mt5 === 'CONNECTED';
      conn.className = 'tt-chip ' + (online ? 'tt-chip--buy' : 'tt-chip--none');
      conn.textContent = online ? 'broker online' : 'broker offline';
    }

    setText($('exec-mode'), (state.executionMode || '—') + (state.safeMode ? ' · SAFE' : ''));

    // Session state, derived from the symbol the user is looking at.
    var ms = state.symbol ? (state.marketStatuses[state.symbol] || {}) : {};
    var sessionEl = $('session-state');
    if (sessionEl) {
      var st = String(ms.status || '').toUpperCase();
      sessionEl.setAttribute('data-state', st === 'OPEN' ? 'open' : (st === 'CLOSED' ? 'closed' : 'pre'));
      setText($('session-label'), ms.status ? ms.status + (ms.countdown_formatted ? ' · ' + ms.countdown_formatted : '') : '—');
    }

    var ver = $('status-version');
    if (ver && window.HMUI && window.HMUI.version) setText(ver, 'v' + window.HMUI.version);
  }

  /* ── Telemetry ────────────────────────────────────────────────────────── */
  function loadTelemetry() {
    var started = Date.now();
    return apiGet('/api/telemetry_state', TIMEOUT.fast).then(function (res) {
      var latency = $('status-latency');
      if (latency) setText(latency, (Date.now() - started) + ' ms');

      if (!res.ok || !res.data) {
        setText($('status-feed'), 'unreachable');
        if (latency) latency.className = 'tt-status__item';
        return;
      }

      var d = res.data;
      state.account = d.account || null;
      state.decisions = d.latest_decisions || {};
      state.marketStatuses = d.market_statuses || {};
      state.positions = d.positions || [];
      state.services = d.services || {};
      state.radar = d.radar_opportunities || [];
      state.executionMode = d.execution_mode;
      state.safeMode = d.safe_mode;
      state.telemetryAt = Date.now();
      state.serverTimestamp = d.timestamp;

      if (!state.symbol) {
        var syms = symbolList();
        if (syms.length) state.symbol = syms.indexOf('XAUUSD') >= 0 ? 'XAUUSD' : syms[0];
      }

      renderAccount();
      renderWatchlist();
      renderPositions();
      renderReasoning();
      renderRadar();
      renderStatus();
    });
  }

  /* ── Analytics ────────────────────────────────────────────────────────── */
  function loadAnalytics() {
    loadReliability();
    loadRegimePolicy();
    renderAnalyticsMetrics();
    renderHistory();
    renderRisk();
  }

  function renderAnalyticsMetrics() {
    var host = $('analytics-metrics');
    var acc = state.account;
    if (!host) return;
    if (!acc) {
      setState(host, 'empty', 'No account data', 'Telemetry has not published an account snapshot.');
      return;
    }
    var ml = Number(acc.margin_level || 0);
    var tiles = [
      ['Balance', num(acc.balance, 2), acc.currency || ''],
      ['Equity', num(acc.equity, 2), acc.currency || ''],
      ['Open P&L', (Number(acc.profit) > 0 ? '+' : '') + num(acc.profit, 2), signClass(acc.profit)],
      ['Free margin', num(acc.free_margin, 2), ''],
      ['Margin used', num(acc.margin, 2), ''],
      ['Margin level', ml > 0 ? num(ml, 1) + '%' : 'flat', ''],
      ['Leverage', '1:' + num(acc.leverage, 0), ''],
      ['Trade allowed', acc.trade_allowed ? 'yes' : 'no', acc.trade_allowed ? 'tt-up' : 'tt-down']
    ];
    host.removeAttribute('data-state');
    host.innerHTML = tiles.map(function (t) {
      return '<div class="tt-metric">' +
        '<span class="tt-metric__label">' + esc(t[0]) + '</span>' +
        '<span class="tt-metric__value ' + esc(t[2]) + '">' + esc(t[1]) + '</span>' +
        '</div>';
    }).join('');
  }

  function loadReliability() {
    var host = $('reliability-body');
    apiGet('/api/intelligence/reliability', TIMEOUT.normal).then(function (res) {
      if (!res.ok || !res.data || !res.data.styles) {
        setState(host, 'error', 'Reliability unavailable', res.error || ('HTTP ' + res.status));
        return;
      }
      state.reliability = res.data.styles || [];
      var src = $('reliability-source');
      if (src && res.data.model) setText(src, res.data.model.source_path || 'neutral weights');

      host.removeAttribute('data-state');
      host.innerHTML = '<table class="tt-table"><thead><tr>' +
        '<th scope="col">Mode</th><th scope="col" class="tt-num">Weight</th>' +
        '<th scope="col" class="tt-num">Trades</th><th scope="col" class="tt-num">Exp (R)</th>' +
        '<th scope="col" class="tt-num">PF</th><th scope="col">Trust</th>' +
        '</tr></thead><tbody>' +
        state.reliability.map(function (s) {
          var w = Number(s.weight || 0);
          var barPct = Math.max(0, Math.min(100, w * 100));
          var cls = w >= 0.5 ? 'tt-up' : 'tt-down';
          return '<tr>' +
            '<td><span class="tt-symbol">' + esc(s.style) + '</span></td>' +
            '<td class="tt-num ' + cls + '">' + num(w, 4) + '</td>' +
            '<td class="tt-num">' + (s.trades !== undefined ? s.trades : '—') + '</td>' +
            '<td class="tt-num ' + signClass(s.expectancy_r) + '">' + num(s.expectancy_r, 4) + '</td>' +
            '<td class="tt-num">' + num(s.profit_factor, 3) + '</td>' +
            '<td><div class="tt-gauge"><div class="tt-gauge__bar">' +
              '<div class="tt-gauge__fill" style="width:' + barPct + '%;background:' +
              (w >= 0.5 ? 'var(--hm-bull)' : 'var(--hm-bear)') + '"></div>' +
            '</div></div></td>' +
            '</tr>';
        }).join('') +
        '</tbody></table>';
    });
  }

  function renderHistory() {
    var body = $('hist-body');
    var rows = state.history || [];
    setText($('hist-count'), String(rows.length));
    if (!rows.length) {
      setState(body, 'empty', 'No closed trades', 'History is empty for this session.');
      return;
    }
    body.removeAttribute('data-state');
    body.innerHTML = rows.map(function (t) {
      var profit = Number(t.profit || 0);
      var side = String(t.type || t.side || '').toUpperCase();
      var dirCls = /BUY|LONG/.test(side) ? 'tt-dir--buy' : 'tt-dir--sell';
      return '<tr>' +
        '<td class="tt-muted">' + esc(t.time || t.close_time || '—') + '</td>' +
        '<td><span class="tt-symbol">' + esc(t.symbol) + '</span></td>' +
        '<td><span class="tt-dir ' + dirCls + '">' + esc(side) + '</span></td>' +
        '<td class="tt-num">' + num(t.volume, 2) + '</td>' +
        '<td class="tt-num ' + signClass(profit) + '">' + (profit > 0 ? '+' : '') + num(profit, 2) + '</td>' +
        '</tr>';
    }).join('');
  }

  function renderRisk() {
    var host = $('risk-metrics');
    var exp = $('exposure-body');
    var positions = state.positions || [];
    var acc = state.account;

    if (!host) return;
    if (!acc) {
      setState(host, 'empty', 'No account data', null);
      setState(exp, 'empty', 'No exposure data', null);
      return;
    }

    var gross = 0, net = 0;
    var bySymbol = {};
    positions.forEach(function (p) {
      var v = Number(p.volume || 0);
      gross += Math.abs(v);
      var side = String(p.type || p.side || '').toUpperCase();
      net += /BUY|LONG/.test(side) ? v : -v;
      bySymbol[p.symbol] = (bySymbol[p.symbol] || 0) + v;
    });

    host.removeAttribute('data-state');
    host.innerHTML = [
      ['Open positions', String(positions.length)],
      ['Gross volume', num(gross, 2)],
      ['Net volume', (net > 0 ? '+' : '') + num(net, 2)],
      ['Margin used', num(acc.margin, 2)]
    ].map(function (t) {
      return '<div class="tt-metric">' +
        '<span class="tt-metric__label">' + esc(t[0]) + '</span>' +
        '<span class="tt-metric__value">' + esc(t[1]) + '</span></div>';
    }).join('');

    var syms = Object.keys(bySymbol);
    if (!syms.length) {
      setState(exp, 'empty', 'No open exposure', null);
      return;
    }
    exp.removeAttribute('data-state');
    exp.innerHTML = '<table class="tt-table"><thead><tr>' +
      '<th scope="col">Symbol</th><th scope="col" class="tt-num">Volume</th>' +
      '</tr></thead><tbody>' +
      syms.sort().map(function (s) {
        return '<tr><td><span class="tt-symbol">' + esc(s) + '</span></td>' +
               '<td class="tt-num">' + num(bySymbol[s], 2) + '</td></tr>';
      }).join('') + '</tbody></table>';
  }

  /* ── Regime policy ────────────────────────────────────────────────────── */
  // Which geometry to run in which market condition, and which conditions the
  // optimiser has switched off. Read from the report the optimiser writes; the
  // dashboard never computes or assumes a policy of its own.
  function loadRegimePolicy() {
    var body = $('regime-policy-body');
    var host = $('regime-policy-metrics');
    var src = $('regime-policy-source');

    apiGet('/api/backtest/regime-policy', TIMEOUT.normal).then(function (res) {
      // 503 is not a failure here: it is the honest "the optimiser has not been
      // run" answer. Showing it as an error would train the operator to ignore
      // it, and showing an empty policy would imply the engine has nothing to
      // trade — a different and much more alarming claim.
      if (res.status === 503) {
        var reason = (res.data && res.data.error) || 'no regime policy report';
        setState(body, 'empty', 'No regime policy yet', reason);
        if (host) setState(host, 'empty', 'Not optimised', reason);
        if (src) src.textContent = 'never run';
        return;
      }
      if (!res.ok || !res.data || res.data.status !== 'OK') {
        setState(body, 'error', 'Regime policy unavailable',
          res.error || ('HTTP ' + res.status));
        if (host) setState(host, 'error', 'Unavailable', null);
        if (src) src.textContent = 'unavailable';
        return;
      }
      if (src) {
        src.textContent = (res.data.source_report || 'report') +
          (res.data.age_seconds != null ? ' · ' + agoText(res.data.age_seconds) : '');
      }
      renderRegimePolicy(res.data);
    });
  }

  function renderRegimePolicy(data) {
    var body = $('regime-policy-body');
    var host = $('regime-policy-metrics');
    if (!body) return;

    var policy = data.policy || {};
    var modes = Object.keys(policy).sort();
    var rows = [];
    var totalRegimes = 0, totalEnabled = 0, totalOwn = 0;

    modes.forEach(function (style) {
      var m = policy[style];
      if (!m || m.error) return;
      var regimes = m.regimes || {};
      Object.keys(regimes).sort().forEach(function (name) {
        var r = regimes[name] || {};
        totalRegimes += 1;
        if (r.enabled) totalEnabled += 1;
        if (r.uses_own_geometry) totalOwn += 1;

        var g = r.geometry || {};
        var geomText = g.tp_r != null ? ('tp ' + num(g.tp_r, 2)) : '—';
        // The quantile is a selectivity statement, so show it as one: "top 1%"
        // is what an operator needs, not "0.99".
        var q = Number(r.min_score_quantile);
        var select = (isFinite(q) && q > 0)
          ? 'top ' + num((1 - q) * 100, 0) + '%'
          : 'all setups';
        var base = r.baseline_expectancy_r;
        var expText = (base == null)
          ? '—'
          : (Number(base) > 0 ? '+' : '') + num(base, 4) + 'R';

        rows.push('<tr>' +
          '<td><span class="tt-chip tt-chip--muted">' + esc(style) + '</span></td>' +
          '<td><span class="tt-symbol">' + esc(name) + '</span></td>' +
          '<td><span class="tt-dir tt-dir--' + (r.enabled ? 'buy' : 'sell') + '">' +
            (r.enabled ? 'yes' : 'no') + '</span></td>' +
          '<td class="tt-num">' + esc(geomText) + ' ' +
            (r.uses_own_geometry
              ? '<span class="tt-chip tt-chip--high">own</span>'
              : '<span class="tt-chip tt-chip--muted">pooled</span>') + '</td>' +
          '<td class="tt-num">' + esc(select) + '</td>' +
          '<td class="tt-num">' +
            (r.baseline_trades == null ? '—' : num(r.baseline_trades, 0)) + '</td>' +
          '<td class="tt-num ' + signClass(base) + '">' + esc(expText) + '</td>' +
          '<td class="tt-hint">' + esc(r.basis || r.reason || '') + '</td>' +
          '</tr>');
      });
    });

    if (!rows.length) {
      setState(body, 'empty', 'Policy table is empty',
        'The report contains no regime rows.');
      if (host) setState(host, 'empty', 'No rows', null);
      return;
    }

    body.removeAttribute('data-state');
    body.innerHTML = rows.join('');

    if (host) {
      host.removeAttribute('data-state');
      host.innerHTML = [
        ['Modes', String(modes.length)],
        ['Conditions', String(totalRegimes)],
        ['Tradeable', String(totalEnabled)],
        ['Own geometry', String(totalOwn)],
        ['Objective', String(data.objective || '—')]
      ].map(function (t) {
        return '<div class="tt-metric">' +
          '<span class="tt-metric__label">' + esc(t[0]) + '</span>' +
          '<span class="tt-metric__value">' + esc(t[1]) + '</span></div>';
      }).join('');
    }
  }

  /* ── Backtest ─────────────────────────────────────────────────────────── */
  function loadBacktestMeta() {
    apiGet('/api/backtest/meta', TIMEOUT.normal).then(function (res) {
      if (!res.ok || !res.data) return;
      var meta = res.data;

      var eng = $('bt-engine');
      if (eng) {
        eng.textContent = meta.orchestrator_attached ? 'engine attached' : 'engine detached';
        eng.className = 'tt-chip ' + (meta.orchestrator_attached ? 'tt-chip--medium' : 'tt-chip--muted');
      }

      // Objectives and modes come from the server, not from this file.
      var objSel = $('bt-objective');
      if (objSel && objSel.options.length === 0 && meta.objectives) {
        meta.objectives.forEach(function (o) {
          var opt = document.createElement('option');
          opt.value = o; opt.textContent = o;
          objSel.appendChild(opt);
        });
      }

      var modeSel = $('bt-modes');
      if (modeSel && modeSel.options.length === 0 && meta.styles) {
        meta.styles.forEach(function (s) {
          var opt = document.createElement('option');
          opt.value = s; opt.textContent = s; opt.selected = true;
          modeSel.appendChild(opt);
        });
      }

      var grid = $('bt-grid');
      if (grid && !grid.children.length && meta.default_space) {
        Object.keys(meta.default_space).forEach(function (dim) {
          var values = meta.default_space[dim];
          var count = Array.isArray(values) ? values.length : 0;
          var label = document.createElement('label');
          label.className = 'tt-check';
          label.style.minWidth = '132px';
          label.innerHTML =
            '<input type="checkbox" data-grid-dim="' + esc(dim) + '" checked>' +
            '<span>' + esc(dim) + '</span>' +
            '<span class="tt-muted">(' + count + ')</span>';
          grid.appendChild(label);
        });
      }

      var hint = $('bt-universe-hint');
      if (hint && meta.defaults) {
        setText(hint, 'Defaults: min trades ' + (meta.defaults.min_trades !== undefined ? meta.defaults.min_trades : '—') +
          ', max DD ' + (meta.defaults.max_dd_r !== undefined ? meta.defaults.max_dd_r : '—') + 'R');
      }
    });
  }

  function collectSpec() {
    var modes = Array.prototype.filter.call($('bt-modes').options, function (o) { return o.selected; })
      .map(function (o) { return o.value; });
    var rawSymbols = ($('bt-symbols').value || '').split(/[\s,]+/).filter(Boolean);
    var grid = Array.prototype.filter.call(document.querySelectorAll('[data-grid-dim]'), function (c) { return c.checked; })
      .map(function (c) { return c.getAttribute('data-grid-dim'); });

    return {
      label: 'dashboard',
      objective: $('bt-objective').value,
      modes: modes,
      symbols: rawSymbols,
      min_trades: Number($('bt-mintrades').value || 30),
      max_dd_r: Number($('bt-maxdd').value || 40),
      passes: Number($('bt-passes').value || 3),
      max_evaluations: Number($('bt-evals').value || 400),
      walk_forward_split: Number($('bt-split').value || 70) / 100,
      grid_dimensions: grid
    };
  }

  function runBacktest(ev) {
    if (ev) ev.preventDefault();
    var btn = $('bt-run');
    if (btn) btn.setAttribute('aria-disabled', 'true');
    setText($('bt-status'), 'queued');
    var log = $('bt-log');
    if (log) log.textContent = 'Submitting…';

    apiPost('/api/backtest/run', collectSpec(), TIMEOUT.slow).then(function (res) {
      if (btn) btn.removeAttribute('aria-disabled');
      if (!res.ok || !res.data) {
        setText($('bt-status'), 'failed');
        if (log) log.textContent = 'Submit failed: ' + (res.error || ('HTTP ' + res.status));
        toast('Backtest could not be queued', 'error');
        return;
      }
      var jobId = res.data.job_id || (res.data.job && res.data.job.job_id);
      if (!jobId) {
        setText($('bt-status'), 'failed');
        if (log) log.textContent = 'Server returned no job id.';
        return;
      }
      state.activeJob = jobId;
      var cancel = $('bt-cancel');
      if (cancel) cancel.removeAttribute('aria-disabled');
      if (log) log.textContent = 'Job ' + jobId + ' queued.';
      toast('Backtest queued: ' + jobId);
      pollJob(jobId);
    });
  }

  function pollJob(jobId) {
    if (timers.job) clearTimeout(timers.job);
    apiGet('/api/backtest/jobs/' + encodeURIComponent(jobId), TIMEOUT.fast).then(function (res) {
      var job = (res.data && (res.data.job || res.data)) || null;
      if (!res.ok || !job) {
        setText($('bt-status'), 'unknown');
        return;
      }
      var status = String(job.status || '').toUpperCase();
      setText($('bt-status'), status.toLowerCase());
      var chip = $('bt-status');
      if (chip) {
        chip.className = 'tt-chip ' +
          (status === 'DONE' ? 'tt-chip--buy' :
           status === 'FAILED' ? 'tt-chip--sell' :
           status === 'RUNNING' ? 'tt-chip--medium' : 'tt-chip--none');
      }

      var progress = Number(job.progress || 0);
      var fill = $('bt-progress-fill');
      if (fill) fill.style.width = Math.max(0, Math.min(100, progress)) + '%';
      var bar = $('bt-progress');
      if (bar) bar.setAttribute('aria-valuenow', String(Math.round(progress)));

      var log = $('bt-log');
      if (log) {
        var lines = job.progress_lines || job.log || [];
        if (typeof lines === 'string') lines = [lines];
        log.textContent = lines.slice(-14).join('\n');
      }

      if (status === 'DONE') {
        loadJobResult(jobId);
        loadJobs();
        var cancel = $('bt-cancel');
        if (cancel) cancel.setAttribute('aria-disabled', 'true');
        return;
      }
      if (status === 'FAILED' || status === 'CANCELLED') {
        if (log) log.textContent = (job.error || status) + '\n' + (log.textContent || '');
        loadJobs();
        var cancel2 = $('bt-cancel');
        if (cancel2) cancel2.setAttribute('aria-disabled', 'true');
        return;
      }
      timers.job = setTimeout(function () { pollJob(jobId); }, POLL.jobs);
    });
  }

  function loadJobResult(jobId) {
    apiGet('/api/backtest/jobs/' + encodeURIComponent(jobId) + '/result', TIMEOUT.slow).then(function (res) {
      if (!res.ok || !res.data) return;
      renderBacktestResult(res.data);
    });
  }

  function renderBacktestResult(payload) {
    var host = $('bt-results');
    var report = payload.report || payload.result || payload;
    if (!host) return;

    var perSymbol = report.per_symbol || report.symbols || null;
    var meta = $('bt-result-meta');
    if (meta) {
      var bits = [];
      if (report.objective) bits.push('objective ' + report.objective);
      if (report.feasible !== undefined) bits.push(report.feasible ? 'feasible' : 'infeasible');
      if (report.symbols_positive !== undefined && report.symbols_total !== undefined) {
        bits.push(report.symbols_positive + '/' + report.symbols_total + ' positive');
      }
      meta.textContent = bits.join(' · ') || '—';
    }

    if (!perSymbol || !perSymbol.length) {
      setState(host, 'empty', 'No per-symbol results',
        'The job completed without a per-symbol breakdown.');
      return;
    }

    host.removeAttribute('data-state');
    host.innerHTML = '<table class="tt-table"><thead><tr>' +
      '<th scope="col">Symbol</th><th scope="col">Style</th>' +
      '<th scope="col" class="tt-num">Trades</th><th scope="col" class="tt-num">Exp (R)</th>' +
      '<th scope="col" class="tt-num">Total R</th><th scope="col" class="tt-num">PF</th>' +
      '<th scope="col" class="tt-num">DD (R)</th><th scope="col">Validated</th>' +
      '</tr></thead><tbody>' +
      perSymbol.map(function (row) {
        var exp = Number(row.expectancy_r || 0);
        var validated = row.generalises === true ? 'yes' : (row.generalises === false ? 'no' : '—');
        var vCls = row.generalises === true ? 'tt-up' : (row.generalises === false ? 'tt-down' : 'tt-muted');
        return '<tr>' +
          '<td><span class="tt-symbol">' + esc(row.symbol) + '</span></td>' +
          '<td class="tt-muted">' + esc(row.style || '—') + '</td>' +
          '<td class="tt-num">' + (row.trades !== undefined ? row.trades : '—') + '</td>' +
          '<td class="tt-num ' + signClass(exp) + '">' + num(exp, 4) + '</td>' +
          '<td class="tt-num ' + signClass(row.total_r) + '">' + num(row.total_r, 2) + '</td>' +
          '<td class="tt-num">' + num(row.profit_factor, 3) + '</td>' +
          '<td class="tt-num">' + num(row.max_dd_r, 2) + '</td>' +
          '<td class="' + vCls + '">' + esc(validated) + '</td>' +
          '</tr>';
      }).join('') + '</tbody></table>';
  }

  function loadJobs() {
    var body = $('bt-history');
    apiGet('/api/backtest/jobs', TIMEOUT.normal).then(function (res) {
      var jobs = (res.data && (res.data.jobs || res.data)) || [];
      if (!Array.isArray(jobs)) jobs = [];
      state.jobs = jobs;
      if (!jobs.length) {
        setState(body, 'empty', 'No jobs yet', null);
        return;
      }
      body.removeAttribute('data-state');
      body.innerHTML = jobs.slice(0, 12).map(function (j) {
        var status = String(j.status || '').toUpperCase();
        var cls = status === 'DONE' ? 'tt-chip--buy' :
                  status === 'FAILED' ? 'tt-chip--sell' :
                  status === 'RUNNING' ? 'tt-chip--medium' : 'tt-chip--none';
        return '<tr data-clickable="true" data-job="' + esc(j.job_id || j.id) + '">' +
          '<td class="tt-muted tt-truncate" style="max-width:150px">' + esc(j.label || j.job_id || j.id) + '</td>' +
          '<td><span class="tt-chip ' + cls + '">' + esc(status.toLowerCase()) + '</span></td>' +
          '<td class="tt-num">' + num(j.progress, 0) + '%</td>' +
          '<td class="tt-muted">' + esc(j.created_utc || j.started_utc || '—') + '</td>' +
          '</tr>';
      }).join('');

      Array.prototype.forEach.call(body.querySelectorAll('tr[data-job]'), function (tr) {
        tr.addEventListener('click', function () {
          var id = tr.getAttribute('data-job');
          state.activeJob = id;
          pollJob(id);
          loadJobResult(id);
          setView('backtest');
        });
      });
    });
  }

  function cancelJob() {
    if (!state.activeJob) return;
    apiPost('/api/backtest/cancel', { job_id: state.activeJob }, TIMEOUT.normal).then(function (res) {
      if (res.ok) toast('Cancel requested');
      else toast('Cancel failed: ' + (res.error || ('HTTP ' + res.status)), 'error');
    });
  }

  /* ── Manual trade ─────────────────────────────────────────────────────── */
  function submitTrade(side) {
    var sym = ($('ticket-symbol').value || '').trim().toUpperCase();
    var vol = Number($('ticket-volume').value || 0);
    if (!sym) { toast('Enter a symbol', 'warn'); return; }
    if (!(vol > 0)) { toast('Enter a volume greater than zero', 'warn'); return; }

    var body = { symbol: sym, side: side, volume: vol };
    var price = Number($('ticket-price').value);
    if (isFinite(price) && price > 0) body.price = price;
    var sl = Number($('ticket-sl').value);
    if (isFinite(sl) && sl > 0) body.sl = sl;
    var tp = Number($('ticket-tp').value);
    if (isFinite(tp) && tp > 0) body.tp = tp;

    apiPost('/api/action/manual_trade', body, TIMEOUT.normal).then(function (res) {
      if (res.ok) toast(side + ' ' + vol + ' ' + sym + ' submitted');
      else toast('Order rejected: ' + ((res.data && res.data.error) || res.error || ('HTTP ' + res.status)), 'error');
    });
  }

  /* ── Scheduler ────────────────────────────────────────────────────────── */
  function schedule() {
    function tick(fn, base) {
      return function loop() {
        // Back off 4x while the tab is hidden — a background tab does not need
        // to keep a trading screen at full refresh rate.
        var delay = document.hidden ? base * 4 : base;
        Promise.resolve().then(fn).then(function () {
          setTimeout(loop, delay);
        });
      };
    }
    tick(loadTelemetry, POLL.telemetry)();
    tick(function () { if (state.view === 'trade') loadChart(); }, POLL.chart)();
    tick(function () { if (state.view === 'analytics') loadReliability(); }, POLL.analytics)();
    tick(loadPendingOrders, POLL.pending)();
  }

  /* ── Boot ─────────────────────────────────────────────────────────────── */
  function bind() {
    Array.prototype.forEach.call(document.querySelectorAll('[data-view-btn]'), function (btn) {
      btn.addEventListener('click', function () { setView(btn.getAttribute('data-view-btn')); });
    });
    Array.prototype.forEach.call(document.querySelectorAll('[data-pane-btn]'), function (btn) {
      btn.addEventListener('click', function () { setPane(btn.getAttribute('data-pane-btn')); });
    });

    var refresh = $('watch-refresh');
    if (refresh) refresh.addEventListener('click', function () { loadTelemetry(); loadSelection(); });

    var radarFilter = $('radar-filter');
    if (radarFilter) {
      radarFilter.value = state.radarFilter;
      radarFilter.addEventListener('change', function () {
        state.radarFilter = radarFilter.value;
        renderRadar();
      });
    }

    var pendingRefresh = $('pending-refresh');
    if (pendingRefresh) pendingRefresh.addEventListener('click', loadPendingOrders);

    var tf = $('chart-timeframe');
    if (tf) {
      tf.value = state.timeframe;
      tf.addEventListener('change', function () { state.timeframe = tf.value; loadChart(); });
    }

    // Levels toggle. Hides support/resistance and trade overlays together, and
    // re-draws from the candles already on screen — no refetch needed.
    var levelsBtn = $('chart-levels');
    if (levelsBtn) {
      levelsBtn.setAttribute('aria-pressed', String(state.showLevels));
      levelsBtn.addEventListener('click', function () {
        state.showLevels = !state.showLevels;
        levelsBtn.setAttribute('aria-pressed', String(state.showLevels));
        levelsBtn.textContent = state.showLevels ? 'Levels on' : 'Levels off';
        levelsBtn.classList.toggle('is-off', !state.showLevels);
        refreshChartDecorations();
      });
    }

    var buy = $('ticket-buy');
    if (buy) buy.addEventListener('click', function () { submitTrade('BUY'); });
    var sell = $('ticket-sell');
    if (sell) sell.addEventListener('click', function () { submitTrade('SELL'); });

    var form = $('bt-form');
    if (form) form.addEventListener('submit', runBacktest);
    var cancel = $('bt-cancel');
    if (cancel) cancel.addEventListener('click', cancelJob);

    var symInput = $('ticket-symbol');
    if (symInput) {
      symInput.addEventListener('change', function () {
        var v = symInput.value.trim().toUpperCase();
        if (v) selectSymbol(v);
      });
    }

    // Keyboard: 1/2/3 switch views, [ ] cycle panes on phone widths.
    document.addEventListener('keydown', function (ev) {
      if (ev.target && /INPUT|TEXTAREA|SELECT/.test(ev.target.tagName)) return;
      if (ev.key === '1') setView('trade');
      if (ev.key === '2') setView('analytics');
      if (ev.key === '3') setView('backtest');
    });

    document.addEventListener('visibilitychange', function () {
      if (!document.hidden) { loadTelemetry(); if (state.view === 'trade') loadChart(); }
    });
  }

  function boot() {
    bind();
    var clock = $('clock');
    var tickClock = function () {
      if (clock) clock.textContent = clockTime(new Date());
    };
    tickClock();
    setInterval(tickClock, 1000);

    loadTelemetry().then(function () {
      loadSelection();
      loadChart();
    });
    loadPendingOrders();
    schedule();

    if (window.HMUI && typeof window.HMUI.announce === 'function') {
      window.HMUI.announce('Trading terminal ready');
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
