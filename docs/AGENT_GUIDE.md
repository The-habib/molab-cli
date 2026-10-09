# Autonomous Agent Guide for MoLab & Blackwell GPU

This guide contains essential operational knowledge and runbooks for AI agents working in Termux with access to MoLab cloud GPU pods.

---

## 1. Golden Rules for Agents

1. **Check Workload Before Touching Pods:**
   * Always run `molab free --json` first.
   * Check if a pod has an active server or high VRAM allocation.
   * If a user says "use the second/free pod", never touch an occupied pod.
2. **Never Buffer Entire Files in Memory on the Phone:**
   * Termux runs on Android with aggressive OOM killers.
   * Always stream directly from/to disk using `molab push` and `molab pull`.
3. **Avoid PTY WebSocket Commands for Large Data:**
   * `molab exec` goes through an interactive PTY with a 4096-byte input buffer.
   * Never run `echo '<large base64>' | base64 -d`. Use `molab push` instead.
4. **Run Heavy Compute in the Background:**
   * Never execute long-running tasks synchronously in `molab exec` (they time out after 30s).
   * Launch background tasks with `nohup`:
     ```bash
     molab exec <pod_id> "nohup python3 /workspace/job.py > /workspace/job.log 2>&1 &"
     ```
   * Monitor output by reading the log file:
     ```bash
     molab exec <pod_id> "tail -n 25 /workspace/job.log ; ps aux | grep job"
     ```
5. **Clean Up Ephemeral Storage:**
   * Remove scratch directories on the pod when done (`rm -rf /workspace/scratch`).

---

## 2. Programmatic Scripting Snippets

### Automated Pod Selection in Python:
```python
from molab_cli.client import MoLabClient
from molab_cli.sandbox import SandboxSession

client = MoLabClient()
running = client.list_running_sandboxes()
notebooks = {nb["id"]: nb for nb in client.list_notebooks()}

free_pod_id = None
for nb_id in running:
    if notebooks.get(nb_id, {}).get("gpu") == "rtxp6000":
        session = SandboxSession(nb_id, client=client)
        status = session.get_workload_status()
        if not status["is_occupied"]:
            free_pod_id = nb_id
            break

print("Selected Pod:", free_pod_id)
```

### High-Level Python SDK Orchestration:
```python
from molab_cli.sdk import MoLabSDK

sdk = MoLabSDK()

# 1. Discover capabilities and health
doctor_report = sdk.doctor()
capabilities = sdk.capabilities()

# 2. Acquire free idle 96GB Blackwell pod
pod = sdk.get_free_pod()
if not pod:
    raise RuntimeError("No idle Blackwell GPU pod available")

# 3. Delta synchronize project directory with SHA-256 manifests
pod.sync("./my_project", "/workspace/my_project")

# 4. Submit background job tracked in local SQLite
job = pod.submit_job(
    command="python3 /workspace/my_project/train.py --epochs 10",
    name="lora-training-run",
    workdir="/workspace/my_project"
)
print("Submitted Job:", job["id"])

# 5. Poll status and download artifacts
status = sdk.get_job(job["id"])
if status["status"] == "COMPLETED":
    sdk.job_manager.download_artifacts(job["id"], "./downloaded_artifacts")
```

### Model Context Protocol (MCP) Integration:
Any MCP-compatible client (Claude Desktop, Cursor, Antigravity) can connect directly to MoLab by running:
```json
{
  "mcpServers": {
    "molab": {
      "command": "molab",
      "args": ["mcp"]
    }
  }
}
```
Exposes 15 typed tools for pod discovery, command execution, streaming transfers, and job tracking.

---

## 3. Extensibility Mandate

`molab-cli` is stored at `~/molab-cli/` in editable mode.
If you need additional flags, custom streaming logic, or specialized diagnostic tools:
1. Edit the relevant file in `~/molab-cli/src/molab_cli/`.
2. Run `pytest ~/molab-cli/tests/`.
3. Commit with `git -C ~/molab-cli commit -am "feat: ..."`
Future agent conversations will automatically inherit your upgrades!

