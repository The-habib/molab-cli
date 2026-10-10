# MoLab CLI — Architecture Decision Records (ADRs)

This document records the foundational design decisions governing the MoLab CLI architecture to ensure consistency across development sessions.

---

### ADR-001: Terminal UI Framework Selection (Rich + Questionary)
* **Status:** Accepted
* **Context:** The CLI must run smoothly in Termux on Android phones as well as standard desktop Linux/macOS terminals.
* **Decision:** Use **Rich** for styled rendering, tables, and panels, and **Questionary (Prompt Toolkit)** for interactive keyboard menus.
* **Rationale:** Full-screen TUI frameworks like Textual require heavy dependencies and suffer from terminal rendering lag and keyboard handling quirks in mobile Termux. Rich + Questionary provides instant startup (<150ms), native arrow-key navigation, and clean pipe-redirection support.

---

### ADR-002: Native HTTP Streaming over WebSocket PTY for File Transfers
* **Status:** Accepted
* **Context:** Uploading files via `echo '<base64>' | base64 -d` over `molab exec` caused buffer overflows and frozen terminals on files >4KB.
* **Decision:** All file transfers (`push`, `pull`, `sync`) use the Marimo HTTP/2 REST endpoints (`/api/files/create` and `/api/files/download`).
* **Rationale:** Linux terminal PTYs enforce a hard `MAX_INPUT = 4096` byte limit. Native HTTP streaming bypasses the PTY buffer completely, enabling multi-gigabyte transfers at wire speed without memory pressure on the client.

---

### ADR-003: 100% On-MoLab In-Notebook Vault Persistence
* **Status:** Accepted
* **Context:** Pod sandboxes reset on idle timeout, wiping `/workspace`. Downloading 20GB checkpoints to local phone storage is infeasible on Termux flash storage.
* **Decision:** Embed `/workspace` state directly into `/marimo/notebook.py` as a compressed, self-extracting base64 cell saved to MoLab's cloud database.
* **Rationale:** Zero local phone storage used, zero 3rd-party S3/R2 cloud storage fees, 100% automatic extraction upon container recreation.

---

### ADR-004: Dual-Loop Keepalive Supervisor
* **Status:** Accepted
* **Context:** CoreWeave sandbox pods enforce a 30-minute idle session timeout.
* **Decision:** Implement a background supervisor daemon executing a dual keepalive loop: an active in-pod Python loop plus a local HTTP heartbeat every 120s.
* **Rationale:** Defeats the idle reaper with minimal resource usage and automatically resurrects the pod if a maintenance reset occurs.

---

### ADR-005: Cloudflare Quick Tunnels for Ingress
* **Status:** Accepted
* **Context:** CoreWeave gVisor containers block outbound QUIC/TCP traffic to port 7844, preventing in-pod Cloudflare named tunnels.
* **Decision:** Run Cloudflare Quick Tunnels (`cloudflared`) on the local client machine (Termux), pointing to local port 8000 bridged from the pod.
* **Rationale:** Local outbound traffic is completely unblocked. It provides free public HTTPS domains (`*.trycloudflare.com`) with zero port forwarding configuration required.

---

### ADR-006: The Prefix-Cache Stability Contract
* **Status:** Accepted
* **Context:** vLLM Automatic Prefix Caching (APC) uses a 16-token radix tree hash. Injecting dynamic variables (timestamps, git diffs) into system prompts invalidated the cache, spiking TTFT to 2–5s.
* **Decision:** Byte-freeze system prompts and tool definitions across turns. Inject dynamic environment context strictly at the user turn's tail boundary.
* **Rationale:** Achieves 90%+ prefix cache hit rates, sub-80ms TTFT, and eliminates prefill recomputation.

---

### ADR-007: SQLite WAL Mode for Multi-Tenant Gateway Keys & Jobs
* **Status:** Accepted
* **Context:** Background jobs, API key rate limiters, forwarders, and CLI commands access local databases concurrently.
* **Decision:** Configure SQLite with Write-Ahead Logging (`PRAGMA journal_mode=WAL;`).
* **Rationale:** Eliminates SQLite locking errors (`database is locked`) and provides lock-free concurrent reads and writes across multiple processes.

---

### ADR-008: Intelligent Target Notebook Auto-Resolution
* **Status:** Accepted
* **Context:** Requiring users to memorize and paste 24-character notebook IDs (`nb_emuq...`) for every command (`gpu`, `shell`, `exec`, `forward`) created severe UX friction.
* **Decision:** Make `notebook_id` optional across all commands and resolve via `resolve_target_notebook()`: auto-select single active pods, prompt interactively if multiple, and retain the active pod in session config.
* **Rationale:** Makes the CLI effortless for interactive human operators while preserving 100% scriptability and backward compatibility.
