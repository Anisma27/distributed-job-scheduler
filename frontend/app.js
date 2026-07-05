// ============================================================
// Scheduler Control — dashboard frontend
// Vanilla JS, no build step. Talks to the FastAPI backend via
// the REST API defined in backend/app/routers/*.
// ============================================================

const state = {
  apiBase: localStorage.getItem('apiBase') || 'http://localhost:8000',
  token: localStorage.getItem('token') || null,
  projects: [],
  currentProjectId: localStorage.getItem('currentProjectId') || null,
  queues: [],
  jobsPage: 1,
  jobsPageSize: 20,
  throughputHistory: [], // {t, completed, failed}
  chart: null,
  pollHandle: null,
};

// ---------------- API helper ----------------

async function api(path, { method = 'GET', body, auth = true } = {}) {
  const headers = { 'Content-Type': 'application/json' };
  if (auth && state.token) headers['Authorization'] = `Bearer ${state.token}`;

  const res = await fetch(`${state.apiBase}${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  if (res.status === 401) {
    logout();
    throw new Error('Session expired — please sign in again');
  }

  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const errBody = await res.json();
      detail = errBody.detail ? (typeof errBody.detail === 'string' ? errBody.detail : JSON.stringify(errBody.detail)) : detail;
    } catch (_) {}
    throw new Error(detail);
  }

  if (res.status === 204) return null;
  return res.json();
}

async function apiForm(path, formBody) {
  const res = await fetch(`${state.apiBase}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: formBody,
  });
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const errBody = await res.json();
      detail = errBody.detail || detail;
    } catch (_) {}
    throw new Error(detail);
  }
  return res.json();
}

// ---------------- Toast ----------------

function toast(message, isError = false) {
  const el = document.getElementById('toast');
  el.textContent = message;
  el.classList.toggle('error', isError);
  el.classList.remove('hidden');
  clearTimeout(el._timer);
  el._timer = setTimeout(() => el.classList.add('hidden'), 3500);
}

// ---------------- Auth screen ----------------

function initAuthScreen() {
  document.getElementById('api-base-login').value = state.apiBase;
  document.getElementById('api-base-register').value = state.apiBase;

  document.querySelectorAll('.auth-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.auth-tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      const target = tab.dataset.tab;
      document.getElementById('login-form').classList.toggle('hidden', target !== 'login');
      document.getElementById('register-form').classList.toggle('hidden', target !== 'register');
    });
  });

  document.getElementById('login-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const errEl = document.getElementById('login-error');
    errEl.textContent = '';
    state.apiBase = document.getElementById('api-base-login').value.trim().replace(/\/$/, '') || state.apiBase;
    localStorage.setItem('apiBase', state.apiBase);

    const email = document.getElementById('login-email').value;
    const password = document.getElementById('login-password').value;

    try {
      const data = await apiForm('/api/auth/login', `username=${encodeURIComponent(email)}&password=${encodeURIComponent(password)}`);
      state.token = data.access_token;
      localStorage.setItem('token', state.token);
      await bootApp();
    } catch (err) {
      errEl.textContent = err.message;
    }
  });

  document.getElementById('register-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const errEl = document.getElementById('register-error');
    errEl.textContent = '';
    state.apiBase = document.getElementById('api-base-register').value.trim().replace(/\/$/, '') || state.apiBase;
    localStorage.setItem('apiBase', state.apiBase);

    const payload = {
      full_name: document.getElementById('reg-name').value,
      organization_name: document.getElementById('reg-org').value,
      email: document.getElementById('reg-email').value,
      password: document.getElementById('reg-password').value,
    };

    try {
      await api('/api/auth/register', { method: 'POST', body: payload, auth: false });
      const data = await apiForm('/api/auth/login', `username=${encodeURIComponent(payload.email)}&password=${encodeURIComponent(payload.password)}`);
      state.token = data.access_token;
      localStorage.setItem('token', state.token);
      await bootApp();
    } catch (err) {
      errEl.textContent = err.message;
    }
  });
}

function logout() {
  state.token = null;
  localStorage.removeItem('token');
  if (state.pollHandle) clearInterval(state.pollHandle);
  document.getElementById('app').classList.add('hidden');
  document.getElementById('auth-screen').classList.remove('hidden');
}

// ---------------- Navigation ----------------

function initNav() {
  document.querySelectorAll('.nav-item').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.nav-item').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
      btn.classList.add('active');
      document.getElementById(`view-${btn.dataset.view}`).classList.add('active');

      if (btn.dataset.view === 'jobs') loadJobs();
      if (btn.dataset.view === 'queues') loadQueues();
      if (btn.dataset.view === 'workers') loadWorkers();
      if (btn.dataset.view === 'deadletter') loadDeadLetter();
      if (btn.dataset.view === 'overview') loadHealth();
    });
  });

  document.getElementById('logout-btn').addEventListener('click', logout);
  document.getElementById('new-project-btn').addEventListener('click', openNewProjectModal);
  document.getElementById('project-select').addEventListener('change', (e) => {
    state.currentProjectId = e.target.value;
    localStorage.setItem('currentProjectId', state.currentProjectId);
    loadQueues();
  });
  document.getElementById('new-queue-btn').addEventListener('click', openNewQueueModal);
  document.getElementById('new-job-btn').addEventListener('click', openNewJobModal);
  document.getElementById('jobs-queue-select').addEventListener('change', () => { state.jobsPage = 1; loadJobs(); });
  document.getElementById('jobs-status-filter').addEventListener('change', () => { state.jobsPage = 1; loadJobs(); });
  document.getElementById('dlq-queue-select').addEventListener('change', loadDeadLetter);
}

// ---------------- Boot ----------------

async function bootApp() {
  document.getElementById('auth-screen').classList.add('hidden');
  document.getElementById('app').classList.remove('hidden');

  await loadProjects();

  if (!state.currentProjectId && state.projects.length) {
    state.currentProjectId = state.projects[0].id;
    localStorage.setItem('currentProjectId', state.currentProjectId);
  }

  renderProjectSelect();
  await loadQueues();
  await loadHealth();
  initThroughputChart();

  if (state.pollHandle) clearInterval(state.pollHandle);
  state.pollHandle = setInterval(() => {
    const activeView = document.querySelector('.nav-item.active').dataset.view;
    if (activeView === 'overview') loadHealth();
    if (activeView === 'jobs') loadJobs(true);
    if (activeView === 'workers') loadWorkers();
  }, 5000);
}

async function loadProjects() {
  try {
    state.projects = await api('/api/projects');
    if (!state.projects.length) {
      toast('Create a project to get started');
    }
  } catch (err) {
    toast(err.message, true);
  }
}

function renderProjectSelect() {
  const sel = document.getElementById('project-select');
  sel.innerHTML = state.projects.map(p => `<option value="${p.id}">${escapeHtml(p.name)}</option>`).join('');
  if (state.currentProjectId) sel.value = state.currentProjectId;
}

// ---------------- Overview ----------------

async function loadHealth() {
  try {
    const health = await api('/api/dashboard/health');
    Object.entries(health).forEach(([key, val]) => {
      const cell = document.querySelector(`.health-cell[data-metric="${key}"] .health-num`);
      if (cell) cell.textContent = val;
    });

    state.throughputHistory.push({ t: new Date().toLocaleTimeString(), completed: health.jobs_completed_last_hour, failed: health.jobs_failed_last_hour });
    if (state.throughputHistory.length > 20) state.throughputHistory.shift();
    updateThroughputChart();
  } catch (err) {
    toast(err.message, true);
  }
}

function initThroughputChart() {
  const ctx = document.getElementById('throughput-chart');
  if (state.chart) state.chart.destroy();
  state.chart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: [],
      datasets: [
        { label: 'Completed (1h)', data: [], borderColor: '#3DD68C', backgroundColor: 'rgba(61,214,140,0.08)', tension: 0.3, fill: true },
        { label: 'Failed (1h)', data: [], borderColor: '#FF5C5C', backgroundColor: 'rgba(255,92,92,0.08)', tension: 0.3, fill: true },
      ],
    },
    options: {
      responsive: true,
      plugins: { legend: { labels: { color: '#8B98A9', font: { family: 'Inter', size: 11 } } } },
      scales: {
        x: { ticks: { color: '#8B98A9', font: { size: 10 } }, grid: { color: '#26303D' } },
        y: { ticks: { color: '#8B98A9', font: { size: 10 } }, grid: { color: '#26303D' }, beginAtZero: true },
      },
    },
  });
}

function updateThroughputChart() {
  if (!state.chart) return;
  state.chart.data.labels = state.throughputHistory.map(h => h.t);
  state.chart.data.datasets[0].data = state.throughputHistory.map(h => h.completed);
  state.chart.data.datasets[1].data = state.throughputHistory.map(h => h.failed);
  state.chart.update();
}

// ---------------- Queues ----------------

async function loadQueues() {
  if (!state.currentProjectId) return;
  try {
    state.queues = await api(`/api/projects/${state.currentProjectId}/queues`);
    renderQueues();
    renderQueueSelects();
  } catch (err) {
    toast(err.message, true);
  }
}

function renderQueues() {
  const wrap = document.getElementById('queues-list');
  if (!state.queues.length) {
    wrap.innerHTML = `<div class="empty-state">No queues yet. Create one to start accepting jobs.</div>`;
    return;
  }
  wrap.innerHTML = state.queues.map(q => `
    <div class="queue-card" data-queue-id="${q.id}">
      <div>
        <div class="queue-name">${escapeHtml(q.name)}${q.is_paused ? '<span class="badge-paused">PAUSED</span>' : ''}</div>
        <div class="queue-meta">priority ${q.priority} · concurrency ${q.concurrency_limit} · ${q.retry_policy ? q.retry_policy.strategy : 'no retry policy'}</div>
      </div>
      <div class="queue-stats" id="queue-stats-${q.id}"></div>
      <div class="queue-actions">
        <button class="btn-small" data-action="toggle-pause" data-queue-id="${q.id}" data-paused="${q.is_paused}">${q.is_paused ? 'Resume' : 'Pause'}</button>
        <button class="btn-small danger" data-action="delete-queue" data-queue-id="${q.id}">Delete</button>
      </div>
    </div>
  `).join('');

  state.queues.forEach(q => loadQueueStats(q.id));

  wrap.querySelectorAll('[data-action="toggle-pause"]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const paused = btn.dataset.paused === 'true';
      try {
        await api(`/api/projects/${state.currentProjectId}/queues/${btn.dataset.queueId}/${paused ? 'resume' : 'pause'}`, { method: 'POST' });
        toast(paused ? 'Queue resumed' : 'Queue paused');
        loadQueues();
      } catch (err) { toast(err.message, true); }
    });
  });

  wrap.querySelectorAll('[data-action="delete-queue"]').forEach(btn => {
    btn.addEventListener('click', async () => {
      if (!confirm('Delete this queue and all its jobs?')) return;
      try {
        await api(`/api/projects/${state.currentProjectId}/queues/${btn.dataset.queueId}`, { method: 'DELETE' });
        toast('Queue deleted');
        loadQueues();
      } catch (err) { toast(err.message, true); }
    });
  });
}

async function loadQueueStats(queueId) {
  try {
    const stats = await api(`/api/projects/${state.currentProjectId}/queues/${queueId}/stats`);
    const el = document.getElementById(`queue-stats-${queueId}`);
    if (!el) return;
    el.innerHTML = `
      <div class="stat-chip"><span class="n">${stats.queued}</span><span class="l">Queued</span></div>
      <div class="stat-chip"><span class="n">${stats.running}</span><span class="l">Running</span></div>
      <div class="stat-chip"><span class="n">${stats.completed}</span><span class="l">Done</span></div>
      <div class="stat-chip"><span class="n">${stats.failed}</span><span class="l">Failed</span></div>
      <div class="stat-chip"><span class="n">${stats.dead_letter}</span><span class="l">Dead</span></div>
    `;
  } catch (_) {}
}

function renderQueueSelects() {
  const options = state.queues.map(q => `<option value="${q.id}">${escapeHtml(q.name)}</option>`).join('');
  document.getElementById('jobs-queue-select').innerHTML = options;
  document.getElementById('dlq-queue-select').innerHTML = options;
}

// ---------------- Jobs ----------------

async function loadJobs(silent = false) {
  const queueId = document.getElementById('jobs-queue-select').value;
  if (!queueId) {
    document.getElementById('jobs-table-wrap').innerHTML = `<div class="empty-state">Create a queue first.</div>`;
    return;
  }
  const status = document.getElementById('jobs-status-filter').value;
  const params = new URLSearchParams({ page: state.jobsPage, page_size: state.jobsPageSize });
  if (status) params.set('status', status);

  try {
    const data = await api(`/api/queues/${queueId}/jobs?${params.toString()}`);
    renderJobsTable(data, queueId);
  } catch (err) {
    if (!silent) toast(err.message, true);
  }
}

function renderJobsTable(data, queueId) {
  const wrap = document.getElementById('jobs-table-wrap');
  if (!data.items.length) {
    wrap.innerHTML = `<div class="empty-state">No jobs match this filter.</div>`;
    document.getElementById('jobs-pagination').innerHTML = '';
    return;
  }

  wrap.innerHTML = `
    <table class="jobs-table">
      <thead><tr><th>Handler</th><th>Status</th><th>Attempt</th><th>Priority</th><th>Created</th><th>ID</th></tr></thead>
      <tbody>
        ${data.items.map(j => `
          <tr class="clickable" data-job-id="${j.id}" data-queue-id="${queueId}">
            <td>${escapeHtml(j.handler)}</td>
            <td><span class="status-pill status-${j.status}">${j.status.replace('_',' ')}</span></td>
            <td class="mono">${j.attempt_count}${j.max_retries != null ? '/' + j.max_retries : ''}</td>
            <td class="mono">${j.priority}</td>
            <td class="mono">${new Date(j.created_at).toLocaleString()}</td>
            <td class="mono">${j.id.slice(0, 8)}</td>
          </tr>
        `).join('')}
      </tbody>
    </table>
  `;

  wrap.querySelectorAll('tr.clickable').forEach(row => {
    row.addEventListener('click', () => openJobDetail(row.dataset.queueId, row.dataset.jobId));
  });

  const totalPages = Math.max(1, Math.ceil(data.total / data.page_size));
  const pagEl = document.getElementById('jobs-pagination');
  pagEl.innerHTML = `
    <button class="btn-small" id="jobs-prev" ${data.page <= 1 ? 'disabled' : ''}>Prev</button>
    <span class="mono" style="align-self:center;font-size:11px;color:var(--text-muted)">Page ${data.page} of ${totalPages}</span>
    <button class="btn-small" id="jobs-next" ${data.page >= totalPages ? 'disabled' : ''}>Next</button>
  `;
  document.getElementById('jobs-prev')?.addEventListener('click', () => { state.jobsPage = Math.max(1, state.jobsPage - 1); loadJobs(); });
  document.getElementById('jobs-next')?.addEventListener('click', () => { state.jobsPage += 1; loadJobs(); });
}

const LIFECYCLE_STAGES = ['queued', 'claimed', 'running', 'completed'];

function renderPipeline(job) {
  const isFailedPath = ['failed', 'dead_letter', 'cancelled'].includes(job.status);
  const currentIdx = LIFECYCLE_STAGES.indexOf(job.status);

  return `<div class="pipeline">
    ${LIFECYCLE_STAGES.map((stage, i) => {
      let cls = '';
      if (isFailedPath) {
        cls = i === 0 ? 'done' : (i === LIFECYCLE_STAGES.length - 1 ? 'failed' : 'done');
      } else if (currentIdx === -1) {
        cls = i === 0 ? 'current' : '';
      } else if (i < currentIdx) cls = 'done';
      else if (i === currentIdx) cls = job.status === 'completed' ? 'done' : 'current';

      return `<div class="pipeline-stage ${cls}">
        <div class="pipeline-track"></div>
        <div class="pipeline-node"></div>
        <div class="pipeline-label">${stage}</div>
      </div>`;
    }).join('')}
  </div>`;
}

async function openJobDetail(queueId, jobId) {
  try {
    const job = await api(`/api/queues/${queueId}/jobs/${jobId}`);
    const canRetry = ['failed', 'dead_letter'].includes(job.status);
    const canCancel = !['completed', 'cancelled'].includes(job.status);

    showModal(`
      <button class="modal-close" data-close>&times;</button>
      <h2>${escapeHtml(job.handler)} <span class="status-pill status-${job.status}">${job.status.replace('_',' ')}</span></h2>
      ${renderPipeline(job)}
      <p class="mono" style="font-size:11px;color:var(--text-muted);margin:18px 0 6px">Job ID: ${job.id}</p>
      <p class="mono" style="font-size:11px;color:var(--text-muted);margin:0 0 18px">Payload: ${escapeHtml(JSON.stringify(job.payload))}</p>

      <h2 style="font-size:13px;margin-top:22px">Execution history</h2>
      ${job.executions.length ? job.executions.map(e => `
        <div class="execution-row">
          #${e.attempt_number} · ${e.worker_id} · <span class="status-pill status-${e.status}">${e.status}</span>
          ${e.duration_ms != null ? `· ${e.duration_ms}ms` : ''}
          ${e.error_message ? `· <span style="color:var(--c-failed)">${escapeHtml(e.error_message)}</span>` : ''}
        </div>
      `).join('') : '<p style="font-size:12px;color:var(--text-muted)">No attempts yet.</p>'}

      <h2 style="font-size:13px;margin-top:22px">Logs</h2>
      ${job.logs.length ? job.logs.map(l => `
        <div class="log-line"><span class="log-level ${l.level}">${l.level}</span><span>${escapeHtml(l.message)}</span></div>
      `).join('') : '<p style="font-size:12px;color:var(--text-muted)">No logs yet.</p>'}

      <div class="modal-actions">
        ${canRetry ? `<button class="btn-small" id="job-retry-btn">Retry job</button>` : ''}
        ${canCancel ? `<button class="btn-small danger" id="job-cancel-btn">Cancel job</button>` : ''}
      </div>
    `);

    document.getElementById('job-retry-btn')?.addEventListener('click', async () => {
      try {
        await api(`/api/queues/${queueId}/jobs/${jobId}/retry`, { method: 'POST' });
        toast('Job requeued');
        closeModal();
        loadJobs();
      } catch (err) { toast(err.message, true); }
    });
    document.getElementById('job-cancel-btn')?.addEventListener('click', async () => {
      try {
        await api(`/api/queues/${queueId}/jobs/${jobId}/cancel`, { method: 'POST' });
        toast('Job cancelled');
        closeModal();
        loadJobs();
      } catch (err) { toast(err.message, true); }
    });
  } catch (err) {
    toast(err.message, true);
  }
}

function openNewJobModal() {
  if (!state.queues.length) { toast('Create a queue first', true); return; }
  const queueOptions = state.queues.map(q => `<option value="${q.id}">${escapeHtml(q.name)}</option>`).join('');

  showModal(`
    <button class="modal-close" data-close>&times;</button>
    <h2>New job</h2>
    <form id="new-job-form">
      <div class="field"><label>Queue</label><select id="job-queue" required>${queueOptions}</select></div>
      <div class="modal-row">
        <div class="field"><label>Handler</label><input id="job-handler" placeholder="noop / sleep / flaky / http_request" required /></div>
        <div class="field"><label>Job type</label>
          <select id="job-type">
            <option value="immediate">Immediate</option>
            <option value="delayed">Delayed</option>
            <option value="scheduled">Scheduled</option>
            <option value="recurring">Recurring (cron)</option>
            <option value="batch">Batch</option>
          </select>
        </div>
      </div>
      <div class="field" id="job-scheduled-field" style="display:none">
        <label>Scheduled at (ISO datetime)</label>
        <input id="job-scheduled-at" type="datetime-local" />
      </div>
      <div class="field" id="job-cron-field" style="display:none">
        <label>Cron expression</label>
        <input id="job-cron" placeholder="*/5 * * * *" />
      </div>
      <div class="field" id="job-batch-field" style="display:none">
        <label>Batch size (replicates the payload below N times)</label>
        <input id="job-batch-size" type="number" min="1" value="5" />
      </div>
      <div class="modal-row">
        <div class="field"><label>Priority</label><input id="job-priority" type="number" value="0" /></div>
        <div class="field"><label>Max retries (blank = queue default)</label><input id="job-max-retries" type="number" min="0" /></div>
      </div>
      <div class="field"><label>Payload (JSON)</label><textarea id="job-payload">{}</textarea></div>
      <div class="modal-actions">
        <button type="submit" class="btn-primary">Create job</button>
      </div>
    </form>
  `);

  const typeSelect = document.getElementById('job-type');
  typeSelect.addEventListener('change', () => {
    const t = typeSelect.value;
    document.getElementById('job-scheduled-field').style.display = (t === 'delayed' || t === 'scheduled') ? 'flex' : 'none';
    document.getElementById('job-cron-field').style.display = t === 'recurring' ? 'flex' : 'none';
    document.getElementById('job-batch-field').style.display = t === 'batch' ? 'flex' : 'none';
  });

  document.getElementById('new-job-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const queueId = document.getElementById('job-queue').value;
    const jobType = typeSelect.value;
    let payload;
    try {
      payload = JSON.parse(document.getElementById('job-payload').value || '{}');
    } catch (_) {
      toast('Payload must be valid JSON', true);
      return;
    }

    const body = {
      handler: document.getElementById('job-handler').value,
      job_type: jobType,
      payload,
      priority: parseInt(document.getElementById('job-priority').value || '0', 10),
    };
    const maxRetries = document.getElementById('job-max-retries').value;
    if (maxRetries !== '') body.max_retries = parseInt(maxRetries, 10);

    if (jobType === 'delayed' || jobType === 'scheduled') {
      const val = document.getElementById('job-scheduled-at').value;
      if (!val) { toast('Scheduled time is required', true); return; }
      body.scheduled_at = new Date(val).toISOString();
    }
    if (jobType === 'recurring') {
      body.cron_expression = document.getElementById('job-cron').value;
      if (!body.cron_expression) { toast('Cron expression is required', true); return; }
    }
    if (jobType === 'batch') {
      body.batch_size = parseInt(document.getElementById('job-batch-size').value || '1', 10);
    }

    try {
      await api(`/api/queues/${queueId}/jobs`, { method: 'POST', body });
      toast('Job created');
      closeModal();
      document.getElementById('jobs-queue-select').value = queueId;
      state.jobsPage = 1;
      loadJobs();
    } catch (err) {
      toast(err.message, true);
    }
  });
}

// ---------------- Workers ----------------

async function loadWorkers() {
  try {
    const workers = await api('/api/workers');
    const wrap = document.getElementById('workers-list');
    if (!workers.length) {
      wrap.innerHTML = `<div class="empty-state">No workers have sent a heartbeat yet. Start one with <span class="mono">python -m worker.worker_main</span>.</div>`;
      return;
    }
    wrap.innerHTML = workers.map(w => `
      <div class="worker-card">
        <span class="worker-dot ${w.status === 'online' ? 'online' : 'offline'}"></span>
        <strong>${escapeHtml(w.id)}</strong>
        <span style="color:var(--text-muted)">${w.status}</span>
        <span class="mono">concurrency ${w.concurrency} · last seen ${w.last_seen_at ? new Date(w.last_seen_at).toLocaleTimeString() : '—'}</span>
      </div>
    `).join('');
  } catch (err) {
    toast(err.message, true);
  }
}

// ---------------- Dead letter ----------------

async function loadDeadLetter() {
  const queueId = document.getElementById('dlq-queue-select').value;
  if (!queueId) {
    document.getElementById('dlq-list').innerHTML = `<div class="empty-state">Create a queue first.</div>`;
    return;
  }
  try {
    const jobs = await api(`/api/queues/${queueId}/jobs/dead-letter/list`);
    const wrap = document.getElementById('dlq-list');
    if (!jobs.length) {
      wrap.innerHTML = `<div class="empty-state">Nothing in the dead-letter queue. 🎉</div>`;
      return;
    }
    wrap.innerHTML = jobs.map(j => `
      <div class="queue-card">
        <div>
          <div class="queue-name">${escapeHtml(j.handler)}</div>
          <div class="queue-meta">${j.attempt_count} attempts · failed ${new Date(j.finished_at).toLocaleString()}</div>
        </div>
        <div class="queue-actions">
          <button class="btn-small" data-retry-id="${j.id}" data-queue-id="${queueId}">Requeue</button>
          <button class="btn-small" data-inspect-id="${j.id}" data-queue-id="${queueId}">Inspect</button>
        </div>
      </div>
    `).join('');

    wrap.querySelectorAll('[data-retry-id]').forEach(btn => {
      btn.addEventListener('click', async () => {
        try {
          await api(`/api/queues/${btn.dataset.queueId}/jobs/${btn.dataset.retryId}/retry`, { method: 'POST' });
          toast('Job requeued');
          loadDeadLetter();
        } catch (err) { toast(err.message, true); }
      });
    });
    wrap.querySelectorAll('[data-inspect-id]').forEach(btn => {
      btn.addEventListener('click', () => openJobDetail(btn.dataset.queueId, btn.dataset.inspectId));
    });
  } catch (err) {
    toast(err.message, true);
  }
}

// ---------------- Project / Queue creation modals ----------------

function openNewProjectModal() {
  showModal(`
    <button class="modal-close" data-close>&times;</button>
    <h2>New project</h2>
    <form id="new-project-form">
      <div class="field"><label>Name</label><input id="project-name" required /></div>
      <div class="field"><label>Description</label><textarea id="project-description"></textarea></div>
      <div class="modal-actions"><button type="submit" class="btn-primary">Create project</button></div>
    </form>
  `);
  document.getElementById('new-project-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
      const project = await api('/api/projects', {
        method: 'POST',
        body: { name: document.getElementById('project-name').value, description: document.getElementById('project-description').value },
      });
      toast('Project created');
      closeModal();
      await loadProjects();
      state.currentProjectId = project.id;
      localStorage.setItem('currentProjectId', project.id);
      renderProjectSelect();
      loadQueues();
    } catch (err) { toast(err.message, true); }
  });
}

function openNewQueueModal() {
  if (!state.currentProjectId) { toast('Create a project first', true); return; }
  showModal(`
    <button class="modal-close" data-close>&times;</button>
    <h2>New queue</h2>
    <form id="new-queue-form">
      <div class="field"><label>Name</label><input id="queue-name" required /></div>
      <div class="modal-row">
        <div class="field"><label>Priority</label><input id="queue-priority" type="number" value="0" /></div>
        <div class="field"><label>Concurrency limit</label><input id="queue-concurrency" type="number" value="5" /></div>
      </div>
      <div class="modal-row">
        <div class="field"><label>Retry strategy</label>
          <select id="queue-retry-strategy">
            <option value="exponential">Exponential</option>
            <option value="linear">Linear</option>
            <option value="fixed">Fixed</option>
          </select>
        </div>
        <div class="field"><label>Max retries</label><input id="queue-max-retries" type="number" value="3" /></div>
      </div>
      <div class="modal-row">
        <div class="field"><label>Base delay (s)</label><input id="queue-base-delay" type="number" value="5" /></div>
        <div class="field"><label>Max delay (s)</label><input id="queue-max-delay" type="number" value="3600" /></div>
      </div>
      <div class="modal-actions"><button type="submit" class="btn-primary">Create queue</button></div>
    </form>
  `);
  document.getElementById('new-queue-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
      await api(`/api/projects/${state.currentProjectId}/queues`, {
        method: 'POST',
        body: {
          name: document.getElementById('queue-name').value,
          priority: parseInt(document.getElementById('queue-priority').value || '0', 10),
          concurrency_limit: parseInt(document.getElementById('queue-concurrency').value || '5', 10),
          retry_policy: {
            strategy: document.getElementById('queue-retry-strategy').value,
            max_retries: parseInt(document.getElementById('queue-max-retries').value || '3', 10),
            base_delay_seconds: parseInt(document.getElementById('queue-base-delay').value || '5', 10),
            max_delay_seconds: parseInt(document.getElementById('queue-max-delay').value || '3600', 10),
          },
        },
      });
      toast('Queue created');
      closeModal();
      loadQueues();
    } catch (err) { toast(err.message, true); }
  });
}

// ---------------- Modal helpers ----------------

function showModal(html) {
  document.getElementById('modal-content').innerHTML = html;
  document.getElementById('modal-backdrop').classList.remove('hidden');
  document.querySelectorAll('[data-close]').forEach(el => el.addEventListener('click', closeModal));
}
function closeModal() {
  document.getElementById('modal-backdrop').classList.add('hidden');
  document.getElementById('modal-content').innerHTML = '';
}
document.getElementById('modal-backdrop').addEventListener('click', (e) => {
  if (e.target.id === 'modal-backdrop') closeModal();
});

// ---------------- Utils ----------------

function escapeHtml(str) {
  return String(str).replace(/[&<>"']/g, (m) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));
}

// ---------------- Init ----------------

initAuthScreen();
initNav();

if (state.token) {
  bootApp().catch(() => logout());
}
