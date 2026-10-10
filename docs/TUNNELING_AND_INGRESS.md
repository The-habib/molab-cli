# MoLab Tunneling, Public Ingress & Enterprise Client Integration Manual

This manual provides complete documentation for exposing MoLab Blackwell GPU endpoints to the internet using Cloudflare Tunnels, configuring custom named domains, bypassing proxy timeouts, and connecting frontend clients (Cursor, Claude Code, LibreChat, Open WebUI, Python/Node SDKs).

---

## 1. Network Topology & Ingress Architecture

```mermaid
flowchart TD
    Client["Client: Cursor / Claude Code / WebUI / Python SDK"] -->|HTTPS 443| CF["Cloudflare Edge Network\n(WAF / DDoS / Global CDN)"]
    CF -->|Zero-Trust Tunnel (QUIC / HTTP/2)| Termux["Localhost Termux Daemon\n(cloudflared PID 22677)"]
    Termux -->|HTTP 8000| Bridge["MoLab AI Gateway Bridge\n(CORS / Bearer Auth / Rate Limits)"]
    Bridge -->|Marimo Secure Stream| Pod["CoreWeave Blackwell GPU Pod\n(vLLM Port 8000 / Qwen 32B)"]
```

### Why Tunnels Run from Termux Instead of the Cloud Pod:
* **The CoreWeave Sandbox Block:** CoreWeave gVisor containers enforce strict egress security policies that drop outbound UDP/TCP connections to port 7844 (Cloudflare edge connectors).
* **The Solution:** The `molab share` daemon runs `cloudflared` from Termux (which has unrestricted outbound networking) pointing to the local bridge on `http://127.0.0.1:8000`.

---

## 2. Quick Tunnels vs Persistent Named Tunnels

MoLab supports two modes of public ingress:

### Option A: Free Zero-Config Quick Tunnel (Instant)
* Automatically provisions a temporary public HTTPS URL on `trycloudflare.com`.
* No account, no API token, and no DNS setup required.
* **Command:**
  ```bash
  molab share
  # Or check active tunnel status:
  molab share --status
  ```

### Option B: Cloudflare Named Tunnel (Persistent Custom Domain)
* Binds your model API to an official enterprise domain (e.g. `https://api.yourdomain.com`).
* Generates a permanent URL that never changes when tunnels restart.
* **How to acquire a Cloudflare Token:**
  1. Go to [Cloudflare Zero Trust Dashboard](https://one.dash.cloudflare.com/) -> **Networks** -> **Tunnels**.
  2. Click **Create a Tunnel** -> Select **Cloudflared**.
  3. Enter a name (e.g. `molab-blackwell-gateway`).
  4. Copy the tunnel token string (`eyJh...`).
  5. Under Public Hostname, route `api.yourdomain.com` to `HTTP` `127.0.0.1:8000`.
* **Command:**
  ```bash
  molab share --token <YOUR_CLOUDFLARE_TUNNEL_TOKEN>
  ```

---

## 3. SSE Keep-Alives & Cloudflare 100-Second Timeout Prevention

### The Problem:
Cloudflare edge proxies enforce a strict **100-second HTTP idle timeout**. When a deep reasoning model (like DeepSeek-R1) spends 40–90 seconds thinking through a complex proof before emitting text, or when a coding model processes a 50,000-token context prefill, standard HTTP streams appear "idle" to Cloudflare and get dropped with **HTTP 524 A Timeout Occurred**.

### The Gateway Solution:
The MoLab AI Gateway bridge actively emits standard SSE comment frames during generation:
```http
: keep-alive\n\n
```
* **Why this works:** SSE lines starting with `:` are treated as comments per the W3C EventSource standard.
* OpenAI and Anthropic SDKs silently ignore these comments without error.
* The TCP socket emits live bytes every 15 seconds, resetting Cloudflare's idle timer and allowing indefinite inference runs!

---

## 4. End-to-End Client Configuration Recipes

### Recipe 1: Cursor IDE Integration
Connect Cursor's AI coding assistant directly to your self-hosted Blackwell model:
1. Open Cursor -> **Settings** -> **Models** -> **OpenAI API Key**.
2. Set **Base URL:**
   ```text
   https://<YOUR-TUNNEL-URL>.trycloudflare.com/v1
   ```
3. Set **API Key:**
   ```text
   sk-molab-blackwell-cluster
   # Or your virtual key: sk-molab-32c483359f860b568cacaaf34846de1d
   ```
4. Under **Model Names**, click **Add Model** and enter:
   ```text
   huihui-ai/Qwen2.5-32B-Instruct-abliterated
   ```

---

### Recipe 2: Anthropic Claude Code CLI
Power the native `claude` CLI with your 96GB Blackwell GPU:
```bash
export ANTHROPIC_BASE_URL="http://127.0.0.1:8082"
export ANTHROPIC_API_KEY="sk-molab-blackwell-cluster"
export CLAUDE_CODE_TERMUX_NO_PROXY_HELPER="1"

claude
```
Or launch automatically in 1 click:
```bash
molab chat --claude
```

---

### Recipe 3: Python OpenAI SDK (Streaming)
```python
import os
from openai import OpenAI

client = OpenAI(
    base_url="https://<YOUR-TUNNEL-URL>.trycloudflare.com/v1",
    api_key="sk-molab-blackwell-cluster",
)

stream = client.chat.completions.create(
    model="huihui-ai/Qwen2.5-32B-Instruct-abliterated",
    messages=[
        {"role": "system", "content": "You are an expert systems programmer."},
        {"role": "user", "content": "Write a high-speed lock-free ring buffer in C++20."}
    ],
    stream=True,
    temperature=0.7,
)

for chunk in stream:
    delta = chunk.choices[0].delta.content or ""
    print(delta, end="", flush=True)
print()
```

---

### Recipe 4: Open WebUI / LibreChat Docker Setup
To connect Open WebUI or LibreChat running on a server or desktop:
1. In Open WebUI Settings -> **Connections** -> **OpenAI API**:
   * **URL:** `https://<YOUR-TUNNEL-URL>.trycloudflare.com/v1`
   * **API Key:** `sk-molab-blackwell-cluster`
2. Click **Verify Connection** (Green checkmark appears).
3. The model list (`huihui-ai/Qwen2.5-32B-Instruct-abliterated`) will populate automatically via `/v1/models`.

---

### Recipe 5: Production cURL Command
```bash
curl -N -X POST "https://<YOUR-TUNNEL-URL>.trycloudflare.com/v1/chat/completions" \
  -H "Authorization: Bearer sk-molab-blackwell-cluster" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "huihui-ai/Qwen2.5-32B-Instruct-abliterated",
    "messages": [
      {"role": "user", "content": "Explain GPU tensor cores in 2 bullet points."}
    ],
    "stream": true
  }'
```
