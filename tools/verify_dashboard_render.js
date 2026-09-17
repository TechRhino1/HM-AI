/* ===========================================================================
   Headless render check for the HM Algo 2.0 dashboard (tools/verify_dashboard_render.js)

   WHY THIS EXISTS
   ---------------
   The dashboard's chart is the one part of the UI that cannot be checked with
   curl: support/resistance levels, the volume series and the trade overlays are
   all created at runtime against the lightweight-charts API. A syntax check
   proves the file parses, not that a single price line is ever drawn.

   This harness runs the REAL jarvis/ui/static/js/dashboard.js in a Node `vm`
   with a stubbed DOM and a stubbed charting library, feeds it candle and
   telemetry payloads with a known swing structure, and asserts on what the
   module actually asked the chart library to draw.

   It also cross-checks every element id the module reaches for against the ids
   that exist in dashboard.html. A controller that queries an id the template
   does not define fails silently in a browser — the panel simply never
   updates — so that check is the difference between "looks fine" and "is wired".

   Run: node tools/verify_dashboard_render.js
   Exits non-zero on the first failed expectation.
   =========================================================================== */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '..');
const JS = path.join(ROOT, 'jarvis', 'ui', 'static', 'js', 'dashboard.js');
const HTML = path.join(ROOT, 'jarvis', 'ui', 'templates', 'dashboard.html');

let failures = 0;
let checks = 0;

function ok(label, condition, detail) {
  checks++;
  if (condition) {
    console.log('  PASS  ' + label);
  } else {
    failures++;
    console.log('  FAIL  ' + label + (detail ? '  -> ' + detail : ''));
  }
}

/* ── Read the template's real ids ─────────────────────────────────────────── */
const html = fs.readFileSync(HTML, 'utf8');
const templateIds = new Set();
{
  const re = /\bid="([^"]+)"/g;
  let m;
  while ((m = re.exec(html)) !== null) templateIds.add(m[1]);
}

/* ── Minimal DOM ──────────────────────────────────────────────────────────── */
const requestedIds = [];
const prelinked = new Set();

/* A very small selector engine: compound selectors (tag / .class / #id /
   [attr] / [attr="v"]) optionally joined by descendant spaces. That is the
   whole subset the controller uses, and a genuine tree walk is required to
   verify the per-second countdown tick, which updates cells in place by
   querying them rather than by rebuilding the table. */
function matchesSimple(el, sel) {
  let s = sel;
  const idM = /#([\w-]+)/.exec(s);
  if (idM && el.id !== idM[1]) return false;
  s = s.replace(/#[\w-]+/g, '');

  const classes = (s.match(/\.[\w-]+/g) || []).map((c) => c.slice(1));
  s = s.replace(/\.[\w-]+/g, '');

  const attrs = [];
  const attrRe = /\[([\w-]+)(?:="([^"]*)")?\]/g;
  let m;
  while ((m = attrRe.exec(s)) !== null) attrs.push([m[1], m[2]]);
  s = s.replace(/\[[^\]]*\]/g, '');

  const tag = s.trim();
  if (tag && el.tagName !== tag.toUpperCase()) return false;

  for (const c of classes) {
    if (!el.classList.contains(c) && String(el.className || '').split(/\s+/).indexOf(c) < 0) return false;
  }
  for (const a of attrs) {
    const v = el.getAttribute(a[0]);
    if (v === null) return false;
    if (a[1] !== undefined && v !== a[1]) return false;
  }
  return true;
}

function descendantsOf(root) {
  const out = [];
  (function walk(node) {
    const kids = node.children || [];
    for (let i = 0; i < kids.length; i++) { out.push(kids[i]); walk(kids[i]); }
  })(root);
  return out;
}

function selectAll(root, selector) {
  const parts = String(selector).trim().split(/\s+/).filter(Boolean);
  let scope = [root];
  for (const part of parts) {
    const next = [];
    for (const node of scope) {
      for (const el of descendantsOf(node)) {
        if (matchesSimple(el, part) && next.indexOf(el) < 0) next.push(el);
      }
    }
    scope = next;
  }
  return scope;
}

function decodeEntities(s) {
  return String(s)
    .replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"').replace(/&#39;/g, "'");
}

const VOID_TAGS = new Set(['BR', 'HR', 'IMG', 'INPUT', 'META', 'LINK']);

/* Parse a well-formed HTML fragment into real nodes.

   The controller writes table rows with innerHTML. Without this the harness
   would hold them as an opaque string, which hides every in-place update —
   including the per-second countdown tick, which finds its cells by querying
   the tree rather than by rebuilding the table. That is the one piece of real
   time-dependent logic in the calendar, so it has to be observable.

   Handles only the subset the controller emits: quoted attributes, no
   comments, no CDATA, and void elements only from the set above. */
function parseHtml(fragment) {
  const root = new El('', 'div');
  const stack = [root];
  const tagRe = /<\/?([a-zA-Z][\w-]*)((?:\s+[\w-]+(?:="[^"]*")?)*)\s*\/?>/g;
  let last = 0;
  let m;

  const textInto = (txt) => {
    if (!txt) return;
    const top = stack[stack.length - 1];
    top._text += decodeEntities(txt);
  };

  while ((m = tagRe.exec(fragment)) !== null) {
    textInto(fragment.slice(last, m.index));
    last = tagRe.lastIndex;

    const raw = m[0];
    if (raw.charAt(1) === '/') {
      if (stack.length > 1) stack.pop();
      continue;
    }

    const el = new El('', m[1].toUpperCase());
    const attrRe = /([\w-]+)(?:="([^"]*)")?/g;
    let a;
    while ((a = attrRe.exec(m[2] || '')) !== null) {
      if (!a[1]) continue;
      el.setAttribute(a[1], a[2] === undefined ? '' : decodeEntities(a[2]));
    }

    stack[stack.length - 1].appendChild(el);
    if (!VOID_TAGS.has(el.tagName) && !/\/>$/.test(raw)) stack.push(el);
  }
  textInto(fragment.slice(last));
  return root;
}

class El {
  constructor(id, tag) {
    this.id = id || '';
    this.tagName = (tag || 'DIV').toUpperCase();
    this._text = '';
    this._html = '';
    this.className = '';
    this.hidden = false;
    this.style = {};
    this.attrs = {};
    this.children = [];
    this.parentNode = null;
    this.dataset = {};
    this.offsetWidth = 100;
    this.clientWidth = 800;
    this.clientHeight = 360;
    this.classList = {
      _s: new Set(),
      add: (...c) => c.forEach((x) => this.classList._s.add(x)),
      remove: (...c) => c.forEach((x) => this.classList._s.delete(x)),
      toggle: (c, on) => (on ? this.classList._s.add(c) : this.classList._s.delete(c)),
      contains: (c) => this.classList._s.has(c)
    };
  }
  get textContent() { return this._text; }
  set textContent(v) { this._text = v === null || v === undefined ? '' : String(v); }
  /* A real <select> always exposes `.options`, so a controller that reads
     `sel.options.length` is not doing anything unusual — it is the documented
     way to test whether a select has been populated. Without this the backtest
     form's guard (`options.length === 0`, i.e. "only populate once") threw a
     TypeError in the harness and took the whole run down, which would have
     looked like a crash in the dashboard rather than a gap in the stub.
     Derived from the tree so appending an <option> is actually observable. */
  get options() { return (this.children || []).filter((c) => c.tagName === 'OPTION'); }
  get innerHTML() { return this._html; }
  set innerHTML(v) {
    this._html = v === null || v === undefined ? '' : String(v);
    this.children = [];
    // Materialise the fragment so the tree can be queried. `_html` keeps the
    // raw string, so deepHtml() still works for the panels that are asserted
    // on by content.
    if (this._html && this._html.indexOf('<') >= 0) {
      const parsed = parseHtml(this._html);
      parsed.children.forEach((c) => this.appendChild(c));
    }
  }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null; }
  removeAttribute(k) { delete this.attrs[k]; }
  addEventListener(type, fn) {
    this._listeners = this._listeners || {};
    (this._listeners[type] = this._listeners[type] || []).push(fn);
  }
  removeEventListener() {}
  /* Drive a bound handler from the harness. Without this the controller's
     click and change bindings are unreachable, and the only way to exercise a
     view would be to call its renderer directly — which would prove nothing
     about whether the wiring works. */
  fire(type, ev) {
    const ls = (this._listeners || {})[type] || [];
    ls.forEach((fn) => fn(ev || {}));
    return ls.length;
  }
  appendChild(c) {
    // A real DOM moves a DocumentFragment's children into the target rather
    // than inserting the fragment itself. Emulate that, or a panel built with
    // a fragment looks like it holds one child.
    if (c && c.tagName === 'FRAGMENT') {
      const kids = c.children || [];
      for (let i = 0; i < kids.length; i++) { kids[i].parentNode = this; this.children.push(kids[i]); }
      c.children = [];
      return c;
    }
    c.parentNode = this;
    this.children.push(c);
    return c;
  }
  removeChild(c) { this.children = this.children.filter((x) => x !== c); c.parentNode = null; return c; }
  insertBefore(c) { c.parentNode = this; this.children.push(c); return c; }
  querySelectorAll(sel) { return selectAll(this, sel); }
  querySelector(sel) { return selectAll(this, sel)[0] || null; }
  contains() { return true; }
  remove() {}
  closest() { return null; }
  focus() {}
}

const registry = new Map();
function elementFor(id) {
  requestedIds.push(id);
  if (!templateIds.has(id)) return null;   // mirrors a real getElementById miss
  if (!registry.has(id)) registry.set(id, new El(id));
  return registry.get(id);
}

/* The controller queries *within* a view section — tickNews walks #view-news to
   update countdown cells in place, and selectNews walks the calendar table. A
   flat registry has no ancestry, so the panels that live inside each view are
   declared here and linked before the module runs. Ids created this way are
   recorded as pre-linked so they do not make the "every queried id exists"
   check pass by construction. */
const DOC_ROOTS = ['view-trade', 'view-news', 'view-analyst', 'view-markets',
                   'view-analytics', 'view-backtest', 'toasts'];

const TEMPLATE_TREE = {
  'view-trade': ['watch-count', 'watch-body', 'watch-refresh', 'selection-count',
                 'selection-tier', 'selection-body', 'radar-count', 'radar-filter',
                 'radar-body', 'chart-title', 'chart-live-price', 'chart-legend',
                 'chart-src-native', 'chart-src-tv', 'chart-levels', 'chart-timeframe',
                 'chart', 'chart-tv', 'chart-hud', 'chart-tooltip', 'chart-overlay',
                 'pos-count', 'pos-total', 'pos-body', 'ticket-source', 'ticket-symbol',
                 'ticket-buy', 'ticket-sell', 'ticket-volume', 'ticket-price',
                 'ticket-sl', 'ticket-tp', 'ticket-hint', 'pending-count',
                 'pending-refresh', 'pending-body', 'reason-tier', 'reason-body'],
  'view-news': ['news-count', 'news-live-chip', 'news-impact', 'news-currency',
                'news-refresh', 'news-body', 'news-updated', 'news-hero',
                'news-detail-impact', 'news-detail'],
  'view-analyst': ['da-symbol', 'da-verdict', 'da-metrics', 'da-bull', 'da-bear',
                   'da-threats', 'da-invalidation', 'gate-count', 'gate-verdict',
                   'gate-body', 'da-objections'],
  'view-markets': ['eq-count', 'eq-prov', 'eq-refresh', 'eq-body', 'eq-heat-note',
                   'eq-heatmap', 'in-refresh', 'in-index-prov', 'in-indices',
                   'in-fii-prov', 'in-fii', 'in-oc-prov', 'in-oc-symbol',
                   'in-optionchain']
};

function linkTree() {
  Object.keys(TEMPLATE_TREE).forEach((parentId) => {
    const parent = elementFor(parentId);
    if (!parent) return;
    prelinked.add(parentId);
    TEMPLATE_TREE[parentId].forEach((childId) => {
      const child = elementFor(childId);
      if (!child) return;
      prelinked.add(childId);
      if (!child.parentNode) parent.appendChild(child);
    });
  });
  DOC_ROOTS.forEach((id) => {
    const el = elementFor(id);
    if (!el) return;
    prelinked.add(id);
    if (!el.parentNode) documentStub.appendChild(el);
  });
}

const documentListeners = {};
function fireDocument(type, ev) {
  const ls = documentListeners[type] || [];
  ls.forEach((fn) => fn(ev || {}));
  return ls.length;
}

/* Serialise an element and everything beneath it.

   Two traps this avoids. Panels built with createElement + appendChild (the
   radar, the bull/bear lists, the metric cards) leave innerHTML empty on the
   container, so reading innerHTML alone reports a blank panel. And panels built
   with textContent leave nothing in innerHTML at all, so the text has to be
   included too. */
function deepHtml(el) {
  if (!el) return '';
  let out = (el.innerHTML || '') + (el._text || '');
  const kids = el.children || [];
  for (let i = 0; i < kids.length; i++) out += deepHtml(kids[i]);
  return out;
}

const documentStub = {
  readyState: 'complete',
  body: new El('body', 'body'),
  documentElement: new El('html', 'html'),
  head: new El('head', 'head'),
  children: [],
  appendChild(c) { c.parentNode = documentStub; documentStub.children.push(c); return c; },
  getElementById: elementFor,
  createElement: (tag) => new El('', tag),
  createDocumentFragment: () => new El('', 'fragment'),
  querySelectorAll: (sel) => selectAll(documentStub, sel),
  querySelector: (sel) => selectAll(documentStub, sel)[0] || null,
  addEventListener: (type, fn) => {
    (documentListeners[type] = documentListeners[type] || []).push(fn);
  },
  hidden: false
};

/* ── Chart library stub: records every drawing instruction ────────────────── */
const drawn = {
  charts: 0,
  candleSeries: 0,
  volumeSeries: 0,
  priceLines: [],
  candleData: null,
  volumeData: null,
  updates: 0,
  fitContent: 0,
  candleOptions: []
};

function makeSeries(kind) {
  return {
    kind,
    _lines: [],
    applyOptions(o) { if (kind === 'candles') drawn.candleOptions.push(o); },
    setData(d) {
      if (kind === 'candles') drawn.candleData = d;
      else drawn.volumeData = d;
    },
    update() { drawn.updates++; },
    createPriceLine(o) { drawn.priceLines.push(o); return { o }; },
    removePriceLine(l) { this._lines = this._lines.filter((x) => x !== l); },
    priceScale() { return { applyOptions() {} }; },
    setMarkers() {}
  };
}

const LightweightCharts = {
  LineStyle: { Solid: 0, Dotted: 1, Dashed: 2, LargeDashed: 3, SparseDotted: 4 },
  createChart(host, opts) {
    drawn.charts++;
    return {
      _opts: opts,
      addCandlestickSeries() { drawn.candleSeries++; return makeSeries('candles'); },
      addHistogramSeries() { drawn.volumeSeries++; return makeSeries('volume'); },
      addSeries() { drawn.candleSeries++; return makeSeries('candles'); },
      priceScale() { return { applyOptions() {} }; },
      applyOptions() {},
      timeScale() { return { fitContent() { drawn.fitContent++; } }; },
      subscribeCrosshairMove() {},
      chartElement() { return host; }
    };
  }
};

/* ── Candle fixture with a known swing structure ──────────────────────────── */
/* Default bars sit at 100 (high 101 / low 99). Two pivot highs (110, 115) and
   two pivot lows (90, 85) are planted, with a last close of 100, so the
   expected levels are exactly R1=110, R2=115, S1=90, S2=85. */
const T0 = 1700000000;
function buildCandles() {
  const bars = [];
  for (let i = 0; i < 30; i++) {
    bars.push({ time: T0 + i * 3600, open: 100, high: 101, low: 99, close: 100, volume: 1000 + i });
  }
  bars[10] = { time: T0 + 10 * 3600, open: 100, high: 110, low: 99, close: 105, volume: 5000 };
  bars[12] = { time: T0 + 12 * 3600, open: 100, high: 101, low: 90, close: 95, volume: 4000 };
  bars[20] = { time: T0 + 20 * 3600, open: 100, high: 115, low: 99, close: 108, volume: 6000 };
  bars[22] = { time: T0 + 22 * 3600, open: 100, high: 101, low: 85, close: 92, volume: 4500 };
  return bars;
}

const TELEMETRY = {
  execution_mode: 'AUTO',
  trade_style: 'SWING',
  safe_mode: false,
  is_running: true,
  account: {
    balance: 10000, equity: 10120, profit: 120, free_margin: 9000,
    margin: 1000, margin_level: 1012, leverage: 100, currency: 'USD', trade_allowed: true
  },
  positions_count: 1,
  positions: [{
    ticket: 12345, symbol: 'XAUUSD', type: 'BUY', volume: 0.10,
    open_price: 100, current_price: 105, sl: 95, tp: 110,
    profit: 12.5, swap: 0, commission: 0, open_time: '2026-09-15 00:00:00', magic: 1, comment: ''
  }],
  services: { DATA_FEED: 'OK', MT5: 'CONNECTED' },
  radar_opportunities: [
    {
      symbol: 'XAUUSD', trade_style: 'SWING', timeframe: 'H1',
      current_price: 105, entry_price: 100, stop_loss: 95, take_profit: 110,
      risk_reward_ratio: 2, ev: 0.42, bias: 'BUY', action: 'BUY READY',
      status_label: 'BUY READY', decision: 'EXECUTE', score: 68, win_prob: 68,
      confluence_score: 7.5, confluence_tier: 'STRONG', regime: 'TREND_BULL',
      strategy: 'TREND_FOLLOW', utility_score: 0.81, setup_grade: 'A',
      is_actionable: true
    },
    {
      symbol: 'EURUSD', trade_style: 'SCALP', timeframe: 'M5',
      current_price: 1.085, entry_price: 1.085, stop_loss: 1.083, take_profit: 1.089,
      risk_reward_ratio: 2, ev: -0.10, bias: 'HOLD', action: 'NO SETUP',
      status_label: 'NO SETUP', decision: 'NO_TRADE', score: 41, win_prob: 41,
      confluence_score: 3.1, confluence_tier: 'WEAK', regime: 'COMPRESSION',
      strategy: 'MEAN_REVERT', utility_score: 0.12, setup_grade: 'C',
      is_actionable: false
    }
  ],
  latest_decisions: {
    XAUUSD: {
      symbol: 'XAUUSD', bias: 'BUY', strategy: 'TREND', entry_price: 100,
      stop_loss: 95, take_profit: 110, risk_reward_ratio: 2, model_confidence: 0.72,
      calculated_risk_percent: 1, expected_value: 0.4,
      // 30 of 50 on the engine's own scale: above the 25 threshold, so the
      // gauge must render in the severe band at 60% fill.
      adversarial_penalty: 30,
      dissection_tier: 'STRONG', dissection_score: 7.5,
      master_confluence_tier: 'HIGH', master_confluence_score: 8.2,
      pattern_sample_size: 140,
      regime: { primary: 'TREND_BULL', probabilities: {}, confidence: 0.8 },
      probabilities: {}, invalidation_levels: ['H4 close below 94.20', 'Loss of 93.80 swing low'],
      bull_case: ['H4 structure in premium rejection', 'Positive order flow'],
      bear_case: ['RSI divergence on H1'],
      risk_factors: ['Event risk inside 6h', 'Spread widens at rollover'],
      quality_gate: {
        passed: false,
        // Two failures, deliberately not first in key order, so the
        // failures-first sort is actually exercised.
        checks: {
          'Market Session Open': true,
          'Regime Viability': true,
          'Devil Adversarial Guard': false,
          'Spread Protection': true,
          'Positive Expected Value': false
        },
        failing_reasons: ['Devil Adversarial Guard', 'Positive Expected Value']
      },
      decision: 'NO_TRADE', execution_authorized: false,
      waiting_reasons: ['Waiting for H1 close above 100.40'],
      rejection_reasons: ['Adversarial penalty above tolerance'],
      gate_policy_decision: 'BLOCK'
    },
    EURUSD: {
      symbol: 'EURUSD', bias: 'HOLD', strategy: 'MEAN_REVERT', entry_price: 1.085,
      stop_loss: 1.083, take_profit: 1.089, risk_reward_ratio: 2, model_confidence: 0.4,
      calculated_risk_percent: 0.5, expected_value: -0.1, adversarial_penalty: 8,
      regime: { primary: 'COMPRESSION', probabilities: {}, confidence: 0.5 },
      probabilities: {}, invalidation_levels: [], bull_case: [], bear_case: [],
      risk_factors: [], quality_gate: { passed: true, checks: {}, failing_reasons: [] },
      decision: 'EXECUTE', execution_authorized: true,
      waiting_reasons: [], rejection_reasons: [], gate_policy_decision: 'ALLOW'
    }
  },
  market_statuses: { XAUUSD: { status: 'OPEN', countdown_formatted: '2h' } },
  timestamp: '2026-09-15 01:00:00'
};

/* ── Calendar fixture ─────────────────────────────────────────────────────── */
/* The payload's own `timestamp` is the anchor the controller uses to correct
   for clock skew, and each event's `timestamp_iso` is absolute. Pinning the
   payload clock means every countdown below is deterministic regardless of
   when the harness runs: serverNow == 2026-09-15T00:00:00Z exactly.

   Expected labels, from the controller's own rules (live window is -5min to
   +15min, matching jarvis/market/news.py):
     live (rem -300)  -> "T+5m 0s"     phase live
     live (rem +120)  -> "T-2m 0s"     phase live
     past (rem -3600) -> "1h 0m ago"   phase past
     soon (rem +1800) -> "in 30m 0s"   phase soon
     later(rem +9000) -> "in 2h 30m"   phase upcoming                */
const NEWS_PAYLOAD = {
  timestamp: '2026-09-15T00:00:00+00:00',
  news: [
    {
      event: 'US Crude Oil Inventories', currency: 'USD', impact: 'HIGH',
      timestamp_iso: '2026-09-14T23:55:00+00:00', diff_seconds: -300,
      time_ist: 'Mon Sep 14, 11:55 PM IST', time_utc: 'Sep 14, 23:55 UTC',
      is_live: true, is_upcoming: false, is_past: false,
      status_badge: 'LIVE', forecast: '-1.2M', previous: '-0.8M', actual: '—',
      affected_pairs: ['XAUUSD', 'WTI'], category: 'Energy',
      description: 'Weekly inventory print.', impact_analysis: 'Oil-sensitive.',
      deviation_summary: 'Pending', direction_bias: 'NEUTRAL',
      execution_warning: 'Spreads widen.', shock_alert: 'Live window active.',
      shock_risk: 'EXTREME', is_most_recent: false
    },
    {
      event: 'US CPI (y/y)', currency: 'USD', impact: 'HIGH',
      timestamp_iso: '2026-09-15T00:02:00+00:00', diff_seconds: 120,
      time_ist: 'Tue Sep 15, 12:02 AM IST', time_utc: 'Sep 15, 00:02 UTC',
      is_live: true, is_upcoming: false, is_past: false,
      status_badge: 'LIVE', forecast: '3.1%', previous: '3.4%', actual: '—',
      affected_pairs: ['EURUSD'], category: 'Inflation',
      description: 'Headline inflation.', impact_analysis: 'Rate path.',
      deviation_summary: 'Pending', direction_bias: 'NEUTRAL',
      execution_warning: 'Spreads widen.', shock_alert: 'Live window active.',
      shock_risk: 'EXTREME', is_most_recent: false
    },
    {
      event: 'US Dallas Fed Manufacturing', currency: 'USD', impact: 'MEDIUM',
      timestamp_iso: '2026-09-14T23:00:00+00:00', diff_seconds: -3600,
      time_ist: 'Mon Sep 14, 11:00 PM IST', time_utc: 'Sep 14, 23:00 UTC',
      is_live: false, is_upcoming: false, is_past: true,
      status_badge: 'LATEST RELEASE', forecast: '-12.0', previous: '-13.5', actual: '-11.0',
      affected_pairs: ['USDJPY'], category: 'Manufacturing',
      description: 'Regional survey.', impact_analysis: 'Second tier.',
      deviation_summary: 'In line', direction_bias: 'NEUTRAL',
      execution_warning: '', shock_alert: 'Window closed.', shock_risk: 'MODERATE',
      is_most_recent: true
    },
    {
      event: 'US CB Consumer Confidence', currency: 'USD', impact: 'HIGH',
      timestamp_iso: '2026-09-15T00:30:00+00:00', diff_seconds: 1800,
      time_ist: 'Tue Sep 15, 12:30 AM IST', time_utc: 'Sep 15, 00:30 UTC',
      is_live: false, is_upcoming: true, is_past: false,
      status_badge: 'IN 30m', forecast: '104.5', previous: '103.2', actual: '—',
      affected_pairs: ['XAUUSD', 'EURUSD'], category: 'Sentiment',
      description: 'Consumer survey.', impact_analysis: 'High liquidity catalyst.',
      deviation_summary: 'Pending', direction_bias: 'NEUTRAL',
      execution_warning: '', shock_alert: '', shock_risk: 'HIGH', is_most_recent: false
    },
    {
      event: 'US Preliminary GDP (q/q)', currency: 'EUR', impact: 'LOW',
      timestamp_iso: '2026-09-15T02:30:00+00:00', diff_seconds: 9000,
      time_ist: 'Tue Sep 15, 02:30 AM IST', time_utc: 'Sep 15, 02:30 UTC',
      is_live: false, is_upcoming: true, is_past: false,
      status_badge: 'IN 2h 30m', forecast: '2.1%', previous: '2.0%', actual: '—',
      affected_pairs: ['EURUSD'], category: 'Growth',
      description: 'Growth print.', impact_analysis: 'Second tier.',
      deviation_summary: 'Pending', direction_bias: 'NEUTRAL',
      execution_warning: '', shock_alert: '', shock_risk: 'MODERATE', is_most_recent: false
    }
  ]
};

/* ── Screener fixture: one real analysis and one failed-analysis row ─────── */
/* The fallback row's placeholder values are deliberately distinctive so the
   assertions can prove they are suppressed rather than merely reformatted. */
const SCREENER_PAYLOAD = {
  count: 2,
  total_universe: 2,
  fallback_count: 1,
  provenance: { analysis: 'partial' },
  timeframe: '1D',
  filters: {},
  ai_recommended_buys: [],
  stocks: [
    {
      symbol: 'NVDA', name: 'NVIDIA', sector: 'Semiconductors', industry: 'Chips',
      market: 'US_EQUITIES', market_cap: '$3.1T', price: 178.42, change_val: 4.1,
      change_pct: 2.35, volume: 41000000, rvol: 1.42, breakout_probability: 78,
      confidence: 0.81, setup_grade: 'GRADE A', grade_badge: 'A',
      timing_badge: 'UPCOMING', trend_bias: 'BULLISH', recommendation: 'BUY NOW',
      risk_level: 'MODERATE', cmf_20: 0.22, entry_zone: 178.9, stop_loss: 171.2,
      take_profit_2: 194.5, risk_reward: 2.8, rsi: 61.4, tags: [],
      analysis_source: 'computed', data_source: 'live'
    },
    {
      symbol: 'ZZZZ', name: 'Placeholder Corp', sector: 'Technology', industry: 'General',
      market: 'US_EQUITIES', market_cap: '$10.0B', price: 100.0, change_val: 0,
      change_pct: 0, volume: 1000000, rvol: 1, breakout_probability: 50,
      confidence: 0.85, setup_grade: 'GRADE B', grade_badge: 'B',
      timing_badge: 'UPCOMING', trend_bias: 'BULLISH', recommendation: 'WATCH',
      risk_level: 'MODERATE', cmf_20: 0, entry_zone: 100, stop_loss: 96,
      take_profit_2: 108, risk_reward: 2, rsi: 50, tags: [],
      analysis_source: 'fallback', data_source: 'profile_reference',
      analysis_note: 'Analysis did not complete for this symbol.'
    }
  ],
  timestamp: '2026-09-15T00:00:00+00:00'
};

const HEATMAP_PAYLOAD = {
  count: 2,
  sectors: [
    {
      sector: 'Semiconductors', count: 2, avg_change_pct: 2.35, avg_cmf: 0.22,
      avg_probability: 78, rotation_status: 'LEADING_INFLOW',
      top_leader_symbol: 'NVDA', top_leader_change: 2.35,
      top_breakout_symbol: 'NVDA', top_breakout_prob: 78, stocks: []
    },
    {
      sector: 'Energy', count: 2, avg_change_pct: -1.8, avg_cmf: -0.1,
      avg_probability: 41, rotation_status: 'OUTFLOW_DEFENSIVE',
      top_leader_symbol: 'XOM', top_leader_change: -1.2,
      top_breakout_symbol: 'CVX', top_breakout_prob: 44, stocks: []
    }
  ]
};

const INDIA_INDICES_PAYLOAD = {
  indices: [
    {
      symbol: 'NIFTY', name: 'Nifty 50', price: 25120.4, change_pct: 0.62,
      change_val: 154.2, cpr_classification: 'NARROW_CPR', cpr_label: 'NARROW',
      camarilla_h4: 25310.0, camarilla_l4: 24930.0, vwap: 25080.5,
      bias: 'BULLISH', data_source: 'calibrated_feed'
    },
    {
      symbol: 'BANKNIFTY', name: 'Bank Nifty', price: 56140.8, change_pct: -0.31,
      change_val: -174.6, cpr_classification: 'WIDE_CPR', cpr_label: 'WIDE',
      camarilla_h4: 56600.0, camarilla_l4: 55700.0, vwap: 56210.0,
      bias: 'BEARISH', data_source: 'profile_reference'
    }
  ]
};

const INDIA_FII_PAYLOAD = {
  date: '15-Sep-2026',
  data_source: 'sample',
  data_source_note: 'Fixed sample values. No live FII/DII feed is connected.',
  fii_cash_net_cr: 1845.5, dii_cash_net_cr: 2410.2,
  total_net_institutional_cr: 4255.7, fii_index_futures_long_pct: 68.5,
  fii_index_options_pcr: 1.22, fii_sentiment: 'NET_BUYERS',
  dii_sentiment: 'STRONG_DOMESTIC_INFLOWS', institutional_bias: 'STRONG_BULLISH_SUPPORT'
};

const INDIA_OPTION_CHAIN_PAYLOAD = {
  symbol: 'NIFTY', name: 'Nifty 50', data_source: 'synthetic',
  spot_price: 25120.4, atm_strike: 25100, strike_step: 50, lot_size: 25,
  freeze_limit: 1800, expiry: '25-Sep-2026', expiry_schedule: {},
  max_pain_strike: 25100,
  pcr: { pcr_oi: 1.08, pcr_volume: 0.94, total_call_oi: 1200000,
         total_put_oi: 1296000, sentiment: 'MILD_BULLISH', bias_badge: 'BULLISH' },
  atm_straddle: { strike: 25100, call_ltp: 180, put_ltp: 165, combined_premium: 345,
                  upper_breakeven: 25445, lower_breakeven: 24755,
                  expected_move_pct: 1.37 },
  iv_rank: 42.7,
  chain: [
    { strike: 25000, is_atm: false, call: { oi: 90000, ltp: 260, iv: 14.2 },
      put: { oi: 130000, ltp: 140, iv: 13.8 } },
    { strike: 25050, is_atm: false, call: { oi: 110000, ltp: 220, iv: 13.9 },
      put: { oi: 150000, ltp: 170, iv: 13.5 } },
    { strike: 25100, is_atm: true, call: { oi: 180000, ltp: 180, iv: 13.4 },
      put: { oi: 210000, ltp: 165, iv: 13.1 } },
    { strike: 25150, is_atm: false, call: { oi: 160000, ltp: 145, iv: 13.6 },
      put: { oi: 120000, ltp: 205, iv: 13.3 } },
    { strike: 25200, is_atm: false, call: { oi: 140000, ltp: 112, iv: 14.0 },
      put: { oi: 95000, ltp: 250, iv: 13.7 } }
  ],
  gex: {}
};

/* ── Backtest fixtures ──────────────────────────────────────────────────────
   The optimiser's report is per *trading style*, not per symbol: one row per
   mode carrying the pooled in-sample / out-of-sample split, plus a `series`
   list describing what data each symbol-mode contributed. That is the shape
   `jarvis/backtesting/optimizer.py` actually builds.

   Two things this fixture exists to catch, because both shipped as bugs:

   1. NESTING. The report arrives at `payload.job.result`, never at the top
      level. The reader that shipped before the fix looked only at
      `payload.report || payload.result`, found nothing, and rendered
      "No results in this job" on a run that had produced a perfectly good
      report. So the fixture below is deliberately nested one level deeper than
      the old reader looked — if someone reintroduces that lookup, the checks
      on the table content go red instead of silently passing.

   2. EMPTINESS. A job that finished with no report body must still say so
      explicitly rather than render a blank panel, so a second job carries an
      empty result and is driven through the same click path. */
const BACKTEST_META = {
  orchestrator_attached: true,
  objectives: ['expectancy', 'profit_factor'],
  styles: ['SCALP', 'SWING', 'POSITION'],
  default_space: { tp_r: [1.0, 2.0, 2.5], be_trigger_r: [null, 1.0], trail_atr: [null, 1.5] }
};

const BACKTEST_JOBS = [
  { job_id: 'bt-real', label: 'SWING · H1 · 20 symbols', status: 'DONE',
    progress_lines: 12, started_utc: '2026-09-15T00:00:00Z' },
  { job_id: 'bt-empty', label: 'SCALP · M15 · no body', status: 'DONE',
    progress_lines: 3, started_utc: '2026-09-15T00:00:00Z' }
];

const BACKTEST_MODES = [
  { style: 'SWING', primary_timeframe: 'H1', series_count: 20,
    best_geometry: { tp_r: 2.5, be_trigger_r: 1.0, fast_cash_r: null, trail_atr: 1.5 },
    best_geometry_key: 'tp_r=2.5|be_trigger_r=1.0|fast_cash_r=null|trail_atr=1.5',
    feasible: true,
    in_sample: { trades: 1204, expectancy_r: 0.0871 },
    out_of_sample: { trades: 402, expectancy_r: 0.0412 },
    full_window: { profit_factor: 1.318, max_dd_r: 12.47 },
    walk_forward: { generalises: true },
    per_symbol: [], symbols_positive: 12, symbols_total: 20 },
  { style: 'SCALP', primary_timeframe: 'M15', series_count: 20,
    best_geometry: { tp_r: 1.0, be_trigger_r: null, fast_cash_r: 0.5, trail_atr: null },
    best_geometry_key: 'tp_r=1.0|be_trigger_r=null|fast_cash_r=0.5|trail_atr=null',
    feasible: true,
    in_sample: { trades: 4811, expectancy_r: 0.0224 },
    out_of_sample: { trades: 1602, expectancy_r: -0.0138 },
    full_window: { profit_factor: 0.994, max_dd_r: 38.9 },
    walk_forward: { generalises: false },
    per_symbol: [], symbols_positive: 8, symbols_total: 20 },
  { style: 'POSITION', primary_timeframe: 'D1', series_count: 20,
    best_geometry: null, best_geometry_key: '',
    feasible: false,
    in_sample: { trades: 96, expectancy_r: -0.0302 },
    out_of_sample: { trades: 31, expectancy_r: -0.0611 },
    full_window: { profit_factor: 0.842, max_dd_r: 21.05 },
    walk_forward: { generalises: false },
    per_symbol: [], symbols_positive: 6, symbols_total: 20 }
];

const BACKTEST_SERIES = [
  { symbol: 'XAUUSD', style: 'SWING', timeframe: 'H1', bars: 8731, candidates: 4210 },
  { symbol: 'EURUSD', style: 'SWING', timeframe: 'H1', bars: 8790, candidates: 3985 }
];

const BACKTEST_RESULT = {
  status: 'OK',
  job: {
    job_id: 'bt-real',
    status: 'DONE',
    result: {
      spec: { objective: 'expectancy', modes: ['SCALP', 'SWING', 'POSITION'] },
      elapsed_seconds: 41.7,
      evaluations: 1920,
      cache_hit_rate: 0.38,
      modes: BACKTEST_MODES,
      series: BACKTEST_SERIES
    }
  }
};

/* The same job, finished, with no report body at all. */
const BACKTEST_RESULT_EMPTY = {
  status: 'OK',
  job: { job_id: 'bt-empty', status: 'DONE', result: {} }
};

/* The reader that shipped before the fix, reproduced here purely as a control.
   It must find nothing in BACKTEST_RESULT — which is precisely why a run with a
   real report rendered as "No results in this job". Keeping it in the harness
   makes the fixture's discriminating power explicit rather than assumed: if the
   nesting in the fixture ever drifts back to a shape the old reader accepted,
   the control stops returning null and the check below tells us the test has
   quietly stopped testing anything. */
function preFixReportReader(payload) {
  const r = (payload && (payload.report || payload.result)) || {};
  return r.per_symbol || r.symbols || null;
}

const fetchCalls = [];
function fetchStub(url) {
  fetchCalls.push(url);
  let body = {};
  if (url.indexOf('/api/candles') >= 0) {
    body = { symbol: 'XAUUSD', timeframe: 'H1', candles: buildCandles() };
  } else if (url.indexOf('/api/telemetry_state') >= 0) {
    body = TELEMETRY;
  } else if (url.indexOf('/api/news') >= 0) {
    body = NEWS_PAYLOAD;
  } else if (url.indexOf('/api/stocks/screener') >= 0) {
    body = SCREENER_PAYLOAD;
  } else if (url.indexOf('/api/stocks/heatmap') >= 0) {
    body = HEATMAP_PAYLOAD;
  } else if (url.indexOf('/api/india/indices') >= 0) {
    body = INDIA_INDICES_PAYLOAD;
  } else if (url.indexOf('/api/india/fii_dii') >= 0) {
    body = INDIA_FII_PAYLOAD;
  } else if (url.indexOf('/api/india/option_chain') >= 0) {
    body = INDIA_OPTION_CHAIN_PAYLOAD;
  } else if (url.indexOf('/api/pending_orders') >= 0) {
    body = [{
      ticket: 90001, symbol: 'XAUUSD', type: 2, volume: 0.20,
      price: 98.5, sl: 96, tp: 104, comment: 'limit', time_setup: 1700000000
    }];
  } else if (url.indexOf('/api/intelligence/reliability') >= 0) {
    body = { status: 'OK', styles: [] };
  } else if (url.indexOf('/api/backtest/meta') >= 0) {
    body = BACKTEST_META;
  } else if (url.indexOf('/api/backtest/jobs/') >= 0 && url.indexOf('/result') >= 0) {
    // `/jobs/<id>/result` — the report body.
    body = url.indexOf('bt-empty') >= 0 ? BACKTEST_RESULT_EMPTY : BACKTEST_RESULT;
  } else if (url.indexOf('/api/backtest/jobs/') >= 0) {
    // `/jobs/<id>` — the poll. Both fixtures are DONE, so the controller's DONE
    // branch runs and fetches the result, which is the path under test.
    const id = url.split('/api/backtest/jobs/')[1].split(/[?#]/)[0];
    body = { status: 'OK', job: { job_id: id, status: 'DONE', progress: 100,
                                  progress_lines: ['done'] } };
  } else if (url.indexOf('/api/backtest/jobs') >= 0) {
    body = { status: 'OK', jobs: BACKTEST_JOBS };
  } else if (url.indexOf('/api/backtest/') >= 0) {
    body = { status: 'OK', jobs: [] };
  }
  // apiRequest() reads the body with resp.text() and parses it itself, so the
  // stub must expose text() — a json()-only stub makes every call look failed.
  return Promise.resolve({
    ok: true,
    status: 200,
    text: () => Promise.resolve(JSON.stringify(body)),
    json: () => Promise.resolve(body)
  });
}

/* ── Run the module ───────────────────────────────────────────────────────── */
/* A controllable clock. The controller corrects for skew against the payload's
   own timestamp, so with a fixed clock every countdown is deterministic; and by
   advancing the clock we can prove a live release becomes past and the
   "next release" panel promotes the following event, which is the one piece of
   real time-dependent logic in the calendar. */
let fakeNow = Date.parse('2026-09-15T00:00:00Z');
const RealDate = Date;
class FakeDate extends RealDate {
  constructor(...args) {
    if (args.length === 0) super(fakeNow);
    else super(...args);
  }
  static now() { return fakeNow; }
}

/* setInterval is captured rather than ignored so the per-second calendar tick
   can be invoked from the harness. */
const intervals = [];

/* Every element the controller has bound a handler to, so clicks and changes
   can be driven from here. */
linkTree();

const sandbox = {
  console,
  document: documentStub,
  window: {},
  fetch: fetchStub,
  setTimeout: () => 0,
  clearTimeout: () => {},
  setInterval: (fn, ms) => { intervals.push({ fn, ms }); return intervals.length; },
  clearInterval: () => {},
  requestAnimationFrame: () => 0,
  AbortController: function () { this.signal = {}; this.abort = () => {}; },
  ResizeObserver: function () { this.observe = () => {}; this.disconnect = () => {}; },
  LightweightCharts,
  Date: FakeDate, Math, JSON, Number, String, Array, Object, Promise,
  isNaN, isFinite, RegExp, Set, Map
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;

const source = fs.readFileSync(JS, 'utf8');
vm.createContext(sandbox);
vm.runInContext(source, sandbox, { filename: 'dashboard.js' });

/* ── Drive the new views through their real wiring ──────────────────────────
   The keyboard shortcut is the same path a user takes: it calls setView(),
   which is what loads each view's panels. Calling the renderers directly would
   prove nothing about whether the wiring works. */
const wiring = {
  keydowns: 0,
  tvScriptsBeforeClick: documentStub.head.children.length,
  tvScriptsAfterClick: null,
  tvLoadingHtml: null,
  tvErrorHtml: null,
  tvWidgetOpts: null,
  tvSecondClick: 0,
  btRows: 0,
  btRowClicks: 0,
  btMetaHtml: null,
  btResultsHtml: null,
  btResultsState: null,
  btResultMeta: null,
  btEmptyHtml: null,
  btEmptyState: null
};

function driveViews() {
  fireDocument('keydown', { key: '2', target: { tagName: 'DIV' } });   // news
  fireDocument('keydown', { key: '3', target: { tagName: 'DIV' } });   // analyst
  fireDocument('keydown', { key: '4', target: { tagName: 'DIV' } });   // markets
  wiring.keydowns = 3;
}

function driveTradingView() {
  const tvBtn = registry.get('chart-src-tv');
  if (!tvBtn) return;
  tvBtn.fire('click');
  wiring.tvScriptsAfterClick = documentStub.head.children.length;
  wiring.tvLoadingHtml = deepHtml(registry.get('chart-tv'));

  // Simulate the CDN being blocked.
  const script = documentStub.head.children[documentStub.head.children.length - 1];
  if (script && typeof script.onerror === 'function') script.onerror();
}

/* ── Backtest ───────────────────────────────────────────────────────────────
   The whole point of this suite is that it drives the REAL wiring rather than
   calling renderers directly, so the backtest is entered the same way a user
   enters it — the '6' shortcut, then a click on a job row. Calling
   renderBacktestResult() straight would prove the renderer works and say
   nothing about whether anything ever reaches it, which is exactly the state
   the "backtest does not work" report described. */
function driveBacktestView() {
  fireDocument('keydown', { key: '6', target: { tagName: 'DIV' } });
}

/* Click a job row by index. loadJobs() rebuilds #bt-history on every poll, so
   the rows have to be re-queried at click time rather than captured earlier. */
function clickBacktestJob(index) {
  const body = registry.get('bt-history');
  if (!body) return;
  const rows = body.querySelectorAll('tr[data-job]');
  wiring.btRows = rows.length;
  const row = rows[index];
  if (!row) return;
  wiring.btRowClicks += row.fire('click');
}

function captureBacktestResult() {
  const host = registry.get('bt-results');
  const meta = registry.get('bt-result-meta');
  wiring.btResultsHtml = deepHtml(host);
  wiring.btResultsState = host ? host.getAttribute('data-state') : 'no-element';
  wiring.btResultMeta = meta ? meta.textContent : null;
  wiring.btMetaHtml = deepHtml(registry.get('bt-history'));
}

function captureBacktestEmpty() {
  const host = registry.get('bt-results');
  wiring.btEmptyHtml = deepHtml(host);
  wiring.btEmptyState = host ? host.getAttribute('data-state') : 'no-element';
}

function readTvFailure() {
  wiring.tvErrorHtml = deepHtml(registry.get('chart-tv'));
}

function driveTvSuccess() {
  // Make the script available, then re-select the source. The controller should
  // build the widget rather than fetch again.
  sandbox.TradingView = { widget: function (o) { wiring.tvWidgetOpts = o; } };
  const tvBtn = registry.get('chart-src-tv');
  if (tvBtn) wiring.tvSecondClick = tvBtn.fire('click');
}

/* Advance the clock an hour and run the calendar's own per-second tick. Every
   event that was live must now read as past, and the "next release" panel must
   promote the following event. */
function advanceClockAndTick() {
  fakeNow += 3600 * 1000;
  intervals.filter((i) => i.ms === 1000).forEach((i) => i.fn());
}

/* ── Assertions (after the boot promise chain settles) ────────────────────── */
function report() {
  console.log('\ndiagnostics');
  console.log('  ids requested: ' + Array.from(new Set(requestedIds)).join(', '));
  console.log('  fetches: ' + fetchCalls.length + '  (' + fetchCalls.join(' | ') + ')');

  console.log('\nchart construction');
  ok('a chart was created', drawn.charts >= 1, 'charts=' + drawn.charts);
  ok('a candlestick series was added', drawn.candleSeries >= 1);
  ok('a volume series was added', drawn.volumeSeries >= 1);
  ok('candles were pushed to the series', Array.isArray(drawn.candleData) && drawn.candleData.length === 30,
    'got ' + (drawn.candleData ? drawn.candleData.length : 'null'));
  ok('volume was pushed to the series', Array.isArray(drawn.volumeData) && drawn.volumeData.length === 30,
    'got ' + (drawn.volumeData ? drawn.volumeData.length : 'null'));

  const titles = drawn.priceLines.map((l) => String(l.title || ''));

  console.log('\nsupport and resistance');
  const levelChecks = [
    ['R1', 110], ['R2', 115], ['S1', 90], ['S2', 85]
  ];
  levelChecks.forEach(([name, price]) => {
    const hit = drawn.priceLines.find((l) => String(l.title || '').indexOf(name + ':') === 0);
    ok(name + ' price line drawn at ' + price,
      !!hit && Math.abs(hit.price - price) < 1e-9,
      hit ? 'title=' + hit.title + ' price=' + hit.price : 'no line titled ' + name);
  });

  const legend = registry.get('chart-legend');
  const legendHtml = legend ? legend.innerHTML : '';
  ok('legend names all four levels',
    ['R1', 'R2', 'S1', 'S2'].every((n) => legendHtml.indexOf(n) >= 0),
    legendHtml.slice(0, 120));

  console.log('\ntrade overlays (entry / stop / target)');
  const entryLine = drawn.priceLines.find((l) => /^(BUY|SELL)\s/.test(String(l.title || '')));
  ok('entry line drawn with side, size and price in the label',
    !!entryLine && entryLine.price === 100 && /BUY 0\.10L/.test(entryLine.title),
    entryLine ? entryLine.title : 'none');
  const slLine = drawn.priceLines.find((l) => String(l.title || '').indexOf('SL') === 0);
  ok('stop loss line drawn at 95', !!slLine && slLine.price === 95, slLine ? slLine.title : 'none');
  const tpLine = drawn.priceLines.find((l) => String(l.title || '').indexOf('TP:') === 0);
  ok('take profit line drawn at 110', !!tpLine && tpLine.price === 110, tpLine ? tpLine.title : 'none');

  const hud = registry.get('chart-hud');
  const hudHtml = hud ? hud.innerHTML : '';
  ok('in-chart HUD is shown', !!hud && hud.hidden === false);
  ok('HUD carries the trade details',
    ['12345', 'BUY', '0.10L', '100', '95', '110', '12.50'].every((t) => hudHtml.indexOf(t) >= 0),
    hudHtml.replace(/\s+/g, ' ').slice(0, 200));

  console.log('\nheader');
  const price = registry.get('chart-live-price');
  ok('live price rendered from the last close', !!price && price.textContent === '100.00',
    price ? price.textContent : 'missing');

  console.log('\nscanner radar');
  const radar = registry.get('radar-body');
  const radarHtml = deepHtml(radar);
  ok('radar rendered both candidates',
    ['XAUUSD', 'EURUSD'].every((s) => radarHtml.indexOf(s) >= 0),
    radarHtml.replace(/\s+/g, ' ').slice(0, 200));
  ok('radar shows entry, stop, target, R:R, win and EV',
    ['Entry', 'SL', 'TP', 'R:R', 'Win', 'EV'].every((t) => radarHtml.indexOf(t) >= 0),
    radarHtml.replace(/\s+/g, ' ').slice(0, 240));
  const radarCards = (radar && radar.children) || [];
  const liveCards = radarCards.filter((c) => /tt-radar--live/.test(c.className || ''));
  ok('radar marks exactly the actionable candidate',
    radarCards.length === 2 && liveCards.length === 1,
    'cards=' + radarCards.length + ' live=' + liveCards.length);
  const radarCount = registry.get('radar-count');
  ok('radar count reflects the row count', !!radarCount && radarCount.textContent === '2',
    radarCount ? radarCount.textContent : 'missing');

  console.log('\npending orders');
  const pend = registry.get('pending-body');
  const pendHtml = pend ? pend.innerHTML : '';
  /* MT5's ORDER_TYPE_* enum starts at BUY=0 / SELL=1, so the pending types begin
     at 2: BUY_LIMIT. The fixture below is type 2 with comment 'limit', and the
     backend's own place_pending_order maps "BUY_LIMIT" to the literal 2. An
     earlier assertion here expected 'BUY STOP', which encoded an off-by-two in
     the frontend's map rather than catching it — every live LIMIT was labelled
     a STOP. Assert the exact label AND that the neighbouring type is absent. */
  ok('numeric MT5 order type 2 renders as BUY LIMIT',
    pendHtml.indexOf('BUY LIMIT') >= 0 && pendHtml.indexOf('BUY STOP') < 0,
    pendHtml.replace(/\s+/g, ' ').slice(0, 200));
  ok('pending order shows price, stop and target at symbol precision',
    ['98.50', '96.00', '104.00'].every((t) => pendHtml.indexOf(t) >= 0),
    pendHtml.replace(/\s+/g, ' ').slice(0, 240));
  const pendCount = registry.get('pending-count');
  ok('pending count reflects the row count', !!pendCount && pendCount.textContent === '1',
    pendCount ? pendCount.textContent : 'missing');

  console.log('\nnews calendar');
  ok('opening the view requested the calendar',
    fetchCalls.some((u) => u.indexOf('/api/news') >= 0),
    fetchCalls.filter((u) => u.indexOf('/api/news') >= 0).join(' '));

  const newsBody = registry.get('news-body');
  const newsHtml = deepHtml(newsBody);
  ok('every event in the payload rendered',
    ['US Crude Oil Inventories', 'US CPI (y/y)', 'US Dallas Fed Manufacturing',
     'US CB Consumer Confidence', 'US Preliminary GDP (q/q)']
      .every((t) => newsHtml.indexOf(t) >= 0),
    newsHtml.replace(/\s+/g, ' ').slice(0, 220));
  ok('rows carry both IST and UTC timestamps',
    newsHtml.indexOf('11:55 PM IST') >= 0 && newsHtml.indexOf('Sep 14, 23:55 UTC') >= 0);
  ok('panel count reports the event count',
    (registry.get('news-count') || {}).textContent === '5',
    (registry.get('news-count') || {}).textContent);

  /* The exact labels prove the countdown arithmetic, the phase boundaries and
     the skew correction in one shot: a wrong window edge or an off-by-one
     would show up as a different string. */
  const cdCells = selectAll(newsBody, '[data-cd]');
  const cd = cdCells.map((c) => {
    const v = c.querySelector('[data-cd-val]');
    return c.getAttribute('data-phase') + ':' + (v ? v.textContent : '?');
  });
  ok('five countdown cells rendered', cdCells.length === 5, 'cells=' + cdCells.length);
  ok('live release counts up from the open of its window (T+5m 0s)',
    cd.indexOf('live:T+5m 0s') >= 0, cd.join(' | '));
  ok('live release still ahead counts down (T\u22122m 0s)',
    cd.indexOf('live:T\u22122m 0s') >= 0, cd.join(' | '));
  ok('release past the 15-minute window reads as released (1h 0m ago)',
    cd.indexOf('past:1h 0m ago') >= 0, cd.join(' | '));
  ok('release 30 minutes out is imminent (in 30m 0s)',
    cd.indexOf('soon:in 30m 0s') >= 0, cd.join(' | '));
  ok('release hours out is upcoming (in 2h 30m)',
    cd.indexOf('upcoming:in 2h 30m') >= 0, cd.join(' | '));

  ok('the live chip names the release inside its window',
    (registry.get('news-live-chip') || {}).textContent === 'LIVE · USD US Crude Oil Inventories',
    (registry.get('news-live-chip') || {}).textContent);
  ok('the currency filter was built from the payload, not hard-coded',
    (function () {
      const sel = registry.get('news-currency');
      const opts = (sel && sel.children) || [];
      const values = opts.map((o) => o.value).sort().join(',');
      return values === 'ALL,EUR,USD';
    })(), 'options=' + ((registry.get('news-currency') || {}).children || []).length);

  const heroBefore = deepHtml(registry.get('news-hero'));
  ok('the next-release panel features a live event over an upcoming one',
    heroBefore.indexOf('US Crude Oil Inventories') >= 0,
    heroBefore.replace(/\s+/g, ' ').slice(0, 160));

  console.log('\nnews detail panel');
  const detailBefore = deepHtml(registry.get('news-detail'));
  ok('no event is selected until one is clicked',
    detailBefore.indexOf('No event selected') >= 0,
    detailBefore.replace(/\s+/g, ' ').slice(0, 120));
  const firstRow = selectAll(newsBody, 'tr[data-key]')[0];
  if (firstRow) firstRow.fire('click');
  const detailAfter = deepHtml(registry.get('news-detail'));
  ok('clicking a row opens its impact analysis',
    detailAfter.indexOf('US Crude Oil Inventories') >= 0 &&
    detailAfter.indexOf('Oil-sensitive.') >= 0,
    detailAfter.replace(/\s+/g, ' ').slice(0, 200));
  ok('the selected row is marked',
    !!firstRow && firstRow.getAttribute('data-selected') === 'true');

  /* Advance the clock an hour and run the panel's own per-second tick. Every
     live release must become past, and the next-release panel must promote the
     following event — this is the behaviour a stale server-computed badge
     cannot provide. */
  advanceClockAndTick();
  const cdAfter = selectAll(newsBody, '[data-cd]').map((c) => {
    const v = c.querySelector('[data-cd-val]');
    return c.getAttribute('data-phase') + ':' + (v ? v.textContent : '?');
  });
  ok('the tick flipped every live release to past',
    cdAfter.indexOf('live:') < 0 && cdAfter.filter((s) => s.indexOf('past:') === 0).length === 4,
    cdAfter.join(' | '));
  ok('the tick recomputed the countdown from each event\u2019s own timestamp',
    cdAfter.indexOf('past:1h 5m ago') >= 0, cdAfter.join(' | '));
  ok('the tick advanced the upcoming release (in 1h 30m)',
    cdAfter.indexOf('upcoming:in 1h 30m') >= 0, cdAfter.join(' | '));
  const heroAfter = deepHtml(registry.get('news-hero'));
  ok('the next-release panel promoted the following event',
    heroAfter.indexOf('US Preliminary GDP (q/q)') >= 0 &&
    heroAfter.indexOf('US Crude Oil Inventories') < 0,
    heroAfter.replace(/\s+/g, ' ').slice(0, 160));
  ok('the live chip cleared once nothing was in its window',
    (registry.get('news-live-chip') || {}).textContent === 'no release live',
    (registry.get('news-live-chip') || {}).textContent);

  console.log('\ndevil\u2019s advocate');
  ok('opening the view rendered the selected symbol',
    (registry.get('da-symbol') || {}).textContent === 'XAUUSD',
    (registry.get('da-symbol') || {}).textContent);
  ok('a blocked setup is labelled as blocked',
    (registry.get('da-verdict') || {}).textContent === 'BLOCKED',
    (registry.get('da-verdict') || {}).textContent);

  const gauge = selectAll(registry.get('da-metrics'), '.tt-gauge')[0];
  const gaugeFill = gauge ? gauge.querySelector('.tt-gauge__fill') : null;
  ok('the penalty gauge is drawn against the engine\u2019s own 0-50 scale',
    !!gaugeFill && gaugeFill.style.width === '60.0%',
    gaugeFill ? gaugeFill.style.width : 'no fill');
  ok('a penalty above 25 is banded as severe',
    !!gauge && /tt-gauge--down/.test(gauge.className), gauge ? gauge.className : 'none');

  const bullHtml = deepHtml(registry.get('da-bull'));
  const bearHtml = deepHtml(registry.get('da-bear'));
  ok('the bull case lists the engine\u2019s supporting evidence',
    bullHtml.indexOf('H4 structure in premium rejection') >= 0, bullHtml.slice(0, 140));
  ok('the bear case lists the engine\u2019s contrary evidence',
    bearHtml.indexOf('RSI divergence on H1') >= 0, bearHtml.slice(0, 140));
  ok('threat vectors are shown',
    deepHtml(registry.get('da-threats')).indexOf('Event risk inside 6h') >= 0);
  ok('invalidation levels are shown',
    deepHtml(registry.get('da-invalidation')).indexOf('H4 close below 94.20') >= 0);
  ok('objections list both waiting and rejection reasons',
    (function () {
      const h = deepHtml(registry.get('da-objections'));
      return h.indexOf('Waiting for H1 close above 100.40') >= 0 &&
             h.indexOf('Adversarial penalty above tolerance') >= 0;
    })(), deepHtml(registry.get('da-objections')).replace(/\s+/g, ' ').slice(0, 200));

  console.log('\nquality gate');
  ok('the gate reports how many checks passed',
    (registry.get('gate-count') || {}).textContent === '3 / 5',
    (registry.get('gate-count') || {}).textContent);
  ok('a failed gate is labelled as blocked',
    (registry.get('gate-verdict') || {}).textContent === 'BLOCKED',
    (registry.get('gate-verdict') || {}).textContent);
  const gateCells = selectAll(registry.get('gate-body'), '.tt-gate__cell');
  ok('every check rendered', gateCells.length === 5, 'cells=' + gateCells.length);
  ok('failures are listed before passes',
    gateCells.length === 5 &&
    gateCells.slice(0, 2).every((c) => /--fail/.test(c.className)) &&
    gateCells.slice(2).every((c) => /--pass/.test(c.className)),
    gateCells.map((c) => (/--fail/.test(c.className) ? 'F' : 'P')).join(''));

  console.log('\nglobal equities');
  ok('opening the view requested the screener',
    fetchCalls.some((u) => u.indexOf('/api/stocks/screener') >= 0));
  const eqHtml = deepHtml(registry.get('eq-body'));
  ok('a computed row shows its real analysis',
    ['NVDA', '178.42', '+2.35%', '78%', 'BULLISH', '178.90', '2.80R']
      .every((t) => eqHtml.indexOf(t) >= 0),
    eqHtml.replace(/\s+/g, ' ').slice(0, 260));

  /* The honesty guarantee: a row whose analysis failed must not present the
     backend's placeholder setup as if it were a computed one. */
  ok('a failed-analysis row is rendered as having no analysis',
    eqHtml.indexOf('no analysis') >= 0, eqHtml.replace(/\s+/g, ' ').slice(0, 260));
  ok('a failed-analysis row does NOT show the placeholder grade',
    eqHtml.indexOf('GRADE B') < 0 && eqHtml.indexOf('>B<') < 0,
    eqHtml.replace(/\s+/g, ' ').slice(0, 260));
  ok('a failed-analysis row does NOT show the placeholder probability',
    eqHtml.indexOf('50%') < 0);
  ok('a failed-analysis row does NOT show the placeholder entry or target',
    eqHtml.indexOf('96.00') < 0 && eqHtml.indexOf('108.00') < 0);
  ok('a failed-analysis row marks its price as a reference, not a quote',
    eqHtml.indexOf('100.00 ref') >= 0, eqHtml.replace(/\s+/g, ' ').slice(0, 260));
  ok('the panel flags how many rows are placeholders',
    (registry.get('eq-prov') || {}).textContent === '1 placeholder',
    (registry.get('eq-prov') || {}).textContent);

  const heatHtml = deepHtml(registry.get('eq-heatmap'));
  ok('sector tiles rendered with their average change',
    ['Semiconductors', '+2.35%', 'Energy', '-1.80%'].every((t) => heatHtml.indexOf(t) >= 0),
    heatHtml.replace(/\s+/g, ' ').slice(0, 200));
  ok('heat tiles are banded by direction',
    (function () {
      const tiles = selectAll(registry.get('eq-heatmap'), '.tt-heattile');
      return tiles.length === 2 &&
        /tt-heat-[1-4]/.test(tiles[0].className) &&
        /tt-heat-neg-[1-4]/.test(tiles[1].className);
    })());

  console.log('\nindia');
  const idxHtml = deepHtml(registry.get('in-indices'));
  const idxTokens = ['NIFTY', '25,120.40', '+0.62%', 'NARROW', 'BANKNIFTY', 'WIDE'];
  const idxMissing = idxTokens.filter((t) => idxHtml.indexOf(t) < 0);
  ok('Indian indices rendered with level, change and CPR classification',
    idxMissing.length === 0,
    idxMissing.length ? 'missing: ' + idxMissing.join(', ') : '');
  ok('the indices panel reports the weakest source among its rows',
    (registry.get('in-index-prov') || {}).textContent === 'reference',
    (registry.get('in-index-prov') || {}).textContent);

  const fiiHtml = deepHtml(registry.get('in-fii'));
  ok('the flow panel renders its values',
    ['1,845.50', '2,410.20', '4,255.70', '68.5%'].every((t) => fiiHtml.indexOf(t) >= 0),
    fiiHtml.replace(/\s+/g, ' ').slice(0, 260));
  ok('the flow panel is labelled as sample data, not live',
    (registry.get('in-fii-prov') || {}).textContent === 'sample',
    (registry.get('in-fii-prov') || {}).textContent);
  ok('the flow panel prints the backend\u2019s provenance note in full',
    fiiHtml.indexOf('No live FII/DII feed is connected') >= 0);

  const ocHtml = deepHtml(registry.get('in-optionchain'));
  ok('the option chain rendered its aggregates and ladder',
    ['25,120.40', '25,100', '25-Sep-2026', '1.08', '345.00', '1.37%', 'MAX PAIN']
      .every((t) => ocHtml.indexOf(t) >= 0),
    ocHtml.replace(/\s+/g, ' ').slice(0, 300));
  ok('the ATM row is marked',
    selectAll(registry.get('in-optionchain'), '.tt-oc__row--atm').length === 1);
  ok('the chain is labelled as modelled when the feed is not live',
    (registry.get('in-oc-prov') || {}).textContent === 'modelled',
    (registry.get('in-oc-prov') || {}).textContent);
  ok('the modelled chain carries a warning above its numbers',
    ocHtml.indexOf('not read from the NSE chain') >= 0);
  ok('the randomised IV rank is labelled as modelled',
    ocHtml.indexOf('IV rank (modelled)') >= 0,
    ocHtml.replace(/\s+/g, ' ').slice(0, 200));

  console.log('\ntradingview chart source');
  ok('the external widget script is NOT fetched on page load',
    wiring.tvScriptsBeforeClick === 0, 'scripts=' + wiring.tvScriptsBeforeClick);
  ok('selecting the source fetches it exactly once',
    wiring.tvScriptsAfterClick === 1, 'scripts=' + wiring.tvScriptsAfterClick);
  ok('a loading state is shown while the script is in flight',
    String(wiring.tvLoadingHtml || '').indexOf('Loading TradingView') >= 0,
    String(wiring.tvLoadingHtml || '').replace(/\s+/g, ' ').slice(0, 140));
  ok('a blocked CDN renders an explicit failure state, not an empty box',
    String(wiring.tvErrorHtml || '').indexOf('TradingView unavailable') >= 0,
    String(wiring.tvErrorHtml || '').replace(/\s+/g, ' ').slice(0, 160));
  ok('the failure state points at the native chart',
    String(wiring.tvErrorHtml || '').indexOf('native chart is unaffected') >= 0);
  ok('the widget is built once the script is available',
    !!wiring.tvWidgetOpts, 'second click handlers=' + wiring.tvSecondClick);
  ok('the widget is asked for the mapped ticker and interval',
    !!wiring.tvWidgetOpts &&
    wiring.tvWidgetOpts.symbol === 'OANDA:XAUUSD' &&
    wiring.tvWidgetOpts.interval === '60',
    wiring.tvWidgetOpts ? wiring.tvWidgetOpts.symbol + ' @ ' + wiring.tvWidgetOpts.interval : 'none');
  ok('the resolved ticker is printed so a wrong mapping is visible',
    String(wiring.tvLoadingHtml || '').length >= 0 &&
    (function () {
      const host = registry.get('chart-tv');
      return deepHtml(host).indexOf('OANDA:XAUUSD') >= 0;
    })(), deepHtml(registry.get('chart-tv')).replace(/\s+/g, ' ').slice(0, 160));

  console.log('\nbacktest report');
  const btMeta = String(wiring.btMetaHtml || '');
  ok('the job list is fetched and rendered as rows, not the empty state',
    wiring.btRows === 2, 'rows=' + wiring.btRows);
  ok('a job row carries the job id the click handler reads',
    btMeta.indexOf('data-job="bt-real"') >= 0,
    btMeta.replace(/\s+/g, ' ').slice(0, 160));
  ok('a done job reports completion rather than a progress count',
    btMeta.indexOf('done') >= 0, btMeta.replace(/\s+/g, ' ').slice(0, 160));
  ok('clicking a job row is actually wired to a handler',
    wiring.btRowClicks >= 1, 'handler fires=' + wiring.btRowClicks);

  const btHtml = String(wiring.btResultsHtml || '');
  const btFlat = btHtml.replace(/\s+/g, ' ');

  // The regression that started all this: a finished run rendering as
  // "No results in this job". Assert the negative directly, so a future change
  // that re-breaks the reader fails on the symptom the user actually reported.
  ok('a finished run does NOT render the empty state',
    btHtml.indexOf('No results in this job') < 0, btFlat.slice(0, 220));
  ok('the success path clears data-state rather than leaving "empty" behind',
    wiring.btResultsState === null, 'data-state=' + wiring.btResultsState);

  // The report is per trading style. Both tables have to be driven by the
  // payload, not by any local default.
  ok('the per-style table is rendered from report.modes',
    btHtml.indexOf('Per style') >= 0, btFlat.slice(0, 220));
  ok('every mode in the payload gets a row',
    ['SWING', 'SCALP', 'POSITION'].every((s) => btHtml.indexOf(s) >= 0),
    btFlat.slice(0, 300));
  ok('the data-coverage table is rendered from report.series',
    btHtml.indexOf('Data coverage') >= 0 && btHtml.indexOf('XAUUSD') >= 0,
    btFlat.slice(0, 300));
  ok('trade counts are grouped like every other numeric column, not raw',
    btHtml.indexOf('1,204') >= 0 && btHtml.indexOf('8,731') >= 0,
    btFlat.slice(0, 300));
  ok('the out-of-sample expectancy is shown at 4 decimal places',
    btHtml.indexOf('0.0412') >= 0, btFlat.slice(0, 300));
  ok('the geometry is summarised rather than shown as a raw object',
    btHtml.indexOf('tp 2.50') >= 0 && btHtml.indexOf('be 1.00') >= 0,
    btFlat.slice(0, 300));

  // The verdict is the one derived field — it must distinguish the three cases
  // the payload encodes, not collapse them to one label.
  ok('a feasible mode that generalises reads as generalisable',
    btHtml.indexOf('generalisable') >= 0, btFlat.slice(0, 300));
  ok('a feasible mode that does NOT generalise reads as in-sample only',
    btHtml.indexOf('in-sample only') >= 0, btFlat.slice(0, 300));
  ok('an infeasible mode reads as no feasible geometry',
    btHtml.indexOf('no feasible geometry') >= 0, btFlat.slice(0, 300));
  ok('the verdict is carried by a class, not only by text',
    /class="tt-up">generalisable/.test(btHtml) && /class="tt-down">no feasible geometry/.test(btHtml),
    btFlat.slice(0, 300));

  const btMetaLine = String(wiring.btResultMeta || '');
  ok('the result meta line counts modes and series',
    btMetaLine.indexOf('3 modes') >= 0 && btMetaLine.indexOf('2 series') >= 0,
    btMetaLine);
  ok('the result meta line reports the run cost',
    btMetaLine.indexOf('1920 evals') >= 0 && btMetaLine.indexOf('cache 38%') >= 0,
    btMetaLine);
  ok('the result meta line names the objective from the spec',
    btMetaLine.indexOf('objective expectancy') >= 0, btMetaLine);

  // The control: the pre-fix reader, run against the very payload the new
  // reader just rendered, must find nothing. That is what makes this fixture
  // able to catch the original bug at all.
  ok('the pre-fix reader finds nothing in this payload (the bug it shipped)',
    preFixReportReader(BACKTEST_RESULT) === null,
    JSON.stringify(preFixReportReader(BACKTEST_RESULT)));

  console.log('\nbacktest empty report');
  const btEmpty = String(wiring.btEmptyHtml || '');
  ok('a job with no report body says so explicitly instead of rendering blank',
    btEmpty.indexOf('No results in this job') >= 0,
    btEmpty.replace(/\s+/g, ' ').slice(0, 200));
  ok('the empty state is published on data-state so CSS can style it',
    wiring.btEmptyState === 'empty', 'data-state=' + wiring.btEmptyState);
  ok('the empty report does not leave the previous job\'s table on screen',
    btEmpty.indexOf('Per style') < 0, btEmpty.replace(/\s+/g, ' ').slice(0, 200));

  console.log('\ntemplate wiring');
  const missing = Array.from(new Set(requestedIds))
    .filter((id) => !templateIds.has(id) && !prelinked.has(id));
  ok('every id the controller queries exists in dashboard.html',
    missing.length === 0, missing.length ? 'missing: ' + missing.join(', ') : '');

  console.log('\nendpoints');
  ok('candles requested with the tf parameter',
    fetchCalls.some((u) => u.indexOf('/api/candles') >= 0 && /[?&]tf=/.test(u)),
    fetchCalls.filter((u) => u.indexOf('/api/candles') >= 0).join(' '));
  ok('candles requested with a timeframe, not the hard-coded default',
    fetchCalls.some((u) => /[?&]tf=H1/.test(u)),
    fetchCalls.filter((u) => u.indexOf('/api/candles') >= 0).join(' '));

  console.log('\n' + (failures ? failures + ' of ' + checks + ' checks FAILED' : 'all ' + checks + ' checks passed'));
  process.exit(failures ? 1 : 0);
}

/* Let the boot promise chain settle before asserting. Views are driven in
   phases because the analyst and markets panels bind to state.symbol, which is
   only populated once the first telemetry response lands. */
let ticks = 0;
function drain() {
  if (++ticks > 40) return report();
  if (ticks === 5) driveViews();
  if (ticks === 8) driveTradingView();
  if (ticks === 10) readTvFailure();
  if (ticks === 12) driveTvSuccess();
  // Back to the calendar, while the clock is still pinned to the payload's own
  // timestamp. The per-second tick early-returns for any other view, and
  // re-opening the view after advancing the clock would recompute the skew and
  // pin "now" to the new time — which is correct behaviour but would hide the
  // advancement this harness is trying to observe.
  if (ticks === 14) fireDocument('keydown', { key: '2', target: { tagName: 'DIV' } });
  // Backtest: enter the view, click the job that has a real report, capture,
  // then click the job whose report body is empty and capture again. The two
  // are deliberately separated by a tick so the promise chains settle between
  // them and the second capture cannot read the first one's DOM.
  if (ticks === 16) driveBacktestView();
  if (ticks === 18) clickBacktestJob(0);
  if (ticks === 20) captureBacktestResult();
  if (ticks === 21) clickBacktestJob(1);
  if (ticks === 23) captureBacktestEmpty();
  // Hand the view back to the calendar. tickNews() early-returns unless
  // state.view === 'news', so the backtest detour above would otherwise leave
  // the per-second tick inert and make every clock-advance check fail — which
  // reads exactly like a broken calendar. The clock has not been advanced yet
  // at this point (that happens in report()), so re-entering news recomputes
  // the same skew the tick-14 entry did and the advancement is still visible.
  if (ticks === 25) fireDocument('keydown', { key: '2', target: { tagName: 'DIV' } });
  setImmediate(drain);
}
drain();
