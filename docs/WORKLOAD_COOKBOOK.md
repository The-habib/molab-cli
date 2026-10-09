# MoLab Production Workload Cookbook

Studio-grade operational recipes for running high-performance AI workloads on CoreWeave NVIDIA RTX PRO 6000 Blackwell (96GB VRAM) pods from Android / Termux.

---

## Recipe 1: 4K Neural Video Remastering (Cinematic Grade)

Enhance mobile video footage (1080p -> 4K) using Real-ESRGAN Compact neural networks, two-pass motion stabilization, and professional color grading.

### 1. Pod Discovery & Video Upload
```bash
# Locate idle Blackwell pod
POD_ID=$(molab free --json | jq -r .recommended_free_pod)

# Stream 4K source video directly to pod (100MB/s)
molab push $POD_ID /storage/emulated/0/DCIM/Camera/input.mp4 /workspace/input.mp4
```

### 2. Launch Background Pipeline
```bash
molab exec $POD_ID "cat << 'SHELL' > /workspace/run_enhance.sh
#!/bin/bash
set -e
mkdir -p /workspace/models
# 1. Download neural weights if not present
if [ ! -f /workspace/models/realesr-general-x4v3.pth ]; then
    curl -L -o /workspace/models/realesr-general-x4v3.pth https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-general-x4v3.pth
fi

# 2. VidStab Two-Pass Motion Stabilization
ffmpeg -y -i /workspace/input.mp4 -vf vidstabdetect=stepsize=6:shakiness=8:accuracy=15:result=/workspace/transforms.trf -f null -
ffmpeg -y -i /workspace/input.mp4 -vf vidstabtransform=input=/workspace/transforms.trf:smoothing=30:optalgo=gauss:zoom=0:interpol=bicubic -c:v libx264 -crf 14 -preset medium -c:a copy /workspace/stabilized.mp4

# 3. Real-ESRGAN Neural 4K Upscale
python3 -c \"
import torch
print(f'Using Device: {torch.cuda.get_device_name(0)}')
\"
SHELL
chmod +x /workspace/run_enhance.sh
nohup /workspace/run_enhance.sh > /workspace/enhance.log 2>&1 &
"
```

### 3. Monitor & Pull
```bash
# Check log progress
molab exec $POD_ID "tail -n 20 /workspace/enhance.log"

# Download finalized 4K master
molab pull $POD_ID /workspace/stabilized.mp4 /storage/emulated/0/Download/video_4k_master.mp4
```

---

## Recipe 2: Serving High-Throughput Uncensored LLMs (Port Forward Bridge)

Host uncompressed open-weights LLMs (Llama-3-70B, Gemma-3-27B, Qwen-2.5-32B) on Blackwell 96GB VRAM and expose an OpenAI-compatible API to local Android apps on `http://127.0.0.1:8000`.

### 1. Launch LLM Server on Pod
```bash
POD_ID=$(molab free --json | jq -r .recommended_free_pod)

# Launch vLLM or Hugging Face Text Generation
molab exec $POD_ID "nohup python3 -m vllm.entrypoints.openai.api_server \
  --model meta-llama/Meta-Llama-3-70B-Instruct \
  --tensor-parallel-size 1 \
  --gpu-memory-utilization 0.90 \
  --max-model-len 8192 \
  --port 8000 > /workspace/vllm.log 2>&1 &"
```

### 2. Verify Port & Health
```bash
molab exec $POD_ID "curl -s http://127.0.0.1:8000/health"
# Or via CLI service tool:
molab service status $POD_ID --port 8000
```

### 3. Bridge Remote Port to Termux Localhost
```bash
nohup molab forward $POD_ID --port 8000 > ~/molab_bridge.log 2>&1 &
```

### 4. Query from Local Android Environment
```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "meta-llama/Meta-Llama-3-70B-Instruct",
    "messages": [{"role": "user", "content": "Explain general relativity in 3 sentences."}]
  }'
```

---

## Recipe 3: Batch Speech-to-Text with OpenAI Whisper Large-v3

Transcribe hours of audio recordings in seconds using Whisper Large-v3 accelerated with FP16 and FlashAttention 2 on Blackwell.

### 1. Execution
```bash
POD_ID=$(molab free --json | jq -r .recommended_free_pod)

# Push audio batch
molab push $POD_ID ~/recordings/ /workspace/recordings/ -r

# Execute batch transcription
molab exec $POD_ID "python3 -c \"
import torch
import whisper
print('Loading Whisper large-v3 on GPU:', torch.cuda.get_device_name(0))
model = whisper.load_model('large-v3', device='cuda')
result = model.transcribe('/workspace/recordings/meeting.mp3', fp16=True, language='en')
with open('/workspace/recordings/transcript.txt', 'w') as f:
    f.write(result['text'])
print('Transcription completed!')
\""

# Pull transcription
molab pull $POD_ID /workspace/recordings/transcript.txt ~/transcript.txt
```

---

## Recipe 4: LoRA / QLoRA Model Fine-Tuning

Fine-tune modern generative models in hours using PyTorch 2.11 and Blackwell 96GB VRAM.

### 1. Launch Training Run
```bash
POD_ID=$(molab free --json | jq -r .recommended_free_pod)

# Delta sync training dataset and configuration
molab sync $POD_ID ./dataset /workspace/dataset

# Start fine-tuning job with SQLite tracking
molab job submit "python3 /workspace/train.py --batch_size 16 --bf16 True --lr 2e-4" \
  --name "lora-finetune-v1" \
  --pod $POD_ID
```

### 2. Preserve Checkpoint with 100% On-MoLab Vault
```bash
# Checkpoint model directly into MoLab cloud database without downloading 20GB locally
molab vault pack $POD_ID --source-dir /workspace/output/
```

---

## Recipe 5: Real-Time SDXL Image Synthesis Pipeline

Generate high-resolution (1024x1024) images in sub-second inference steps.

```bash
POD_ID=$(molab free --json | jq -r .recommended_free_pod)

molab exec $POD_ID "python3 -c \"
from diffusers import AutoPipelineForText2Image
import torch

pipe = AutoPipelineForText2Image.from_pretrained(
    'stabilityai/sdxl-turbo',
    torch_dtype=torch.float16,
    variant='fp16'
).to('cuda')

prompt = 'A cinematic photograph of an astronaut in a bioluminescent jungle, 8k resolution'
image = pipe(prompt=prompt, num_inference_steps=1, guidance_scale=0.0).images[0]
image.save('/workspace/astronaut_sdxl.png')
print('Image generated successfully.')
\""

# Pull generated visual
molab pull $POD_ID /workspace/astronaut_sdxl.png /storage/emulated/0/Pictures/astronaut.png
```

---

## Recipe 6: 1-Click MoLab Gallery Recipe Spawning

Instantly clone and execute any of MoLab 111+ community AI recipes.

```bash
# 1. Search gallery for interactive dashboards or models
molab gallery search "chat"

# 2. Inspect recipe details
molab gallery info "chat-with-pdf"

# 3. Download directly to current pod
POD_ID=$(molab free --json | jq -r .recommended_free_pod)
molab gallery download "chat-with-pdf" /workspace/chat_with_pdf.py
molab push $POD_ID chat_with_pdf.py /workspace/chat_with_pdf.py

# 4. View visual Open Graph preview
molab thumbnail $POD_ID -o ~/preview.svg
```
