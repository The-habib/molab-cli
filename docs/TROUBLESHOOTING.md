# MoLab CLI Troubleshooting & FAQ

## 1. File Transfer Issues

### Symptom: `molab push` or `molab pull` hangs or fails with timeout
* **Cause (Old CLI):** Attempting to transfer files over interactive WebSocket PTY using base64. Terminal input buffer overflows at 4096 bytes.
* **Solution:** Use the upgraded `molab push` and `molab pull` commands, which stream directly over HTTP/2 using the native Marimo `/api/files/create` and `/api/files/download` endpoints.

### Symptom: `HTTP 403 Forbidden` on file upload/download
* **Cause:** The sandbox pod restarted, resulting in a new authorization token.
* **Solution:** Run any status/inspect command or call `SandboxSession.resolve(force_refresh=True)` to fetch the newly minted token.

---

## 2. Pod & Sandbox Issues

### Symptom: `molab exec` times out after 30 seconds
* **Cause:** Foreground synchronous execution of a long compute job.
* **Solution:** Run compute tasks in the background with `nohup`:
  ```bash
  molab exec <id> "nohup <command> > /workspace/log.txt 2>&1 &"
  ```
  Monitor progress by reading the log with `tail -n 25 /workspace/log.txt`.

### Symptom: Pod shows as "STOPPED" in `molab list`
* **Cause:** Ephemeral CoreWeave pods have idle timeouts (typically 30–60 minutes without user input).
* **Solution:** Restart the pod using:
  ```bash
  molab compute <notebook_id> --blackwell
  ```
  This provisions a new container with full GPU acceleration.

---

## 3. Localhost Bridge Issues

### Symptom: `molab forward` fails on large context requests (>4KB)
* **Cause:** Terminal buffer limitation when sending large POST requests over PTY.
* **Solution:** The latest `LocalHttpForwarder` automatically routes POST bodies larger than 3000 bytes through temporary HTTP file streaming, preventing PTY buffer overflow.
