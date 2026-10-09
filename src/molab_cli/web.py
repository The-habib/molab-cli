"""
Local Web Control Center & Modern Dashboard for MoLab CLI.
Serves a responsive, OLED dark-mode single-page application on localhost.
"""

import json
import os
import sys
import webbrowser
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

from molab_cli.auth import inspect_auth_status
from molab_cli.backend import MarimoBackendClient
from molab_cli.client import MoLabClient
from molab_cli.gallery import GalleryManager
from molab_cli.jobs import JobManager
from molab_cli.keepalive import KeepaliveManager
from molab_cli.sandbox import SandboxSession
from molab_cli.vault import MoLabVault

app = FastAPI(title="MoLab Cloud GPU Dashboard", version="2.3.1")


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en" class="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>MoLab Cloud GPU Control Center</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://unpkg.com/lucide@latest"></script>
  <script>
    tailwind.config = {
      darkMode: 'class',
      theme: {
        extend: {
          colors: {
            obsidian: '#0B0F19',
            cardbg: '#111827',
            cardborder: '#1F2937',
            blackwell: '#76B900',
            cyanprimary: '#06B6D4',
            emeraldaccent: '#10B981',
          }
        }
      }
    }
  </script>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Inter:wght@400;500;600;700&display=swap');
    body { font-family: 'Inter', sans-serif; background-color: #0B0F19; color: #F3F4F6; }
    .font-mono { font-family: 'JetBrains Mono', monospace; }
  </style>
</head>
<body class="min-h-screen flex flex-col bg-obsidian text-gray-100">

  <!-- Top Navbar -->
  <header class="border-b border-cardborder bg-cardbg/80 backdrop-blur sticky top-0 z-50 px-4 py-3">
    <div class="max-w-7xl mx-auto flex items-center justify-between">
      <div class="flex items-center space-x-3">
        <div class="w-9 h-9 rounded-lg bg-cyanprimary/20 border border-cyanprimary/40 flex items-center justify-center text-cyanprimary">
          <i data-lucide="cpu" class="w-5 h-5"></i>
        </div>
        <div>
          <h1 class="font-bold text-lg leading-tight flex items-center space-x-2">
            <span>MoLab Control Center</span>
            <span class="text-xs px-2 py-0.5 rounded bg-blackwell/20 text-blackwell border border-blackwell/30 font-mono">v2.3.1</span>
          </h1>
          <p class="text-xs text-gray-400">NVIDIA RTX PRO 6000 Blackwell Server Fabric (96GB VRAM)</p>
        </div>
      </div>

      <div class="flex items-center space-x-3">
        <div id="auth-badge" class="hidden sm:flex items-center space-x-2 px-3 py-1 rounded-full bg-gray-800/80 border border-gray-700 text-xs">
          <span class="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
          <span id="user-email" class="text-gray-300 font-medium">Checking auth...</span>
        </div>
        <button onclick="refreshAll()" class="p-2 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-300 transition flex items-center space-x-1 text-xs border border-gray-700">
          <i data-lucide="refresh-cw" class="w-4 h-4" id="refresh-icon"></i>
          <span class="hidden sm:inline">Refresh</span>
        </button>
      </div>
    </div>
  </header>

  <!-- Main Container -->
  <main class="flex-1 max-w-7xl w-full mx-auto px-4 py-6 space-y-8">

    <!-- Overview Metrics Cards -->
    <section class="grid grid-cols-1 md:grid-cols-4 gap-4">
      <div class="bg-cardbg border border-cardborder rounded-xl p-4 flex items-center space-x-4">
        <div class="p-3 bg-blackwell/10 text-blackwell rounded-lg border border-blackwell/20">
          <i data-lucide="zap" class="w-6 h-6"></i>
        </div>
        <div>
          <p class="text-xs text-gray-400 font-medium">Active Pods</p>
          <h3 id="stat-active-pods" class="text-2xl font-bold font-mono">--</h3>
          <p id="stat-free-hint" class="text-xs text-emerald-400">Auditing...</p>
        </div>
      </div>

      <div class="bg-cardbg border border-cardborder rounded-xl p-4 flex items-center space-x-4">
        <div class="p-3 bg-cyanprimary/10 text-cyanprimary rounded-lg border border-cyanprimary/20">
          <i data-lucide="hard-drive" class="w-6 h-6"></i>
        </div>
        <div>
          <p class="text-xs text-gray-400 font-medium">Blackwell VRAM</p>
          <h3 class="text-2xl font-bold font-mono">94.97 <span class="text-sm font-normal text-gray-400">GB</span></h3>
          <p class="text-xs text-gray-400">GDDR7 (sm_120)</p>
        </div>
      </div>

      <div class="bg-cardbg border border-cardborder rounded-xl p-4 flex items-center space-x-4">
        <div class="p-3 bg-purple-500/10 text-purple-400 rounded-lg border border-purple-500/20">
          <i data-lucide="shield-check" class="w-6 h-6"></i>
        </div>
        <div>
          <p class="text-xs text-gray-400 font-medium">On-MoLab Permanence</p>
          <h3 class="text-2xl font-bold font-mono">0 <span class="text-sm font-normal text-gray-400">Local Bytes</span></h3>
          <p class="text-xs text-emerald-400">Cloud Vault Armed</p>
        </div>
      </div>

      <div class="bg-cardbg border border-cardborder rounded-xl p-4 flex items-center space-x-4">
        <div class="p-3 bg-amber-500/10 text-amber-400 rounded-lg border border-amber-500/20">
          <i data-lucide="sparkles" class="w-6 h-6"></i>
        </div>
        <div>
          <p class="text-xs text-gray-400 font-medium">MoLab Gallery</p>
          <h3 id="stat-gallery-count" class="text-2xl font-bold font-mono">111+</h3>
          <p class="text-xs text-gray-400">Neural Recipes</p>
        </div>
      </div>
    </section>

    <!-- Navigation Tabs -->
    <div class="flex border-b border-cardborder space-x-6 text-sm font-medium">
      <button onclick="switchTab('pods')" id="tab-pods" class="pb-3 border-b-2 border-cyanprimary text-cyanprimary flex items-center space-x-2">
        <i data-lucide="server" class="w-4 h-4"></i>
        <span>Compute Pods</span>
      </button>
      <button onclick="switchTab('gallery')" id="tab-gallery" class="pb-3 border-b-2 border-transparent text-gray-400 hover:text-gray-200 flex items-center space-x-2">
        <i data-lucide="layout-grid" class="w-4 h-4"></i>
        <span>Community Gallery</span>
      </button>
      <button onclick="switchTab('jobs')" id="tab-jobs" class="pb-3 border-b-2 border-transparent text-gray-400 hover:text-gray-200 flex items-center space-x-2">
        <i data-lucide="play-circle" class="w-4 h-4"></i>
        <span>Background Jobs</span>
      </button>
    </div>

    <!-- TAB 1: Compute Pods -->
    <section id="content-pods" class="space-y-4">
      <div class="flex justify-between items-center">
        <h2 class="text-lg font-semibold flex items-center space-x-2">
          <span>Active Pod Containers</span>
          <span id="pods-count-badge" class="px-2 py-0.5 rounded-full text-xs bg-gray-800 text-gray-300">0</span>
        </h2>
        <div class="text-xs text-gray-400">
          Auto-refreshes every 5s • Safe Workload Protection active
        </div>
      </div>

      <div id="pods-grid" class="grid grid-cols-1 md:grid-cols-2 gap-4">
        <!-- Rendered dynamically -->
        <div class="col-span-full py-12 text-center text-gray-500">
          <i data-lucide="loader-2" class="w-8 h-8 animate-spin mx-auto mb-2 text-cyanprimary"></i>
          <p>Querying MoLab workspace and GPU nodes...</p>
        </div>
      </div>
    </section>

    <!-- TAB 2: Gallery -->
    <section id="content-gallery" class="hidden space-y-4">
      <div class="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-3">
        <div>
          <h2 class="text-lg font-semibold">MoLab Community Neural Gallery</h2>
          <p class="text-xs text-gray-400">111+ production AI pipelines and interactive notebooks</p>
        </div>
        <div class="relative w-full sm:w-72">
          <input type="text" id="gallery-search" oninput="debounceGallerySearch()" placeholder="Search recipes (e.g. chat, diffusion)..." class="w-full bg-cardbg border border-cardborder rounded-lg px-3 py-1.5 text-sm pl-9 text-gray-200 focus:outline-none focus:border-cyanprimary">
          <i data-lucide="search" class="w-4 h-4 text-gray-500 absolute left-2.5 top-2.5"></i>
        </div>
      </div>

      <div id="gallery-grid" class="grid grid-cols-1 md:grid-cols-3 gap-4">
        <!-- Rendered dynamically -->
      </div>
    </section>

    <!-- TAB 3: Jobs -->
    <section id="content-jobs" class="hidden space-y-4">
      <div class="flex justify-between items-center">
        <div>
          <h2 class="text-lg font-semibold">SQLite Job Engine & Artifacts</h2>
          <p class="text-xs text-gray-400">Autonomous background tasks tracked in local SQLite</p>
        </div>
      </div>

      <div class="bg-cardbg border border-cardborder rounded-xl overflow-hidden">
        <table class="w-full text-left text-sm">
          <thead class="bg-gray-900/60 border-b border-cardborder text-xs uppercase text-gray-400 font-mono">
            <tr>
              <th class="px-4 py-3">Status</th>
              <th class="px-4 py-3">Job Name</th>
              <th class="px-4 py-3">Command</th>
              <th class="px-4 py-3">Pod ID</th>
              <th class="px-4 py-3">Started</th>
              <th class="px-4 py-3 text-right">Actions</th>
            </tr>
          </thead>
          <tbody id="jobs-table-body" class="divide-y divide-cardborder">
            <tr>
              <td colspan="6" class="px-4 py-8 text-center text-gray-500">No background jobs executed yet.</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

  </main>

  <!-- Log Modal -->
  <div id="log-modal" class="hidden fixed inset-0 z-50 bg-black/80 flex items-center justify-center p-4">
    <div class="bg-cardbg border border-cardborder rounded-xl max-w-3xl w-full p-6 space-y-4 shadow-2xl">
      <div class="flex justify-between items-center border-b border-cardborder pb-3">
        <h3 id="modal-title" class="font-bold text-gray-200">Execution Logs</h3>
        <button onclick="closeLogModal()" class="text-gray-400 hover:text-white">
          <i data-lucide="x" class="w-5 h-5"></i>
        </button>
      </div>
      <pre id="modal-logs" class="bg-black/90 p-4 rounded-lg font-mono text-xs text-gray-300 max-h-96 overflow-y-auto whitespace-pre-wrap"></pre>
      <div class="flex justify-end">
        <button onclick="closeLogModal()" class="px-4 py-1.5 rounded-lg bg-gray-800 text-sm hover:bg-gray-700">Close</button>
      </div>
    </div>
  </div>

  <footer class="border-t border-cardborder py-4 text-center text-xs text-gray-500">
    MoLab Cloud GPU Fabric • Studio-Grade Architecture • 100% On-MoLab Permanence
  </footer>

  <script>
    let activeTab = 'pods';
    let galleryDebounceTimer = null;

    function switchTab(tab) {
      activeTab = tab;
      ['pods', 'gallery', 'jobs'].forEach(t => {
        const btn = document.getElementById('tab-' + t);
        const content = document.getElementById('content-' + t);
        if (t === tab) {
          btn.className = 'pb-3 border-b-2 border-cyanprimary text-cyanprimary flex items-center space-x-2';
          content.classList.remove('hidden');
        } else {
          btn.className = 'pb-3 border-b-2 border-transparent text-gray-400 hover:text-gray-200 flex items-center space-x-2';
          content.classList.add('hidden');
        }
      });
      lucide.createIcons();
    }

    async function fetchAuth() {
      try {
        const res = await fetch('/api/status');
        const data = await res.json();
        document.getElementById('user-email').innerText = data.email || 'Unauthenticated';
        document.getElementById('auth-badge').classList.remove('hidden');
        document.getElementById('stat-active-pods').innerText = data.active_pods_count;
        if (data.recommended_free_pod) {
          document.getElementById('stat-free-hint').innerText = '★ Free: ' + data.recommended_free_pod.substring(0, 10) + '...';
        }
      } catch (e) {
        console.error('Failed to fetch status:', e);
      }
    }

    async function fetchPods() {
      try {
        const res = await fetch('/api/pods');
        const pods = await res.json();
        const container = document.getElementById('pods-grid');
        document.getElementById('pods-count-badge').innerText = pods.length;

        if (pods.length === 0) {
          container.innerHTML = '<div class="col-span-full py-8 text-center text-gray-500">No active pods. Launch one with "molab create --blackwell"</div>';
          return;
        }

        container.innerHTML = pods.map(p => {
          const isFree = p.is_free;
          const statusBadge = isFree 
            ? '<span class="px-2 py-0.5 rounded text-xs bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 font-semibold">★ FREE / IDLE</span>'
            : '<span class="px-2 py-0.5 rounded text-xs bg-amber-500/20 text-amber-400 border border-amber-500/30 font-semibold">⚠️ OCCUPIED</span>';
          
          const vramUsed = p.vram_used_gb || 0;
          const vramTotal = 94.97;
          const pct = Math.min(100, Math.round((vramUsed / vramTotal) * 100));

          return `
            <div class="bg-cardbg border border-cardborder hover:border-gray-600 rounded-xl p-5 space-y-4 transition">
              <div class="flex justify-between items-start">
                <div>
                  <h3 class="font-bold text-gray-100 flex items-center space-x-2">
                    <span>${p.title || 'Untitled Pod'}</span>
                    ${p.gpu ? '<span class="text-xs px-2 py-0.5 rounded bg-blackwell/20 text-blackwell border border-blackwell/30 font-mono">RTX PRO 6000</span>' : ''}
                  </h3>
                  <p class="text-xs text-gray-500 font-mono">${p.id} • ${p.sandbox_id || 'Connecting...'}</p>
                </div>
                <div>${statusBadge}</div>
              </div>

              <!-- VRAM Meter -->
              <div class="space-y-1.5">
                <div class="flex justify-between text-xs">
                  <span class="text-gray-400">VRAM Allocation</span>
                  <span class="font-mono text-gray-200 font-semibold">${vramUsed.toFixed(1)} GB / ${vramTotal} GB (${pct}%)</span>
                </div>
                <div class="w-full h-2 bg-gray-800 rounded-full overflow-hidden">
                  <div class="h-full bg-gradient-to-r from-cyanprimary to-blackwell" style="width: ${pct}%"></div>
                </div>
              </div>

              <!-- Quick Actions -->
              <div class="grid grid-cols-2 sm:grid-cols-3 gap-2 pt-2 border-t border-cardborder">
                <button onclick="triggerAction('${p.id}', 'permanent')" class="px-3 py-1.5 rounded-lg bg-emerald-950/40 hover:bg-emerald-900/60 text-emerald-400 border border-emerald-800/40 text-xs font-medium flex items-center justify-center space-x-1">
                  <i data-lucide="shield" class="w-3.5 h-3.5"></i>
                  <span>Permanent 24/7</span>
                </button>
                <button onclick="triggerAction('${p.id}', 'vault_pack')" class="px-3 py-1.5 rounded-lg bg-blue-950/40 hover:bg-blue-900/60 text-blue-400 border border-blue-800/40 text-xs font-medium flex items-center justify-center space-x-1">
                  <i data-lucide="archive" class="w-3.5 h-3.5"></i>
                  <span>Pack Vault</span>
                </button>
                <button onclick="triggerAction('${p.id}', 'vault_unpack')" class="px-3 py-1.5 rounded-lg bg-purple-950/40 hover:bg-purple-900/60 text-purple-400 border border-purple-800/40 text-xs font-medium flex items-center justify-center space-x-1">
                  <i data-lucide="folder-output" class="w-3.5 h-3.5"></i>
                  <span>Unpack Vault</span>
                </button>
              </div>
            </div>
          `;
        }).join('');
        lucide.createIcons();
      } catch (e) {
        console.error('Failed to fetch pods:', e);
      }
    }

    async function fetchGallery(query = '') {
      try {
        const url = query ? `/api/gallery?q=${encodeURIComponent(query)}` : '/api/gallery';
        const res = await fetch(url);
        const templates = await res.json();
        const container = document.getElementById('gallery-grid');

        if (templates.length === 0) {
          container.innerHTML = '<div class="col-span-full py-8 text-center text-gray-500">No matching recipes found.</div>';
          return;
        }

        container.innerHTML = templates.slice(0, 18).map(t => `
          <div class="bg-cardbg border border-cardborder hover:border-cyanprimary/50 rounded-xl p-4 flex flex-col justify-between space-y-3 transition">
            <div>
              <h4 class="font-bold text-gray-200 text-sm leading-tight">${t.title}</h4>
              <p class="text-xs text-gray-500 font-mono mt-1">${t.slug}</p>
            </div>
            <div class="flex items-center justify-between pt-2 border-t border-cardborder">
              <a href="${t.url}" target="_blank" class="text-xs text-cyanprimary hover:underline flex items-center space-x-1">
                <span>View on MoLab</span>
                <i data-lucide="external-link" class="w-3 h-3"></i>
              </a>
              <span class="text-xs text-gray-400 font-mono">1-Click Ready</span>
            </div>
          </div>
        `).join('');
        lucide.createIcons();
      } catch (e) {
        console.error('Failed to fetch gallery:', e);
      }
    }

    function debounceGallerySearch() {
      clearTimeout(galleryDebounceTimer);
      galleryDebounceTimer = setTimeout(() => {
        const q = document.getElementById('gallery-search').value;
        fetchGallery(q);
      }, 300);
    }

    async function fetchJobs() {
      try {
        const res = await fetch('/api/jobs');
        const jobs = await res.json();
        const tbody = document.getElementById('jobs-table-body');

        if (!jobs || jobs.length === 0) {
          tbody.innerHTML = '<tr><td colspan="6" class="px-4 py-8 text-center text-gray-500">No background jobs executed yet.</td></tr>';
          return;
        }

        tbody.innerHTML = jobs.map(j => `
          <tr class="hover:bg-gray-800/40 transition">
            <td class="px-4 py-3">
              <span class="px-2 py-0.5 rounded text-xs font-mono font-semibold ${
                j.status === 'COMPLETED' ? 'bg-emerald-500/20 text-emerald-400' :
                j.status === 'RUNNING' ? 'bg-cyanprimary/20 text-cyanprimary animate-pulse' :
                j.status === 'FAILED' ? 'bg-red-500/20 text-red-400' : 'bg-gray-700 text-gray-300'
              }">${j.status}</span>
            </td>
            <td class="px-4 py-3 font-medium text-gray-200">${j.name || 'Unnamed Job'}</td>
            <td class="px-4 py-3 font-mono text-xs text-gray-400 truncate max-w-xs">${j.command}</td>
            <td class="px-4 py-3 font-mono text-xs text-gray-500">${(j.notebook_id || '').substring(0, 10)}...</td>
            <td class="px-4 py-3 text-xs text-gray-400">${new Date(j.created_at).toLocaleTimeString()}</td>
            <td class="px-4 py-3 text-right">
              <button onclick="viewLogs('${j.id}')" class="px-2.5 py-1 rounded bg-gray-800 hover:bg-gray-700 text-xs text-gray-300 font-mono">Logs</button>
            </td>
          </tr>
        `).join('');
      } catch (e) {
        console.error('Failed to fetch jobs:', e);
      }
    }

    async function viewLogs(jobId) {
      try {
        const res = await fetch(`/api/jobs/${jobId}/logs`);
        const data = await res.json();
        document.getElementById('modal-title').innerText = `Execution Logs — Job ${jobId}`;
        document.getElementById('modal-logs').innerText = data.logs || 'No logs recorded yet.';
        document.getElementById('log-modal').classList.remove('hidden');
      } catch (e) {
        alert('Failed to retrieve logs: ' + e);
      }
    }

    function closeLogModal() {
      document.getElementById('log-modal').classList.add('hidden');
    }

    async function triggerAction(podId, action) {
      if (!confirm(`Are you sure you want to run "${action}" on pod ${podId}?`)) return;
      try {
        const res = await fetch(`/api/pods/${podId}/${action}`, { method: 'POST' });
        const data = await res.json();
        alert('Action complete: ' + JSON.stringify(data));
        refreshAll();
      } catch (e) {
        alert('Action error: ' + e);
      }
    }

    function refreshAll() {
      const icon = document.getElementById('refresh-icon');
      if (icon) icon.classList.add('animate-spin');
      Promise.all([fetchAuth(), fetchPods(), fetchGallery(), fetchJobs()]).finally(() => {
        if (icon) icon.classList.remove('animate-spin');
      });
    }

    // Initialize
    refreshAll();
    setInterval(fetchPods, 5000);
  </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def serve_dashboard():
    """Serve the single-page responsive Web Dashboard."""
    return HTMLResponse(content=DASHBOARD_HTML)


@app.get("/api/status")
def api_status():
    """Return auth status and pod counts."""
    auth_info = inspect_auth_status()
    client = MoLabClient()
    running_pods = client.list_running_sandboxes()

    recommended_free_pod = None
    for nb_id in running_pods:
        try:
            sess = SandboxSession(nb_id, client=client)
            status = sess.get_workload_status()
            if not status.get("is_occupied", False):
                recommended_free_pod = nb_id
                break
        except Exception:
            pass

    return {
        "authenticated": auth_info.get("authenticated", False),
        "email": auth_info.get("user_email"),
        "active_pods_count": len(running_pods),
        "recommended_free_pod": recommended_free_pod,
    }


@app.get("/api/pods")
def api_pods():
    """List all running pods with hardware and occupancy telemetry."""
    client = MoLabClient()
    notebooks = {nb["id"]: nb for nb in client.list_notebooks()}
    running_sandboxes = client.list_running_sandboxes()

    result = []
    for nb_id, sb_info in running_sandboxes.items():
        nb_meta = notebooks.get(nb_id, {})
        vram_used = 0.0
        is_free = True
        try:
            sess = SandboxSession(nb_id, client=client)
            wl = sess.get_workload_status()
            is_free = not wl.get("is_occupied", False)
            vram_used = wl.get("gpu_allocated_gb", 0.0)
        except Exception:
            pass

        result.append({
            "id": nb_id,
            "title": nb_meta.get("title", "Untitled Pod"),
            "sandbox_id": sb_info.get("sandbox_id"),
            "gpu": nb_meta.get("gpu", "rtxp6000"),
            "is_free": is_free,
            "vram_used_gb": vram_used,
        })

    return result


@app.post("/api/pods/{notebook_id}/permanent")
def api_pod_permanent(notebook_id: str):
    """Arm 100% on-MoLab permanence mode."""
    from molab_cli.keepalive import KeepaliveManager
    km = KeepaliveManager()
    daemon_info = km.start_daemon(notebook_id=notebook_id, auto_restore=True)
    return {"status": "ARMED", "daemon": daemon_info}


@app.post("/api/pods/{notebook_id}/vault_pack")
def api_vault_pack(notebook_id: str):
    """Pack /workspace into in-notebook vault."""
    sess = SandboxSession(notebook_id)
    vault = MoLabVault(sess)
    res = vault.pack_workspace()
    return res


@app.post("/api/pods/{notebook_id}/vault_unpack")
def api_vault_unpack(notebook_id: str):
    """Unpack in-notebook vault back into /workspace."""
    sess = SandboxSession(notebook_id)
    vault = MoLabVault(sess)
    res = vault.unpack_workspace()
    return res


@app.get("/api/gallery")
def api_gallery(q: Optional[str] = Query(None)):
    """Return MoLab Community Gallery templates."""
    gm = GalleryManager()
    if q:
        return gm.search_templates(q)
    return gm.list_templates()


@app.get("/api/jobs")
def api_jobs():
    """Return recent background jobs from SQLite."""
    jm = JobManager()
    return jm.list_jobs(limit=25)


@app.get("/api/jobs/{job_id}/logs")
def api_job_logs(job_id: str):
    """Fetch trailing logs for a job."""
    jm = JobManager()
    logs = jm.get_job_logs(job_id, lines=50)
    return {"logs": logs}


def start_web_server(port: int = 8080, open_browser: bool = True):
    """Start the Uvicorn web server."""
    from rich.console import Console
    from rich.panel import Panel
    console = Console()

    url = f"http://127.0.0.1:{port}"
    console.print(Panel(
        f"[bold green]✨ MoLab Web Control Center is Running![/bold green]\n\n"
        f" • Local URL: [bold cyan]{url}[/bold cyan]\n"
        f" • Architecture: NVIDIA RTX PRO 6000 Blackwell Fabric\n"
        f" • Press [bold yellow]Ctrl+C[/bold yellow] to stop the dashboard server.",
        title="[bold cyan]MoLab Web Dashboard[/bold cyan]",
        border_style="cyan",
        padding=(1, 2),
    ))

    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    start_web_server()
