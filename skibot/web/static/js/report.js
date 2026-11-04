(() => {
  const reduce = matchMedia('(prefers-reduced-motion: reduce)');

  /* 动态数字：指标读数进入视口后从 0 滚到中位数 */
  const nums = [...document.querySelectorAll('.metric-num')].map(el => {
    const node = [...el.childNodes].find(n => n.nodeType === 3 && n.textContent.trim());
    if (!node) return null;
    const raw = node.textContent.trim();
    const val = parseFloat(raw);
    if (!isFinite(val)) return null;
    return {el, node, raw, val, dec: (raw.split('.')[1] || '').length, done: false};
  }).filter(Boolean);
  if (!reduce.matches && nums.length) {
    const ease = t => 1 - Math.pow(1 - t, 3);
    const run = n => {
      n.node.textContent = n.dec ? '0.' + '0'.repeat(n.dec) : '0';
      const t0 = performance.now();
      const step = now => {
        if (n.canceled) { n.node.textContent = n.raw; return; }
        const t = Math.min(1, (now - t0) / 900);
        if (t >= 1) { n.node.textContent = n.raw; return; }
        const v = n.val * ease(t);
        n.node.textContent = n.dec ? v.toFixed(n.dec) : String(Math.round(v));
        requestAnimationFrame(step);
      };
      requestAnimationFrame(step);
    };
    if ('IntersectionObserver' in window) {
      const io = new IntersectionObserver(entries => {
        for (const e of entries) {
          if (!e.isIntersecting) continue;
          const n = nums.find(x => x.el === e.target);
          if (n && !n.done) { n.done = true; run(n); }
          io.unobserve(e.target);
        }
      }, {threshold: .35});
      nums.forEach(n => io.observe(n.el));
    } else nums.forEach(run);
  }

  /* 动态进度：参考带填充与标记进入视口后再生长 */
  const bands = [...document.querySelectorAll('.band')];
  if (bands.length) {
    if (reduce.matches || !('IntersectionObserver' in window)) {
      bands.forEach(b => { b.classList.add('prep', 'in'); });
    } else {
      bands.forEach(b => b.classList.add('prep'));
      const io = new IntersectionObserver(entries => {
        for (const e of entries) {
          if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); }
        }
      }, {threshold: .55});
      bands.forEach(b => io.observe(b));
    }
  }

  /* 直接打印时，没滚到过的卡片还停在起始值：先把数字与参考带落到终态 */
  addEventListener('beforeprint', () => {
    nums.forEach(n => { n.canceled = true; n.node.textContent = n.raw; });
    bands.forEach(b => b.classList.add('in'));
  });
})();
(() => {
  const picker = document.getElementById('picker');
  const TASK_ID = picker ? picker.dataset.taskId : '';
  let restore = null;
  try { restore = JSON.parse(sessionStorage.getItem('report_restore') || 'null'); } catch (_) {}
  if (restore && restore.id === TASK_ID) {
    sessionStorage.removeItem('report_restore');
    if ('scrollRestoration' in history) history.scrollRestoration = 'manual';
    requestAnimationFrame(() => window.scrollTo(0, restore.y || 0));
    if (restore.t != null) {
      const v = document.getElementById('v3d') || document.getElementById('vfallback');
      if (v) v.addEventListener('loadedmetadata', () => {
        v.currentTime = Math.min(restore.t, v.duration || restore.t);
      }, {once: true});
    }
  }
  function remember() {
    const v = document.getElementById('v3d') || document.getElementById('vfallback');
    try {
      sessionStorage.setItem('report_restore', JSON.stringify({
        id: TASK_ID, y: window.scrollY, t: v ? v.currentTime : null,
      }));
    } catch (_) {}
  }
  document.querySelectorAll('button.pick').forEach(b => b.onclick = () => {
    if (b.dataset.current) return;
    const msg = document.getElementById('pickmsg');
    document.querySelectorAll('button.pick').forEach(x => x.disabled = true);
    b.classList.add('is-busy');
    msg.textContent = '正在切换…';
    const body = new FormData(); body.append('person', b.dataset.id);
    const fail = err => {
      document.querySelectorAll('button.pick').forEach(x => {
        x.disabled = false;
        x.classList.remove('is-busy');
      });
      msg.textContent = '切换失败：' + err.message;
    };
    let attempts = 0;
    const poll = async () => {
      if (document.hidden) { setTimeout(() => poll().catch(fail), 2000); return; }
      if (++attempts > 120) throw new Error('切换超时，请刷新页面查看结果');
      const j = await (await fetch('/api/tasks/' + TASK_ID)).json();
      if (!j.reselecting) {
        if (j.reselect_error) throw new Error(j.reselect_error);
        remember();
        location.reload();
        return;
      }
      setTimeout(() => poll().catch(fail), 1000);
    };
    fetch('/api/tasks/' + TASK_ID + '/person', {method: 'POST', body})
      .then(async r => {
        if (!r.ok) {
          let detail = r.status;
          try { detail = (await r.json()).detail || r.status; } catch (_) {}
          throw new Error(detail);
        }
        setTimeout(() => poll().catch(fail), 800);
      })
      .catch(fail);
  });

  /* 悬停大图：精确指针下在缩略图旁浮出放大预览，自动避让视口边缘 */
  const fine = matchMedia('(hover:hover) and (pointer:fine)');
  const picks = [...document.querySelectorAll('button.pick')];
  if (fine.matches && picks.length) {
    const peek = document.createElement('div');
    peek.className = 'pick-peek';
    peek.setAttribute('aria-hidden', 'true');
    const pimg = document.createElement('img');
    const plab = document.createElement('span');
    plab.className = 'pick-peek-label';
    peek.append(pimg, plab);
    document.body.append(peek);
    const place = btn => {
      const img = btn.querySelector('img');
      if (!img) return false;
      pimg.src = img.src;
      pimg.alt = '';
      const lab = btn.querySelector('.pick-label');
      plab.textContent = lab ? lab.textContent : '';
      const r = btn.getBoundingClientRect();
      const w = 264, h = 300;
      let left = r.right + 14;
      if (left + w > innerWidth - 12) left = r.left - w - 14;
      if (left < 12) left = Math.max(12, Math.min(innerWidth - w - 12, r.left));
      let top = r.top - 10;
      top = Math.max(12, Math.min(innerHeight - h - 12, top));
      peek.style.left = Math.round(left) + 'px';
      peek.style.top = Math.round(top) + 'px';
      return true;
    };
    let hideTimer = 0;
    const show = btn => {
      clearTimeout(hideTimer);
      if (place(btn)) peek.classList.add('show');
    };
    const hide = () => {
      hideTimer = setTimeout(() => peek.classList.remove('show'), 60);
    };
    picks.forEach(btn => {
      btn.addEventListener('pointerenter', e => {
        if (e.pointerType === 'mouse') show(btn);
      });
      btn.addEventListener('pointerleave', hide);
      btn.addEventListener('focus', () => show(btn));
      btn.addEventListener('blur', hide);
    });
    addEventListener('scroll', () => peek.classList.remove('show'), {passive: true});
  }
})();
  (() => {
    const v = document.getElementById('vfallback');
    const p = document.getElementById('video-error');
    if (v && p) v.addEventListener('error', () => { p.hidden = false; });
  })();
