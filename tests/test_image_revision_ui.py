import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from app.services.desktop_service import EpisodeSnapshot, ImageSnapshot, ShotSnapshot
from app.ui.desktop import ImageRevisionDialog, StoryboardPage


def _make_image(path: Path, width: int = 832, height: int = 480) -> Path:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("#2f6f65"))
    assert image.save(str(path))
    return path


def test_image_revision_dialog_returns_exact_source_size(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    source = _make_image(tmp_path / "source.png")
    dialog = ImageRevisionDialog(
        source,
        "young hero in a teal robe",
        title="镜头 01",
    )
    dialog.issue.setPlainText("去掉胡子，保持服装和背景不变")
    dialog.negative_prompt.setText("beard, moustache")
    dialog.preservation.setCurrentIndex(
        dialog.preservation.findData("strict")
    )
    dialog.candidate_count.setValue(3)

    payload = dialog.payload()

    assert payload["width"] == 832
    assert payload["height"] == 480
    assert payload["preservation"] == "strict"
    assert payload["candidate_count"] == 3
    assert payload["issue"] == "去掉胡子，保持服装和背景不变"
    assert payload["negative_prompt"] == "beard, moustache"
    dialog.deleteLater()
    app.processEvents()


def test_storyboard_enables_revision_and_history_for_existing_image(
    tmp_path: Path,
) -> None:
    app = QApplication.instance() or QApplication([])
    source = _make_image(tmp_path / "source.png")
    candidate = _make_image(tmp_path / "candidate.png")
    page = StoryboardPage()
    page.set_episode(
        EpisodeSnapshot(
            number=1,
            title="第一集",
            path=tmp_path / "episode_001.json",
            characters=[],
            shots=[
                ShotSnapshot(
                    number=1,
                    description="少年站在药园中",
                    prompt="young hero in a herb garden",
                    source_image=source,
                    image_candidates=[
                        ImageSnapshot(
                            path=candidate,
                            model_id="flux_kontext",
                            model_label="FLUX.1 Kontext Dev FP8",
                            generated_at="2026-08-08 18:20:30",
                            layout_label="分镜首帧版本",
                        )
                    ],
                )
            ],
        )
    )
    app.processEvents()

    assert page.revise_image.isEnabled()
    assert page.image_history.isEnabled()
    assert page.image_readiness.text().startswith("1/1 有画面")
    page.deleteLater()
    app.processEvents()
