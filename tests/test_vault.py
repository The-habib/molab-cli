"""
Unit tests for MoLabVault in-notebook persistent storage subsystem.
"""

from unittest.mock import MagicMock, patch
import pytest

from molab_cli.vault import MoLabVault, VaultError


def test_vault_pack_success():
    vault = MoLabVault("nb_test_123")
    with patch.object(vault.session, "resolve"), \
         patch.object(vault.session, "execute_command", return_value="SUCCESS:5:1024:4096"):

        res = vault.pack_workspace()
        assert res["notebook_id"] == "nb_test_123"
        assert res["status"] == "packed_in_notebook"
        assert res["files_packed"] == 5
        assert res["compressed_bytes"] == 1024
        assert res["uncompressed_bytes"] == 4096
        assert "100% on MoLab" in res["storage_location"]


def test_vault_pack_too_large():
    vault = MoLabVault("nb_test_123")
    with patch.object(vault.session, "resolve"), \
         patch.object(vault.session, "execute_command", return_value="TOO_LARGE:30000000"):

        with pytest.raises(VaultError) as exc_info:
            vault.pack_workspace(max_size_mb=25.0)
        assert "exceeds" in str(exc_info.value)


def test_vault_unpack_success():
    vault = MoLabVault("nb_test_123")
    with patch.object(vault.session, "resolve"), \
         patch.object(vault.session, "execute_command", return_value="SUCCESS:5:1024"):

        res = vault.unpack_workspace()
        assert res["notebook_id"] == "nb_test_123"
        assert res["status"] == "unpacked"
        assert res["files_unpacked"] == 5
        assert res["archive_bytes"] == 1024


def test_vault_unpack_no_vault():
    vault = MoLabVault("nb_test_123")
    with patch.object(vault.session, "resolve"), \
         patch.object(vault.session, "execute_command", return_value="NO_VAULT_FOUND"):

        with pytest.raises(VaultError) as exc_info:
            vault.unpack_workspace()
        assert "No vault found" in str(exc_info.value)


def test_vault_inspect_present():
    vault = MoLabVault("nb_test_123")
    with patch.object(vault.session, "resolve"), \
         patch.object(vault.session, "execute_command", return_value="EXISTS:400"):

        res = vault.inspect_vault()
        assert res["has_vault"] is True
        assert res["approx_size_bytes"] == 300
        assert "MoLab Cloud Database" in res["storage_location"]


def test_vault_inspect_absent():
    vault = MoLabVault("nb_test_123")
    with patch.object(vault.session, "resolve"), \
         patch.object(vault.session, "execute_command", return_value="NONE"):

        res = vault.inspect_vault()
        assert res["has_vault"] is False
