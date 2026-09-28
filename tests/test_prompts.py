from app.core.prompts import load_prompt


def test_packaged_prompts_are_loadable() -> None:
    assert "职业类型小说编剧" in load_prompt("story_planner")
    assert "每个章节必须且只能出现一次" in load_prompt("adaptation_outline")
    assert "纯 JSON 对象" in load_prompt("chapter_analyzer")
    assert "continuity_plan" in load_prompt("director")
