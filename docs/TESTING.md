# MoLab CLI — Testing Guide & Verification Strategy

## 1. Test Suite Overview

MoLab CLI features a comprehensive test suite of **178 automated unit and integration tests** built on `pytest` and `pytest-asyncio`.

Every feature, CLI subcommand, state transition, and error condition is verified locally using deterministic mocks. Testing requires **zero active cloud GPU compute** and **zero external credentials**, guaranteeing reproducible CI/CD execution.

---

## 2. Running the Tests

### Execute Full Test Suite
```bash
pytest tests/
```

### Verbose Mode with Timing
```bash
pytest -v --durations=10 tests/
```

### Run Specific Test Modules
```bash
pytest tests/test_ui_ux_overhaul.py     # UI/UX overhaul, redaction & resolution
pytest tests/test_deploy.py             # 1-Click model deployment engine
pytest tests/test_perf.py               # Live telemetry & APC cache auditing
pytest tests/test_gateway_db.py         # SQLite WAL virtual key governance
pytest tests/test_chat.py               # Autonomous coding agent loop
pytest tests/test_vault.py              # 100% on-MoLab storage vault
pytest tests/test_transfer.py           # Native HTTP/2 streaming transfers
```

---

## 3. Test File Architecture

| Test File | Tests | Functional Scope |
| :--- | :--- | :--- |
| `test_ui_ux_overhaul.py` | 11 | Pod auto-resolution, secret redaction, error classification, theme badges |
| `test_deploy.py` | 2 | 1-Click model deployment, supervisor launcher, credentials |
| `test_perf.py` | 2 | Prometheus metric parsing, APC hit rate, GPU thermals |
| `test_gateway_db.py` | 4 | Virtual API key issuance, SQLite WAL mode, audit logs |
| `test_rate_limiter.py` | 4 | Sliding window RPM and TPM token metering |
| `test_chat.py` | 8 | Coding agent loop, tool execution, byte-frozen system prompt |
| `test_bridge.py` | 8 | Hermes & Claude Code local bridge routing |
| `test_vault.py` | 8 | In-Notebook Vault packing, unpacking, and base64 AST injection |
| `test_keepalive.py` | 5 | Dual-loop anti-idle daemon, heartbeat counters, TTL renewal |
| `test_batch_orchestrator.py` | 5 | DAG task scheduling, dependencies, worker pools |
| `test_transfer.py` | 6 | HTTP multipart uploads, streaming downloads, SHA-256 sync |
| `test_mcp.py` | 5 | Model Context Protocol 48-tool JSON-RPC stdio server |
| `test_cli.py` | 6 | Click CLI subcommands, `--help` flags, JSON outputs |

---

## 4. Mocking Guidelines & Principles

1. **No External Network Calls:** Remote HTTP calls (`urllib.request.urlopen`, `httpx`) must be patched using `unittest.mock.patch` or `respx`.
2. **Temporary State Directories:** File system operations must use `tmp_path` fixtures to ensure tests never touch user `~/.config/molab/` or actual production databases.
3. **Strict Assertion Policy:** Tests must assert exact state properties (e.g. exit codes, redacted output, JSON schema integrity) rather than merely checking that exceptions are not thrown.
