'use strict';

const root = document.getElementById('app-root');
const subtitle = document.getElementById('subtitle');

let csrfToken = '';
let currentJobId = null;
let pollingTimer = null;
let selectedFile = null;
let workflows = [];

// ── ユーティリティ ────────────────────────────────────────────────────────────

function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function render(htmlStr) {
  root.innerHTML = htmlStr;
}

const ERROR_MSG = {
  INVALID_PIN:       'PINが正しくありません。',
  SESSION_EXPIRED:   'セッションが期限切れです。ページを再読み込みしてください。',
  ROOM_EXPIRED:      '投入口の有効期限が切れました。',
  ROOM_STOPPED:      '投入口は終了しました。必要な場合は、投入口を開いた人にもう一度URLを発行してもらってください。',
  QUEUE_FULL:        '現在、投入口が混雑しています。しばらくしてからもう一度お試しください。',
  RATE_LIMITED:      'リクエストが多すぎます。しばらく待ってからお試しください。',
  INVALID_FILE_TYPE: '対応していないファイル形式です。JPG / PNG / WEBP のみアップロードできます。',
  FILE_TOO_LARGE:    'ファイルサイズが大きすぎます。20MB以下の画像を選択してください。',
  IMAGE_TOO_LARGE:   '画像の解像度が大きすぎます。4096px以下の画像を使用してください。',
  GENERATION_BUSY:   '現在フォルダ一括生成中のため受付できません。しばらくしてからお試しください。',
  GENERATION_FAILED: '生成に失敗しました。しばらくしてからもう一度お試しください。',
};

function getErrMsg(code, fallback) {
  return ERROR_MSG[code] || fallback || code;
}

// ── 画面: PIN 入力 ─────────────────────────────────────────────────────────────

function showPin(prefillPin) {
  subtitle.textContent = 'PIN を入力してください';
  render(`
    <div class="card">
      <div class="card-title">🔒 認証</div>
      <label for="pin-input">PIN (6桁)</label>
      <input type="tel" id="pin-input" maxlength="6" placeholder="000000" autocomplete="one-time-code">
      <div class="error-msg" id="pin-error"></div>
      <button class="btn" id="pin-btn">入室する</button>
    </div>
    <div class="card">
      <p style="font-size:13px;color:var(--text2);line-height:1.6">
        画像をアップロードすると、この投入口を開いているPCでAI動画生成が行われます。
        完成まで数分〜20分程度かかることがあります。
      </p>
    </div>
  `);
  const input = document.getElementById('pin-input');
  const btn   = document.getElementById('pin-btn');
  input.addEventListener('keydown', e => { if (e.key === 'Enter') submitPin(); });
  btn.addEventListener('click', submitPin);
  if (prefillPin) {
    input.value = prefillPin;
  }
  setTimeout(() => input.focus(), 50);
}

async function submitPin() {
  const input = document.getElementById('pin-input');
  const btn   = document.getElementById('pin-btn');
  const errEl = document.getElementById('pin-error');
  if (!input || !btn || !errEl) return;
  const pin = input.value.trim();
  if (!pin) return;

  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> 確認中...';
  errEl.textContent = '';

  try {
    const res = await fetch('/api/auth', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pin }),
    });
    const data = await res.json();
    if (data.ok) {
      csrfToken = data.csrfToken;
      showUpload();
    } else {
      errEl.textContent = getErrMsg(data.error, 'PINが正しくありません。');
      btn.disabled = false;
      btn.textContent = '入室する';
    }
  } catch {
    errEl.textContent = 'ネットワークエラー。再試行してください。';
    btn.disabled = false;
    btn.textContent = '入室する';
  }
}

// ── 画面: アップロード ─────────────────────────────────────────────────────────

function showUpload(errorMsg) {
  subtitle.textContent = '画像をアップロードして動画生成';
  render(`
    <div class="card">
      <div class="card-title">🖼️ 画像を選ぶ</div>
      <img id="preview" class="preview-img" alt="プレビュー">
      <div class="drop-zone" id="drop-zone">
        <input type="file" id="file-input" accept=".jpg,.jpeg,.png,.webp">
        <div class="drop-icon">📂</div>
        <div class="drop-text">ここに画像をドロップ、またはタップして選択</div>
        <div class="drop-hint">JPG / PNG / WEBP ・ 最大20MB</div>
      </div>
      ${errorMsg ? `<div class="error-msg">${escHtml(errorMsg)}</div>` : ''}
      <div class="select-row" id="workflow-select-wrap" style="display:none">
        <label for="workflow-select">ワークフローを選ぶ</label>
        <select id="workflow-select">
          <option value="">おまかせ（標準設定）</option>
        </select>
      </div>
      <button class="btn" id="generate-btn" disabled>動画にする ▶</button>
    </div>
    <div class="card" id="room-info-card">
      <div class="queue-info" id="queue-info"></div>
    </div>
    <div class="notice">
      アップロードされた画像は、この投入口を開いているPC上で動画生成に使用されます。
      個人情報・秘密情報・第三者の同意がない画像はアップロードしないでください。
    </div>
  `);
  document.getElementById('file-input').addEventListener('change', e => onFileSelected(e.target.files[0]));
  document.getElementById('generate-btn').addEventListener('click', submitJob);
  setupDropZone();
  loadRoomInfo();
  loadWorkflows();
}

function setupDropZone() {
  const zone = document.getElementById('drop-zone');
  if (!zone) return;
  zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('drag-over'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));
  zone.addEventListener('drop', e => {
    e.preventDefault();
    zone.classList.remove('drag-over');
    const file = e.dataTransfer.files[0];
    if (file) onFileSelected(file);
  });
}

function onFileSelected(file) {
  if (!file) return;
  selectedFile = file;
  const preview = document.getElementById('preview');
  if (preview) {
    preview.src = URL.createObjectURL(file);
    preview.style.display = 'block';
  }
  const btn = document.getElementById('generate-btn');
  if (btn) btn.disabled = false;
}

async function loadRoomInfo() {
  try {
    const res = await fetch('/api/room');
    const data = await res.json();
    const el = document.getElementById('queue-info');
    if (!el) return;
    const waiting = data.queueSize || 0;
    const max = data.maxQueueSize || 3;
    el.innerHTML = `<span>現在の待ち: <strong>${waiting}件</strong></span><span>上限: <strong>${max}件</strong></span>`;
  } catch { /* ignore */ }
}

async function loadWorkflows() {
  try {
    const res = await fetch('/api/workflows');
    const data = await res.json();
    workflows = Array.isArray(data.workflows) ? data.workflows : [];
  } catch {
    workflows = [];
  }
  renderWorkflowOptions();
}

function renderWorkflowOptions() {
  const wrap   = document.getElementById('workflow-select-wrap');
  const select = document.getElementById('workflow-select');
  if (!wrap || !select) return;
  if (!workflows.length) {
    wrap.style.display = 'none';
    return;
  }
  select.innerHTML = '<option value="">おまかせ（標準設定）</option>' +
    workflows.map(w => `<option value="${escHtml(w.key)}">${escHtml(w.label)}</option>`).join('');
  wrap.style.display = '';
}

// ── ジョブ送信 ────────────────────────────────────────────────────────────────

async function submitJob() {
  if (!selectedFile) return;
  const btn = document.getElementById('generate-btn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> 送信中...';

  const formData = new FormData();
  formData.append('image', selectedFile);
  const workflowSelect = document.getElementById('workflow-select');
  if (workflowSelect && workflowSelect.value) {
    formData.append('workflow', workflowSelect.value);
  }

  try {
    const res = await fetch('/api/jobs', {
      method: 'POST',
      headers: { 'X-MAF-CSRF': csrfToken },
      body: formData,
    });
    const data = await res.json();
    if (data.error) {
      showUpload(getErrMsg(data.error, data.message));
      return;
    }
    currentJobId = data.jobId;
    showProcessing(data.position);
    startPolling();
  } catch {
    showUpload('送信に失敗しました。もう一度お試しください。');
  }
}

// ── 画面: 生成中 ───────────────────────────────────────────────────────────────

function showProcessing(position) {
  subtitle.textContent = '加工中です...';
  render(`
    <div class="card">
      <div class="card-title">⚙️ 加工中です</div>
      <div class="status-row"><span class="status-key">状態</span><span id="proc-status" class="badge queued">待機中</span></div>
      <div class="status-row"><span class="status-key">待ち順</span><span id="proc-pos" class="status-val">${escHtml(position > 0 ? position + '番目' : '間もなく開始')}</span></div>
      <div class="status-row"><span class="status-key">進捗</span><span id="proc-label" class="status-val">—</span></div>
      <div class="progress-wrap"><div class="progress-bar" id="proc-bar" style="width:0%"></div></div>
      <p style="font-size:12px;color:var(--text2);margin-top:12px;line-height:1.7">
        このページを開いたままお待ちください。<br>
        PC性能と混雑状況により数分〜20分程度かかります。
      </p>
    </div>
  `);
}

function updateProcessing(data) {
  const statusEl = document.getElementById('proc-status');
  const posEl    = document.getElementById('proc-pos');
  const labelEl  = document.getElementById('proc-label');
  const barEl    = document.getElementById('proc-bar');
  if (!statusEl) return;

  const labels = { queued: '待機中', running: '生成中', completed: '完了', failed: '失敗', cancelled: 'キャンセル' };
  statusEl.textContent = labels[data.status] || data.status;
  statusEl.className = 'badge ' + (data.status || 'queued');

  if (posEl)   posEl.textContent   = data.position > 0 ? data.position + '番目' : '実行中';
  if (labelEl) labelEl.textContent = data.progressLabel || '—';
  if (barEl)   barEl.style.width   = (data.progressPct || 0) + '%';
}

// ── ポーリング ────────────────────────────────────────────────────────────────

function startPolling() {
  stopPolling();
  pollingTimer = setInterval(poll, 3000);
}

function stopPolling() {
  if (pollingTimer) { clearInterval(pollingTimer); pollingTimer = null; }
}

async function poll() {
  if (!currentJobId) return;
  try {
    const res = await fetch('/api/jobs/' + currentJobId);
    if (!res.ok) return;
    const data = await res.json();
    updateProcessing(data);
    if (data.status === 'completed' && data.videoUrl) {
      stopPolling();
      showComplete(data.videoUrl);
    } else if (['failed', 'cancelled', 'expired'].includes(data.status)) {
      stopPolling();
      showError(data.errorMessage || '生成に失敗しました。もう一度お試しください。');
    }
  } catch { /* network error, retry next tick */ }
}

// ── 画面: 完成 ────────────────────────────────────────────────────────────────

function showComplete(videoUrl) {
  subtitle.textContent = '完成しました！';
  render(`
    <div class="card">
      <div class="card-title">🎬 完成しました！</div>
      <video controls playsinline src="${escHtml(videoUrl)}"></video>
      <a class="btn" id="dl-btn" href="${escHtml(videoUrl)}" download="makeAiFactory_output.mp4">
        ⬇ 保存する
      </a>
      <button class="btn secondary" id="again-btn" style="margin-top:8px">
        もう一度生成する
      </button>
    </div>
  `);
  document.getElementById('again-btn').addEventListener('click', resetToUpload);
}

function resetToUpload() {
  selectedFile = null;
  currentJobId = null;
  stopPolling();
  showUpload();
}

// ── 画面: エラー ───────────────────────────────────────────────────────────────

function showError(message) {
  subtitle.textContent = 'エラーが発生しました';
  render(`
    <div class="card">
      <div class="card-title" style="color:var(--error)">⚠ エラー</div>
      <p style="font-size:14px;line-height:1.7;color:var(--text2)">${escHtml(message)}</p>
      <button class="btn secondary" id="back-btn" style="margin-top:16px">戻る</button>
    </div>
  `);
  document.getElementById('back-btn').addEventListener('click', resetToUpload);
}

// ── 初期化 ────────────────────────────────────────────────────────────────────

async function init() {
  // QR コードに ?pin=XXXXXX が埋め込まれている場合は自動入力
  const urlPin = new URLSearchParams(window.location.search).get('pin');

  try {
    const res = await fetch('/api/room');
    const data = await res.json();
    if (data.status === 'stopped' || data.status === 'error') {
      showError('投入口は終了しました。必要な場合は、投入口を開いた人にもう一度URLを発行してもらってください。');
      return;
    }
    // PIN 不要 or URL の PIN で認証を試みる
    const authRes = await fetch('/api/auth', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pin: urlPin || '' }),
    });
    const authData = await authRes.json();
    if (authData.ok) {
      csrfToken = authData.csrfToken;
      showUpload();
    } else {
      showPin(urlPin || null);
    }
  } catch {
    showPin(urlPin || null);
  }
}

init();
