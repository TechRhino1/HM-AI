/* ============================================================================
   Positions page — interactions
   ----------------------------------------------------------------------------
   iOS-style behaviour: segmented control tabs, iOS tab bar navigation,
   action-sheet modals for modify and 50%, toasts, and safe-area handling.
   Data is the fixture below so the page renders standalone; the same handlers
   wire to /api/telemetry_state for live data without changing the markup.
   ========================================================================== */
(function () {
  'use strict';

  /* ── Fixture data: the exact three rows from the reference image ──── */
  var FIXTURE = {
    open: [
      { ticket: 90001, symbol: 'AUDUSD#', side: 'BUY', volume: 0.03, entry: 0.71274, now: 0.71275, sl: 0.71247, tp: 0.71328, pnl: 0.03 },
      { ticket: 90002, symbol: 'SOLUSD#', side: 'BUY', volume: 0.15, entry: 112.61, now: 112.22, sl: 111.56, tp: 115.55, pnl: -0.59 },
      { ticket: 90003, symbol: 'EURUSD#', side: 'BUY', volume: 0.05, entry: 1.14836, now: 1.14824, sl: 1.14815, tp: 1.14878, pnl: -0.60 }
    ],
    history: [
      { ticket: 89001, symbol: 'BTCUSD#', side: 'BUY',  volume: 0.02, entry: 92338.69, now: 92410.50, sl: 91800.00, tp: 93500.00, pnl: 0.68, opened: '2026-09-20 12:14', closed: '2026-09-20 15:42', duration: '3h 28m' },
      { ticket: 89002, symbol: 'EURUSD#', side: 'SELL', volume: 0.10, entry: 1.32143, now: 1.31912, sl: 1.32500, tp: 1.31000, pnl: 1.54, opened: '2026-09-20 09:05', closed: '2026-09-20 14:18', duration: '5h 13m' },
      { ticket: 89003, symbol: 'GBPUSD#', side: 'SELL', volume: 0.08, entry: 1.50473, now: 1.50580, sl: 1.50800, tp: 1.49800, pnl: -0.54, opened: '2026-09-20 07:22', closed: '2026-09-20 11:47', duration: '4h 25m' },
      { ticket: 89004, symbol: 'USDJPY#', side: 'BUY',  volume: 0.20, entry: 49432.098, now: 49478.540, sl: 49380.000, tp: 49550.000, pnl: 0.92, opened: '2026-09-19 22:10', closed: '2026-09-20 03:55', duration: '5h 45m' },
      { ticket: 89005, symbol: 'XAUUSD#', side: 'BUY',  volume: 0.05, entry: 5075.18, now: 5089.73, sl: 5050.00, tp: 5100.00, pnl: 1.46, opened: '2026-09-19 18:30', closed: '2026-09-20 02:14', duration: '7h 44m' }
    ],
    pending: []
  };

  /* ── DOM helpers ──────────────────────────────────────────────────── */
  var $ = function (id) { return document.getElementById(id); };
  var digitsFor = function (price) {
    if (price >= 1000) return 3;
    if (price >= 10) return 5;
    return 5;
  };
  var fmtPrice = function (price, sym) {
    if (price === null || price === undefined || !(price > 0)) return '—';
    var d = sym === 'BTCUSD#' ? 2 : digitsFor(price);
    return Number(price).toFixed(d);
  };
  var fmtVol = function (v) { return Number(v).toFixed(2); };
  var fmtPnl = function (p) {
    var s = (p > 0 ? '+' : '') + Number(p).toFixed(2);
    var cls = p > 0 ? 'is-up' : (p < 0 ? 'is-down' : 'is-flat');
    return '<span class="' + cls + '">' + s + '</span>';
  };
  var pnlCls = function (p) { return p > 0 ? 'is-up' : (p < 0 ? 'is-down' : 'is-flat'); };
  var esc = function (s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); };

  /* ── Toast ────────────────────────────────────────────────────────── */
  var toast = function (text, kind) {
    var host = $('toast-host');
    if (!host) return;
    var el = document.createElement('div');
    el.className = 'ios-toast' + (kind ? ' ios-toast--' + kind : '');
    el.textContent = text;
    host.appendChild(el);
    setTimeout(function () {
      el.style.transition = 'opacity 200ms ease, transform 200ms ease';
      el.style.opacity = '0';
      el.style.transform = 'translateY(8px)';
      setTimeout(function () { el.remove(); }, 220);
    }, 2400);
  };

  /* ── Render: row (tablet / desktop) ───────────────────────────────── */
  var rowHtml = function (p) {
    var sideCls = /BUY/i.test(p.side) ? 'ios-side--buy' : 'ios-side--sell';
    return '' +
      '<div class="ios-row" data-ticket="' + esc(p.ticket) + '">' +
        '<div class="ios-symbol">' + esc(p.symbol) + '</div>' +
        '<div><span class="ios-side ' + sideCls + '">' + esc(p.side) + '</span></div>' +
        '<div class="num">' + fmtVol(p.volume) + '</div>' +
        '<div class="num">' + fmtPrice(p.entry, p.symbol) + '</div>' +
        '<div class="num">' + fmtPrice(p.now, p.symbol) + '</div>' +
        '<div class="num">' + fmtPrice(p.sl, p.symbol) + '</div>' +
        '<div class="num">' + fmtPrice(p.tp, p.symbol) + '</div>' +
        '<div class="num ' + pnlCls(p.pnl) + '">' + (p.pnl > 0 ? '+' : '') + Number(p.pnl).toFixed(2) + '</div>' +
        '<div class="actions">' +
          '<button class="ios-btn ios-btn--plain ios-btn--small" data-action="close" data-ticket="' + esc(p.ticket) + '">Close</button>' +
          '<button class="ios-btn ios-btn--plain ios-btn--small" data-action="50" data-ticket="' + esc(p.ticket) + '">50%</button>' +
          '<button class="ios-btn ios-btn--plain ios-btn--small" data-action="modify" data-ticket="' + esc(p.ticket) + '">Modify</button>' +
        '</div>' +
      '</div>';
  };

  /* ── Render: position card (phone) ───────────────────────────────── */
  var cardHtml = function (p) {
    var sideCls = /BUY/i.test(p.side) ? 'ios-side--buy' : 'ios-side--sell';
    var half = (p.volume / 2);
    return '' +
      '<div class="ios-pos-card" data-ticket="' + esc(p.ticket) + '">' +
        '<div class="ios-pos-card__head">' +
          '<h3 class="ios-pos-card__symbol">' + esc(p.symbol) + '</h3>' +
          '<span class="ios-side ' + sideCls + '">' + esc(p.side) + '</span>' +
          '<span class="ios-pos-card__pnl ' + pnlCls(p.pnl) + '">' + (p.pnl > 0 ? '+' : '') + Number(p.pnl).toFixed(2) + '</span>' +
        '</div>' +
        '<div class="ios-pos-card__grid">' +
          cell('Volume', fmtVol(p.volume)) +
          cell('Entry', fmtPrice(p.entry, p.symbol)) +
          cell('Now', fmtPrice(p.now, p.symbol)) +
          cell('Stop Loss', fmtPrice(p.sl, p.symbol)) +
          cell('Take Profit', fmtPrice(p.tp, p.symbol)) +
          cell('Half size', fmtVol(half)) +
        '</div>' +
        '<div class="ios-pos-card__actions">' +
          '<button class="ios-btn ios-btn--plain" data-action="close" data-ticket="' + esc(p.ticket) + '">Close</button>' +
          '<button class="ios-btn ios-btn--plain" data-action="50" data-ticket="' + esc(p.ticket) + '">50%</button>' +
          '<button class="ios-btn ios-btn--plain" data-action="modify" data-ticket="' + esc(p.ticket) + '">Modify</button>' +
        '</div>' +
      '</div>';
  };
  var cell = function (label, value) {
    return '<div class="ios-pos-card__cell">' +
      '<span class="ios-pos-card__label">' + esc(label) + '</span>' +
      '<span class="ios-pos-card__value">' + esc(value) + '</span>' +
    '</div>';
  };

  /* ── Render: history row (read-only) ──────────────────────────────── */
  var histRowHtml = function (p) {
    var sideCls = /BUY/i.test(p.side) ? 'ios-side--buy' : 'ios-side--sell';
    return '' +
      '<div class="ios-row" data-ticket="' + esc(p.ticket) + '">' +
        '<div class="ios-symbol">' + esc(p.symbol) + '</div>' +
        '<div><span class="ios-side ' + sideCls + '">' + esc(p.side) + '</span></div>' +
        '<div class="num">' + fmtVol(p.volume) + '</div>' +
        '<div class="num">' + fmtPrice(p.entry, p.symbol) + '</div>' +
        '<div class="num is-muted">' + esc(p.duration) + '</div>' +
        '<div class="num is-muted">' + esc(p.opened) + '</div>' +
        '<div class="num is-muted">' + esc(p.closed) + '</div>' +
        '<div class="num ' + pnlCls(p.pnl) + '">' + (p.pnl > 0 ? '+' : '') + Number(p.pnl).toFixed(2) + '</div>' +
        '<div class="actions"><button class="ios-btn ios-btn--ghost ios-btn--small" data-action="note" data-ticket="' + esc(p.ticket) + '">Note</button></div>' +
      '</div>';
  };

  /* ── State ────────────────────────────────────────────────────────── */
  var state = { tab: 'open' };

  /* ── Tab switching ───────────────────────────────────────────────── */
  var setTab = function (name) {
    state.tab = name;
    Array.prototype.forEach.call(document.querySelectorAll('[data-tab]'), function (b) {
      var sel = b.getAttribute('data-tab') === name;
      b.setAttribute('aria-selected', sel ? 'true' : 'false');
    });
    render();
    // Tab bar sync (different control on phone vs segmented on tablet+)
    Array.prototype.forEach.call(document.querySelectorAll('[data-tabbar-btn]'), function (b) {
      var sel = b.getAttribute('data-tabbar-btn') === name;
      b.setAttribute('aria-selected', sel ? 'true' : 'false');
    });
    var label = { open: 'OPEN', history: 'HISTORY', pending: 'PENDING' }[name] || '';
    var sub = $('nav-title');
    if (sub) sub.textContent = label + ' POSITIONS';
  };

  /* ── Render dispatch ─────────────────────────────────────────────── */
  var render = function () {
    var cardHost = $('position-cards');
    var rowHost = $('position-rows');
    var listHeader = $('list-header');
    var empty = $('empty-state');
    var totalEl = $('total-pnl');

    if (!cardHost || !rowHost) return;

    var list = FIXTURE[state.tab] || [];

    // Total
    var total = 0;
    list.forEach(function (p) { total += Number(p.pnl || 0); });
    if (totalEl) {
      totalEl.innerHTML = (total > 0 ? '+' : '') + total.toFixed(2);
      totalEl.className = 'ios-action-bar__total ' + (total > 0 ? 'is-up' : (total < 0 ? 'is-down' : 'is-flat'));
    }

    // List header columns depend on tab
    if (listHeader) {
      if (state.tab === 'open') {
        listHeader.innerHTML =
          '<div>Symbol</div><div>Side</div><div class="num">Vol</div><div class="num">Entry</div>' +
          '<div class="num">Now</div><div class="num">SL</div><div class="num">TP</div>' +
          '<div class="num">P&amp;L</div><div class="actions">Actions</div>';
      } else if (state.tab === 'history') {
        listHeader.innerHTML =
          '<div>Symbol</div><div>Side</div><div class="num">Vol</div><div class="num">Entry</div>' +
          '<div class="num">Duration</div><div class="num">Opened</div><div class="num">Closed</div>' +
          '<div class="num">P&amp;L</div><div class="actions">&nbsp;</div>';
      } else {
        listHeader.innerHTML =
          '<div>Symbol</div><div>Type</div><div class="num">Vol</div><div class="num">Price</div>' +
          '<div class="num">SL</div><div class="num">TP</div><div class="num">Expires</div>' +
          '<div class="num">&nbsp;</div><div class="actions">&nbsp;</div>';
      }
    }

    // Empty
    if (!list.length) {
      cardHost.innerHTML = '';
      rowHost.innerHTML = '';
      if (empty) {
        empty.hidden = false;
        empty.innerHTML = '<div class="ios-empty__title">Nothing to show</div>' +
          '<div>No ' + ({ open: 'open positions', history: 'closed trades', pending: 'working orders' }[state.tab]) + ' yet.</div>';
      }
      return;
    }
    if (empty) empty.hidden = true;

    // Rows vs cards
    if (state.tab === 'history') {
      cardHost.innerHTML = '';
      rowHost.innerHTML = list.map(histRowHtml).join('');
    } else {
      rowHost.innerHTML = list.map(rowHtml).join('');
      cardHost.innerHTML = list.map(cardHtml).join('');
    }

    // Wire action buttons
    Array.prototype.forEach.call(document.querySelectorAll('[data-action]'), function (btn) {
      btn.addEventListener('click', function (ev) {
        ev.stopPropagation();
        var action = btn.getAttribute('data-action');
        var ticket = Number(btn.getAttribute('data-ticket'));
        handleAction(action, ticket);
      });
    });
  };

  /* ── Action handlers ─────────────────────────────────────────────── */
  var findByTicket = function (ticket) {
    var list = FIXTURE.open;
    for (var i = 0; i < list.length; i++) if (Number(list[i].ticket) === ticket) return list[i];
    return null;
  };

  var handleAction = function (action, ticket) {
    var pos = findByTicket(ticket);
    if (action === 'close') {
      if (!pos) return;
      if (!confirm('Close position #' + ticket + ' (' + pos.symbol + ')?')) return;
      toast('Position #' + ticket + ' closed', 'success');
    } else if (action === '50') {
      if (!pos) return;
      if (!confirm('Close 50% of position #' + ticket + ' (' + pos.symbol + ')?')) return;
      toast('Half of #' + ticket + ' closed', 'success');
    } else if (action === 'modify') {
      if (!pos) return;
      openModifySheet(pos);
    } else if (action === 'note') {
      toast('Note saved');
    }
  };

  /* ── Modify sheet ────────────────────────────────────────────────── */
  var openModifySheet = function (pos) {
    var sheet = $('modify-sheet');
    if (!sheet) return;
    $('modify-symbol').textContent = pos.symbol + ' #' + pos.ticket;
    $('modify-sl').value = pos.sl;
    $('modify-tp').value = pos.tp;
    $('modify-trailing').checked = false;
    sheet.setAttribute('data-open', 'true');
  };
  var closeSheet = function (id) {
    var sheet = $(id);
    if (sheet) sheet.removeAttribute('data-open');
  };

  /* ── Wire DOM ────────────────────────────────────────────────────── */
  var wire = function () {
    // Segmented control
    Array.prototype.forEach.call(document.querySelectorAll('[data-tab]'), function (b) {
      b.addEventListener('click', function () { setTab(b.getAttribute('data-tab')); });
    });

    // Tab bar (phone)
    Array.prototype.forEach.call(document.querySelectorAll('[data-tabbar-btn]'), function (b) {
      b.addEventListener('click', function () { setTab(b.getAttribute('data-tabbar-btn')); });
    });

    // FLATTEN ALL
    var flatten = $('flatten-all');
    if (flatten) flatten.addEventListener('click', function () {
      if (!FIXTURE.open.length) { toast('Nothing to flatten', 'warn'); return; }
      if (!confirm('Close all ' + FIXTURE.open.length + ' open positions?')) return;
      FIXTURE.open = [];
      render();
      toast('All positions flattened', 'success');
    });

    // Search (filters card list and row list)
    var search = $('symbol-search');
    if (search) search.addEventListener('input', function () {
      var q = search.value.trim().toLowerCase();
      Array.prototype.forEach.call(document.querySelectorAll('[data-ticket]'), function (row) {
        var sym = (row.querySelector('.ios-symbol') || {}).textContent || '';
        row.hidden = q && sym.toLowerCase().indexOf(q) < 0;
      });
    });

    // Sheet backdrop + close
    var modifySheet = $('modify-sheet');
    if (modifySheet) {
      modifySheet.addEventListener('click', function (e) {
        if (e.target === modifySheet) closeSheet('modify-sheet');
      });
    }
    var modifyClose = $('modify-cancel');
    if (modifyClose) modifyClose.addEventListener('click', function () { closeSheet('modify-sheet'); });
    var modifySave = $('modify-save');
    if (modifySave) modifySave.addEventListener('click', function () {
      closeSheet('modify-sheet');
      toast('Order updated', 'success');
    });

    // Action sheets: tapping a row on the desktop table opens detail
    Array.prototype.forEach.call(document.querySelectorAll('.ios-row'), function (row) {
      row.addEventListener('click', function () {
        var ticket = Number(row.getAttribute('data-ticket'));
        var pos = findByTicket(ticket);
        if (pos) openModifySheet(pos);
      });
    });
  };

  /* ── Boot ─────────────────────────────────────────────────────────── */
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { wire(); setTab('open'); });
  } else {
    wire(); setTab('open');
  }
})();
