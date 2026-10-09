# MoLab Model Context Protocol (MCP) Studio-Grade Guide

The MoLab CLI includes a high-performance Model Context Protocol (MCP) server that exposes **48 typed tools** to AI agents across **Claude Desktop**, **Cursor IDE**, and **Google Antigravity**.

The server implements the Model Context Protocol over standard input/output (`stdio`) and provides direct access to CoreWeave NVIDIA RTX PRO 6000 Blackwell (96GB VRAM) cloud pods.

---

## 1. Quickstart Configuration

### Connecting from Claude Desktop (`claude_desktop_config.json`)
```json
{
  "mcpServers": {
    "molab": {
      "command": "python3",
      "args": ["-m", "molab_cli.mcp"]
    }
  }
}
```

### Connecting from Cursor IDE (`.cursor/mcp.json`)
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

### Direct Antigravity / Agent Stdio Invocation
```bash
python3 -m molab_cli.mcp
```

---

## 2. Tool Catalog by Category (48 Typed Tools)

### Category A: Pod Discovery & Diagnostics
1. **`molab_doctor`**: Audit authentication, network latency, system dependencies (`curl`, `ffmpeg`, `jq`), and active pods.
   * *Arguments:* None.
2. **`molab_capabilities`**: Discover confirmed environment facts, supported GPU architectures, and runtime limits.
   * *Arguments:* None.
3. **`molab_list_pods`**: List all cloud notebooks and CoreWeave sandboxes in workspace.
   * *Arguments:* `status` (optional: "all" | "running").
4. **`molab_get_free_pod`**: Discover the optimal idle Blackwell GPU pod with zero running workloads without touching occupied pods.
   * *Arguments:* None.
5. **`molab_gpu_telemetry`**: Fetch real-time NVML metrics (allocated VRAM, temperature, compute load, SM count).
   * *Arguments:* `notebook_id` (string).
6. **`molab_remote_environment`**: Query remote pod environment specifications (OS, gVisor 4.19, Python 3.13, Node v22, uv, pre-installed AI/ML packages).
   * *Arguments:* `notebook_id` (optional string).
7. **`molab_get_connections`**: Audit active WebSocket client connections to the remote notebook server.
   * *Arguments:* `notebook_id` (string).

### Category B: High-Speed Remote Execution & Transfer
8. **`molab_execute`**: Execute bash commands with exit code trapping, working directory, and environment variables.
   * *Arguments:* `notebook_id` (string), `command` (string), `workdir` (optional string), `timeout_seconds` (optional int).
9. **`molab_push_file`**: Upload files or directories using native Starlette chunked HTTP streaming (bypasses terminal PTY).
   * *Arguments:* `notebook_id` (string), `local_path` (string), `remote_path` (string), `recursive` (optional bool).
10. **`molab_pull_file`**: Download files or directories from pod to local disk using native HTTP streaming.
    * *Arguments:* `notebook_id` (string), `remote_path` (string), `local_path` (string), `recursive` (optional bool).
11. **`molab_sync_directory`**: Delta synchronize a local directory by calculating SHA-256 manifests, transferring only changed files.
    * *Arguments:* `notebook_id` (string), `local_dir` (string), `remote_dir` (string).

### Category C: Asynchronous Job Lifecycle & Artifacts
12. **`molab_job_submit`**: Submit a detached background job with persistent SQLite tracking, auto-log recording, and artifact indexing.
    * *Arguments:* `command` (string), `notebook_id` (optional string), `name` (optional string), `workdir` (optional string).
13. **`molab_job_status`**: Check execution status, exit code, and timestamps for a background job.
    * *Arguments:* `job_id` (string).
14. **`molab_job_logs`**: Retrieve trailing execution logs for a background job.
    * *Arguments:* `job_id` (string), `lines` (optional int).
15. **`molab_job_cancel`**: Terminate a running background job via SIGTERM/SIGKILL.
    * *Arguments:* `job_id` (string).

### Category D: Multi-Pod DAG Batch Orchestration
16. **`molab_batch_validate`**: Validate a multi-task pipeline manifest against DAG dependency schemas.
    * *Arguments:* `manifest` (object).
17. **`molab_batch_submit`**: Submit a multi-task batch pipeline manifest for autonomous multi-pod scheduling.
    * *Arguments:* `manifest` (object).
18. **`molab_batch_status`**: Query batch pipeline state, completed task counts, and failed task diagnostics.
    * *Arguments:* `batch_id` (string).
19. **`molab_batch_cancel`**: Cancel an entire batch pipeline and terminate all remote child processes across all pods.
    * *Arguments:* `batch_id` (string).

### Category E: Native Marimo Kernel & File REST
20. **`molab_usage`**: Query host RAM, cgroup limits, server RAM, kernel RAM, and GPU memory directly via `/api/usage`.
    * *Arguments:* `notebook_id` (string).
21. **`molab_export_notebook`**: Export notebook directly to HTML, Markdown, IPYNB, or Python script via `/api/export`.
    * *Arguments:* `notebook_id` (string), `format` (string: "html" | "markdown" | "ipynb" | "script").
22. **`molab_kernel_eval`**: Execute Python code directly inside the live Marimo kernel without PTY buffering.
    * *Arguments:* `notebook_id` (string), `code` (string).
23. **`molab_kernel_status`**: Query running vs idle state of the remote Python kernel.
    * *Arguments:* `notebook_id` (string).
24. **`molab_kernel_restart`**: Soft-restart the remote Marimo Python kernel without dropping container state.
    * *Arguments:* `notebook_id` (string).
25. **`molab_kernel_interrupt`**: Send interrupt signal (SIGINT) to running cell computation in the kernel.
    * *Arguments:* `notebook_id` (string).
26. **`molab_file_list`**: List files and directories directly over native HTTP JSON endpoint.
    * *Arguments:* `notebook_id` (string), `path` (optional string).
27. **`molab_file_details`**: Fetch metadata, mime type, and readable text contents of a file on the remote pod.
    * *Arguments:* `notebook_id` (string), `path` (string).
28. **`molab_file_search`**: Fast server-side recursive file and directory search.
    * *Arguments:* `notebook_id` (string), `query` (string).
29. **`molab_pkg_list`**: List installed Python packages directly from Marimo package manager.
    * *Arguments:* `notebook_id` (string).
30. **`molab_get_thumbnail`**: Generate visual Open Graph SVG thumbnail of the notebook canvas via `/og/thumbnail`.
    * *Arguments:* `notebook_id` (string), `output_path` (optional string).

### Category F: Infinite Permanence, Keepalive & 100% On-MoLab Vault
31. **`molab_make_permanent`**: Complete 1-click infinite pod permanence (prevents idle reaper, acquires Termux wake-lock, launches in-pod guard, packs vault 100% on MoLab).
    * *Arguments:* `notebook_id` (optional string), `interval` (optional int), `auto_pack` (optional bool).
32. **`molab_keepalive_start`**: Start autonomous background anti-idle heartbeat daemon to defeat the 30-minute reaper.
    * *Arguments:* `notebook_id` (string), `interval` (optional int), `max_hours` (optional int), `auto_restore` (optional bool).
33. **`molab_keepalive_stop`**: Stop running keepalive daemon.
    * *Arguments:* `notebook_id` (string).
34. **`molab_keepalive_status`**: Inspect live PID, heartbeat counter, and auto-restore status.
    * *Arguments:* `notebook_id` (string).
35. **`molab_vault_pack`**: Compress `/workspace` into self-extracting Marimo cell stored in MoLab cloud database (**0 bytes local phone storage**).
    * *Arguments:* `notebook_id` (string), `source_dir` (optional string), `max_size_mb` (optional int).
36. **`molab_vault_unpack`**: Extract stored vault archive directly back into pod `/workspace`.
    * *Arguments:* `notebook_id` (string), `target_dir` (optional string).
37. **`molab_vault_inspect`**: Audit whether an in-notebook vault exists and return stored size.
    * *Arguments:* `notebook_id` (string).
38. **`molab_snapshot_create`**: Create compressed archive of pod `/workspace` and stream to local persistent storage.
    * *Arguments:* `notebook_id` (string), `name` (optional string), `remote_path` (optional string).
39. **`molab_snapshot_restore`**: Restore local snapshot archive back into pod `/workspace`.
    * *Arguments:* `notebook_id` (string), `snapshot_id` (optional string), `remote_path` (optional string).
40. **`molab_snapshot_list`**: List saved local workspace snapshots and checkpoints.
    * *Arguments:* `notebook_id` (optional string).

### Category G: Multi-Gigabit Cloud Storage Bridges
41. **`molab_storage_backup`**: Sync pod directory directly to remote cloud bucket (S3/R2/B2/GCS) via rclone at 10Gbps+.
    * *Arguments:* `notebook_id` (string), `remote_dest` (string), `source_dir` (optional string), `flags` (optional string).
42. **`molab_storage_restore`**: Restore pod directory directly from remote cloud bucket via rclone at 10Gbps+.
    * *Arguments:* `notebook_id` (string), `remote_source` (string), `target_dir` (optional string), `flags` (optional string).
43. **`molab_storage_hf_pull`**: Stream model weights or datasets directly from Hugging Face Hub to pod at multi-gigabit speeds.
    * *Arguments:* `notebook_id` (string), `repo_id` (string), `dest` (optional string), `filename` (optional string), `token` (optional string).

### Category H: Workload Registry & Services
44. **`molab_run_workload`**: Launch pre-configured AI workload template (`video-enhance-4k`, `whisper-transcribe`, `vllm-serve`) with validated resource allocations.
    * *Arguments:* `workload_name` (string), `notebook_id` (optional string), `params` (optional object).
45. **`molab_service_status`**: Verify application-level health and port binding for model servers running on pod.
    * *Arguments:* `notebook_id` (string), `port` (optional int), `endpoint` (optional string).

### Category I: MoLab Community Gallery
46. **`molab_gallery_list`**: List 111+ curated community notebook templates and AI recipes from MoLab Gallery.
    * *Arguments:* `limit` (optional int).
47. **`molab_gallery_search`**: Search MoLab gallery recipes by keyword, topic, or tag.
    * *Arguments:* `query` (string).
48. **`molab_gallery_info`**: Fetch metadata, description, and source repository links for a gallery recipe.
    * *Arguments:* `slug` (string).

---

## 3. Real-World Agent Interaction Flow

Here is the exact tool calling sequence for an agent executing a machine learning workload:

```
[Agent] molab_get_free_pod()
  └─► Returns: {"recommended_free_pod": "nb_emuqXoWkVed6jPNZxND7eo"}

[Agent] molab_remote_environment(notebook_id="nb_emuqXoWkVed6jPNZxND7eo")
  └─► Confirms: Python 3.13, PyTorch 2.11, CUDA ready

[Agent] molab_vault_inspect(notebook_id="nb_emuqXoWkVed6jPNZxND7eo")
  └─► Checks: Prior state exists? If yes -> molab_vault_unpack()

[Agent] molab_push_file(notebook_id="nb_emuqXoWkVed6jPNZxND7eo", local_path="./pipeline.py", remote_path="/workspace/pipeline.py")
  └─► Uploads at 100MB/s HTTP streaming

[Agent] molab_job_submit(command="python3 /workspace/pipeline.py", notebook_id="nb_emuqXoWkVed6jPNZxND7eo", name="finetune-run")
  └─► Returns: {"job_id": "job_941a8e2"}

[Agent] molab_vault_pack(notebook_id="nb_emuqXoWkVed6jPNZxND7eo")
  └─► Persists workspace permanently to MoLab cloud metadata
```
