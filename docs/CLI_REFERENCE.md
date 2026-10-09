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

---

## 11. Multi-Pod Batch Pipelines

### `molab batch validate <manifest.json>`
Validate schema, task specifications, and DAG dependency structure. Displays topological execution stages.
* **Flags:**
  * `-j, --json`: Output validation result as JSON.
* **Example:**
  ```bash
  molab batch validate pipeline.json
  molab batch validate pipeline.json --json
  ```

### `molab batch submit <manifest.json>`
Submit a batch manifest into the SQLite persistent queue without executing immediately.
* **Options:**
  * `-p, --max-parallel <int>`: Max parallel tasks allowed.
  * `-j, --json`: Output batch ID and status as JSON.
* **Example:**
  ```bash
  molab batch submit pipeline.json -p 4
  ```

### `molab batch run <manifest_or_id>`
Execute an autonomous multi-pod pipeline with dynamic scheduling, task progression, and live telemetry updates.
* **Options:**
  * `-p, --max-parallel <int>`: Concurrency cap across pods.
  * `--poll <seconds>`: Status polling interval (Default: 3.0s).
  * `--timeout <seconds>`: Overall execution timeout.
  * `-j, --json`: Stream output summary as JSON.
* **Example:**
  ```bash
  molab batch run pipeline.json
  molab batch run batch_cb8c7e43 --poll 2.0
  ```

### `molab batch list`
List historic and active batch pipelines with task completion ratios.
* **Options:**
  * `-n, --limit <int>`: Maximum batches to list (Default: 20).
  * `-s, --status <text>`: Filter by status (`PENDING`, `RUNNING`, `COMPLETED`, `FAILED`, `CANCELLED`).
  * `-j, --json`: Machine-readable JSON output.
* **Example:**
  ```bash
  molab batch list
  molab batch list --status COMPLETED
  ```

### `molab batch status <batch_id>`
Display batch overview card and breakdown table of constituent tasks.
* **Flags:**
  * `-j, --json`: Full JSON record with all task details.
* **Example:**
  ```bash
  molab batch status batch_cb8c7e43
  ```

### `molab batch logs <batch_id>`
View remote execution logs for constituent tasks.
* **Options:**
  * `-t, --task <key>`: Tail logs for a specific task key.
  * `-n, --tail <int>`: Lines to read (Default: 100).
* **Example:**
  ```bash
  molab batch logs batch_cb8c7e43
  molab batch logs batch_cb8c7e43 --task gpu_verify -n 50
  ```

### `molab batch cancel <batch_id>`
Terminate all active remote processes and cancel pending tasks in the pipeline.
* **Example:**
  ```bash
  molab batch cancel batch_cb8c7e43
  ```

### `molab batch retry <batch_id>`
Reset failed or skipped tasks to `PENDING` so the batch can be resumed.
* **Example:**
  ```bash
  molab batch retry batch_cb8c7e43
  ```

---

## 12. Webhook Notifications

### `molab notify test`
Send a sanitized test webhook notification to verify endpoint connectivity.
* **Options:**
  * `-u, --url <url>`: Target webhook endpoint (HTTPS, Discord, Telegram).
  * `-e, --event <name>`: Custom event name (Default: `test_notification`).
  * `-j, --json`: JSON response status.
* **Example:**
  ```bash
  molab notify test --url https://discord.com/api/webhooks/...
  ```

---

## 13. Native Marimo Backend Subsystem

Direct REST and WebSocket integration with the running Marimo instance on pod port 8080.

### `molab usage <notebook_id>`
Query real-time host RAM, server RAM, kernel RAM, host CPU usage, and GPU memory telemetry.
* **Flags:**
  * `-j, --json`: Output full telemetry dictionary as JSON.
* **Example:**
  ```bash
  molab usage nb_emuqXoWkVed6jPNZxND7eo
  molab usage nb_emuqXoWkVed6jPNZxND7eo --json
  ```

### `molab export <format> <notebook_id>`
Export reactive notebooks directly via remote server exporter without requiring local Pandoc/Typst/Jupyter tools.
* **Subcommands:**
  * `html`: Export to self-contained interactive HTML (`-o <file>`, `--no-code`).
  * `md`: Export to Markdown format (`-o <file>`).
  * `ipynb`: Export to standard Jupyter Notebook format (`-o <file>`).
  * `script`: Export to clean Python script (`-o <file>`).
  * `pdf`: Export to PDF format (`-o <file>`).
* **Example:**
  ```bash
  molab export html nb_xxx -o report.html
  molab export md nb_xxx -o notes.md
  molab export ipynb nb_xxx -o notebook.ipynb
  molab export script nb_xxx -o script.py
  ```

### `molab file <subcommand> <notebook_id> [args]`
Perform instant server-side file operations directly on the pod.
* **Subcommands:**
  * `ls <id> [path]`: List files and folders with type, size, and paths (`-j, --json`).
  * `cat <id> <path>`: Print text contents of remote file directly to terminal.
  * `info <id> <path>`: View file metadata, MIME type, and size (`-j, --json`).
  * `cp <id> <src> <dst>`: Instant server-side copy without client bandwidth.
  * `mv <id> <src> <dst>`: Instant server-side rename/move.
  * `rm <id> <path>`: Delete file or directory on pod (`-y, --yes`).
  * `search <id> <query>`: Recursive search (`--path <dir>`, `--depth <int>`, `--limit <int>`, `-j`).
* **Example:**
  ```bash
  molab file ls nb_xxx /workspace
  molab file cat nb_xxx /workspace/config.yaml
  molab file cp nb_xxx /workspace/model.pt /workspace/model_bak.pt
  molab file search nb_xxx "weights" --path /workspace
  ```

### `molab kernel <subcommand> <notebook_id> [args]`
Interact directly with the remote Python kernel without terminal PTY buffers.
* **Subcommands:**
  * `status <id>`: Query whether kernel is idle or running (`-j, --json`).
  * `eval <id> <code>`: Evaluate Python code directly in kernel, capturing stdout/stderr and output (`--timeout <sec>`, `-j, --json`).
  * `restart <id>`: Soft-restart kernel without restarting container pod.
  * `interrupt <id>`: Interrupt running computation cell.
* **Example:**
  ```bash
  molab kernel status nb_xxx
  molab kernel eval nb_xxx "import torch; print(torch.cuda.is_available())"
  molab kernel restart nb_xxx
  ```

### `molab pkg <subcommand> <notebook_id> [args]`
Inspect and manage Python packages natively on the pod.
* **Subcommands:**
  * `list <id>`: List installed packages (`-j, --json`).
  * `add <id> <package_name>`: Install Python package natively (`--upgrade`).
* **Example:**
  ```bash
  molab pkg list nb_xxx --json
  molab pkg add nb_xxx torchaudio --upgrade
  ```

---

## 10. Anti-Idle Keepalive & Session Renewal (v2.3)

### `molab keepalive <subcommand> <notebook_id> [args]`
Defeat the 30-minute idle session timeout by running an autonomous background heartbeat daemon that continuously renews Clerk tokens and pings the CoreWeave data plane proxy.

* **Subcommands:**
  * `start <id>`: Launch detached background daemon (`--interval <sec>`, `--max-hours <hours>`, `--auto-restore / --no-auto-restore`, `-j, --json`).
  * `stop <id>`: Terminate running keepalive daemon.
  * `status <id>`: Query live daemon PID, heartbeat count, auto-restores, and remaining TTL (`-j, --json`).
  * `list`: List all active and historic keepalive daemons (`-j, --json`).
  * `logs <id>`: View trailing heartbeat logs (`--lines <n>`).

* **Example:**
  ```bash
  # Keep pod alive indefinitely and auto-restore workspace if pod ever resets:
  molab keepalive start nb_emuqXoWkVed6jPNZxND7eo --interval 120 --auto-restore

  # Inspect daemon health:
  molab keepalive status nb_emuqXoWkVed6jPNZxND7eo
  molab keepalive logs nb_emuqXoWkVed6jPNZxND7eo --lines 20

  # Stop daemon when finished:
  molab keepalive stop nb_emuqXoWkVed6jPNZxND7eo
  ```

---

## 11. Workspace Snapshots & Checkpoints (v2.3)

### `molab snapshot <subcommand> [args]`
Capture compressed archives of pod `/workspace`, stream them to local persistent storage (`~/.config/molab/snapshots/`), and restore them on demand or automatically upon pod resurrection.

* **Subcommands:**
  * `create <id>`: Pack `/workspace`, stream tarball to local storage, record SHA-256 in SQLite (`--name <name>`, `--remote-path <dir>`, `-j, --json`).
  * `restore <id>`: Stream archive to pod and unpack into `/workspace` (`--snapshot-id <id>`, `--remote-path <dir>`, `-j, --json`).
  * `list [id]`: List saved snapshots (`--limit <n>`, `-j, --json`).
  * `delete <snapshot_id>`: Delete local archive file and remove database entry.

* **Example:**
  ```bash
  # Take a snapshot before running fine-tuning:
  molab snapshot create nb_xxx --name "pre-finetune"

  # List snapshots:
  molab snapshot list nb_xxx

  # Restore latest snapshot:
  molab snapshot restore nb_xxx

  # Delete snapshot:
  molab snapshot delete snap_abc123
  ```

---

## 12. Multi-Gigabit Cloud Storage Bridge (v2.3)

### `molab storage <subcommand> <notebook_id> [args]`
Leverage pod pre-installed tools (`rclone`, `huggingface-cli`, `git`) for multi-gigabit transfers directly between cloud storage and pod `/workspace`.

* **Subcommands:**
  * `rclone-config <id>`: Push local `~/.config/rclone/rclone.conf` to pod (`--config <path>`).
  * `rclone-backup <id> <remote_dest>`: Sync `/workspace` directly to remote bucket (`--source-dir <dir>`, `--flags <flags>`).
  * `rclone-restore <id> <remote_source>`: Restore `/workspace` directly from remote bucket (`--target-dir <dir>`, `--flags <flags>`).
  * `hf-pull <id> <repo_id>`: Download weights/datasets from Hugging Face Hub (`--dest <dir>`, `--filename <file>`, `--token <tok>`).
  * `hf-push <id> <local_path> <repo_id>`: Upload model weights to Hugging Face Hub (`--type <model|dataset>`, `--token <tok>`).
  * `git-clone <id> <repo_url>`: Clone git repo onto pod (`--dest <dir>`, `--branch <branch>`).

* **Example:**
  ```bash
  # Upload rclone config:
  molab storage rclone-config nb_xxx

  # Sync workspace to Cloudflare R2 at 10Gbps+:
  molab storage rclone-backup nb_xxx r2:my-bucket/weights

  # Pull model from Hugging Face Hub:
  molab storage hf-pull nb_xxx meta-llama/Llama-3-8b --dest /workspace/llama3
  ```

---

## 13. Infinite Pod Permanence Engine (v2.3)

### `molab permanent [notebook_id]`
Transform a Blackwell GPU pod into an infinite, permanently online machine that defeats both the 30-minute idle reaper and container resets.

* **What it does automatically:**
  * Acquires Android Termux wake-lock (`termux-wake-lock`) so phone OS never sleeps.
  * Injects and starts the self-sustaining in-pod guard (`/tmp/_molab_guard.py`) inside the container.
  * Compresses `/workspace` into a permanent in-notebook vault saved to MoLab's cloud database.
  * Launches the detached background supervisor daemon (renews Clerk tokens and pings Marimo proxy).
* **Flags:**
  * `--interval <seconds>`: Heartbeat frequency (default: 120s).
  * `--auto-pack / --no-auto-pack`: Whether to auto-pack workspace into in-notebook vault (default: true).
  * `-j, --json`: Output machine-readable JSON summary.
* **Example:**
  ```bash
  # 1-Click permanent machine:
  molab permanent nb_emuqXoWkVed6jPNZxND7eo

  # Auto-discover first free Blackwell pod and make permanent:
  molab permanent
  ```

---

## 14. 100% On-MoLab Storage Vault (v2.3)

### `molab vault <subcommand> <notebook_id> [args]`
Permanent workspace persistence stored directly inside `/marimo/notebook.py` metadata in MoLab's cloud database. **Zero phone storage and zero third-party cloud accounts required.**

* **Subcommands:**
  * `pack <id>`: Compress pod `/workspace` into self-extracting Marimo cell (`--source-dir <dir>`, `--max-size <mb>`, `-j, --json`).
  * `unpack <id>`: Extract stored vault archive directly into `/workspace` (`--target-dir <dir>`, `-j, --json`).
  * `inspect <id>`: Check if notebook contains an active vault and report size (`-j, --json`).
* **Example:**
  ```bash
  # Pack workspace into notebook metadata on MoLab:
  molab vault pack nb_emuqXoWkVed6jPNZxND7eo

  # Inspect vault on MoLab:
  molab vault inspect nb_emuqXoWkVed6jPNZxND7eo

  # Extract files back to /workspace:
  molab vault unpack nb_emuqXoWkVed6jPNZxND7eo
  ```
