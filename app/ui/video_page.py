"""Desktop video-generation workbench."""

from __future__ import annotations

import re
import time
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.services.audio_service import VOICE_PRESETS, DubbingRuntimeStatus
from app.services.cosyvoice_service import CosyVoiceStatus
from app.services.desktop_service import EpisodeSnapshot, ShotSnapshot
from app.services.latentsync_service import LatentSyncStatus
from app.services.video_service import MOTION_PRESETS, VideoRuntimeStatus


class VideoGenerationPage(QWidget):
    generate_requested = Signal(int, object, int, int, int)
    end_frames_requested = Signal(int, object, int, int, int)
    source_requested = Signal(int, int, object)
    end_source_requested = Signal(int, int, object)
    save_settings_requested = Signal(int, int, object)
    compose_requested = Signal(int)
    dub_requested = Signal(int, object)
    timeline_plan_requested = Signal(int, object)
    voice_preview_requested = Signal(int, int, object)
    lip_sync_requested = Signal(int, int, object)
    lip_sync_batch_requested = Signal(int, bool)
    open_file_requested = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.episode: EpisodeSnapshot | None = None
        self.episodes: list[EpisodeSnapshot] = []
        self.current_shot: ShotSnapshot | None = None
        self.ai_model_ready = False
        self.ai_adapter_ready = False
        self.ready_ai_engines: set[str] = set()
        self.dubbing_ready = False
        self.cosyvoice_online = False
        self.latentsync_ready = False
        self.started_at = 0.0
        self.elapsed_timer = QTimer(self)
        self.elapsed_timer.timeout.connect(self._refresh_elapsed)

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 26)
        root.setSpacing(12)

        header = QVBoxLayout()
        title = QLabel("视频生成")
        title.setObjectName("pageTitle")
        subtitle = QLabel(
            "规划人物与环境动作、首尾帧和镜头衔接，生成镜头 MP4 与整集预览"
        )
        subtitle.setObjectName("pageSubtitle")
        header.addWidget(title)
        header.addWidget(subtitle)
        root.addLayout(header)

        runtime = QFrame()
        runtime.setObjectName("card")
        runtime_layout = QGridLayout(runtime)
        runtime_layout.setContentsMargins(18, 15, 18, 16)
        runtime_layout.setHorizontalSpacing(12)
        runtime_layout.setVerticalSpacing(9)
        runtime_layout.addWidget(QLabel("生成引擎"), 0, 0)
        self.engine = QComboBox()
        self.engine.addItem("漫画动效 · 本机 FFmpeg（仅静态推拉预览）", "comic_motion")
        self.engine.addItem("Wan2.2 TI2V 5B · AI 人物自然动作", "wan22_ti2v_5b")
        self.engine.addItem(
            "MiniMax H3 FL2VA · 人物动作与原生音效配乐",
            "minimax_h3_fl2va",
        )
        self.engine.addItem("Wan2.2 FLF2V 14B · 首尾帧动作控制", "wan22_flf2v")
        self.engine.addItem("Wan2.2 Animate · 动作参考（待接入）", "wan22_animate")
        self.engine.addItem("Wan2.2 S2V · 配音驱动（待接入）", "wan22_s2v")
        self.engine.setCurrentIndex(2)
        self.engine.setMinimumWidth(290)
        runtime_layout.addWidget(self.engine, 0, 1)
        self.runtime_pill = QLabel("检测中")
        self.runtime_pill.setObjectName("pillWarn")
        runtime_layout.addWidget(self.runtime_pill, 0, 2)
        self.auto_route = QCheckBox("按镜头动作自动选择模型")
        self.auto_route.setChecked(True)
        self.auto_route.setToolTip(
            "开启后统一使用 MiniMax H3 FL2VA；取消后使用左侧手工选择的模型"
        )
        runtime_layout.addWidget(self.auto_route, 0, 3)
        self.runtime_detail = QLabel("正在检测本地视频编码器")
        self.runtime_detail.setObjectName("muted")
        runtime_layout.addWidget(self.runtime_detail, 1, 1, 1, 3)
        self.ai_note = QLabel(
            "AI 图生视频：Wan2.2 动作参数和任务结构已就绪；"
            "尚未检测 GPU 服务器和视频模型。"
        )
        self.ai_note.setWordWrap(True)
        self.ai_note.setStyleSheet(
            "background:#FFF7E8;color:#8A5B12;border:1px solid #F5D79B;"
            "border-radius:8px;padding:8px 10px;"
        )
        runtime_layout.addWidget(self.ai_note, 2, 0, 1, 4)
        self.dubbing_note = QLabel("配音引擎：检测中")
        self.dubbing_note.setWordWrap(True)
        self.dubbing_note.setObjectName("muted")
        runtime_layout.addWidget(self.dubbing_note, 3, 0, 1, 4)
        root.addWidget(runtime)

        episode_row = QHBoxLayout()
        self.episode_label = QLabel("尚未加载分镜")
        self.episode_label.setObjectName("cardTitle")
        self.episode_combo = QComboBox()
        self.episode_combo.setMinimumWidth(220)
        self.episode_combo.currentIndexChanged.connect(self._select_episode)
        self.readiness = QLabel("0/0 有首帧")
        self.readiness.setObjectName("pillOff")
        episode_row.addWidget(self.episode_label)
        episode_row.addStretch()
        episode_row.addWidget(self.readiness)
        episode_row.addWidget(QLabel("剧集"))
        episode_row.addWidget(self.episode_combo)
        root.addLayout(episode_row)

        self.table = QTableWidget(0, 11)
        self.table.setHorizontalHeaderLabels(
            [
                "生成",
                "镜头",
                "画面描述",
                "起始帧",
                "结束帧",
                "引擎",
                "运镜",
                "时长",
                "转场",
                "视频结果",
                "配音",
            ]
        )
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.currentCellChanged.connect(self._show_shot)
        self.table.verticalHeader().setVisible(False)
        table_header = self.table.horizontalHeader()
        table_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        table_header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        table_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for column in (3, 4, 5, 6, 7, 8, 9, 10):
            table_header.setSectionResizeMode(
                column,
                QHeaderView.ResizeMode.ResizeToContents,
            )
        root.addWidget(self.table, 2)

        batch_row = QHBoxLayout()
        select_ready = QPushButton("选择全部可生成镜头")
        select_ready.setObjectName("secondaryButton")
        select_ready.clicked.connect(self._select_all_ready)
        batch_row.addWidget(select_ready)
        batch_row.addStretch()
        batch_row.addWidget(QLabel("输出规格"))
        self.resolution = QComboBox()
        self.resolution.addItem("1280 × 720", (1280, 720))
        self.resolution.addItem("960 × 540 · 漫画预览", (960, 540))
        self.resolution.addItem("960 × 544 · AI 视频", (960, 544))
        self.resolution.addItem("832 × 480 · AI 推荐", (832, 480))
        self.engine.currentIndexChanged.connect(self._engine_changed)
        self.fps = QSpinBox()
        self.fps.setRange(12, 30)
        self.fps.setValue(24)
        self.fps.setSuffix(" fps")
        self.generate = QPushButton("生成选中镜头")
        self.generate.setObjectName("primaryButton")
        self.generate.clicked.connect(self._request_generate)
        self.compose = QPushButton("合成整集预览")
        self.compose.setObjectName("secondaryButton")
        self.compose.clicked.connect(self._request_compose)
        self.dub = QPushButton("生成配音成片")
        self.dub.setObjectName("primaryButton")
        self.dub.clicked.connect(self._request_dub)
        self.plan_timeline = QPushButton("按配音规划时长")
        self.plan_timeline.setObjectName("secondaryButton")
        self.plan_timeline.setToolTip(
            "无需 GPU，先按对白和语速估算时长，标出需要拆分或重生成的镜头"
        )
        self.plan_timeline.clicked.connect(self._request_timeline_plan)
        self.open_dubbed = QPushButton("播放带声成片")
        self.open_dubbed.setObjectName("secondaryButton")
        self.open_dubbed.clicked.connect(self._open_dubbed_video)
        self.open_dubbed.setEnabled(False)
        batch_row.addWidget(self.resolution)
        batch_row.addWidget(self.fps)
        batch_row.addWidget(self.generate)
        batch_row.addWidget(self.compose)
        batch_row.addWidget(self.plan_timeline)
        batch_row.addWidget(self.dub)
        batch_row.addWidget(self.open_dubbed)
        root.addLayout(batch_row)

        editor = QFrame()
        editor.setObjectName("card")
        editor_layout = QGridLayout(editor)
        editor_layout.setContentsMargins(18, 15, 18, 16)
        editor_layout.setHorizontalSpacing(12)
        editor_layout.setVerticalSpacing(9)
        self.preview = QLabel("选择一个镜头")
        self.preview.setObjectName("imageSurface")
        self.preview.setFixedSize(210, 118)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.end_preview = QLabel("可选结束帧")
        self.end_preview.setObjectName("imageSurface")
        self.end_preview.setFixedSize(210, 118)
        self.end_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.editor_title = QLabel("镜头参数")
        self.editor_title.setObjectName("cardTitle")
        editor_layout.addWidget(self.editor_title, 0, 0, 1, 2)

        preview_panel = QWidget()
        preview_layout = QGridLayout(preview_panel)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setHorizontalSpacing(8)
        preview_layout.setVerticalSpacing(6)
        preview_layout.addWidget(QLabel("起始帧"), 0, 0)
        preview_layout.addWidget(QLabel("结束帧（可选）"), 0, 1)
        preview_layout.addWidget(self.preview, 1, 0)
        preview_layout.addWidget(self.end_preview, 1, 1)
        self.choose_source = QPushButton("选择/更换起始帧")
        self.choose_source.setObjectName("secondaryButton")
        self.choose_source.clicked.connect(self._choose_source)
        self.choose_end_source = QPushButton("选择/更换结束帧")
        self.choose_end_source.setObjectName("secondaryButton")
        self.choose_end_source.clicked.connect(self._choose_end_source)
        preview_layout.addWidget(self.choose_source, 2, 0)
        preview_layout.addWidget(self.choose_end_source, 2, 1)
        editor_layout.addWidget(preview_panel, 1, 0)

        tabs = QTabWidget()
        motion_tab = QWidget()
        motion_layout = QGridLayout(motion_tab)
        motion_layout.setContentsMargins(10, 8, 10, 8)
        motion_layout.setHorizontalSpacing(10)
        motion_layout.setVerticalSpacing(7)
        motion_layout.addWidget(QLabel("人物动作"), 0, 0)
        self.subject_motion = QTextEdit()
        self.subject_motion.setMaximumHeight(62)
        self.subject_motion.setPlaceholderText(
            "一个镜头只描述一个主要动作，例如：缓慢蹲下，右手拨开泥土"
        )
        motion_layout.addWidget(self.subject_motion, 0, 1, 1, 5)
        motion_layout.addWidget(QLabel("环境运动"), 1, 0)
        self.environment_motion = QTextEdit()
        self.environment_motion.setMaximumHeight(54)
        self.environment_motion.setPlaceholderText(
            "例如：晨雾从左向右飘动，发梢和衣袖轻微摆动"
        )
        motion_layout.addWidget(self.environment_motion, 1, 1, 1, 5)
        motion_layout.addWidget(QLabel("镜头运动"), 2, 0)
        self.motion = QComboBox()
        for preset_id, preset in MOTION_PRESETS.items():
            self.motion.addItem(preset.label, preset_id)
        self.motion_strength = QComboBox()
        self.motion_strength.addItem("轻微", "low")
        self.motion_strength.addItem("中等", "medium")
        self.motion_strength.addItem("强烈", "high")
        self.duration = QDoubleSpinBox()
        self.duration.setRange(1.0, 15.0)
        self.duration.setSingleStep(0.5)
        self.duration.setDecimals(1)
        self.duration.setSuffix(" 秒")
        self.candidate_count = QSpinBox()
        self.candidate_count.setRange(1, 4)
        self.candidate_count.setSuffix(" 个候选")
        motion_layout.addWidget(self.motion, 2, 1)
        motion_layout.addWidget(QLabel("动作幅度"), 2, 2)
        motion_layout.addWidget(self.motion_strength, 2, 3)
        motion_layout.addWidget(self.duration, 2, 4)
        motion_layout.addWidget(self.candidate_count, 2, 5)
        tabs.addTab(motion_tab, "动作与镜头")

        continuity_tab = QWidget()
        continuity_layout = QGridLayout(continuity_tab)
        continuity_layout.setContentsMargins(10, 8, 10, 8)
        continuity_layout.setHorizontalSpacing(10)
        continuity_layout.setVerticalSpacing(7)
        continuity_layout.addWidget(QLabel("连续性限制"), 0, 0)
        self.continuity_constraints = QTextEdit()
        self.continuity_constraints.setMaximumHeight(62)
        self.continuity_constraints.setPlaceholderText(
            "保持脸型、发型、服装、道具和场景布局一致；不增加人物和肢体"
        )
        continuity_layout.addWidget(self.continuity_constraints, 0, 1, 1, 5)
        continuity_layout.addWidget(QLabel("负面提示词"), 1, 0)
        self.negative_prompt = QTextEdit()
        self.negative_prompt.setMaximumHeight(54)
        self.negative_prompt.setPlaceholderText(
            "face morphing, extra limbs, flicker, camera shake"
        )
        continuity_layout.addWidget(self.negative_prompt, 1, 1, 1, 5)
        continuity_layout.addWidget(QLabel("结束帧提示词"), 2, 0)
        self.end_frame_prompt = QTextEdit()
        self.end_frame_prompt.setMaximumHeight(62)
        self.end_frame_prompt.setPlaceholderText(
            "描述同一镜头中动作完成后的姿态；系统会自动保持人物、服装、镜头和场景一致"
        )
        continuity_layout.addWidget(self.end_frame_prompt, 2, 1, 1, 5)
        self.screen_direction = QComboBox()
        self.screen_direction.addItem("自动", "auto")
        self.screen_direction.addItem("从左向右", "left_to_right")
        self.screen_direction.addItem("从右向左", "right_to_left")
        self.screen_direction.addItem("原地/静止", "static")
        self.transition = QComboBox()
        self.transition.addItem("直接切换", "cut")
        self.transition.addItem("动作匹配切换", "match_cut")
        self.transition.addItem("交叉溶解", "dissolve")
        self.transition.addItem("淡入黑场", "fade_black")
        self.transition_frames = QSpinBox()
        self.transition_frames.setRange(0, 48)
        self.transition_frames.setValue(8)
        self.transition_frames.setSuffix(" 帧")
        self.handle_frames = QSpinBox()
        self.handle_frames.setRange(0, 48)
        self.handle_frames.setValue(8)
        self.handle_frames.setSuffix(" 帧余量")
        continuity_layout.addWidget(QLabel("运动方向"), 3, 0)
        continuity_layout.addWidget(self.screen_direction, 3, 1)
        continuity_layout.addWidget(QLabel("镜头切换"), 3, 2)
        continuity_layout.addWidget(self.transition, 3, 3)
        continuity_layout.addWidget(self.transition_frames, 3, 4)
        continuity_layout.addWidget(self.handle_frames, 3, 5)
        self.routing_note = QLabel("自动路由尚未分析")
        self.routing_note.setObjectName("muted")
        self.routing_note.setWordWrap(True)
        continuity_layout.addWidget(self.routing_note, 4, 0, 1, 6)
        tabs.addTab(continuity_tab, "连续性与转场")

        h3_audio_tab = QWidget()
        h3_audio_layout = QGridLayout(h3_audio_tab)
        h3_audio_layout.setContentsMargins(10, 8, 10, 8)
        h3_audio_layout.setHorizontalSpacing(10)
        h3_audio_layout.setVerticalSpacing(7)
        h3_audio_layout.addWidget(QLabel("原生声音策略"), 0, 0)
        self.native_audio_mode = QComboBox()
        self.native_audio_mode.addItem(
            "环境声、音效、配乐 + 后期精确配音（推荐）",
            "ambience_sfx_music",
        )
        self.native_audio_mode.addItem(
            "H3 直接生成完整声音（包含对白）",
            "native_full",
        )
        self.native_audio_mode.addItem("不保留 H3 原生声音", "off")
        h3_audio_layout.addWidget(self.native_audio_mode, 0, 1, 1, 5)
        h3_audio_layout.addWidget(QLabel("环境与动作音效"), 1, 0)
        self.sound_effect_prompt = QTextEdit()
        self.sound_effect_prompt.setMaximumHeight(62)
        self.sound_effect_prompt.setPlaceholderText(
            "例如：药圃微风、衣袖摩擦、脚踩泥土，全部与画面动作同步"
        )
        h3_audio_layout.addWidget(self.sound_effect_prompt, 1, 1, 1, 5)
        h3_audio_layout.addWidget(QLabel("背景配乐"), 2, 0)
        self.music_prompt = QTextEdit()
        self.music_prompt.setMaximumHeight(62)
        self.music_prompt.setPlaceholderText(
            "例如：克制的仙侠电影配乐，古琴与低弦，无歌词，为对白留空间"
        )
        h3_audio_layout.addWidget(self.music_prompt, 2, 1, 1, 5)
        h3_note = QLabel(
            "推荐让 H3 只生成环境声、动作音效和配乐；角色台词继续使用 "
            "CosyVoice，并在成片时自动压低背景声。"
        )
        h3_note.setWordWrap(True)
        h3_note.setObjectName("muted")
        h3_audio_layout.addWidget(h3_note, 3, 0, 1, 6)
        tabs.addTab(h3_audio_tab, "H3 声音与配乐")

        dubbing_tab = QWidget()
        dubbing_layout = QGridLayout(dubbing_tab)
        dubbing_layout.setContentsMargins(10, 8, 10, 8)
        dubbing_layout.setHorizontalSpacing(10)
        dubbing_layout.setVerticalSpacing(7)
        dubbing_layout.addWidget(QLabel("配音模式"), 0, 0)
        self.audio_mode = QComboBox()
        self.audio_mode.addItem("自动旁白（对白为空时读画面描述）", "auto_narration")
        self.audio_mode.addItem("角色对白/自定义文案", "dialogue")
        self.audio_mode.addItem("本镜头静音", "mute")
        dubbing_layout.addWidget(self.audio_mode, 0, 1)
        dubbing_layout.addWidget(QLabel("说话人"), 0, 2)
        self.speaker = QLineEdit("旁白")
        self.speaker.setPlaceholderText("旁白 / 秦风 / 林浪")
        dubbing_layout.addWidget(self.speaker, 0, 3)
        dubbing_layout.addWidget(QLabel("引擎"), 0, 4)
        self.tts_engine = QComboBox()
        self.tts_engine.addItem("Edge TTS·在线免密钥", "edge_tts")
        self.tts_engine.addItem("CosyVoice 3·3090 本地音色克隆", "cosyvoice")
        self.tts_engine.currentIndexChanged.connect(self._tts_engine_changed)
        dubbing_layout.addWidget(self.tts_engine, 0, 5)

        dubbing_layout.addWidget(QLabel("配音文案"), 1, 0)
        self.dialogue = QTextEdit()
        self.dialogue.setMaximumHeight(72)
        self.dialogue.setPlaceholderText(
            "留空时自动使用精简旁白；也可输入角色台词。"
        )
        dubbing_layout.addWidget(self.dialogue, 1, 1, 1, 5)

        dubbing_layout.addWidget(QLabel("音色"), 2, 0)
        self.voice = QComboBox()
        for voice_id, preset in VOICE_PRESETS.items():
            self.voice.addItem(preset.label, voice_id)
        dubbing_layout.addWidget(self.voice, 2, 1, 1, 2)
        dubbing_layout.addWidget(QLabel("语速"), 2, 3)
        self.speech_rate = QSpinBox()
        self.speech_rate.setRange(-30, 50)
        self.speech_rate.setValue(5)
        self.speech_rate.setSuffix("%")
        dubbing_layout.addWidget(self.speech_rate, 2, 4)
        self.subtitle_enabled = QCheckBox("烧录中文字幕")
        self.subtitle_enabled.setChecked(True)
        dubbing_layout.addWidget(self.subtitle_enabled, 2, 5)

        dubbing_layout.addWidget(QLabel("参考音频"), 3, 0)
        self.voice_reference = QLineEdit()
        self.voice_reference.setReadOnly(True)
        self.voice_reference.setPlaceholderText(
            "CosyVoice 使用 3–15 秒清晰人声；留空时自动创建基础音色"
        )
        dubbing_layout.addWidget(self.voice_reference, 3, 1, 1, 3)
        self.choose_voice_reference = QPushButton("选择音频")
        self.choose_voice_reference.setObjectName("secondaryButton")
        self.choose_voice_reference.clicked.connect(self._choose_voice_reference)
        dubbing_layout.addWidget(self.choose_voice_reference, 3, 4)
        self.preview_voice = QPushButton("试听当前文案")
        self.preview_voice.setObjectName("secondaryButton")
        self.preview_voice.clicked.connect(self._request_voice_preview)
        dubbing_layout.addWidget(self.preview_voice, 3, 5)

        dubbing_layout.addWidget(QLabel("参考台词"), 4, 0)
        self.voice_reference_text = QLineEdit()
        self.voice_reference_text.setPlaceholderText(
            "必须与参考音频逐字一致；自动基础音色会自动填写"
        )
        dubbing_layout.addWidget(self.voice_reference_text, 4, 1, 1, 5)
        dubbing_layout.addWidget(QLabel("表演指令"), 5, 0)
        self.voice_instruct = QLineEdit()
        self.voice_instruct.setPlaceholderText(
            "例如：年轻男声，自然克制，略带紧张，语气坚定。"
        )
        dubbing_layout.addWidget(self.voice_instruct, 5, 1, 1, 4)
        self.fallback_to_edge = QCheckBox("失败时回退 Edge")
        self.fallback_to_edge.setChecked(True)
        dubbing_layout.addWidget(self.fallback_to_edge, 5, 5)

        self.lip_sync_enabled = QCheckBox("对白镜头启用人物口型")
        self.lip_sync_enabled.setToolTip(
            "当前先保存任务参数；检测到 LatentSync 后才会执行"
        )
        dubbing_layout.addWidget(self.lip_sync_enabled, 6, 0, 1, 2)
        dubbing_layout.addWidget(QLabel("目标人物"), 6, 2)
        self.lip_sync_target = QLineEdit()
        self.lip_sync_target.setPlaceholderText("默认使用说话人")
        dubbing_layout.addWidget(self.lip_sync_target, 6, 3)
        self.lip_sync_engine = QComboBox()
        self.lip_sync_engine.addItem("LatentSync 1.6 · 高质量", "latentsync_1_6")
        self.lip_sync_engine.addItem("LatentSync 1.5 · 8GB备用", "latentsync_1_5")
        dubbing_layout.addWidget(self.lip_sync_engine, 6, 4, 1, 2)

        dubbing_layout.addWidget(QLabel("目标脸选择"), 7, 0)
        self.lip_sync_mode = QComboBox()
        self.lip_sync_mode.addItem("按说话人自动跟踪", "speaker_tracking")
        self.lip_sync_mode.addItem("单人镜头自动选择", "auto_single_face")
        self.lip_sync_mode.addItem("手动框选目标脸", "manual_anchor")
        dubbing_layout.addWidget(self.lip_sync_mode, 7, 1, 1, 2)
        self.lip_sync_status = QLabel("口型：未启用")
        self.lip_sync_status.setObjectName("muted")
        dubbing_layout.addWidget(self.lip_sync_status, 7, 3, 1, 3)
        self.latentsync_runtime = QLabel("LatentSync：尚未检测")
        self.latentsync_runtime.setObjectName("muted")
        self.run_lip_sync = QPushButton("生成当前镜头口型")
        self.run_lip_sync.setObjectName("primaryButton")
        self.run_lip_sync.clicked.connect(self._request_lip_sync)
        dubbing_layout.addWidget(self.latentsync_runtime, 8, 0, 1, 4)
        dubbing_layout.addWidget(self.run_lip_sync, 8, 4, 1, 2)
        self.lip_sync_batch_status = QLabel("整集口型：等待检查")
        self.lip_sync_batch_status.setObjectName("muted")
        self.run_lip_sync_batch = QPushButton("批量生成整集口型")
        self.run_lip_sync_batch.setObjectName("secondaryButton")
        self.run_lip_sync_batch.clicked.connect(self._request_lip_sync_batch)
        dubbing_layout.addWidget(self.lip_sync_batch_status, 9, 0, 1, 4)
        dubbing_layout.addWidget(self.run_lip_sync_batch, 9, 4, 1, 2)
        tabs.addTab(dubbing_tab, "配音与字幕")
        editor_layout.addWidget(tabs, 1, 1)

        actions = QHBoxLayout()
        self.save_settings = QPushButton("保存视频参数")
        self.save_settings.setObjectName("primaryButton")
        self.save_settings.clicked.connect(self._save_settings)
        self.open_video = QPushButton("播放当前视频")
        self.open_video.setObjectName("secondaryButton")
        self.open_video.clicked.connect(self._open_current_video)
        actions.addWidget(self.open_video)
        actions.addStretch()
        actions.addWidget(self.save_settings)
        editor_layout.addLayout(actions, 2, 0, 1, 2)
        root.addWidget(editor, 1)

        progress_row = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.stage = QLabel("等待生成")
        self.stage.setObjectName("muted")
        self.elapsed = QLabel("00:00")
        self.elapsed.setObjectName("pillOff")
        progress_row.addWidget(self.progress, 1)
        progress_row.addWidget(self.stage)
        progress_row.addWidget(self.elapsed)
        root.addLayout(progress_row)

    def set_runtime_status(self, status: VideoRuntimeStatus) -> None:
        self.runtime_pill.setText("可用" if status.available else "不可用")
        self.runtime_pill.setObjectName("pillGood" if status.available else "pillOff")
        self.runtime_pill.style().unpolish(self.runtime_pill)
        self.runtime_pill.style().polish(self.runtime_pill)
        self.runtime_detail.setText(
            f"{status.message} · {status.ffmpeg_version}"
            if status.ffmpeg_version
            else status.message
        )
        self.generate.setEnabled(status.available)

    def set_dubbing_status(self, status: DubbingRuntimeStatus) -> None:
        self.dubbing_ready = status.available
        state = "可用" if status.available else "不可用"
        self.dubbing_note.setText(f"配音引擎：{state} · {status.message}")
        self.dub.setEnabled(status.available and bool(self.episode))

    def set_cosyvoice_status(self, status: CosyVoiceStatus) -> None:
        self.cosyvoice_online = status.online
        local_state = (
            f"CosyVoice：{status.message}"
            if status.ssh_online
            else "CosyVoice：GPU 服务器未连接"
        )
        edge_state = "Edge TTS 可用" if self.dubbing_ready else "Edge TTS 不可用"
        self.dubbing_note.setText(f"配音引擎：{edge_state} · {local_state}")
        self._tts_engine_changed(self.tts_engine.currentIndex())

    def set_latentsync_status(self, status: LatentSyncStatus) -> None:
        self.latentsync_ready = status.callable
        self.latentsync_runtime.setText(f"LatentSync：{status.message}")
        self.latentsync_runtime.setObjectName(
            "pillGood" if status.callable else "pillWarn"
            if status.installing or status.installed else "muted"
        )
        self.latentsync_runtime.style().unpolish(self.latentsync_runtime)
        self.latentsync_runtime.style().polish(self.latentsync_runtime)
        self.run_lip_sync.setToolTip(
            "使用 3090 上的 LatentSync 1.6 生成口型"
            if status.callable
            else status.message
        )
        self.run_lip_sync_batch.setToolTip(
            "自动跳过旁白和已完成镜头，失败后可从未完成处续跑"
            if status.callable
            else status.message
        )

    def set_ai_model_status(
        self,
        *,
        server_online: bool,
        model_ready: bool,
        adapter_ready: bool = False,
        model_name: str = "",
        h3_model_ready: bool = False,
        h3_adapter_ready: bool = False,
        h3_model_name: str = "",
        flf_model_ready: bool = False,
        flf_adapter_ready: bool = False,
        flf_model_name: str = "",
    ) -> None:
        self.ai_model_ready = server_online and model_ready
        self.ai_adapter_ready = self.ai_model_ready and adapter_ready
        self.ready_ai_engines = set()
        if self.ai_adapter_ready:
            self.ready_ai_engines.add("wan22_ti2v_5b")
        if server_online and h3_model_ready and h3_adapter_ready:
            self.ready_ai_engines.add("minimax_h3_fl2va")
        if server_online and flf_model_ready and flf_adapter_ready:
            self.ready_ai_engines.add("wan22_flf2v")
        if not server_online:
            text = (
                "AI 图生视频：GPU 服务器未连接。需要服务器启动后检测 "
                "Wan2.2 与 MiniMax H3。"
            )
        elif self.ready_ai_engines:
            labels = []
            if "wan22_ti2v_5b" in self.ready_ai_engines:
                labels.append(model_name or "Wan2.2 TI2V 5B")
            if "minimax_h3_fl2va" in self.ready_ai_engines:
                labels.append(h3_model_name or "MiniMax H3 FL2VA")
            if "wan22_flf2v" in self.ready_ai_engines:
                labels.append(flf_model_name or "Wan2.2 FLF2V 14B")
            text = (
                f"AI 图生视频：{'、'.join(labels)} 已就绪；"
                "H3 可生成原生环境声、音效和配乐。"
            )
        elif model_ready or h3_model_ready or flf_model_ready:
            text = (
                "AI 视频模型文件已检测到，但 ComfyUI 缺少对应原生节点；"
                "请更新 ComfyUI 后重新检测。"
            )
        else:
            text = (
                "AI 图生视频：服务器在线，但尚未检测到完整的 Wan2.2 或 "
                "MiniMax H3 或 FLF2V 模型组合。"
            )
        self.ai_note.setText(text)

    def set_episodes(self, episodes: list[EpisodeSnapshot]) -> None:
        current_number = self.episode.number if self.episode else 0
        self.episodes = episodes
        self.episode_combo.blockSignals(True)
        self.episode_combo.clear()
        for episode in episodes:
            self.episode_combo.addItem(
                f"第 {episode.number} 集 · {episode.title}",
                episode.number,
            )
        index = self.episode_combo.findData(current_number)
        self.episode_combo.setCurrentIndex(max(0, index))
        self.episode_combo.blockSignals(False)
        self.set_episode(episodes[max(0, index)] if episodes else None)

    def set_episode(self, episode: EpisodeSnapshot | None) -> None:
        self.episode = episode
        self.current_shot = None
        self.table.setRowCount(0)
        if not episode:
            self.episode_label.setText("尚未加载分镜")
            self.readiness.setText("0/0 有首帧")
            self.dub.setEnabled(False)
            self.open_dubbed.setEnabled(False)
            self.plan_timeline.setEnabled(False)
            self._clear_editor()
            return
        total_duration = sum(
            shot.duration_seconds for shot in episode.shots
        )
        duration_warning = "（不足 60 秒）" if total_duration < 60 else ""
        self.episode_label.setText(
            f"第 {episode.number} 集 · {episode.title} · "
            f"{len(episode.shots)} 个镜头 · 预计 {total_duration:.0f} 秒"
            f"{duration_warning}"
        )
        source_count = sum(bool(shot.source_image) for shot in episode.shots)
        approved_count = sum(
            shot.image_qc_status == "approved" for shot in episode.shots
        )
        video_count = sum(bool(shot.video_path) for shot in episode.shots)
        lip_sync_shots = [
            shot
            for shot in episode.shots
            if shot.lip_sync_enabled
            and shot.audio_mode == "dialogue"
            and shot.speaker != "旁白"
        ]
        lip_sync_completed = sum(
            shot.lip_sync_status == "succeeded" for shot in lip_sync_shots
        )
        self.lip_sync_batch_status.setText(
            f"整集口型：{lip_sync_completed}/{len(lip_sync_shots)} 已完成 · "
            f"点击后检查缺少视频、定妆照和画面角色"
        )
        self.readiness.setText(
            f"{source_count}/{len(episode.shots)} 有首帧 · "
            f"{approved_count}/{len(episode.shots)} 质检通过 · "
            f"{video_count}/{len(episode.shots)} 有视频"
        )
        self.readiness.setObjectName(
            "pillGood" if approved_count == len(episode.shots) else "pillWarn"
        )
        self.readiness.style().unpolish(self.readiness)
        self.readiness.style().polish(self.readiness)
        self.table.setRowCount(len(episode.shots))
        for row, shot in enumerate(episode.shots):
            checkbox = QTableWidgetItem()
            checkbox.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsUserCheckable
            )
            checkbox.setCheckState(
                Qt.CheckState.Checked
                if (
                    shot.source_image
                    and shot.image_qc_status == "approved"
                    and not shot.video_path
                )
                else Qt.CheckState.Unchecked
            )
            if not shot.source_image or shot.image_qc_status != "approved":
                checkbox.setFlags(Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(row, 0, checkbox)
            self.table.setItem(row, 1, QTableWidgetItem(f"{shot.number:02d}"))
            self.table.setItem(row, 2, QTableWidgetItem(shot.description))
            self.table.setItem(
                row,
                3,
                QTableWidgetItem(
                    (
                        f"{shot.source_image.name} · "
                        + {
                            "approved": "已通过",
                            "rejected": "已驳回",
                            "pending": "待审核",
                        }.get(shot.image_qc_status, "待审核")
                    )
                    if shot.source_image
                    else "未指定"
                ),
            )
            self.table.setItem(
                row,
                4,
                QTableWidgetItem(shot.end_image.name if shot.end_image else "可选"),
            )
            engine_index = self.engine.findData(shot.engine_profile)
            engine_label = (
                self.engine.itemText(engine_index).split(" · ")[0]
                if engine_index >= 0
                else shot.engine_profile
            )
            self.table.setItem(row, 5, QTableWidgetItem(engine_label))
            motion = MOTION_PRESETS.get(shot.camera_movement)
            self.table.setItem(
                row,
                6,
                QTableWidgetItem(motion.label if motion else shot.camera_movement),
            )
            self.table.setItem(
                row,
                7,
                QTableWidgetItem(f"{shot.duration_seconds:.1f}s"),
            )
            transition_index = self.transition.findData(shot.transition_out)
            transition_label = (
                self.transition.itemText(transition_index)
                if transition_index >= 0
                else shot.transition_out
            )
            self.table.setItem(
                row,
                8,
                QTableWidgetItem(transition_label),
            )
            self.table.setItem(
                row,
                9,
                QTableWidgetItem(shot.video_path.name if shot.video_path else "待生成"),
            )
            self.table.setItem(
                row,
                10,
                QTableWidgetItem(
                    
                        f"{shot.audio_path.name} · "
                        f"{shot.planned_timeline_duration_seconds:.1f}s"
                        if shot.audio_path
                        and shot.planned_timeline_duration_seconds > 0
                        else (
                            f"建议拆 {shot.recommended_segments} 段"
                            if shot.timing_status == "needs_split"
                            else (
                                f"需重生成 · "
                                f"{shot.planned_timeline_duration_seconds:.1f}s"
                                if shot.timing_status == "needs_regeneration"
                                else "已规划"
                                if shot.timing_status == "ready"
                                else "待配音"
                            )
                        )
                    
                ),
            )
            self.table.setRowHeight(row, 54)
        self.compose.setEnabled(video_count > 0)
        self.dub.setEnabled(video_count > 0 and self.dubbing_ready)
        self.plan_timeline.setEnabled(bool(episode.shots))
        self.open_dubbed.setEnabled(bool(episode.dubbed_video_path))
        if episode.shots:
            self.table.setCurrentCell(0, 1)

    def set_generating(self, active: bool, message: str = "") -> None:
        self.generate.setDisabled(active)
        self.compose.setDisabled(active)
        self.dub.setDisabled(active)
        self.open_dubbed.setDisabled(active)
        self.plan_timeline.setDisabled(active)
        self.run_lip_sync.setDisabled(active)
        self.run_lip_sync_batch.setDisabled(active)
        self.table.setDisabled(active)
        self.choose_source.setDisabled(active)
        self.choose_end_source.setDisabled(active)
        self.save_settings.setDisabled(active)
        if active:
            self.started_at = time.monotonic()
            self.progress.setValue(0)
            self.stage.setText(message or "正在准备视频任务")
            self.elapsed_timer.start(1000)
        else:
            self.elapsed_timer.stop()

    def set_progress(self, percent: int, message: str) -> None:
        self.progress.setValue(percent)
        self.stage.setText(message)
        self._refresh_elapsed()

    def finish_generation(self, message: str) -> None:
        self.set_generating(False)
        self.progress.setValue(100)
        self.stage.setText(message)
        self._refresh_elapsed()

    def fail_generation(self, message: str) -> None:
        self.set_generating(False)
        self.stage.setText(message)

    def _select_episode(self, index: int) -> None:
        if 0 <= index < len(self.episodes):
            self.set_episode(self.episodes[index])

    def _show_shot(
        self,
        row: int,
        _column: int,
        _previous_row: int,
        _previous_column: int,
    ) -> None:
        if not self.episode or row < 0 or row >= len(self.episode.shots):
            return
        shot = self.episode.shots[row]
        self.current_shot = shot
        self.editor_title.setText(f"镜头 {shot.number:02d} 视频参数")
        self.subject_motion.setPlainText(
            shot.subject_motion or shot.motion_prompt or shot.description
        )
        self.environment_motion.setPlainText(shot.environment_motion)
        self.continuity_constraints.setPlainText(shot.continuity_constraints)
        self.negative_prompt.setPlainText(shot.negative_prompt)
        self.end_frame_prompt.setPlainText(shot.end_frame_prompt)
        self.routing_note.setText(
            f"自动路由：{shot.routing_reason}"
            if shot.routing_reason
            else "自动路由尚未分析"
        )
        index = self.native_audio_mode.findData(shot.native_audio_mode)
        self.native_audio_mode.setCurrentIndex(max(0, index))
        self.sound_effect_prompt.setPlainText(shot.sound_effect_prompt)
        self.music_prompt.setPlainText(shot.music_prompt)
        index = self.engine.findData(shot.engine_profile)
        self.engine.setCurrentIndex(max(0, index))
        index = self.motion.findData(shot.camera_movement)
        self.motion.setCurrentIndex(max(0, index))
        index = self.motion_strength.findData(shot.motion_strength)
        self.motion_strength.setCurrentIndex(max(0, index))
        index = self.screen_direction.findData(shot.screen_direction)
        self.screen_direction.setCurrentIndex(max(0, index))
        index = self.transition.findData(shot.transition_out)
        self.transition.setCurrentIndex(max(0, index))
        self.transition_frames.setValue(shot.transition_frames)
        self.handle_frames.setValue(shot.handle_frames)
        self.candidate_count.setValue(shot.candidate_count)
        self.duration.setValue(shot.duration_seconds)
        index = self.audio_mode.findData(shot.audio_mode)
        self.audio_mode.setCurrentIndex(max(0, index))
        self.speaker.setText(shot.speaker)
        index = self.tts_engine.findData(shot.tts_engine)
        self.tts_engine.setCurrentIndex(max(0, index))
        index = self.voice.findData(shot.voice_id)
        self.voice.setCurrentIndex(max(0, index))
        rate_match = re.fullmatch(r"([+-]?\d+)%", shot.speech_rate)
        self.speech_rate.setValue(
            int(rate_match.group(1)) if rate_match else 5
        )
        self.dialogue.setPlainText(shot.dialogue)
        self.voice_reference.setText(
            str(shot.voice_reference_path) if shot.voice_reference_path else ""
        )
        self.voice_reference_text.setText(shot.voice_reference_text)
        self.voice_instruct.setText(shot.voice_instruct_text)
        self.fallback_to_edge.setChecked(shot.fallback_to_edge)
        self.subtitle_enabled.setChecked(shot.subtitle_enabled)
        self.lip_sync_enabled.setChecked(shot.lip_sync_enabled)
        self.lip_sync_target.setText(
            shot.lip_sync_target_character or shot.speaker
        )
        index = self.lip_sync_engine.findData(shot.lip_sync_engine)
        self.lip_sync_engine.setCurrentIndex(max(0, index))
        index = self.lip_sync_mode.findData(shot.lip_sync_mode)
        self.lip_sync_mode.setCurrentIndex(max(0, index))
        status_labels = {
            "disabled": "未启用",
            "pending": "等待模型",
            "ready": "可处理",
            "processing": "处理中",
            "succeeded": (
                f"已完成 · 身份匹配 {shot.lip_sync_score:.2f}"
                if shot.lip_sync_score > 0
                else "已完成"
            ),
            "failed": "处理失败",
            "needs_face_selection": "需要框选目标脸",
        }
        self.lip_sync_status.setText(
            f"口型：{status_labels.get(shot.lip_sync_status, shot.lip_sync_status)}"
        )
        self.run_lip_sync.setEnabled(True)
        self._tts_engine_changed(self.tts_engine.currentIndex())
        self.open_video.setEnabled(bool(shot.video_path))
        self._show_preview(self.preview, shot.source_image, "尚未指定起始帧")
        self._show_preview(self.end_preview, shot.end_image, "可选结束帧")

    @staticmethod
    def _show_preview(
        target: QLabel,
        path: Path | None,
        empty_text: str,
    ) -> None:
        if not path or not path.is_file():
            target.setPixmap(QPixmap())
            target.setText(empty_text)
            return
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            target.setPixmap(QPixmap())
            target.setText(path.name)
            return
        target.setText("")
        target.setPixmap(
            pixmap.scaled(
                QSize(200, 108),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _choose_source(self) -> None:
        if not self.episode or not self.current_shot:
            return
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "选择镜头首帧",
            "",
            "图片 (*.png *.jpg *.jpeg *.webp);;所有文件 (*)",
        )
        if path:
            self.source_requested.emit(
                self.episode.number,
                self.current_shot.number,
                Path(path),
            )

    def _choose_end_source(self) -> None:
        if not self.episode or not self.current_shot:
            return
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "选择镜头结束帧",
            "",
            "图片 (*.png *.jpg *.jpeg *.webp);;所有文件 (*)",
        )
        if path:
            self.end_source_requested.emit(
                self.episode.number,
                self.current_shot.number,
                Path(path),
            )

    def _choose_voice_reference(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "选择角色参考音频",
            "",
            "音频 (*.wav *.mp3 *.flac *.m4a *.ogg);;所有文件 (*)",
        )
        if path:
            self.voice_reference.setText(path)

    def _save_settings(self) -> None:
        if not self.episode or not self.current_shot:
            return
        self._commit_current_editor()
        payload = self._shot_settings(self.current_shot)
        payload["audio_generation"] = self._shot_audio_settings(
            self.current_shot
        )
        payload["lip_sync"] = self._shot_lip_sync_settings(self.current_shot)
        self.save_settings_requested.emit(
            self.episode.number,
            self.current_shot.number,
            payload,
        )

    def _commit_current_editor(self) -> None:
        if not self.current_shot:
            return
        if not self.auto_route.isChecked():
            self.current_shot.engine_profile = str(self.engine.currentData())
        self.current_shot.subject_motion = self.subject_motion.toPlainText().strip()
        self.current_shot.environment_motion = (
            self.environment_motion.toPlainText().strip()
        )
        self.current_shot.continuity_constraints = (
            self.continuity_constraints.toPlainText().strip()
        )
        self.current_shot.negative_prompt = self.negative_prompt.toPlainText().strip()
        self.current_shot.end_frame_prompt = (
            self.end_frame_prompt.toPlainText().strip()
        )
        self.current_shot.native_audio_mode = str(
            self.native_audio_mode.currentData()
        )
        self.current_shot.dialogue_prompt = self.dialogue.toPlainText().strip()
        self.current_shot.sound_effect_prompt = (
            self.sound_effect_prompt.toPlainText().strip()
        )
        self.current_shot.music_prompt = self.music_prompt.toPlainText().strip()
        self.current_shot.motion_prompt = self.current_shot.subject_motion
        self.current_shot.camera_movement = str(self.motion.currentData())
        self.current_shot.motion_strength = str(self.motion_strength.currentData())
        self.current_shot.screen_direction = str(self.screen_direction.currentData())
        self.current_shot.transition_out = str(self.transition.currentData())
        self.current_shot.transition_frames = self.transition_frames.value()
        self.current_shot.handle_frames = self.handle_frames.value()
        self.current_shot.candidate_count = self.candidate_count.value()
        self.current_shot.duration_seconds = self.duration.value()
        self.current_shot.audio_mode = str(self.audio_mode.currentData())
        self.current_shot.speaker = self.speaker.text().strip() or "旁白"
        self.current_shot.tts_engine = str(self.tts_engine.currentData())
        self.current_shot.voice_id = str(self.voice.currentData())
        self.current_shot.voice_reference_path = (
            Path(self.voice_reference.text().strip())
            if self.voice_reference.text().strip()
            else None
        )
        self.current_shot.voice_reference_text = (
            self.voice_reference_text.text().strip()
        )
        self.current_shot.voice_instruct_text = self.voice_instruct.text().strip()
        self.current_shot.fallback_to_edge = self.fallback_to_edge.isChecked()
        self.current_shot.speech_rate = f"{self.speech_rate.value():+d}%"
        self.current_shot.dialogue = self.dialogue.toPlainText().strip()
        self.current_shot.subtitle_enabled = self.subtitle_enabled.isChecked()
        self.current_shot.lip_sync_enabled = self.lip_sync_enabled.isChecked()
        self.current_shot.lip_sync_engine = str(
            self.lip_sync_engine.currentData()
        )
        self.current_shot.lip_sync_target_character = (
            self.lip_sync_target.text().strip()
            or self.current_shot.speaker
        )
        self.current_shot.lip_sync_mode = str(
            self.lip_sync_mode.currentData()
        )

    @staticmethod
    def _shot_settings(shot: ShotSnapshot) -> dict[str, object]:
        return {
            "engine_profile": shot.engine_profile,
            "subject_motion": shot.subject_motion,
            "environment_motion": shot.environment_motion,
            "continuity_constraints": shot.continuity_constraints,
            "negative_prompt": shot.negative_prompt,
            "end_frame_prompt": shot.end_frame_prompt,
            "motion_prompt": shot.motion_prompt or shot.subject_motion,
            "native_audio_mode": shot.native_audio_mode,
            "dialogue_prompt": shot.dialogue or shot.dialogue_prompt,
            "sound_effect_prompt": shot.sound_effect_prompt,
            "music_prompt": shot.music_prompt,
            "camera_movement": shot.camera_movement,
            "motion_strength": shot.motion_strength,
            "screen_direction": shot.screen_direction,
            "transition_out": shot.transition_out,
            "transition_frames": shot.transition_frames,
            "handle_frames": shot.handle_frames,
            "candidate_count": shot.candidate_count,
            "duration_seconds": shot.duration_seconds,
        }

    @staticmethod
    def _shot_audio_settings(shot: ShotSnapshot) -> dict[str, object]:
        text = shot.dialogue.strip()
        if shot.audio_mode == "auto_narration" and not text:
            text = shot.description.strip()
        return {
            "shot_number": shot.number,
            "source_video": shot.video_path,
            "mode": shot.audio_mode,
            "speaker": shot.speaker or "旁白",
            "text": text,
            "engine": shot.tts_engine,
            "voice_id": shot.voice_id,
            "reference_audio": (
                str(shot.voice_reference_path)
                if shot.voice_reference_path
                else ""
            ),
            "reference_text": shot.voice_reference_text,
            "instruct_text": shot.voice_instruct_text,
            "fallback_to_edge": shot.fallback_to_edge,
            "rate": shot.speech_rate,
            "volume": shot.speech_volume,
            "pitch": shot.speech_pitch,
            "subtitle_enabled": shot.subtitle_enabled,
            "preserve_source_audio": shot.native_audio_mode != "off",
            "source_audio_gain_db": -6.0,
            "ducking_gain_db": -12.0,
        }

    @staticmethod
    def _shot_lip_sync_settings(shot: ShotSnapshot) -> dict[str, object]:
        return {
            "enabled": shot.lip_sync_enabled,
            "engine": shot.lip_sync_engine,
            "target_character": (
                shot.lip_sync_target_character or shot.speaker
            ),
            "mode": shot.lip_sync_mode,
            "inference_steps": 20,
            "guidance_scale": 1.5,
        }

    def _request_generate(self) -> None:
        if not self.episode:
            return
        self._commit_current_editor()
        selected_engine = str(self.engine.currentData())
        selected: list[dict[str, object]] = []
        for row, shot in enumerate(self.episode.shots):
            item = self.table.item(row, 0)
            if (
                item
                and item.checkState() == Qt.CheckState.Checked
                and shot.source_image
                and shot.image_qc_status == "approved"
            ):
                payload = self._shot_settings(shot)
                payload.update(
                    {
                        "shot_number": shot.number,
                        "source_image": shot.source_image,
                        "end_image": shot.end_image,
                        "scene_description": shot.description,
                        "engine_profile": (
                            shot.engine_profile
                            if self.auto_route.isChecked()
                            else selected_engine
                        ),
                    }
                )
                selected.append(payload)
        if not selected:
            QMessageBox.warning(
                self,
                "没有可生成镜头",
                "请先在“分镜脚本”页面通过首帧质检，再勾选至少一个镜头。",
            )
            return
        required_engines = {
            str(item.get("engine_profile") or "comic_motion") for item in selected
        }
        unavailable = sorted(
            engine
            for engine in required_engines
            if engine != "comic_motion" and engine not in self.ready_ai_engines
        )
        if unavailable:
            status = "所选模型文件或 ComfyUI 原生节点尚未就绪。"
            QMessageBox.information(
                self,
                "AI 视频引擎尚未开放",
                f"{status}\n缺少：{'、'.join(unavailable)}\n\n"
                "请到“连接与设置”启动服务器并重新检测。",
            )
            return
        if any(
            item.get("engine_profile") == "wan22_flf2v" for item in selected
        ):
            missing_end = [
                int(item["shot_number"])
                for item in selected
                if item.get("engine_profile") == "wan22_flf2v"
                and not item.get("end_image")
            ]
            if missing_end:
                width, height = self.resolution.currentData()
                self.end_frames_requested.emit(
                    self.episode.number,
                    selected,
                    int(width),
                    int(height),
                    self.fps.value(),
                )
                return
        width, height = self.resolution.currentData()
        self.generate_requested.emit(
            self.episode.number,
            selected,
            int(width),
            int(height),
            self.fps.value(),
        )

    def _request_compose(self) -> None:
        if self.episode:
            self.compose_requested.emit(self.episode.number)

    def _request_dub(self) -> None:
        if not self.episode or not self.dubbing_ready:
            return
        self._commit_current_editor()
        payload: list[dict[str, object]] = []
        for shot in self.episode.shots:
            settings = self._shot_audio_settings(shot)
            if shot.video_path and shot.video_path.is_file():
                if (
                    settings["mode"] == "dialogue"
                    and not str(settings["text"]).strip()
                ):
                    QMessageBox.warning(
                        self,
                        "配音文案为空",
                        f"镜头 {shot.number:02d} 使用角色对白模式，请输入配音文案。",
                    )
                    return
                payload.append(settings)
        if not payload:
            QMessageBox.warning(
                self,
                "没有可配音镜头",
                "请先生成镜头视频，并保留自动旁白或输入配音文案。",
            )
            return
        self.dub_requested.emit(self.episode.number, payload)

    def _request_timeline_plan(self) -> None:
        if not self.episode:
            return
        self._commit_current_editor()
        payload = [
            self._shot_audio_settings(shot)
            for shot in self.episode.shots
        ]
        self.timeline_plan_requested.emit(self.episode.number, payload)

    def _request_voice_preview(self) -> None:
        if not self.episode or not self.current_shot:
            return
        self._commit_current_editor()
        payload = self._shot_audio_settings(self.current_shot)
        if payload["mode"] == "mute":
            QMessageBox.information(self, "当前镜头静音", "静音镜头没有可试听文案。")
            return
        if not str(payload["text"]).strip():
            QMessageBox.warning(self, "配音文案为空", "请先填写要试听的配音文案。")
            return
        self.voice_preview_requested.emit(
            self.episode.number,
            self.current_shot.number,
            payload,
        )

    def _request_lip_sync(self) -> None:
        if not self.episode or not self.current_shot:
            return
        self._commit_current_editor()
        shot = self.current_shot
        if not self.latentsync_ready:
            QMessageBox.warning(
                self,
                "LatentSync 尚未就绪",
                "请先到“连接与设置”检测或安装 LatentSync 1.6。",
            )
            return
        if not shot.lip_sync_enabled:
            QMessageBox.warning(
                self,
                "口型任务未启用",
                "请先勾选“对白镜头启用人物口型”。",
            )
            return
        if shot.audio_mode != "dialogue" or shot.speaker == "旁白":
            QMessageBox.warning(
                self,
                "不是人物对白",
                "旁白镜头不应驱动人物嘴型，请仅对角色对白启用口型。",
            )
            return
        if not shot.video_path or not shot.video_path.is_file():
            QMessageBox.warning(
                self,
                "缺少镜头视频",
                "请先生成并选中当前镜头的视频候选。",
            )
            return
        if shot.lip_sync_mode == "manual_anchor":
            QMessageBox.warning(
                self,
                "手动目标脸尚未接入",
                "当前官方适配器先支持单人镜头；多人镜头请改用“单人镜头自动选择”。",
            )
            return
        payload = self._shot_audio_settings(shot)
        payload["lip_sync"] = self._shot_lip_sync_settings(shot)
        self.lip_sync_requested.emit(
            self.episode.number,
            shot.number,
            payload,
        )

    def _request_lip_sync_batch(self) -> None:
        if not self.episode:
            return
        if not self.latentsync_ready:
            QMessageBox.warning(
                self,
                "LatentSync 尚未就绪",
                "请先到“连接与设置”检测或安装 LatentSync 1.6。",
            )
            return
        self._commit_current_editor()
        self.lip_sync_batch_requested.emit(self.episode.number, False)

    def _tts_engine_changed(self, _index: int) -> None:
        cosy = str(self.tts_engine.currentData()) == "cosyvoice"
        self.voice.setEnabled(not cosy)
        self.voice_reference.setEnabled(cosy)
        self.choose_voice_reference.setEnabled(cosy)
        self.voice_reference_text.setEnabled(cosy)
        self.voice_instruct.setEnabled(cosy)
        self.fallback_to_edge.setEnabled(cosy)
        self.preview_voice.setToolTip(
            (
                "使用 3090 上的 CosyVoice 3 试听"
                if self.cosyvoice_online
                else "生成时会尝试启动 3090 上的 CosyVoice 3"
            )
            if cosy
            else "使用 Edge TTS 试听"
        )

    def _engine_changed(self, _index: int) -> None:
        engine = str(self.engine.currentData())
        if engine == "comic_motion":
            return
        recommended = self.resolution.findData((832, 480))
        if recommended >= 0:
            self.resolution.setCurrentIndex(recommended)
        self.fps.setValue(24 if engine == "minimax_h3_fl2va" else 16)

    def _open_current_video(self) -> None:
        if self.current_shot and self.current_shot.video_path:
            self.open_file_requested.emit(self.current_shot.video_path)

    def _open_dubbed_video(self) -> None:
        if self.episode and self.episode.dubbed_video_path:
            self.open_file_requested.emit(self.episode.dubbed_video_path)

    def _select_all_ready(self) -> None:
        if not self.episode:
            return
        for row, shot in enumerate(self.episode.shots):
            item = self.table.item(row, 0)
            if (
                item
                and shot.source_image
                and shot.image_qc_status == "approved"
            ):
                item.setCheckState(Qt.CheckState.Checked)

    def _clear_editor(self) -> None:
        self.editor_title.setText("镜头参数")
        self.subject_motion.clear()
        self.environment_motion.clear()
        self.continuity_constraints.clear()
        self.negative_prompt.clear()
        self.native_audio_mode.setCurrentIndex(0)
        self.sound_effect_prompt.clear()
        self.music_prompt.clear()
        self.dialogue.clear()
        self.speaker.setText("旁白")
        self.voice_reference.clear()
        self.voice_reference_text.clear()
        self.voice_instruct.clear()
        self.fallback_to_edge.setChecked(True)
        self.lip_sync_enabled.setChecked(False)
        self.lip_sync_target.clear()
        self.lip_sync_status.setText("口型：未启用")
        self.run_lip_sync.setEnabled(False)
        self.preview.setPixmap(QPixmap())
        self.preview.setText("选择一个镜头")
        self.end_preview.setPixmap(QPixmap())
        self.end_preview.setText("可选结束帧")
        self.open_video.setEnabled(False)
        self.open_dubbed.setEnabled(False)

    def _refresh_elapsed(self) -> None:
        seconds = time.monotonic() - self.started_at if self.started_at else 0
        minutes, remaining = divmod(int(seconds), 60)
        self.elapsed.setText(f"{minutes:02d}:{remaining:02d}")
