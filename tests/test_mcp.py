"""
Unit tests for Model Context Protocol (MCP) JSON-RPC 2.0 Server.
"""

from unittest.mock import MagicMock, patch

from molab_cli.mcp import MoLabMCPServer


def test_mcp_initialize():
    server = MoLabMCPServer()
    req = {
        "jsonrpc": "2.0",
        "id": 100,
        "method": "initialize",
        "params": {}
    }
    resp = server.handle_request(req)
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 100
    assert resp["result"]["protocolVersion"] == "2024-11-05"
    assert resp["result"]["serverInfo"]["name"] == "molab-mcp"


def test_mcp_tools_list():
    server = MoLabMCPServer()
    req = {
        "jsonrpc": "2.0",
        "id": 101,
        "method": "tools/list",
        "params": {}
    }
    resp = server.handle_request(req)
    tools = resp["result"]["tools"]
    tool_names = [t["name"] for t in tools]

    assert "molab_doctor" in tool_names
    assert "molab_capabilities" in tool_names
    assert "molab_list_pods" in tool_names
    assert "molab_get_free_pod" in tool_names
    assert "molab_execute" in tool_names
    assert "molab_push_file" in tool_names
    assert "molab_job_submit" in tool_names
    assert "molab_job_status" in tool_names


def test_mcp_tool_call_doctor():
    server = MoLabMCPServer()
    with patch("molab_cli.mcp.run_doctor", return_value={"status": "HEALTHY", "checks": []}):
        req = {
            "jsonrpc": "2.0",
            "id": 102,
            "method": "tools/call",
            "params": {
                "name": "molab_doctor",
                "arguments": {}
            }
        }
        resp = server.handle_request(req)
        assert resp["id"] == 102
        assert resp["result"]["isError"] is False
        assert "HEALTHY" in resp["result"]["content"][0]["text"]


def test_mcp_backend_tools():
    server = MoLabMCPServer()
    req = {
        "jsonrpc": "2.0",
        "id": 103,
        "method": "tools/list",
        "params": {}
    }
    resp = server.handle_request(req)
    tools = resp["result"]["tools"]
    tool_names = [t["name"] for t in tools]

    assert "molab_usage" in tool_names
    assert "molab_export_notebook" in tool_names
    assert "molab_kernel_eval" in tool_names
    assert "molab_kernel_status" in tool_names
    assert "molab_file_list" in tool_names
    assert "molab_file_details" in tool_names
    assert "molab_file_search" in tool_names
    assert "molab_pkg_list" in tool_names
    assert len(tools) >= 29

    with patch("molab_cli.backend.MarimoBackendClient.get_usage", return_value={"total_gb": 160.0}), \
         patch("molab_cli.sandbox.SandboxSession.resolve"):
        call_req = {
            "jsonrpc": "2.0",
            "id": 104,
            "method": "tools/call",
            "params": {
                "name": "molab_usage",
                "arguments": {"notebook_id": "nb_test_123"}
            }
        }
        call_resp = server.handle_request(call_req)
        assert call_resp["id"] == 104
        assert call_resp["result"]["isError"] is False
        assert "160.0" in call_resp["result"]["content"][0]["text"]
