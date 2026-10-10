# MoLab Enterprise Model Catalog & Blackwell Optimization Matrix

This document provides battle-tested, industry-grade production configurations for hosting open-weights neural models on **NVIDIA RTX PRO 6000 Blackwell (96GB GDDR7 VRAM, sm_120)** cloud pods via MoLab.

---

## 1. Hardware Architecture Profile: NVIDIA RTX PRO 6000 Blackwell

* **Compute Silicon:** Blackwell Architecture (sm_120) with 4th Generation Tensor Cores.
* **VRAM Capacity:** 94.97 GB GDDR7 (97,887 MiB physical address space).
* **Memory Bandwidth:** ~1,800 GB/s peak throughput.
* **Native Numerical Formats:** FP4 / NVFP4, FP8 (E4M3, E5M2), Bfloat16, Float16, INT8, INT4.
* **Host Environment:** CoreWeave gVisor container sandbox, 4–8 vCPUs, 32–64 GB System RAM.

---

## 2. Category-Wise Production Configuration Matrix

| Model Category | Exemplar Models | Weight Dtype | Context Length | GPU Memory Util | Chunked Prefill Batched Tokens | Attention Backend | KV Cache Dtype | Tool Call Parser |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **1. Flagship Dense Coding & Agents** | `Qwen2.5-Coder-32B-Instruct`, `huihui-ai/Qwen2.5-32B-Instruct-abliterated` | `bfloat16` | 65,536 | `0.88 - 0.90` | 8,192 | `FLASH_ATTN` / `FLASHINFER` | `auto` | `hermes` / `qwen` |
| **2. Frontier Dense 70B+ Models** | `Meta-Llama-3.3-70B-Instruct-FP8`, `Qwen2.5-72B-Instruct-AWQ` | `fp8` / `awq` | 32,768 - 65,536 | `0.92` | 8,192 | `FLASH_ATTN` | `auto` / `fp8` | `llama3_json` / `hermes` |
| **3. Deep Reasoning & Math (CoT)** | `DeepSeek-R1-Distill-Qwen-32B`, `DeepSeek-R1-Distill-Llama-70B-FP8` | `bfloat16` / `fp8` | 65,536 - 131,072 | `0.90` | 8,192 | `FLASH_ATTN` | `auto` | `none` (CoT stream) |
| **4. Mixture of Experts (MoE)** | `Mixtral-8x7B-Instruct-v0.1`, `Qwen1.5-MoE-A2.7B-Chat` | `bfloat16` | 32,768 | `0.88` | 8,192 | `FLASH_ATTN` | `auto` | `mistral` / `hermes` |
| **5. Vision-Language Multimodal (VLLM)**| `Qwen2-VL-7B-Instruct`, `Qwen2-VL-72B-Instruct-AWQ` | `bfloat16` / `awq` | 32,768 | `0.88` | 4,096 | `FLASH_ATTN` | `auto` | `qwen` |
| **6. Speculative Decoding Pipelines** | Target: `Qwen2.5-32B` + Draft: `Qwen2.5-0.5B` | `bfloat16` | 32,768 | `0.90` | 8,192 | `FLASH_ATTN` | `auto` | `hermes` |
| **7. High-Throughput Embeddings** | `BAAI/bge-m3`, `Qwen2.5-Coder-1.5B-Embedding` | `bfloat16` | 8,192 | `0.85` | 16,384 | `FLASH_ATTN` | `auto` | N/A |
| **8. Media & Audio Transcription** | `openai/whisper-large-v3`, `Systran/faster-whisper-large-v3` | `float16` | Chunked | `0.50` | N/A | TensorRT / CTranslate2 | N/A | N/A |

---

## 3. Detailed Per-Category Launch Specifications

### Category 1: Flagship Dense 32B Coding & Agent Models
* **Best For:** Claude Code, Hermes Agent, Cursor, full-stack software development, automated test generation, repository-wide search.
* **Model ID:** `huihui-ai/Qwen2.5-32B-Instruct-abliterated` or `Qwen/Qwen2.5-Coder-32B-Instruct`
* **VRAM Footprint:** ~61 GB weights + ~24 GB dynamic KV cache = 85 GB total.
* **Launch Command:**
```bash
python3 -m vllm.entrypoints.openai.api_server \
  --model huihui-ai/Qwen2.5-32B-Instruct-abliterated \
  --dtype bfloat16 \
  --port 8000 \
  --max-model-len 65536 \
  --gpu-memory-utilization 0.88 \
  --tensor-parallel-size 1 \
  --trust-remote-code \
  --enable-auto-tool-choice \
  --tool-call-parser hermes \
  --host 0.0.0.0
```
* **Performance Profile:**
  * **Tokens/Second:** 50–65 tok/s decode.
  * **Prefix Cache Hit TTFT:** < 80 ms.
  * **Cold Batch TTFT:** 800–1200 ms.

---

### Category 2: Frontier Dense 70B+ Models (FP8 / Quantized)
* **Best For:** Enterprise reasoning, complex architectural design, compliance auditing, long-form document synthesis.
* **Model ID:** `neuralmagic/Meta-Llama-3.3-70B-Instruct-FP8` or `Qwen/Qwen2.5-72B-Instruct-AWQ`
* **VRAM Footprint:** ~72 GB weights (FP8) + ~18 GB dynamic KV cache = 90 GB total.
* **Launch Command:**
```bash
python3 -m vllm.entrypoints.openai.api_server \
  --model neuralmagic/Meta-Llama-3.3-70B-Instruct-FP8 \
  --quantization fp8 \
  --kv-cache-dtype auto \
  --port 8000 \
  --max-model-len 32768 \
  --gpu-memory-utilization 0.92 \
  --tensor-parallel-size 1 \
  --trust-remote-code \
  --enable-auto-tool-choice \
  --tool-call-parser llama3_json \
  --host 0.0.0.0
```
* **Performance Profile:**
  * **Tokens/Second:** 38–48 tok/s decode.
  * **Prefix Cache Hit TTFT:** < 110 ms.

---

### Category 3: Deep Reasoning & Math Models (CoT / Thinking)
* **Best For:** Hard algorithmic challenges, theorem proving, security vulnerability analysis, multi-step formal reasoning.
* **Model ID:** `deepseek-ai/DeepSeek-R1-Distill-Qwen-32B`
* **VRAM Footprint:** ~62 GB weights + ~23 GB dynamic KV cache = 85 GB total.
* **Sampling Guidelines:** Temperature `0.6`, Top-P `0.95`, Max Tokens `8,192` to allow detailed CoT exploration.
* **Launch Command:**
```bash
python3 -m vllm.entrypoints.openai.api_server \
  --model deepseek-ai/DeepSeek-R1-Distill-Qwen-32B \
  --dtype bfloat16 \
  --port 8000 \
  --max-model-len 65536 \
  --gpu-memory-utilization 0.90 \
  --tensor-parallel-size 1 \
  --trust-remote-code \
  --host 0.0.0.0
```
* **Reasoning Extraction:** The model automatically emits thought chains between `<think>` and `</think>` tags. The MoLab streaming engine maps these live to Anthropic `thinking_delta` events.

---

### Category 4: Mixture of Experts (MoE)
* **Best For:** High inference throughput at balanced compute costs; broad multi-domain knowledge.
* **Model ID:** `mistralai/Mixtral-8x7B-Instruct-v0.1`
* **VRAM Footprint:** ~87 GB weights (bfloat16) = fits in 96GB GDDR7 with 8GB KV cache.
* **Launch Command:**
```bash
python3 -m vllm.entrypoints.openai.api_server \
  --model mistralai/Mixtral-8x7B-Instruct-v0.1 \
  --dtype bfloat16 \
  --port 8000 \
  --max-model-len 32768 \
  --gpu-memory-utilization 0.92 \
  --tensor-parallel-size 1 \
  --trust-remote-code \
  --host 0.0.0.0
```

---

### Category 5: Vision-Language Multimodal (VLLMs)
* **Best For:** UI screenshot debugging, document OCR extraction, diagram analysis, visual QA.
* **Model ID:** `Qwen/Qwen2-VL-7B-Instruct` or `Qwen/Qwen2-VL-72B-Instruct-AWQ`
* **Launch Command (7B Native bfloat16):**
```bash
python3 -m vllm.entrypoints.openai.api_server \
  --model Qwen/Qwen2-VL-7B-Instruct \
  --dtype bfloat16 \
  --port 8000 \
  --max-model-len 32768 \
  --gpu-memory-utilization 0.88 \
  --limit-mm-per-prompt image=4 \
  --trust-remote-code \
  --host 0.0.0.0
```
* **Client Payload Structure:** Accepts standard OpenAI image payload with base64 data URLs:
```json
{
  "model": "Qwen/Qwen2-VL-7B-Instruct",
  "messages": [
    {
      "role": "user",
      "content": [
        {"type": "text", "text": "Describe the bug in this screenshot:"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
      ]
    }
  ]
}
```

---

### Category 6: Ultra-Fast Speculative Decoding Pipelines
* **Best For:** Interactive terminal code completion and interactive chat where Time-Per-Output-Token (TPOT) must be halved.
* **Architecture:** Target Model (`Qwen2.5-32B`) paired with Draft Model (`Qwen2.5-0.5B`) sharing the exact same vocabulary and tokenizer.
* **Launch Command:**
```bash
python3 -m vllm.entrypoints.openai.api_server \
  --model Qwen/Qwen2.5-Coder-32B-Instruct \
  --speculative-model Qwen/Qwen2.5-0.5B-Instruct \
  --num-speculative-tokens 5 \
  --speculative-disable-by-batch-size 8 \
  --dtype bfloat16 \
  --port 8000 \
  --max-model-len 32768 \
  --gpu-memory-utilization 0.90 \
  --trust-remote-code \
  --host 0.0.0.0
```
* **Performance Acceleration:** Boosts generation speed from **55 tok/s to 90–110 tok/s** for structured and repetitive coding tokens!

---

### Category 7: High-Throughput Embeddings & Reranking
* **Best For:** Vector retrieval, RAG knowledge bases, hybrid lexical-dense search.
* **Model ID:** `BAAI/bge-m3`
* **Launch Command:**
```bash
python3 -m vllm.entrypoints.openai.api_server \
  --model BAAI/bge-m3 \
  --dtype bfloat16 \
  --port 8000 \
  --max-model-len 8192 \
  --gpu-memory-utilization 0.85 \
  --host 0.0.0.0
```
* **Throughput:** > 5,000 sentences/second batched over Blackwell tensor cores.
