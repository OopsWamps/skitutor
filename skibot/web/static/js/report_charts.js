// 报告页指标卡图表：逐帧角度曲线（手绘 SVG，零依赖）。
// 数据来自三维回放卡已下载的动画 JSON：replay.js 数据就绪后广播
// skibot:replay 事件（携带 data 与 seek 接口）。二维降级任务没有这份数据，
// 曲线容器保持 hidden，卡片退回中位数 + 参考带的现状。
// 交互：悬停十字线读数（时刻/角度/相位），点击把回放 seek 到该时刻；
// 回放播放时在曲线上画一个同步播放头点。
(() => {
  // 报告 metrics[].metric → 动画 JSON series 键。唯一不同名的是躯干相
  // trunk_lean_sagittal（映射见 compare.PHASE_TO_REPORT 的反向）
  const TO_SERIES = {hip_flexion: 'hip_flexion', knee_flexion: 'knee_flexion', trunk_lean: 'trunk_lean_sagittal'};
  // 相位色与 replay.js 一样单点取自 CSS --phase-* token
  const PH_COLOR = (() => {
    const css = getComputedStyle(document.documentElement);
    const v = n => (css.getPropertyValue(n) || '').trim();
    return {
      entry: v('--phase-entry') || '#067fa0',
      control: v('--phase-control') || '#1a7d38',
      exit: v('--phase-exit') || '#6658cc',
      '': v('--dim') || '#54748a',
    };
  })();
  const PH_CN = {entry: '入弯', control: '控制', exit: '出弯', '': '弯间'};
  const NS = 'http://www.w3.org/2000/svg';
  const W = 560, H = 76;      // viewBox；页面里随卡片宽度拉伸
  const TOP = 9, PBOT = 58;   // 曲线绘图区 y 范围；下方留给相位条
  const STRIP_Y = 66, STRIP_H = 5;

  let replay = null;
  let tip = null;

  function ingest(detail) {
    replay = detail;
    try {
      build();
    } catch (err) {
      // 图表任何失败都不影响报告本身；痕迹留在 body 上便于诊断
      document.body.dataset.chartErr = (err && (err.stack || err.message)) || String(err);
    }
  }

  // 数据桥：replay.js 先落 window 再广播——defer 脚本挂监听可能晚于
  // 本地 fetch 的 resolve，先查桥补接，再听后续事件
  document.addEventListener('skibot:replay', e => ingest(e.detail));
  if (window.__skibotReplay) ingest(window.__skibotReplay);

  function ensureTip() {
    if (tip) return tip;
    tip = document.createElement('div');
    tip.className = 'chart-tip';
    document.body.append(tip);
    return tip;
  }

  function el(name, attrs) {
    const n = document.createElementNS(NS, name);
    for (const k in attrs) n.setAttribute(k, attrs[k]);
    return n;
  }

  function build() {
    const data = replay.data || {};
    const all = data.series || {};
    const fps = data.fps || 25;
    // phaseOf 由 replay.js 推好并随数据桥带来（含旧动画缺 phase_of 的回退）
    const phaseOf = Array.isArray(replay.phaseOf) ? replay.phaseOf : [];
    document.querySelectorAll('.metric[data-basis="3d"]').forEach(card => {
      const s = all[TO_SERIES[card.dataset.metric]];
      if (Array.isArray(s) && s.some(v => v != null && isFinite(v)))
        buildSpark(card, s, fps, phaseOf);
    });
  }

  function buildSpark(card, s, fps, phaseOf) {
    const box = card.querySelector('[data-spark]');
    if (!box || box.querySelector('svg')) return;
    const n = s.length;
    let lo = Infinity, hi = -Infinity;
    for (const v of s) {
      if (v == null || !isFinite(v)) continue;
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    }
    const rLo = parseFloat(card.dataset.refLo), rHi = parseFloat(card.dataset.refHi);
    // 三维卡目前 reference 恒 None（三维参考带待建，issue 09/12）：
    // hasRef 路径今日不触发，band3d 落地后曲线自动带参考域
    const hasRef = isFinite(rLo) && isFinite(rHi) && rHi > rLo;
    if (hasRef) {
      lo = Math.min(lo, rLo);
      hi = Math.max(hi, rHi);
    }
    const span = (hi - lo) || 1;
    lo -= span * .07;
    hi += span * .07;
    const yOf = v => PBOT - (v - lo) / (hi - lo) * (PBOT - TOP);
    const xOf = f => (n > 1 ? f / (n - 1) : .5) * W;

    const svg = el('svg', {viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: 'none'});
    svg.setAttribute('aria-hidden', 'true');

    if (hasRef) {
      svg.append(el('rect', {class: 'sp-ref', x: 0, y: yOf(rHi), width: W, height: yOf(rLo) - yOf(rHi)}));
      svg.append(el('line', {class: 'sp-refline', x1: 0, x2: W, y1: yOf(rLo), y2: yOf(rLo)}));
      svg.append(el('line', {class: 'sp-refline', x1: 0, x2: W, y1: yOf(rHi), y2: yOf(rHi)}));
    }

    // 相位条：按 run 切分着色，段间留 1px 雪面缝；整段都没有弯信息时不画
    const hasPhases = phaseOf.some(ph => ph);
    if (hasPhases) {
      let runStart = 0;
      for (let f = 1; f <= n; f++) {
        if (f === n || phaseOf[f] !== phaseOf[runStart]) {
          const x = runStart / n * W, w = (f - runStart) / n * W;
          if (w > .8) svg.append(el('rect', {class: 'sp-strip', x: x + .5, y: STRIP_Y,
            width: Math.max(1, w - 1), height: STRIP_H, rx: 1, fill: PH_COLOR[phaseOf[runStart]] || PH_COLOR['']}));
          runStart = f;
        }
      }
    }

    // 曲线：按像素列做 min/max 保形抽稀（记录原生序，不造假峰）
    const cols = 160;
    let d = '';
    const put = (x, y, first) => { d += (first ? 'M' : 'L') + x.toFixed(1) + ' ' + y.toFixed(1); };
    let started = false;
    for (let c = 0; c < cols; c++) {
      const a = Math.floor(c * n / cols), b = Math.max(a + 1, Math.floor((c + 1) * n / cols));
      let loI = -1, hiI = -1;
      for (let i = a; i < b; i++) {
        const v = s[i];
        if (v == null || !isFinite(v)) continue;
        if (loI < 0 || v < s[loI]) loI = i;
        if (hiI < 0 || v >= s[hiI]) hiI = i;
      }
      if (loI < 0) continue;
      const x = xOf((a + b - 1) / 2);
      if (!started || Math.abs(xOf(hiI) - xOf(loI)) < 1) {
        put(x, yOf(s[loI]), !started); put(x, yOf(s[hiI]), false);
      } else if (loI < hiI) {
        put(xOf(loI), yOf(s[loI]), false); put(xOf(hiI), yOf(s[hiI]), false);
      } else {
        put(xOf(hiI), yOf(s[hiI]), false); put(xOf(loI), yOf(s[loI]), false);
      }
      started = true;
    }
    if (started) svg.append(el('path', {class: 'sp-curve', d}));

    const xhair = el('line', {class: 'sp-xhair', y1: TOP - 3, y2: PBOT + 3, visibility: 'hidden'});
    const hdot = el('circle', {class: 'sp-hdot', r: 3.5, visibility: 'hidden'});
    const play = el('circle', {class: 'sp-play', r: 4, visibility: 'hidden'});
    svg.append(xhair, hdot, play);

    const cap = el('rect', {x: 0, y: 0, width: W, height: H, fill: 'transparent'});
    svg.append(cap);

    const name = (card.querySelector('h2') || {}).textContent || '指标';
    const dur = (n / fps);
    box.setAttribute('role', 'img');
    box.setAttribute('aria-label',
      `${name}逐帧曲线，共约 ${dur.toFixed(0)} 秒；悬停可读数，点击可把回放定位到对应时刻`);
    box.removeAttribute('hidden');
    box.append(svg);

    const fmtT = t => t < 60 ? t.toFixed(1) + ' s'
      : Math.floor(t / 60) + ':' + String(Math.round(t % 60)).padStart(2, '0') + ' s';

    // 悬停：十字线 + 读数浮签；点击：回放 seek
    const move = e => {
      const r = svg.getBoundingClientRect();
      const frac = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
      const f = Math.round(frac * (n - 1));
      const val = s[f];
      const x = xOf(f);
      xhair.setAttribute('x1', x); xhair.setAttribute('x2', x);
      xhair.setAttribute('visibility', 'visible');
      if (val != null && isFinite(val)) {
        hdot.setAttribute('cx', x); hdot.setAttribute('cy', yOf(val));
        hdot.setAttribute('visibility', 'visible');
      } else hdot.setAttribute('visibility', 'hidden');
      const ph = phaseOf[f] || '';
      const t = ensureTip();
      t.innerHTML = `<span class="tt-ph" style="background:${PH_COLOR[ph]}"></span>`
        + `${fmtT(f / fps)} · ${val != null && isFinite(val) ? val.toFixed(1) + '°' : '—'} · ${PH_CN[ph] || PH_CN['']}`;
      t.classList.add('show');
      const tw = t.offsetWidth, th = t.offsetHeight;
      let left = e.clientX + 14;
      if (left + tw > innerWidth - 10) left = e.clientX - tw - 14;
      let top = r.top - th - 10;
      if (top < 8) top = Math.min(innerHeight - th - 8, r.bottom + 10);
      t.style.left = Math.round(left) + 'px';
      t.style.top = Math.round(top) + 'px';
      cap.onclick = () => replay.seek(f / fps);
    };
    const leave = () => {
      xhair.setAttribute('visibility', 'hidden');
      hdot.setAttribute('visibility', 'hidden');
      if (tip) tip.classList.remove('show');
    };
    cap.addEventListener('pointermove', move);
    cap.addEventListener('pointerdown', move);
    cap.addEventListener('pointerleave', leave);
    addEventListener('scroll', leave, {passive: true});

    // 播放头：回放播放时沿曲线移动（timeupdate 频率足够，不必 rAF）
    const v = document.getElementById('v3d');
    if (v) {
      let lastF = -1;
      v.addEventListener('timeupdate', () => {
        const f = Math.round(v.currentTime * fps);
        if (f === lastF) return;
        lastF = f;
        const val = f >= 0 && f < n ? s[f] : null;
        if (val != null && isFinite(val)) {
          play.setAttribute('cx', xOf(f));
          play.setAttribute('cy', yOf(val));
          play.setAttribute('visibility', 'visible');
        } else play.setAttribute('visibility', 'hidden');
      });
    }
  }
})();
