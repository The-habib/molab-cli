# MoLab Python SDK Studio-Grade API Reference

The `molab_cli.sdk` package provides a high-level, production-grade Python interface for controlling MoLab CoreWeave cloud GPU pods (NVIDIA RTX PRO 6000 Blackwell 96GB VRAM) from Android / Termux and automated AI agents.

---

## Architecture Overview

```
                               ┌──────────────────────────┐
                               │         MoLabSDK         │
                               └────────────┬─────────────┘
                                            │
        ┌───────────────────┬───────────────┴──────────────┬──────────────────┐
        ▼                   ▼                              ▼                  ▼
  ┌───────────┐      ┌───────────────┐              ┌──────────────┐   ┌──────────────┐
  │    Pod    │      │ MoLabVault    │              │ GalleryMgr   │   │ BatchOrch    │
  └─────┬─────┘      └───────────────┘              └──────────────┘   └──────────────┘
        │
        ├─► SandboxSession (PTY / WebSocket / HTTP Proxy)
        └─► MarimoBackendClient (Kernel REST / File APIs / Usage / Og)
```

---

## 1. `MoLabSDK`

Main entry point for all programmatic orchestration.

### Initialization

```python
from molab_cli.sdk import MoLabSDK

sdk = MoLabSDK(
    credentials_path="~/.config/molab/credentials.json",  # Optional custom path
    default_gpu="rtxp6000"                               # Default hardware target
)
```

### Discovery & Health

#### `sdk.doctor() -> Dict[str, Any]`
Performs a comprehensive local and remote environment health audit.
* **Returns:**
  ```python
  {
      "auth": {"authenticated": True, "email": "user@example.com"},
      "cli_tools": {"curl": True, "ffmpeg": True, "jq": True},
      "active_pods": 2,
      "recommended_free_pod": "nb_emuqXoWkVed6jPNZxND7eo"
  }
  ```

#### `sdk.capabilities() -> Dict[str, Any]`
Returns hardware capabilities, supported GPU architectures, and runtime features.

#### `sdk.list_pods() -> List[Pod]`
Returns a list of `Pod` objects representing all notebooks in the workspace.

#### `sdk.get_pod(notebook_id: str) -> Pod`
Instantiates a `Pod` handle for a specific notebook.
* **Parameters:** `notebook_id` (str) — Full ID (`nb_...`) or 20-char slug.

#### `sdk.get_free_pod() -> Optional[Pod]`
Audits all running Blackwell GPU pods, checks process tables and VRAM usage, and returns an idle pod ready for immediate execution. Returns `None` if all pods are busy.

### Workload Registry & Jobs

#### `sdk.run(workload_name: str, pod_id: Optional[str] = None, **kwargs) -> Dict[str, Any]`
Executes a registered workload profile (e.g. `video-enhance`, `serve-llm`, `whisper-transcribe`).

#### `sdk.submit_job(command: str, name: Optional[str] = None, pod_id: Optional[str] = None, workdir: str = "/workspace") -> Dict[str, Any]`
Submits an asynchronous background job tracked in the local SQLite database.

#### `sdk.get_job(job_id: str) -> Dict[str, Any]`
Fetches status, exit code, start/finish timestamps, and metadata for a job.

#### `sdk.cancel_job(job_id: str) -> bool`
Sends SIGTERM/SIGKILL to a running remote background job.

### Community Gallery

#### `sdk.gallery_list(limit: int = 50) -> List[Dict[str, Any]]`
Lists curated AI recipes and community notebooks from MoLab Gallery.

#### `sdk.gallery_search(query: str) -> List[Dict[str, Any]]`
Searches templates by title, description, or keyword.

#### `sdk.gallery_info(slug: str) -> Dict[str, Any]`
Fetches metadata, source GitHub URL, and download links for a recipe.

#### `sdk.gallery_download(slug: str, output_path: str) -> str`
Downloads runnable Python notebook code directly to disk.

---

## 2. `Pod`

Represents an active or provisionable cloud compute container.

### Properties
* `pod.id`: Notebook unique identifier (`nb_...`).
* `pod.name`: Human-readable notebook name.
* `pod.gpu`: GPU model string (e.g. `rtxp6000`).
* `pod.is_running`: Boolean indicating container runtime status.

### Execution & PTY

#### `pod.exec(command: str, timeout: int = 60, env: Optional[Dict[str, str]] = None) -> Dict[str, Any]`
Executes a shell command inside the pod via the secure proxy PTY.
* **Parameters:**
  * `command` (str): Bash command string.
  * `timeout` (int): Maximum execution time in seconds.
  * `env` (dict): Optional remote environment variables.
* **Returns:**
  ```python
  {"stdout": "...", "stderr": "", "exit_code": 0, "duration_sec": 1.24}
  ```

#### `pod.eval(python_code: str) -> Dict[str, Any]`
Evaluates arbitrary Python code directly inside the live Marimo kernel without launching an external shell. Returns parsed JSON or textual output.

### High-Speed File Streaming

#### `pod.push(local_path: str, remote_path: str, recursive: bool = False) -> Dict[str, Any]`
Uploads local files or directories directly to the pod using native chunked HTTP streaming.
* **Parameters:**
  * `local_path` (str): Path on local storage.
  * `remote_path` (str): Target destination on pod (e.g. `/workspace/data/`).
  * `recursive` (bool): If True, recurses through subdirectories.
* **Performance:** Up to 120MB/s without memory buffering.

#### `pod.pull(remote_path: str, local_path: str, recursive: bool = False) -> Dict[str, Any]`
Downloads files or directories from the pod to local storage.

#### `pod.sync(local_dir: str, remote_dir: str) -> Dict[str, Any]`
Delta synchronization engine: computes local and remote SHA-256 manifests, transferring only modified or newly created files.

### Telemetry & Diagnostics

#### `pod.gpu_telemetry() -> Dict[str, Any]`
Queries real-time NVIDIA Management Library (NVML) data:
* GPU Model, VRAM Used / Total (MB), Temperature (°C), Compute Utilization (%), Power Usage (W).

#### `pod.usage() -> Dict[str, Any]`
Fetches kernel and host memory cgroup telemetry via native Marimo REST route (`/api/usage`):
* `used_gb`, `total_gb`, `percent_used`, `server_memory_mb`, `kernel_memory_mb`, `gpus`.

#### `pod.environment() -> Dict[str, Any]`
Fetches complete remote container runtime specifications:
* OS (`Linux`), gVisor kernel version (`4.19.0-gvisor`), Python (`3.13`), Node.js (`v22`), uv (`0.12.1`), pre-installed ML packages (`torch`, `torchvision`, `transformers`, etc.).

#### `pod.thumbnail(output_path: str = "thumbnail.svg") -> str`
Generates and downloads a dynamic Open Graph SVG thumbnail of the remote notebook canvas.

#### `pod.connections() -> Dict[str, Any]`
Audits active client WebSocket connections to the notebook server.

#### `pod.sessions() -> List[Dict[str, Any]]`
Lists active interactive notebook sessions.

### Permanence & Vault

#### `pod.vault_save(max_size_mb: int = 50) -> Dict[str, Any]`
Compresses `/workspace` into a permanent self-extracting Marimo cell saved inside MoLab cloud database. **Uses 0 bytes of local phone storage.**

#### `pod.vault_restore() -> Dict[str, Any]`
Unpacks the embedded vault back into `/workspace`.

#### `pod.make_permanent(interval: int = 120) -> Dict[str, Any]`
Activates Android wake-lock, in-pod anti-idle guard, and supervisor background keepalive daemon.

---

## 3. `MoLabVault`

100% on-MoLab persistence manager. Embeds encrypted/compressed tarballs directly into `/marimo/notebook.py` cloud metadata.

```python
from molab_cli.vault import MoLabVault
from molab_cli.sandbox import SandboxSession

session = SandboxSession("nb_emuqXoWkVed6jPNZxND7eo")
vault = MoLabVault(session)

# 1. Inspect existing vault
status = vault.inspect_vault()
print("Vault exists:", status["exists"], "Size:", status.get("size_bytes", 0))

# 2. Pack workspace to cloud
pack_res = vault.pack_workspace(source_dir="/workspace", max_size_mb=100)
print("Pack Result:", pack_res["status"], "Bytes:", pack_res["compressed_bytes"])

# 3. Restore workspace on clean pod
unpack_res = vault.unpack_workspace(target_dir="/workspace")
print("Unpack Result:", unpack_res["status"], "Files:", unpack_res["extracted_files"])
```

---

## 4. `KeepaliveManager`

Defeats the 30-minute idle reaper by orchestrating detached supervisor daemons.

```python
from molab_cli.keepalive import KeepaliveManager

mgr = KeepaliveManager()

# 1. Start daemon for a pod
mgr.start_daemon(
    notebook_id="nb_emuqXoWkVed6jPNZxND7eo",
    interval=120,            # Heartbeat every 2 minutes
    max_hours=24,            # Keep alive for 24 hours
    auto_restore=True        # Auto-restore vault if pod resets
)

# 2. Check daemon status
status = mgr.get_status("nb_emuqXoWkVed6jPNZxND7eo")
print("PID:", status["pid"], "Heartbeats:", status["heartbeat_count"])

# 3. View trailing logs
logs = mgr.get_logs("nb_emuqXoWkVed6jPNZxND7eo", lines=25)

# 4. Stop daemon
mgr.stop_daemon("nb_emuqXoWkVed6jPNZxND7eo")
```

---

## 5. `GalleryManager`

Programmatic client for MoLab 111+ open-source recipes and templates.

```python
from molab_cli.gallery import GalleryManager

gm = GalleryManager()

# 1. List all templates
templates = gm.list_templates()
print(f"Total community templates: {len(templates)}")

# 2. Search for specialized workloads
results = gm.search_templates("diffusion")
for r in results:
    print(r["slug"], "-", r["title"])

# 3. Get detailed metadata
info = gm.get_template_info("stable-diffusion-3")
print("GitHub URL:", info["github_url"])

# 4. Download directly to local path or pod
gm.download_template("stable-diffusion-3", "/workspace/sd3.py")
```

---

## 6. `BatchOrchestrator`

High-throughput multi-pod DAG pipeline execution engine.

```python
from molab_cli.batch import BatchOrchestrator

orchestrator = BatchOrchestrator()

# Define pipeline with dependencies
pipeline = {
    "name": "video-transcode-and-upscale",
    "tasks": [
        {
            "id": "extract-audio",
            "command": "ffmpeg -i /workspace/input.mp4 -vn -c:a aac /workspace/audio.m4a",
            "depends_on": []
        },
        {
            "id": "upscale-frames",
            "command": "python3 /workspace/upscale.py --input /workspace/input.mp4",
            "depends_on": []
        },
        {
            "id": "mux-output",
            "command": "ffmpeg -i /workspace/upscaled.mp4 -i /workspace/audio.m4a -c copy /workspace/final_4k.mp4",
            "depends_on": ["extract-audio", "upscale-frames"]
        }
    ]
}

# Run across available Blackwell pods
batch_id = orchestrator.submit(pipeline)
results = orchestrator.wait(batch_id, poll_interval=5)
print("Pipeline Status:", results["status"])
```

---

## 7. `MarimoBackendClient`

Low-level client for direct Starlette / FastAPI backend REST endpoints on the pod.

```python
from molab_cli.backend import MarimoBackendClient
from molab_cli.sandbox import SandboxSession

session = SandboxSession("nb_emuqXoWkVed6jPNZxND7eo")
backend = MarimoBackendClient(session)

# Health & Status
server_status = backend.get_server_status()
print("Filename:", server_status.get("filename"))

# System Specs
env = backend.get_environment()
print("Kernel:", env["OS Version"], "Python:", env["Python Version"])

# Dynamic Open Graph SVG Thumbnail
backend.get_thumbnail("notebook_preview.svg")

# Real-time WebSocket audits
connections = backend.get_connections()
print("Active connections:", connections["active"])

# Session management
sessions = backend.get_sessions()
print("Active sessions:", len(sessions))
```
