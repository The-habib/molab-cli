# MoLab CLI — Error Handling & Reliability Architecture

## 1. Core Error Philosophy

Every error in MoLab CLI must answer four questions for the user:
1. **What failed?** (Clear, human-readable summary without jargon).
2. **Which resource was affected?** (Specific pod ID, file path, or endpoint).
3. **Why did it fail?** (Meaningful root cause, sanitized of sensitive tokens).
4. **What should the user do next?** (Exact CLI command or actionable remediation step).

Raw stack traces are strictly suppressed during ordinary CLI execution and reserved for `--debug` modes.

---

## 2. Exception Hierarchy

All application errors inherit from `MoLabError` in `src/molab_cli/exceptions.py`:

```
MoLabError (message, hint, code, details)
├── AuthError (AUTH_ERROR)
├── ClerkApiError (CLERK_API_ERROR)
├── SandboxOfflineError (SANDBOX_OFFLINE)
├── MissingPodError (MISSING_POD)
├── NotebookNotFoundError (NOTEBOOK_NOT_FOUND)
├── FileTransferError (FILE_TRANSFER_ERROR)
├── ExecutionError (EXECUTION_ERROR)
│   └── ExecutionTimeoutError (EXECUTION_TIMEOUT)
├── JobNotFoundError (JOB_NOT_FOUND)
├── JobError (JOB_ERROR)
├── BatchNotFoundError (BATCH_NOT_FOUND)
├── SchedulingError (SCHEDULING_ERROR)
├── ValidationError (VALIDATION_ERROR)
├── ServiceError (SERVICE_ERROR)
└── CapabilityUnsupportedError (CAPABILITY_UNSUPPORTED)
```

---

## 3. Dynamic Exception Classification (`classify_exception`)

When lower-level Python or network exceptions are raised (e.g. `urllib.error.HTTPError`, `socket.timeout`, `ConnectionRefusedError`, `RuntimeError`), `classify_exception` inspects the error and wraps it in a typed `MoLabError`:

| Underlying Exception | Classified As | Actionable Remediation Hint |
| :--- | :--- | :--- |
| HTTP 401 / 403 | `AuthError` | `"Run 'molab login' to configure or refresh your Clerk cookie."` |
| "sandbox session" missing | `SandboxOfflineError` | `"Run 'molab compute <id> --blackwell' to start the pod."` |
| Timeout / Socket timeout | `ExecutionTimeoutError` | `"Run heavy tasks via 'molab job submit' or increase --timeout."` |
| HTTP 404 | `MoLabError(NOT_FOUND)` | `"Verify resource identifier with 'molab list' or 'molab ps'."` |
| Connection Refused | `MoLabError(CONNECTION_FAILED)` | `"Verify the sandbox container is active via 'molab ps'."` |

---

## 4. Secret Redaction Protocol

Before any error message or log is formatted, it passes through `redact_sensitive_text()`:
* **Cookies:** `__client=...` ➔ `__client=[REDACTED]`
* **URL Tokens:** `token=...` ➔ `token=[REDACTED]`
* **Bearer JWTs:** `Bearer eyJ...` ➔ `Bearer [REDACTED_JWT]`
* **Virtual API Keys:** `sk-molab-live-12345...` ➔ `sk-molab-li...[REDACTED]`

---

## 5. Dual-Mode Presentation (`handle_cli_error`)

* **Interactive TTY:** Rendered as an eye-catching Rich error card with a red border, bold title, error code chip, sanitized description, and yellow suggested action box.
* **Non-Interactive JSON (`-j, --json`):** Formatted as structured JSON on stdout:
  ```json
  {
    "error": true,
    "code": "SANDBOX_OFFLINE",
    "message": "Cloud pod for notebook 'nb_123' is offline or stopped.",
    "hint": "Start the pod on Blackwell with: molab compute nb_123 --blackwell",
    "details": { "notebook_id": "nb_123" }
  }
  ```
  Exits with non-zero exit code `1`.
