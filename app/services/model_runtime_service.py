"""Detect local generative-model files, hardware fit, and callable runtimes."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import requests

from app.core.config import settings


@dataclass(frozen=True, slots=True)
class LocalModelDefinition:
    model_id: str
    label: str
    category: str
    backend: str
    minimum_vram_gb: float
    relative_files: tuple[str, ...] = ()
    directory_name: str = ""
    minimum_directory_gb: float = 0.0
    adapter_supported: bool = False
    required_nodes: tuple[str, ...] = ()


@dataclass(slots=True)
class LocalModelStatus:
    model_id: str
    label: str
    category: str
    install_path: Path
    installed: bool
    compatible: bool
    runtime_online: bool
    adapter_supported: bool
    callable: bool
    minimum_vram_gb: float
    installed_size_gb: float = 0.0
    message: str = ""


@dataclass(slots=True)
class LocalRuntimeInventory:
    gpu_name: str = "未检测到 NVIDIA GPU"
    vram_gb: float = 0.0
    ram_gb: float = 0.0
    model_root: Path = Path()
    comfy_root: Path = Path()
    disk_free_gb: float = 0.0
    comfy_online: bool = False
    comfy_url: str = ""
    models: list[LocalModelStatus] = field(default_factory=list)
    message: str = ""


LOCAL_MODEL_DEFINITIONS: tuple[LocalModelDefinition, ...] = (
    LocalModelDefinition(
        "juggernaut_xi",
        "Juggernaut XI（SDXL）",
        "生图",
        "comfyui",
        8.0,
        relative_files=(
            "ComfyUI/models/checkpoints/Juggernaut_XI/"
            "Juggernaut-XI-byRunDiffusion.safetensors",
        ),
        adapter_supported=True,
        required_nodes=("CheckpointLoaderSimple",),
    ),
    LocalModelDefinition(
        "flux_krea",
        "FLUX.1 Krea Dev FP8",
        "生图",
        "comfyui",
        8.0,
        relative_files=(
            "ComfyUI/models/diffusion_models/"
            "flux1-krea-dev_fp8_scaled.safetensors",
            "ComfyUI/models/text_encoders/clip_l.safetensors",
            "ComfyUI/models/text_encoders/t5xxl_fp8_e4m3fn.safetensors",
            "ComfyUI/models/vae/ae.safetensors",
        ),
        adapter_supported=True,
        required_nodes=("UNETLoader", "DualCLIPLoader", "VAELoader"),
    ),
    LocalModelDefinition(
        "minimax_h3_fl2va",
        "MiniMax H3 FL2VA（INT8/NVFP4）",
        "音视频生成",
        "comfyui",
        24.0,
        relative_files=(
            "ComfyUI/models/diffusion_models/"
            "minimax_h3_fl2va_pruned_int8_convrot.safetensors",
            "ComfyUI/models/text_encoders/"
            "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
            "ComfyUI/models/vae/minimax_h3_video_vae_fp16.safetensors",
            "ComfyUI/models/vae/minimax_h3_audio_vae_fp32.safetensors",
        ),
        adapter_supported=True,
        required_nodes=(
            "MiniMaxH3ImageToVideo",
            "VAEDecodeAudio",
            "CreateVideo",
            "SaveVideo",
        ),
    ),
    LocalModelDefinition(
        "latentsync_1_6",
        "LatentSync 1.6",
        "口型",
        "latentsync",
        18.0,
        relative_files=(
            "LatentSync/checkpoints/latentsync_unet.pt",
            "LatentSync/checkpoints/whisper/tiny.pt",
        ),
    ),
)


class LocalModelRuntimeService:
    """Read-only inventory used by the model center and routing decisions."""

    def __init__(
        self,
        model_root: Path | None = None,
        *,
        comfy_url: str | None = None,
    ) -> None:
        configured = os.getenv("LOCAL_AI_ROOT", "").strip()
        self.model_root = Path(
            model_root
            or configured
            or settings.models_dir / "generative"
        ).resolve()
        raw_url = (comfy_url or settings.comfyui_url).strip().rstrip("/")
        self.comfy_url = (
            raw_url
            if raw_url.startswith(("http://", "https://"))
            else f"http://{raw_url}"
        )

    @property
    def comfy_root(self) -> Path:
        return self.model_root / "ComfyUI"

    def check_status(self) -> LocalRuntimeInventory:
        gpu_name, vram_gb = self._gpu_info()
        ram_gb = self._ram_gb()
        disk_target = self._existing_parent(self.model_root)
        disk_free = shutil.disk_usage(disk_target).free / 1024**3
        comfy_online, nodes = self._comfy_status()
        models = [
            self._model_status(
                definition,
                vram_gb=vram_gb,
                comfy_online=comfy_online,
                nodes=nodes,
            )
            for definition in LOCAL_MODEL_DEFINITIONS
        ]
        installed = sum(model.installed for model in models)
        callable_count = sum(model.callable for model in models)
        return LocalRuntimeInventory(
            gpu_name=gpu_name,
            vram_gb=vram_gb,
            ram_gb=ram_gb,
            model_root=self.model_root,
            comfy_root=self.comfy_root,
            disk_free_gb=round(disk_free, 1),
            comfy_online=comfy_online,
            comfy_url=self.comfy_url,
            models=models,
            message=(
                f"检测到 {installed}/{len(models)} 个模型，"
                f"{callable_count} 个当前可调用"
            ),
        )

    def _model_status(
        self,
        definition: LocalModelDefinition,
        *,
        vram_gb: float,
        comfy_online: bool,
        nodes: set[str],
    ) -> LocalModelStatus:
        paths = [self.model_root / value for value in definition.relative_files]
        if paths:
            installed = all(path.is_file() and path.stat().st_size > 0 for path in paths)
            size = sum(path.stat().st_size for path in paths if path.is_file())
            install_path = paths[0].parent
        else:
            install_path = self.model_root / definition.directory_name
            size = self._directory_model_bytes(install_path)
            installed = (
                install_path.is_dir()
                and size >= definition.minimum_directory_gb * 1024**3
            )
        compatible = vram_gb + 0.05 >= definition.minimum_vram_gb
        if definition.backend == "comfyui":
            runtime_online = comfy_online and all(
                node in nodes for node in definition.required_nodes
            )
        elif definition.backend == "latentsync":
            runtime_online = (
                (self.model_root / "LatentSync" / "scripts" / "inference.py")
                .is_file()
            )
        else:
            runtime_online = False
        callable_now = (
            installed
            and compatible
            and runtime_online
            and definition.adapter_supported
        )
        if not installed:
            message = "未安装"
        elif not compatible:
            message = (
                f"已安装，但至少需要 {definition.minimum_vram_gb:g}GB 显存"
            )
        elif not definition.adapter_supported:
            message = "模型已安装，应用适配器待接入"
        elif not runtime_online:
            message = "文件完整，运行服务或所需节点未就绪"
        else:
            message = "本机可调用"
        return LocalModelStatus(
            model_id=definition.model_id,
            label=definition.label,
            category=definition.category,
            install_path=install_path,
            installed=installed,
            compatible=compatible,
            runtime_online=runtime_online,
            adapter_supported=definition.adapter_supported,
            callable=callable_now,
            minimum_vram_gb=definition.minimum_vram_gb,
            installed_size_gb=round(size / 1024**3, 2),
            message=message,
        )

    def _comfy_status(self) -> tuple[bool, set[str]]:
        try:
            health = requests.get(
                f"{self.comfy_url}/system_stats",
                timeout=2,
            )
            health.raise_for_status()
            info = requests.get(
                f"{self.comfy_url}/object_info",
                timeout=4,
            )
            info.raise_for_status()
            payload = info.json()
            return True, set(payload) if isinstance(payload, dict) else set()
        except Exception:
            return False, set()

    @staticmethod
    def _gpu_info() -> tuple[str, float]:
        try:
            process = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=name,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=8,
                check=True,
                creationflags=(
                    subprocess.CREATE_NO_WINDOW
                    if hasattr(subprocess, "CREATE_NO_WINDOW")
                    else 0
                ),
            )
            first = process.stdout.splitlines()[0]
            name, memory = [part.strip() for part in first.split(",", 1)]
            return name, round(float(memory) / 1024, 1)
        except Exception:
            return "未检测到 NVIDIA GPU", 0.0

    @staticmethod
    def _ram_gb() -> float:
        if os.name == "nt":
            try:
                import ctypes

                class MemoryStatus(ctypes.Structure):
                    _fields_ = [
                        ("length", ctypes.c_ulong),
                        ("memory_load", ctypes.c_ulong),
                        ("total_phys", ctypes.c_ulonglong),
                        ("avail_phys", ctypes.c_ulonglong),
                        ("total_page_file", ctypes.c_ulonglong),
                        ("avail_page_file", ctypes.c_ulonglong),
                        ("total_virtual", ctypes.c_ulonglong),
                        ("avail_virtual", ctypes.c_ulonglong),
                        ("avail_extended_virtual", ctypes.c_ulonglong),
                    ]

                status = MemoryStatus()
                status.length = ctypes.sizeof(MemoryStatus)
                ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
                return round(status.total_phys / 1024**3, 1)
            except Exception:
                return 0.0
        try:
            return round(
                os.sysconf("SC_PAGE_SIZE")
                * os.sysconf("SC_PHYS_PAGES")
                / 1024**3,
                1,
            )
        except Exception:
            return 0.0

    @staticmethod
    def _directory_model_bytes(path: Path) -> int:
        if not path.is_dir():
            return 0
        total = 0
        for pattern in ("*.safetensors", "*.pt", "*.pth", "*.bin"):
            total += sum(
                item.stat().st_size
                for item in path.rglob(pattern)
                if item.is_file()
            )
        return total

    @staticmethod
    def _existing_parent(path: Path) -> Path:
        candidate = path
        while not candidate.exists() and candidate != candidate.parent:
            candidate = candidate.parent
        return candidate
