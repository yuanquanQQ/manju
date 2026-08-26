from types import SimpleNamespace

from app.services.gpu_service import GpuConnection
from app.services.latentsync_service import LatentSyncRemoteService


class _FakeGpuService:
    def _connect(self, _config):
        return SimpleNamespace(close=lambda: None)

    def _exec(self, _client, _command, *, timeout):
        assert timeout == 45
        return "\n".join(
            [
                "1",
                "0",
                "NVIDIA GeForce RTX 3090, 24576",
                str(5 * 1024**3),
                "1",
            ]
        )

    @staticmethod
    def _friendly_error(exc):
        return str(exc)


def test_remote_latentsync_status_requires_files_imports_and_vram() -> None:
    service = LatentSyncRemoteService(_FakeGpuService())

    status = service.check_status(GpuConnection(password="secret"))

    assert status.installed is True
    assert status.callable is True
    assert status.checkpoint_size_gb == 5.0
    assert status.memory_total_mb == 24576
    assert status.message == "LatentSync 1.6 已就绪"
