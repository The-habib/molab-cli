# molab-cli

[![Architecture: Cloud Native](https://img.shields.io/badge/Architecture-Cloud--Native%20TUI%20%26%20CLI-blue.svg)](#)
[![GPU: NVIDIA Blackwell](https://img.shields.io/badge/GPU-RTX%20PRO%206000%20(96GB%20Blackwell)-76b900.svg)](#)
[![File Transfer: Native HTTP REST](https://img.shields.io/badge/File%20Transfer-Native%20HTTP%20Streaming%20(No%20PTY%20Limit)-success.svg)](#)
[![Agent Ready: JSON & Skills](https://img.shields.io/badge/Agent%20Ready-AGY%20Skills%20%2B%20JSON%20APIs-purple.svg)](#)
[![UI: Interactive TUI](https://img.shields.io/badge/UI-Zero--Typing%20Interactive%20TUI-magenta.svg)](#)
[![Python: 3.10+](https://img.shields.io/badge/Python-3.10%2B-brightgreen.svg)](#)

> **Modern, visually stunning standalone interactive CLI & cloud orchestrator for [MoLab](https://molab.marimo.io)**.  
> Provision **NVIDIA RTX PRO 6000 Blackwell Server Edition (96 GB VRAM)** pods, stream interactive root bash terminals, deploy uncompressed 27B+ LLMs, transfer files at 80+ MB/s with native HTTP streaming, and bridge cloud AI directly to `localhost:8000` with **zero typing** and **zero bytes of phone storage used**.

---

## 📚 Complete Documentation Suite

- 🏛️ **[Architecture & Protocol Deep-Dive](docs/ARCHITECTURE.md)**: Network architecture, Clerk auto-minting, PTY vs. HTTP REST streaming, forwarder architecture.
- 📖 **[Command-Line Reference](docs/CLI_REFERENCE.md)**: Full syntax and options for all interactive and automated CLI commands.
- 🤖 **[Autonomous Agent Guide](docs/AGENT_GUIDE.md)**: Golden rules, programmatic snippets, and background job patterns for AI coding agents.
- 🍳 **[Recipes & Cloud Workflows](docs/RECIPES.md)**: Step-by-step guides for 4K video neural remastering, vLLM serving, LoRA fine-tuning, and ComfyUI.
- 🔧 **[Troubleshooting Runbook](docs/TROUBLESHOOTING.md)**: Diagnosing Clerk 401s, WebSocket disconnects, OOM states, and port forwarding issues.

---

## ✨ Features at a Glance

1. **🎨 Interactive Visual Dashboard (Zero Typing)**:
   - Run `molab` without any arguments to enter the **Interactive Control Center**.
   - Navigate notebooks, cloud containers, AI models, and file transfers using arrow keys and instant selectors.
   - Beautiful OLED Dark Mode aesthetic, live spinners, status pills (`🟢 RUNNING`, `⚪ STOPPED`, `⚡ BLACKWELL 96GB`).

2. **⚡ NVIDIA RTX PRO 6000 Blackwell (96 GB VRAM)**:
   - Provisions ephemeral CoreWeave pods powered by **NVIDIA RTX PRO 6000 Blackwell Server Edition** (`sm_120`).
   - Access **94.97 GB GDDR7 VRAM**, **160 GiB Host RAM**, **20 CPU cores**, and **CUDA 13.0**.

3. **🚀 Native High-Speed HTTP/2 File Streaming (`push` / `pull` / `-r`)**:
   - **Zero PTY Buffer Truncation**: Replaces legacy base64 terminal echo with direct Marimo REST endpoints (`/api/files/create` and `/api/files/download`).
   - Handles multi-gigabyte files (e.g., 4K raw videos, model weights, checkpoints) seamlessly at line rate (80+ MB/s).
   - Recursive folder support (`-r, --recursive`) auto-packages directories via streaming tarballs without disk bloat.

4. **🧠 Smart Free-Pod Discovery (`molab free`)**:
   - Automated multi-pod workload inspection queries GPU VRAM allocation, process trees, and server uptime.
   - Automatically identifies idle Blackwell pods to protect active production jobs or LLM servers from disruption.
   - Programmatic `--json` support for seamless AI agent decision-making.

5. **🤖 AI Model Studio & Localhost Bridge**:
   - **Terminal Chat**: Chat directly with deployed models (such as `gemma-3-27b-it-abliterated`) with streaming markdown formatting.
   - **Localhost Bridge (`molab forward`)**: Expose the cloud model server to `http://localhost:8000/v1` for use with Open WebUI, SillyTavern, or the Python OpenAI SDK.
   - **Zero Phone Storage**: All 53+ GB of model weights remain strictly inside the cloud pod.

6. **💻 Ephemeral Cloud PC from Terminal**:
   - Interactive root bash terminal over WebSockets (`wss://*.sb.molab.run/terminal/ws`) with full PTY emulation.
   - Remote package management with `uv pip` for instantaneous dependency resolution.

---

## 🛠️ Installation

```bash
# Clone or navigate to the repository
cd /data/data/com.termux/files/home/molab-cli

# Install in editable mode
pip install -e .
```

Both `molab` and `molabctl` commands are available globally in your PATH.

---

## 🚀 Quick Start (Zero Typing)

Simply type:

```bash
molab
```

This launches the **Interactive Control Center**:
```
╭──────────────────────────────────────────────────────────────────────────────╮
│                                                                              │
│   ███╗   ███╗ ██████╗ ██╗      █████╗ ██████╗                                │
│   ████╗ ████║██╔═══██╗██║     ██╔══██╗██╔══██╗                               │
│   ██╔████╔██║██║   ██║██║     ███████║██████╔╝                               │
│   ██║╚██╔╝██║██║   ██║██║     ██╔══██║██╔══██╗                               │
│   ██║ ╚═╝ ██║╚██████╔╝███████╗██║  ██║██████╔╝                               │
│   ╚═╝     ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═╝╚═════╝                                │
│                                                                              │
│     ⚡ Cloud Notebooks & NVIDIA Blackwell Server Hub  •  v2.0.0              │
╰──────────────────────────────────────────────────────────────────────────────╯
╭──────────────────────────────────────────────────────────────────────────────╮
│ ● user@example.com      ⚡ 1 Pod Running (Blackwell 96GB)  NVIDIA RTX PRO 6000│
╰──────────────────────────────────────────────────────────────────────────────╯

? Select an action:
  ❯ 📋 Browse & Manage Notebooks
    🚀 1-Click Launch Blackwell Pod
    🔍 Find Idle / Free GPU Pod
    💻 Open Cloud Root Terminal (Shell)
    🤖 AI Model Studio (Gemma 3 27B)
    ⚡ Real-time GPU Telemetry
    📂 Cloud File Transfer (Push / Pull)
    📦 Python Package Manager
    🔑 Account & Authentication
    ❓ Quick Guide & Documentation
    🚪 Exit
```

---

## 📖 Command Line Reference (For Scripts & Automation)

All commands can be run with either `molab` or `molabctl`. Every inspection command supports `--json` (`-j`) for programmatic automation:

### 1. Interactive UI
```bash
molab          # Launch interactive TUI Control Center
molab ui       # Explicitly launch TUI Control Center
```

### 2. Workspace & Authentication
```bash
molab status                            # Inspect session validity & hardware access
molab status --json                     # Output auth metadata as JSON
molab login                             # Launch guided onboarding setup wizard
molab login --client "<__client cookie>" # Configure cookie non-interactively
```

### 3. Smart Discovery & Pod Lifecycle
```bash
molab free                              # Audit all active pods & recommend idle pod
molab free --json                       # Machine-readable workload audit
molab list                              # List all cloud notebooks and running status
molab list --json                       # JSON array of notebooks
molab ps                                # List active CoreWeave sandbox pods
molab ps --json                         # Active pods in JSON
molab create --title "My Blackwell Pod" # 1-click create sandbox with 96GB Blackwell GPU
molab compute <id> --blackwell          # Switch existing notebook to Blackwell GPU
molab compute <id> --cpu-only           # Switch notebook to CPU mode
molab inspect <id>                      # Inspect container ID, hardware, and kernel
molab inspect <id> --json               # Container metadata in JSON
molab stop <id>                         # Stop / shutdown a running pod
molab clone <id>                        # Duplicate notebook
molab rename <id> "New Name"            # Rename notebook
molab delete <id>                       # Delete notebook
```

### 4. Cloud PC Terminal & Remote Exec
```bash
molab shell <id>                        # Open interactive root bash terminal
molab exec <id> "nvidia-smi"            # Execute one-shot bash command on pod
molab gpu <id>                          # View Blackwell VRAM & hardware telemetry
molab gpu <id> --json                   # Raw GPU telemetry metrics in JSON
molab install <id> transformers vllm    # Install Python packages inside pod
```

### 5. High-Speed File Transfer (Native HTTP/2 Streaming)
```bash
# Upload a single file (direct Marimo REST streaming, bypasses PTY buffer):
molab push <id> ./video.mp4 /workspace/input.mp4

# Upload an entire directory recursively:
molab push <id> ./my_project /workspace/project -r

# Download a processed file:
molab pull <id> /workspace/enhanced.mp4 ./enhanced.mp4

# Download an entire folder recursively:
molab pull <id> /workspace/checkpoints ./local_checkpoints -r
```

### 6. AI Model Studio & Localhost Bridge
```bash
# Interactive terminal chat with deployed 27B unrestricted model:
molab chat <notebook_id>

# Bridge localhost:8000 to the remote model server:
molab forward <notebook_id> --port 8000
```

Once forwarded, query `http://localhost:8000/v1` from any local app:
```bash
curl http://localhost:8000/health

curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemma-3-27b-it-abliterated",
    "messages": [{"role": "user", "content": "Explain relativity in 2 sentences."}]
  }'
```

---

## 🤖 Antigravity & AI Agent Extensibility

### 💡 Core Design Philosophy
> **"This is not a finished tool; any agent working with it must discover, improve, and extend it as they use it."**

The MoLab CLI is designed from the ground up for agentic pair-programming. Every agent operating in this environment is empowered to:
1. **Audit & Extend Code**: `molab-cli` is installed in editable mode (`pip install -e ~/molab-cli`). Any new endpoint, CLI flag, or streaming capability added to `src/molab_cli/` is immediately available globally.
2. **Consult Built-in Skills**: Agents have access to the Antigravity `molab` skill at `~/.agents/skills/molab/SKILL.md` and helper scripts like `~/.agents/skills/molab/scripts/find_free_pod.py`.
3. **Preserve Safe Pod Workflows**: Always run `molab free --json` before executing heavy tasks to protect existing user workloads.
4. **Always Test Changes**: Run `pytest tests/` after modifying the CLI codebase to verify compatibility.

---

## 🔬 Hardware Specifications

| Metric | CoreWeave Cloud Container Spec |
|---|---|
| **GPU** | NVIDIA RTX PRO 6000 Blackwell Server Edition |
| **VRAM** | 94.97 GB GDDR7 (97,251 MB) |
| **Compute Capability** | `sm_120` (Architecture: Blackwell, Major: 12, Minor: 0) |
| **Multiprocessors** | 188 SMs |
| **Host System RAM** | 160.0 GiB RAM |
| **Host CPU Cores** | 20 vCPUs (x86_64) |
| **CUDA & PyTorch** | CUDA 13.0 + PyTorch 2.11.0 |
| **Local Phone Storage** | **0 Bytes Used** (Zero disk wear on device) |

---

## 📄 License

MIT License. Copyright (c) 2026 TG Habib.
