# MoLab Production Recipes

## Recipe 1: 4K Neural Video Enhancement (iPhone Cinematic Grade)

Enhance mobile video footage on the NVIDIA RTX PRO 6000 Blackwell GPU using Real-ESRGAN Compact AI and professional color grading.

### Prerequisites:
- Source video on Android: `/storage/emulated/0/DCIM/Camera/input.mp4`
- Active Blackwell pod: Check with `molab free`

### Execution Steps:

1. **Find Free Pod:**
   ```bash
   POD_ID=$(molab free --json | jq -r .recommended_free_pod)
   ```

2. **Upload Video:**
   ```bash
   molab push $POD_ID /storage/emulated/0/DCIM/Camera/input.mp4 /workspace/video/input.mp4
   ```

3. **Run Two-Pass Motion Stabilization & Real-ESRGAN Upscaling:**
   ```bash
   # Download weights on pod
   molab exec $POD_ID "mkdir -p /workspace/models && curl -s -L -o /workspace/models/realesr-general-x4v3.pth https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-general-x4v3.pth"

   # Calculate motion stabilization vectors
   molab exec $POD_ID "ffmpeg -y -i /workspace/video/input.mp4 -vf vidstabdetect=stepsize=6:shakiness=8:accuracy=15:result=/workspace/video/transforms.trf -f null -"

   # Run 4K enhancement script in background
   molab exec $POD_ID "nohup python3 /workspace/enhance_4k_pipeline.py > /workspace/video/enhance.log 2>&1 &"
   ```

4. **Monitor & Download:**
   ```bash
   molab exec $POD_ID "tail -n 10 /workspace/video/enhance.log"
   molab pull $POD_ID /workspace/video/video_4k_enhanced.mp4 /storage/emulated/0/Download/
   ```

---

## Recipe 2: Serving Uncensored LLM with Localhost Port Bridge

Deploy an uncensored, uncompressed open LLM (e.g. Gemma 3 27B or Llama 3) on the Blackwell 96GB GPU and access it locally at `http://localhost:8000/v1`.

### Execution Steps:

1. **Verify GPU Telemetry:**
   ```bash
   molab gpu $POD_ID
   ```

2. **Launch Model Server on Pod (Port 8000):**
   ```bash
   molab exec $POD_ID "nohup python3 /workspace/server.py > /workspace/server.log 2>&1 &"
   molab exec $POD_ID "curl -s http://127.0.0.1:8000/health"
   ```

3. **Start Localhost Bridge in Background:**
   ```bash
   nohup molab forward $POD_ID --port 8000 > ~/bridge.log 2>&1 &
   ```

4. **Query Locally:**
   ```bash
   curl http://localhost:8000/v1/chat/completions \
     -H "Content-Type: application/json" \
     -d '{
       "model": "gemma-3-27b",
       "messages": [{"role": "user", "content": "Hello!"}]
     }'
   ```
