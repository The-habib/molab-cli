# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.5.0] - 2026-10-12

### Added
- **Modular Command Architecture**: Decomposed monolithic 3,348-line `cli.py` into 6 high-cohesion domain modules under `src/molab_cli/commands/`: `compute.py`, `gateway.py`, `files.py`, `jobs.py`, `agent.py`, and `system.py` with 100% backward compatibility.
- **Direct HTTP Reverse Proxying & SSE Streaming**: Engineered `_async_stream_via_http` in `sandbox.py` using `httpx.AsyncClient` for line-rate SSE token streaming, bypassing bash PTY terminal scraping, VT100 ANSI sequences, and tempfile uploads.
- **In-Memory Multipart Upload Engine**: Replaced disk-based `/tmp` JSON uploads with in-memory HTTP multipart streaming uploads (`_upload_temp_json_payload`).
- **Resilience Engine & Exponential Backoff**: Implemented `retry.py` featuring Full Jitter backoff algorithms, transient HTTP status filters (429, 502, 503, 504), and seamless integration into `MoLabClient`.
- **Cross-Platform Compatibility Layer**: Implemented `compat.py` supporting Windows 11, Linux, macOS, and Termux on Android with platform-aware process discovery (`psutil` / CIM / `pgrep`) and graceful termination.
- **Enterprise SQLite WAL Session Management**: Implemented `db.py` context manager with `PRAGMA journal_mode=WAL;` and 15-second busy timeout to eliminate database locking across concurrent operations.
- **Structured Diagnostic Observability**: Integrated `logger.py` with `--debug` flag and `MOLAB_DEBUG=1` trace observability across networking, gateway, and sandbox daemons.
- **Smart Pod Auto-Resolution**: Enhanced 14 commands (`usage`, `cat`, `download`, `file`, `pkg`, `kernel`, `keys`, `vault`, `snapshot`, `job`, `keepalive`, `serve`) to automatically discover and target the active Blackwell pod when `notebook_id` is omitted.
- **Smart File Path Delegation**: Enhanced `molab cat` to automatically detect remote file paths (e.g. `molab cat /workspace/file.txt`) and delegate directly to `molab file cat`.

### Fixed
- **Windows Process Crash**: Replaced hardcoded POSIX `pkill` calls in `cmd_bridge_stop` and `cmd_share` with cross-platform Win32 PowerShell CIM / `taskkill` routines, resolving `FileNotFoundError: [WinError 2]`.
- **Model Server Probe Fast-Fail**: Fixed bash shell syntax error (`& ||`) in `ensure_model_server_running` and added upfront file checks, reducing offline model probe latency from 60+ seconds to 14 seconds.
- **Vault Exclusions**: Configured smart directory and extension filters in `vault.py` to prevent multi-gigabyte `.venv`, `node_modules`, and binary model caches from blowing past the 25MB notebook cell limit.
- **Virtual API Key Governance**: Added `--name` option support to `molab keys create` and added `molab keys delete` alias for `revoke`.

### Tested
- **Exhaustive Test Suite**: 210 unit and integration tests passing in 21.23s (100% pass rate).

## [1.0.0] - 2026-10-07

### Added
- **Interactive TUI Control Center**: Zero-typing dashboard accessible via `molab` or `molab ui`.
- **First-time Onboarding Wizard**: Automated 30-second guided setup with Clerk auto-discovery.
- **NVIDIA Blackwell GPU Support**: 1-click provisioning and switching to **NVIDIA RTX PRO 6000 Blackwell Server Edition (96 GB VRAM)**.
- **AI Model Studio**:
  - Interactive terminal chat with uncompressed 27B LLMs (`gemma-3-27b-it-abliterated`).
  - Native localhost OpenAI bridge (`molab forward <id> --port 8000`) for Open WebUI, SillyTavern, and Python SDKs.
- **Interactive Root Terminal**: Bidirectional WebSocket PTY connection directly into CoreWeave cloud containers.
- **Real-time GPU Telemetry**: Live VRAM metrics, SM counts, CUDA 13.0, and PyTorch telemetry.
- **Cloud File Transfer**: Bidirectional `push` and `pull` commands.
- **Remote Package Management**: Remote `install` via `uv pip`.
- **Dual CLI Aliases**: Global `molab` and `molabctl` commands.
