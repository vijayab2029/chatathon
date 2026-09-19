/* ==========================================================================
   Team Stress Insight — Part 5 UI
   Two views over the Part 3 / Part 4 JSON contracts. All data is synthetic.
   ========================================================================== */

(() => {
'use strict';

const SVGNS = 'http://www.w3.org/2000/svg';
const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;

const $  = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));

function el(tag, attrs = {}, text) {
  const n = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (text != null) n.textContent = text;           // labels are untrusted data
  return n;
}
function h(tag, attrs = {}, text) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') n.className = v; else n.setAttribute(k, v);
  }
  if (text != null) n.textContent = text;
  return n;
}
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

// A chart inside a hidden tabpanel measures 0. Fall back to a sane width and
// let the tab switch redraw at the real size.
function chartWidth(svg, fallback = 620) {
  const w = svg.clientWidth || (svg.parentElement ? svg.parentElement.clientWidth - 54 : 0);
  return w > 120 ? w : fallback;
}

/* --- design tokens pulled from CSS so there is one source of truth ------- */
const T = {
  surfaceCard: () => css('--surface-card'),
  grid:  () => css('--grid'),
  axis:  () => css('--axis'),
  brand: () => css('--brand'),
  pink:  () => css('--pink'),
  line:  () => css('--b500'),
  // validated ordinal ramps (see README — re-run the validator if you change these)
  heatRamp:   () => ['--b300', '--b350', '--b450', '--b550', '--b650'].map(css),
  factorRamp: () => ['--b300', '--b400', '--b500', '--b600'].map(css),
};

const SEVERITY = {
  Low:      { step: 1, mark: '--good',     ink: '--good-ink' },
  Moderate: { step: 2, mark: '--warning',  ink: '--warning-ink' },
  Elevated: { step: 3, mark: '--serious',  ink: '--serious-ink' },
  High:     { step: 4, mark: '--critical', ink: '--critical-ink' },
};

/* Status icons — severity never travels as colour alone. */
function severityIcon(band, color) {
  const s = el('svg', { viewBox: '0 0 16 16', class: 'band__ic', 'aria-hidden': 'true' });
  if (band === 'High' || band === 'Elevated') {
    s.append(el('path', { d: 'M8 1.4 L15.2 14.6 H0.8 Z', fill: color }));
    s.append(el('rect', { x: '7.1', y: '6', width: '1.8', height: '4.4', rx: '.9', fill: '#fff' }));
    s.append(el('circle', { cx: '8', cy: '12', r: '1', fill: '#fff' }));
  } else if (band === 'Moderate') {
    s.append(el('circle', { cx: '8', cy: '8', r: '7', fill: color }));
    s.append(el('rect', { x: '7.1', y: '4', width: '1.8', height: '5', rx: '.9', fill: '#fff' }));
    s.append(el('circle', { cx: '8', cy: '11.4', r: '1', fill: '#fff' }));
  } else {
    s.append(el('circle', { cx: '8', cy: '8', r: '7', fill: color }));
    s.append(el('path', { d: 'M4.6 8.2 L7 10.6 L11.4 5.6', fill: 'none', stroke: '#fff', 'stroke-width': '1.9', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }));
  }
  return s;
}

/* ==========================================================================
   Tooltip
   ========================================================================== */
const tip = {
  node: $('#tooltip'),
  show(x, y, build) {
    this.node.textContent = '';
    build(this.node);
    this.node.classList.add('is-on');
    const r = this.node.getBoundingClientRect();
    this.node.style.left = clamp(x + 14, 8, innerWidth - r.width - 8) + 'px';
    this.node.style.top  = clamp(y - r.height - 14, 8, innerHeight - r.height - 8) + 'px';
  },
  hide() { this.node.classList.remove('is-on'); },
};
function tipRow(parent, value, key, color) {
  parent.append(h('div', { class: 'tooltip__v' }, value));
  parent.append(h('div', { class: 'tooltip__k' }, key));
  if (color) {
    const row = h('div', { class: 'tooltip__row' });
    const k = h('span', { class: 'tooltip__key' }); k.style.background = color;
    row.append(k, h('span', {}, color.label || ''));
    parent.append(row);
  }
}

/* ==========================================================================
   Background triangles + parallax
   ========================================================================== */
function paintBackground() {
  const svg = $('#bgTris');
  svg.setAttribute('viewBox', '0 0 1000 1400');
  svg.textContent = '';
  const specs = [
    [ 60,  120, 120, 12, .07, 'fill'],   [880,  90, 100, -28, .16, 'stroke'],
    [230,  330,  62, 34, .20, 'stroke'], [700, 260, 150,  8, .05, 'fill'],
    [930, 430,  80, -14, .10, 'fill'],   [110, 560, 110, 22, .13, 'stroke'],
    [420, 640,  50, -40, .18, 'fill'],   [810, 690, 130, 30, .11, 'stroke'],
    [ 40, 880,  90,  6, .09, 'fill'],    [560, 950, 140, -20, .05, 'fill'],
    [900, 1010, 72, 44, .19, 'stroke'],  [250, 1120, 120, -10, .12, 'stroke'],
    [690, 1250,  58, 26, .17, 'fill'],   [ 90, 1300, 100, -34, .08, 'fill'],
  ];
  // brand + pink + a light blue step; the deep navy reads muddy grey on cream
  const hues = [T.brand(), T.pink(), css('--b400')];
  const layers = [];
  specs.forEach(([x, y, size, rot, op, mode], i) => {
    const half = size / 2;
    // Two nested groups on purpose: a CSS `transform` on an element overrides
    // its SVG transform *attribute*, so parallax and placement cannot share one.
    const outer = el('g', { opacity: op, 'data-speed': (0.06 + (i % 5) * 0.045).toFixed(3) });
    const inner = el('g', { transform: `translate(${x} ${y}) rotate(${rot})` });
    const pts = `0,${-half} ${half},${half} ${-half},${half}`;
    inner.append(mode === 'fill'
      ? el('polygon', { points: pts, fill: hues[i % 3] })
      : el('polygon', { points: pts, fill: 'none', stroke: hues[i % 3], 'stroke-width': 2.5, 'stroke-linejoin': 'round' }));
    outer.append(inner);
    svg.append(outer);
    layers.push(outer);
  });
  return layers;
}

/* ==========================================================================
   Hero: shrinks and rounds as you scroll, then pops like a bubble
   ========================================================================== */
function makeHeroBubble() {
  let popped = false;
  const pill = $('#pill');
  const easeInOut = t => t < .5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2;

  function activeStage() {
    const view = $('#view-employee').hidden ? 'employer' : 'employee';
    return { stage: $('#stage-' + view), card: $('#hero-' + view), shards: $('#shards-' + view) };
  }

  function burst(card, layer) {
    if (reduceMotion) return;
    const cr = card.getBoundingClientRect();
    const lr = layer.getBoundingClientRect();
    const cx = cr.left - lr.left + cr.width / 2;
    const cy = cr.top - lr.top + cr.height / 2;
    const hues = [T.brand(), T.pink(), css('--brand-deep'), css('--b400')];

    for (let i = 0; i < 18; i++) {
      const size = 8 + Math.random() * 20;
      const s = h('span', { class: 'shard' });
      s.style.left = cx + 'px';
      s.style.top = cy + 'px';
      s.style.borderLeft = `${size / 2}px solid transparent`;
      s.style.borderRight = `${size / 2}px solid transparent`;
      s.style.borderBottom = `${size}px solid ${hues[i % 4]}`;
      layer.append(s);

      const ang = (i / 18) * Math.PI * 2 + Math.random() * .4;
      const dist = 130 + Math.random() * 230;
      s.animate([
        { transform: 'translate(-50%,-50%) scale(.25) rotate(0deg)', opacity: 1 },
        { transform: `translate(${Math.cos(ang) * dist - 50}%, ${Math.sin(ang) * dist - 50}%) scale(1) rotate(${(Math.random() * 2 - 1) * 540}deg)`, opacity: 0 },
      ], { duration: 780 + Math.random() * 420, easing: 'cubic-bezier(.16,.9,.3,1)', fill: 'forwards' })
        .finished.then(() => s.remove(), () => s.remove());
    }
  }

  function pop(card, layer, stage) {
    popped = true;
    burst(card, layer);
    if (!reduceMotion) {
      card.animate(
        [{ transform: card.style.transform, opacity: 1 },
         { transform: card.style.transform + ' scale(1.07)', opacity: .9, offset: .32 },
         { transform: card.style.transform + ' scale(.2)', opacity: 0 }],
        { duration: 440, easing: 'cubic-bezier(.3,0,.2,1)' });
    }
    setTimeout(() => { if (popped) card.style.visibility = 'hidden'; }, reduceMotion ? 0 : 300);

    // A popped bubble leaves no hole: collapse the space it occupied so the
    // next section closes up instead of scrolling past an empty band.
    if (!stage.dataset.naturalH) stage.dataset.naturalH = String(stage.offsetHeight);
    stage.style.height = stage.dataset.naturalH + 'px';
    void stage.offsetHeight;
    stage.style.transition = reduceMotion ? 'none' : 'height .45s cubic-bezier(.4,0,.2,1)';
    stage.style.height = '0px';

    pill.classList.add('is-on');
    pill.setAttribute('aria-hidden', 'false');
  }

  function unpop(card, stage) {
    popped = false;
    card.style.visibility = '';
    if (stage && stage.dataset.naturalH) stage.style.height = stage.dataset.naturalH + 'px';
    pill.classList.remove('is-on');
    pill.setAttribute('aria-hidden', 'true');
  }

  function frame() {
    const { stage, card, shards } = activeStage();
    if (!stage || !card) return;
    const r = stage.getBoundingClientRect();
    // Shrink across the card's own travel up the viewport: it starts full size
    // around mid-screen and is fully tightened by the time it reaches the top
    // bar — so the whole effect is visible without leaving dead space behind.
    const startY = innerHeight * .58, endY = 64;
    const p = clamp((startY - r.top) / (startY - endY), 0, 1);
    const e = easeInOut(p);

    if (!popped) {
      const scale = 1 - .4 * e;
      // surface tension: the bubble wobbles slightly as it tightens
      const wob = reduceMotion ? 0 : Math.sin(p * Math.PI * 2.6) * 0.9 * p;
      card.style.transform = `translateY(${-24 * e}px) scale(${scale.toFixed(4)}) rotate(${wob.toFixed(2)}deg)`;
      card.style.borderRadius = (34 + 108 * e) + 'px';
      card.style.boxShadow = `0 ${(4 + 22 * (1 - e)).toFixed(0)}px ${(20 + 50 * (1 - e)).toFixed(0)}px rgba(48,43,92,${(.1 + .12 * (1 - e)).toFixed(3)})`;
    }

    if (!popped && p >= .92) pop(card, shards, stage);
    else if (popped && p < .70) unpop(card, stage);

    // parallax on the background triangles
    const y = scrollY;
    tris.forEach(g => { g.style.transform = `translateY(${(-y * +g.dataset.speed).toFixed(1)}px)`; });
  }

  let ticking = false;
  const onScroll = () => {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(() => { frame(); ticking = false; });
  };
  addEventListener('scroll', onScroll, { passive: true });
  addEventListener('resize', onScroll);

  return {
    reset() {
      $$('[data-hero]').forEach(c => { c.style.visibility = ''; c.style.transform = ''; c.style.borderRadius = ''; c.style.boxShadow = ''; });
      $$('.stage').forEach(s => { s.style.transition = 'none'; s.style.height = ''; delete s.dataset.naturalH; });
      popped = false;
      pill.classList.remove('is-on');
      pill.setAttribute('aria-hidden', 'true');
      frame();
    },
    frame,
  };
}
const tris = paintBackground();

/* ==========================================================================
   Chart: 14-day stress trend (single series → no legend box needed)
   ========================================================================== */
function drawTrend(data) {
  const svg = $('#trendChart');
  const W = chartWidth(svg, 640);
  const H = 300;
  const M = { t: 30, r: 48, b: 36, l: 38 };
  const iw = W - M.l - M.r, ih = H - M.t - M.b;
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('height', H);
  svg.textContent = '';

  const n = data.length;
  const X = i => M.l + (n === 1 ? iw / 2 : (i / (n - 1)) * iw);
  const Y = v => M.t + ih - (v / 100) * ih;

  // weekend wash — explains the dips without spending a colour on it
  data.forEach((d, i) => {
    if (d.weekday !== 'Sat' && d.weekday !== 'Sun') return;
    const half = iw / (n - 1) / 2;
    svg.append(el('rect', {
      x: (X(i) - half).toFixed(1), y: M.t, width: (half * 2).toFixed(1), height: ih,
      fill: css('--surface-sunk'), opacity: .85,
    }));
  });

  // gridlines: hairline, solid, recessive
  [0, 25, 50, 75, 100].forEach(v => {
    svg.append(el('line', { x1: M.l, x2: M.l + iw, y1: Y(v), y2: Y(v), stroke: v === 0 ? T.axis() : T.grid(), 'stroke-width': 1 }));
    svg.append(el('text', { x: M.l - 9, y: Y(v) + 4, 'text-anchor': 'end', class: 'tickLabel' }, String(v)));
  });

  const area = `M${X(0)},${Y(0)} ` + data.map((d, i) => `L${X(i)},${Y(d.stress_score)}`).join(' ') + ` L${X(n - 1)},${Y(0)} Z`;
  const grad = el('linearGradient', { id: 'trendFill', x1: '0', y1: '0', x2: '0', y2: '1' });
  grad.append(el('stop', { offset: '0', 'stop-color': T.line(), 'stop-opacity': '.16' }));
  grad.append(el('stop', { offset: '1', 'stop-color': T.line(), 'stop-opacity': '.02' }));
  const defs = el('defs'); defs.append(grad); svg.append(defs);
  svg.append(el('path', { d: area, fill: 'url(#trendFill)' }));

  const line = 'M' + data.map((d, i) => `${X(i)},${Y(d.stress_score)}`).join(' L');
  svg.append(el('path', { d: line, fill: 'none', stroke: T.line(), 'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }));

  // x ticks — every other day so labels never collide
  data.forEach((d, i) => {
    if (i % 2) return;
    svg.append(el('text', { x: X(i), y: H - 12, 'text-anchor': 'middle', class: 'tickLabel' }, d.date.slice(5).replace('-', '/')));
  });

  // Direct labels, sparingly: the peak and the endpoint only.
  const peakIdx = data.reduce((b, d, i) => d.stress_score > data[b].stress_score ? i : b, 0);
  const lastIdx = n - 1;
  [[peakIdx, 'Peak', 'pointLabel--muted'], [lastIdx, 'Today', 'pointLabel']].forEach(([i, tag, cls]) => {
    if (i === lastIdx && peakIdx === lastIdx && tag === 'Peak') return;
    const d = data[i];
    svg.append(el('circle', { cx: X(i), cy: Y(d.stress_score), r: 6.5, fill: T.line(), stroke: T.surfaceCard(), 'stroke-width': 2 }));
    const anchor = i > n - 3 ? 'end' : 'middle';
    const dx = i > n - 3 ? 10 : 0;
    svg.append(el('text', { x: X(i) + dx, y: Y(d.stress_score) - 14, 'text-anchor': anchor, class: cls }, `${tag} ${d.stress_score}`));
  });

  // crosshair + hit layer: readers aim at a date, never at a 2px line
  const cross = el('line', { y1: M.t, y2: M.t + ih, stroke: css('--brand-deep'), 'stroke-width': 1, opacity: 0 });
  const focusDot = el('circle', { r: 5.5, fill: T.pink(), stroke: T.surfaceCard(), 'stroke-width': 2, opacity: 0 });
  svg.append(cross, focusDot);

  const hit = el('rect', { x: M.l, y: M.t, width: iw, height: ih, fill: 'transparent', tabindex: '0' });
  svg.append(hit);

  const at = i => {
    const d = data[i];
    cross.setAttribute('x1', X(i)); cross.setAttribute('x2', X(i)); cross.setAttribute('opacity', .32);
    focusDot.setAttribute('cx', X(i)); focusDot.setAttribute('cy', Y(d.stress_score)); focusDot.setAttribute('opacity', 1);
    const box = hit.getBoundingClientRect();
    tip.show(box.left + (X(i) - M.l), box.top + Y(d.stress_score), t => {
      t.append(h('div', { class: 'tooltip__v' }, `${d.stress_score} / 100`));
      t.append(h('div', { class: 'tooltip__k' }, `${d.weekday} ${d.date}`));
      const row = h('div', { class: 'tooltip__row' });
      const k = h('span', { class: 'tooltip__key' }); k.style.background = T.pink();
      row.append(k, h('span', {}, `${d.meetings} meeting${d.meetings === 1 ? '' : 's'}`));
      t.append(row);
    });
  };
  const clear = () => { cross.setAttribute('opacity', 0); focusDot.setAttribute('opacity', 0); tip.hide(); };

  hit.addEventListener('pointermove', ev => {
    const box = hit.getBoundingClientRect();
    at(clamp(Math.round(((ev.clientX - box.left) / box.width) * (n - 1)), 0, n - 1));
  });
  hit.addEventListener('pointerleave', clear);
  let kb = n - 1;
  hit.addEventListener('focus', () => at(kb));
  hit.addEventListener('blur', clear);
  hit.addEventListener('keydown', ev => {
    if (ev.key === 'ArrowLeft') { kb = clamp(kb - 1, 0, n - 1); at(kb); ev.preventDefault(); }
    if (ev.key === 'ArrowRight') { kb = clamp(kb + 1, 0, n - 1); at(kb); ev.preventDefault(); }
  });

  // table view — every value reachable without hovering
  const tbl = h('table', { class: 'dataTable' });
  const cap = h('caption', {}, 'Daily stress score and meeting count (simulated)');
  tbl.append(cap);
  const thead = h('thead'); const trh = h('tr');
  ['Date', 'Day', 'Stress score', 'Meetings'].forEach(t => trh.append(h('th', { scope: 'col' }, t)));
  thead.append(trh); tbl.append(thead);
  const tb = h('tbody');
  data.forEach(d => {
    const tr = h('tr');
    tr.append(h('td', {}, d.date), h('td', {}, d.weekday), h('td', {}, String(d.stress_score)), h('td', {}, String(d.meetings)));
    tb.append(tr);
  });
  tbl.append(tb);
  const wrap = $('#trendTable'); wrap.textContent = ''; wrap.append(tbl);
}

/* ==========================================================================
   Chart: contributing factors (one measure across categories → sequential)
   ========================================================================== */
function drawFactors(factors) {
  const svg = $('#factorChart');
  const W = chartWidth(svg, 420);
  const ROW = 54, BAR = 20, PAD_R = 50;
  const H = factors.length * ROW + 10;
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('height', H);
  svg.textContent = '';

  const max = Math.max(...factors.map(f => f.weight_pct));
  const scaleMax = Math.ceil(max / 5) * 5;
  const iw = W - PAD_R;
  const ramp = T.factorRamp();

  factors.forEach((f, i) => {
    const y = i * ROW + 6;
    const w = Math.max(4, (f.weight_pct / scaleMax) * iw);
    const bucket = clamp(Math.floor((f.weight_pct / scaleMax) * ramp.length), 0, ramp.length - 1);
    const fill = ramp[bucket];

    svg.append(el('text', { x: 0, y: y + 12, class: 'pointLabel--muted' }, f.factor));

    // 4px rounded data-end, square at the baseline
    const r = 4, top = y + 20;
    const d = `M0,${top} H${w - r} A${r},${r} 0 0 1 ${w},${top + r} V${top + BAR - r} A${r},${r} 0 0 1 ${w - r},${top + BAR} H0 Z`;
    const bar = el('path', { d, fill });
    svg.append(bar);

    svg.append(el('text', { x: w + 10, y: top + BAR - 5, class: 'pointLabel' }, `${f.weight_pct}%`));

    // the mark is the hit target, padded well past the painted pixels
    const hit = el('rect', { x: 0, y: y + 12, width: W, height: ROW - 12, fill: 'transparent', tabindex: '0', role: 'img' });
    hit.setAttribute('aria-label', `${f.factor}: ${f.weight_pct} percent. ${f.note}`);
    const show = ev => {
      bar.setAttribute('opacity', .82);
      const b = hit.getBoundingClientRect();
      tip.show(ev ? ev.clientX : b.left + 40, b.top + b.height / 2, t => {
        t.append(h('div', { class: 'tooltip__v' }, `${f.weight_pct}%`));
        t.append(h('div', { class: 'tooltip__k' }, f.factor));
        t.append(h('div', { class: 'tooltip__row' }, f.note));
      });
    };
    const hide = () => { bar.setAttribute('opacity', 1); tip.hide(); };
    hit.addEventListener('pointermove', show);
    hit.addEventListener('pointerleave', hide);
    hit.addEventListener('focus', () => show(null));
    hit.addEventListener('blur', hide);
    svg.append(hit);
  });
}

/* ==========================================================================
   Chart: meeting density heatmap (sequential magnitude on a grid)
   ========================================================================== */
function drawHeatmap(hm) {
  const svg = $('#heatChart');
  const W = chartWidth(svg, 1030);
  const LEFT = 56, TOP = 26, GAP = 2;
  const cols = hm.days.length, rows = hm.blocks.length;
  const cw = (W - LEFT) / cols;
  const ch = 46;
  const H = TOP + rows * ch + 6;
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('height', H);
  svg.textContent = '';

  const ramp = T.heatRamp();
  const max = Math.max(...hm.grid.flat());
  const bucket = v => clamp(Math.floor((v / (max * 1.001)) * ramp.length), 0, ramp.length - 1);

  hm.days.forEach((d, c) => svg.append(el('text', { x: LEFT + c * cw + cw / 2, y: 16, 'text-anchor': 'middle', class: 'axisLabel' }, d)));
  hm.blocks.forEach((b, r) => svg.append(el('text', { x: LEFT - 10, y: TOP + r * ch + ch / 2 + 4, 'text-anchor': 'end', class: 'tickLabel' }, b)));

  hm.blocks.forEach((block, r) => {
    hm.days.forEach((day, c) => {
      const v = hm.grid[c][r];
      const bi = bucket(v);
      const x = LEFT + c * cw + GAP / 2, y = TOP + r * ch + GAP / 2;
      const w = cw - GAP, hh = ch - GAP;
      const cell = el('rect', { x, y, width: w, height: hh, rx: 7, fill: ramp[bi] });
      svg.append(cell);

      // Label only the cells dark enough to carry white ink comfortably.
      // Inline style, not a fill attribute — the class's `fill` would win.
      if (bi >= ramp.length - 2) {
        const t = el('text', { x: x + w / 2, y: y + hh / 2 + 4, 'text-anchor': 'middle', class: 'pointLabel' }, v.toFixed(1));
        t.style.fill = '#fff';
        svg.append(t);
      }

      const hit = el('rect', { x: x - 2, y: y - 2, width: w + 4, height: hh + 4, rx: 9, fill: 'transparent', tabindex: '0', role: 'img' });
      hit.setAttribute('aria-label', `${day} ${block}: ${v.toFixed(1)} meetings per person`);
      const show = ev => {
        cell.setAttribute('stroke', css('--brand-deep')); cell.setAttribute('stroke-width', 2);
        const b = hit.getBoundingClientRect();
        tip.show(ev ? ev.clientX : b.left + b.width / 2, b.top + b.height / 2, t => {
          t.append(h('div', { class: 'tooltip__v' }, v.toFixed(1)));
          t.append(h('div', { class: 'tooltip__k' }, `${day}, ${block} · meetings per person`));
        });
      };
      const hide = () => { cell.removeAttribute('stroke'); tip.hide(); };
      hit.addEventListener('pointermove', show);
      hit.addEventListener('pointerleave', hide);
      hit.addEventListener('focus', () => show(null));
      hit.addEventListener('blur', hide);
      svg.append(hit);
    });
  });

  const legend = $('#heatLegend'); legend.textContent = '';
  ramp.forEach(c => { const s = h('div', { class: 'rampLegend__step' }); s.style.background = c; legend.append(s); });

  const tbl = h('table', { class: 'dataTable' });
  tbl.append(h('caption', {}, 'Average meetings per person by weekday and time block (simulated, team-level only)'));
  const thead = h('thead'), trh = h('tr');
  trh.append(h('th', { scope: 'col' }, 'Time block'));
  hm.days.forEach(d => trh.append(h('th', { scope: 'col' }, d)));
  thead.append(trh); tbl.append(thead);
  const tb = h('tbody');
  hm.blocks.forEach((block, r) => {
    const tr = h('tr');
    tr.append(h('th', { scope: 'row' }, block));
    hm.days.forEach((_, c) => tr.append(h('td', {}, hm.grid[c][r].toFixed(1))));
    tb.append(tr);
  });
  tbl.append(tb);
  const wrap = $('#heatTable'); wrap.textContent = ''; wrap.append(tbl);
}

/* ==========================================================================
   Employee view
   ========================================================================== */
function renderEmployee(d) {
  $('#empWho').textContent = `${d.display_name} · ${d.person_id}`;
  $('#empMeta').textContent = `Today ${d.generated_at} · private to you · never sent to your employer`;
  $('#empScore').textContent = d.current.stress_score;
  $('#empBand').textContent = `${d.current.band} stress`;

  const delta = d.current.delta_vs_baseline;
  const dn = $('#empDelta'); dn.textContent = '';
  const up = delta >= 0;
  const arrow = el('svg', { viewBox: '0 0 12 12', width: '11', height: '11', 'aria-hidden': 'true' });
  arrow.append(el('path', { d: up ? 'M6 1 L11.5 11 H.5 Z' : 'M6 11 L.5 1 H11.5 Z', fill: up ? css('--critical') : css('--good') }));
  dn.append(arrow);
  dn.append(h('span', {}, `${up ? '+' : ''}${delta} vs your 14-day average`));
  dn.style.color = up ? css('--critical-ink') : css('--good-ink');

  const kp = $('#empKpis'); kp.textContent = '';
  d.biometrics_today.forEach(b => {
    const c = h('div', { class: 'kpi' });
    c.append(h('div', { class: 'kpi__label' }, b.label));
    const v = h('div', { class: 'kpi__value' }, String(b.value));
    if (b.unit) v.append(h('span', {}, b.unit));
    c.append(v);
    c.append(h('div', { class: 'kpi__base' }, `baseline ${b.baseline}${b.unit}`));
    kp.append(c);
  });

  drawTrend(d.trend);
  drawFactors(d.contributing_factors);

  // insight card
  const ins = $('#empInsight'); ins.textContent = '';
  ins.append(h('div', { class: 'insight__eyebrow' }, 'Pattern found in your data'));
  ins.append(h('h3', { class: 'insight__headline' }, d.insight.headline));
  ins.append(h('p', { class: 'insight__detail' }, d.insight.detail));
  const ul = h('ul', { class: 'insight__evidence' });
  d.insight.evidence.forEach(e => ul.append(h('li', {}, e)));
  ins.append(ul);
  ins.append(h('div', { class: 'insight__conf' }, `Confidence: ${d.insight.confidence} · ${d.insight.confidence_note}`));

  // opt-in
  const sw = $('#optinSwitch');
  const list = $('#optinList');
  const state = $('#optinState');
  const paint = on => {
    sw.setAttribute('aria-checked', String(on));
    state.textContent = on
      ? 'On. Only the two lines marked below are shared — and only into the team aggregate, never as a per-person row.'
      : 'Off. Your manager sees nothing that originates from you.';
    list.textContent = '';
    d.opt_in.shares_if_enabled.forEach(s => {
      const li = h('li');
      li.append(h('span', { class: 'shareList__ic ' + (on ? 'shareList__ic--yes' : 'shareList__ic--no') }, on ? 'Shared' : 'Off'));
      li.append(h('span', {}, s));
      list.append(li);
    });
    d.opt_in.never_shares.forEach(s => {
      const li = h('li');
      li.append(h('span', { class: 'shareList__ic shareList__ic--no' }, 'Never'));
      li.append(h('span', {}, s));
      list.append(li);
    });
  };
  paint(d.opt_in.enabled);
  sw.onclick = () => paint(sw.getAttribute('aria-checked') !== 'true');
}

/* ==========================================================================
   Employer view
   ========================================================================== */
const PRINCIPLES = [
  ['01', 'The employee owns the detail', 'Full biometrics and scores exist only in the private view. This page never receives them.'],
  ['02', 'Never a per-person number', 'There is no score, no name, and no ranking in this payload — not hidden, not computed.'],
  ['03', 'k-anonymity floor', 'Below the team-size threshold the aggregate is withheld entirely, so small teams cannot be reverse-engineered.'],
  ['04', 'Causes attach to the calendar', '"6 back-to-back meetings, no buffer" is a shareable fact. "Alex is stressed" is not.'],
  ['05', 'Recommend process, not people', 'The output is a calendar change or a team ritual — never an instruction to check on someone.'],
  ['06', 'Opt-in escalation only', 'Employees can push their own specifics upward. Consent is never inferred from the aggregate.'],
];

function renderEmployer(d, teamSize) {
  const k = d.k_anonymity;
  const size = teamSize ?? k.team_size;
  const open = size >= k.threshold;

  $('#sizeVal').textContent = size;
  $('#sizeDown').disabled = size <= 1;
  $('#sizeUp').disabled = size >= 24;

  const gate = $('#gate');
  gate.classList.toggle('gate--open', open);
  gate.classList.toggle('gate--shut', !open);
  $('#gateTitle').textContent = open
    ? `Aggregate released — ${size} people, threshold ${k.threshold}`
    : `Aggregate withheld — ${size} ${size === 1 ? 'person' : 'people'}, threshold ${k.threshold}`;
  $('#gateText').textContent = open
    ? 'The team is above the k-anonymity floor, so team-level structure can be reported without any individual becoming identifiable.'
    : 'With this few people, any team-level figure would be attributable back to an individual. Nothing is released — not a blurred version, not a rounded version.';

  const ic = $('#gateIc'); ic.textContent = '';
  ic.append(el('path', {
    d: 'M20 3 L37 34 H3 Z', fill: 'none',
    stroke: open ? css('--good') : css('--critical'), 'stroke-width': 3, 'stroke-linejoin': 'round',
  }));
  ic.append(open
    ? el('path', { d: 'M14.5 24.5 L18.5 28.5 L26 20', fill: 'none', stroke: css('--good'), 'stroke-width': 3.2, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' })
    : el('path', { d: 'M16 20 L24 28 M24 20 L16 28', stroke: css('--critical'), 'stroke-width': 3.2, 'stroke-linecap': 'round' }));

  $('#gateBlocked').hidden = open;
  $('#blockedText').textContent = open ? '' :
    `This team has ${size} ${size === 1 ? 'member' : 'members'}, below the floor of ${k.threshold}. ` +
    'This is the answer to "what happens with two employees at different stress levels": the employer sees nothing at all, because anything shown would be a per-person number wearing a team-level label.';

  $('#stage-employer').hidden = !open;
  $('#orgBody').hidden = !open;
  if (!open) { $('#pill').classList.remove('is-on'); return; }

  const s = d.structural_summary;
  $('#orgWho').textContent = `${d.team_label} · ${size} people`;
  $('#orgMeta').textContent = `Week of ${d.generated_at} · aggregate only · no individual data present`;
  $('#orgHeroNum').textContent = s.hero.value;
  $('#orgHeroLabel').textContent = s.hero.label;
  const kp = $('#orgKpis'); kp.textContent = '';
  s.tiles.forEach(t => {
    const c = h('div', { class: 'kpi' });
    c.append(h('div', { class: 'kpi__label' }, t.label));
    const v = h('div', { class: 'kpi__value' }, String(t.value));
    if (t.unit) v.append(h('span', {}, t.unit));
    c.append(v);
    kp.append(c);
  });

  // causal categories
  const grid = $('#catGrid'); grid.textContent = '';
  d.causal_categories.forEach(cat => {
    const sev = SEVERITY[cat.severity_band] || SEVERITY.Moderate;
    const mark = css(sev.mark), ink = css(sev.ink);

    const card = h('article', { class: 'card card--ridged cat' });
    card.append(h('h3', { class: 'cat__name' }, cat.category));

    const band = h('div', { class: 'band' });
    const steps = h('div', { class: 'band__steps' });
    for (let i = 1; i <= 4; i++) {
      const st = h('div', { class: 'band__step' });
      if (i <= sev.step) { st.style.background = mark; st.style.borderColor = mark; }
      steps.append(st);
    }
    band.append(steps);
    const lab = h('div', { class: 'band__label' });
    lab.style.color = ink;
    lab.append(severityIcon(cat.severity_band, mark), h('span', {}, cat.severity_band));
    band.append(lab);
    card.append(band);

    card.append(h('p', { class: 'cat__fact' }, cat.structural_fact));

    const act = h('div', { class: 'cat__action' });
    const inner = h('div', { class: 'cat__actionIn' });
    inner.append(h('span', { class: 'cat__actionLabel' }, 'Recommended action'));
    inner.append(document.createTextNode(cat.recommended_action));
    act.append(inner);
    card.append(act);

    grid.append(card);
  });

  drawHeatmap(d.meeting_density);
  $('#heatCaption').textContent = d.meeting_density.caption;

  const acts = $('#actionList'); acts.textContent = '';
  d.causal_categories
    .filter(c => c.severity_band !== 'Low')
    .forEach(c => {
      const li = h('li');
      const body = h('div', { class: 'actionList__text' });
      body.append(h('span', { class: 'actionList__cat' }, c.category));
      body.append(document.createTextNode(c.recommended_action));
      li.append(body);
      acts.append(li);
    });

  const never = $('#neverList'); never.textContent = '';
  d.never_shown.forEach(t => {
    const li = h('li');
    li.append(h('span', { class: 'neverList__ic' }, '✕'));
    li.append(h('span', {}, t));
    never.append(li);
  });

  const os = $('#optinShares'); os.textContent = '';
  const o = d.opt_in_shares;
  if (o.reportable) {
    os.append(h('div', { class: 'kpi__value' }, `${o.count} shared`));
    os.append(h('p', { class: 'card__sub' }, 'Above the reporting floor, so the shared content is included above.'));
  } else {
    const box = h('div', { class: 'blocked' });
    box.style.padding = '26px 20px';
    box.append(h('h4', { class: 'blocked__title' }, 'Withheld'));
    box.append(h('p', { class: 'blocked__text' }, o.suppressed_message));
    os.append(box);
  }

  const pr = $('#principles'); pr.textContent = '';
  PRINCIPLES.forEach(([n, t, dd]) => {
    const c = h('div', { class: 'principle' });
    c.append(h('div', { class: 'principle__n' }, n));
    c.append(h('h4', { class: 'principle__t' }, t));
    c.append(h('p', { class: 'principle__d' }, dd));
    pr.append(c);
  });
}

/* ==========================================================================
   Boot
   ========================================================================== */
async function load() {
  try {
    const [a, b] = await Promise.all([
      fetch('../data/employee_insight.json').then(r => r.ok ? r.json() : Promise.reject()),
      fetch('../data/employer_view.json').then(r => r.ok ? r.json() : Promise.reject()),
    ]);
    return { employee: a, employer: b, live: true };
  } catch {
    return { employee: window.FALLBACK.employee, employer: window.FALLBACK.employer, live: false };
  }
}

(async function boot() {
  const { employee, employer, live } = await load();

  if (!live) {
    const b = $('#fallbackBanner');
    b.hidden = false;
    b.textContent = 'Showing the inlined snapshot of data/*.json — the live files could not be fetched (this happens when the page is opened with file://). Run ./serve.sh to read the real files.';
  }

  const bubble = makeHeroBubble();
  let teamSize = employer.k_anonymity.team_size;

  // The collapsed pill belongs to whichever view is on screen — both renders
  // run at boot, so it cannot be set as a side effect of either one.
  function syncPill() {
    const onEmployee = !$('#view-employee').hidden;
    $('#pillLabel').textContent = onEmployee ? 'Stress today' : 'Wed meetings / person';
    $('#pillValue').textContent = onEmployee
      ? String(employee.current.stress_score)
      : String(employer.structural_summary.hero.value);
  }

  renderEmployee(employee);
  renderEmployer(employer, teamSize);
  syncPill();

  const resize = () => { renderEmployer(employer, teamSize); syncPill(); bubble.reset(); };
  $('#sizeDown').onclick = () => { teamSize = Math.max(1, teamSize - 1); resize(); };
  $('#sizeUp').onclick   = () => { teamSize = Math.min(24, teamSize + 1); resize(); };

  // table toggles
  const wireTable = (btnId, tblId) => {
    const btn = $(btnId), tbl = $(tblId);
    btn.onclick = () => {
      const on = btn.getAttribute('aria-pressed') !== 'true';
      btn.setAttribute('aria-pressed', String(on));
      tbl.hidden = !on;
      btn.textContent = on ? 'Chart only' : 'Table';
    };
  };
  wireTable('#trendTableBtn', '#trendTable');
  wireTable('#heatTableBtn', '#heatTable');

  // view tabs
  const tabs = $$('.tab');
  tabs.forEach(tab => {
    tab.onclick = () => {
      tabs.forEach(t => {
        const on = t === tab;
        t.setAttribute('aria-selected', String(on));
        $('#' + t.getAttribute('aria-controls')).hidden = !on;
      });
      scrollTo({ top: 0, behavior: 'auto' });
      bubble.reset();
      if (tab.id === 'tab-employee') {
        drawTrend(employee.trend);
        drawFactors(employee.contributing_factors);
      } else {
        renderEmployer(employer, teamSize);
      }
      syncPill();
    };
    tab.onkeydown = ev => {
      if (ev.key !== 'ArrowRight' && ev.key !== 'ArrowLeft') return;
      const other = tabs[(tabs.indexOf(tab) + 1) % tabs.length];
      other.focus(); other.click();
    };
  });

  // charts are sized from their container, so redraw on resize
  let rt;
  addEventListener('resize', () => {
    clearTimeout(rt);
    rt = setTimeout(() => {
      if (!$('#view-employee').hidden) { drawTrend(employee.trend); drawFactors(employee.contributing_factors); }
      else if (!$('#stage-employer').hidden) { drawHeatmap(employer.meeting_density); }
    }, 140);
  });

  bubble.frame();
})();

})();
