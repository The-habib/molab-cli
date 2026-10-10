# MoLab CLI — Comprehensive User Guide

A step-by-step manual for developer workflows on MoLab cloud GPU compute powered by **NVIDIA RTX PRO 6000 Blackwell Server Edition (96GB GDDR7 VRAM)**.

---

## 1. Getting Started & Authentication

### Step 1: Initial Login
```bash
molab login
```
If you already have your Clerk master session cookie:
```bash
molab login --client "your___client_cookie_here"
```

### Step 2: Verify Status
```bash
molab status
```
Displays your email, organization slug, active sessions, and Clerk token minting status.

---

## 2. Pod Discovery & Compute Provisioning

### Find Free / Idle Blackwell Pods
Never guess pod IDs or interrupt active jobs:
```bash
molab free
```
Audits all running pods, inspects active processes and free VRAM, and highlights the recommended idle pod.

### Launch or Switch Compute to Blackwell 96GB
```bash
# Provision a new Blackwell container:
molab create --title "My-AI-Studio" --blackwell

# Hot-swap an existing notebook between CPU and Blackwell 96GB:
molab compute --blackwell
```
*(If no notebook ID is specified, MoLab CLI automatically selects your active pod).*

---

## 3. 1-Click Autonomous Model Deployment

Deploy industry-grade, Blackwell-optimized AI models in 60 seconds:

```bash
# 1. Deploy 32B coding workhorse (Qwen 2.5 Coder 32B):
molab deploy coder-32b

# 2. Deploy Deep Reasoning model (DeepSeek-R1 32B):
molab deploy r1-32b

# 3. Deploy high-precision 70B model (Llama 3.3 70B FP8):
molab deploy llama-70b
```

This single command:
1. Configures FlashInfer attention kernels, chunked prefill, and 16-token Automatic Prefix Caching (APC).
2. Launches a detached background supervisor guardian inside the pod.
3. Automatically bridges port 8000 to `http://localhost:8000/v1`.
4. Establishes a public Cloudflare tunnel.
5. Prints copy-paste credentials for Cursor, Claude Code, and Python.

### Profile Live Inference Telemetry
```bash
molab perf
```
Displays real-time vLLM Automatic Prefix Caching (APC) hit rates, Time To First Token (TTFT), KV cache memory geometry, and GPU thermals/wattage.

---

## 4. Autonomous Terminal Coding Agent

Launch the built-in terminal coding agent powered by your self-hosted Blackwell model:

```bash
molab chat
```
Key capabilities:
* **Tool Calling Loop:** Automatically searches files (`grep_search`), inspects directories (`list_dir`), reads files (`read_file`), applies surgical edits (`edit_file`), and runs shell commands (`run_shell`).
* **Interactive Approvals:** Prompts before applying file modifications or running shell commands (or pass `-y / --auto` to auto-approve).
* **Instant Undo:** Type `/undo` during chat to roll back unwanted file edits.
* **Dual Agents:** Supports `--claude` (Claude Code protocol on port 8082) or `--hermes` (Hermes Agent on port 8000).

---

## 5. Sharing Model Endpoints to External Clients

Expose your deployed model to external tools (Cursor IDE, Cline, Claude Code, Open WebUI):

```bash
# Start public sharing tunnel:
molab share

# Check active public URL and credentials:
molab share --status

# Stop public tunnel:
molab share --stop
```

### External Client Configuration

* **Cursor IDE:**
  * Base URL: `https://<subdomain>.trycloudflare.com/v1`
  * API Key: `sk-molab-master`
  * Model Name: `huihui-ai/Qwen2.5-Coder-32B-Instruct-abliterated`

* **Python OpenAI SDK:**
  ```python
  from openai import OpenAI
  client = OpenAI(
      base_url="https://<subdomain>.trycloudflare.com/v1",
      api_key="sk-molab-master"
  )
  response = client.chat.completions.create(
      model="qwen-32b",
      messages=[{"role": "user", "content": "Write a Python script"}]
  )
  print(response.choices[0].message.content)
  ```

---

## 6. 100% On-MoLab Persistence & 24/7 Permanence

CoreWeave containers reset after 30 minutes of idle time. MoLab defeats this with zero local phone storage and zero third-party cloud bills:

### Arm 24/7 Permanent Pod
```bash
molab permanent
```
Acquires an Android Termux wake-lock, deploys the in-pod self-sustaining supervisor, and packs `/workspace` into the cloud database.

### In-Notebook Vault Operations
```bash
# Pack workspace into notebook AST cell:
molab vault pack

# Inspect active vault size:
molab vault inspect

# Manually unpack workspace:
molab vault unpack
```

---

## 7. High-Speed File Transfers

Never stream files over terminal PTYs. Always use native HTTP/2 streaming:

```bash
# Upload file or directory to remote pod:
molab push my_dataset.csv /workspace/

# Download remote artifact to local storage:
molab pull /workspace/output_model.pt ./

# Delta sync local directory with remote:
molab sync ./src /workspace/src
```

---

## 8. Background Jobs & Batch DAG Pipelines

### Submit Detached Background Job
```bash
molab job submit "python3 /workspace/train.py" --name "training-run-1"
molab job list
molab job logs <job_id>
```

### Multi-Pod DAG Batch Pipeline
```bash
molab batch run pipeline.json
molab batch status <batch_id>
```

---

## 9. Interactive Control Centers

* **Terminal Control Center (TUI):** Run `molab` or `molab ui` for full arrow-key zero-typing navigation.
* **Web Control Center:** Run `molab web` to open the FastAPI OLED browser dashboard on port 8080.
* **Model Context Protocol (MCP):** Run `molab mcp` to start the 48-tool MCP server for Cursor or Claude Desktop.
