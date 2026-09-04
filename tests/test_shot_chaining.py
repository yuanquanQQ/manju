"""Shot-to-shot pixel chaining: shot N's first frame = shot N-1's last frame."""

from __future__ import annotations

import json
from pathlib import Path

from app.domain.video import VideoRenderSpec
from app.services.gpu_service import GpuServerService
from app.services.video_service import VideoBatchResult, VideoClipResult, VideoRenderService
from scripts.generate_episode_h3 import H3_GENERATION_REVISION, run


def _spec(shot_number: int, source: Path) -> VideoRenderSpec:
    return VideoRenderSpec(
        episode_number=1,
        shot_number=shot_number,
        source_image=source,
        fps=24,
        width=832,
        height=480,
        engine_profile="minimax_h3_fl2va",
        native_audio_mode="native_full",
    )


class _FakeProc:
    returncode = 0
    stdout = ""
    stderr = ""


def _patched_run(captured: list[list[str]]):
    def fake_run(self, command, *, timeout, check=True):
        captured.append(command)
        # Simulate ffmpeg writing the destination (last argv element).
        Path(command[-1]).write_bytes(b"png")
        return _FakeProc()

    return fake_run


def test_extract_last_frame_uses_select_with_frame_count(tmp_path, monkeypatch):
    service = VideoRenderService.__new__(VideoRenderService)
    service.ffmpeg_executable = Path("ffmpeg")
    captured: list[list[str]] = []

    monkeypatch.setattr(VideoRenderService, "_run", _patched_run(captured))

    source = tmp_path / "shot_001.mp4"
    source.write_bytes(b"mp4")
    destination = tmp_path / "chained.png"

    ok = service.extract_last_frame(source, destination, frame_count=124)
    assert ok is True
    args = captured[0]
    assert any("select=eq(n\\,123)" in arg for arg in args)
    assert "-frames:v" in args
    assert destination.is_file()


def test_extract_last_frame_falls_back_to_sseof_without_count(tmp_path, monkeypatch):
    service = VideoRenderService.__new__(VideoRenderService)
    service.ffmpeg_executable = Path("ffmpeg")
    captured: list[list[str]] = []

    monkeypatch.setattr(VideoRenderService, "_run", _patched_run(captured))

    source = tmp_path / "v.mp4"
    source.write_bytes(b"mp4")
    destination = tmp_path / "out.png"

    ok = service.extract_last_frame(source, destination)
    assert ok is True
    args = captured[0]
    assert "-sseof" in args
    assert "-0.5" in args


def test_extract_last_frame_returns_false_when_source_missing(tmp_path):
    service = VideoRenderService.__new__(VideoRenderService)
    service.ffmpeg_executable = Path("ffmpeg")
    ok = service.extract_last_frame(tmp_path / "nope.mp4", tmp_path / "out.png")
    assert ok is False


def test_revision_is_chained():
    assert H3_GENERATION_REVISION == "h3_t8_chained_v1"


def test_chained_prompt_uses_continue_seamlessly():
    spec = _spec(2, Path("a.png")).model_copy(update={"chained_from_previous": True})
    prompt = GpuServerService._h3_positive_prompt(spec)
    assert "Continue seamlessly from the previous shot's final frame" in prompt
    assert "inherited pose" in prompt


def test_unchained_prompt_uses_begin_from_first_frame():
    spec = _spec(1, Path("a.png"))
    prompt = GpuServerService._h3_positive_prompt(spec)
    assert "Begin exactly from the supplied first frame" in prompt


def _make_episode(project_root: Path, shot_numbers: list[int]) -> Path:
    episode_path = project_root / "production" / "episodes" / "episode_001.json"
    episode_path.parent.mkdir(parents=True, exist_ok=True)
    shots = []
    for n in shot_numbers:
        source = project_root / "production" / "video_inputs" / "episode_001" / f"shot_{n:03d}.png"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"png")
        shots.append(
            {
                "shot_number": n,
                "scene_description": "scene",
                "dialogue": f"秦风：台词{n}",
                "audio_generation": {
                    "mode": "dialogue",
                    "speaker": "秦风",
                    "text": f"台词{n}",
                },
                "characters": ["秦风"],
                "image_generation": {"qc_status": "approved"},
                "video_generation": {
                    "source_image": str(source.relative_to(project_root).as_posix()),
                    "subject_motion": "walk",
                    "duration_seconds": 3.0,
                },
            }
        )
    cast_path = project_root / "production" / "cast_selection.json"
    cast_path.parent.mkdir(parents=True, exist_ok=True)
    cast_path.write_text('{"selections": {"秦风": "ref.png"}}', encoding="utf-8")
    episode_path.write_text(
        json.dumps({"shots": shots}, ensure_ascii=False), encoding="utf-8"
    )
    return episode_path


def _fake_batch(shot_number: int, root: Path) -> VideoBatchResult:
    clip_dir = root / "production" / "videos" / "episode_001" / f"shot_{shot_number:03d}"
    clip_dir.mkdir(parents=True, exist_ok=True)
    video = clip_dir / f"shot_{shot_number:03d}_c01.mp4"
    video.write_bytes(b"mp4")
    manifest = clip_dir / f"shot_{shot_number:03d}_c01.json"
    manifest.write_text(
        json.dumps({"technical_qc": {"technical_pass": True}}), encoding="utf-8"
    )
    clip = VideoClipResult(
        episode_number=1,
        shot_number=shot_number,
        video_path=video,
        manifest_path=manifest,
        source_image=video,
        elapsed_seconds=1.0,
        candidate_index=1,
    )
    return VideoBatchResult(clips=[clip], job_id="job", elapsed_seconds=1.0)


def _install_common_mocks(monkeypatch, tmp_path):
    """Mock the GPU/manifest/episode plumbing shared by both run() tests."""

    def fake_generate(self, config, root, specs, *, progress_callback=None, clip_callback=None):
        spec = specs[0]
        clip_batch = _fake_batch(spec.shot_number, root)
        if clip_callback:
            clip_callback(clip_batch.clips[0])
        # Write selected_video + candidates back into the episode so the loop's
        # re-read sees a completed shot.
        episode_path = root / "production" / "episodes" / "episode_001.json"
        episode = json.loads(episode_path.read_text(encoding="utf-8"))
        for shot in episode["shots"]:
            if shot["shot_number"] == spec.shot_number:
                vg = shot.setdefault("video_generation", {})
                vg["selected_video"] = str(
                    clip_batch.clips[0].video_path.relative_to(root).as_posix()
                )
                vg.setdefault("candidates", []).append(
                    {
                        "file": str(clip_batch.clips[0].video_path.relative_to(root).as_posix()),
                        "manifest": str(clip_batch.clips[0].manifest_path.relative_to(root).as_posix()),
                    }
                )
        episode_path.write_text(
            json.dumps(episode, ensure_ascii=False), encoding="utf-8"
        )
        return clip_batch

    monkeypatch.setattr(
        "scripts.generate_episode_h3.GpuServerService.generate_h3_videos", fake_generate
    )
    monkeypatch.setattr(
        "scripts.generate_episode_h3._valid_h3_candidate", lambda root, shot: None
    )
    monkeypatch.setattr(
        "scripts.generate_episode_h3._pending_h3_candidates", lambda root, shot: []
    )
    monkeypatch.setattr(
        "scripts.generate_episode_h3.DesktopProjectService.save_shot_video_result",
        lambda *a, **k: None,
    )

    class FakeResult:
        video_path = tmp_path / "episode.mp4"
        manifest_path = tmp_path / "episode.json"

    monkeypatch.setattr(VideoRenderService, "compose_episode", lambda *a, **k: FakeResult())


def test_run_chains_shot_two_first_frame_from_shot_one(tmp_path, monkeypatch):
    project_root = tmp_path / "projects" / "jueshi"
    _make_episode(project_root, [1, 2])
    workspace = tmp_path
    (workspace / ".env").write_text(
        "GPU_SSH_HOST=h\nGPU_SSH_PORT=22\n", encoding="utf-8"
    )
    (workspace / "ssh.txt").write_text("password=pw", encoding="utf-8")

    generated_specs: list[VideoRenderSpec] = []
    chain_records: list[Path] = []

    real_generate = None

    def tracking_generate(self, config, root, specs, *, progress_callback=None, clip_callback=None):
        generated_specs.extend(specs)
        return real_generate(self, config, root, specs, progress_callback=progress_callback, clip_callback=clip_callback)

    _install_common_mocks(monkeypatch, tmp_path)
    # Re-wrap generate to also record specs.
    real_generate = _fake_generate_impl(tmp_path)
    monkeypatch.setattr(
        "scripts.generate_episode_h3.GpuServerService.generate_h3_videos", tracking_generate
    )

    def fake_extract(self, path, destination, *, frame_count=None):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"png")
        chain_records.append(destination)
        return True

    monkeypatch.setattr(
        "scripts.generate_episode_h3.VideoRenderService.extract_last_frame", fake_extract
    )

    run("jueshi", 1, workspace, max_shots=2, chain_shots=True)

    assert len(generated_specs) == 2
    assert generated_specs[0].chained_from_previous is False
    assert generated_specs[1].chained_from_previous is True
    assert len(chain_records) == 1
    assert chain_records[0].name == "shot_002_chained.png"
    assert generated_specs[1].source_image == chain_records[0]


def test_run_no_chain_shots_keeps_independent_frames(tmp_path, monkeypatch):
    project_root = tmp_path / "projects" / "jueshi"
    _make_episode(project_root, [1, 2])
    workspace = tmp_path
    (workspace / ".env").write_text(
        "GPU_SSH_HOST=h\nGPU_SSH_PORT=22\n", encoding="utf-8"
    )
    (workspace / "ssh.txt").write_text("password=pw", encoding="utf-8")

    generated_specs: list[VideoRenderSpec] = []
    real_generate = None

    def tracking_generate(self, config, root, specs, *, progress_callback=None, clip_callback=None):
        generated_specs.extend(specs)
        return real_generate(self, config, root, specs, progress_callback=progress_callback, clip_callback=clip_callback)

    _install_common_mocks(monkeypatch, tmp_path)
    real_generate = _fake_generate_impl(tmp_path)
    monkeypatch.setattr(
        "scripts.generate_episode_h3.GpuServerService.generate_h3_videos", tracking_generate
    )

    run("jueshi", 1, workspace, max_shots=2, chain_shots=False)

    assert len(generated_specs) == 2
    assert all(spec.chained_from_previous is False for spec in generated_specs)
    # Shot 2 keeps its original storyboard source_image (not rewritten).
    original_shot2_source = (
        project_root
        / "production"
        / "video_inputs"
        / "episode_001"
        / "shot_002.png"
    )
    assert generated_specs[1].source_image == original_shot2_source.resolve()


def _fake_generate_impl(tmp_path: Path):
    """Return the canonical generate_h3_videos fake used by the run() tests."""

    def fake_generate(self, config, root, specs, *, progress_callback=None, clip_callback=None):
        spec = specs[0]
        clip_batch = _fake_batch(spec.shot_number, root)
        if clip_callback:
            clip_callback(clip_batch.clips[0])
        episode_path = root / "production" / "episodes" / "episode_001.json"
        episode = json.loads(episode_path.read_text(encoding="utf-8"))
        for shot in episode["shots"]:
            if shot["shot_number"] == spec.shot_number:
                vg = shot.setdefault("video_generation", {})
                vg["selected_video"] = str(
                    clip_batch.clips[0].video_path.relative_to(root).as_posix()
                )
                vg.setdefault("candidates", []).append(
                    {
                        "file": str(clip_batch.clips[0].video_path.relative_to(root).as_posix()),
                        "manifest": str(clip_batch.clips[0].manifest_path.relative_to(root).as_posix()),
                    }
                )
        episode_path.write_text(
            json.dumps(episode, ensure_ascii=False), encoding="utf-8"
        )
        return clip_batch

    return fake_generate
