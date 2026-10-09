"""
Unit tests for TransferManager, checksumming, and manifest comparison.
"""

import io
import os
import tarfile
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from molab_cli.exceptions import FileTransferError
from molab_cli.transfer import TransferManager, calculate_local_sha256, safe_extract_tar


def test_calculate_local_sha256():
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        f.write("test content for hashing")
        f_path = f.name

    try:
        hash_val = calculate_local_sha256(f_path)
        assert len(hash_val) == 64
        assert isinstance(hash_val, str)
    finally:
        os.remove(f_path)


def test_build_local_manifest():
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create test files
        f1 = os.path.join(tmpdir, "file1.txt")
        f2 = os.path.join(tmpdir, "subdir", "file2.txt")
        os.makedirs(os.path.dirname(f2), exist_ok=True)

        with open(f1, "w") as fp:
            fp.write("content 1")
        with open(f2, "w") as fp:
            fp.write("content 2")

        session_mock = MagicMock()
        tm = TransferManager(session_mock)
        manifest = tm.build_local_manifest(tmpdir)

        assert "file1.txt" in manifest
        assert os.path.join("subdir", "file2.txt") in manifest
        assert manifest["file1.txt"]["size"] == len("content 1")
        assert len(manifest["file1.txt"]["sha256"]) == 64


def test_safe_extract_tar_rejects_path_traversal():
    """Ensure safe_extract_tar blocks directory traversal attacks."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tar_path = os.path.join(tmpdir, "evil.tar.gz")
        extract_dir = os.path.join(tmpdir, "extracted")
        os.makedirs(extract_dir, exist_ok=True)

        # Create malicious tarball with ../ escaped file
        with tarfile.open(tar_path, "w:gz") as tar:
            data = b"malicious payload"
            ti = tarfile.TarInfo(name="../escape.txt")
            ti.size = len(data)
            tar.addfile(ti, io.BytesIO(data))

        with pytest.raises(FileTransferError, match="Refusing to extract path-traversing"):
            safe_extract_tar(tar_path, extract_dir)

        # Ensure escaped file was never written outside extraction dir
        assert not os.path.exists(os.path.join(tmpdir, "escape.txt"))


def test_download_checksum_mismatch_preserves_destination():
    """Verify that if downloaded file checksum mismatches, existing destination is preserved."""
    with tempfile.TemporaryDirectory() as tmpdir:
        dest_file = os.path.join(tmpdir, "important_dataset.csv")
        with open(dest_file, "w") as f:
            f.write("ORIGINAL_CONTENT")

        session_mock = MagicMock()
        session_mock.base_url = "https://mock.pod.molab.run"
        session_mock.auth_token = "mock_token"

        tm = TransferManager(session_mock)
        # Remote claims expected hash is 'aaaaaaaa...'
        tm.get_remote_file_sha256 = MagicMock(return_value="a" * 64)

        # Mock curl downloading corrupted data
        def mock_curl(cmd, **kwargs):
            out_idx = cmd.index("-o") + 1
            temp_path = cmd[out_idx]
            with open(temp_path, "w") as f:
                f.write("CORRUPTED_STREAM")
            res = MagicMock()
            res.returncode = 0
            return res

        with patch("subprocess.run", side_effect=mock_curl):
            with pytest.raises(FileTransferError, match="Checksum mismatch"):
                tm.download_file("/workspace/remote.csv", local_path=dest_file, verify_checksum=True)

        # Crucial assertion: original file content is intact!
        with open(dest_file, "r") as f:
            assert f.read() == "ORIGINAL_CONTENT"


def test_download_network_failure_preserves_destination():
    """Verify that if curl download fails, existing destination is preserved and temp file cleaned."""
    with tempfile.TemporaryDirectory() as tmpdir:
        dest_file = os.path.join(tmpdir, "existing.bin")
        with open(dest_file, "w") as f:
            f.write("MY_DATA")

        session_mock = MagicMock()
        session_mock.base_url = "https://mock.pod.molab.run"
        session_mock.auth_token = "mock_token"

        tm = TransferManager(session_mock)
        tm.get_remote_file_sha256 = MagicMock(return_value=None)

        def mock_failing_curl(cmd, **kwargs):
            res = MagicMock()
            res.returncode = 28
            res.stderr = "Operation timed out"
            return res

        with patch("subprocess.run", side_effect=mock_failing_curl):
            with pytest.raises(FileTransferError, match="Failed to download"):
                tm.download_file("/workspace/remote.bin", local_path=dest_file)

        # Existing file is preserved
        with open(dest_file, "r") as f:
            assert f.read() == "MY_DATA"


def test_build_remote_manifest_distinguishes_not_found_vs_error():
    """Verify build_remote_manifest returns empty dict for not found, but raises on errors."""
    session_mock = MagicMock()
    tm = TransferManager(session_mock)

    # 1. Not found
    session_mock.execute_command.return_value = "___MANIFEST_NOT_FOUND___"
    res = tm.build_remote_manifest("/workspace/nonexistent")
    assert res == {}

    # 2. Execution error
    session_mock.execute_command.return_value = "___MANIFEST_ERROR___Permission denied"
    with pytest.raises(FileTransferError, match="Remote error building manifest"):
        tm.build_remote_manifest("/workspace/protected")

    # 3. Crash / unhandled output
    session_mock.execute_command.return_value = "bash: python3: command not found"
    with pytest.raises(FileTransferError, match="Failed to query remote directory manifest"):
        tm.build_remote_manifest("/workspace/corrupt")
