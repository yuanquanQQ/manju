from argparse import Namespace
from pathlib import Path

from scripts.generate_episode_h3 import (
    _native_dialogue_prompt,
    _pending_h3_candidates,
    _valid_h3_candidate,
)
from workflows.minimax_h3.generate_video import (
    H3_AUDIO_VAE,
    H3_MODEL,
    build_prompt,
    create_video_qc_sheet,
    find_generated_video,
    normalize_frame_count,
    probe_video,
    run,
)


def test_h3_frame_count_uses_seventeen_k_plus_five_grid() -> None:
    assert normalize_frame_count(120) == 124
    assert normalize_frame_count(124) == 124
    assert normalize_frame_count(1) == 5


def test_h3_api_prompt_generates_joint_audio_video() -> None:
    prompt = build_prompt(
        image_name="novel2anime/start.png",
        end_image_name="novel2anime/end.png",
        positive_prompt="一段连续动作；自然环境音和低声配乐，无对白",
        width=832,
        height=480,
        frame_count=120,
        fps=24,
        seed=42,
        filename_prefix="novel2anime/run/candidate_01",
    )

    assert prompt["1"]["inputs"]["unet_name"] == H3_MODEL
    assert prompt["7"]["class_type"] == "MiniMaxH3ImageToVideo"
    assert prompt["7"]["inputs"]["first_frame"] == ["5", 0]
    assert prompt["7"]["inputs"]["last_frame"] == ["6", 0]
    assert prompt["7"]["inputs"]["length"] == 124
    assert prompt["14"]["inputs"]["vae"] == ["4", 0]
    assert prompt["4"]["inputs"]["vae_name"] == H3_AUDIO_VAE
    assert prompt["15"]["inputs"]["audio"] == ["14", 0]
    assert prompt["16"]["class_type"] == "SaveVideo"


def test_h3_prompt_allows_first_frame_only() -> None:
    prompt = build_prompt(
        image_name="novel2anime/start.png",
        positive_prompt="自然动作",
        width=832,
        height=480,
        frame_count=124,
        fps=24,
        seed=1,
        filename_prefix="novel2anime/run/candidate_01",
    )

    assert "6" not in prompt
    assert "last_frame" not in prompt["7"]["inputs"]


def test_find_generated_h3_video_uses_unique_prefix(tmp_path: Path) -> None:
    output = tmp_path / "output" / "novel2anime" / "run"
    output.mkdir(parents=True)
    expected = output / "candidate_01_00001_.mp4"
    expected.write_bytes(b"mp4")

    found = find_generated_video(
        tmp_path,
        "novel2anime/run/candidate_01",
        submitted_at=expected.stat().st_mtime,
    )

    assert found == expected


def test_h3_run_reads_utf8_prompt_file(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.png"
    source.write_bytes(b"png")
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("秦风缓慢抬头", encoding="utf-8")
    captured: dict[str, str] = {}

    def fake_build_prompt(**kwargs):
        captured["positive_prompt"] = kwargs["positive_prompt"]
        return {}

    monkeypatch.setattr(
        "workflows.minimax_h3.generate_video._copy_input",
        lambda *_args: "source.png",
    )
    monkeypatch.setattr(
        "workflows.minimax_h3.generate_video.build_prompt",
        fake_build_prompt,
    )
    monkeypatch.setattr(
        "workflows.minimax_h3.generate_video.request_json",
        lambda *_args, **_kwargs: {"prompt_id": "prompt-1"},
    )
    monkeypatch.setattr(
        "workflows.minimax_h3.generate_video.wait_for_prompt",
        lambda *_args, **_kwargs: {},
    )
    generated = tmp_path / "generated.mp4"
    generated.write_bytes(b"mp4")
    monkeypatch.setattr(
        "workflows.minimax_h3.generate_video.find_generated_video",
        lambda *_args, **_kwargs: generated,
    )

    run(
        Namespace(
            comfy_root=str(tmp_path),
            source_image=str(source),
            end_image="",
            output_dir=str(tmp_path / "output"),
            positive_prompt="",
            positive_prompt_file=str(prompt_file),
            candidate_count=1,
            seed=1,
            run_name="test",
            width=832,
            height=480,
            frame_count=124,
            fps=24,
            comfy_url="http://127.0.0.1:8188",
            timeout_seconds=10,
        )
    )

    assert captured["positive_prompt"] == "秦风缓慢抬头"


def test_probe_video_requires_video_audio_and_expected_geometry(
    tmp_path: Path, monkeypatch
) -> None:
    video = tmp_path / "candidate.mp4"
    video.write_bytes(b"mp4")
    payload = {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 832,
                "height": 480,
                "avg_frame_rate": "24/1",
                "duration": "5.1667",
            },
            {
                "codec_type": "audio",
                "codec_name": "aac",
                "sample_rate": "48000",
                "channels": 2,
                "duration": "5.1667",
            },
        ],
        "format": {"duration": "5.1667"},
    }

    class Result:
        returncode = 0
        stdout = __import__("json").dumps(payload)
        stderr = ""

    monkeypatch.setattr("shutil.which", lambda _name: "ffprobe")
    monkeypatch.setattr("subprocess.run", lambda *_args, **_kwargs: Result())

    qc = probe_video(
        video,
        expected_width=832,
        expected_height=480,
        expected_fps=24,
        expected_duration=124 / 24,
    )

    assert qc["technical_pass"] is True
    assert qc["approval_status"] == "pending_visual_motion_audio_review"
    assert qc["audio_duration_seconds"] == 5.1667
    assert qc["audio_channels"] == 2


def test_native_dialogue_prompt_locks_exact_mandarin_line_and_speaker() -> None:
    prompt = _native_dialogue_prompt(
        {
            "shot_number": 10,
            "dialogue": "秦风：三秋叔，你怎么在这里？",
            "audio_generation": {
                "mode": "dialogue",
                "speaker": "秦风",
                "text": "三秋叔，你怎么在这里？",
                "instruct_text": "平静询问，句尾不拖长。",
            },
        }
    )

    assert "标准中国普通话" in prompt
    assert "说话人：秦风" in prompt
    assert "必须逐字、只说一次：『三秋叔，你怎么在这里？』" in prompt
    assert "嘴唇与每个汉字自然同步" in prompt
    assert "平静询问" in prompt


def test_native_narration_stays_off_screen_without_lip_motion() -> None:
    prompt = _native_dialogue_prompt(
        {
            "shot_number": 1,
            "dialogue": "旁白：云影镇，秦家药圃。",
            "audio_generation": {
                "mode": "auto_narration",
                "speaker": "旁白",
                "text": "云影镇，秦家药圃。",
            },
        }
    )

    assert "画外旁白" in prompt
    assert "画面人物不得对口型" in prompt


def test_video_qc_sheet_samples_start_middle_and_end(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "candidate.mp4"
    destination = tmp_path / "qc.jpg"
    source.write_bytes(b"mp4")
    captured: list[str] = []

    class Result:
        returncode = 0

    def fake_run(command, **_kwargs):
        captured.extend(command)
        destination.write_bytes(b"jpg")
        return Result()

    monkeypatch.setattr("shutil.which", lambda _name: "ffmpeg")
    monkeypatch.setattr("subprocess.run", fake_run)

    assert create_video_qc_sheet(source, destination, frame_count=124) is True
    filter_value = captured[captured.index("-vf") + 1]
    assert "eq(n\\,0)" in filter_value
    assert "eq(n\\,62)" in filter_value
    assert "eq(n\\,123)" in filter_value


def test_episode_h3_only_resumes_selected_candidates(tmp_path: Path) -> None:
    project = tmp_path / "project"
    clip = project / "production" / "videos" / "candidate.mp4"
    manifest = clip.with_suffix(".json")
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"mp4")
    manifest.write_text(
        __import__("json").dumps(
            {
                "engine_profile": "minimax_h3_fl2va",
                "model_name": "minimax_h3_fl2va_pruned_int8_convrot.safetensors",
                "text_encoder": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
                "approval_status": "pending_visual_motion_audio_review",
                "native_audio_mode": "native_full",
                "dialogue_prompt": "说话人：秦风。必须逐字说出台词。",
                "technical_qc": {
                    "technical_pass": True,
                    "checks": {"has_audio": True},
                },
            }
        ),
        encoding="utf-8",
    )
    candidate = {
        "file": clip.relative_to(project).as_posix(),
        "manifest": manifest.relative_to(project).as_posix(),
    }
    shot = {"video_generation": {"candidates": [candidate]}}

    assert _valid_h3_candidate(project, shot) is None
    assert _pending_h3_candidates(project, shot) == [clip.resolve()]

    shot["video_generation"]["selected_video"] = candidate["file"]
    assert _valid_h3_candidate(project, shot) == (clip.resolve(), manifest.resolve())
