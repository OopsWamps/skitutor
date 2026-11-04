(() => {
  const reduce = matchMedia('(prefers-reduced-motion: reduce)');

  /* ---------- 顶部栏：滚动后凝结成霜玻璃 + 药丸高亮跟随 ---------- */
  const nav = document.getElementById('topnav');
  const links = document.getElementById('navlinks');
  const pill = document.getElementById('navpill');
  // 整页跳转没有共享动画，这里用 sessionStorage 接住上一页的高亮位置，
  // 新页第一次定位时从旧项滑过来，看起来是同一枚药丸在移动
  const NAV_FROM = 'navFrom';
  let handoffDone = false, handoffActive = false;
  function movePill() {
    if (!links || !pill) return;
    const all = Array.from(links.querySelectorAll('a'));
    const cur = links.querySelector('a.active');
    if (!cur) { pill.classList.remove('on'); return; }
    const lr = links.getBoundingClientRect(), cr = cur.getBoundingClientRect();
    const targetX = cr.left - lr.left;
    let fromIdx = null;
    try { fromIdx = sessionStorage.getItem(NAV_FROM); } catch (_) {}
    const fromEl = fromIdx == null ? null : all[Number(fromIdx)];
    if (fromEl && fromEl !== cur && !handoffDone && !reduce.matches) {
      handoffDone = true;
      handoffActive = true;
      const fr = fromEl.getBoundingClientRect();
      // 先无过渡地落到上一页那一项，强制回流定住起点，再放开过渡滑到当前项
      pill.style.transition = 'none';
      pill.style.transform = 'translateX(' + (fr.left - lr.left) + 'px)';
      pill.style.width = fr.width + 'px';
      void pill.offsetWidth;
      pill.style.transition = '';
      pill.classList.add('on', 'is-handoff');
      requestAnimationFrame(() => {
        pill.style.transform = 'translateX(' + targetX + 'px)';
        pill.style.width = cr.width + 'px';
      });
      // 滑动期间不响应字体加载/改尺寸的重测，结束后再按最终字形校一次
      const settle = () => {
        if (!handoffActive) return;
        handoffActive = false;
        pill.removeEventListener('transitionend', onEnd);
        movePill();
      };
      const onEnd = e => { if (e.propertyName === 'transform') settle(); };
      pill.addEventListener('transitionend', onEnd);
      setTimeout(settle, 600);
      return;
    }
    if (handoffActive) return;
    pill.classList.remove('is-handoff');
    pill.style.width = cr.width + 'px';
    pill.style.transform = 'translateX(' + targetX + 'px)';
    pill.classList.add('on');
  }
  if (nav) {
    // 只有一处判定：滚动超过 8px 即结霜。哨兵与阈值并用会互相覆盖，已去掉
    const apply = () => nav.classList.toggle('scrolled', scrollY > 8);
    addEventListener('scroll', apply, { passive: true });
    apply();
    movePill();
    addEventListener('resize', movePill);
    // 字体加载完成后药丸宽度会变，重新量一次
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(movePill);
    // 任何跳转（导航、表单、品牌、返回）离开前都记下高亮停在哪一项
    addEventListener('pagehide', () => {
      const cur = links.querySelector('a.active');
      if (!cur) return;
      const i = Array.from(links.querySelectorAll('a')).indexOf(cur);
      if (i < 0) return;
      try { sessionStorage.setItem(NAV_FROM, String(i)); } catch (_) {}
    });
  }

  /* ---------- 磁吸按钮：精确指针下轻微跟随，离开时弹簧归位 ---------- */
  const finePointer = matchMedia('(hover:hover) and (pointer:fine)');
  if (finePointer.matches) {
    document.querySelectorAll('.btn, .feature--primary').forEach(el => {
      el.classList.add('magnet');
      let raf = 0, tx = 0, ty = 0;
      el.addEventListener('pointermove', e => {
        if (e.pointerType !== 'mouse' || reduce.matches) return;
        const r = el.getBoundingClientRect();
        tx = Math.max(-6, Math.min(6, (e.clientX - (r.left + r.width / 2)) * .12));
        ty = Math.max(-5, Math.min(5, (e.clientY - (r.top + r.height / 2)) * .14));
        el.classList.add('is-tracking');
        if (!raf) raf = requestAnimationFrame(() => {
          raf = 0;
          el.style.translate = tx.toFixed(1) + 'px ' + ty.toFixed(1) + 'px';
        });
      });
      el.addEventListener('pointerleave', () => {
        el.classList.remove('is-tracking');
        el.style.translate = '';
      });
    });
    reduce.addEventListener('change', () => {
      if (!reduce.matches) return;
      document.querySelectorAll('.magnet').forEach(el => {
        el.classList.remove('is-tracking');
        el.style.translate = '';
      });
    });
  }

  /* ---------- 聚光灯卡片：光斑跟随指针，位置写进背景层 ---------- */
  if (finePointer.matches) {
    document.querySelectorAll('.feature, .metric').forEach(el => {
      el.classList.add('spot');
      let raf = 0, px = 0, py = 0;
      el.addEventListener('pointerenter', e => {
        if (e.pointerType === 'mouse') el.style.setProperty('--spot-a', '1');
      });
      el.addEventListener('pointermove', e => {
        if (e.pointerType !== 'mouse') return;
        const r = el.getBoundingClientRect();
        px = e.clientX - r.left; py = e.clientY - r.top;
        if (!raf) raf = requestAnimationFrame(() => {
          raf = 0;
          el.style.setProperty('--sx', px.toFixed(0) + 'px');
          el.style.setProperty('--sy', py.toFixed(0) + 'px');
        });
      });
      el.addEventListener('pointerleave', () => {
        el.style.setProperty('--spot-a', '0');
      });
    });
  }

  /* ---------- 命令面板：Ctrl/⌘+K 打开，过滤后键盘直接执行 ---------- */
  const cmdk = document.getElementById('cmdk');
  const cmdkInput = document.getElementById('cmdk-input');
  const cmdkList = document.getElementById('cmdk-list');
  const cmdkOpenBtn = document.getElementById('cmdkopen');
  const cmdkKey = document.getElementById('cmdkkey');
  if (cmdkKey && /Mac|iPhone|iPad/.test(navigator.platform)) cmdkKey.textContent = '⌘ K';

  const CMDK_ICON = {
    upload: '<svg viewBox="0 0 24 24"><path d="M12 16V4m0 0L8 8m4-4 4 4M5 20h14"/></svg>',
    tasks: '<svg viewBox="0 0 24 24"><path d="M8 4h8"/><path d="M7 7h10a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V9a2 2 0 0 1 2-2Z"/><path d="M9 13h6M9 17h4"/></svg>',
    compare: '<svg viewBox="0 0 24 24"><rect x="3" y="5" width="7" height="14" rx="2"/><rect x="14" y="5" width="7" height="14" rx="2"/><path d="M8 12h8"/></svg>',
    report: '<svg viewBox="0 0 24 24"><path d="M6 3h9l4 4v14H6Z"/><path d="M14 3v5h5M9 13h6M9 17h4"/></svg>',
    print: '<svg viewBox="0 0 24 24"><path d="M7 8V4h10v4M7 17H5a2 2 0 0 1-2-2v-4a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v4a2 2 0 0 1-2 2h-2"/><path d="M7 14h10v7H7Z"/></svg>',
    download: '<svg viewBox="0 0 24 24"><path d="M12 4v11m0 0-4-4m4 4 4-4M5 20h14"/></svg>',
    refresh: '<svg viewBox="0 0 24 24"><path d="M20 11a8 8 0 1 0-2.3 6.3M20 5v6h-6"/></svg>',
  };

  function cmdkBase() {
    const items = [
      {label: '上传分析', hint: '回到首页，选择视频并开始分析', icon: 'upload',
       keys: '上传 首页 video', run() {
         if (location.pathname === '/') {
           const up = document.getElementById('upload');
           if (up) up.scrollIntoView({behavior: reduce.matches ? 'auto' : 'smooth'});
         } else location.href = '/#upload';
       }},
      {label: '任务记录', hint: '排队、进行中与历史任务', icon: 'tasks',
       keys: '任务 列表 history', run() { location.href = '/tasks'; }},
      {label: '前后对比', hint: '两次分析按相位比较', icon: 'compare',
       keys: '对比 compare', run() { location.href = '/compare'; }},
    ];
    if (location.pathname.indexOf('/report/') === 0) {
      const dl = document.querySelector('.report-actions a[download]');
      if (dl) items.push({label: '下载标注视频', hint: '保存这次分析的标注 mp4',
                          icon: 'download', keys: '下载 视频 mp4',
                          run() { dl.click(); }});
      const pr = document.querySelector('.report-actions a[onclick]');
      if (pr) items.push({label: '打印 / 另存为 PDF', hint: '打开浏览器打印面板',
                          icon: 'print', keys: '打印 pdf 导出',
                          run() { window.print(); }});
    }
    if (location.pathname === '/tasks') {
      const all = document.querySelector('a[href*="show_all=1"]');
      if (all) items.push({label: '显示全部任务', hint: '展开超过 50 条的历史记录',
                           icon: 'tasks', keys: '全部 历史',
                           run() { all.click(); }});
      items.push({label: '刷新任务状态', hint: '重新读取当前排队与进度',
                  icon: 'refresh', keys: '刷新 reload',
                  run() { location.reload(); }});
    }
    return items;
  }

  let cmdkTasks = [];
  function loadCmdkTasks() {
    fetch('/api/tasks', {headers: {'Accept': 'application/json'}})
      .then(r => r.ok ? r.json() : [])
      .then(list => {
        cmdkTasks = (Array.isArray(list) ? list : [])
          .filter(t => t.status === 'done' && t.report_url)
          .slice(0, 8)
          .map(t => ({
            label: '报告 · ' + t.video,
            hint: (t.created || '').slice(5, 16).replace('T', ' ')
              + ' · ' + (t.level_label || t.level),
            icon: 'report',
            keys: '报告 report ' + t.video,
            run() { location.href = t.report_url; },
          }));
        if (cmdk && !cmdk.hidden) renderCmdk();
      })
      .catch(() => {});
  }

  let cmdkShown = [], cmdkSel = 0, cmdkLastFocus = null, cmdkHideTimer = 0,
      cmdkPrevOverflow = '';

  function paintCmdk() {
    [...cmdkList.children].forEach((el, i) => {
      if (el.id) el.setAttribute('aria-selected', String(i === cmdkSel));
    });
    const cur = cmdkList.children[cmdkSel];
    if (cur) {
      cur.scrollIntoView({block: 'nearest'});
      if (cmdkInput) cmdkInput.setAttribute('aria-activedescendant', cur.id || '');
    } else if (cmdkInput) cmdkInput.removeAttribute('aria-activedescendant');
  }

  function renderCmdk() {
    const q = (cmdkInput.value || '').trim().toLowerCase();
    const all = cmdkBase().concat(cmdkTasks);
    cmdkShown = q
      ? all.filter(it => (it.label + ' ' + it.hint + ' ' + (it.keys || ''))
          .toLowerCase().indexOf(q) !== -1)
      : all;
    if (cmdkSel >= cmdkShown.length) cmdkSel = Math.max(0, cmdkShown.length - 1);
    cmdkList.replaceChildren();
    if (!cmdkShown.length) {
      const empty = document.createElement('p');
      empty.className = 'cmdk-empty';
      empty.textContent = '没有匹配的命令';
      cmdkList.append(empty);
      paintCmdk();
      return;
    }
    cmdkShown.forEach((it, i) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'cmdk-item';
      b.id = 'cmdk-opt-' + i;
      b.tabIndex = -1;
      b.setAttribute('role', 'option');
      const ic = document.createElement('span');
      ic.className = 'ci';
      ic.innerHTML = CMDK_ICON[it.icon] || CMDK_ICON.report;
      const ct = document.createElement('span');
      ct.className = 'ct';
      const lb = document.createElement('b');
      lb.textContent = it.label;
      const sm = document.createElement('small');
      sm.textContent = it.hint || '';
      ct.append(lb, sm);
      b.append(ic, ct);
      b.addEventListener('pointermove', () => {
        if (cmdkSel !== i) { cmdkSel = i; paintCmdk(); }
      });
      b.addEventListener('click', () => runCmdk(i));
      cmdkList.append(b);
    });
    paintCmdk();
  }

  function runCmdk(i) {
    const it = cmdkShown[i];
    closeCmdk();
    if (it) setTimeout(() => it.run(), 0);
  }

  function openCmdk() {
    if (!cmdk || !cmdkInput) return;
    clearTimeout(cmdkHideTimer);
    cmdkLastFocus = document.activeElement;
    cmdkPrevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    cmdk.hidden = false;
    cmdkInput.value = '';
    cmdkSel = 0;
    renderCmdk();
    requestAnimationFrame(() => cmdk.classList.add('show'));
    cmdkInput.focus();
    loadCmdkTasks();
  }

  function closeCmdk() {
    if (!cmdk || cmdk.hidden) return;
    cmdk.classList.remove('show');
    document.body.style.overflow = cmdkPrevOverflow;
    cmdkHideTimer = setTimeout(() => { cmdk.hidden = true; },
                              reduce.matches ? 0 : 170);
    if (cmdkLastFocus && cmdkLastFocus.focus) cmdkLastFocus.focus();
  }

  if (cmdk && cmdkInput && cmdkList) {
    if (cmdkOpenBtn) cmdkOpenBtn.addEventListener('click', openCmdk);
    document.addEventListener('keydown', e => {
      if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        if (cmdk.hidden) openCmdk(); else closeCmdk();
        return;
      }
      if (cmdk.hidden) return;
      if (e.key === 'Escape') {
        e.preventDefault();
        closeCmdk();
      } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        if (!cmdkShown.length) return;
        e.preventDefault();
        const dir = e.key === 'ArrowDown' ? 1 : -1;
        cmdkSel = (cmdkSel + dir + cmdkShown.length) % cmdkShown.length;
        paintCmdk();
      } else if (e.key === 'Enter' && !e.isComposing) {
        if (!cmdkShown.length) return;
        e.preventDefault();
        runCmdk(cmdkSel);
      } else if (e.key === 'Tab') {
        e.preventDefault();
        cmdkInput.focus();
      }
    });
    cmdkInput.addEventListener('input', () => { cmdkSel = 0; renderCmdk(); });
    cmdk.addEventListener('mousedown', e => { if (e.target === cmdk) closeCmdk(); });
  }

  /* ---------- 雪花：一张 64×64 精灵，逐帧只做 drawImage ---------- */
  const cv = document.getElementById('snow');
  if (cv) {
    const g = cv.getContext('2d');
    const DPR = Math.min(devicePixelRatio || 1, 2);
    const lowDensity = document.body.dataset.density === 'low';
    let W = 0, H = 0, flakes = [], wind = 8, windTarget = 8,
        pointerX = null, raf = 0, last = 0, t = 0;

    // 六角雪花精灵：冷蓝晶体 + 淡光晕（浅底上也看得见），逐帧只做 drawImage
    const sprite = document.createElement('canvas');
    sprite.width = sprite.height = 64;
    (() => {
      const c = sprite.getContext('2d');
      const grad = c.createRadialGradient(32, 32, 0, 32, 32, 30);
      grad.addColorStop(0, 'rgba(255,255,255,.95)');
      grad.addColorStop(.2, 'rgba(150,205,232,.55)');
      grad.addColorStop(.55, 'rgba(120,180,215,.14)');
      grad.addColorStop(1, 'rgba(120,180,215,0)');
      c.fillStyle = grad;
      c.beginPath(); c.arc(32, 32, 30, 0, 7); c.fill();
      c.strokeStyle = 'rgba(52,118,158,.72)';
      c.lineWidth = 2; c.lineCap = 'round';
      for (let i = 0; i < 3; i++) {           // 六角晶体：三条直径 + 每端两枝
        c.save(); c.translate(32, 32); c.rotate(i * Math.PI / 3);
        c.beginPath(); c.moveTo(0, -22); c.lineTo(0, 22); c.stroke();
        c.beginPath();
        c.moveTo(0, -14); c.lineTo(6, -20); c.moveTo(0, -14); c.lineTo(-6, -20);
        c.moveTo(0, 14); c.lineTo(6, 20); c.moveTo(0, 14); c.lineTo(-6, 20);
        c.stroke(); c.restore();
      }
    })();

    const rnd = () => Math.random();
    function makeFlake(fromTop) {
      const depth = Math.pow(rnd(), 1.7);
      return {
        x: rnd() * W, y: fromTop ? -20 : rnd() * H,
        // 大小刻意拉开：近处大颗、远处小点，比例差到 8 倍
        r: 0.8 + Math.pow(depth, 1.15) * 6.4,
        vy: 9 + depth * 38,
        windBase: 8 + depth * 14,
        swayAmp: 6 + depth * 22,
        swayHz: 0.05 + rnd() * 0.12,
        phase: rnd() * Math.PI * 2,
        alpha: 0.22 + depth * 0.46,
        spin: (rnd() - 0.5) * 0.6 * depth,
        rot: rnd() * Math.PI * 2,
      };
    }
    function resize() {
      W = innerWidth; H = innerHeight;
      cv.width = Math.round(W * DPR); cv.height = Math.round(H * DPR);
      cv.style.width = W + 'px'; cv.style.height = H + 'px';
      g.setTransform(DPR, 0, 0, DPR, 0, 0);
      // 数量加密：1080p 下约 240 颗，大屏封顶 400，窄屏仍有 130
      const n = Math.round(W * H / 4800);
      const count = Math.max(130, Math.min(400, n)) * (lowDensity ? 0.55 : 1);
      flakes = Array.from({ length: Math.round(count) }, () => makeFlake(false));
    }
    function step(dt) {
      const windBase = pointerX == null ? 8 : 8 + (pointerX / W - 0.5) * 18;
      windTarget = windBase + 6 * Math.sin(performance.now() * 0.00012);
      wind += (windTarget - wind) * 0.04;
      t += dt;
      g.clearRect(0, 0, W, H);
      for (const f of flakes) {
        f.y += f.vy * dt;
        f.x += (wind + Math.sin(t * Math.PI * 2 * f.swayHz + f.phase) * f.swayAmp * 0.35) * dt;
        f.rot += f.spin * dt;
        if (f.y - f.r > H) { f.y = -f.r - rnd() * 40; f.x = rnd() * W; }
        if (f.x - f.r > W) f.x = -f.r;
        if (f.x + f.r < 0) f.x = W + f.r;
        const light = 0.72 + 0.28 * (1 - f.y / H);       // 高处清晰、贴地处淡
        const twinkle = 0.82 + 0.18 * Math.sin(t * 1.7 + f.phase);
        const s = f.r * 3.2;
        g.globalAlpha = Math.min(1, f.alpha * light * twinkle);
        g.save(); g.translate(f.x, f.y); g.rotate(f.rot);
        g.drawImage(sprite, -s / 2, -s / 2, s, s);
        g.restore();
      }
      g.globalAlpha = 1;
    }
    function loop(now) {
      const dt = Math.min(1 / 30, (now - last) / 1000 || 1 / 60);
      last = now;
      step(dt);
      raf = requestAnimationFrame(loop);
    }
    function start() {
      resize();
      if (reduce.matches) { step(0.016); return; }   // 静态一帧，不跑 rAF
      last = performance.now();
      raf = requestAnimationFrame(loop);
    }
    function stop() { cancelAnimationFrame(raf); raf = 0; }
    // reduced-motion 下没有 rAF 循环：resize 重建完雪花必须亲手补一帧静态画
    addEventListener('resize', () => { clearTimeout(start._t); start._t = setTimeout(() => {
      resize(); if (reduce.matches) step(0.016);
    }, 150); });
    addEventListener('pointermove', e => { pointerX = e.clientX; }, { passive: true });
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) stop();
      else if (!reduce.matches && !raf) { last = performance.now(); raf = requestAnimationFrame(loop); }
    });
    reduce.addEventListener('change', () => { stop(); start(); });
    start();
  }

})();
