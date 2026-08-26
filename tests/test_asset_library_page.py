import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.services.asset_package_service import (
    ResourcePackage,
    ResourcePackageState,
)
from app.ui.asset_library_page import AssetLibraryPage


def test_asset_library_page_displays_four_packages(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    page = AssetLibraryPage()
    state = ResourcePackageState(
        [
            ResourcePackage(
                key=key,
                label=label,
                path=tmp_path / key,
                description=label,
                file_count=index,
                total_bytes=index * 1024,
            )
            for index, (key, label) in enumerate(
                (
                    ("characters", "人物资源包"),
                    ("locations", "场景资源包"),
                    ("voices", "人声资源包"),
                    ("deliverables", "合成内容包"),
                ),
                start=1,
            )
        ]
    )

    page.set_state(state)
    app.processEvents()

    assert len(page._cards) == 4
    assert "10 个资源文件" in page.summary.text()
    page.deleteLater()
