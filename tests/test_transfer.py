"""
Unit tests for TransferManager, checksumming, and manifest comparison.
"""

import os
import tempfile
from unittest.mock import MagicMock

from molab_cli.transfer import TransferManager, calculate_local_sha256


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
