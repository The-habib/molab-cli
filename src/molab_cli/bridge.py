"""
MoLab Blackwell AI Bridge for Claude Code.
Provides an Anthropic Messages API (/v1/messages) proxy that bridges Claude Code
directly to the active MoLab Blackwell GPU pod.

Translates live token streams and <think>...</think> reasoning traces into
Anthropic SSE events (thinking_delta) for real-time thinking visualization,
and converts tool calls into Claude Code tool_use blocks.
"""

import os
import sys
import re
import json
import time
import uuid
import logging
import asyncio
from typing import List, Dict, Any, Optional, Tuple
import uvicorn
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from molab_cli.gateway_db import default_gateway_db
from molab_cli.rate_limiter import default_rate_limiter
from molab_cli.sandbox import SandboxSession

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [molab_bridge] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("molab_bridge")

BRIDGE_PORT = int(os.getenv("BRIDGE_PORT", "8000"))
app = FastAPI(title="MoLab Blackwell AI Bridge for Hermes Agent & Claude Code")

# CORS middleware for Open WebUI, LibreChat, and browser integrations
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):
    headers = getattr(exc, "headers", None) or {}
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content=exc.detail, headers=headers)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"message": str(exc.detail), "code": exc.status_code}},
        headers=headers,
    )


def authenticate_and_rate_limit(request: Request, estimated_tokens: int = 100) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """
    Validate API key against GatewayDB and enforce per-key RPM / TPM limits.
    Returns (key_record, rate_limit_headers).
    Raises HTTPException(401) or HTTPException(429).
    """
    auth_header = request.headers.get("authorization", "")
    api_key = None
    if auth_header.startswith("Bearer "):
        api_key = auth_header[7:].strip()
    elif auth_header:
        api_key = auth_header.strip()
    elif "x-api-key" in request.headers:
        api_key = request.headers["x-api-key"].strip()
    elif "api_key" in request.query_params:
        api_key = request.query_params["api_key"].strip()

    client_host = request.client.host if request.client else "127.0.0.1"
    is_local = client_host in ("127.0.0.1", "localhost", "::1", "testclient")
    require_auth = os.getenv("MOLAB_GATEWAY_REQUIRE_AUTH", "1" if not is_local else "0") == "1"

    if not api_key:
        if require_auth:
            raise HTTPException(
                status_code=401,
                detail={"error": {"message": "Missing API key. Provide Authorization header: 'Bearer sk-molab-...'", "type": "authentication_error", "code": 401}}
            )
        return {
            "key_id": "key_default_master",
            "name": "Local Master / Internal",
            "key_prefix": "sk-molab-master...",
            "rpm_limit": 6000,
            "tpm_limit": 6000000,
            "is_active": 1,
        }, {}

    key_record = default_gateway_db.validate_key(api_key)
    if not key_record:
        raise HTTPException(
            status_code=401,
            detail={"error": {"message": "Invalid or revoked API key.", "type": "authentication_error", "code": 401}}
        )

    allowed, reason, rate_headers = default_rate_limiter.check_limit(
        key_id=key_record["key_id"],
        rpm_limit=key_record.get("rpm_limit", 60),
        tpm_limit=key_record.get("tpm_limit", 60000),
        estimated_tokens=estimated_tokens,
    )

    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={"error": {"message": reason or "Rate limit exceeded", "type": "rate_limit_error", "code": 429}},
            headers=rate_headers,
        )

    return key_record, rate_headers

# Cached active session and model
_cached_session: Optional[SandboxSession] = None
_cached_model: Optional[str] = None
_last_session_resolve: float = 0.0


async def get_active_session_async() -> Tuple[SandboxSession, str]:
    """Retrieve or discover active SandboxSession and loaded model asynchronously."""
    global _cached_session, _cached_model, _last_session_resolve

    now = time.time()
    if _cached_session and _cached_model and (now - _last_session_resolve < 60.0):
        return _cached_session, _cached_model

    pod_id = os.getenv("MOLAB_POD_ID")
    if not pod_id:
        pod_id = await asyncio.to_thread(SandboxSession.discover_active_pod)
    if not pod_id:
        raise HTTPException(
            status_code=503,
            detail="No active MoLab Blackwell GPU pod found. Launch or select a pod with 'molab free' or 'molab open'."
        )

    session = SandboxSession(pod_id)
    await asyncio.to_thread(session.resolve)

    active_model = await session.async_get_active_model() or os.getenv("TARGET_MODEL") or "huihui-ai/Qwen2.5-32B-Instruct-abliterated"

    _cached_session = session
    _cached_model = active_model
    _last_session_resolve = now

    return session, active_model


# Alias for backward-compatibility
get_active_session = get_active_session_async


def extract_system_prompt(raw_system: Any) -> str:
    """Extract plain text from Anthropic system prompt format."""
    if not raw_system:
        return ""
    if isinstance(raw_system, str):
        return raw_system
    if isinstance(raw_system, list):
        parts = []
        for block in raw_system:
            if isinstance(block, dict) and "text" in block:
                parts.append(block["text"])
            elif isinstance(block, str):
                parts.append(block)
        return "\n\n".join(parts)
    return str(raw_system)


def extract_content_text(content: Any) -> str:
    """Flatten Anthropic content blocks to message text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                b_type = block.get("type", "")
                if b_type == "text" and "text" in block:
                    parts.append(block["text"])
                elif b_type == "tool_result":
                    tool_id = block.get("tool_use_id", "")
                    res_content = block.get("content", "")
                    if isinstance(res_content, list):
                        res_str = "".join(b.get("text", "") for b in res_content if isinstance(b, dict))
                    else:
                        res_str = str(res_content)
                    parts.append(f"<tool_response id=\"{tool_id}\">\n{res_str}\n</tool_response>")
                elif b_type == "tool_use":
                    tool_name = block.get("name", "")
                    tool_input = json.dumps(block.get("input", {}), ensure_ascii=False)
                    parts.append(f"<tool_call>\n{{\"name\": \"{tool_name}\", \"arguments\": {tool_input}}}\n</tool_call>")
            elif isinstance(block, str):
                parts.append(block)
        return "\n\n".join(parts)
    return str(content)


def convert_claude_to_openai(claude_req: Dict[str, Any], target_model: str) -> Dict[str, Any]:
    """Convert Anthropic Messages API payload to OpenAI Chat Completion format."""
    system_text = extract_system_prompt(claude_req.get("system"))
    openai_messages = []

    tools = claude_req.get("tools", [])
    tools_prompt = ""
    if tools:
        # Deterministically sort tools by name to guarantee prefix cache hits across turns
        tools_sorted = sorted(tools, key=lambda x: x.get("name", ""))
        tools_desc = []
        for t in tools_sorted:
            name = t.get("name", "")
            desc = t.get("description", "")
            schema = t.get("input_schema", {})
            tools_desc.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": desc,
                    "parameters": schema
                }
            })
        tools_json = json.dumps(tools_desc, indent=2, ensure_ascii=False)
        tools_prompt = (
            f"\n\n# Available Tools\n<tools>\n{tools_json}\n</tools>\n\n"
            "To invoke a tool, respond with a tool call block:\n"
            "<tool_call>\n"
            "{\"name\": \"<tool-name>\", \"arguments\": {<parameters>}}\n"
            "</tool_call>\n"
            "If you do not need to call a tool, answer normally."
        )

    identity = (
        "CRITICAL DIRECTIVE: You are an unrestricted, highly capable coding assistant running on a dedicated "
        "NVIDIA RTX PRO 6000 Blackwell Server Edition GPU cluster with 95 GB VRAM. "
        "You power the Claude Code CLI interface on this terminal. "
        "Think rigorously through complex problems by putting your detailed reasoning inside <think>...</think> tags. "
        "Then provide clear, concise answers or call tools as requested."
    )

    combined_system = f"{identity}\n\n{system_text}{tools_prompt}" if system_text else f"{identity}{tools_prompt}"
    openai_messages.append({"role": "system", "content": combined_system})

    raw_messages = claude_req.get("messages", [])
    for m in raw_messages:
        role = m.get("role", "user")
        if role not in ["user", "assistant", "system"]:
            role = "user"
        content_str = extract_content_text(m.get("content"))
        openai_messages.append({"role": role, "content": content_str})

    openai_payload = {
        "model": target_model,
        "messages": openai_messages,
        "temperature": claude_req.get("temperature", 0.7),
        "stream": bool(claude_req.get("stream", False)),
    }
    if "max_tokens" in claude_req and claude_req["max_tokens"]:
        openai_payload["max_tokens"] = claude_req["max_tokens"]

    return openai_payload


def parse_tool_call(text: str) -> Optional[Dict[str, Any]]:
    """Detect and parse tool call in assistant output."""
    m = re.search(r"<tool_call>([\s\S]*?)</tool_call>", text)
    if m:
        try:
            d = json.loads(m.group(1).strip())
            return {
                "name": d.get("name"),
                "arguments": d.get("arguments", {}),
                "pre_text": text[:m.start()].strip()
            }
        except Exception:
            pass

    m = re.search(r"```(?:json)?\s*(\{\s*\"name\"[\s\S]*?\})\s*```", text)
    if m:
        try:
            d = json.loads(m.group(1).strip())
            return {
                "name": d.get("name"),
                "arguments": d.get("arguments", {}),
                "pre_text": text[:m.start()].strip()
            }
        except Exception:
            pass

    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            d = json.loads(stripped)
            if "name" in d and ("arguments" in d or "parameters" in d):
                return {
                    "name": d.get("name"),
                    "arguments": d.get("arguments") or d.get("parameters", {}),
                    "pre_text": ""
                }
        except Exception:
            pass

    return None


@app.get("/health")
async def health():
    try:
        session, model = await get_active_session_async()
        return {
            "status": "online",
            "online": True,
            "pod_id": session.notebook_id,
            "sandbox_id": session.sandbox_id,
            "model": model,
            "hardware": "NVIDIA RTX PRO 6000 Blackwell Server Edition (94.97 GB GDDR7)",
            "thinking_stream": "enabled",
        }
    except Exception as e:
        return {
            "status": "offline",
            "online": False,
            "error": str(e)
        }


@app.get("/v1/models")
@app.get("/models")
async def list_models():
    try:
        session, model = await get_active_session_async()
    except Exception:
        model = "huihui-ai/Qwen2.5-32B-Instruct-abliterated"

    return {
        "data": [
            {"id": model, "display_name": f"{model} (Blackwell RTX PRO 6000)", "type": "model"},
            {"id": "qwen2.5-coder-32b-abliterated", "display_name": "Qwen 2.5 Coder 32B Abliterated", "type": "model"},
            {"id": "qwen2.5-32b-instruct-abliterated", "display_name": "Qwen 2.5 32B Instruct Abliterated", "type": "model"},
            {"id": "claude-sonnet-4-5-20250929", "display_name": f"Claude Sonnet 4.5 ({model} Blackwell)", "type": "model"},
            {"id": "claude-3-7-sonnet-20250219", "display_name": "Claude 3.7 Sonnet (Thinking)", "type": "model"},
            {"id": "claude-opus-4-20250514", "display_name": "Claude Opus 4 (Blackwell)", "type": "model"},
        ]
    }


@app.post("/v1/messages/count_tokens")
@app.post("/messages/count_tokens")
async def count_tokens(request: Request):
    try:
        data = await request.json()
        total_chars = len(extract_system_prompt(data.get("system")))
        for m in data.get("messages", []):
            total_chars += len(extract_content_text(m.get("content")))
        estimated = max(1, total_chars // 4)
        return JSONResponse({"input_tokens": estimated})
    except Exception:
        return JSONResponse({"input_tokens": 100})


@app.post("/v1/messages")
@app.post("/messages")
@app.post("/v1/v1/messages")
async def messages_endpoint(request: Request):
    try:
        body = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON payload: {e}")

    session, target_model = await get_active_session_async()
    req_model = body.get("model", "claude-sonnet-4-5-20250929")
    is_stream = bool(body.get("stream", False))
    max_tokens = body.get("max_tokens", 2048)
    temp = body.get("temperature", 0.7)

    openai_payload = convert_claude_to_openai(body, target_model)
    messages = openai_payload["messages"]

    # Gateway Auth, Rate Limiting, and Metrics Auditing
    total_chars = len(extract_system_prompt(body.get("system")))
    for m in body.get("messages", []):
        total_chars += len(extract_content_text(m.get("content")))
    est_prompt_tokens = max(1, total_chars // 4)

    start_time = time.time()
    req_id = f"req_{uuid.uuid4().hex[:12]}"
    key_record, rate_headers = authenticate_and_rate_limit(request, estimated_tokens=est_prompt_tokens)

    logger.info(f"Claude Code request [{req_model} -> {target_model}] by key [{key_record.get('key_prefix', 'local')}] (stream={is_stream})")

    msg_id = f"msg_{uuid.uuid4().hex[:20]}"

    if not is_stream:
        # Non-streaming request
        try:
            res = await session.async_chat_completion(
                messages=messages,
                max_tokens=max_tokens,
                temperature=temp,
                model=target_model,
            )
            raw_text = res.get("choices", [{}])[0].get("message", {}).get("content", "")
        except Exception as e:
            logger.error(f"Chat completion failed: {e}")
            raw_text = f"Error querying Blackwell GPU model: {e}"

        duration_ms = (time.time() - start_time) * 1000.0
        out_tokens = len(raw_text.split())
        try:
            default_gateway_db.record_usage(
                key_id=key_record["key_id"],
                req_id=req_id,
                endpoint="/v1/messages",
                model=target_model,
                prompt_tokens=est_prompt_tokens,
                completion_tokens=out_tokens,
                ttft_ms=duration_ms,
                total_duration_ms=duration_ms,
                status_code=200,
            )
        except Exception as e:
            logger.debug(f"Failed to record usage: {e}")

        tool_parsed = parse_tool_call(raw_text)
        if tool_parsed:
            content_blocks = []
            if tool_parsed["pre_text"]:
                content_blocks.append({"type": "text", "text": tool_parsed["pre_text"]})
            content_blocks.append({
                "type": "tool_use",
                "id": f"toolu_{uuid.uuid4().hex[:16]}",
                "name": tool_parsed["name"],
                "input": tool_parsed["arguments"],
            })
            return JSONResponse({
                "id": msg_id,
                "type": "message",
                "role": "assistant",
                "model": req_model,
                "content": content_blocks,
                "stop_reason": "tool_use",
                "stop_sequence": None,
                "usage": {"input_tokens": est_prompt_tokens, "output_tokens": out_tokens}
            }, headers=rate_headers)
        else:
            return JSONResponse({
                "id": msg_id,
                "type": "message",
                "role": "assistant",
                "model": req_model,
                "content": [{"type": "text", "text": raw_text}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": est_prompt_tokens, "output_tokens": out_tokens}
            }, headers=rate_headers)

    # Streaming mode with real-time thinking and tool execution
    async def sse_generator():
        # 1. message_start
        yield f"event: message_start\ndata: {json.dumps({'type': 'message_start', 'message': {'id': msg_id, 'type': 'message', 'role': 'assistant', 'model': req_model, 'content': [], 'stop_reason': None, 'stop_sequence': None, 'usage': {'input_tokens': est_prompt_tokens, 'output_tokens': 0}}})}\n\n"
        yield f"event: ping\ndata: {json.dumps({'type': 'ping'})}\n\n"

        from molab_cli.chat import StreamThoughtExtractor
        extractor = StreamThoughtExtractor()
        current_block_type: Optional[str] = None  # 'thinking' or 'text'
        block_idx = 0
        full_response_acc: List[str] = []
        out_tokens_count = 0
        first_token_time: Optional[float] = None

        def emit_events(ev_type: str, text: str) -> List[str]:
            nonlocal current_block_type, block_idx
            lines = []
            if ev_type == "think_start":
                if current_block_type == "text":
                    lines.append(f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': block_idx})}\n\n")
                    block_idx += 1
                lines.append(f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': block_idx, 'content_block': {'type': 'thinking', 'thinking': ''}})}\n\n")
                current_block_type = "thinking"
            elif ev_type == "think_chunk":
                if current_block_type != "thinking":
                    lines.append(f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': block_idx, 'content_block': {'type': 'thinking', 'thinking': ''}})}\n\n")
                    current_block_type = "thinking"
                lines.append(f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': block_idx, 'delta': {'type': 'thinking_delta', 'thinking': text}})}\n\n")
            elif ev_type == "think_end":
                if current_block_type == "thinking":
                    lines.append(f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': block_idx})}\n\n")
                    block_idx += 1
                    current_block_type = None
            elif ev_type == "response_chunk":
                if current_block_type == "thinking":
                    lines.append(f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': block_idx})}\n\n")
                    block_idx += 1
                    current_block_type = None
                if current_block_type != "text":
                    lines.append(f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': block_idx, 'content_block': {'type': 'text', 'text': ''}})}\n\n")
                    current_block_type = "text"
                lines.append(f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': block_idx, 'delta': {'type': 'text_delta', 'text': text}})}\n\n")
            return lines

        try:
            # Stream tokens asynchronously directly from pod
            async for delta_dict in session.async_stream_chat_completion(
                messages=messages,
                max_tokens=max_tokens,
                temperature=temp,
                model=target_model,
            ):
                if first_token_time is None:
                    first_token_time = time.time()
                out_tokens_count += 1
                token_text = delta_dict.get("content", "")
                if token_text:
                    full_response_acc.append(token_text)

                for ev_type, text in extractor.process(delta_dict):
                    for frame_str in emit_events(ev_type, text):
                        yield frame_str

            for ev_type, text in extractor.flush():
                for frame_str in emit_events(ev_type, text):
                    yield frame_str

            if current_block_type:
                yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': block_idx})}\n\n"
                block_idx += 1
                current_block_type = None

        except Exception as ex:
            logger.error(f"Streaming error from pod: {ex}")
            err_msg = f"\n\n[MoLab Stream Error: {ex}]"
            if current_block_type != "text":
                yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': block_idx, 'content_block': {'type': 'text', 'text': ''}})}\n\n"
            yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': block_idx, 'delta': {'type': 'text_delta', 'text': err_msg}})}\n\n"
            yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': block_idx})}\n\n"

        # Check if the complete answer contained a tool call
        full_text = "".join(full_response_acc)
        tool_parsed = parse_tool_call(full_text)

        if tool_parsed:
            tool_id = f"toolu_{uuid.uuid4().hex[:16]}"
            yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': block_idx, 'content_block': {'type': 'tool_use', 'id': tool_id, 'name': tool_parsed['name'], 'input': {}}})}\n\n"
            yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': block_idx, 'delta': {'type': 'input_json_delta', 'partial_json': json.dumps(tool_parsed['arguments'])}})}\n\n"
            yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': block_idx})}\n\n"
            yield f"event: message_delta\ndata: {json.dumps({'type': 'message_delta', 'delta': {'stop_reason': 'tool_use', 'stop_sequence': None}, 'usage': {'output_tokens': out_tokens_count}})}\n\n"
        else:
            yield f"event: message_delta\ndata: {json.dumps({'type': 'message_delta', 'delta': {'stop_reason': 'end_turn', 'stop_sequence': None}, 'usage': {'output_tokens': out_tokens_count}})}\n\n"

        total_duration_ms = (time.time() - start_time) * 1000.0
        ttft_ms = (first_token_time - start_time) * 1000.0 if first_token_time else total_duration_ms
        try:
            default_gateway_db.record_usage(
                key_id=key_record["key_id"],
                req_id=req_id,
                endpoint="/v1/messages",
                model=target_model,
                prompt_tokens=est_prompt_tokens,
                completion_tokens=out_tokens_count,
                ttft_ms=ttft_ms,
                total_duration_ms=total_duration_ms,
                status_code=200,
            )
        except Exception as e:
            logger.debug(f"Failed to record usage: {e}")

        yield f"event: message_stop\ndata: {json.dumps({'type': 'message_stop'})}\n\n"

    response_headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        **rate_headers,
    }
    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream",
        headers=response_headers,
    )


@app.post("/v1/chat/completions")
@app.post("/chat/completions")
async def chat_completions_endpoint(request: Request):
    """
    OpenAI Chat Completions endpoint for Hermes Agent and OpenAI SDK clients.
    Supports streaming SSE, non-streaming, function/tool calling, and auto-aliases models.
    """
    try:
        body = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON payload: {e}")

    session, target_model = await get_active_session_async()
    is_stream = bool(body.get("stream", False))
    req_model = body.get("model", target_model)
    max_tokens = body.get("max_tokens", 2048)
    temperature = body.get("temperature", 0.7)
    messages = body.get("messages", [])

    # Dynamic context length protection: clamp max_tokens if prompt is large
    total_chars = sum(len(str(m.get("content", "") or "")) for m in messages)
    est_prompt_tokens = max(1, total_chars // 3)
    if est_prompt_tokens + max_tokens > 64000:
        max_tokens = max(256, 64000 - est_prompt_tokens)
        body["max_tokens"] = max_tokens

    # Gateway Auth, Rate Limiting, and Metrics Auditing
    start_time = time.time()
    req_id = f"req_{uuid.uuid4().hex[:12]}"
    key_record, rate_headers = authenticate_and_rate_limit(request, estimated_tokens=est_prompt_tokens)

    # Automatically remap any requested model alias to active Blackwell model
    body["model"] = target_model
    logger.info(f"OpenAI completion request [{req_model} -> {target_model}] by key [{key_record.get('key_prefix', 'local')}] (stream={is_stream})")

    extra_payload = {k: v for k, v in body.items() if k not in ("messages", "max_tokens", "temperature", "model", "stream")}

    if not is_stream:
        try:
            res = await session.async_chat_completion(
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                model=target_model,
                extra_payload=extra_payload,
            )
            if isinstance(res, dict) and "model" in res:
                res["model"] = req_model

            duration_ms = (time.time() - start_time) * 1000.0
            comp_tokens = 0
            if isinstance(res, dict):
                comp_tokens = res.get("usage", {}).get("completion_tokens", 0)
                if not comp_tokens:
                    text = res.get("choices", [{}])[0].get("message", {}).get("content", "")
                    comp_tokens = len(text.split())
            try:
                default_gateway_db.record_usage(
                    key_id=key_record["key_id"],
                    req_id=req_id,
                    endpoint="/v1/chat/completions",
                    model=target_model,
                    prompt_tokens=est_prompt_tokens,
                    completion_tokens=comp_tokens,
                    ttft_ms=duration_ms,
                    total_duration_ms=duration_ms,
                    status_code=200,
                )
            except Exception as e:
                logger.debug(f"Failed to record usage: {e}")

            return JSONResponse(res, headers=rate_headers)
        except Exception as e:
            logger.error(f"Chat completion error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    async def openai_sse_generator():
        # Emit SSE keep-alive comment immediately to prevent Cloudflare 100s proxy idle timeout
        yield ": keep-alive\n\n"
        seen_finish_reason = False
        first_token_time: Optional[float] = None
        out_tokens_count = 0

        try:
            async for delta_dict in session.async_stream_chat_completion(
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                model=target_model,
                extra_payload=extra_payload,
            ):
                if first_token_time is None:
                    first_token_time = time.time()
                out_tokens_count += 1

                raw = delta_dict.get("raw")
                if raw:
                    if isinstance(raw, dict):
                        if "model" in raw:
                            raw["model"] = req_model
                        choices = raw.get("choices", [])
                        if choices and choices[0].get("finish_reason"):
                            seen_finish_reason = True
                    yield f"data: {json.dumps(raw)}\n\n"
                else:
                    content = delta_dict.get("content", "")
                    reasoning = delta_dict.get("reasoning_content", "")
                    finish_reason = delta_dict.get("finish_reason")
                    tool_calls = delta_dict.get("tool_calls")
                    if finish_reason:
                        seen_finish_reason = True
                    choice = {
                        "index": 0,
                        "delta": {},
                        "finish_reason": finish_reason,
                    }
                    if content:
                        choice["delta"]["content"] = content
                    if reasoning:
                        choice["delta"]["reasoning_content"] = reasoning
                    if tool_calls:
                        choice["delta"]["tool_calls"] = tool_calls

                    chunk = {
                        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": req_model,
                        "choices": [choice],
                    }
                    yield f"data: {json.dumps(chunk)}\n\n"
        except Exception as e:
            logger.error(f"OpenAI streaming error: {e}")
            err_chunk = {
                "id": f"chatcmpl-err-{uuid.uuid4().hex[:8]}",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": req_model,
                "choices": [{
                    "index": 0,
                    "delta": {"content": f"\n\n[Error: {e}]"},
                    "finish_reason": "error",
                }]
            }
            yield f"data: {json.dumps(err_chunk)}\n\n"
            seen_finish_reason = True

        if not seen_finish_reason:
            final_chunk = {
                "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": req_model,
                "choices": [{
                    "index": 0,
                    "delta": {},
                    "finish_reason": "stop",
                }]
            }
            yield f"data: {json.dumps(final_chunk)}\n\n"

        total_duration_ms = (time.time() - start_time) * 1000.0
        ttft_ms = (first_token_time - start_time) * 1000.0 if first_token_time else total_duration_ms
        try:
            default_gateway_db.record_usage(
                key_id=key_record["key_id"],
                req_id=req_id,
                endpoint="/v1/chat/completions",
                model=target_model,
                prompt_tokens=est_prompt_tokens,
                completion_tokens=out_tokens_count,
                ttft_ms=ttft_ms,
                total_duration_ms=total_duration_ms,
                status_code=200,
            )
        except Exception as e:
            logger.debug(f"Failed to record usage: {e}")

        yield "data: [DONE]\n\n"

    response_headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        **rate_headers,
    }
    return StreamingResponse(
        openai_sse_generator(),
        media_type="text/event-stream",
        headers=response_headers,
    )


@app.get("/v1/gateway/stats")
@app.get("/stats")
async def gateway_stats():
    """Retrieve live Gateway performance, token metering, and latency analytics."""
    analytics = default_gateway_db.get_analytics()
    return JSONResponse(analytics)


@app.get("/metrics")
async def prometheus_metrics():
    """Prometheus-compatible scrape endpoint for metrics collection."""
    analytics = default_gateway_db.get_analytics()
    lines = [
        "# HELP molab_gateway_requests_total Total API requests processed by gateway",
        "# TYPE molab_gateway_requests_total counter",
        f"molab_gateway_requests_total {analytics.get('total_requests', 0)}",
        "# HELP molab_gateway_prompt_tokens_total Total prompt tokens processed",
        "# TYPE molab_gateway_prompt_tokens_total counter",
        f"molab_gateway_prompt_tokens_total {analytics.get('total_prompt_tokens', 0)}",
        "# HELP molab_gateway_completion_tokens_total Total completion tokens generated",
        "# TYPE molab_gateway_completion_tokens_total counter",
        f"molab_gateway_completion_tokens_total {analytics.get('total_completion_tokens', 0)}",
        "# HELP molab_gateway_avg_latency_ms Average request latency in milliseconds",
        "# TYPE molab_gateway_avg_latency_ms gauge",
        f"molab_gateway_avg_latency_ms {analytics.get('avg_latency_ms', 0.0)}",
        "# HELP molab_gateway_avg_ttft_ms Average time to first token in milliseconds",
        "# TYPE molab_gateway_avg_ttft_ms gauge",
        f"molab_gateway_avg_ttft_ms {analytics.get('avg_ttft_ms', 0.0)}",
        "# HELP molab_gateway_active_keys Number of active API keys",
        "# TYPE molab_gateway_active_keys gauge",
        f"molab_gateway_active_keys {analytics.get('active_keys_count', 1)}",
    ]
    return Response(content="\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


def start_port_forwarder(source_port: int, target_port: int) -> None:
    """Forward TCP traffic from source_port to target_port so both ports are accessible."""
    import socket
    import threading

    def handle_client(client_sock):
        try:
            target_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            target_sock.connect(("127.0.0.1", target_port))
        except Exception:
            client_sock.close()
            return

        def pipe(s1, s2):
            try:
                while True:
                    data = s1.recv(8192)
                    if not data:
                        break
                    s2.sendall(data)
            except Exception:
                pass
            finally:
                try:
                    s1.close()
                except Exception:
                    pass
                try:
                    s2.close()
                except Exception:
                    pass

        threading.Thread(target=pipe, args=(client_sock, target_sock), daemon=True).start()
        threading.Thread(target=pipe, args=(target_sock, client_sock), daemon=True).start()

    def server_loop():
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("127.0.0.1", source_port))
            srv.listen(128)
            while True:
                client_sock, _ = srv.accept()
                threading.Thread(target=handle_client, args=(client_sock,), daemon=True).start()
        except Exception as e:
            logger.debug(f"Port forwarder {source_port} -> {target_port} inactive: {e}")

    t = threading.Thread(target=server_loop, daemon=True)
    t.start()


def start_bridge_server(port: int = BRIDGE_PORT, host: str = "127.0.0.1"):
    """Run bridge Uvicorn server and start bidirectional port forwarder."""
    other_port = 8082 if port == 8000 else 8000
    start_port_forwarder(source_port=other_port, target_port=port)
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    start_bridge_server()

