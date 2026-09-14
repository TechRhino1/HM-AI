/* ===========================================================================
   Headless render check for the JARVIS dashboard (tools/verify_dashboard_render.js)

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
  get innerHTML() { return this._html; }
  set innerHTML(v) { this._html = v === null || v === undefined ? '' : String(v); this.children = []; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null; }
  removeAttribute(k) { delete this.attrs[k]; }
  addEventListener() {}
  removeEventListener() {}
  appendChild(c) {
    // A real DOM moves a DocumentFragment's children into the target rather
    // than inserting the fragment itself. Emulate that, or a panel built with
    // a fragment looks like it holds one child.
    if (c && c.tagName === 'FRAGMENT') {
      const kids = c.children || [];
      for (let i = 0; i < kids.length; i++) this.children.push(kids[i]);
      c.children = [];
      return c;
    }
    this.children.push(c);
    return c;
  }
  removeChild(c) { this.children = this.children.filter((x) => x !== c); return c; }
  insertBefore(c) { this.children.push(c); return c; }
  querySelectorAll() { return []; }
  querySelector() { return null; }
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

/* Serialise an element and everything appended beneath it. Panels built with
   createElement + appendChild (the radar, the selection list) leave innerHTML
   empty on the container, so reading innerHTML alone reports a blank panel. */
function deepHtml(el) {
  if (!el) return '';
  let out = el.innerHTML || '';
  const kids = el.children || [];
  for (let i = 0; i < kids.length; i++) out += deepHtml(kids[i]);
  return out;
}

const documentStub = {
  readyState: 'complete',
  body: new El('body', 'body'),
  documentElement: new El('html', 'html'),
  getElementById: elementFor,
  createElement: (tag) => new El('', tag),
  createDocumentFragment: () => new El('', 'fragment'),
  querySelectorAll: () => [],
  querySelector: () => null,
  addEventListener: () => {},
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
      calculated_risk_percent: 1, expected_value: 0.4, adversarial_penalty: 0,
      regime: { primary: 'TREND_BULL', probabilities: {}, confidence: 0.8 },
      probabilities: {}, invalidation_levels: [], bull_case: [], bear_case: [],
      risk_factors: [], quality_gate: {}, decision: 'BUY', execution_authorized: true,
      waiting_reasons: [], rejection_reasons: [], gate_policy_decision: 'ALLOW'
    }
  },
  market_statuses: { XAUUSD: { status: 'OPEN', countdown_formatted: '2h' } },
  timestamp: '2026-09-15 01:00:00'
};

const fetchCalls = [];
function fetchStub(url) {
  fetchCalls.push(url);
  let body = {};
  if (url.indexOf('/api/candles') >= 0) {
    body = { symbol: 'XAUUSD', timeframe: 'H1', candles: buildCandles() };
  } else if (url.indexOf('/api/telemetry_state') >= 0) {
    body = TELEMETRY;
  } else if (url.indexOf('/api/pending_orders') >= 0) {
    body = [{
      ticket: 90001, symbol: 'XAUUSD', type: 2, volume: 0.20,
      price: 98.5, sl: 96, tp: 104, comment: 'limit', time_setup: 1700000000
    }];
  } else if (url.indexOf('/api/intelligence/reliability') >= 0) {
    body = { status: 'OK', styles: [] };
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
const sandbox = {
  console,
  document: documentStub,
  window: {},
  fetch: fetchStub,
  setTimeout: () => 0,
  clearTimeout: () => {},
  setInterval: () => 0,
  clearInterval: () => {},
  requestAnimationFrame: () => 0,
  AbortController: function () { this.signal = {}; this.abort = () => {}; },
  ResizeObserver: function () { this.observe = () => {}; this.disconnect = () => {}; },
  LightweightCharts,
  Date, Math, JSON, Number, String, Array, Object, Promise, isNaN, isFinite, RegExp, Set, Map
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;

const source = fs.readFileSync(JS, 'utf8');
vm.createContext(sandbox);
vm.runInContext(source, sandbox, { filename: 'dashboard.js' });

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
  ok('numeric MT5 order type is mapped to a readable name',
    pendHtml.indexOf('BUY STOP') >= 0,
    pendHtml.replace(/\s+/g, ' ').slice(0, 200));
  ok('pending order shows price, stop and target at symbol precision',
    ['98.50', '96.00', '104.00'].every((t) => pendHtml.indexOf(t) >= 0),
    pendHtml.replace(/\s+/g, ' ').slice(0, 240));
  const pendCount = registry.get('pending-count');
  ok('pending count reflects the row count', !!pendCount && pendCount.textContent === '1',
    pendCount ? pendCount.textContent : 'missing');

  console.log('\ntemplate wiring');
  const missing = Array.from(new Set(requestedIds)).filter((id) => !templateIds.has(id));
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

/* Let the boot promise chain settle before asserting. */
let ticks = 0;
function drain() {
  if (++ticks > 40) return report();
  setImmediate(drain);
}
drain();
