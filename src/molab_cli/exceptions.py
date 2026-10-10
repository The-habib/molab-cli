"""
Custom Exception hierarchy for molab-cli with user-friendly remediation hints and error codes.
"""

from typing import Any, Dict, Optional


class MoLabError(Exception):
    """Base exception for all MoLab CLI errors."""

    def __init__(
        self,
        message: str,
        hint: Optional[str] = None,
        code: str = "MOLAB_ERROR",
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.code = code
        self.details = details or {}

    def to_dict(self) -> Dict[str, Any]:
        """Serialize error to machine-readable dictionary."""
        data: Dict[str, Any] = {
            "error": True,
            "code": self.code,
            "message": self.message,
        }
        if self.hint:
            data["hint"] = self.hint
        if self.details:
            data["details"] = self.details
        return data


class AuthError(MoLabError):
    """Raised when authentication credentials are missing, expired, or invalid."""

    def __init__(
        self,
        message: str = "MoLab authentication credentials are missing or expired.",
        hint: Optional[str] = "Run 'molab login' or select 'Account & Authentication' to configure your Clerk cookie.",
        code: str = "AUTH_ERROR",
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message, hint, code=code, details=details)


class ClerkApiError(MoLabError):
    """Raised when Clerk Frontend API returns an error during session minting."""

    def __init__(
        self,
        message: str,
        hint: Optional[str] = "Your __client cookie may have expired. Grab a fresh cookie from clerk.marimo.io.",
        code: str = "CLERK_API_ERROR",
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message, hint, code=code, details=details)


class SandboxOfflineError(MoLabError):
    """Raised when attempting to interact with a stopped or unprovisioned cloud pod."""

    def __init__(
        self,
        notebook_id: str,
        message: Optional[str] = None,
        hint: Optional[str] = None,
        code: str = "SANDBOX_OFFLINE",
        details: Optional[Dict[str, Any]] = None,
    ):
        msg = message or f"Cloud pod for notebook '{notebook_id}' is currently offline."
        h = hint or f"Run 'molab compute {notebook_id} --blackwell' to start the pod on NVIDIA Blackwell."
        d = details or {}
        d["notebook_id"] = notebook_id
        super().__init__(msg, h, code=code, details=d)
        self.notebook_id = notebook_id


class NotebookNotFoundError(MoLabError):
    """Raised when a requested notebook ID cannot be found."""

    def __init__(
        self,
        notebook_id: str,
        hint: Optional[str] = "Run 'molab list' to view all available notebooks in your workspace.",
        code: str = "NOTEBOOK_NOT_FOUND",
        details: Optional[Dict[str, Any]] = None,
    ):
        d = details or {}
        d["notebook_id"] = notebook_id
        super().__init__(f"Notebook '{notebook_id}' was not found in your MoLab workspace.", hint, code=code, details=d)
        self.notebook_id = notebook_id


class FileTransferError(MoLabError):
    """Raised when pushing or pulling files fails."""

    def __init__(
        self,
        message: str,
        hint: Optional[str] = "Check that the remote pod is running and destination path is writable.",
        code: str = "FILE_TRANSFER_ERROR",
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message, hint, code=code, details=details)


class ExecutionError(MoLabError):
    """Raised when remote execution fails."""

    def __init__(
        self,
        message: str,
        exit_code: Optional[int] = None,
        hint: Optional[str] = None,
        code: str = "EXECUTION_ERROR",
        details: Optional[Dict[str, Any]] = None,
    ):
        d = details or {}
        if exit_code is not None:
            d["exit_code"] = exit_code
        super().__init__(message, hint, code=code, details=d)
        self.exit_code = exit_code


class ExecutionTimeoutError(ExecutionError):
    """Raised when a remote command execution exceeds its allocated timeout."""

    def __init__(
        self,
        message: str = "Remote execution timed out.",
        timeout_seconds: Optional[float] = None,
        hint: Optional[str] = "Consider running long tasks via 'molab job submit' or increasing the timeout.",
        details: Optional[Dict[str, Any]] = None,
    ):
        d = details or {}
        if timeout_seconds:
            d["timeout_seconds"] = timeout_seconds
        super().__init__(message, exit_code=124, hint=hint, code="EXECUTION_TIMEOUT", details=d)


class JobNotFoundError(MoLabError):
    """Raised when a queried job ID does not exist."""

    def __init__(
        self,
        job_id: str,
        hint: Optional[str] = "Run 'molab job list' to view active and historic jobs.",
        code: str = "JOB_NOT_FOUND",
        details: Optional[Dict[str, Any]] = None,
    ):
        d = details or {}
        d["job_id"] = job_id
        super().__init__(f"Job '{job_id}' not found.", hint, code=code, details=d)
        self.job_id = job_id


class JobError(MoLabError):
    """Raised when a background job lifecycle error occurs."""

    def __init__(
        self,
        message: str,
        hint: Optional[str] = None,
        code: str = "JOB_ERROR",
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message, hint, code=code, details=details)


class CapabilityUnsupportedError(MoLabError):
    """Raised when an operation is attempted that the environment or pod does not support."""

    def __init__(
        self,
        capability: str,
        message: Optional[str] = None,
        hint: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        msg = message or f"Capability '{capability}' is unsupported in the current environment."
        h = hint or "Run 'molab doctor' to inspect supported capabilities."
        d = details or {}
        d["capability"] = capability
        super().__init__(msg, h, code="CAPABILITY_UNSUPPORTED", details=d)
        self.capability = capability


class ServiceError(MoLabError):
    """Raised when managing a remote model server or tunnel service fails."""

    def __init__(
        self,
        message: str,
        hint: Optional[str] = None,
        code: str = "SERVICE_ERROR",
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message, hint, code=code, details=details)


class ValidationError(MoLabError):
    """Raised when user parameters or workload manifests fail validation."""

    def __init__(
        self,
        message: str,
        hint: Optional[str] = None,
        code: str = "VALIDATION_ERROR",
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message, hint, code=code, details=details)


class BatchNotFoundError(MoLabError):
    """Raised when a requested batch workload ID cannot be found."""

    def __init__(
        self,
        batch_id: str,
        hint: Optional[str] = "Run 'molab batch list' to view active and completed batches.",
        code: str = "BATCH_NOT_FOUND",
        details: Optional[Dict[str, Any]] = None,
    ):
        d = details or {}
        d["batch_id"] = batch_id
        super().__init__(f"Batch '{batch_id}' not found.", hint, code=code, details=d)
        self.batch_id = batch_id


class SchedulingError(MoLabError):
    """Raised when no suitable pod meets workload requirements or scheduling fails."""

    def __init__(
        self,
        message: str,
        hint: Optional[str] = "Inspect available pods with 'molab free' or reduce task VRAM requirements.",
        code: str = "SCHEDULING_ERROR",
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message, hint, code=code, details=details)


class MissingPodError(MoLabError):
    """Raised when an operation requires an active notebook pod but none was specified or running."""

    def __init__(
        self,
        message: str = "No active Blackwell pod specified or found running.",
        hint: Optional[str] = "Run 'molab list' to see all notebooks, or 'molab compute <id> --blackwell' to start a pod.",
        code: str = "MISSING_POD",
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message, hint, code=code, details=details)


def redact_sensitive_text(text: str) -> str:
    """Sanitize sensitive cookies, JWT tokens, and API keys from error messages."""
    import re
    if not text:
        return text
    # Redact __client cookie
    text = re.sub(r'(__client=)[^;\s]+', r'\1[REDACTED]', text)
    # Redact session tokens
    text = re.sub(r'(token=)[^&\s]+', r'\1[REDACTED]', text)
    # Redact Bearer JWTs
    text = re.sub(r'(Bearer\s+)[A-Za-z0-9-_=]+\.[A-Za-z0-9-_=]+\.?[A-Za-z0-9-_.+/=]*', r'\1[REDACTED_JWT]', text)
    # Redact sk-molab-... or other sk-... virtual keys
    text = re.sub(r'(sk-[a-zA-Z0-9_-]{8})[a-zA-Z0-9_-]+', r'\1...[REDACTED]', text)
    return text


def classify_exception(e: Exception) -> MoLabError:
    """Classify arbitrary exceptions into structured MoLabError with actionable hints."""
    if isinstance(e, MoLabError):
        # Sanitize message
        e.message = redact_sensitive_text(e.message)
        return e

    msg = redact_sensitive_text(str(e))

    # Detect sandbox offline in generic RuntimeError strings
    if "Could not locate active sandbox session" in msg or "sandbox session" in msg.lower():
        import re
        m = re.search(r'nb_[a-zA-Z0-9]+', msg)
        nb_id = m.group(0) if m else "unknown"
        return SandboxOfflineError(
            notebook_id=nb_id,
            message=f"Cloud pod for notebook '{nb_id}' is offline or initializing.",
            hint=f"Start the pod on Blackwell with: molab compute {nb_id} --blackwell",
        )

    # Detect HTTP Errors
    if hasattr(e, "code") and isinstance(getattr(e, "code"), int):
        code = getattr(e, "code")
        if code in (401, 403):
            return AuthError(
                message=f"Authentication rejected by MoLab (HTTP {code}).",
                hint="Your session cookie may be expired. Run 'molab login' to re-authenticate.",
            )
        elif code == 404:
            return MoLabError(
                message=f"Requested cloud resource not found (HTTP 404).",
                hint="Verify resource identifier with 'molab list' or 'molab ps'.",
                code="NOT_FOUND",
            )
        elif code in (500, 502, 503, 504):
            return MoLabError(
                message=f"MoLab remote cloud service error (HTTP {code}).",
                hint="The CoreWeave container or Marimo host may be restarting. Try again shortly.",
                code="SERVICE_UNAVAILABLE",
            )

    # Detect Timeout Errors
    if "timeout" in msg.lower() or "timed out" in msg.lower():
        return ExecutionTimeoutError(
            message=f"Operation timed out: {msg}",
            hint="For heavy operations, consider 'molab job submit' or increase the command --timeout.",
        )

    # Detect Connection Errors
    if "connection refused" in msg.lower() or "nodename nor servname" in msg.lower():
        return MoLabError(
            message=f"Failed to connect to cloud endpoint: {msg}",
            hint="Check your network connection and verify the sandbox container is running via 'molab ps'.",
            code="CONNECTION_FAILED",
        )

    # Fallback to sanitized MoLabError
    return MoLabError(
        message=msg or "An unexpected internal error occurred.",
        hint="Inspect full system health with 'molab doctor' or test with 'molab status'.",
        code="UNEXPECTED_ERROR",
    )


