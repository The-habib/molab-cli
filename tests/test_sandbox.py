"""
Unit tests for CoreWeave sandbox session protocol and telemetry parsing.
"""

from unittest.mock import MagicMock, patch
import pytest
from molab_cli.sandbox import SandboxSession


def test_sandbox_session_initialization():
    sess = SandboxSession("nb_123456")
    assert sess.notebook_id == "nb_123456"

    sess_no_prefix = SandboxSession("123456")
    assert sess_no_prefix.notebook_id == "nb_123456"


def test_get_terminal_size():
    sess = SandboxSession("nb_test")
    lines, cols = sess._get_terminal_size()
    assert isinstance(lines, int) and lines > 0
    assert isinstance(cols, int) and cols > 0


def test_install_packages_command_building():
    sess = SandboxSession("nb_test")
    with patch.object(sess, "execute_command", return_value="installed") as mock_exec:
        sess.install_packages(["torch", "transformers"])
        mock_exec.assert_called_once()
        args, kwargs = mock_exec.call_args
        assert "uv pip install torch transformers" in args[0]


def test_get_direct_http_endpoint_priority():
    # 1. Explicit instance attribute
    sess = SandboxSession("nb_test", direct_endpoint="https://custom.endpoint.com/v1")
    assert sess.get_direct_http_endpoint() == "https://custom.endpoint.com/v1"

    # 2. MOLAB_API_BASE env var
    sess2 = SandboxSession("nb_test")
    with patch.dict("os.environ", {"MOLAB_API_BASE": "https://env.endpoint.com/v1"}):
        assert sess2.get_direct_http_endpoint() == "https://env.endpoint.com/v1"

    # 3. Active tunnel check
    sess3 = SandboxSession("nb_test")
    with patch("molab_cli.tunnel.PublicTunnelManager.get_active_tunnel", return_value={"public_url": "https://active.trycloudflare.com", "openai_base_url": "https://active.trycloudflare.com/v1"}):
        with patch.dict("os.environ", {}, clear=True):
            assert sess3.get_direct_http_endpoint() == "https://active.trycloudflare.com/v1"


@pytest.mark.asyncio
async def test_async_stream_via_http_success():
    sess = SandboxSession("nb_test", direct_endpoint="https://mock.api/v1")
    
    mock_sse_lines = [
        'data: {"choices": [{"delta": {"content": "Hello", "reasoning_content": "thinking"}, "finish_reason": null}]}',
        '',
        'data: {"choices": [{"delta": {"content": " world!"}, "finish_reason": "stop"}]}',
        '',
        'data: [DONE]',
    ]

    class MockStreamResponse:
        def raise_for_status(self):
            pass
        async def aiter_lines(self):
            for line in mock_sse_lines:
                yield line

    class MockAsyncClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        def stream(self, method, url, **kwargs):
            class StreamContext:
                async def __aenter__(self):
                    return MockStreamResponse()
                async def __aexit__(self, *args):
                    pass
            return StreamContext()

    with patch("httpx.AsyncClient", return_value=MockAsyncClient()):
        deltas = []
        async for chunk in sess._async_stream_via_http("https://mock.api/v1", {"messages": []}):
            deltas.append(chunk)

        assert len(deltas) == 2
        assert deltas[0]["content"] == "Hello"
        assert deltas[0]["reasoning_content"] == "thinking"
        assert deltas[1]["content"] == " world!"
        assert deltas[1]["finish_reason"] == "stop"


@pytest.mark.asyncio
async def test_upload_temp_json_payload_in_memory():
    sess = SandboxSession("nb_test")
    sess.sandbox_id = "sb-12345"
    sess.auth_token = "tok-abc"
    sess._resolved = True

    class MockResponse:
        def raise_for_status(self):
            pass

    mock_client = MagicMock()
    mock_post = MagicMock()
    
    async def async_post(*args, **kwargs):
        # Verify files parameter passed in memory as tuple
        files = kwargs.get("files")
        assert "file" in files
        assert files["file"][1] == b'{"test": 123}'
        return MockResponse()

    class MockAsyncClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def post(self, *args, **kwargs):
            return await async_post(*args, **kwargs)

    with patch("httpx.AsyncClient", return_value=MockAsyncClient()):
        req_name = await sess._upload_temp_json_payload(b'{"test": 123}')
        assert req_name.startswith("_req_")
        assert req_name.endswith(".json")


def test_chat_completion_direct_http():
    sess = SandboxSession("nb_test", direct_endpoint="https://mock.api/v1")
    expected_resp = {"choices": [{"message": {"content": "Direct response"}}]}

    class MockResponse:
        def raise_for_status(self):
            pass
        def json(self):
            return expected_resp

    class MockClient:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def post(self, url, **kwargs):
            assert "mock.api/v1/chat/completions" in url
            return MockResponse()

    with patch("httpx.Client", return_value=MockClient()):
        res = sess.chat_completion(messages=[{"role": "user", "content": "hi"}])
        assert res == expected_resp

