# MoLab Multi-Pod DAG Batch Orchestration Engine

The MoLab Batch Orchestration subsystem enables autonomous AI agents and engineers to schedule and execute complex Directed Acyclic Graph (DAG) pipelines across clusters of NVIDIA RTX PRO 6000 Blackwell (96GB VRAM) pods.

---

## 1. Core Architecture

```
                  ┌─────────────────────────────────────────┐
                  │       Batch Pipeline Manifest           │
                  │        (YAML / JSON DAG Spec)           │
                  └────────────────────┬────────────────────┘
                                       │
                                       ▼
                  ┌─────────────────────────────────────────┐
                  │           BatchOrchestrator             │
                  │   - Topological Dependency Sorter       │
                  │   - Dynamic Blackwell Worker Pool       │
                  │   - Inter-Pod Artifact Router           │
                  │   - SQLite Run State Machine            │
                  └────────────┬───────────────┬────────────┘
                               │               │
                 Task A (Free) │               │ Task B (Free)
                               ▼               ▼
                       ┌──────────────┐ ┌──────────────┐
                       │ Blackwell #1 │ │ Blackwell #2 │
                       │ 96GB VRAM    │ │ 96GB VRAM    │
                       └──────┬───────┘ └──────┬───────┘
                              │                │
                              └───────┬────────┘
                                      ▼
                               Task C (Aggregator)
                               (Waits on A & B)
```

---

## 2. Pipeline Manifest Specification

A pipeline manifest defines tasks, shell commands, hardware requirements, retry limits, and task dependencies.

### JSON Manifest Example (`pipeline.json`)
```json
{
  "name": "cinematic-4k-video-mastering",
  "concurrency": 2,
  "tasks": [
    {
      "id": "extract-audio",
      "command": "ffmpeg -y -i /workspace/input.mp4 -vn -c:a aac /workspace/audio.aac",
      "workdir": "/workspace",
      "gpu_required": false,
      "depends_on": []
    },
    {
      "id": "stabilize-motion",
      "command": "ffmpeg -y -i /workspace/input.mp4 -vf vidstabdetect=stepsize=6:shakiness=8:accuracy=15:result=/workspace/transforms.trf -f null -",
      "workdir": "/workspace",
      "gpu_required": true,
      "depends_on": []
    },
    {
      "id": "ai-upscale-frames",
      "command": "python3 /workspace/upscale_esrgan.py --input /workspace/input.mp4 --transforms /workspace/transforms.trf",
      "workdir": "/workspace",
      "gpu_required": true,
      "depends_on": ["stabilize-motion"]
    },
    {
      "id": "final-multiplex",
      "command": "ffmpeg -y -i /workspace/upscaled.mp4 -i /workspace/audio.aac -c:v copy -c:a copy /workspace/final_4k.mp4",
      "workdir": "/workspace",
      "gpu_required": false,
      "depends_on": ["extract-audio", "ai-upscale-frames"]
    }
  ]
}
```

---

## 3. CLI Workflow

### Validate Manifest
Validate JSON syntax, circular dependency absence, and DAG acyclicity:
```bash
molab batch validate pipeline.json
```

### Submit Pipeline
Submit to the orchestrator queue:
```bash
molab batch submit pipeline.json
# Outputs: Batch batch_9f1a23c submitted with 4 tasks.
```

### Monitor Live Execution
```bash
molab batch status batch_9f1a23c
```

### Inspect Output Logs
```bash
molab batch logs batch_9f1a23c --task ai-upscale-frames
```

### Retry Failed Tasks
If a task fails due to a network glitch or spot interruption, retry without re-running finished upstream dependencies:
```bash
molab batch retry batch_9f1a23c
molab batch run batch_9f1a23c
```

---

## 4. Programmatic Python SDK Usage

```python
from molab_cli.sdk import MoLabSDK
from molab_cli.batch import BatchOrchestrator

sdk = MoLabSDK()
orchestrator = BatchOrchestrator()

# Define pipeline
pipeline_def = {
    "name": "parallel-model-evaluation",
    "concurrency": 2,
    "tasks": [
        {
            "id": "eval-model-a",
            "command": "python3 /workspace/eval.py --model llama-3-8b --out /workspace/out_a.json",
            "depends_on": []
        },
        {
            "id": "eval-model-b",
            "command": "python3 /workspace/eval.py --model mistral-7b --out /workspace/out_b.json",
            "depends_on": []
        },
        {
            "id": "compare-benchmarks",
            "command": "python3 /workspace/compare.py /workspace/out_a.json /workspace/out_b.json",
            "depends_on": ["eval-model-a", "eval-model-b"]
        }
    ]
}

# Submit and wait
batch_id = orchestrator.submit(pipeline_def)
print(f"Batch submitted: {batch_id}")

final_state = orchestrator.wait(batch_id, poll_interval=5)
print("Finished with status:", final_state["status"])
```

---

## 5. Fault Tolerance & Pod Recovery

1. **Autonomous Worker Selection:** The orchestrator never hardcodes pod IDs. At runtime, tasks dynamically discover and claim idle Blackwell pods via `molab_get_free_pod()`.
2. **Safe Multi-Tenant Protection:** Occupied pods running active user sessions are skipped automatically.
3. **Task Retries:** Tasks can define `"retries": 3`. If a command exits non-zero, the task will be re-queued with exponential backoff.
4. **Permanent State Vaulting:** Completed tasks can trigger `vault_pack` to guarantee intermediate artifacts survive container teardowns.
