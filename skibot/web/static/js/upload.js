function showReject(detail) {
  // 预检拒绝：结构化列出每条原因与重拍建议；其他错误按字符串展示
  const box = document.getElementById('reject');
  box.replaceChildren();
  if (detail && typeof detail === 'object' && detail.issues) {
    const h = document.createElement('strong');
    h.textContent = detail.message || '视频未通过上传预检';
    box.append(h);
    const ul = document.createElement('ul');
    ul.style.margin = '8px 0 0';
    ul.style.paddingLeft = '18px';
    for (const it of detail.issues) {
      const li = document.createElement('li');
      li.textContent = it.problem + ' —— ' + it.advice;
      ul.append(li);
    }
    box.append(ul);
  } else {
    box.textContent = String(detail || '上传失败');
  }
  box.style.display = 'block';
  box.classList.remove('show');
  void box.offsetWidth;
  box.classList.add('show');
}
// 拖放：把文件写回原生 input，并显示可换行的完整文件名
const dz = document.getElementById('dropzone');
const fi = dz && dz.querySelector('input[type=file]');
const fn = document.getElementById('filename');
if (dz && fi) {
  const showName = () => {
    if (fn) fn.textContent = fi.files && fi.files[0] ? fi.files[0].name : '';
  };
  fi.addEventListener('change', showName);
  ['dragenter', 'dragover'].forEach(ev => dz.addEventListener(ev, e => {
    e.preventDefault(); dz.classList.add('dragover');
  }));
  ['dragleave', 'drop'].forEach(ev => dz.addEventListener(ev, e => {
    e.preventDefault(); dz.classList.remove('dragover');
  }));
  dz.addEventListener('drop', e => {
    const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (!f) return;
    try {
      const dt = new DataTransfer();
      dt.items.add(f);
      fi.files = dt.files;
      showName();
    } catch (_) { /* 旧浏览器不支持赋值，保留原生选择 */ }
  });
}
// 水平档卡片：点选写入隐藏字段；点击同时给一个播放窗口（触屏没有 hover）。
// 窗口取各档动画周期的整数倍，播完正好落回起始姿态，不在半途定住
document.querySelectorAll('.level-card').forEach(card => {
  let playTimer = 0;
  const PERIOD = {novice: 4800, intermediate: 3200, advanced: 2400};
  const CYCLES = {novice: 1, intermediate: 1, advanced: 2};
  const ms = (PERIOD[card.dataset.level] || 4800) * (CYCLES[card.dataset.level] || 1);
  card.addEventListener('click', () => {
    document.querySelectorAll('.level-card').forEach(c =>
      c.setAttribute('aria-pressed', String(c === card)));
    document.getElementById('level').value = card.dataset.level;
    card.classList.remove('is-playing');
    void card.offsetWidth;
    card.classList.add('is-playing');
    clearTimeout(playTimer);
    playTimer = setTimeout(() => card.classList.remove('is-playing'), ms);
  });
});
document.getElementById('f').addEventListener('submit', (e) => {
  e.preventDefault();
  const form = e.target;
  const btn = document.getElementById('go'), msg = document.getElementById('msg');
  const progress = document.getElementById('progress');
  const bar = document.getElementById('progress-bar');
  const pctEl = document.getElementById('pct');
  const rejectBox = document.getElementById('reject');
  rejectBox.style.display = 'none';
  rejectBox.classList.remove('show');
  btn.disabled = true;
  btn.setAttribute('aria-busy', 'true');
  form.setAttribute('aria-busy', 'true');
  msg.textContent = '上传中…';
  progress.hidden = false; pctEl.hidden = false;
  bar.style.width = '0%'; pctEl.textContent = '0%';
  const busy = setTimeout(() => btn.classList.add('is-busy'), 150);
  const clearBusy = () => {
    clearTimeout(busy);
    btn.classList.remove('is-busy');
    btn.disabled = false;
    btn.removeAttribute('aria-busy');
    form.removeAttribute('aria-busy');
    progress.hidden = true;
    pctEl.hidden = true;
    bar.style.width = '0%';
  };
  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/api/upload');
  xhr.upload.onprogress = (ev) => {
    if (!ev.lengthComputable) return;
    const pct = Math.min(100, Math.round(ev.loaded / ev.total * 100));
    bar.style.width = pct + '%';
    pctEl.textContent = pct + '%';
  };
  xhr.upload.onload = () => {
    bar.style.width = '100%'; pctEl.textContent = '100%';
    msg.textContent = '上传完成，预检中…';
  };
  xhr.onload = () => {
    let j;
    try { j = JSON.parse(xhr.responseText); }
    catch (_) { j = {detail: '服务端返回了无法解析的响应'}; }
    if (xhr.status < 200 || xhr.status >= 300) {
      clearBusy(); msg.textContent = '';
      showReject(j.detail);
      return;
    }
    // 预检放行说明走服务端传递（/tasks?notes=…），不再经浏览器 sessionStorage：
    // 直接落到任务页刷新、二次进入也还在
    const notes = Array.isArray(j.precheck_notes) && j.precheck_notes.length
      ? '?notes=' + encodeURIComponent(j.precheck_notes.join('；')) : '';
    location.href = '/tasks' + notes;
  };
  xhr.onerror = () => { clearBusy(); msg.textContent = ''; showReject('上传失败：网络连接中断'); };
  xhr.onabort = () => { clearBusy(); msg.textContent = ''; showReject('上传已取消'); };
  xhr.send(new FormData(form));
});
