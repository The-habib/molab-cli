# MoLab CLI Command Reference

Both `molab` and `molabctl` can be used interchangeably.

---

## 1. Discovery & Status

### `molab list`
List all cloud notebooks in your workspace.
* **Flags:**
  * `-j, --json`: Output machine-readable JSON array of notebooks.
* **Example:**
  ```bash
  molab list
  molab list --json
  ```

### `molab free`
Audit all active Blackwell GPU pods, determine VRAM usage and running processes, and output the recommended idle pod.
* **Flags:**
  * `-j, --json`: Output full audit dictionary as JSON.
* **Example:**
  ```bash
  molab free
  molab free --json
  ```

### `molab status`
Display authentication status, email, organization, and session ID.
* **Flags:**
  * `-j, --json`: Output auth metadata as JSON.
* **Example:**
  ```bash
  molab status
  molab status --json
  ```

### `molab inspect <notebook_id>`
Display hardware allocation, sandbox container health, and Marimo version.
* **Flags:**
  * `-j, --json`: Output inspection dictionary as JSON.
* **Example:**
  ```bash
  molab inspect nb_emuqXoWkVed6jPNZxND7eo
  ```

### `molab ps`
List actively running CoreWeave sandbox pods.
* **Flags:**
  * `-j, --json`: Output active pods as JSON.
* **Example:**
  ```bash
  molab ps
  ```

### `molab gpu <notebook_id>`
Query real-time NVIDIA Blackwell GPU telemetry (allocated VRAM, temperature, compute capability, CUDA version).
* **Flags:**
  * `-j, --json`: Output telemetry as JSON.
* **Example:**
  ```bash
  molab gpu nb_emuqXoWkVed6jPNZxND7eo
  ```

---

## 2. Pod Lifecycle

### `molab create`
Create a new cloud notebook with an attached GPU or CPU.
* **Options:**
  * `--blackwell / --cpu-only`: Attach NVIDIA RTX PRO 6000 Blackwell (Default: True).
  * `--cpu <cores>`: Number of vCPU cores (Default: 4).
  * `--memory <GiB>`: System RAM in GiB (Default: 32).
  * `--title <name>`: Optional notebook title.
  * `--code <file>`: Local Python script to initialize notebook cells.
* **Example:**
  ```bash
  molab create --blackwell --title "Inference-Pod"
  ```

### `molab compute <notebook_id>`
Hot-swap hardware resources on an existing notebook.
* **Options:**
  * `--blackwell / --cpu-only`: Switch to Blackwell GPU or CPU.
  * `--cpu <cores>`: Number of vCPU cores.
  * `--memory <GiB>`: System RAM in GiB.
* **Example:**
  ```bash
  molab compute nb_xxx --blackwell --cpu 8 --memory 64
  ```

### `molab stop <notebook_id>`
Stop a running sandbox container to conserve cloud compute time.
* **Example:**
  ```bash
  molab stop nb_xxx
  ```

### `molab delete <notebook_id>`
Permanently delete a cloud notebook.
* **Options:**
  * `-y, --yes`: Skip confirmation prompt.
* **Example:**
  ```bash
  molab delete nb_xxx -y
  ```

---

## 3. High-Speed File Transfer

### `molab push <notebook_id> <local_path> [remote_path]`
Upload local files or directories directly to the pod using native HTTP multipart streaming.
* **Options:**
  * `-r, --recursive`: Package and upload directory recursively.
* **Examples:**
  ```bash
  # Upload single file
  molab push nb_xxx ./input.mp4 /workspace/input.mp4

  # Upload directory recursively
  molab push -r nb_xxx ./my_project /workspace/my_project
  ```

### `molab pull <notebook_id> <remote_path> [local_path]`
Download files or directories from the pod to local storage using native HTTP streaming.
* **Options:**
  * `-r, --recursive`: Package and download directory recursively.
* **Examples:**
  ```bash
  # Download single file
  molab pull nb_xxx /workspace/output.mp4 ~/Download/

  # Download directory recursively
  molab pull -r nb_xxx /workspace/models ./local_models/
  ```

---

## 4. Execution & Networking

### `molab exec <notebook_id> "<command>"`
Execute a command inside the root bash environment of the pod.
* **Options:**
  * `--timeout <seconds>`: Execution timeout (Default: 30.0s).
* **Examples:**
  ```bash
  molab exec nb_xxx "nvidia-smi"
  molab exec nb_xxx "nohup python3 /workspace/server.py > /workspace/server.log 2>&1 &"
  ```

### `molab shell <notebook_id>`
Open an interactive pseudo-terminal bash session with root access.
* **Example:**
  ```bash
  molab shell nb_xxx
  ```

### `molab install <notebook_id> <packages...>`
Install Python packages using `uv pip` on the pod.
* **Example:**
  ```bash
  molab install nb_xxx torch torchvision torchaudio
  ```

### `molab forward <notebook_id>`
Bridge the pod's model server (port 8000) to localhost (`http://127.0.0.1:8000/v1`).
* **Options:**
  * `--port <number>`: Local port to bind (Default: 8000).
* **Example:**
  ```bash
  molab forward nb_xxx --port 8000
  ```

---

## 5. System Diagnostics & Capabilities

### `molab doctor`
Run comprehensive diagnostic health checks across auth, network, tools, local storage, and active Blackwell GPU pods.
* **Flags:**
  * `-j, --json`: Machine-readable diagnostic health report.
* **Example:**
  ```bash
  molab doctor
  molab doctor --json
  ```

### `molab capabilities`
Discover confirmed facts and environment constraints across local host, remote compute, transfer protocols, and execution runtimes.
* **Flags:**
  * `-j, --json`: Machine-readable inventory of capabilities.
* **Example:**
  ```bash
  molab capabilities
  molab capabilities --json
  ```

---

## 6. Directory Delta Sync

### `molab sync <notebook_id> <local_dir> <remote_dir>`
Delta synchronize a local directory to a pod by comparing local and remote SHA-256 manifests. Only files that are missing or have modified checksums are uploaded.
* **Options:**
  * `--dry-run`: Compute and report the sync manifest diff without transferring files.
  * `-j, --json`: Machine-readable sync summary.
* **Examples:**
  ```bash
  molab sync nb_xxx ./src /workspace/src
  molab sync nb_xxx ./datasets /workspace/datasets --dry-run --json
  ```

---

## 7. Universal Job & Artifact Subsystem

### `molab job submit <notebook_id> "<command>"`
Submit a detached background job with persistent SQLite tracking, auto-log recording, and artifact collection.
* **Options:**
  * `--name <string>`: Descriptive job name.
  * `--workdir <path>`: Working directory on pod (Default: `/workspace`).
  * `-j, --json`: Output job record as JSON.
* **Example:**
  ```bash
  molab job submit nb_xxx "python3 train.py --epochs 10" --name "lora-run-1"
  ```

### `molab job list`
List historic and active background jobs.
* **Options:**
  * `--limit <number>`: Max jobs to list (Default: 25).
  * `-j, --json`: Output jobs as JSON.
* **Example:**
  ```bash
  molab job list
  molab job list --json
  ```

### `molab job status <job_id>`
Query refreshed status of a background job. Synchronizes controller state with remote process state and captures exit codes upon completion.
* **Example:**
  ```bash
  molab job status job_abc12345
  molab job status job_abc12345 --json
  ```

### `molab job logs <job_id>`
Read trailing execution logs for a background job.
* **Options:**
  * `--tail <lines>`: Number of lines to tail (Default: 100).
* **Example:**
  ```bash
  molab job logs job_abc12345 --tail 50
  ```

### `molab job cancel <job_id>`
Gracefully terminate a running background job on the remote pod.
* **Example:**
  ```bash
  molab job cancel job_abc12345
  ```

### `molab job artifacts <job_id>`
List or download output artifacts produced by a completed job.
* **Options:**
  * `--download <dir>`: Local destination folder to download artifacts to.
  * `-j, --json`: Output artifacts list as JSON.
* **Examples:**
  ```bash
  molab job artifacts job_abc12345
  molab job artifacts job_abc12345 --download ./output
  ```

---

## 8. Pluggable Workload Extensions

### `molab workload list`
List available pluggable workload templates (e.g., `video-enhance-4k`, `whisper-transcribe`, `vllm-serve`).
* **Example:**
  ```bash
  molab workload list
  molab workload list --json
  ```

---

## 9. Model Serving & Lifecycle

### `molab serve status <notebook_id>`
Verify application-level health and port bindings on an active pod.
* **Options:**
  * `--port <number>`: Port to check (Default: 8000).
  * `-j, --json`: JSON status report.
* **Example:**
  ```bash
  molab serve status nb_xxx
  ```

### `molab serve stop <notebook_id>`
Gracefully terminate model service running on the pod.
* **Example:**
  ```bash
  molab serve stop nb_xxx
  ```

### `molab serve logs <notebook_id>`
Read recent model service logs from the pod.
* **Example:**
  ```bash
  molab serve logs nb_xxx --tail 50
  ```

---

## 10. Model Context Protocol (MCP) Server

### `molab mcp`
Launch the JSON-RPC 2.0 stdio MCP server for seamless integration with AI agents (Claude, Cursor, Antigravity, Open WebUI).
* **Example:**
  ```bash
  molab mcp
  ```

