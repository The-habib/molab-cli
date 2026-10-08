# molab-cli

[![Architecture: Cloud Native](https://img.shields.io/badge/Architecture-Cloud--Native%20TUI%20%26%20CLI-blue.svg)](#)
[![GPU: NVIDIA Blackwell](https://img.shields.io/badge/GPU-RTX%20PRO%206000%20(96GB%20Blackwell)-76b900.svg)](#)
[![Auth: Clerk Permanent](https://img.shields.io/badge/Auth-Clerk%20Auto--Minting-green.svg)](#)
[![UI: Interactive TUI](https://img.shields.io/badge/UI-Zero--Typing%20Interactive%20TUI-magenta.svg)](#)
[![Python: 3.10+](https://img.shields.io/badge/Python-3.10%2B-brightgreen.svg)](#)

> **Modern, visually stunning standalone interactive CLI & cloud orchestrator for [MoLab](https://molab.marimo.io)**.  
> Provision **NVIDIA RTX PRO 6000 Blackwell Server Edition (96 GB VRAM)** pods, stream interactive root bash terminals, deploy uncompressed 27B+ LLMs, and bridge cloud AI directly to `localhost:80[...]

---

## 🎯 Why People Use This CLI

`molab-cli` is built for people who want to run serious AI workloads without the friction of traditional cloud management. It is especially useful for developers, researchers, and teams that need fast access to high-memory GPU compute, interactive environments, and local-like workflows from a terminal.

### Real-world use cases

1. **Running large AI models locally through the cloud**
   - Launch a Blackwell GPU pod and serve a 27B+ model in minutes.
   - Connect tools like Open WebUI, SillyTavern, or Python clients to a local bridge endpoint.
   - Work with powerful open-source models without managing bare-metal hardware.

2. **Training and experimenting with LLMs and ML workloads**
   - Provision a GPU workspace for fine-tuning, evaluation, and experimentation.
   - Install Python libraries, run notebooks, and iterate quickly in a disposable cloud environment.
   - Spin up a fresh environment per project without long setup cycles.

3. **Interactive AI development from a terminal**
   - Open a root shell in a remote GPU pod and work like you are on a local machine.
   - Copy files to and from the cloud, install packages, and debug tasks in real time.
   - Ideal for prototyping and shipping AI features quickly.

4. **Research and benchmarking**
   - Test model performance, inference speed, memory usage, and compatibility on high-memory GPUs.
   - Compare configurations and workloads using reproducible cloud environments.
   - Run heavy compute tasks without tying up local hardware.

5. **Team-based AI infrastructure access**
   - Give engineers and researchers a fast, consistent CLI to manage GPU pods.
   - Keep experimentation isolated while maintaining a single workflow for cloud compute.
   - Reduce onboarding time for AI projects that depend on specialized hardware.

6. **Hybrid local-to-cloud workflows**
   - Use `molab forward` to expose a cloud model server at `localhost:8000`.
   - Keep local tools and apps unchanged while running the heavy compute remotely.
   - Perfect for a smooth bridge between local development and cloud-scale inference.

7. **Rapid environment creation for demos and presentations**
   - Launch GPU pods on demand for showcase environments, workshops, or product demos.
   - Deliver a polished AI experience without requiring developers to configure infrastructure manually.

### Who this is for

- AI engineers building or serving large models
- Researchers running GPU-intensive experiments
- ML practitioners testing fine-tuning, inference, and agent workflows
- Developers who prefer a terminal-first workflow over browser-heavy cloud dashboards
- Teams that want cloud GPUs without the operational burden of managing hardware directly

---

## ✨ Features at a Glance

1. **🎨 Interactive Visual Dashboard (Zero Typing)**:
   - Run `molab` without any arguments to enter the **Interactive Control Center**.
   - Navigate notebooks, cloud containers, AI models, and file transfers using arrow keys and instant selectors.
   - Beautiful OLED Dark Mode aesthetic, live spinners, status pills (`🟢 RUNNING`, `⚪ STOPPED`, `⚡ BLACKWELL 96GB`).

2. **🔑 30-Second Guided Onboarding Wizard**:
   - First time user? `molab` automatically guides you through connecting your account.
   - Visual instructions show where to grab your master `__client` cookie from Developer Tools.
   - Smart cookie parser accepts full headers or tokens, auto-discovers your user profile, active sessions, and organizations from Clerk.

3. **⚡ NVIDIA RTX PRO 6000 Blackwell (96 GB VRAM)**:
   - Provisions ephemeral CoreWeave pods powered by **NVIDIA RTX PRO 6000 Blackwell Server Edition** (`sm_120`).
   - Access **94.97 GB GDDR7 VRAM**, **160 GiB Host RAM**, **20 CPU cores**, and **CUDA 13.0**.

4. **🤖 AI Model Studio & Localhost Bridge**:
   - **Terminal Chat**: Chat directly with deployed models (such as `gemma-3-27b-it-abliterated`) with streaming markdown formatting.
   - **Localhost Bridge (`molab forward`)**: Expose the cloud model server to `http://localhost:8000/v1` for use with Open WebUI, SillyTavern, or the Python OpenAI SDK.
   - **Zero Phone Storage**: All 53+ GB of model weights remain strictly inside the cloud pod.

5. **💻 Ephemeral Cloud PC from Terminal**:
   - Interactive root bash terminal over WebSockets (`wss://*.sb.molab.run/terminal/ws`) with full PTY emulation.
   - Bidirectional file transfer (`push` / `pull`).
   - Remote package management with `uv pip`.

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
╭────────────────────────────────────────────────────────────────�[...]
│                                                                              │
│   ███╗   ███╗ ██████╗ ██╗      █████╗ ██████╗                                │
│   ████╗ ████║██╔═══██╗██║     ██╔══██╗██╔══██╗                               │
│   ██╔████╔██║██║   ██║██║     ███████║██████╔╝                               │
│   ██║╚██╔╝██║██║   ██║██║     ██╔══██║██╔══██╗                               │
│   ██║ ╚═╝ ██║╚██████╔╝███████╗██║  ██║██████╔╝                               │
│   ╚═╝     ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═╝╚═════╝                                │
│                                                                              │
│     ⚡ Cloud Notebooks & NVIDIA Blackwell Server Hub  •  v1.0.0              │
╰────────────────────────────────────────────────────────────────�[...]
╭────────────────────────────────────────────────────────────────�[...]
│ ● user@example.com      ⚡ 1 Pod Running (Blackwell 96GB)  NVIDIA RTX PRO 6000│
╰────────────────────────────────────────────────────────────────�[...]

? Select an action:
  ❯ 📋 Browse & Manage Notebooks
    🚀 1-Click Launch Blackwell Pod
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

All commands can be run with either `molab` or `molabctl`:

### 1. Interactive UI
```bash
molab          # Launch interactive TUI Control Center
molab ui       # Explicitly launch TUI Control Center
```

### 2. Workspace & Authentication
```bash
molab status                            # Inspect session validity & hardware access
molab login                             # Launch guided onboarding setup wizard
molab login --client "<__client cookie>" # Configure cookie non-interactively
```

### 3. Notebooks & Pod Lifecycle
```bash
molab list                              # List all cloud notebooks and running status
molab create --title "My Blackwell Pod" # 1-click create sandbox with 96GB Blackwell GPU
molab compute <id> --blackwell          # Switch existing notebook to Blackwell GPU
molab compute <id> --cpu-only           # Switch notebook to CPU mode
molab inspect <id>                      # Inspect container ID, hardware, and kernel
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
molab install <id> transformers vllm    # Install Python packages inside pod
```

### 5. AI Model Studio & Localhost Bridge
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

### 6. Bidirectional File Transfer
```bash
molab push <id> local_file.py /marimo/script.py   # Upload file to pod
molab pull <id> /marimo/output.csv ./output.csv   # Download file from pod
```

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
