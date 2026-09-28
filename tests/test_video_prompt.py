from app.pipeline.video_prompt import (
    beat_boundaries,
    build_storyboard_motion_prompt,
)


def test_twelve_second_prompt_matches_approved_beat_timing() -> None:
    assert beat_boundaries(12) == (0.0, 0.5, 3.5, 6.1, 9.0, 12.0)

    prompt = build_storyboard_motion_prompt(
        shot_number=1,
        duration_seconds=12,
        scene_description=(
            "苏州私家园林水榭。知微在左侧合上旅行画册；"
            "沈砚从右侧廊门进入，把三地行程平板放到石桌右沿，停在右侧。"
        ),
        subject_motion="知微合书抬眼；沈砚进门后把平板放到石桌右沿",
        environment_motion="园林水面与人物呼吸轻微连续运动",
        entry_state="本集初始构图，知微左侧、沈砚从右侧廊门进入，石桌居中",
        exit_state="知微左侧合书抬眼；沈砚右侧停步，平板压在右掌下",
        dialogue="沈砚：你今天又没安排？／知微：正好，给我件难的。",
        sound_effect="园林水声、合书声与脚步声",
        dramatic_point="用一句挑衅建立兄妹反差",
    )

    assert "拍2｜本镜0.5-3.5秒｜起势拍" in prompt
    assert "拍3｜本镜3.5-6.1秒｜推进拍" in prompt
    assert "拍4｜本镜6.1-9秒｜反应拍" in prompt
    assert "拍5｜本镜9-12秒｜落点拍" in prompt
    assert "台词同步：沈砚：你今天又没安排？／知微：正好，给我件难的。" in prompt
    assert "禁止凭空出现、瞬移、突然消失、机械连招和肢体僵硬" in prompt
    assert "尾帧定格：C01，知微左侧合书抬眼；沈砚右侧停步" in prompt


def test_long_dialogue_prompt_keeps_tail_frame_contract() -> None:
    prompt = build_storyboard_motion_prompt(
        shot_number=43,
        duration_seconds=12,
        scene_description="石桌前保持左右对峙和明确道具位置",
        subject_motion="左侧人物伸手提出条件，右侧人物保持听取姿态",
        environment_motion="水面、树叶和衣摆轻微连续运动",
        entry_state="上一镜尾帧的左右站位、视线和道具",
        exit_state="左侧人物收手，右侧人物抬眼准备答复",
        dialogue="林浪：你若完成今年的灵药收缴，我便把林家铁矿拱手相让；若完不成，药圃未来三年归我管理。你可敢赌？",
        sound_effect="园林水声与衣料声",
        dramatic_point="挑衅与谈判压力",
    )
    assert len(prompt) < 4000
    assert "尾帧定格：C43，左侧人物收手" in prompt
