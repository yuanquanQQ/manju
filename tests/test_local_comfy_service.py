import json
from pathlib import Path
from types import SimpleNamespace

from app.services.local_comfy_service import LocalComfyGenerationService


class _Runtime:
    comfy_url = "http://localhost:8189"
    model_root = Path("E:/AIModels")

    @staticmethod
    def check_status():
        return SimpleNamespace(
            model_root=Path("E:/AIModels"),
            models=[
                SimpleNamespace(
                    model_id="juggernaut_xi",
                    callable=True,
                    message="本机可调用",
                )
            ],
        )


def test_local_character_generation_uses_existing_workflow(
    tmp_path: Path,
    monkeypatch,
) -> None:
    episode = tmp_path / "episode.json"
    episode.write_text(
        json.dumps(
            {"character_profiles": {"秦风": "十八岁青年"}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    output = tmp_path / "output"
    captured: list[str] = []
    service = LocalComfyGenerationService(_Runtime())

    def fake_run(command, *, timeout, output_callback=None):
        captured.extend(command)
        output.mkdir(parents=True, exist_ok=True)
        image = output / "character_01_juggernaut_xi_candidate_01.png"
        image.write_bytes(b"png")
        (output / "manifest.json").write_text(
            json.dumps(
                {
                    "images": [
                        {
                            "file": image.name,
                            "model_id": "juggernaut_xi",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        if output_callback:
            output_callback("[PROGRESS] 1/1 秦风")
        return "done"

    monkeypatch.setattr(service, "_run_streaming", fake_run)
    result = service.generate_character(
        episode_path=episode,
        character="秦风",
        model_ids=["juggernaut_xi"],
        layout_preset="portrait",
        count=1,
        seed=7,
        local_output_dir=output,
    )

    assert result.images[0].is_file()
    assert "--comfy-url" in captured
    assert "http://localhost:8189" in captured
