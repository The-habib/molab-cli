# MoLab CLI — State Management & Reliability Specification

## 1. State Taxonomy

MoLab CLI organizes state into five distinct tiers with well-defined lifecycles and authoritative sources of truth:

```
+-------------------------------------------------------------------------------+
| Tier 1: Application State (In-Memory, Interactive Loop)                       |
|   • Active Screen / Menu View (tui.py)                                        |
|   • Current Terminal Input Buffer & History (prompt_toolkit)                  |
|   • Active Progress Spinners & Status Lines (rich.console)                    |
+-------------------------------------------------------------------------------+
                                      │
+-------------------------------------------------------------------------------+
| Tier 2: Session State (Transient Local & JWT)                                 |
|   • Clerk Master Client Cookie (__client)                                     |
|   • RS256 Session JWT (minted via Clerk Frontend API, rotates every 60s)       |
|   • Selected Workspace Organization (org_id)                                  |
|   • Active Target Notebook Memory (active_notebook_id)                        |
+-------------------------------------------------------------------------------+
                                      │
+-------------------------------------------------------------------------------+
| Tier 3: Persistent Local State (Filesystem & SQLite WAL)                      |
|   • ~/.config/molab/config.json    : Master auth, user profile, active org    |
|   • ~/.config/molab/jobs.db        : Detached background jobs, logs & hashes   |
|   • ~/.config/molab/gateway.db     : Virtual API keys, RPM/TPM rate limits    |
|   • ~/.config/molab/keepalive_*.pid: Supervisor daemon process tracking       |
|   • ~/.config/molab/tunnel.pid     : Cloudflare tunnel ingress process        |
+-------------------------------------------------------------------------------+
                                      │
+-------------------------------------------------------------------------------+
| Tier 4: Remote Pod State (CoreWeave Kubernetes Sandbox)                       |
|   • Pod Lifecycle: PROVISIONING ➔ RUNNING ➔ TERMINATING ➔ OFFLINE             |
|   • Ephemeral Container Sandbox ID (sb-xxxxxxxx)                              |
|   • Container Auth Token (auth_token in page AST, changes on pod restart)     |
|   • GPU VRAM Allocation (nvidia-smi via gVisor sandbox)                       |
|   • Model Server Process (vLLM on port 8000, PID tracking)                    |
+-------------------------------------------------------------------------------+
                                      │
+-------------------------------------------------------------------------------+
| Tier 5: Transient Operation State (Runtime IPC & Daemons)                     |
|   • Port Forwarder Threads (LocalHttpForwarder on port 8000 / 8082)           |
|   • Cloudflare Quick Tunnel Child Process (cloudflared)                       |
|   • Real-Time Token Generation Streams (SSE keep-alive loop)                  |
+-------------------------------------------------------------------------------+
```

---

## 2. Source of Truth & Ownership

| State Entity | Authoritative Owner | Storage Location | Invalidation Trigger |
| :--- | :--- | :--- | :--- |
| **Auth Credentials** | `molab_cli.auth` | `~/.config/molab/config.json` | `molab login`, 401/403 API response |
| **Active Target Pod** | `molab_cli.config` | `~/.config/molab/config.json` | Explicit target change or pod stop |
| **Background Jobs** | `molab_cli.jobs` | `~/.config/molab/jobs.db` | Job status sync, completion, deletion |
| **Virtual API Keys** | `molab_cli.gateway_db` | `~/.config/molab/gateway.db` | Key creation, revocation (`molab keys`) |
| **Rate Limiter Windows**| `molab_cli.rate_limiter`| In-Memory Sliding Window | Timestamp expiration (60s window) |
| **Remote Pod Credentials**| `molab_cli.sandbox` | In-Memory `SandboxSession` | Pod container restart, HTTP 403 |
| **Tunnel Status** | `molab_cli.tunnel` | `~/.config/molab/tunnel.pid` | Process exit, `molab share --stop` |

---

## 3. Dynamic Sandbox Resolution & Pod Reboots

CoreWeave containers are ephemeral. When a pod reboots or idle-times out:
1. The container receives a new `sandbox_id` and new `auth_token`.
2. Hardcoded endpoints will immediately fail with HTTP 403 or connection errors.
3. **Resolution Lifecycle:**
   * When any operation fails with 401/403, `SandboxSession.resolve(force_refresh=True)` re-fetches the live notebook page from `molab.marimo.io`.
   * It extracts the fresh base64 descriptor, decodes `sandbox_id` and `auth_token`, and transparently retries the request.

---

## 4. SQLite WAL Mode for Multi-Process Reliability

Both `jobs.db` and `gateway.db` operate with **Write-Ahead Logging (WAL)**:
```sql
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;
PRAGMA synchronous=NORMAL;
```
This guarantees that concurrent processes (e.g. `molab chat` querying virtual keys while `LocalHttpForwarder` logs request metrics, and `molab stats` reads telemetry) never experience database locks or corruption.
