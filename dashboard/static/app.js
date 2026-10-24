/**
 * CloudLab VPS Hypervisor - Web Application Logic
 * Zero external framework dependencies - Vanilla JavaScript
 */

let STATE = {
  token: localStorage.getItem('tf_vps_token') || '',
  user: null,
  activeTab: 'instances',
  instances: [],
  catalog: [],
  localImages: [],
  networkInfo: null,
  systemStats: null,
  activeJobId: null,
  jobPollTimer: null,
  deleteTargetVm: null,
  selectedPlan: 'Standard',
  selectedOs: 'ubuntu-jammy.img'
};

// ==================== AUTHENTICATION ====================

async function apiRequest(endpoint, options = {}) {
  const headers = {
    'Content-Type': 'application/json',
    ...(options.headers || {})
  };
  if (STATE.token) {
    headers['Authorization'] = `Bearer ${STATE.token}`;
  }

  try {
    const res = await fetch(endpoint, { ...options, headers });
    if (res.status === 401) {
      handleUnauthorized();
      return { success: false, error: 'Session expired' };
    }
    const data = await res.json();
    return data;
  } catch (err) {
    console.error(`API Error on ${endpoint}:`, err);
    return { success: false, error: err.message };
  }
}

function handleUnauthorized() {
  STATE.token = '';
  localStorage.removeItem('tf_vps_token');
  document.getElementById('login-view').style.display = 'flex';
  document.getElementById('app-view').style.display = 'none';
}

async function handleLogin(e) {
  e.preventDefault();
  const username = document.getElementById('login-username').value.trim();
  const password = document.getElementById('login-password').value;
  const alertEl = document.getElementById('login-alert');
  const btn = document.getElementById('btn-login-submit');

  alertEl.style.display = 'none';
  btn.disabled = true;
  btn.innerHTML = '<span>Signing in...</span>';

  const res = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password })
  });
  const data = await res.json();

  btn.disabled = false;
  btn.innerHTML = '<span>Sign In to Dashboard</span>';

  if (data.success && data.token) {
    STATE.token = data.token;
    STATE.user = data.user;
    localStorage.setItem('tf_vps_token', data.token);
    initApp();
  } else {
    alertEl.textContent = data.error || 'Authentication failed';
    alertEl.style.display = 'block';
  }
}

function fillDefaultCreds() {
  document.getElementById('login-username').value = 'admin';
  document.getElementById('login-password').value = 'admin123';
}

function togglePass(inputId) {
  const el = document.getElementById(inputId);
  el.type = el.type === 'password' ? 'text' : 'password';
}

async function handleLogout() {
  await apiRequest('/api/auth/logout', { method: 'POST' });
  handleUnauthorized();
  showToast('Logged out successfully', 'info');
}

async function handlePasswordChange(e) {
  e.preventDefault();
  const old_password = document.getElementById('old-pass').value;
  const new_password = document.getElementById('new-pass').value;
  const confirm_password = document.getElementById('confirm-pass').value;
  const alertEl = document.getElementById('password-alert');

  if (new_password !== confirm_password) {
    alertEl.className = 'auth-alert';
    alertEl.textContent = 'New passwords do not match';
    alertEl.style.display = 'block';
    return;
  }

  const res = await apiRequest('/api/auth/change-password', {
    method: 'POST',
    body: JSON.stringify({ old_password, new_password })
  });

  if (res.success) {
    alertEl.className = 'auth-alert success';
    alertEl.textContent = 'Password updated successfully!';
    alertEl.style.display = 'block';
    document.getElementById('old-pass').value = '';
    document.getElementById('new-pass').value = '';
    document.getElementById('confirm-pass').value = '';
    showToast('Administrator password updated', 'success');
  } else {
    alertEl.className = 'auth-alert';
    alertEl.textContent = res.error || 'Failed to update password';
    alertEl.style.display = 'block';
  }
}

function toggleUserDropdown() {
  const el = document.getElementById('user-dropdown');
  el.style.display = el.style.display === 'none' ? 'block' : 'none';
}

// Close dropdown on outside click
document.addEventListener('click', (e) => {
  const menu = document.getElementById('user-dropdown');
  const btn = document.querySelector('.user-btn');
  if (menu && btn && !btn.contains(e.target) && !menu.contains(e.target)) {
    menu.style.display = 'none';
  }
});

// ==================== TABS & NAVIGATION ====================

function showTab(tabId) {
  STATE.activeTab = tabId;
  document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.nav-link').forEach(el => el.classList.remove('active'));

  const tabEl = document.getElementById(`tab-${tabId}`);
  const navEl = document.getElementById(`nav-${tabId}`);
  if (tabEl) tabEl.classList.add('active');
  if (navEl) navEl.classList.add('active');

  if (tabId === 'deploy') {
    fetchNextIP();
    generateInstanceName();
    updateSummary();
  }
}

// ==================== INITIALIZATION & DATA FETCHING ====================

async function initApp() {
  document.getElementById('login-view').style.display = 'none';
  document.getElementById('app-view').style.display = 'block';

  // Check current session
  const meRes = await apiRequest('/api/auth/me');
  if (!meRes || !meRes.user) {
    handleUnauthorized();
    return;
  }

  document.getElementById('user-display-name').textContent = meRes.user.username;
  document.getElementById('dropdown-username').textContent = meRes.user.username;

  await refreshDashboard();

  // Background auto-refresh every 6 seconds
  setInterval(() => {
    if (STATE.token && document.visibilityState === 'visible') {
      refreshDashboard(true);
    }
  }, 6000);
}

async function refreshDashboard(silent = false) {
  const [statsData, instancesData, imagesData, networkData] = await Promise.all([
    apiRequest('/api/system/stats'),
    apiRequest('/api/instances'),
    apiRequest('/api/images/catalog'),
    apiRequest('/api/networks')
  ]);

  if (statsData) renderSystemStats(statsData);
  if (instancesData && instancesData.instances) renderInstances(instancesData.instances);
  if (imagesData && imagesData.catalog) renderImagesCatalog(imagesData.catalog, imagesData.tasks || {});
  if (networkData) renderNetwork(networkData);

  if (!silent) {
    showToast('Dashboard data refreshed', 'info');
  }
}

// ==================== RENDER: SYSTEM METRICS ====================

function renderSystemStats(data) {
  STATE.systemStats = data;
  if (!data.cpu) return;

  // Header quick pills
  document.getElementById('stat-cpu-val').textContent = `${data.cpu.usage_percent || 0}%`;
  
  if (data.memory) {
    const usedGb = (data.memory.used_mb / 1024).toFixed(1);
    const totalGb = (data.memory.total_mb / 1024).toFixed(1);
    document.getElementById('stat-ram-val').textContent = `${usedGb} / ${totalGb} GB`;
  }

  if (data.storage && data.storage.vms) {
    document.getElementById('stat-storage-val').textContent = `${data.storage.vms.used_gb || 0} / ${data.storage.vms.total_gb || 0} GB`;
    
    // Storage Pool Tab Meters
    const vmsPct = data.storage.vms.percent || 0;
    document.getElementById('pool-vms-percent').textContent = `${vmsPct}% Used`;
    document.getElementById('pool-vms-bar').style.width = `${vmsPct}%`;
    document.getElementById('pool-vms-used').textContent = `Used: ${data.storage.vms.used_gb} GB`;
    document.getElementById('pool-vms-free').textContent = `Available: ${data.storage.vms.free_gb} GB`;
  }

  if (data.storage && data.storage.isos) {
    const isosPct = data.storage.isos.percent || 0;
    document.getElementById('pool-isos-percent').textContent = `${isosPct}% Used`;
    document.getElementById('pool-isos-bar').style.width = `${isosPct}%`;
    document.getElementById('pool-isos-used').textContent = `Used: ${data.storage.isos.used_gb} GB`;
    document.getElementById('pool-isos-free').textContent = `Available: ${data.storage.isos.free_gb} GB`;
  }

  if (data.virtualization) {
    const kvm = data.virtualization;
    document.getElementById('node-hostname').textContent = kvm.hostname || 'homelab-node';
    document.getElementById('node-arch').textContent = `${kvm.arch || 'x86_64'} • ${kvm.os || 'Linux'}`;

    const kvmPill = document.getElementById('stat-kvm-val');
    if (kvm.nested_virtualization) {
      kvmPill.textContent = 'KVM Nested Active';
    } else if (kvm.kvm_available) {
      kvmPill.textContent = 'KVM Ready';
    } else {
      kvmPill.textContent = 'KVM Standby';
    }

    // Host Monitor tab
    document.getElementById('monitor-cpu-pct').textContent = `${data.cpu.usage_percent || 0}%`;
    document.getElementById('monitor-cpu-model').textContent = `${data.cpu.cores} Cores (${data.cpu.model})`;
    
    if (data.memory) {
      const uGb = (data.memory.used_mb / 1024).toFixed(1);
      const tGb = (data.memory.total_mb / 1024).toFixed(1);
      document.getElementById('monitor-ram-val').textContent = `${uGb} / ${tGb} GB`;
      document.getElementById('monitor-ram-pct').textContent = `${data.memory.percent}% Utilization`;
    }

    document.getElementById('monitor-nested-val').textContent = kvm.nested_virtualization ? 'Enabled (host-passthrough)' : 'Disabled';
    document.getElementById('monitor-libvirtd-val').textContent = kvm.libvirtd_active ? 'Active & Running' : 'Inactive';
    document.getElementById('monitor-os-val').textContent = `${kvm.os} (${kvm.arch})`;

    // Diagnostics table
    const diagBody = document.getElementById('host-diagnostics-body');
    diagBody.innerHTML = `
      <tr><td>Host Node Name</td><td><code>${kvm.hostname}</code></td></tr>
      <tr><td>CPU Architecture</td><td><strong>${kvm.arch}</strong></td></tr>
      <tr><td>KVM Hardware Acceleration</td><td><span class="text-success">${kvm.kvm_available ? '✓ /dev/kvm Available' : '✗ Emulated'}</span></td></tr>
      <tr><td>Nested Virtualization</td><td><span class="text-success">${kvm.nested_virtualization ? '✓ Active (nested=1)' : '○ Standalone'}</span></td></tr>
      <tr><td>Libvirt Daemon (libvirtd)</td><td><span class="${kvm.libvirtd_active ? 'text-success' : 'text-danger'}">${kvm.libvirtd_active ? '✓ Systemd Service Active' : '✗ Service Stopped'}</span></td></tr>
      <tr><td>Virtual Bridge (virbr10)</td><td><span class="${kvm.bridge_active ? 'text-success' : 'text-warning'}">${kvm.bridge_active ? '✓ Bridge virbr10 Up' : '○ Standby'}</span></td></tr>
      <tr><td>VM Storage Path</td><td><code>/data/vms</code></td></tr>
      <tr><td>ISOs / Cloud Image Path</td><td><code>/data/isos</code></td></tr>
    `;
  }
}

// ==================== RENDER: INSTANCES LIST ====================

function renderInstances(instances) {
  STATE.instances = instances;
  const tbody = document.getElementById('instances-table-body');
  document.getElementById('badge-instance-count').textContent = instances.length;
  document.getElementById('table-instance-badge').textContent = `${instances.length} Instances`;

  let runningCount = 0;
  let totalVcpus = 0;
  let totalMemoryMb = 0;

  instances.forEach(inst => {
    if (inst.state === 'running') runningCount++;
    totalVcpus += (inst.vcpus || 1);
    totalMemoryMb += (inst.memory_mb || 1024);
  });

  document.getElementById('stat-card-total-vms').textContent = instances.length;
  document.getElementById('stat-card-running-vms').textContent = runningCount;
  document.getElementById('stat-card-vcpus').textContent = `${totalVcpus} Cores`;
  document.getElementById('stat-card-memory').textContent = `${(totalMemoryMb / 1024).toFixed(1)} GB`;

  if (instances.length === 0) {
    tbody.innerHTML = `
      <tr>
        <td colspan="6" class="text-center py-6">
          <div style="padding: 24px;">
            <p class="text-muted" style="margin-bottom: 12px;">No virtual instances created yet.</p>
            <button class="btn btn-primary btn-sm" onclick="showTab('deploy')">
              + Deploy Your First VPS
            </button>
          </div>
        </td>
      </tr>
    `;
    return;
  }

  tbody.innerHTML = instances.map(inst => {
    const isRunning = inst.state === 'running';
    const statusClass = isRunning ? 'status-running' : 'status-stopped';
    const statusDot = isRunning ? '🟢' : '🔴';
    const ip = inst.ip || 'Pending DHCP';
    const ramGb = (inst.memory_mb / 1024).toFixed(1);
    const vncPort = inst.vnc_port || 5900;

    return `
      <tr>
        <td>
          <div style="display: flex; align-items: center; gap: 10px;">
            <div style="width: 32px; height: 32px; background: rgba(59, 130, 246, 0.15); border-radius: 6px; display: flex; align-items: center; justify-content: center; font-weight: 700; color: #60A5FA;">
              🐧
            </div>
            <div>
              <strong class="font-mono" style="font-size: 14px;">${inst.name}</strong>
              <div class="text-dim" style="font-size: 11px;">ID: ${inst.id} • ${inst.os || 'Ubuntu'}</div>
            </div>
          </div>
        </td>
        <td>
          <span class="status-tag ${statusClass}">
            <span>${statusDot}</span>
            <span>${inst.state.toUpperCase()}</span>
          </span>
        </td>
        <td>
          <div style="display: flex; align-items: center; gap: 8px;">
            <code class="font-mono">${ip}</code>
            ${inst.ip ? `<button class="btn btn-outline btn-sm" style="padding: 2px 6px;" onclick="copyToClipboard('${ip}')" title="Copy IP">📋</button>` : ''}
          </div>
        </td>
        <td>
          <span class="badge">${inst.vcpus || 1} vCPU</span>
          <span class="badge">${ramGb} GB RAM</span>
        </td>
        <td>
          <button class="btn btn-outline btn-sm" onclick="openConsoleModal('${inst.name}', '${ip}', ${vncPort})">
            🖥️ VNC (${vncPort})
          </button>
        </td>
        <td>
          <div class="action-btn-group">
            ${isRunning ? `
              <button class="btn btn-outline btn-sm" onclick="stopInstance('${inst.name}')" title="Graceful ACPI Shutdown">⏹ Stop</button>
              <button class="btn btn-outline btn-sm" onclick="rebootInstance('${inst.name}')" title="Reboot">🔄</button>
              <button class="btn btn-outline btn-sm text-warning" onclick="forceStopInstance('${inst.name}')" title="Force Power Off">⚡ Kill</button>
            ` : `
              <button class="btn btn-primary btn-sm" onclick="startInstance('${inst.name}')" title="Power On">▶ Start</button>
            `}
            <button class="btn btn-outline btn-sm" onclick="openSnapshotModal('${inst.name}')" title="Create Snapshot & Template Image">📸 Snapshot</button>
            <button class="btn btn-outline btn-sm" onclick="openSparsifyModal('${inst.name}')" title="Sparsify & Shrink Disk (virt-sparsify)">🗜️ Shrink</button>
            <button class="btn btn-outline btn-sm text-danger" onclick="confirmDeleteInstance('${inst.name}')" title="Destroy Instance">🗑</button>
          </div>
        </td>
      </tr>
    `;
  }).join('');
}

function filterInstances() {
  const query = document.getElementById('search-instances').value.toLowerCase();
  const rows = document.querySelectorAll('#instances-table-body tr');
  rows.forEach(r => {
    const text = r.textContent.toLowerCase();
    r.style.display = text.includes(query) ? '' : 'none';
  });
}

function copyToClipboard(text) {
  navigator.clipboard.writeText(text);
  showToast(`Copied to clipboard: ${text}`, 'info');
}

// ==================== INSTANCE ACTIONS ====================

async function startInstance(name) {
  showToast(`Starting instance ${name}...`, 'info');
  const res = await apiRequest(`/api/instances/${name}/start`, { method: 'POST' });
  if (res.success) {
    showToast(`Instance ${name} started!`, 'success');
    refreshDashboard(true);
  } else {
    showToast(`Error starting ${name}: ${res.error}`, 'error');
  }
}

async function stopInstance(name) {
  showToast(`Initiating ACPI shutdown for ${name}...`, 'info');
  const res = await apiRequest(`/api/instances/${name}/stop`, { method: 'POST' });
  if (res.success) {
    showToast(`Instance ${name} stopping...`, 'info');
    setTimeout(() => refreshDashboard(true), 3000);
  } else {
    showToast(`Error: ${res.error}`, 'error');
  }
}

async function forceStopInstance(name) {
  const res = await apiRequest(`/api/instances/${name}/force-stop`, { method: 'POST' });
  if (res.success) {
    showToast(`Instance ${name} killed`, 'warning');
    refreshDashboard(true);
  } else {
    showToast(`Error: ${res.error}`, 'error');
  }
}

async function rebootInstance(name) {
  const res = await apiRequest(`/api/instances/${name}/reboot`, { method: 'POST' });
  if (res.success) {
    showToast(`Rebooting ${name}...`, 'info');
    refreshDashboard(true);
  } else {
    showToast(`Error: ${res.error}`, 'error');
  }
}

function openSnapshotModal(vmName) {
  document.getElementById('snap-source-vm').value = vmName;
  const dateStr = new Date().toISOString().slice(0, 10).replace(/-/g, '');
  document.getElementById('snap-target-name').value = `${vmName}-snapshot-${dateStr}.img`;
  document.getElementById('snap-auto-restart').checked = true;
  document.getElementById('modal-snapshot').style.display = 'flex';
}

function closeSnapshotModal() {
  document.getElementById('modal-snapshot').style.display = 'none';
}

async function submitVmSnapshot() {
  const name = document.getElementById('snap-source-vm').value;
  const snapshot_name = document.getElementById('snap-target-name').value.trim();
  const auto_restart = document.getElementById('snap-auto-restart').checked;

  if (!snapshot_name) {
    showToast('Please specify a snapshot name', 'error');
    return;
  }

  closeSnapshotModal();
  openLogModal(`Snapshotting Instance: ${name} -> ${snapshot_name}`);

  const res = await apiRequest(`/api/instances/${name}/snapshot`, {
    method: 'POST',
    body: JSON.stringify({ snapshot_name, auto_restart })
  });

  if (res.success && res.job_id) {
    pollSnapshotJob(res.job_id);
  } else {
    showToast(`Snapshot error: ${res.error}`, 'error');
    const term = document.getElementById('terminal-output');
    term.textContent = `Error:\n${res.error}`;
  }
}

function pollSnapshotJob(jobId) {
  const term = document.getElementById('terminal-output');
  const statusText = document.getElementById('log-status-text');
  let startTime = Date.now();

  const timer = setInterval(async () => {
    const elapsed = Math.round((Date.now() - startTime) / 1000);
    document.getElementById('log-elapsed-time').textContent = `Elapsed: ${elapsed}s`;

    const job = await apiRequest(`/api/instances/snapshot-jobs/${jobId}`);
    if (!job || !job.id) return;

    statusText.textContent = `Status: ${job.status.toUpperCase()} (qemu-img convert)`;
    term.textContent = (job.logs || []).join('\n');
    term.scrollTop = term.scrollHeight;

    if (job.status === 'completed') {
      clearInterval(timer);
      showToast(`Snapshot ${job.vm_name} exported successfully to /data/isos!`, 'success');
      refreshDashboard(true);
    } else if (job.status === 'failed') {
      clearInterval(timer);
      showToast(`Snapshot failed: ${job.error}`, 'error');
    }
  }, 1000);
}

function openSparsifyModal(vmName) {
  document.getElementById('sparsify-vm-name').value = vmName;
  document.getElementById('sparsify-compress').checked = false;
  document.getElementById('sparsify-auto-restart').checked = true;
  document.getElementById('modal-sparsify').style.display = 'flex';
}

function closeSparsifyModal() {
  document.getElementById('modal-sparsify').style.display = 'none';
}

async function submitSparsify() {
  const name = document.getElementById('sparsify-vm-name').value;
  const compress = document.getElementById('sparsify-compress').checked;
  const auto_restart = document.getElementById('sparsify-auto-restart').checked;

  closeSparsifyModal();
  openLogModal(`Sparsifying & Compacting Disk: ${name}`);

  const res = await apiRequest(`/api/instances/${name}/sparsify`, {
    method: 'POST',
    body: JSON.stringify({ compress, auto_restart })
  });

  if (res.success && res.job_id) {
    pollSparsifyJob(res.job_id);
  } else {
    showToast(`Sparsify error: ${res.error}`, 'error');
    const term = document.getElementById('terminal-output');
    term.textContent = `Error:\n${res.error}`;
  }
}

function pollSparsifyJob(jobId) {
  const term = document.getElementById('terminal-output');
  const statusText = document.getElementById('log-status-text');
  let startTime = Date.now();

  const timer = setInterval(async () => {
    const elapsed = Math.round((Date.now() - startTime) / 1000);
    document.getElementById('log-elapsed-time').textContent = `Elapsed: ${elapsed}s`;

    const job = await apiRequest(`/api/instances/sparsify-jobs/${jobId}`);
    if (!job || !job.id) return;

    statusText.textContent = `Status: ${job.status.toUpperCase()} (virt-sparsify)`;
    term.textContent = (job.logs || []).join('\n');
    term.scrollTop = term.scrollHeight;

    if (job.status === 'completed') {
      clearInterval(timer);
      const reclaimed = job.reclaimed_mb ? `${job.reclaimed_mb} MB` : 'storage';
      showToast(`Disk for ${job.vm_name} shrunk successfully! Reclaimed: ${reclaimed}`, 'success');
      refreshDashboard(true);
    } else if (job.status === 'failed') {
      clearInterval(timer);
      showToast(`Sparsify failed: ${job.error}`, 'error');
    }
  }, 1000);
}

function confirmDeleteInstance(name) {
  STATE.deleteTargetVm = name;
  document.getElementById('delete-vm-name').textContent = name;
  document.getElementById('modal-delete').style.display = 'flex';
}

function closeDeleteModal() {
  STATE.deleteTargetVm = null;
  document.getElementById('modal-delete').style.display = 'none';
}

async function executeDeleteInstance() {
  const name = STATE.deleteTargetVm;
  if (!name) return;
  closeDeleteModal();

  showToast(`Destroying instance ${name}...`, 'warning');
  openLogModal(`Destroying Instance: ${name}`);

  const res = await apiRequest(`/api/instances/${name}`, { method: 'DELETE' });
  if (res.success && res.job_id) {
    pollJob(res.job_id);
  } else {
    showToast(`Delete failed: ${res.error}`, 'error');
    closeLogModal();
  }
}

// ==================== DEPLOY WIZARD LOGIC ====================

function renderImagesCatalog(catalog, tasks = {}) {
  STATE.catalog = catalog;
  const osSelector = document.getElementById('os-selector-list');
  const catalogTable = document.getElementById('catalog-table-body');
  const localTable = document.getElementById('local-isos-body');

  // 1. Render Wizard OS Selector Cards
  osSelector.innerHTML = catalog.map(img => {
    const isDownloaded = img.downloaded;
    const badgeClass = isDownloaded ? 'badge-ready' : 'badge-needs-dl';
    const badgeText = isDownloaded ? '✓ Ready in Pool' : '⬇ Needs Download';
    const isActive = STATE.selectedOs === img.filename ? 'active' : '';

    return `
      <div class="os-card ${isActive}" onclick="selectOs('${img.filename}', '${img.name}')">
        <span class="os-card-badge ${badgeClass}">${badgeText}</span>
        <div style="font-size: 24px;">${img.distro === 'debian' ? '🌀' : img.distro === 'snapshot' ? '📸' : '🐧'}</div>
        <span class="os-card-title">${img.name}</span>
        <span class="os-card-sub">${img.filename} • ~${img.size_estimate}</span>
      </div>
    `;
  }).join('');

  // 2. Render Storage Catalog Table
  catalogTable.innerHTML = catalog.map(img => {
    const isDownloaded = img.downloaded;
    const activeTask = img.download_task;

    let actionHtml = '';
    if (isDownloaded) {
      actionHtml = `<span class="badge" style="color: #34D399;">✓ Ready in /data/isos</span>`;
    } else if (activeTask && activeTask.status === 'downloading') {
      actionHtml = `
        <div style="width: 140px;">
          <div class="progress-bar-wrap" style="margin-bottom: 4px;">
            <div class="progress-bar-fill progress-bar-cyan" style="width: ${activeTask.progress}%;"></div>
          </div>
          <small class="text-cyan">${activeTask.progress}% Downloading...</small>
        </div>
      `;
    } else {
      actionHtml = `
        <button class="btn btn-outline btn-sm" onclick="downloadImage('${img.url}', '${img.filename}')">
          ⬇ Download Image
        </button>
      `;
    }

    return `
      <tr>
        <td><strong>${img.name}</strong></td>
        <td><code>${img.filename}</code></td>
        <td>${img.size_estimate}</td>
        <td>${isDownloaded ? '<span class="status-tag status-running">Ready</span>' : '<span class="status-tag status-creating">Available</span>'}</td>
        <td style="text-align: right;">${actionHtml}</td>
      </tr>
    `;
  }).join('');

  // 3. Render Local Downloaded ISOs
  const downloaded = catalog.filter(c => c.downloaded && c.local_info);
  if (downloaded.length === 0) {
    localTable.innerHTML = `<tr><td colspan="4" class="text-center py-6 text-muted">No images downloaded yet. Click "Download Image" above to fetch Ubuntu Jammy.</td></tr>`;
  } else {
    localTable.innerHTML = downloaded.map(d => {
      return `
        <tr>
          <td><strong class="font-mono">${d.filename}</strong></td>
          <td>${d.local_info.size_display}</td>
          <td>${new Date(d.local_info.modified * 1000).toLocaleString()}</td>
          <td style="text-align: right; display: flex; gap: 6px; justify-content: flex-end;">
            <button class="btn btn-outline btn-sm" onclick="openCustomizeModal('${d.filename}')">🛠️ Customize</button>
            <button class="btn btn-outline btn-sm text-danger" onclick="deleteLocalImage('${d.filename}')">🗑 Delete</button>
          </td>
        </tr>
      `;
    }).join('');
  }
}

function selectOs(filename, displayName) {
  STATE.selectedOs = filename;
  document.getElementById('sum-os').textContent = displayName || filename;
  document.querySelectorAll('.os-card').forEach(c => c.classList.remove('active'));
  event.currentTarget.classList.add('active');
  updateSummary();
}

function selectPlan(planName, vcpus, ramMb, diskGb) {
  STATE.selectedPlan = planName;
  document.getElementById('input-vcpus').value = vcpus;
  document.getElementById('input-ram').value = Math.round(ramMb / 1024);
  document.getElementById('input-disk').value = diskGb;

  document.querySelectorAll('.plan-card').forEach(c => c.classList.remove('active'));
  if (event && event.currentTarget) {
    event.currentTarget.classList.add('active');
  }

  updateSliders();
}

function updateSliders() {
  const vcpus = document.getElementById('input-vcpus').value;
  const ramGb = document.getElementById('input-ram').value;
  const diskGb = document.getElementById('input-disk').value;

  document.getElementById('slider-vcpu-val').textContent = vcpus;
  document.getElementById('slider-ram-val').textContent = ramGb;
  document.getElementById('slider-disk-val').textContent = diskGb;

  updateSummary();
}

function updateSummary() {
  const name = document.getElementById('deploy-name').value.trim() || 'dev-box-1';
  const vcpus = document.getElementById('input-vcpus').value;
  const ramGb = document.getElementById('input-ram').value;
  const diskGb = document.getElementById('input-disk').value;
  const ip = document.getElementById('deploy-ip').value || '10.10.10.10';
  const nested = document.getElementById('deploy-nested').checked;
  const dockerEl = document.getElementById('deploy-docker');
  const installDocker = dockerEl ? dockerEl.checked : false;

  document.getElementById('sum-name').textContent = name;
  document.getElementById('sum-plan').textContent = STATE.selectedPlan;
  document.getElementById('sum-compute').textContent = `${vcpus} vCPUs / ${ramGb} GB RAM`;
  document.getElementById('sum-storage').textContent = `${diskGb} GB SSD`;
  document.getElementById('sum-ip').textContent = ip;
  document.getElementById('sum-nested').textContent = nested ? 'Enabled' : 'Disabled';
  
  const sumDocker = document.getElementById('sum-docker');
  if (sumDocker) {
    sumDocker.textContent = installDocker ? 'Pre-installed' : 'Disabled';
    sumDocker.className = installDocker ? 'text-cyan' : 'text-muted';
  }
}

async function fetchNextIP() {
  const netSelect = document.getElementById('deploy-network-select');
  const netName = netSelect ? netSelect.value : '';
  const url = netName ? `/api/networks/${encodeURIComponent(netName)}` : '/api/networks';
  const res = await apiRequest(url);
  const nextIp = res ? (res.next_available_ip || (res.current && res.current.next_available_ip)) : null;
  if (nextIp) {
    document.getElementById('deploy-ip').value = nextIp;
    updateSummary();
    showToast(`Auto-assigned next IP: ${nextIp}`, 'info');
  }
}

async function onDeployNetworkChange() {
  const netSelect = document.getElementById('deploy-network-select');
  const netName = netSelect ? netSelect.value : '';
  if (!netName) return;
  const res = await apiRequest(`/api/networks/${encodeURIComponent(netName)}`);
  if (res && res.next_available_ip) {
    document.getElementById('deploy-ip').value = res.next_available_ip;
    document.getElementById('deploy-ip').placeholder = res.next_available_ip;
  }
  updateSummary();
}

function generateRandomPass() {
  const chars = 'abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789!@#$%';
  let pass = '';
  for (let i = 0; i < 12; i++) {
    pass += chars.charAt(Math.floor(Math.random() * chars.length));
  }
  document.getElementById('deploy-password').value = pass;
  document.getElementById('deploy-password').type = 'text';
  showToast('Generated strong password for VM', 'info');
}

function generateInstanceName() {
  const adjectives = ['prod', 'dev', 'lab', 'node', 'app', 'web', 'worker', 'db'];
  const nouns = ['alpha', 'beta', 'cluster', 'box', 'vps', 'core', 'host'];
  const num = Math.floor(Math.random() * 90 + 10);
  const adj = adjectives[Math.floor(Math.random() * adjectives.length)];
  const noun = nouns[Math.floor(Math.random() * nouns.length)];
  const name = `${adj}-${noun}-${num}`;
  
  document.getElementById('deploy-name').value = name;
  updateSummary();
}

async function submitDeployment() {
  const name = document.getElementById('deploy-name').value.trim().toLowerCase();
  if (!name) {
    showToast('Please provide an instance name', 'error');
    return;
  }

  const vcpus = parseInt(document.getElementById('input-vcpus').value);
  const ramGb = parseInt(document.getElementById('input-ram').value);
  const diskGb = parseInt(document.getElementById('input-disk').value);
  const netSelect = document.getElementById('deploy-network-select');
  const network = netSelect ? netSelect.value : 'net-10.10.10';
  const ip = document.getElementById('deploy-ip').value.trim();
  const ssh_key = document.getElementById('deploy-ssh-key').value.trim();
  const password = document.getElementById('deploy-password').value;
  const nested = document.getElementById('deploy-nested').checked;
  const dockerEl = document.getElementById('deploy-docker');
  const install_docker = dockerEl ? dockerEl.checked : false;

  const payload = {
    name,
    os: STATE.selectedOs,
    plan: STATE.selectedPlan,
    vcpus,
    memory_mb: ramGb * 1024,
    disk_gb: diskGb,
    network,
    ip,
    ssh_key,
    password,
    nested,
    install_docker,
    console: 'vnc'
  };

  openLogModal(`Deploying Instance: ${name}`);
  const res = await apiRequest('/api/instances/deploy', {
    method: 'POST',
    body: JSON.stringify(payload)
  });

  if (res.success && res.job_id) {
    pollJob(res.job_id);
  } else {
    showToast(`Deployment error: ${res.error}`, 'error');
    const term = document.getElementById('terminal-output');
    term.textContent = `Deployment Error:\n${res.error}`;
  }
}

// ==================== REAL-TIME JOB STREAMING MODAL ====================

function openLogModal(title) {
  document.getElementById('modal-log-title').textContent = title;
  document.getElementById('terminal-output').textContent = 'Connecting to job runner...\n';
  document.getElementById('log-status-text').textContent = 'Status: Initializing...';
  document.getElementById('modal-deploy-logs').style.display = 'flex';
}

function closeLogModal() {
  if (STATE.jobPollTimer) {
    clearInterval(STATE.jobPollTimer);
    STATE.jobPollTimer = null;
  }
  document.getElementById('modal-deploy-logs').style.display = 'none';
  refreshDashboard(true);
}

function pollJob(jobId) {
  STATE.activeJobId = jobId;
  const term = document.getElementById('terminal-output');
  const statusText = document.getElementById('log-status-text');
  let startTime = Date.now();

  STATE.jobPollTimer = setInterval(async () => {
    const elapsed = Math.round((Date.now() - startTime) / 1000);
    document.getElementById('log-elapsed-time').textContent = `Elapsed: ${elapsed}s`;

    const job = await apiRequest(`/api/instances/jobs/${jobId}`);
    if (!job || !job.id) return;

    statusText.textContent = `Status: ${job.status.toUpperCase()} (${job.action})`;
    term.textContent = (job.logs || []).join('\n');
    term.scrollTop = term.scrollHeight;

    if (job.status === 'completed') {
      clearInterval(STATE.jobPollTimer);
      STATE.jobPollTimer = null;
      showToast(`Instance ${job.vm_name} ${job.action} completed successfully!`, 'success');
      refreshDashboard(true);
      showTab('instances');
    } else if (job.status === 'failed') {
      clearInterval(STATE.jobPollTimer);
      STATE.jobPollTimer = null;
      showToast(`Job failed for ${job.vm_name}: ${job.error}`, 'error');
    }
  }, 1000);
}

// ==================== RENDER: STORAGE & NETWORK ====================

async function downloadImage(url, filename) {
  showToast(`Starting download of ${filename}...`, 'info');
  const res = await apiRequest('/api/images/download', {
    method: 'POST',
    body: JSON.stringify({ url, filename })
  });
  if (res.success) {
    showToast(`Background download started for ${filename}`, 'success');
    refreshDashboard(true);
  } else {
    showToast(`Error: ${res.error}`, 'error');
  }
}

async function handleCustomDownload(e) {
  e.preventDefault();
  const url = document.getElementById('custom-dl-url').value.trim();
  const filename = document.getElementById('custom-dl-filename').value.trim();
  if (!url || !filename) return;

  await downloadImage(url, filename);
  document.getElementById('custom-dl-url').value = '';
  document.getElementById('custom-dl-filename').value = '';
}

async function deleteLocalImage(filename) {
  if (!confirm(`Are you sure you want to remove ${filename} from /data/isos?`)) return;
  const res = await apiRequest(`/api/images/${filename}`, { method: 'DELETE' });
  if (res.success) {
    showToast(`Image ${filename} removed`, 'info');
    refreshDashboard(true);
  } else {
    showToast(`Error: ${res.error}`, 'error');
  }
}

// ==================== IMAGE CUSTOMIZATION / BAKE MODAL ====================

function openCustomizeModal(filename) {
  document.getElementById('custom-source-img').value = filename;
  const nameBase = filename.replace(/\.(img|qcow2)$/, '');
  document.getElementById('custom-target-img').value = `${nameBase}-docker.img`;
  document.getElementById('custom-opt-docker').checked = true;
  document.getElementById('custom-opt-upgrade').checked = false;
  document.getElementById('custom-opt-root-pass').value = '';
  document.getElementById('custom-opt-script').value = '';
  document.getElementById('modal-customize').style.display = 'flex';
}

function closeCustomizeModal() {
  document.getElementById('modal-customize').style.display = 'none';
}

async function submitImageCustomization() {
  const source_filename = document.getElementById('custom-source-img').value;
  const target_filename = document.getElementById('custom-target-img').value.trim();
  const install_docker = document.getElementById('custom-opt-docker').checked;
  const run_upgrade = document.getElementById('custom-opt-upgrade').checked;
  const root_password = document.getElementById('custom-opt-root-pass').value.trim();
  const custom_script = document.getElementById('custom-opt-script').value.trim();

  if (!target_filename) {
    showToast('Please provide a target filename', 'error');
    return;
  }

  closeCustomizeModal();
  openLogModal(`Baking Image: ${target_filename}`);

  const payload = {
    source_filename,
    target_filename,
    install_docker,
    run_upgrade,
    root_password,
    custom_script
  };

  const res = await apiRequest('/api/images/customize', {
    method: 'POST',
    body: JSON.stringify(payload)
  });

  if (res.success && res.job_id) {
    pollCustomizeJob(res.job_id);
  } else {
    showToast(`Failed: ${res.error}`, 'error');
    const term = document.getElementById('terminal-output');
    term.textContent = `Error: ${res.error}`;
  }
}

function pollCustomizeJob(jobId) {
  const term = document.getElementById('terminal-output');
  const statusText = document.getElementById('log-status-text');
  let startTime = Date.now();

  const timer = setInterval(async () => {
    const elapsed = Math.round((Date.now() - startTime) / 1000);
    document.getElementById('log-elapsed-time').textContent = `Elapsed: ${elapsed}s`;

    const job = await apiRequest(`/api/images/jobs/${jobId}`);
    if (!job || !job.id) return;

    statusText.textContent = `Status: ${job.status.toUpperCase()} (virt-customize)`;
    term.textContent = (job.logs || []).join('\n');
    term.scrollTop = term.scrollHeight;

    if (job.status === 'completed') {
      clearInterval(timer);
      showToast(`Image ${job.vm_name} customized successfully!`, 'success');
      refreshDashboard(true);
    } else if (job.status === 'failed') {
      clearInterval(timer);
      showToast(`Customization failed: ${job.error}`, 'error');
    }
  }, 1000);
}

// ==================== NETWORKS & IPAM ====================

async function switchNetwork(netName) {
  STATE.selectedNetworkName = netName;
  const res = await apiRequest(`/api/networks/${encodeURIComponent(netName)}`);
  if (res) {
    renderNetworkDetail(res);
    if (STATE.networksList) {
      renderNetworkPills(STATE.networksList);
    }
  }
}

function renderNetworkPills(networks) {
  const pillsBar = document.getElementById('network-pills-bar');
  if (!pillsBar) return;
  pillsBar.innerHTML = networks.map(n => `
    <button class="network-pill-btn ${n.name === STATE.selectedNetworkName ? 'active' : ''}" onclick="switchNetwork('${n.name}')">
      <span class="status-dot ${n.active ? 'dot-active' : 'dot-inactive'}"></span>
      <span>${n.name}</span>
      <small>(${n.subnet || n.bridge})</small>
    </button>
  `).join('');
}

function renderNetwork(data) {
  let current = null;
  let networks = [];

  if (data.networks && Array.isArray(data.networks)) {
    networks = data.networks;
    STATE.networksList = networks;
    current = data.current || (networks.length > 0 ? networks[0] : null);
  } else {
    current = data;
  }

  if (current && !STATE.selectedNetworkName) {
    STATE.selectedNetworkName = current.network_name || current.name;
  }

  if (networks.length > 0) {
    renderNetworkPills(networks);
    
    // Also populate deploy select
    const deployNetSelect = document.getElementById('deploy-network-select');
    if (deployNetSelect) {
      const currentVal = deployNetSelect.value || (current ? (current.network_name || current.name) : 'net-10.10.10');
      deployNetSelect.innerHTML = networks.map(n => `
        <option value="${n.name}" ${n.name === currentVal ? 'selected' : ''}>
          ${n.name} (${n.bridge} - ${n.subnet})
        </option>
      `).join('');
    }
  }

  if (current) {
    renderNetworkDetail(current);
  }
}

function renderNetworkDetail(data) {
  const netName = data.network_name || data.name || 'net-10.10.10';
  STATE.networkInfo = data;
  STATE.selectedNetworkName = netName;

  document.getElementById('net-title').textContent = netName;
  const statusBadge = document.getElementById('net-status-badge');
  if (statusBadge) {
    const isActive = data.active;
    statusBadge.textContent = isActive ? '● Active' : '○ Inactive';
    statusBadge.className = isActive ? 'text-success' : 'text-danger';
  }

  document.getElementById('net-bridge').textContent = data.bridge || 'virbr10';
  const modeLabel = document.getElementById('net-mode-label');
  if (modeLabel) {
    modeLabel.textContent = `Forward: ${data.forward_mode || 'route'}`;
  }

  document.getElementById('net-gateway').textContent = data.gateway || '10.10.10.1';
  const maskLabel = document.getElementById('net-mask-label');
  if (maskLabel) {
    maskLabel.textContent = `Netmask: ${data.netmask || '255.255.255.0'}`;
  }

  document.getElementById('net-allocated-count').textContent = `${data.total_allocated || 0} / ${data.total_ips || 253}`;
  document.getElementById('net-next-ip').textContent = `Next Available: ${data.next_available_ip || '--'}`;

  // Action button: delete network if not default
  const actionsWrap = document.getElementById('net-actions-wrap');
  if (actionsWrap) {
    if (netName !== 'net-10.10.10') {
      actionsWrap.innerHTML = `
        <button class="btn btn-outline btn-sm text-danger" onclick="deleteNetwork('${netName}')">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
          Delete Network
        </button>
      `;
    } else {
      actionsWrap.innerHTML = `<span class="badge">Default Network</span>`;
    }
  }

  // IPAM Table
  const tbody = document.getElementById('ipam-table-body');
  const allocated = data.allocated_ips || {};
  const ips = Object.keys(allocated).sort((a, b) => {
    const numA = parseInt(a.split('.')[3]) || 0;
    const numB = parseInt(b.split('.')[3]) || 0;
    return numA - numB;
  });

  if (ips.length === 0) {
    tbody.innerHTML = `<tr><td colspan="5" class="text-center py-6 text-muted">No allocated IP addresses in this subnet.</td></tr>`;
    return;
  }

  tbody.innerHTML = ips.map(ip => {
    const info = allocated[ip];
    return `
      <tr>
        <td><strong class="font-mono text-cyan">${ip}</strong></td>
        <td><strong>${info.vm}</strong></td>
        <td><span class="badge">${info.status}</span></td>
        <td>${info.vcpus && info.vcpus !== '-' ? `${info.vcpus} vCPU` : '-'}</td>
        <td>${info.memory_mb && info.memory_mb !== '-' ? `${(info.memory_mb/1024).toFixed(1)} GB` : '-'}</td>
      </tr>
    `;
  }).join('');
}

function openAddNetworkModal() {
  updateNetworkXmlPreview();
  document.getElementById('modal-add-network').style.display = 'flex';
}

function closeAddNetworkModal() {
  document.getElementById('modal-add-network').style.display = 'none';
}

function updateNetworkXmlPreview() {
  const name = document.getElementById('net-input-name').value.trim() || 'net-10.20.10';
  const bridge = document.getElementById('net-input-bridge').value.trim() || 'virbr20';
  const gateway = document.getElementById('net-input-gateway').value.trim() || '10.20.10.1';
  const netmask = document.getElementById('net-input-netmask').value.trim() || '255.255.255.0';
  const mode = document.getElementById('net-input-mode').value;

  const fwd = mode !== 'isolated' ? `  <forward mode='${mode}'/>\n` : '';
  const xml = `<network>
  <name>${name}</name>
${fwd}  <bridge name='${bridge}' stp='on' delay='0'/>
  <domain name='network'/>
  <ip address='${gateway}' netmask='${netmask}'>
  </ip>
</network>`;

  const preview = document.getElementById('net-xml-preview');
  if (preview) {
    preview.textContent = xml;
  }
}

async function submitCreateNetwork() {
  const name = document.getElementById('net-input-name').value.trim();
  const bridge = document.getElementById('net-input-bridge').value.trim();
  const gateway = document.getElementById('net-input-gateway').value.trim();
  const netmask = document.getElementById('net-input-netmask').value.trim();
  const forward_mode = document.getElementById('net-input-mode').value;
  const enable_nat = document.getElementById('net-input-nat').checked;

  if (!name || !bridge || !gateway) {
    showToast('Network Name, Bridge, and Gateway IP are required.', 'error');
    return;
  }

  showToast('Creating virtual network in Libvirt...', 'info');
  const res = await apiRequest('/api/networks/create', {
    method: 'POST',
    body: JSON.stringify({
      name,
      bridge,
      gateway,
      netmask,
      forward_mode,
      enable_nat
    })
  });

  if (res.success) {
    showToast(res.message || 'Virtual network created successfully!', 'success');
    closeAddNetworkModal();
    STATE.selectedNetworkName = name;
    refreshDashboard(true);
  } else {
    showToast(`Error: ${res.error}`, 'error');
  }
}

async function deleteNetwork(name) {
  if (!confirm(`Are you sure you want to delete virtual network '${name}'?\n\nThis will remove the virsh definition and deactivate the bridge.`)) {
    return;
  }

  showToast(`Deleting network ${name}...`, 'info');
  const res = await apiRequest(`/api/networks/${encodeURIComponent(name)}`, {
    method: 'DELETE'
  });

  if (res.success) {
    showToast(`Network '${name}' deleted successfully.`, 'success');
    STATE.selectedNetworkName = 'net-10.10.10';
    refreshDashboard(true);
  } else {
    showToast(`Failed to delete network: ${res.error}`, 'error');
  }
}

// ==================== CONSOLE MODAL ====================

function openConsoleModal(name, ip, port) {
  document.getElementById('modal-console-title').textContent = `Console: ${name}`;
  document.getElementById('console-ssh-cmd').textContent = `ssh student@${ip}`;
  document.getElementById('console-host').textContent = window.location.hostname || 'YOUR_HOST_IP';
  document.getElementById('console-port').textContent = port;
  document.getElementById('console-vnc-cmd').textContent = `vncviewer ${window.location.hostname || 'YOUR_HOST_IP'}:${port}`;
  document.getElementById('modal-console').style.display = 'flex';
}

function closeConsoleModal() {
  document.getElementById('modal-console').style.display = 'none';
}

// ==================== TOAST NOTIFICATIONS ====================

function showToast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  
  const icon = type === 'success' ? '✓' : type === 'error' ? '✕' : type === 'warning' ? '⚠' : 'ℹ';
  toast.innerHTML = `<strong>${icon}</strong> <span>${message}</span>`;
  container.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = '0';
    setTimeout(() => toast.remove(), 300);
  }, 4000);
}

// ==================== BOOTSTRAP ====================

window.addEventListener('DOMContentLoaded', () => {
  if (STATE.token) {
    initApp();
  } else {
    document.getElementById('login-view').style.display = 'flex';
    document.getElementById('app-view').style.display = 'none';
  }
});
