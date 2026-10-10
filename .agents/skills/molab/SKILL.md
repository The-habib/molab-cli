---
name: molab
description: Control and automate MoLab CoreWeave cloud GPU pods (NVIDIA RTX PRO 6000 Blackwell 96GB VRAM) from Android/Termux. Use when provisioning GPU pods, inspecting telemetry, serving uncensored LLMs, running 4K neural video/image enhancement, performing high-speed file transfers via native Marimo HTTP streaming APIs, running anti-idle keepalive daemons to prevent the 30-minute session timeout, creating workspace snapshots to protect against pod resets, or bridging cloud AI endpoints to localhost:8000.
argument-hint: "[free-pod, keepalive, snapshot, storage, push, pull, exec, forward, gpu, compute, recipe]"
license: MIT
metadata:
  author: antigravity
  version: "2.4.0"
---

# MoLab Cloud GPU Master Skill

Comprehensive guide for controlling, automating, and extending temporary **MoLab cloud GPU pods** powered by **NVIDIA RTX PRO 6000 Blackwell Server Edition (96GB VRAM)** on CoreWeave infrastructure from Termux / Android.

---

## 1. Core Philosophy: The Extensible Toolchain Directive

> [!IMPORTANT]
> **`molab-cli` is an open, agent-extensible tool — not a frozen black box.**
> As an autonomous agent, whenever you encounter a missing feature, new endpoint, buffer bottleneck, or novel workflow need, you are expected to:
> 1. Inspect the source code at [`~/molab-cli/src/molab_cli/`](file:///data/data/com.termux/files/home/molab-cli/src/molab_cli/).
> 2. Implement the enhancement directly in Python.
> 3. Verify changes with `pytest ~/molab-cli/tests/`.
> 4. Commit your improvements via `git -C ~/molab-cli commit -am "feat: ..."` so that future agents and sessions benefit.

The CLI package is installed in **editable mode** directly into the environment, meaning changes to `~/molab-cli/src/molab_cli/` take effect immediately without reinstallation.

---

## 2. Architecture & Gateways

MoLab pairs an ephemeral CoreWeave Kubernetes pod with a local client:

```
+-----------------------------------------------------------------------------------+
| Android / Termux                                                                  |
|   molab-cli (Python 3.14)                                                         |
|   ~/.config/molab/config.json (Clerk auth cookie, session token)                  |
+-----------------------------------------------------------------------------------+
          |                                                               |
  [1] REST / HTTPS Gateway                                        [2] WebSocket PTY Gateway
  https://<sb-id>.sb.molab.run                                   wss://<sb-id>.sb.molab.run/terminal/ws
  - /api/files/create (Multipart upload)                          - Interactive root shell
  - /api/files/download (Direct stream)                           - Single command exec (molab exec)
  - /api/status (Pod health & marimo kernel)                      - Hard limit: 4096 bytes input buffer
          |                                                               |
          +-------------------------------+-------------------------------+
                                          |
+-----------------------------------------------------------------------------------+
| Remote CoreWeave Sandbox Pod (Ubuntu Linux, root)                                 |
|   - NVIDIA RTX PRO 6000 Blackwell Server Edition (96 GB GDDR7 VRAM, sm_120)       |
|   - Driver 595.71+, CUDA 13.2+, PyTorch 2.11+                                     |
|   - Marimo server on port 8080 (Process 1)                                        |
|   - User workload directory: /workspace                                          |
+-----------------------------------------------------------------------------------+
```

---

## 3. The 5 Golden Safety Rules for Autonomous Agents

### Rule 1: Always Identify & Respect Occupied vs. Free Pods
Users often have multiple notebooks. One pod may be running a vital long-term LLM server (`server.py`, `vllm`).
* **Never assume which pod to use.**
* **Run `molab free` (or `molab free --json`) first.**
* It automatically queries GPU VRAM and process tables to flag occupied pods as `OCCUPIED` and recommend the idle pod as `FREE / IDLE`.
* **Strictly never inspect, kill, stop, or overwrite workloads on occupied pods without explicit user permission.**

### Rule 2: NEVER Stream Large Files Over PTY / WebSocket
* Linux terminal PTYs have a hard **`MAX_INPUT = 4096 bytes`** limit.
* Trying to send files using `echo '<base64>' | base64 -d` over `molab exec` will overflow the buffer, drop bytes, stall, and fail with 25s timeouts.
* **Always use native HTTP streaming**:
  * Upload: `molab push <notebook_id> <local_file_or_dir> [remote_path] [-r]`
  * Download: `molab pull <notebook_id> <remote_path> [local_destination] [-r]`
  * Under the hood, this uses `POST /api/files/create` and `GET /api/files/download`.

### Rule 3: Run Compute Jobs Asynchronously in Background
* Foreground commands through `molab exec` have an execution timeout (default 30s).
* Any long task (video processing, model fine-tuning, weights download) must be run via `nohup` in the background:
  ```bash
  molab exec <id> "nohup python3 /workspace/my_job.py > /workspace/job.log 2>&1 &"
  ```
* Track progress by periodically inspecting the log:
  ```bash
  molab exec <id> "tail -n 20 /workspace/job.log ; ps aux | grep my_job"
  ```

### Rule 4: Protect Phone Storage & Thermal Budget
* Android phones running Termux have limited internal storage and aggressive thermal throttling.
* Never duplicate large video files or cache full 20GB+ model checkpoints locally on the phone.
* Stream uploads directly from `/storage/emulated/0/...` to the pod, and stream downloads straight back to `/storage/emulated/0/Download/...`.
* Once processing is finished, clean up temporary frame caches and models on the pod (`rm -rf /workspace/temp`).

### Rule 5: Dynamic Sandbox Resolution
* MoLab pods can restart, and their sandbox IDs (`sb-xxxxxxxx`) and auth tokens change dynamically upon restart.
* Never hardcode an old sandbox URL or token.
* Always resolve credentials dynamically using `SandboxSession(notebook_id)` or CLI commands.

### Rule 6: Mandatory Kernel Evaluation & Compiled Kernels over Fragile PTY and Naive PyTorch
* **PTY Buffer Hazard:** Terminal PTYs (`molab exec`, WebSocket) have a strict **`MAX_INPUT = 4,096 bytes`** limit. Overrunning it silently drops characters, corrupts payloads, and crashes.
* **Kernel Eval Priority:** For state inspection, variable extraction, or telemetry, **always prioritize Direct Kernel Evaluation (`molab kernel eval` / `Pod.eval()`) and native REST routes (`molab usage`, `molab env`)** over spawning PTY bash subshells.
* **Inference Acceleration (Kernels > PyTorch):** For model serving on the Blackwell RTX PRO 6000, **never use naive PyTorch inference loops**. Strictly deploy with compiled, fused kernels (**FlashInfer**, **Triton**, **Chunked Prefill**, **vLLM**) to saturate the 96GB GDDR7 memory bus and achieve sub-80ms TTFT.

---

## 4. Complete CLI Command Reference

### Pod Discovery & Audit
| Command | Options | Description |
| :--- | :--- | :--- |
| `molab free` | `-j, --json` | **Audits all running Blackwell pods**; displays VRAM and active workloads; highlights recommended free pod. |
| `molab list` | `-j, --json` | Lists all cloud notebooks in workspace (ID, title, running status, GPU). |
| `molab ps` | `-j, --json` | Lists actively running CoreWeave sandbox containers. |
| `molab status` | `-j, --json` | Shows Clerk user email, session ID, client ID, and organization status. |
| `molab inspect <id>` | `-j, --json` | Displays notebook hardware specs, sandbox pod ID, and Marimo health. |
| `molab gpu <id>` | `-j, --json` | Shows real-time Blackwell GPU telemetry: VRAM, SMs, CUDA version, temperature. |

### Lifecycle & Compute Control
| Command | Options | Description |
| :--- | :--- | :--- |
| `molab deploy <alias> [id]` | `--port`, `--max-model-len`, `--gpu-util`, `--force`, `-j` | **1-Click Autonomous Model Deployment**: Deploys industry-grade catalog models (`qwen-32b`, `coder-32b`, `r1-32b`, `llama-70b`) on Blackwell with verified hyperparameters, process guardian, local bridge, and client credentials. |
| `molab create` | `--blackwell`, `--cpu <N>`, `--memory <N>`, `--title <T>` | Provisions a brand new CoreWeave sandbox pod (defaults to RTX PRO 6000 Blackwell). |
| `molab compute <id>` | `--blackwell / --cpu-only`, `--cpu <N>`, `--memory <N>` | Hot-swaps notebook hardware between CPU and RTX PRO 6000 Blackwell (96GB). |
| `molab stop <id>` | | Stops a running pod to conserve cloud compute hours. |
| `molab delete <id>` | `-y, --yes` | Permanently deletes a notebook. |

### Diagnostics & Capabilities
| Command | Options | Description |
| :--- | :--- | :--- |
| `molab doctor` | `-j, --json` | Comprehensive health checks: auth, network, storage, tools, and active pods. |
| `molab capabilities` | `-j, --json` | Verified inventory of local, remote, execution, and transfer capabilities. |

### File Transfer & Directory Sync
| Command | Options | Description |
| :--- | :--- | :--- |
| `molab push <id> <local> [remote]` | `-r, --recursive` | Streams local file or directory directly to pod disk via `/api/files/create`. |
| `molab pull <id> <remote> [local]` | `-r, --recursive` | Streams remote file or directory from pod to phone via `/api/files/download`. |
| `molab sync <id> <local> <remote>` | `--dry-run`, `-j` | Delta synchronizes local directory to pod using SHA-256 manifest comparison. |

### Universal Job & Artifact Subsystem
| Command | Options | Description |
| :--- | :--- | :--- |
| `molab job submit <id> "<cmd>"` | `--name`, `--workdir`, `-j` | Submits detached background job tracked in persistent local SQLite (`~/.config/molab/jobs.db`). |
| `molab job list` | `--limit <N>`, `-j` | Lists historic and active jobs with exit codes and status. |
| `molab job status <job_id>` | `-j, --json` | Synchronizes controller state with remote process and retrieves exit code. |
| `molab job logs <job_id>` | `--tail <N>` | Retrieves execution logs from pod. |
| `molab job cancel <job_id>` | | Terminates running background job on pod. |
| `molab job artifacts <job_id>` | `--download <dir>` | Lists or downloads generated output artifacts with SHA-256 verification. |

### Execution, Services & Agent Integration
| Command | Options | Description |
| :--- | :--- | :--- |
| `molab exec <id> "<cmd>"` | `--timeout <sec>` | Runs a structured bash command on pod root and returns output. |
| `molab shell <id>` | | Opens an interactive root shell (WS PTY). |
| `molab serve status <id>` | `--port 8000`, `-j` | Checks application-level health of model server on pod. |
| `molab forward <id>` | `--port 8000` | Bridges remote port 8000 to local `http://127.0.0.1:8000/v1` (OpenAI compatible). |
| `molab bridge [start\|status\|stop]` | `--port 8000` | Unified Blackwell AI Bridge hosting **Hermes Agent** (port 8000) and **Claude Code** (port 8082) concurrently with live reasoning & native tool calling. |
| `molab chat [id]` | `-p <str>`, `-y/--auto`, `--no-tools`, `--claude`, `--hermes`, `--think full/compact/off`, `--temp`, `--tokens` | **Industry-grade terminal coding agent** with autonomous tool calling loop (`run_shell`, `read_file`, `edit_file`, `write_file`, `list_dir`, `grep_search`), live thinking streaming, instant `/undo`, git awareness, and tok/s metrics. |
| `molab perf [id]` | `-j, --json` | **Enterprise Model Telemetry**: Profiles real-time vLLM Automatic Prefix Caching (APC) Hit Rate %, Time To First Token (TTFT), KV Cache VRAM allocation, and Blackwell GPU thermal/wattage. |
| `molab keys [create\|list\|revoke]` | `--rpm <N>`, `--tpm <N>`, `-j` | **AI Gateway Virtual Keys**: Create and manage multi-tenant API keys with per-key RPM/TPM limits and SQLite WAL audit logs. |
| `molab stats` | `--limit <N>`, `-j` | **Real-Time Gateway Analytics**: Audit token metering, request volume, TTFT, and latency across all model sessions. |
| `molab share` | `--port 8000`, `--token <tok>`, `--status`, `--stop`, `-j` | **Production Tunnel Ingress**: Zero-config Cloudflare Quick Tunnel or persistent Named Tunnel (`--token`) exposing model endpoint. |
| `molab mcp` | | Starts Model Context Protocol (MCP) JSON-RPC 2.0 stdio server (15 typed tools). |


---

## 5. Python SDK Usage

You can automate MoLab directly inside Python scripts:

```python
from molab_cli.client import MoLabClient
from molab_cli.sandbox import SandboxSession

# 1. Initialize client and session
session = SandboxSession("nb_emuqXoWkVed6jPNZxND7eo")
session.resolve()

print("Base URL:", session.base_url)        # https://<sandbox_id>.sb.molab.run
print("Auth Token:", session.auth_token)    # fb2b9b0c...

# 2. Check pod workload & free VRAM
workload = session.get_workload_status()
if not workload["is_occupied"]:
    print(f"Pod is free with {workload['free_vram_gb']} GB VRAM available!")

# 3. High-speed file transfers
dest, size = session.push_file("my_video.mp4", "/workspace/input.mp4")
print(f"Uploaded {size} bytes to {dest}")

# 4. Remote execution
output = session.execute_command("nvidia-smi")
print(output)

# 5. Pull output back
local_dest, dl_size = session.pull_file("/workspace/video_enhanced.mp4", "~/Download/")
print(f"Downloaded {dl_size} bytes to {local_dest}")
```

---

## 6. Standard Recipes

### Recipe A: Heavy Compute (e.g. 4K Neural Video Enhancement)
1. Query free pod: `molab free --json` -> grab `recommended_free_pod`.
2. Push source file: `molab push <pod_id> /storage/emulated/0/DCIM/Camera/video.mp4 /workspace/input.mp4`
3. Push pipeline script: `molab push <pod_id> pipeline.py /workspace/pipeline.py`
4. Launch via nohup:
   ```bash
   molab exec <pod_id> "nohup python3 /workspace/pipeline.py > /workspace/pipeline.log 2>&1 &"
   ```
5. Monitor log periodically:
   ```bash
   molab exec <pod_id> "tail -n 15 /workspace/pipeline.log ; ps aux | grep pipeline"
   ```
6. Pull enhanced result:
   ```bash
   molab pull <pod_id> /workspace/output_4k.mp4 /storage/emulated/0/Download/
   ```
7. Clean up pod scratch files:
   ```bash
   molab exec <pod_id> "rm -rf /workspace/input.mp4 /workspace/output_4k.mp4"
   ```

### Recipe B: Localhost OpenAI API Bridge
1. Start remote server on pod (e.g. `vLLM` or custom `server.py` on port 8000).
2. Start the bridge:
   ```bash
   molab forward <pod_id> --port 8000
   ```
3. Use `http://localhost:8000/v1` in any OpenAI-compatible app on Android (Open WebUI, SillyTavern, or coding agents).

### Recipe C: Autonomous Multi-Pod Batch Pipelines (v2.2)
1. Write a DAG workload manifest (`pipeline.json`):
   ```json
   {
     "version": "1.0",
     "name": "media-pipeline",
     "concurrency_limit": 2,
     "tasks": [
       {"id": "step1", "command": "python3 /workspace/prep.py", "requirements": {"gpu": false}},
       {"id": "step2", "command": "python3 /workspace/train.py", "dependencies": ["step1"], "requirements": {"gpu": true, "min_vram_gb": 16.0}}
     ]
   }
   ```
2. Validate pipeline schema and execution stages:
   ```bash
   molab batch validate pipeline.json
   ```
3. Execute autonomous orchestration across candidate pods:
   ```bash
   molab batch run pipeline.json
   ```
4. Inspect live progress and task status:
   ```bash
   molab batch status <batch_id>
   molab batch logs <batch_id> --task step2
   ```

### Recipe D: Native Marimo Kernel Evaluation & Server File Operations
1. Check real-time hardware telemetry (RAM, server RAM, GPU):
   ```bash
   molab usage <pod_id>
   ```
2. Execute Python directly inside the Marimo kernel without terminal PTY buffers:
   ```bash
   molab kernel eval <pod_id> "import torch; print(torch.cuda.get_device_name(0))"
   ```
3. Export notebook without local toolchain dependencies:
   ```bash
   molab export html <pod_id> -o analysis.html
   molab export md <pod_id> -o report.md
   molab export ipynb <pod_id> -o notebook.ipynb
   ```
4. Fast server-side file management without local network transfers:
   ```bash
   molab file ls <pod_id> /workspace
   molab file cat <pod_id> /workspace/config.yaml
   molab file cp <pod_id> /workspace/model.pt /workspace/model_backup.pt
   molab file search <pod_id> "checkpoint" --path /workspace
   ```

### Recipe E: Permanent 24/7 Pod Operation & 100% On-MoLab In-Notebook Vault
CoreWeave sandbox pods enforce a **30-minute idle session timeout** (`expires_at = now + 1800`), and pod `/workspace` storage is an ephemeral overlayfs container that wipes files on reboot. MoLab completely defeats this with **100% on-MoLab persistent infrastructure**:

1. **1-Click Permanent Pod Machine (`molab permanent`):**
   ```bash
   # One command does everything: locks Android wake-lock, deploys in-pod self-sustaining guard,
   # packs workspace into MoLab cloud vault, and launches autonomous supervisor daemon:
   molab permanent <pod_id>
   ```

2. **100% On-MoLab Storage Vault (`molab vault` — Zero Phone Storage, Zero 3rd-Party Cloud):**
   Stores workspace files directly inside `/marimo/notebook.py` as a self-extracting cell saved to MoLab's cloud database. When a pod reboots, Marimo automatically re-extracts all files on boot!
   ```bash
   # Pack /workspace directly into notebook cell on MoLab:
   molab vault pack <pod_id>

   # Inspect active vault metadata and size:
   molab vault inspect <pod_id>

   # Manually restore / unpack vault:
   molab vault unpack <pod_id>
   ```

3. **Background Anti-Idle Supervisor & Auto-Resurrection:**
   ```bash
   # Inspect daemon health, heartbeat counter, and remaining session TTL:
   molab keepalive status <pod_id>
   molab keepalive logs <pod_id>

   # Cleanly stop keepalive daemon when compute is finished:
   molab keepalive stop <pod_id>
   ```

4. **External Cloud Storage Bridge (Optional rclone / Hugging Face):**
   ```bash
   # Multi-gigabit transfer directly between pod and S3/R2/HF without touching local storage:
   molab storage rclone-backup <pod_id> r2:my-bucket/weights
   molab storage hf-pull <pod_id> meta-llama/Llama-3-8b --dest /workspace/llama3
   ```

### Recipe F: MoLab Community Gallery & 1-Click Neural Recipes
Browse and deploy 111+ curated production notebooks from MoLab Gallery (`https://molab.run/gallery`):
```bash
# Search gallery recipes:
molab gallery search "chat"

# Inspect recipe metadata and GitHub source:
molab gallery info "chat-with-pdf"

# Download runnable Python code:
molab gallery download "chat-with-pdf" ./chat_with_pdf.py
```

### Recipe G: Remote Environment Diagnostics & Visual Thumbnails
Inspect container runtime specs and generate Open Graph previews:
```bash
# Inspect gVisor kernel, Python 3.13, Node v22, uv, and pre-installed AI/ML packages:
molab env <pod_id>

# Audit active WebSocket client connections:
molab connections <pod_id>

# Generate visual Open Graph SVG thumbnail of the remote notebook:
molab thumbnail <pod_id> -o preview.svg
```

### Recipe H: Interactive Web & Terminal Control Centers (v2.3.1)
Zero-typing navigation and browser-based real-time control:
```bash
# 1. Launch local browser Web Control Center (FastAPI OLED single-page dashboard):
molab web
# Or:
molab dashboard --port 8080

# 2. Launch full-screen interactive Terminal Control Center:
molab ui
# Or simply:
molab
```

### Recipe I: Public Model Sharing & Global External Client Access
Expose any deployed model on MoLab to the public internet via Cloudflare Quick Tunnels:
```bash
# 1. Generate live public HTTPS URL & client credentials:
molab share
# (or: molab public)

# 2. Inspect active public credentials:
molab share --status

# 3. Terminate public tunnel daemon:
molab share --stop
```
Provides standard OpenAI compatible endpoints (`https://<subdomain>.trycloudflare.com/v1`) and Anthropic Claude Code endpoints with zero configuration required on external clients (Cursor, Cline, LangChain, Python).

### Recipe J: 1-Click Autonomous Model Deployment (v2.4)
Deploy industry-grade models to Blackwell with zero configuration:
```bash
# 1. Deploy 32B coding workhorse (Qwen 2.5 Coder 32B) with Automatic Prefix Caching:
molab deploy coder-32b

# 2. Deploy 70B FP8 heavyweight or DeepSeek-R1 reasoning:
molab deploy llama-70b
molab deploy r1-32b

# 3. Check live Prefix Caching Hit Rate and GPU telemetry:
molab perf
```
This automatically configures FlashInfer attention, chunked prefill, 16-token radix tree caching, background process supervision, port forwarding, and prints instant client credentials for Cursor, Claude Code, and Python.

---

## 7. Studio-Grade Documentation Reference Index

All systems are documented in dedicated, separated guides in [`~/molab-cli/docs/`](file:///data/data/com.termux/files/home/molab-cli/docs/):
* [`USER_GUIDE.md`](file:///data/data/com.termux/files/home/molab-cli/docs/USER_GUIDE.md): End-to-end developer workflows, zero-friction pod targeting, and production recipes.
* [`UI_AND_UX.md`](file:///data/data/com.termux/files/home/molab-cli/docs/UI_AND_UX.md): OLED Slate design tokens, status/hardware badges, layout composition, and non-interactive pipe safety.
* [`STATE_MANAGEMENT.md`](file:///data/data/com.termux/files/home/molab-cli/docs/STATE_MANAGEMENT.md): 5-tier state architecture, transactional SQLite WAL durability, and caching contracts.
* [`ERROR_HANDLING.md`](file:///data/data/com.termux/files/home/molab-cli/docs/ERROR_HANDLING.md): Typed `MoLabError` hierarchy, dynamic exception classification, and zero-leak secret redaction.
* [`TESTING.md`](file:///data/data/com.termux/files/home/molab-cli/docs/TESTING.md): Deterministic mocking architecture, test matrix, and continuous verification guidelines.
* [`DECISIONS.md`](file:///data/data/com.termux/files/home/molab-cli/docs/DECISIONS.md): Architectural Decision Records (ADR-001 through ADR-008).
* [`SESSION_HANDOFF.md`](file:///data/data/com.termux/files/home/molab-cli/docs/SESSION_HANDOFF.md): Protocol for autonomous session handoffs, active state markers, and verification checklists.
* [`QUICKSTART_NON_TECH.md`](file:///data/data/com.termux/files/home/molab-cli/docs/QUICKSTART_NON_TECH.md): 60-second 1-click model deployment guide for non-technical users.
* [`MODEL_CATALOG_CONFIGS.md`](file:///data/data/com.termux/files/home/molab-cli/docs/MODEL_CATALOG_CONFIGS.md): Category-wise Blackwell 96GB hyperparameters and model profiles.
* [`INFERENCE_ENGINE_OPTIMIZATION.md`](file:///data/data/com.termux/files/home/molab-cli/docs/INFERENCE_ENGINE_OPTIMIZATION.md): Kernel dispatch, FlashInfer, Prefix Caching (APC), Chunked Prefill, and memory geometry.
* [`TUNNELING_AND_INGRESS.md`](file:///data/data/com.termux/files/home/molab-cli/docs/TUNNELING_AND_INGRESS.md): Cloudflare Quick & Named Tunnels, SSE keep-alive bypass, CORS, and client setups.
* [`GATEWAY_AND_SECURITY.md`](file:///data/data/com.termux/files/home/molab-cli/docs/GATEWAY_AND_SECURITY.md): Virtual key governance (`sk-molab-...`), SQLite WAL rate limiting, and Prometheus metrics.
* [`API_REFERENCE.md`](file:///data/data/com.termux/files/home/molab-cli/docs/API_REFERENCE.md): Comprehensive reference for all Python SDK classes.
* [`MCP_GUIDE.md`](file:///data/data/com.termux/files/home/molab-cli/docs/MCP_GUIDE.md): 48-tool Model Context Protocol guide with JSON schemas and agent tool flows.
* [`PERMANENCE_GUIDE.md`](file:///data/data/com.termux/files/home/molab-cli/docs/PERMANENCE_GUIDE.md): Complete architecture of the 100% on-MoLab vault and anti-idle supervisor.
* [`BATCH_ORCHESTRATION.md`](file:///data/data/com.termux/files/home/molab-cli/docs/BATCH_ORCHESTRATION.md): Multi-pod DAG pipeline execution, worker pools, and task retry semantics.
* [`WORKLOAD_COOKBOOK.md`](file:///data/data/com.termux/files/home/molab-cli/docs/WORKLOAD_COOKBOOK.md): Battle-tested production recipes.
* [`CLI_REFERENCE.md`](file:///data/data/com.termux/files/home/molab-cli/docs/CLI_REFERENCE.md): Complete command-line manual covering all 16 command groups and subcommands.
* [`ARCHITECTURE.md`](file:///data/data/com.termux/files/home/molab-cli/docs/ARCHITECTURE.md): Technical architecture, subsystems, and protocol flowcharts.

---

## 8. Troubleshooting & FAQ

* **Issue: `molab exec` timed out after 30s**
  * *Cause:* A long-running job was run synchronously in the foreground.
  * *Fix:* Use `nohup <cmd> > <log> 2>&1 &` and check output with `tail -n 20 <log>`.
* **Issue: HTTP 403 or Unauthorized on file transfer**
  * *Cause:* Sandbox pod restarted and received a new auth token.
  * *Fix:* Call `session.resolve(force_refresh=True)` or rerun the command to re-fetch the latest sandbox token.
* **Issue: Large context window (>4KB) fails on `molab forward`**
  * *Cause:* PTY buffer overflow on old CLI versions.
  * *Fix:* The upgraded `LocalHttpForwarder` automatically routes bodies >3000 bytes through temporary HTTP file streaming.
* **Issue: Browser web apps (e.g. Base44, Open WebUI) fail with CORS preflight errors**
  * *Cause:* Missing `OPTIONS` handler or missing CORS headers on error responses.
  * *Fix:* The production AI Gateway includes `universal_cors_middleware` and route-level `@app.options("/{rest_of_path:path}")` returning `204 No Content` with `Access-Control-Max-Age: 86400` and `x-api-key`/`anthropic-version` headers.

---

## 9. Hardened Production API Service & Universal Browser CORS Standard

When serving self-hosted LLMs from the Blackwell cluster to web applications, browser clients, or external agent tools:

### Universal CORS & Preflight Contract
1. **Preflight Invariance:** Preflight `OPTIONS` requests on `/v1/chat/completions`, `/v1/messages`, and `/v1/models` **never** require authentication and unconditionally return `HTTP 204 No Content`.
2. **Four Mandatory Headers:** Every response must include:
   * `Access-Control-Allow-Origin: <Origin|*>`
   * `Access-Control-Allow-Methods: GET, POST, OPTIONS, PUT, DELETE, PATCH`
   * `Access-Control-Allow-Headers: Authorization, Content-Type, x-api-key, anthropic-version, anthropic-beta, User-Agent, Accept, Cache-Control, X-Requested-With`
   * `Access-Control-Max-Age: 86400`
3. **Error CORS Preservation:** Error responses (401, 404, 429, 500) must carry the exact same CORS headers so browsers can parse JSON error objects rather than failing with generic network errors.

### Dual API & Model Alias Mapping
* **OpenAI Client:** `POST /v1/chat/completions` (SSE streaming chunks with `data: {...}` and `data: [DONE]`).
* **Anthropic Client:** `POST /v1/messages` (SSE streaming events `message_start`, `content_block_delta`, `message_delta`, `message_stop`).
* **Model Aliasing:** Automatically maps aliases (`gpt-4o`, `claude-3-7-sonnet-20250219`, `qwen-32b`, `default`) to the active Blackwell model. Returns `HTTP 404` for unrecognized/garbage model names.

### Ingress & Custom Domains
* **Quick Tunnel (Ephemeral):**
  ```bash
  molab share
  ```
* **Named Tunnel (Permanent Custom Domain):**
  ```bash
  molab share --token <CLOUDFLARE_TUNNEL_TOKEN> --hostname ai.mydomain.com
  ```
