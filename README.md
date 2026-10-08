# molab-cli

[![Architecture: Cloud Native](https://img.shields.io/badge/Architecture-Cloud--Native%20TUI%20%26%20CLI-blue.svg)](#)
[![GPU: NVIDIA Blackwell](https://img.shields.io/badge/GPU-RTX%20PRO%206000%20(96GB%20Blackwell)-76b900.svg)](#)
[![Auth: Clerk Permanent](https://img.shields.io/badge/Auth-Clerk%20Auto--Minting-green.svg)](#)
[![UI: Interactive TUI](https://img.shields.io/badge/UI-Zero--Typing%20Interactive%20TUI-magenta.svg)](#)
[![Python: 3.10+](https://img.shields.io/badge/Python-3.10%2B-brightgreen.svg)](#)

A modern interactive TUI and CLI for orchestrating high-performance GPU workloads on MoLab infrastructure.

`molab-cli` gives developers, researchers, and AI teams an elegant terminal-first workflow to create Blackwell GPU pods, inspect them, run commands, transfer files, and expose model endpoints locally without leaving the command line.

---

## Why this project exists

Running large AI workloads in the cloud should feel simple, fast, and reliable. Traditional cloud workflows often require juggling multiple dashboards, browser tabs, and fragmented tooling. `molab-cli` solves that by combining:

- one-command pod lifecycle management
- an interactive control center for zero-typing workflows
- direct root shell access to GPU environments
- model serving and localhost bridging for local tools
- a clean terminal experience tailored for AI infrastructure

This makes it ideal for teams building with large models, running GPU intensive research, and iterating quickly in ephemeral cloud environments.

---

## Best use cases

### 1. Large AI model deployment
Use `molab-cli` to launch a Blackwell GPU pod and serve heavyweight open-source models with minimal setup. The local bridge support lets tools like Open WebUI or Python SDK clients connect to a remote model server as if it were running on your machine.

### 2. LLM research and experimentation
Whether you're benchmarking models, testing prompts, fine-tuning, or exploring memory-heavy inference workflows, the CLI gives you an isolated, reproducible environment with powerful GPU resources.

### 3. Interactive remote development
Open a root shell inside the cloud pod, install packages, debug jobs, inspect hardware, and treat the environment like a powerful remote workstation from your terminal.

### 4. Team-based cloud AI infrastructure
Give engineers and researchers a single, consistent CLI for managing GPU workloads instead of relying on fragile ad hoc browser-based workflows.

### 5. Fast, disposable AI environments
Create GPU-backed sessions on demand for demos, experiments, workshops, and short-lived projects without maintaining long-lived infrastructure.

---

## Core capabilities

### Interactive control center
Run `molab` with no arguments to open the interactive TUI and access cloud resources through a guided dashboard.

Features include:
- notebook and pod listing
- one-click GPU pod creation
- status and diagnostics
- file transfer
- package installation
- shell access
- AI model interaction

### GPU pod orchestration
Provision and manage GPU-powered workspaces built for NVIDIA Blackwell compute environments, including:
- notebook and pod creation
- lifecycle control
- status and inspection
- scaling between compute tiers
- cleanup and teardown

### Remote terminal access
Open a cloud terminal with full PTY support and interact as root in a remote Linux environment.

Useful for:
- running commands
- testing installs
- debugging workloads
- managing inference services
- handling project files remotely

### AI model studio and localhost bridge
Expose cloud model endpoints to your local machine using the built-in forwarding workflow so you can connect local apps or scripts to the GPU environment.

Typical integrations:
- Open WebUI
- SillyTavern
- Python OpenAI SDK clients
- curl-based testing

### File transfer and package management
Move files in and out of the remote pod and install Python libraries directly in the environment.

---

## Hardware profile

`molab-cli` targets high-memory GPU workloads on MoLab infrastructure.

| Metric | Configuration |
|---|---|
| GPU | NVIDIA RTX PRO 6000 Blackwell Server Edition |
| VRAM | 94.97 GB GDDR7 |
| Compute Capability | `sm_120` |
| Host RAM | 160 GiB |
| CPU Cores | 20 vCPUs |
| CUDA | 13.0 |
| PyTorch | 2.11.0 |

This profile is purpose-built for large model inference, training, and memory-intensive experimentation.

---

## Installation

```bash
# From the repository root
pip install -e .
```

Both `molab` and `molabctl` are available after installation.

---

## Quick start

Launch the interactive UI:

```bash
molab
```

This opens the control center where you can:
- browse notebooks
- create a Blackwell pod
- inspect systems
- open a terminal
- bridge models locally
- transfer files

---

## Command reference

### Interactive commands

```bash
molab          # Launch the interactive TUI dashboard
molab ui       # Explicitly open the control center
```

### Authentication and session state

```bash
molab status                            # Inspect session validity and hardware access
molab login                             # Launch the guided setup wizard
molab login --client "<__client cookie>" # Configure authentication non-interactively
```

### Notebook and pod lifecycle

```bash
molab list                              # List notebooks and pod state
molab create --title "My Blackwell Pod" # Create a new GPU-enabled pod
molab compute <id> --blackwell          # Switch to Blackwell GPU compute
molab compute <id> --cpu-only           # Switch to CPU-only compute
molab inspect <id>                      # Inspect the pod and hardware details
molab stop <id>                         # Stop a running pod
molab clone <id>                        # Duplicate a notebook or pod
molab rename <id> "New Name"           # Rename an environment
molab delete <id>                      # Delete a pod or notebook
```

### Shell and execution

```bash
molab shell <id>                        # Open an interactive root shell
molab exec <id> "nvidia-smi"            # Run a one-off command on the remote pod
molab gpu <id>                          # View GPU telemetry and system details
molab install <id> transformers vllm    # Install Python packages inside the pod
```

### AI model access and localhost forwarding

```bash
# Open a chat session with a deployed model
molab chat <notebook_id>

# Expose the remote model server on localhost:8000
molab forward <notebook_id> --port 8000
```

Once forwarded, you can interact with the endpoint locally:

```bash
curl http://localhost:8000/health

curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemma-3-27b-it-abliterated",
    "messages": [{"role": "user", "content": "Explain relativity in 2 sentences."}]
  }'
```

### File transfer

```bash
molab push <id> local_file.py /marimo/script.py   # Upload a file to the pod
molab pull <id> /marimo/output.csv ./output.csv   # Download a file from the pod
```

---

## Typical workflows

### Workflow 1: Launch a GPU pod and access a shell

```bash
molab create --title "Research Pod"
molab list
molab shell <pod_id>
```

Then inside the shell:

```bash
nvidia-smi
python --version
pip install -r requirements.txt
```

### Workflow 2: Start a local AI model bridge

```bash
molab chat <pod_id>
molab forward <pod_id> --port 8000
```

Now connect your local tools to:

```text
http://localhost:8000/v1
```

### Workflow 3: Move code into the remote environment

```bash
molab push <pod_id> train.py /workspace/train.py
molab exec <pod_id> "python /workspace/train.py"
```

---

## Who this is for

`molab-cli` is best for:

- AI engineers deploying and testing large models
- ML researchers running GPU-heavy experiments
- developers needing remote GPU workstations from a terminal
- teams building local-to-cloud AI workflows
- users who want a guided, interactive alternative to browser-first cloud tooling

---

## Why developers choose it

- fast onboarding with guided login flow
- minimal friction for cloud GPU management
- keyboard-first workflow in the terminal
- direct access to remote environments with root shell access
- polished experience designed for AI and GPU workloads
- seamless transition between local tools and cloud infrastructure

---

## License

MIT License. Copyright (c) 2026 TG Habib.

---

## Project status

This project is designed for modern AI infrastructure workflows and is optimized for MoLab resources with Blackwell GPU capabilities.
