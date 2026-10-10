# Global Agent Operational Rules & Guidelines

## 1. MoLab Cloud GPU Workloads (NVIDIA RTX PRO 6000 Blackwell 96GB)

When working with cloud GPU compute, pods, or AI model hosting on MoLab:

1. **Activate the MoLab Skill & Documentation:**
   * Always consult and read [`~/.agents/skills/molab/SKILL.md`](file:///data/data/com.termux/files/home/.agents/skills/molab/SKILL.md).
   * Refer to the studio-grade, dedicated documentation in [`~/molab-cli/docs/`](file:///data/data/com.termux/files/home/molab-cli/docs/):
     - [`USER_GUIDE.md`](file:///data/data/com.termux/files/home/molab-cli/docs/USER_GUIDE.md): End-to-end developer workflows, zero-friction pod targeting, and production recipes.
     - [`UI_AND_UX.md`](file:///data/data/com.termux/files/home/molab-cli/docs/UI_AND_UX.md): OLED Slate design tokens, status/hardware badges, and pipe safety.
     - [`STATE_MANAGEMENT.md`](file:///data/data/com.termux/files/home/molab-cli/docs/STATE_MANAGEMENT.md): 5-tier state architecture and SQLite WAL durability.
     - [`ERROR_HANDLING.md`](file:///data/data/com.termux/files/home/molab-cli/docs/ERROR_HANDLING.md): Typed MoLabError hierarchy and secret redaction.
     - [`TESTING.md`](file:///data/data/com.termux/files/home/molab-cli/docs/TESTING.md): Deterministic mocking architecture and test matrix.
     - [`DECISIONS.md`](file:///data/data/com.termux/files/home/molab-cli/docs/DECISIONS.md): Architectural Decision Records (ADR-001 to ADR-008).
     - [`SESSION_HANDOFF.md`](file:///data/data/com.termux/files/home/molab-cli/docs/SESSION_HANDOFF.md): Autonomous session handoff contracts and active markers.
     - [`QUICKSTART_NON_TECH.md`](file:///data/data/com.termux/files/home/molab-cli/docs/QUICKSTART_NON_TECH.md): 60-second 1-click model deployment guide.
     - [`MODEL_CATALOG_CONFIGS.md`](file:///data/data/com.termux/files/home/molab-cli/docs/MODEL_CATALOG_CONFIGS.md): Category-wise Blackwell 96GB hyperparameters and model profiles.
     - [`INFERENCE_ENGINE_OPTIMIZATION.md`](file:///data/data/com.termux/files/home/molab-cli/docs/INFERENCE_ENGINE_OPTIMIZATION.md): Kernel dispatch, FlashInfer, Prefix Caching (APC), Chunked Prefill, and memory geometry.
     - [`TUNNELING_AND_INGRESS.md`](file:///data/data/com.termux/files/home/molab-cli/docs/TUNNELING_AND_INGRESS.md): Cloudflare Quick & Named Tunnels, SSE keep-alives, CORS, and Cursor/Claude Code/Python client setups.
     - [`GATEWAY_AND_SECURITY.md`](file:///data/data/com.termux/files/home/molab-cli/docs/GATEWAY_AND_SECURITY.md): Virtual key governance (`sk-molab-...`), SQLite WAL rate limiting (RPM/TPM), and Prometheus metrics.
     - [`API_REFERENCE.md`](file:///data/data/com.termux/files/home/molab-cli/docs/API_REFERENCE.md): Complete Python SDK reference.
     - [`MCP_GUIDE.md`](file:///data/data/com.termux/files/home/molab-cli/docs/MCP_GUIDE.md): 48-tool Model Context Protocol reference.
     - [`PERMANENCE_GUIDE.md`](file:///data/data/com.termux/files/home/molab-cli/docs/PERMANENCE_GUIDE.md): 100% on-MoLab vault & keepalive engine.
     - [`BATCH_ORCHESTRATION.md`](file:///data/data/com.termux/files/home/molab-cli/docs/BATCH_ORCHESTRATION.md): Multi-pod DAG pipelines.
     - [`WORKLOAD_COOKBOOK.md`](file:///data/data/com.termux/files/home/molab-cli/docs/WORKLOAD_COOKBOOK.md): Battle-tested AI production recipes.
     - [`CLI_REFERENCE.md`](file:///data/data/com.termux/files/home/molab-cli/docs/CLI_REFERENCE.md): Full CLI subcommands & flags.

2. **Smart Pod Discovery & Workload Safety:**
   * **Never guess pod IDs or assume a pod is free.**
   * Run `molab free` (or `molab free --json`) to automatically identify the idle pod vs occupied pods.
   * **Strictly never inspect, kill, stop, or overwrite workloads on occupied pods without explicit user instruction.**

3. **High-Speed Streaming File Transfers:**
   * **Never pipe files or base64 over PTY/WebSocket (`molab exec`).** Terminal PTYs crash on inputs > 4096 bytes.
   * **Always use native HTTP streaming**:
     * Upload: `molab push <pod_id> <local_file_or_dir> [remote_path] [-r]`
     * Download: `molab pull <pod_id> <remote_path> [local_dest] [-r]`

4. **Background Execution for Heavy Compute:**
   * Never run long training, rendering, or video enhancement jobs in the foreground (they will time out).
   * Launch with `nohup`:
     `molab exec <pod_id> "nohup python3 /workspace/job.py > /workspace/job.log 2>&1 &"`
   * Periodically check status via log files:
     `molab exec <pod_id> "tail -n 20 /workspace/job.log ; ps aux | grep job"`

5. **100% On-MoLab Permanence Protocol (Zero Local Storage, Zero Cloud Bills):**
   * Blackwell pod containers are ephemeral (gVisor sandboxes reset on idle timeout or maintenance).
   * **Never download massive model checkpoints (20GB+) to local Android storage.** Termux runs on flash storage with aggressive OS memory reapers.
   * **Always persist state directly on MoLab using the In-Notebook Vault:**
     * Pack: `molab vault pack <pod_id>` (embeds `/workspace` tarball into `/marimo/notebook.py` saved in MoLab's cloud database).
     * Restore: `molab vault unpack <pod_id>`.
     * Inspect: `molab vault inspect <pod_id>`.
   * Defeat the 30-minute idle reaper by launching the dual-loop keepalive supervisor:
     `molab keepalive start <pod_id> --auto-restore` or `molab permanent <pod_id>`.

6. **Kernel Evaluation & Native REST over Fragile PTY WebSockets (Mandatory for All Agents):**
   * **The PTY Buffer Hazard:** Emulated Linux pseudoterminals (`molab exec`, WebSocket PTY) have a strict 4,096-byte input buffer (`MAX_INPUT`), drop bytes on large inputs, and add high latency by spawning bash subshells. Never pipe large code, JSON, or base64 over PTY.
   * **Kernel Eval Preference:** Whenever inspecting environment variables, reading state, evaluating Python expressions, or checking telemetry, **agents must prioritize Direct Kernel Evaluation (`molab kernel eval` or SDK `Pod.eval()`) and native REST routes (`molab usage`, `molab env`)** over launching PTY commands.
   * **Inference Engine Acceleration (Kernels > PyTorch):** For model serving and LLM inference on the Blackwell RTX PRO 6000, **never use naive or unoptimized PyTorch loops**. Strictly deploy with compiled, fused kernels (**FlashInfer**, **Triton**, **vLLM**) with Automatic Prefix Caching (APC) and Chunked Prefill enabled for maximum memory bandwidth saturation and sub-80ms TTFT.

7. **MoLab Community Gallery Integration:**
   * MoLab maintains 111+ curated neural recipes at `https://molab.run/gallery`.
   * Use `molab gallery search <query>` and `molab gallery download <slug>` to instantly acquire production-ready pipelines without writing boilerplate from scratch.

8. **Toolchain Extensibility Directive:**
   * `molab-cli` is installed in editable mode at [`~/molab-cli/`](file:///data/data/com.termux/files/home/molab-cli/).
   * It is an evolving tool. Any agent is authorized and expected to inspect `~/molab-cli/src/molab_cli/`, fix bugs, add missing flags/features, run `pytest ~/molab-cli/tests/`, and commit changes.

9. **Studio-Grade Documentation & Test Discipline:**
   * Every new feature, endpoint, or CLI subcommand must be covered by unit tests in `~/molab-cli/tests/` and verified with `pytest`.
   * All changes must be thoroughly documented in the appropriate separated file in `~/molab-cli/docs/` and synced with `~/.agents/skills/molab/SKILL.md`.

10. **The Prefix-Cache Stability Contract:**
    * When prompting or interacting with models, **never inject dynamic variables** (e.g. current timestamp, git diff count, changing briefings, random session IDs) into the base system prompt or tool schemas.
    * Base system prompts and tool definitions must remain 100% byte-frozen across turns. Inject dynamic environment observations strictly at the tail boundary (in the user turn) to ensure a 90%+ Prefix Cache Hit Rate and sub-80ms TTFT.

11. **Autonomous 1-Click Model Deployment Protocol:**
    * When deploying or serving models, use `molab deploy <alias>` (`qwen-32b`, `coder-32b`, `r1-32b`, `llama-70b`).
    * The deployment engine automatically configures Blackwell-specific hyperparameters, validates process health, establishes the local bridge, and outputs ready-to-use client credentials for Cursor, Claude Code, and Python.

12. **Inference Telemetry & Performance Auditing:**
    * Profile self-hosted model performance using `molab perf` to inspect live APC Cache Hit Rate %, TTFT, and GPU thermal/wattage metrics directly from the vLLM Prometheus engine.

13. **Autonomous Session Handoff & Continuity Protocol:**
    * When handing off tasks across sessions or completing major overhauls, consult and update [`docs/SESSION_HANDOFF.md`](file:///data/data/com.termux/files/home/molab-cli/docs/SESSION_HANDOFF.md).
    * Never leave working trees dirty without running the full test suite (`pytest tests/`) and documenting state markers.

14. **Production LLM Infrastructure & API Serving Standard:**
    * **Universal Browser CORS Architecture:** Every exposed endpoint (`/v1/models`, `/v1/chat/completions`, `/v1/messages`, `/health`) must support arbitrary web origins (e.g. `https://gpt-6.base44.app`, Open WebUI, LibreChat). Preflight `OPTIONS` requests must unconditionally return `HTTP 204 No Content` with `Access-Control-Allow-Origin: <origin|*>`, `Access-Control-Allow-Methods: GET, POST, OPTIONS, PUT, DELETE, PATCH`, `Access-Control-Allow-Headers: Authorization, Content-Type, x-api-key, anthropic-version, anthropic-beta, User-Agent, Accept, Cache-Control, X-Requested-With`, `Access-Control-Max-Age: 86400`, and `Access-Control-Allow-Credentials: true`. Preflight requests must **never** require authentication.
    * **Error CORS Invariance:** All error responses (400, 401, 404, 429, 500) must carry complete CORS headers so browser frontends can inspect structured JSON error bodies instead of throwing opaque CORS policy errors.
    * **Dual API Compatibility & Model Aliasing:** Expose side-by-side OpenAI (`POST /v1/chat/completions`) and Anthropic (`POST /v1/messages`) endpoints with SSE streaming (`text/event-stream`). Automatically map caller model aliases (`gpt-4o`, `claude-3-7-sonnet-20250219`, `qwen-32b`, `default`) to the loaded Blackwell model, while returning standard `HTTP 404` for unrecognized/garbage model identifiers.
    * **Security & Quota Governance:** Support both `Bearer sk-molab-...` and `x-api-key` headers. Enforce token-bucket rate limits (RPM/TPM) backed by SQLite WAL returning `HTTP 429` with `Retry-After`. Never leak raw Python tracebacks.
    * **Permanent Named Tunnels:** When persistent endpoints are needed, use named Cloudflare tunnels with `--token <TOKEN>` and `--hostname <DOMAIN>` to prevent URL churn on restarts.
