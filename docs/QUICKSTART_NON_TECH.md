# Non-Technical Quickstart: 1-Click Cloud Model Deployment

This guide is designed for developers, non-engineers, and creators who want to deploy a model on a dedicated **NVIDIA RTX PRO 6000 Blackwell (96GB VRAM)** GPU and immediately get working credentials for Cursor, Claude Code, or Python.

---

## 1. The 60-Second 1-Command Deployment

To deploy a high-performance 32B model with optimal settings, run a single command in your terminal:

```bash
molab deploy qwen-32b
```

### What Happens Automatically:
1. **Finds your GPU pod:** Automatically identifies your active Blackwell GPU.
2. **Applies Blackwell Tuning:** Automatically configures FlashInfer, Automatic Prefix Caching, and 64K context.
3. **Launches Background Supervisor:** Ensures the model server stays online 24/7.
4. **Creates Public HTTPS Tunnel:** Generates a secure Cloudflare URL.
5. **Displays Copy-Paste Credentials:** Prints ready-to-use configuration blocks for Cursor, Claude Code, and Python.

---

## 2. Instant Model Aliases Available

You can pass any of these friendly names to `molab deploy`:

| Friendly Alias | Full Neural Model Name | Description | Recommended For |
| :--- | :--- | :--- | :--- |
| `qwen-32b` *(Default)* | `huihui-ai/Qwen2.5-32B-Instruct-abliterated` | Uncensored flagship coding & agent model | Daily programming, Claude Code, Cursor |
| `coder-32b` | `Qwen/Qwen2.5-Coder-32B-Instruct` | Specialized software development model | Refactoring, debugging, repository audits |
| `r1-32b` | `deepseek-ai/DeepSeek-R1-Distill-Qwen-32B` | Deep reasoning & mathematics model | Complex algorithms, logic puzzles, proofs |
| `llama-70b` | `neuralmagic/Meta-Llama-3.3-70B-Instruct-FP8` | Frontier 70B dense enterprise model | High-reasoning architecture & system design |

---

## 3. How to Connect Your Tools

### Step A: Connect Cursor IDE (In 30 Seconds)
1. Open **Cursor**.
2. Press `Ctrl + Shift + J` (or go to **Cursor Settings** -> **Models**).
3. Under **OpenAI API Key**:
   * Turn **ON** the OpenAI toggle.
   * Paste your **Base URL** (from `molab share` or `molab deploy`):
     ```text
     https://<YOUR-URL>.trycloudflare.com/v1
     ```
   * Paste your **API Key**:
     ```text
     sk-molab-blackwell-cluster
     ```
4. Click **Add Model**, type `huihui-ai/Qwen2.5-32B-Instruct-abliterated`, and save!

---

### Step B: Launch Claude Code (In 1 Second)
Run:
```bash
molab chat --claude
```
This immediately starts Claude Code connected directly to your Blackwell GPU pod with zero setup.

---

### Step C: Test in Python (Copy & Paste)
```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8000/v1",  # Or public tunnel URL
    api_key="sk-molab-blackwell-cluster"
)

response = client.chat.completions.create(
    model="huihui-ai/Qwen2.5-32B-Instruct-abliterated",
    messages=[{"role": "user", "content": "Hello Blackwell!"}]
)

print(response.choices[0].message.content)
```

---

## 4. Useful 1-Word Commands

* **Check GPU Speed & Cache Hit Rate:**
  ```bash
  molab perf
  ```
* **Check Tunnel Credentials & URL:**
  ```bash
  molab share --status
  ```
* **Stop Tunnel:**
  ```bash
  molab share --stop
  ```
* **Stop Pod (Conserve Hours):**
  ```bash
  molab stop <pod_id>
  ```
