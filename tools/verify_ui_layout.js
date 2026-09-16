/* ===========================================================================
   Layout / responsive verifier for the JARVIS UI (tools/verify_ui_layout.js)

   WHY THIS EXISTS
   ---------------
   verify_dashboard_render.js proves the controller issues the right drawing
   calls, and verify_dashboard_nav.js proves real clicks work. Neither can see
   LAYOUT: whether a page scrolls sideways on a phone, whether a control is too
   small to tap, or whether a CSS effect silently failed to apply. Those are the
   defects a user notices first and the ones no other tool here can catch.

   This drives the real Chrome that is installed on the box through
   `puppeteer-core` (managed node workspace) and checks, per page per viewport:

     1. HORIZONTAL OVERFLOW - documentElement.scrollWidth vs clientWidth.
        A page that scrolls sideways on a phone is broken even if every element
        is individually correct, because one oversized child widens the document
        and every sibling inherits the width.
     2. TAP TARGET SIZE - primary nav/action controls must be >= 44px tall on a
        coarse pointer (WCAG 2.5.5). Measured, not assumed from CSS.
     3. GLASS ACTUALLY APPLIED - computed backdrop-filter on the glass surfaces
        must not be `none`. A `@supports` block or a source-order mistake can
        drop the effect with no error anywhere; this is the only way to notice.
     4. CONSOLE / NETWORK ERRORS - with an explicit allowlist, so a new error is
        a failure rather than noise.

   Run:  node tools/verify_ui_layout.js
         node tools/verify_ui_layout.js --shots .scratch/shots   (also write PNGs)
   Exits non-zero if any check fails.

   Requires the server to be up on 127.0.0.1:8501 (HM_dashboard.bat).
   =========================================================================== */
'use strict';

const fs = require('fs');
const path = require('path');

/* puppeteer-core is NOT vendored in this repo (there is no package.json). It lives in the
   managed node workspace. Requiring it bare only works when NODE_PATH happens to be set, so
   resolve it explicitly and fail with an actionable message rather than a MODULE_NOT_FOUND
   stack that says nothing about what to install or where. */
const puppeteer = (function () {
  const candidates = [
    'puppeteer-core',
    path.join(process.env.WORKBUDDY_NODE_WORKSPACE || '', 'node_modules', 'puppeteer-core'),
    'C:/Users/Itrai/.workbuddy-ai/binaries/node/workspace/node_modules/puppeteer-core',
  ].filter(Boolean);
  for (const c of candidates) {
    try { return require(c); } catch (e) { /* try the next candidate */ }
  }
  console.error(
    'verify_ui_layout: cannot load puppeteer-core.\n' +
    '  Install it into the managed node workspace, or point WORKBUDDY_NODE_WORKSPACE at it:\n' +
    '    cd "C:/Users/Itrai/.workbuddy-ai/binaries/node/workspace" && npm install puppeteer-core\n' +
    '  Tried:\n' + candidates.map(c => '    ' + c).join('\n'));
  process.exit(2);
})();

const BASE = process.env.JARVIS_BASE || 'http://127.0.0.1:8501';
const CHROME = process.env.JARVIS_CHROME ||
  'C:/Program Files/Google/Chrome/Application/chrome.exe';

const shotsIdx = process.argv.indexOf('--shots');
const SHOTS_DIR = shotsIdx !== -1 ? process.argv[shotsIdx + 1] : null;

const VIEWPORTS = [
  { name: 'phone-360', width: 360, height: 780, mobile: true },
  { name: 'phone-390', width: 390, height: 844, mobile: true },
  { name: 'tablet-768', width: 768, height: 1024, mobile: true },
  { name: 'desktop-1440', width: 1440, height: 900, mobile: false },
];

/* Dashboard tabs are separate layouts, not just separate data - each has its
   own grid-template-columns, so each can overflow independently. */
const PAGES = [
  { name: 'dashboard', url: '/dashboard', views: ['trade', 'news', 'analyst', 'markets', 'analytics', 'backtest'] },
  { name: 'forex', url: '/' },
  { name: 'stocks', url: '/stocks' },
  { name: 'india', url: '/india' },
  { name: 'options', url: '/options' },
  { name: 'console', url: '/console' },
];

/* Controls WCAG 2.5.5 applies to: the primary way in and out of every view. */
const TAP_SELECTORS = [
  '.tt-tab', '.tt-pane-bar__btn', '.mob-tab-btn',
  '.market-nav-item', '.tt-nav-trigger', '.btn-nav-switch',
];

/* Surfaces that must carry the liquid-glass treatment. */
const GLASS_SELECTORS = ['.tt-rail', '.tt-panel'];

/* Errors that are expected on a UI-only server (no MT5 client attached) or are
   not defects. Anything not listed here fails the run - that is the point. */
const ERROR_ALLOWLIST = [
  /auto-selection.*503/i,
  /status of 503/i,
];

let checks = 0;
let failures = 0;

function ok(label, condition, detail) {
  checks++;
  if (condition) {
    console.log('  PASS  ' + label);
  } else {
    failures++;
    console.log('  FAIL  ' + label + (detail ? '\n          ' + detail : ''));
  }
}

function section(title) {
  console.log('\n' + title);
}

async function main() {
  if (!fs.existsSync(CHROME)) {
    console.error('Chrome not found at ' + CHROME + ' - set JARVIS_CHROME');
    process.exit(2);
  }
  if (SHOTS_DIR) fs.mkdirSync(SHOTS_DIR, { recursive: true });

  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: 'new',
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--force-color-profile=srgb'],
  });

  for (const vp of VIEWPORTS) {
    section(`viewport ${vp.name} (${vp.width}x${vp.height})`);

    for (const pageDef of PAGES) {
      const page = await browser.newPage();
      await page.setViewport({
        width: vp.width, height: vp.height,
        deviceScaleFactor: vp.mobile ? 2 : 1,
        isMobile: vp.mobile, hasTouch: vp.mobile,
      });

      const errors = [];
      page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
      page.on('pageerror', e => errors.push('PAGEERROR: ' + String(e.message)));
      page.on('requestfailed', r => errors.push('REQFAIL: ' + r.url()));

      let navigated = true;
      try {
        await page.goto(BASE + pageDef.url, { waitUntil: 'networkidle2', timeout: 45000 });
      } catch (e) {
        navigated = false;
        ok(`${pageDef.name} loads`, false, e.message.slice(0, 140));
      }
      if (!navigated) { await page.close(); continue; }
      await new Promise(r => setTimeout(r, 1200));

      const views = pageDef.views || [null];
      for (const view of views) {
        const label = view ? `${pageDef.name}/${view}` : pageDef.name;

        if (view) {
          const sel = `[data-view-btn="${view}"]`;
          if (await page.$(sel)) {
            await page.evaluate(s => document.querySelector(s).click(), sel);
            await new Promise(r => setTimeout(r, 1400));
          }
        }

        const probe = await page.evaluate((tapSel, glassSel) => {
          const de = document.documentElement;
          const out = {
            scrollW: de.scrollWidth,
            clientW: de.clientWidth,
            tap: [],
            glass: [],
          };

          // Tap targets: only on a coarse pointer, and only real controls.
          const coarse = window.matchMedia('(pointer: coarse)').matches;
          if (coarse) {
            tapSel.forEach(s => {
              document.querySelectorAll(s).forEach(el => {
                const r = el.getBoundingClientRect();
                if (r.width === 0 && r.height === 0) return;   // hidden
                out.tap.push({
                  sel: s,
                  label: (el.textContent || '').trim().slice(0, 18),
                  h: Math.round(r.height),
                });
              });
            });
          }

          glassSel.forEach(s => {
            document.querySelectorAll(s).forEach(el => {
              const cs = getComputedStyle(el);
              const bf = cs.backdropFilter || cs.webkitBackdropFilter || '';
              const bi = cs.backgroundImage || '';
              out.glass.push({
                sel: s,
                backdrop: bf,
                hasSheen: /gradient/i.test(bi),
              });
            });
          });

          // ---- App shell integrity (dashboard only) --------------------------
          // The dashboard is an app shell: the rail and the status bar are
          // pinned and the VIEW scrolls. If the DOCUMENT scrolls instead, the
          // shell height has become indefinite (min-height where height was
          // meant), the 1fr main row has resolved against CONTENT, and every
          // panel that was supposed to fill the screen is cut off at the fold.
          // That is a 1263px-tall view on a 900px screen, and nothing else in
          // this repo could see it.
          const appEl = document.querySelector('.tt-app');
          const mainEl = document.querySelector('.tt-main');
          const viewEl = document.querySelector('.tt-view[data-active="true"]');
          out.shell = appEl ? {
            docScrolls: de.scrollHeight > de.clientHeight + 1,
            docScrollH: de.scrollHeight,
            docClientH: de.clientHeight,
            mainH: mainEl ? Math.round(mainEl.getBoundingClientRect().height) : 0,
            viewH: viewEl ? Math.round(viewEl.getBoundingClientRect().height) : 0,
            viewId: viewEl ? viewEl.id : '(none)',
          } : null;

          // ---- Silent clipping inside a panel --------------------------------
          // A panel can clip its own content (overflow:hidden, or a flex child
          // that shrank below its content) while the document width stays
          // legal, so the overflow check above cannot see it. Three things are
          // NOT defects and are excluded, because a check that fires on them
          // stops being read:
          //   - text-overflow:ellipsis  designed truncation, the reader sees it
          //   - .tt-sr-only             visually-hidden screen-reader text
          //   - form controls           scroll their own value when focused
          //   - #chart / #chart-tv      lightweight-charts' own generated DOM
          out.clip = [];
          if (viewEl) {
            for (const el of viewEl.querySelectorAll('*')) {
              const cs = getComputedStyle(el);
              if (cs.textOverflow === 'ellipsis') continue;
              if (el.closest('.tt-sr-only')) continue;
              if (el.closest('#chart, #chart-tv')) continue;
              if (/^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName)) continue;
              if (el.clientWidth > 0 && el.scrollWidth > el.clientWidth + 1 &&
                  (cs.overflowX === 'hidden' || cs.overflowX === 'clip')) {
                out.clip.push({
                  tag: el.tagName.toLowerCase(),
                  id: el.id || '',
                  scroll: el.scrollWidth,
                  client: el.clientWidth,
                });
              }
            }
          }

          return out;
        }, TAP_SELECTORS, GLASS_SELECTORS);

        // 1. Horizontal overflow.
        const overflow = probe.scrollW - probe.clientW;
        ok(`${label}: no horizontal overflow`, overflow <= 2,
          `scrollWidth ${probe.scrollW} vs clientWidth ${probe.clientW} (+${overflow}px)`);

        // 2. Tap targets.
        const small = probe.tap.filter(t => t.h < 44);
        if (probe.tap.length) {
          ok(`${label}: ${probe.tap.length} controls >= 44px tall`, small.length === 0,
            small.map(t => `${t.sel} "${t.label}" ${t.h}px`).join(', '));
        }

        // 3. Glass applied (only meaningful where the surface exists).
        const badGlass = probe.glass.filter(g => !g.backdrop || g.backdrop === 'none');
        if (probe.glass.length) {
          ok(`${label}: ${probe.glass.length} glass surfaces have backdrop-filter`,
            badGlass.length === 0,
            badGlass.map(g => g.sel).join(', '));
          const noSheen = probe.glass.filter(g => !g.hasSheen);
          ok(`${label}: glass surfaces carry the sheen layer`, noSheen.length === 0,
            noSheen.map(g => g.sel).join(', '));
        }

        // 4. App shell integrity + silent clipping (dashboard only).
        if (probe.shell) {
          ok(`${label}: document does not scroll (app shell)`,
            !probe.shell.docScrolls,
            `documentElement ${probe.shell.docScrollH} > ${probe.shell.docClientH}px — ` +
            'the shell height is indefinite, so 1fr main resolved against content');
          ok(`${label}: view fits inside main`,
            probe.shell.viewH <= probe.shell.mainH + 1,
            `${probe.shell.viewId} ${probe.shell.viewH}px > main ${probe.shell.mainH}px — content past the fold`);

          const clips = probe.clip || [];
          ok(`${label}: no silently clipped content`, clips.length === 0,
            clips.slice(0, 3).map(c =>
              `${c.tag}${c.id ? '#' + c.id : ''} ${c.scroll}>${c.client}`).join(', '));
        }

        if (SHOTS_DIR) {
          const f = path.join(SHOTS_DIR, `${vp.name}-${label.replace('/', '-')}.png`);
          await page.screenshot({ path: f });
        }
      }

      // 4. Console / network errors, minus the allowlist.
      const unexpected = [...new Set(errors)].filter(
        e => !ERROR_ALLOWLIST.some(re => re.test(e)));
      ok(`${pageDef.name}: no unexpected console/network errors`,
        unexpected.length === 0, unexpected.slice(0, 4).join(' | '));

      await page.close();
    }
  }

  await browser.close();

  console.log('\n' + '='.repeat(74));
  console.log(`${checks - failures}/${checks} checks passed`);
  if (failures) {
    console.log(`${failures} FAILED`);
    process.exit(1);
  }
  console.log('layout, tap targets and glass application all verified');
}

main().catch(e => { console.error('FAILED TO RUN:', e); process.exit(2); });
