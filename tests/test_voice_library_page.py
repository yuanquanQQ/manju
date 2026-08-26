import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QComboBox

from app.services.voice_library_service import (
    CharacterVoiceTraits,
    VoiceAssignment,
    VoiceProfile,
)
from app.ui.voice_library_page import VoiceLibraryPage


def test_voice_library_page_lists_profiles_and_manual_assignments() -> None:
    app = QApplication.instance() or QApplication([])
    page = VoiceLibraryPage()
    profiles = [
        VoiceProfile(
            profile_id="voice_a",
            name="青年男声 A",
            gender="男声",
            age_group="青年",
        ),
        VoiceProfile(
            profile_id="voice_b",
            name="冷峻男声 B",
            gender="男声",
            age_group="青年",
            temperament="冷峻",
        ),
    ]
    traits = [
        CharacterVoiceTraits(
            character="秦风",
            gender="男声",
            age_group="青年",
            temperament="冷峻",
            role="主角",
            dialogue_count=8,
        )
    ]
    assignments = {
        "秦风": VoiceAssignment(
            character="秦风",
            profile_id="voice_a",
            mode="auto",
            confidence=0.82,
            reasons=["年龄感一致"],
        )
    }

    page.set_state(profiles, traits, assignments)

    assert page.voice_table.rowCount() == 2
    assert page.cast_table.rowCount() == 1
    combo = page.cast_table.cellWidget(0, 3)
    assert isinstance(combo, QComboBox)
    assert combo.currentData() == "voice_a"
    combo.setCurrentIndex(combo.findData("voice_b"))
    app.processEvents()
    assert page.assignment_selections() == {"秦风": "voice_b"}
    assert page.cast_table.item(0, 4).text() == "手动锁定"

    page.deleteLater()
