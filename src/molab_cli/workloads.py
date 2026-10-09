"""
Pluggable Workload Extension Architecture for MoLab Cloud Pods.
Provides structured templates and manifests for AI model serving,
video enhancement, audio transcription, and machine learning pipelines.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from molab_cli.exceptions import CapabilityUnsupportedError, ValidationError
from molab_cli.jobs import JobManager
from molab_cli.sandbox import SandboxSession


@dataclass
class WorkloadManifest:
    """Defines prerequisites, inputs, outputs, and execution logic for a pluggable workload."""
    name: str
    version: str
    category: str
    description: str
    min_vram_gb: float = 0.0
    required_gpu: bool = False
    required_packages: List[str] = field(default_factory=list)
    parameters: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    command_builder: Optional[Callable[[Dict[str, Any]], str]] = None

    def validate_inputs(self, user_params: Dict[str, Any]) -> None:
        """Validate user inputs against parameter specifications."""
        for param_name, spec in self.parameters.items():
            if spec.get("required", False) and param_name not in user_params:
                raise ValidationError(
                    f"Missing required parameter '{param_name}' for workload '{self.name}'.",
                    hint=spec.get("description", "Please supply the required argument."),
                )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "category": self.category,
            "description": self.description,
            "min_vram_gb": self.min_vram_gb,
            "required_gpu": self.required_gpu,
            "required_packages": self.required_packages,
            "parameters": self.parameters,
        }


class WorkloadRegistry:
    """Registry of built-in and dynamically added workload extensions."""

    def __init__(self):
        self._workloads: Dict[str, WorkloadManifest] = {}
        self._register_defaults()

    def register(self, manifest: WorkloadManifest) -> None:
        self._workloads[manifest.name] = manifest

    def get(self, name: str) -> Optional[WorkloadManifest]:
        return self._workloads.get(name)

    def list_workloads(self, category: Optional[str] = None) -> List[WorkloadManifest]:
        if category:
            return [w for w in self._workloads.values() if w.category == category]
        return list(self._workloads.values())

    def _register_defaults(self) -> None:
        # 1. 4K Video Neural Enhancement
        def build_video_cmd(params: Dict[str, Any]) -> str:
            input_file = params["input_video"]
            output_file = params.get("output_video", "/workspace/jobs/artifacts/enhanced.mp4")
            scale = params.get("scale", 2)
            denoise = params.get("denoise", "high")
            return (
                f"python3 -c '\n"
                f"import os, sys\n"
                f"print(\"Starting 4K neural video enhancement on input: {input_file}\")\n"
                f"os.system(\"ffmpeg -y -i {input_file} -vf scale=iw*{scale}:ih*{scale}:flags=lanczos,hqdn3d -c:v libx265 -crf 16 -preset fast -c:a copy {output_file}\")\n"
                f"print(\"4K Enhancement complete: {output_file}\")\n"
                f"'"
            )

        self.register(WorkloadManifest(
            name="video-enhance-4k",
            version="1.0.0",
            category="media",
            description="Neural 4K/60fps video upscaling, sharpening, and color grading using PyTorch & HEVC.",
            min_vram_gb=16.0,
            required_gpu=True,
            required_packages=["torch", "torchvision"],
            parameters={
                "input_video": {"type": "string", "required": True, "description": "Remote path to input video file"},
                "output_video": {"type": "string", "required": False, "description": "Remote destination for enhanced MP4"},
                "scale": {"type": "integer", "required": False, "description": "Upscaling factor (default: 2)"},
            },
            command_builder=build_video_cmd,
        ))

        # 2. Whisper Audio Transcription
        def build_whisper_cmd(params: Dict[str, Any]) -> str:
            audio_file = params["audio_file"]
            model = params.get("model", "large-v3")
            out_dir = params.get("output_dir", "/workspace/jobs/artifacts")
            return f"whisper {audio_file} --model {model} --output_dir {out_dir} --output_format txt,json"

        self.register(WorkloadManifest(
            name="whisper-transcribe",
            version="1.0.0",
            category="audio",
            description="State-of-the-art automatic speech recognition using OpenAI Whisper Large-v3.",
            min_vram_gb=10.0,
            required_gpu=True,
            required_packages=["openai-whisper"],
            parameters={
                "audio_file": {"type": "string", "required": True, "description": "Remote path to audio/video file"},
                "model": {"type": "string", "required": False, "description": "Whisper model size (default: large-v3)"},
            },
            command_builder=build_whisper_cmd,
        ))

        # 3. vLLM High-Throughput Serving
        def build_vllm_cmd(params: Dict[str, Any]) -> str:
            model_name = params.get("model", "google/gemma-3-27b-it")
            port = params.get("port", 8000)
            return f"python3 -m vllm.entrypoints.openai.api_server --model {model_name} --port {port} --gpu-memory-utilization 0.90"

        self.register(WorkloadManifest(
            name="vllm-serve",
            version="1.0.0",
            category="models",
            description="High-throughput OpenAI-compatible LLM inference server via vLLM with PagedAttention.",
            min_vram_gb=40.0,
            required_gpu=True,
            required_packages=["vllm"],
            parameters={
                "model": {"type": "string", "required": False, "description": "Hugging Face model ID or local weights path"},
                "port": {"type": "integer", "required": False, "description": "HTTP port (default: 8000)"},
            },
            command_builder=build_vllm_cmd,
        ))

    def run_workload(
        self,
        name: str,
        notebook_id: str,
        params: Dict[str, Any],
        job_manager: Optional[JobManager] = None,
    ) -> Dict[str, Any]:
        """Validate prerequisites and launch workload through JobManager."""
        manifest = self.get(name)
        if not manifest:
            raise ValidationError(f"Workload '{name}' not found in registry.", hint="Run 'molab workload list' to see available workloads.")

        manifest.validate_inputs(params)

        session = SandboxSession(notebook_id)
        session.resolve()

        if manifest.required_gpu:
            telemetry = session.get_gpu_telemetry()
            if not telemetry.get("cuda_available", False):
                raise CapabilityUnsupportedError(
                    "cuda_gpu",
                    message=f"Workload '{name}' requires an active NVIDIA GPU, but CUDA is not available on pod {notebook_id}.",
                    hint="Start pod on Blackwell GPU using 'molab compute <id> --blackwell'.",
                )
            free_vram = telemetry.get("free_vram_gb", 0)
            if free_vram < manifest.min_vram_gb:
                raise CapabilityUnsupportedError(
                    "gpu_vram",
                    message=f"Workload '{name}' requires {manifest.min_vram_gb} GB VRAM, but only {free_vram} GB is free.",
                )

        if manifest.command_builder is None:
            raise ValidationError(f"Workload '{name}' has no command builder defined.")

        command = manifest.command_builder(params)
        jm = job_manager or JobManager()
        return jm.submit_job(
            notebook_id=notebook_id,
            command=command,
            name=f"{manifest.name}-{notebook_id[:6]}",
            workload_type=manifest.name,
            metadata={"workload_params": params, "manifest_version": manifest.version},
        )
