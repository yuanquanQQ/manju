"""Deployability of the MiniMax H3 Audio T8 custom-node bundle."""

import inspect
import tarfile
from pathlib import Path

from app.core.config import settings
from app.services.gpu_service import GpuServerService, GpuStatus

T8_NODES = (
    "MiniMaxH3AudioConditioningT8",
    "MiniMaxH3DualClockSamplerT8",
    "MiniMaxH3AVDecodeT8",
    "MiniMaxH3AudioMixT8",
    "MiniMaxH3OutputTrimT8",
)


def test_t8_deploy_script_and_archive_present() -> None:
    root = settings.project_root / "scripts" / "gpu" / "minimax_h3_t8"
    script = root / "install.sh"
    archive = root / "comfyui-minimax-h3-audio-T8-main.tar.gz"
    assert script.is_file(), f"missing {script}"
    assert archive.is_file(), f"missing {archive}"
    assert archive.stat().st_size > 1_000_000, "T8 archive looks truncated"


def test_t8_archive_is_flat_bundle_with_core_modules() -> None:
    archive = (
        settings.project_root
        / "scripts"
        / "gpu"
        / "minimax_h3_t8"
        / "comfyui-minimax-h3-audio-T8-main.tar.gz"
    )
    with tarfile.open(archive, "r:gz") as handle:
        names = handle.getnames()
    prefix = "comfyui-minimax-h3-audio-T8-main"
    assert names and all(name.startswith(prefix) for name in names)
    for required in (
        f"{prefix}/nodes.py",
        f"{prefix}/__init__.py",
        f"{prefix}/conditioning.py",
        f"{prefix}/audio_ops.py",
        f"{prefix}/README.md",
    ):
        assert required in names, f"archive missing {required}"


def test_check_status_bash_probes_all_t8_nodes() -> None:
    source = inspect.getsource(GpuServerService.check_status)
    for node in T8_NODES:
        assert f'grep -q \'"{node}"\'' in source, f"check_status probe missing {node}"
    assert "$t8_nodes_ready" in source


def test_ensure_remote_t8_greps_five_core_nodes() -> None:
    source = inspect.getsource(GpuServerService._ensure_remote_t8)
    for node in T8_NODES:
        assert f'grep -q \'"{node}"\'' in source, f"_ensure_remote_t8 missing {node}"


def test_gpu_status_has_t8_field() -> None:
    status = GpuStatus()
    assert hasattr(status, "t8_runtime_ready")
    assert status.t8_runtime_ready is False
