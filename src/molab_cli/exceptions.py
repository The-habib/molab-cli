"""
Custom Exception hierarchy for molab-cli with user-friendly remediation hints.
"""

from typing import Optional


class MoLabError(Exception):
    """Base exception for all MoLab CLI errors."""

    def __init__(self, message: str, hint: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.hint = hint


class AuthError(MoLabError):
    """Raised when authentication credentials are missing, expired, or invalid."""

    def __init__(
        self,
        message: str = "MoLab authentication credentials are missing or expired.",
        hint: Optional[str] = "Run 'molab login' or select 'Account & Authentication' to configure your Clerk cookie.",
    ):
        super().__init__(message, hint)


class ClerkApiError(MoLabError):
    """Raised when Clerk Frontend API returns an error during session minting."""

    def __init__(
        self,
        message: str,
        hint: Optional[str] = "Your __client cookie may have expired. Grab a fresh cookie from clerk.marimo.io.",
    ):
        super().__init__(message, hint)


class SandboxOfflineError(MoLabError):
    """Raised when attempting to interact with a stopped or unprovisioned cloud pod."""

    def __init__(
        self,
        notebook_id: str,
        message: Optional[str] = None,
        hint: Optional[str] = None,
    ):
        msg = message or f"Cloud pod for notebook '{notebook_id}' is currently offline."
        h = hint or f"Run 'molab compute {notebook_id} --blackwell' to start the pod on NVIDIA Blackwell."
        super().__init__(msg, h)
        self.notebook_id = notebook_id


class NotebookNotFoundError(MoLabError):
    """Raised when a requested notebook ID cannot be found."""

    def __init__(
        self,
        notebook_id: str,
        hint: Optional[str] = "Run 'molab list' to view all available notebooks in your workspace.",
    ):
        super().__init__(f"Notebook '{notebook_id}' was not found in your MoLab workspace.", hint)
        self.notebook_id = notebook_id


class FileTransferError(MoLabError):
    """Raised when pushing or pulling files fails."""

    def __init__(
        self,
        message: str,
        hint: Optional[str] = "Check that the remote pod is running and destination path is writable.",
    ):
        super().__init__(message, hint)
