# MoLab CLI Technical Architecture

## 1. System Overview

`molab-cli` (accessible as both `molab` and `molabctl`) provides a unified command-line and programmatic interface to ephemeral **CoreWeave GPU pods** running the **Marimo reactive notebook engine**.

```
+-------------------------------------------------------------------------------+
| Termux / Android Client                                                       |
|                                                                               |
|   ~/.config/molab/config.json  <-- Clerk Auth Cookie & Session                |
|                                                                               |
|   molab_cli/                                                                  |
|   ├── auth.py     : JWT decode, session inspection, user metadata             |
|   ├── client.py   : MoLab Web API (GraphQL / REST) for notebook lifecycle     |
|   ├── sandbox.py  : CoreWeave Pod connection (HTTP REST + WS PTY + Forwarder) |
|   ├── cli.py      : Click commands, Rich formatting, JSON outputs             |
|   └── theme.py    : UI styles, banners, tables, error cards                   |
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

## 3. High-Speed File Transfer Engine

Traditional terminal base64 piping fails over WebSocket PTYs due to the Linux 4096-byte input buffer limitation. 

`molab push` and `molab pull` bypass the PTY entirely by communicating directly with the Marimo HTTP/2 REST endpoints:
* **Upload:** `POST /api/files/create?token=<token>` (multipart form streaming).
* **Download:** `GET /api/files/download?path=<path>&token=<token>` (streamed directly to disk).
* **Directory Support:** Directories are automatically packaged into temporary compressed tarballs (`.tar.gz`), streamed over the HTTP API, and unpacked at the destination.
