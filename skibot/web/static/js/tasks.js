(() => {
  const errBox = document.getElementById('taskerr');
  const pollNote = document.getElementById('poll-note');
  const live = document.getElementById('task-live');
  const rows = new Map();
  document.querySelectorAll('tr[data-id]').forEach(r => rows.set(r.dataset.id, r));
  let timer = null;

  function showError(msg) {
    if (!errBox) return;
    errBox.textContent = msg;
    errBox.hidden = false;
  }

  function fmtElapsed(s) {
    return Math.floor(s / 60) + ':' + String(Math.floor(s % 60)).padStart(2, '0');
  }
  function renderStatus(td, t) {
    td.className = 'st-' + t.status;
    td.dataset.status = t.status;
    td.replaceChildren();
    td.append(document.createTextNode(t.status_label || t.status));
    if (t.status === 'running' && t.elapsed_sec != null) {
      const s = document.createElement('span');
      s.className = 'muted'; s.dataset.elapsed = '';
      s.textContent = ' 已用 ' + fmtElapsed(t.elapsed_sec);
      td.append(s);
    }
    if (t.status === 'queued' && t.queue_position) {
      const s = document.createElement('span');
      s.className = 'muted'; s.dataset.queue = '';
      s.textContent = ' 第 ' + t.queue_position + ' 位';
      td.append(s);
    }
    if (t.status === 'failed' && t.error) {
      const d = document.createElement('div');
      d.className = 'muted';
      d.textContent = t.error;
      td.append(d);
    }
  }

  function renderAction(td, t) {
    td.replaceChildren();
    if (t.status === 'done' && t.report_url) {
      const a = document.createElement('a');
      a.className = 'ghost-link';
      a.href = t.report_url;
      a.textContent = '查看报告';
      td.append(a);
    } else if (t.status === 'failed') {
      const b = document.createElement('button');
      b.className = 'retry';
      b.dataset.id = t.id;
      b.textContent = '重试';
      b.onclick = () => retry(b);
      td.append(b);
    } else if (t.status === 'queued') {
      const b = document.createElement('button');
      b.className = 'cancel';
      b.dataset.id = t.id;
      b.textContent = '取消';
      b.onclick = () => cancel(b);
      td.append(b);
    }
  }

  async function retry(btn) {
    btn.disabled = true;
    btn.classList.add('is-busy');
    try {
      const r = await fetch('/api/tasks/' + btn.dataset.id + '/retry', {method: 'POST'});
      if (!r.ok) {
        let detail = r.status;
        try { detail = (await r.json()).detail || r.status; } catch (_) {}
        showError('重试失败：' + detail);
        // 404/409 说明页面状态已过期，刷新一次同步
        if (r.status === 404 || r.status === 409) { location.reload(); return; }
        btn.disabled = false;
        btn.classList.remove('is-busy');
        return;
      }
      location.reload();
    } catch (err) {
      btn.disabled = false;
      btn.classList.remove('is-busy');
      showError('重试失败：' + err.message);
    }
  }

  async function cancel(btn) {
    if (!window.confirm('取消这个排队任务？')) return;
    btn.disabled = true;
    btn.classList.add('is-busy');
    try {
      const r = await fetch('/api/tasks/' + btn.dataset.id + '/cancel', {method: 'POST'});
      if (!r.ok) {
        let detail = r.status;
        try { detail = (await r.json()).detail || r.status; } catch (_) {}
        showError('取消失败：' + detail);
        if (r.status === 404 || r.status === 409) { location.reload(); return; }
        btn.disabled = false;
        btn.classList.remove('is-busy');
        return;
      }
      location.reload();
    } catch (err) {
      btn.disabled = false;
      btn.classList.remove('is-busy');
      showError('取消失败：' + err.message);
    }
  }

  document.querySelectorAll('button.retry').forEach(b => b.onclick = () => retry(b));
  document.querySelectorAll('button.cancel').forEach(b => b.onclick = () => cancel(b));

  async function poll() {
    timer = null;
    if (document.hidden) return;
    try {
      const res = await fetch('/api/tasks', {headers: {'Accept': 'application/json'}});
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const list = await res.json();
      let active = false;
      for (const t of list) {
        if (t.status === 'queued' || t.status === 'running') active = true;
        const row = rows.get(t.id);
        if (!row) { location.reload(); return; }
        const td = row.querySelector('td[data-status]');
        const act = row.querySelector('td[data-action]');
        if (td) renderStatus(td, t);
        if (act) renderAction(act, t);
      }
      if (live) live.textContent = '任务状态已更新';
      if (pollNote) pollNote.hidden = !active;
      if (active) timer = setTimeout(poll, 5000);
    } catch (err) {
      timer = setTimeout(poll, 10000);
    }
  }

  if (document.querySelector('td.st-queued, td.st-running')) timer = setTimeout(poll, 5000);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && timer == null && document.querySelector('td.st-queued, td.st-running')) poll();
  });
})();
