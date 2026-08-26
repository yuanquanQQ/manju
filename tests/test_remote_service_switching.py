from types import SimpleNamespace

import pytest

from app.services.cosyvoice_service import CosyVoiceRemoteService, CosyVoiceStatus
from app.services.gpu_service import GpuConnection, GpuServerService


def test_ensure_comfy_releases_cosyvoice_and_starts_runtime(monkeypatch) -> None:
    service = GpuServerService()
    events: list[str] = []
    probes = iter((False, True))

    monkeypatch.setattr(
        service,
        "_stop_remote_cosyvoice",
        lambda _client: events.append("stop_cosy") or True,
    )
    monkeypatch.setattr(
        service,
        "_probe_remote_comfy",
        lambda _client: next(probes),
    )

    def fake_exec(_client, command: str, *, timeout: int) -> str:
        assert timeout == 8
        if "pgrep" in command:
            return ""
        assert "setsid" in command
        events.append("start_comfy")
        return ""

    monkeypatch.setattr(service, "_exec", fake_exec)
    monkeypatch.setattr("app.services.gpu_service.time.sleep", lambda _seconds: None)

    service._ensure_remote_comfy(SimpleNamespace())

    assert events == ["stop_cosy", "start_comfy"]


def test_stop_comfy_refuses_to_interrupt_nonempty_queue(monkeypatch) -> None:
    service = GpuServerService()
    monkeypatch.setattr(service, "_probe_remote_comfy", lambda _client: True)
    monkeypatch.setattr(
        service,
        "_exec",
        lambda _client, command, *, timeout: (
            '{"queue_running": [[1]], "queue_pending": []}'
            if command.endswith("/queue")
            else pytest.fail("ComfyUI process must not be stopped")
        ),
    )

    with pytest.raises(RuntimeError, match="仍有生成任务"):
        service._stop_remote_comfy(SimpleNamespace(), require_idle=True)


class _FakeGpuService:
    def __init__(self) -> None:
        self.events: list[str] = []

    def _connect(self, _config):
        return SimpleNamespace(close=lambda: None)

    def _stop_remote_comfy(self, _client, *, require_idle: bool) -> bool:
        assert require_idle is True
        self.events.append("stop_comfy")
        return True

    def _exec(self, _client, command: str, *, timeout: int) -> str:
        assert timeout == 20
        assert command.endswith("/root/cosyvoice-service/start.sh")
        self.events.append("start_cosy")
        return ""


def test_cosyvoice_start_switches_from_idle_comfy() -> None:
    gpu = _FakeGpuService()
    service = CosyVoiceRemoteService(gpu)
    service.check_status = lambda _config: CosyVoiceStatus(online=True)

    status = service.start(GpuConnection(password="secret"))

    assert status.online is True
    assert gpu.events == ["stop_comfy", "start_cosy"]
