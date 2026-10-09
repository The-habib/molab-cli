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
