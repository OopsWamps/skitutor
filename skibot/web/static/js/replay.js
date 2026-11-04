(() => {
  const v = document.getElementById('v3d');
  const cv = document.getElementById('sk3d');
  const g = cv.getContext('2d');
  const strip = document.getElementById('tstrip');
  const phEl = document.getElementById('ph3d');
  const ovBox = document.getElementById('ov');
  const ovOn = document.getElementById('ov3d');
  const replayWrap = document.getElementById('replay-wrap');
  const replayStatus = document.getElementById('replay-status');
  const replayRetryWrap = document.getElementById('replay-retry-wrap');
  const replayRetry = document.getElementById('replay-retry');
  // 相位色单点取自 CSS 的 --phase-* token（控制相 #1a7d38 与入弯青全色觉
  // ΔE≥15，dataviz 校验器通过）；画布 #f3f9fc 上仍够对比。'' 即 --dim
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
  // 按 CSS 尺寸与 DPR 配画布，避免高分屏发糊
  let CW = 420, CH = 420;
  let redraw = () => {};   // resize 可能在动画数据到达前发生
  function fit() {
    const dpr = Math.min(devicePixelRatio || 1, 2);
    CW = cv.clientWidth || 420;
    CH = CW;
    cv.width = Math.round(CW * dpr);
    cv.height = Math.round(CH * dpr);
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  fit();
  addEventListener('resize', () => { fit(); redraw(); });

  fetch(replayWrap.dataset.animationUrl)
    .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
    .then(data => {
    if (!Array.isArray(data.frames) || !data.frames.length)
      throw new Error('动画数据为空');
    const fps = data.fps, frames = data.frames;
    const conf = data.confidence || [], edges = data.edges || [];
    const series = data.series || {};
    // 逐帧相位归属与中文标签由服务端算好（finalize.build_animation 的
    // phase_of / phase_labels，唯一事实源是 phases.phase_labels）；
    // 旧动画缺这两个字段时回退到本地按 turns 推
    let phaseOf = data.phase_of;
    const PH_CN = Object.assign({'': '弯间 / 未检出'}, data.phase_labels || {});
    if (!Array.isArray(phaseOf)) {
      phaseOf = new Array(frames.length).fill('');
      for (const t of ((data.phases || {}).turns) || [])
        for (const ph of ['entry', 'control', 'exit']) {
          const iv = t[ph];
          for (let f = iv.start_frame; f < iv.end_frame && f < frames.length; f++)
            phaseOf[f] = ph;
        }
    }
    let runStart = 0;
    for (let f = 1; f <= frames.length; f++) {
      if (f === frames.length || phaseOf[f] !== phaseOf[runStart]) {
        const seg = document.createElement('div');
        seg.style.cssText = 'position:absolute;top:0;bottom:0;background:'
          + PH_COLOR[phaseOf[runStart]] + ';left:' + (runStart / frames.length * 100)
          + '%;width:' + ((f - runStart) / frames.length * 100) + '%';
        strip.append(seg);
        runStart = f;
      }
    }
    const cur = document.createElement('div');
    cur.style.cssText = 'position:absolute;top:-3px;width:2px;height:18px;'
      + 'background:#0e3a55;box-shadow:0 0 6px rgba(14,58,85,.45);left:0';
    strip.append(cur);

    let scale = 0.3;
    const step = Math.max(1, Math.floor(frames.length / 200));
    for (let f = 0; f < frames.length; f += step)
      for (const j of frames[f] || [])
        if (j) scale = Math.max(scale, Math.abs(j[0]), Math.abs(j[1]));

    let yaw = -0.6, pitch = 0.15, zoom = 1, drag = null, pinch = null;
    const pts = new Map();
    cv.addEventListener('pointerdown', e => {
      if (e.pointerType === 'mouse' && e.button !== 0) return;
      pts.set(e.pointerId, [e.clientX, e.clientY]);
      try { cv.setPointerCapture(e.pointerId); } catch (_) {}
      if (pts.size === 2) {
        const [a, b] = [...pts.values()];
        pinch = { d: Math.hypot(a[0] - b[0], a[1] - b[1]), z: zoom };
        drag = null;
        return;
      }
      drag = [e.clientX, e.clientY];
      cv.style.cursor = 'grabbing';
    });
    cv.addEventListener('pointermove', e => {
      if (pts.has(e.pointerId)) pts.set(e.pointerId, [e.clientX, e.clientY]);
      if (pinch && pts.size >= 2) {
        const [a, b] = [...pts.values()];
        const d = Math.hypot(a[0] - b[0], a[1] - b[1]);
        if (pinch.d > 0) zoom = Math.max(0.3, Math.min(5, pinch.z * d / pinch.d));
        draw();
        return;
      }
      if (!drag) return;
      yaw += (e.clientX - drag[0]) * 0.01;
      pitch = Math.max(-1.4, Math.min(1.4, pitch + (e.clientY - drag[1]) * 0.01));
      drag = [e.clientX, e.clientY];
      draw();
    });
    const endDrag = e => {
      if (e && e.pointerId != null) pts.delete(e.pointerId);
      if (pts.size < 2) pinch = null;
      drag = null;
      cv.style.cursor = 'grab';
    };
    cv.addEventListener('pointerup', endDrag);
    cv.addEventListener('pointercancel', endDrag);
    cv.addEventListener('lostpointercapture', endDrag);
    cv.addEventListener('wheel', e => {
      e.preventDefault();
      zoom = Math.max(0.3, Math.min(5, zoom * (e.deltaY < 0 ? 1.1 : 0.9)));
      draw();
    }, {passive: false});
    cv.addEventListener('keydown', e => {
      const step = 0.08;
      let handled = true;
      switch (e.key) {
        case 'ArrowLeft': yaw -= step; break;
        case 'ArrowRight': yaw += step; break;
        case 'ArrowUp': pitch = Math.min(1.4, pitch + step); break;
        case 'ArrowDown': pitch = Math.max(-1.4, pitch - step); break;
        case '+': case '=': zoom = Math.min(5, zoom * 1.1); break;
        case '-': case '_': zoom = Math.max(0.3, zoom / 1.1); break;
        case '0': yaw = -0.6; pitch = 0.15; zoom = 1; break;
        default: handled = false;
      }
      if (handled) { e.preventDefault(); draw(); }
    });

    function project(p) {
      const cy = Math.cos(yaw), sy = Math.sin(yaw);
      const x1 = p[0] * cy + p[2] * sy, z1 = -p[0] * sy + p[2] * cy;
      const cp = Math.cos(pitch), sp = Math.sin(pitch);
      const y1 = p[1] * cp - z1 * sp;
      const s = Math.min(CW, CH) * 0.42 * zoom / scale;
      return [CW / 2 + x1 * s, CH / 2 - y1 * s];
    }
    function curFrame() {
      return Math.max(0, Math.min(frames.length - 1, Math.round(v.currentTime * fps)));
    }
    // 缺失关节序列化成 [null,null,null]（真值数组）：三分量齐全才算有效，
    // 否则会被投影到画布中心，画成叠在骨盆上的灰团
    const ok = p => p && p[0] != null && p[1] != null && p[2] != null;
    function draw() {
      const f = curFrame(), fr = frames[f] || [];
      const col = PH_COLOR[phaseOf[f]] || PH_COLOR[''];
      g.clearRect(0, 0, CW, CH);
      g.lineWidth = 3; g.lineCap = 'round'; g.strokeStyle = col;
      for (const [a, b] of edges) {
        const pa = fr[a], pb = fr[b];
        if (!ok(pa) || !ok(pb)) continue;
        const A = project(pa), B = project(pb);
        g.beginPath(); g.moveTo(A[0], A[1]); g.lineTo(B[0], B[1]); g.stroke();
      }
      for (let j = 0; j < fr.length; j++) {
        if (!ok(fr[j])) continue;
        const P = project(fr[j]);
        g.fillStyle = (conf[f] && conf[f][j] != null && conf[f][j] < 0.3)
          ? 'rgba(125,151,168,.45)' : '#12324a';
        g.beginPath(); g.arc(P[0], P[1], 3.5, 0, 7); g.fill();
      }
      phEl.textContent = 'f=' + f + '/' + frames.length
        + ' · ' + (PH_CN[phaseOf[f]] || PH_CN['']);
      cur.style.left = (frames.length > 1 ? f / (frames.length - 1) * 100 : 0) + '%';
      if (ovOn.checked) {
        const parts = [];
        for (const [k, cn] of [['hip_flexion', '屈髋'], ['knee_flexion', '膝'],
                               ['trunk_lean_sagittal', '前倾']])
          if (series[k]) {
            const val = series[k][f];
            parts.push(cn + ' ' + (val == null ? '—' : val.toFixed(1) + '°'));
          }
        ovBox.textContent = parts.join(' · ');
      } else ovBox.textContent = '';
    }
    replayWrap.setAttribute('aria-busy', 'false');
    if (replayStatus) replayStatus.hidden = true;
    // 数据就绪：先落到 window 桥再广播。report_charts.js 是 defer 脚本，
    // 挂监听可能晚于本地 fetch 的 resolve——只靠事件会漏接，桥保底
    const bridge = {
      data,
      phaseOf,   // 含旧动画缺 phase_of 字段时的本地回退，图表模块直接复用
      seek(t) { try { v.currentTime = Math.max(0, Math.min(v.duration || 0, t)); } catch (_) {} },
    };
    window.__skibotReplay = bridge;
    document.dispatchEvent(new CustomEvent('skibot:replay', {detail: bridge}));
    let raf = null, lastF = -1;
    function tick() {
      raf = null;
      if (v.paused || v.ended) { draw(); return; }
      const f = curFrame();
      if (f !== lastF) { lastF = f; draw(); }
      raf = requestAnimationFrame(tick);
    }
    function startLoop() {
      if (raf != null || v.paused || v.ended) return;
      lastF = -1;
      raf = requestAnimationFrame(tick);
    }
    function stopLoop() {
      if (raf != null) { cancelAnimationFrame(raf); raf = null; }
      lastF = -1;
      draw();
    }
    v.addEventListener('play', startLoop);
    v.addEventListener('playing', startLoop);
    v.addEventListener('pause', stopLoop);
    v.addEventListener('ended', stopLoop);
    v.addEventListener('seeked', () => { lastF = -1; draw(); });
    ovOn.addEventListener('change', draw);
    redraw = draw;
    draw();
    if (!v.paused) startLoop();
    v.addEventListener('error', () => {
      if (replayStatus) {
        replayStatus.hidden = false;
        replayStatus.textContent = '标注视频加载失败；三维骨架仍可在右侧拖动查看。';
      }
    });
    })
    .catch(err => {
      // 404（重跑删了动画）/ 网络失败 / JSON 损坏：给出可见提示，不留黑画布
      phEl.textContent = '回放数据加载失败：' + err.message;
      cv.setAttribute('aria-label', '三维骨架加载失败');
      replayWrap.setAttribute('aria-busy', 'false');
      if (replayStatus) {
        replayStatus.hidden = false;
        replayStatus.textContent = '回放数据加载失败：' + err.message;
      }
      if (replayRetryWrap && replayRetry) {
        replayRetryWrap.hidden = false;
        replayRetry.onclick = () => location.reload();
      }
    });
})();
