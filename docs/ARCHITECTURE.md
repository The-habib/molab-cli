# MoLab CLI Technical Architecture

## 1. System Overview

`molab-cli` (accessible as both `molab` and `molabctl`) provides a unified command-line and programmatic interface to ephemeral **CoreWeave GPU pods** running the **Marimo reactive notebook engine**.

```
+-------------------------------------------------------------------------------+
| Termux / Android Client                                                       |
|                                                                               |
|   ~/.config/molab/config.json  <-- Clerk Auth Cookie & Session                |
|   ~/.config/molab/jobs.db      <-- Persistent SQLite Job & Artifact Store     |
|                                                                               |
|   molab_cli/                                                                  |
|   ├── auth.py         : JWT decode, session inspection, user metadata         |
|   ├── capabilities.py : Capabilities detection & diagnostic Doctor checks     |
|   ├── client.py       : MoLab Web API (GraphQL / REST) for notebook lifecycle |
|   ├── execution.py    : Structured execution, exit code traps, nohup launcher |
|   ├── jobs.py         : SQLite background job queue, lifecycle & artifacts    |
|   ├── mcp.py          : JSON-RPC 2.0 Model Context Protocol Server (stdio)    |
|   ├── sandbox.py      : CoreWeave Pod connection & interactive terminal       |
|   ├── sdk.py          : High-level typed Python SDK for automated scripts     |
|   ├── services.py     : Model server lifecycle & application health checks    |
|   ├── transfer.py     : Native HTTP streaming, SHA-256 manifests & sync      |
|   ├── workloads.py    : Pluggable AI workload templates & parameter schemas   |
|   ├── cli.py          : Click commands, Rich formatting, JSON outputs         |
|   └── theme.py        : UI styles, banners, tables, error cards               |
+-------------------------------------------------------------------------------+
         │                                                      │
         │ [HTTPS REST Gateway]                                 │ [WebSocket Gateway]
         │ https://<sb-id>.sb.molab.run                         │ wss://<sb-id>.sb.molab.run/terminal/ws
         │                                                      │
         v                                                      v
+-------------------------------------------------------------------------------+
| Remote Kubernetes Sandbox Pod (Ubuntu 24.04 LTS, root)                        |
|                                                                               |
|   Port 8080: Marimo Server (PID 1)                                            |
|     ├── /api/files/create   : Streaming multipart/form-data upload            |
|     ├── /api/files/download : Binary streaming file download                  |
|     ├── /api/status         : Kernel health & version metadata                |
|     └── /terminal/ws        : Interactive PTY root bash shell                 |
|                                                                               |
|   Port 8000: User Model Server (Optional)                                     |
|     └── /v1/chat/completions (OpenAI compatible inference API)                |
|                                                                               |
|   Hardware:                                                                   |
|     NVIDIA RTX PRO 6000 Blackwell Server Edition (96 GB GDDR7, sm_120)        |
|     CUDA 13.2 / PyTorch 2.11 / FFmpeg 7.1 with Hardware Codecs                |
+-------------------------------------------------------------------------------+
```

---

## 2. Authentication Protocol

MoLab uses a hybrid authentication scheme:
1. **Web Session:** Stored in `~/.config/molab/config.json`:
   - `client_cookie`: Clerk JWT containing `client_id` and rotating tokens.
   - `session_id`: Active Clerk session identifier.
   - `org_id`: Target organization workspace identifier.
2. **Dynamic Sandbox Tokens:**
   Each sandbox container pod generates an ephemeral `auth_token` valid for the lifetime of that container.
   `SandboxSession.resolve()` automatically unpacks the base64-encoded session descriptor from the notebook webpage, extracting:
   - `sandbox_id` (e.g. `sb-d6e12d0f6e7c769a`)
   - `auth_token` (e.g. `fb2b9b0c29b80c0c26b2c0221763d837d038...`)
   - `expires_at` (epoch timestamp)

---

## 3. High-Speed File Transfer & Manifest Sync

Traditional terminal base64 piping fails over WebSocket PTYs due to the Linux 4096-byte input buffer limitation. 

`molab push`, `molab pull`, and `molab sync` bypass the PTY entirely by communicating directly with the Marimo HTTP/2 REST endpoints:
* **Upload:** `POST /api/files/create?token=<token>` (multipart form streaming).
* **Download:** `GET /api/files/download?path=<path>&token=<token>` (streamed directly to disk).
* **Directory Support:** Directories are automatically packaged into temporary compressed tarballs (`.tar.gz`), streamed over the HTTP API, and unpacked at the destination.
* **Integrity & Delta Sync:** Files are hashed using chunked streaming SHA-256. `molab sync` generates local and remote manifests, diffs them, and only transfers modified files.

---

## 4. Universal Job & Artifact Subsystem

Long-running compute jobs (4K video rendering, LLM fine-tuning, batch inference) require asynchronous tracking:
1. **Controller State Persistence:** Stored locally in `~/.config/molab/jobs.db` using SQLite with transactional state transitions:
   `PENDING → RUNNING → COMPLETED / FAILED / CANCELLED / LOST`.
2. **Remote Process Isolation:** Jobs run detached via `nohup` on the pod with stdout and stderr piped to a dedicated run log (`/workspace/jobs/<job_id>/run.log`) and exit code trapped into an exit file (`exit_code`).
3. **Artifact Discovery:** When a job completes, any files generated in `/workspace/jobs/<job_id>/artifacts/` are automatically cataloged into the SQLite database with file sizes and SHA-256 hashes, ready for download via `molab job artifacts <id> --download <dir>`.
4. **Failure & Disconnect Tolerance:** If a pod expires or reboots while a job was marked `RUNNING`, the status check detects the unreachable sandbox and updates status to `LOST` with a diagnostic explanation.

---

## 5. Model Context Protocol (MCP) Integration

`molab mcp` implements the official JSON-RPC 2.0 stdio MCP specification (2024-11-05). It exposes 19 typed tools across:
- **Discovery:** `molab_doctor`, `molab_capabilities`, `molab_list_pods`, `molab_get_free_pod`, `molab_gpu_telemetry`.
- **Execution & Storage:** `molab_execute`, `molab_push_file`, `molab_pull_file`, `molab_sync_directory`.
- **Jobs & Workloads:** `molab_job_submit`, `molab_job_status`, `molab_job_logs`, `molab_job_cancel`, `molab_run_workload`, `molab_service_status`.
- **Batch Orchestration:** `molab_batch_validate`, `molab_batch_submit`, `molab_batch_status`, `molab_batch_cancel`.

This allows Claude, Cursor, Antigravity, and any MCP-compliant agent to orchestrate 96GB Blackwell GPU pods natively without custom shell scripting.

---

## 6. Multi-Pod Autonomous Orchestration Engine (v2.2)

The Multi-Pod Orchestration Engine (`molab_cli/scheduler.py`, `molab_cli/notifications.py`) elevates `molab-cli` from single-job tracking to multi-pod pipeline coordination:

```
                      +-----------------------------+
                      | Batch Manifest (batch.json) |
                      +-----------------------------+
                                     │
                                     ▼
                      +-----------------------------+
                      |   DAG & Cycle Validation    |
                      |   (Kahn's Topological Sort) |
                      +-----------------------------+
                                     │
                 ┌───────────────────┴───────────────────┐
                 ▼                                       ▼
      Stage 1 (Parallel Tasks)                Stage 2 (Dependent Tasks)
                 │                                       │
                 ▼                                       ▼
    +─────────────────────────+             +─────────────────────────+
    | Resource-Aware Matcher  |             | Resource-Aware Matcher  |
    | (CUDA, Headroom, Load)  |             | (CUDA, Headroom, Load)  |
    +─────────────────────────+             +─────────────────────────+
                 │                                       │
                 ├───────────────────┬───────────────────┤
                 ▼                   ▼                   ▼
          [Pod A: 96GB GPU]   [Pod B: 96GB GPU]   [Pod C: 96GB GPU]
```

### 6.1 Explainable Resource-Aware Scheduling
- **Zero Hardcoded Pod IDs or GPU Model Names:** Dynamically queries live telemetry across running sandboxes via `torch.cuda.mem_get_info()` and process audits.
- **Header Selection Audit:** Every candidate pod evaluation produces an explainable reason trace:
  - Acceptance: `Free VRAM satisfies requirement (94.4 GB free >= 16.0 GB requested)` + idle priority bonus.
  - Rejection: Insufficient VRAM, active server occupancy, offline state, or missing CUDA.
- **Priority Scoring:** Ranks eligible pods by free memory headroom and least concurrent allocations.

### 6.2 Transactional Batch State Machine
Stored in SQLite tables `batches` and `batch_tasks`:
- Task lifecycle: `PENDING → READY → RUNNING → COMPLETED / FAILED / SKIPPED / CANCELLED`.
- Dynamic DAG Propagation: When prerequisites succeed, dependent tasks transition to `READY`. If a dependency fails and exhausts its `max_attempts`, downstream tasks are safely marked `SKIPPED`.
- Lease and Concurrency Control: Concurrency caps (`--max-parallel`) are strictly enforced at runtime.

### 6.3 Isolated Webhook Notifications
- **Multi-Platform Support:** Automatically formats JSON payloads for generic HTTPS webhooks, Discord embeds (with color status coding), and Telegram bot endpoints.
- **Sensitive Credential Redaction:** Recursively sanitizes payloads, replacing auth cookies (`__client=`), Bearer tokens, and secrets with `[REDACTED]`.
- **Fault Isolation:** Webhook connection timeouts and HTTP errors never disrupt running compute pipelines. All outbound notification attempts are audited in the `notifications` table.


