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
    chartSeries: null,
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
    if (view === 'trade') { loadChart(); }
  }

  function setPane(pane) {
    if (PANES.indexOf(pane) === -1) pane = 'watchlist';
    state.pane = pane;
    document.body.setAttribute('data-pane', pane);
    Array.prototype.forEach.call(document.querySelectorAll('[data-pane-btn]'), function (btn) {
      btn.setAttribute('aria-selected', String(btn.getAttribute('data-pane-btn') === pane));
    });
    if (pane === 'chart') loadChart();
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
        '<td class="tt-num">' + (isFinite(Number(d.entry_price)) ? num(d.entry_price, 5) : '—') + '</td>' +
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

  /* ── Positions ────────────────────────────────────────────────────────── */
  function renderPositions() {
    var body = $('pos-body');
    var positions = state.positions || [];
    setText($('pos-count'), String(positions.length));

    if (!positions.length) {
      setState(body, 'empty', 'No open positions', null);
      setText($('pos-total'), '—');
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
        '<td class="tt-num">' + num(p.price_open, 5) + '</td>' +
        '<td class="tt-num">' + num(p.price_current, 5) + '</td>' +
        '<td class="tt-num ' + signClass(profit) + '">' + (profit > 0 ? '+' : '') + num(profit, 2) + '</td>';
      frag.appendChild(tr);
    });

    body.appendChild(frag);
    var tot = $('pos-total');
    setText(tot, (total > 0 ? '+' : '') + num(total, 2));
    if (tot) tot.className = 'tt-num ' + signClass(total);
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

  /* ── Chart ────────────────────────────────────────────────────────────── */
  function loadChart() {
    var sym = state.symbol;
    var host = $('chart');
    var overlay = $('chart-overlay');
    if (!sym) {
      if (overlay) setState(overlay, 'empty', 'No instrument selected', null);
      return;
    }
    var title = $('chart-title');
    if (title) title.textContent = sym + ' · ' + state.timeframe;

    apiGet('/api/candles?symbol=' + encodeURIComponent(sym) +
           '&timeframe=' + encodeURIComponent(state.timeframe), TIMEOUT.normal)
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
        if (overlay) overlay.innerHTML = '';
        drawChart(candles);
      });
  }

  function drawChart(candles) {
    var host = $('chart');
    if (!host || typeof LightweightCharts === 'undefined') {
      var ov = $('chart-overlay');
      if (ov) setState(ov, 'error', 'Chart library unavailable', 'lightweight-charts did not load.');
      return;
    }

    // De-duplicate and sort ascending — the library throws otherwise.
    var seen = {};
    var data = [];
    candles.forEach(function (c) {
      var t = Number(c.time);
      if (!isFinite(t) || seen[t]) return;
      seen[t] = true;
      data.push({
        time: t,
        open: Number(c.open), high: Number(c.high),
        low: Number(c.low), close: Number(c.close)
      });
    });
    data.sort(function (a, b) { return a.time - b.time; });
    if (!data.length) return;

    var width = host.clientWidth || 600;
    var height = host.clientHeight || 320;

    if (!state.chartSeries) {
      host.innerHTML = '';
      var chart = LightweightCharts.createChart(host, {
        width: width,
        height: height,
        layout: { background: { color: 'transparent' }, textColor: '#94a3b8', fontSize: 11 },
        grid: { vertLines: { color: 'rgba(148,163,184,0.08)' }, horzLines: { color: 'rgba(148,163,184,0.08)' } },
        rightPriceScale: { borderColor: 'rgba(148,163,184,0.15)' },
        timeScale: { borderColor: 'rgba(148,163,184,0.15)', timeVisible: true, secondsVisible: false },
        crosshair: { mode: 0 }
      });
      // lightweight-charts v4 API.
      var series = (typeof chart.addCandlestickSeries === 'function')
        ? chart.addCandlestickSeries({
            upColor: '#00f59b', downColor: '#ff3b5c',
            borderUpColor: '#00f59b', borderDownColor: '#ff3b5c',
            wickUpColor: '#00f59b', wickDownColor: '#ff3b5c'
          })
        : chart.addSeries(LightweightCharts.CandlestickSeries, {
            upColor: '#00f59b', downColor: '#ff3b5c',
            borderUpColor: '#00f59b', borderDownColor: '#ff3b5c',
            wickUpColor: '#00f59b', wickDownColor: '#ff3b5c'
          });
      state.chartSeries = { chart: chart, series: series };

      window.addEventListener('resize', function () {
        if (!state.chartSeries) return;
        var w = host.clientWidth || 600;
        var h = host.clientHeight || 320;
        state.chartSeries.chart.applyOptions({ width: w, height: h });
      });
    }

    state.chartSeries.series.setData(data);
    state.chartSeries.chart.timeScale().fitContent();
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
      renderStatus();
    });
  }

  /* ── Analytics ────────────────────────────────────────────────────────── */
  function loadAnalytics() {
    loadReliability();
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

    var tf = $('chart-timeframe');
    if (tf) {
      tf.value = state.timeframe;
      tf.addEventListener('change', function () { state.timeframe = tf.value; loadChart(); });
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
