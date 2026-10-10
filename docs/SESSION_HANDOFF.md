# MoLab CLI — Authoritative Session Handoff Document

*Document Version:* 2.4.0  
*Timestamp:* 2026-10-10  
*Repository Root:* `/data/data/com.termux/files/home/molab-cli`  
*Target Hardware:* NVIDIA RTX PRO 6000 Blackwell Server Edition (96GB GDDR7 VRAM, sm_120) on CoreWeave  

---

## 1. Current Project Status

The MoLab CLI (`molab` / `molabctl`) has completed an end-to-end UX, architectural, state-management, reliability, and documentation overhaul.
* **Working Tree:** Clean, production-ready.
* **Test Suite:** **178 passed / 178 tests (100% pass rate)** in `pytest`.
* **CLI Surface:** 56 commands and command groups across pod lifecycle, 1-click model deployments, live telemetry profiling, terminal coding agents, public ingress, in-notebook vault persistence, and batch DAG orchestration.
* **Zero-Friction Target Resolution:** Pod ID is optional across all commands; the CLI automatically resolves active pods, prompts in interactive sessions, and remembers the session pod.
* **Interactive TUI:** Fully synchronized with all modern v2.4 capabilities (1-click catalog deployment, telemetry, virtual keys, public sharing).

---

## 2. Core Architecture & Primary Entry Points

* **CLI Entry Point:** `src/molab_cli/cli.py` (`molab` and `molabctl` scripts defined in `pyproject.toml`).
* **Terminal UI (TUI):** `src/molab_cli/tui.py` (`molab` or `molab ui`).
* **Web UI Dashboard:** `src/molab_cli/web.py` (`molab web` or `molab dashboard`).
* **MCP Stdio Server:** `src/molab_cli/mcp.py` (`molab mcp`).
* **Python SDK:** `src/molab_cli/sdk.py` (`MoLabSDK`, `Pod`, `MoLabVault`).
* **AI Model Engine:** `src/molab_cli/deploy.py` (Blackwell catalog deployments) + `bridge.py` + `chat.py`.
* **State & DBs:** `src/molab_cli/config.py`, `gateway_db.py` (`gateway.db`), `jobs.py` (`jobs.db`).
* **Design & Errors:** `src/molab_cli/theme.py`, `src/molab_cli/exceptions.py`.

---

## 3. Completed Overhaul Work

1. **Foundational State & Config Centralization (`config.py`):**
   - Implemented `get_config_dir()`, `get_db_path()`, `get_active_notebook_id()`, `set_active_notebook_id()`, `clear_active_notebook_id()`.
   - Eliminates hardcoded scattered paths across modules.

2. **Intelligent Target Notebook Auto-Resolution (`sandbox.py`):**
   - Implemented `resolve_target_notebook()`.
   - Commands no longer require tedious copy-pasting of 24-character IDs. Single running pods are automatically targeted; multi-pod sessions prompt with arrow keys.

3. **Unified Error Classification & Secret Redaction (`exceptions.py` & `theme.py`):**
   - Implemented `classify_exception()` mapping lower-level errors into structured `MoLabError` with remediation hints.
   - Implemented `redact_sensitive_text()` sanitizing cookies, JWT tokens, and API keys.
   - Implemented `handle_cli_error()` supporting dual presentation: Rich cards for TTY and clean JSON for `-j, --json` pipes.

4. **CLI Ergonomics & Pipe Safety (`cli.py`):**
   - Upgraded `compute`, `inspect`, `exec`, `shell`, `cat`, `gpu`, `forward`, `stop`, `push`, `pull` to support optional pod IDs and unified error handling.
   - Smart argument parsing for `molab exec "<cmd>"` and `molab push <file> [path]`.

5. **Interactive Control Center Overhaul (`tui.py`):**
   - Replaced outdated Gemma references with modern 1-Click Blackwell deployments (`qwen-32b`, `coder-32b`, `r1-32b`, `llama-70b`).
   - Integrated live inference telemetry profiler (`molab perf`), Cloudflare tunnel sharing (`molab share`), virtual API key management (`molab keys`), and gateway analytics (`molab stats`).
   - Dynamic versioning (`__version__`).

6. **Comprehensive Test Suite Expansion (`tests/test_ui_ux_overhaul.py`):**
   - 11 new tests verifying secret redaction, exception classification, active pod state, auto-resolution, and JSON error handling. Total suite increased from 167 to 178 passing tests.

7. **Dedicated Documentation Suite (`docs/`):**
   - `docs/UI_AND_UX.md`: Design tokens, card primitives, page structure, pipe safety.
   - `docs/STATE_MANAGEMENT.md`: 5 state tiers, single source of truth, WAL mode.
   - `docs/ERROR_HANDLING.md`: Error hierarchy, classification, secret redaction, JSON mode.
   - `docs/TESTING.md`: Test execution, suite architecture, mocking guidelines.
   - `docs/DECISIONS.md`: ADR-001 through ADR-008.
   - `docs/USER_GUIDE.md`: Step-by-step developer workflows from login to deployment.
   - `docs/SESSION_HANDOFF.md`: This authoritative handoff document.

---

## 4. Key Architectural Decisions (Summary of ADRs)

* **ADR-001:** Rich + Questionary selected for TUI (lightweight, <150ms startup, robust on Termux).
* **ADR-002:** Native Marimo HTTP/2 REST streaming used for file transfers (bypasses Linux PTY 4KB buffer).
* **ADR-003:** In-Notebook Vault for persistence (stores workspace inside notebook AST cell on MoLab cloud database; zero phone storage).
* **ADR-004:** Dual-loop keepalive supervisor defeats 30-minute idle reaper.
* **ADR-005:** Local Cloudflare Quick Tunnels for ingress (bypasses CoreWeave gVisor port 7844 block).
* **ADR-006:** Prefix-Cache Stability Contract: system prompt and tool schemas byte-frozen; dynamic environment context appended strictly at the tail of user turns.
* **ADR-007:** SQLite Write-Ahead Logging (`WAL`) mode across all local databases.
* **ADR-008:** Intelligent Target Auto-Resolution for all CLI commands.

---

## 5. Verification Results

* **Full Test Run:** `pytest tests/` ➔ **178 passed, 1 warning in 9.23s**.
* **Zero Regressions:** All pre-existing 167 tests maintained and passing.
* **Redaction Verified:** Zero credentials, cookies, or JWTs leaked in error outputs or test transcripts.

---

## 6. Known Limitations & Environmental Constraints

1. **gVisor Port 7844 Outbound Block:** Named Cloudflare tunnels cannot be established from *inside* the CoreWeave container pod due to firewall egress rules. Tunnels must always be launched from the client via `molab share`.
2. **Foreground Execution Timeout:** `molab exec` has a default 30-second timeout. Long-running workloads must be launched via `molab job submit` or `nohup`.
3. **PTY Terminal Buffer:** PTY input buffer is limited to 4096 bytes. File uploads must never use base64 over PTY (`molab exec`); always use `molab push`.

---

## 7. Recommended Next Actions for Future Sessions

1. **External Web Dashboard Enhancements:** `src/molab_cli/web.py` can be updated with real-time SSE graphs matching the `molab perf` terminal dashboard.
2. **Package Version Bump:** When publishing to PyPI, update `__version__ = "2.4.1"` in `src/molab_cli/__init__.py` and `pyproject.toml`.

---

## 8. Commands to Resume Development

```bash
# Verify environment and tests:
cd ~/molab-cli
pytest tests/

# Test interactive TUI:
molab ui

# Test 1-click model deployment:
molab deploy coder-32b

# Check live inference telemetry:
molab perf

# Review git status:
git status
```
