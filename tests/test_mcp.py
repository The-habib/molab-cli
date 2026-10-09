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
