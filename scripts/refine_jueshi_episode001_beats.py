"""Apply the reviewed 25-beat adaptation of Jueshi episode 1."""

from __future__ import annotations

import json
from pathlib import Path

from app.agents.director import _expand_compact_beat, _parse_shots
from app.core.config import settings
from app.core.files import atomic_write_json


def _beat(
    scene: str,
    *,
    characters: list[str],
    action: str,
    visual: str,
    duration: float,
    beat_type: str,
    camera: str = "medium shot",
    movement: str = "static",
    dialogue: str = "",
    location: str = "云影镇秦家药圃",
    expression: str = "",
    lighting: str = "清晨柔和侧光，固定从画面右后方照入",
    atmosphere: str = "低处薄雾与药草叶片轻微摆动",
    direction: str = "static",
    transition: str = "cut",
) -> dict[str, object]:
    return {
        "scene_description": scene,
        "characters": characters,
        "location": location,
        "camera_angle": camera,
        "camera_movement": movement,
        "beat_type": beat_type,
        "visible_action": action,
        "expression": expression,
        "dialogue": dialogue,
        "duration_seconds": duration,
        "visual_prompt": visual,
        "lighting": lighting,
        "atmosphere": atmosphere,
        "screen_direction": direction,
        "transition_hint": transition,
    }


BEATS = [
    _beat(
        "清晨薄雾笼罩秦家药圃，大片灵田沿山势铺开。秦风沿土径缓步进入画面，手掌轻按胸口，望向枯黄药草，神情复杂而警醒。",
        characters=["秦风"],
        action="秦风沿土径向画面右侧缓慢走两步，按住胸口后停下，视线落向枯萎灵田",
        visual="wide environmental shot of Qin Feng entering a misty damaged medicinal herb garden, one hand over his injured chest, withered fields and wooden sheds",
        duration=3.5,
        beat_type="establish",
        camera="wide shot",
        movement="tracking",
        dialogue="旁白：秦风重生回到十万年前，第一件事便是为自己寻找疗伤灵药。",
        direction="left_to_right",
    ),
    _beat(
        "秦家书院大比的记忆骤然闪回。月夜擂台上，林淑婉白衣翻飞，剑锋刚刚刺入秦风胸前衣料；两人身体轮廓清楚分离，秦风因剧痛向后失去重心。",
        characters=["林淑婉", "秦风"],
        action="林淑婉维持短促刺剑余势，秦风胸口受击后向后退半步，剑与双手始终清晰分离",
        visual="moonlit academy arena flashback, white-robed young swordswoman Lin Shuwan's sword touching Qin Feng's chest as he recoils, anatomically separated bodies",
        duration=4.0,
        beat_type="flashback",
        camera="medium shot",
        dialogue="旁白：一个月前，未婚妻林淑婉一剑刺穿了他的心肺。",
        location="秦家书院大比擂台·闪回",
        lighting="冷蓝月光与擂台火把形成强烈侧逆光",
        atmosphere="记忆边缘轻微失焦，空气中浮动细小尘屑",
    ),
    _beat(
        "画面回到药圃。秦风收紧按在胸口的手指，短暂闭眼压下痛楚，再睁眼时目光已恢复沉静坚定；青色衣摆被晨风向右轻轻带起。",
        characters=["秦风"],
        action="秦风缓慢吐气，放下按胸的手，睁眼后把重心稳稳移向前脚",
        visual="close medium shot of Qin Feng recovering from chest pain in the herb garden, calm determined phoenix eyes, cyan robe moving in morning breeze",
        duration=3.5,
        beat_type="reaction",
        camera="close-up",
        movement="slow_push",
        dialogue="旁白：前世的悲剧，绝不能再度上演。",
    ),
    _beat(
        "泥土近景中，一株七叶星珑草歪倒在田埂边，恰好七片叶子卷曲发黄，茎秆干裂。秦风的青色袖口从画面左侧伸入，却没有遮住灵草与根部。",
        characters=["秦风"],
        action="秦风的两根手指轻轻拨开一片枯叶，动作停在露出茎根的位置",
        visual="macro insert of an exactly seven-leaf withered spirit herb, cracked stem and yellow curled leaves, Qin Feng's pale-cyan sleeve and careful fingers",
        duration=3.0,
        beat_type="action",
        camera="close-up",
        dialogue="旁白：七叶星珑草本可压制他的伤势，如今却已大半枯萎。",
    ),
    _beat(
        "秦风单膝蹲在灵地边缘，用指尖拨开松散泥土，完整露出被粗暴扯动的草根与新鲜移植划痕。他侧脸清楚，视线紧盯根系。",
        characters=["秦风"],
        action="秦风顺着上一镜头的手势拨开薄土，托起一小段外露草根后保持不动",
        visual="medium close-up of Qin Feng kneeling and revealing exposed transplanted roots in dark soil, analytical side profile fully visible",
        duration=3.5,
        beat_type="action",
        camera="medium shot",
        dialogue="秦风：草根外露，还有移植痕迹。",
    ),
    _beat(
        "低位镜头沿灵泉灌溉沟向前看，碎石与淤泥堵住一半水道，细弱水流绕过障碍。秦风的手指顺着水流方向划过沟沿，指出堵塞位置。",
        characters=["秦风"],
        action="秦风的食指沿沟沿向右移动，停在碎石堵塞处，水流持续缓慢绕行",
        visual="low angle insert of a partially blocked ancient irrigation channel, weak water flow around stones, Qin Feng's pointing hand entering from left",
        duration=3.0,
        beat_type="action",
        camera="POV",
        movement="pan_right",
        dialogue="旁白：灵泉灌溉也被人动了手脚。",
        direction="left_to_right",
    ),
    _beat(
        "一簇红艳离阳草立在背阳灵地中央，与周围枯黄药草形成刺眼对比。秦风从画面左后方俯身靠近，红色叶尖映入他锐利的双眼。",
        characters=["秦风"],
        action="秦风从水沟方向抬眼看向红色离阳草，身体随视线轻微转向右侧",
        visual="red Liyang grass glowing among dying herbs, Qin Feng leaning into frame and recognizing the clue, vivid red against muted earth tones",
        duration=3.0,
        beat_type="reaction",
        camera="medium shot",
        movement="slow_push",
        dialogue="旁白：离阳草不该与其他灵药混种，这就是药圃枯萎的关键。",
        direction="left_to_right",
    ),
    _beat(
        "秦风半蹲在离阳草旁，目光在红草、堵塞水沟与移植痕迹之间迅速移动，最后定在来路方向。他眉峰轻抬，已将破坏手法完全串联起来。",
        characters=["秦风"],
        action="秦风依次扫视三处线索，最后抬头看向画面左侧来路，嘴角压成冷静直线",
        visual="analytical close-up of Qin Feng connecting clues in the herb garden, red grass foreground, blocked channel and uprooted herbs layered behind",
        duration=3.5,
        beat_type="reaction",
        camera="close-up",
        dialogue="秦风：原来如此，动手的人手法并不高明。",
        direction="right_to_left",
    ),
    _beat(
        "秦风从离阳草旁站起，转身面向药圃深处。他没有拔剑，只抬手示意身后护卫上前，声音穿过晨雾。",
        characters=["秦风"],
        action="秦风起身后向身后招手一次，手掌随即落回身侧，视线保持朝向来人",
        visual="Qin Feng rising beside red spirit grass and summoning his guard with one controlled hand gesture, no weapon drawn",
        duration=3.0,
        beat_type="dialogue",
        camera="medium shot",
        dialogue="秦风：秦三秋！",
    ),
    _beat(
        "秦三秋从画面右侧快步进入，在秦风身前两步处抱拳停下。深棕皮甲与秦风的青色长袍形成清楚区分，两人的视线沿同一轴线相接。",
        characters=["秦风", "秦三秋"],
        action="秦三秋快步上前后抱拳站定，秦风只转动视线看向他，双方位置保持不变",
        visual="two-shot in herb garden, Qin Sanqiu in dark-brown leather armor reporting to Qin Feng in pale-cyan and deep-teal robes, distinct faces and silhouettes",
        duration=4.0,
        beat_type="dialogue",
        camera="medium shot",
        dialogue="秦三秋：林家大公子半个月前才将药圃交割给属下打理。",
    ),
    _beat(
        "秦风不发一言沿主路向药圃深处行进，秦三秋落后半步跟随。前景枯叶掠过，两侧受损灵田一块接一块显露，二人始终向画面右侧移动。",
        characters=["秦风", "秦三秋"],
        action="秦风稳定向右行走，秦三秋保持半步距离跟随并不断观察两侧灵田",
        visual="tracking two-shot of Qin Feng and armored Qin Sanqiu walking deeper through successive damaged herb plots, consistent left-to-right movement",
        duration=3.5,
        beat_type="action",
        camera="wide shot",
        movement="tracking",
        dialogue="旁白：越往药圃深处走，发现的问题越多。",
        direction="left_to_right",
    ),
    _beat(
        "成片天罗果树在高处灵田中落叶枯黄，青涩果实散落泥地。秦三秋站在前景中央面色惨白、汗珠顺颊而下；秦风在他侧后方冷静查看枝叶。",
        characters=["秦三秋", "秦风"],
        action="秦三秋抬手触碰一片枯叶后迅速缩回，肩膀下沉；秦风伸手接住落叶检查",
        visual="devastated Tianluo fruit grove, armored Qin Sanqiu sweating in panic while calm Qin Feng examines a falling yellow leaf",
        duration=4.0,
        beat_type="reaction",
        camera="medium shot",
        dialogue="秦三秋：少爷，必须马上奏报主族，请真正的炼丹师前来治理！",
    ),
    _beat(
        "一名秦家护卫从主路尽头奔来，在秦风和秦三秋面前急停抱拳。秦风听到林浪的名字后，目光由天罗果缓慢移向谷口，眼底骤然变冷。",
        characters=["秦风", "秦三秋"],
        action="画外护卫在背景抱拳禀报，秦风只转动眼神看向谷口，秦三秋随之回头",
        visual="messenger guard reporting in background as Qin Feng and Qin Sanqiu turn their attention toward the valley entrance, cold reaction",
        duration=3.5,
        beat_type="reaction",
        camera="medium shot",
        dialogue="护卫：林家大公子林浪，已至谷外。",
        direction="right_to_left",
    ),
    _beat(
        "谷口木门被推开，林浪身穿皇家蓝银云纹锦袍，从画面左侧率四名随从闯入。秦风与秦三秋在远处右侧等候，双方沿主路形成明确对峙轴线。",
        characters=["林浪", "秦风", "秦三秋"],
        action="林浪跨过门槛后向右走两步，抬手让随从停在身后；秦风一方保持原位",
        visual="wide confrontation at herb garden gate, Lin Lang in royal-blue silver-cloud brocade entering from left with followers, Qin Feng and armored guard waiting on right",
        duration=4.0,
        beat_type="establish",
        camera="wide shot",
        movement="slow_push",
        dialogue="林浪：自然是来替你们解决问题的。",
        direction="left_to_right",
    ),
    _beat(
        "秦三秋从秦风身侧踏出半步，深棕皮甲肩线展开，长剑只拔出一小段寒光，剑柄与双手完整可见。林浪在对面停步，仍保持轻蔑笑意。",
        characters=["秦三秋", "林浪"],
        action="秦三秋向前踏半步并将剑拔出三分后停住，林浪只微抬下巴",
        visual="armored Qin Sanqiu partially drawing his sword against blue-robed nobleman Lin Lang, clear hands and blade, opposed eyelines",
        duration=3.5,
        beat_type="action",
        camera="medium shot",
        dialogue="秦三秋：秦家的产业，你们也敢擅闯！",
    ),
    _beat(
        "林浪站在画面左侧前景，用折扇轻点掌心，含笑逼视秦三秋；秦三秋位于右侧中景，拔剑气势因顾虑天罗果而稍稍迟疑，秦风仍在后方观察。",
        characters=["林浪", "秦三秋", "秦风"],
        action="林浪用折扇向枯黄灵田点一下，秦三秋握剑的手略微下沉，秦风保持不动",
        visual="over-shoulder pressure shot, distinct royal-blue nobleman Lin Lang gesturing toward ruined crops while brown-armored Qin Sanqiu hesitates and Qin Feng watches",
        duration=4.0,
        beat_type="dialogue",
        camera="over-shoulder",
        dialogue="林浪：本公子若走了，到时候你跪着求我都没用。",
    ),
    _beat(
        "越过秦风青色肩线看向林浪。林浪占据对面画面左侧，狭长狐眼带着讥诮，蓝色锦袍与银云纹清晰；他把视线从秦三秋转向秦风。",
        characters=["林浪", "秦风"],
        action="林浪缓慢转动眼神锁定秦风，折扇收拢停在胸前，嘴角浮起轻蔑笑意",
        visual="over Qin Feng's cyan shoulder toward Lin Lang's long narrow face and fox eyes, royal-blue brocade, contemptuous confrontation",
        duration=4.0,
        beat_type="dialogue",
        camera="over-shoulder",
        dialogue="林浪：你一个心脉受损的废人，就不要在这里碍事。",
    ),
    _beat(
        "反打镜头越过林浪蓝色肩线看向秦风。秦风站在画面右侧，凤凰眼中压着怒意，却没有前冲；他将按胸的手缓缓放下，直视林浪。",
        characters=["秦风", "林浪"],
        action="秦风放下按胸的手，抬眼与林浪对视，身体和脚步保持稳定",
        visual="reverse over-shoulder shot of Qin Feng's youthful oval face and large phoenix eyes, cyan ribbon and deep-teal robe, controlled anger",
        duration=3.5,
        beat_type="dialogue",
        camera="over-shoulder",
        dialogue="秦风：我的心脉，正是你的好妹妹林淑婉亲手所伤。",
    ),
    _beat(
        "林浪面部中近景，狭长狐眼毫无愧色，下巴高昂。他向外摊开一只手，把比武受伤说成秦风技不如人，身后随从彼此交换怪异目光。",
        characters=["林浪"],
        action="林浪摊开右手后轻蔑地向下压一次，下巴始终高昂，脸部保持正侧三分之二可见",
        visual="close medium portrait of arrogant young nobleman Lin Lang with narrow fox eyes, aquiline nose, royal-blue brocade, shameless dismissive gesture",
        duration=4.0,
        beat_type="dialogue",
        camera="close-up",
        dialogue="林浪：武道大比，生死各安天命！受伤只怪你技不如人。",
    ),
    _beat(
        "第二段记忆闪回中，少年秦风在擂台上已将剑势收住，林淑婉却从他侧前方突然刺来。白衣剑影与青衣身体分离，秦风胸口衣料破开但画面不过度血腥。",
        characters=["林淑婉", "秦风"],
        action="秦风先收剑后撤，林淑婉趁空隙完成一小段突刺，秦风受击后向后失去重心",
        visual="academy duel flashback, Qin Feng lowering his sword in mercy as white-robed Lin Shuwan suddenly thrusts, readable betrayal without gore",
        duration=4.0,
        beat_type="flashback",
        camera="medium shot",
        dialogue="旁白：前世的他最后关头收手，却被那一剑断了武道之路。",
        location="秦家书院大比擂台·第二段闪回",
        lighting="冷白擂台天光与暗红旗帜形成高反差",
        atmosphere="短促记忆闪烁与细小尘屑，背景观众保持静止",
    ),
    _beat(
        "回到药圃的双人侧面镜头。林浪在左、秦风在右，二人隔着一条灵泉沟保持对峙。林浪继续谈及退婚与月摇仙宫，秦风的眼神却恢复如水般平静。",
        characters=["林浪", "秦风"],
        action="林浪微微前倾继续施压，秦风只进行一次缓慢呼吸，目光始终稳定",
        visual="profile two-shot across an irrigation channel, Lin Lang left in royal blue pressuring Qin Feng right in cyan and deep teal, distinct silhouettes and calm eyelines",
        duration=4.0,
        beat_type="dialogue",
        camera="medium shot",
        dialogue="林浪：小妹前途无量，你们的婚约日后就不要再提了。",
    ),
    _beat(
        "秦风向前半步站到画面右侧主位，抬手指向谷口，清楚说出秦家执法队。秦三秋在他后方重新握稳长剑，周围护卫从两侧形成包围。",
        characters=["秦风", "秦三秋", "林浪"],
        action="秦风向谷口做一次明确送客手势，秦三秋与护卫同步收紧包围但不挥剑",
        visual="heroic medium-wide shot, Qin Feng ordering Lin Lang out while brown-armored Qin Sanqiu and Qin guards close a controlled semicircle",
        duration=4.0,
        beat_type="action",
        camera="wide shot",
        dialogue="秦风：再敢涉足药圃半步，定请秦家执法队行事！",
    ),
    _beat(
        "护卫包围中，林浪先因执法队之名脸色一变，随即指向枯萎天罗果激将秦风。秦风在对面保持从容，双方之间留出清晰的谈判空间。",
        characters=["林浪", "秦风"],
        action="林浪指向天罗果后收回手，向前探身提出赌约；秦风保持原位听完",
        visual="tense bargaining two-shot, blue-robed Lin Lang pointing at ruined Tianluo fruit while calm Qin Feng listens across clear negative space",
        duration=4.5,
        beat_type="dialogue",
        camera="medium shot",
        dialogue="林浪：你若完成今年灵药收缴，本公子便把林家铁矿矿脉拱手相让！",
    ),
    _beat(
        "石桌俯拍中，赌约铺在中央，铁矿地图和双方印记分列两侧。林浪蓝色银纹袖口握笔停在左侧，秦风青色袖口在右侧按稳纸张，两人的脸在上方背景仍可辨认。",
        characters=["林浪", "秦风"],
        action="林浪完成最后一小段落笔后抬起笔，秦风按稳赌约并在自己的名字旁落下指印",
        visual="top-down contract signing at stone table, royal-blue silver sleeve and pale-cyan sleeve clearly separated, iron mine map, both young faces visible above",
        duration=4.0,
        beat_type="action",
        camera="high angle",
        dialogue="秦风：林家铁矿，我要定了。",
    ),
    _beat(
        "谷口方向的收束镜头中，林浪带随从沿主路向画面左侧离去，蓝色背影逐渐远离。秦风留在右侧前景，青色衣摆轻动，望着对方露出克制而轻蔑的笑。",
        characters=["秦风", "林浪"],
        action="林浪向左走出数步不再回头，秦风保持站位，只缓慢抬眼并形成一丝轻蔑笑意",
        visual="cinematic closing wide shot, Lin Lang and followers departing left while Qin Feng remains right foreground with a restrained knowing smile in the damaged herb garden",
        duration=3.5,
        beat_type="reaction",
        camera="wide shot",
        movement="slow_pull",
        dialogue="秦风：林家铁矿，便是我回报你们兄妹的第一份大礼。",
        direction="right_to_left",
        transition="fade_black",
    ),
]


CURATED_FINGERPRINTS = {
    "秦风": (
        "18-year-old unmistakably male hero; handsome masculine young male facial "
        "bone structure, flat male chest and visible male neck; small refined oval "
        "face, soft youthful cheeks, very "
        "large bright phoenix eyes, short delicate chin; high flowing black "
        "ponytail with a pale-cyan jade clasp and narrow cyan ribbon; pale-cyan "
        "cross-collar inner robe under deep-teal outer robe with restrained "
        "silver bamboo embroidery; slim silhouette; never armor or royal blue"
    ),
    "林浪": (
        "19-year-old unmistakably male noble rival; handsome masculine young male "
        "facial bone structure, flat male chest and visible male neck; visibly "
        "longer narrow oval face, high "
        "cheekbones, narrow fox-like eyes, aquiline nose and thin smirking lips; "
        "polished high black ponytail with dark-blue jade clasp; luxurious "
        "royal-blue brocade with silver cloud embroidery and jade thumb ring; "
        "never pale-cyan robe, leather armor or facial scars"
    ),
    "秦三秋": (
        "21-year-old unmistakably male guard; masculine young male facial bone "
        "structure, flat male chest and visible male neck; softly square broad "
        "youthful face, straight loyal "
        "eyes, warmer healthy skin and broad athletic shoulders; practical high "
        "ponytail; fitted dark-brown leather lamellar armor over charcoal-black "
        "robes, sword at waist; clean unscarred face; never silk noble clothing"
    ),
    "林淑婉": (
        "18-year-old unmistakably female swordswoman; delicate heart-shaped face, "
        "enormous luminous "
        "almond eyes, small straight nose and rose-petal lips; waist-length black "
        "hair in a half-up style with white-jade blossoms and silver hair chains; "
        "floating pure-white layered silk hanfu with silver embroidery; willowy "
        "silhouette; never cyan, royal-blue or leather armor"
    ),
}


def main() -> int:
    episode_path = (
        settings.projects_dir
        / "jueshi"
        / "production"
        / "episodes"
        / "episode_001.json"
    )
    value = json.loads(episode_path.read_text(encoding="utf-8"))
    profiles = {
        str(name): str(profile)
        for name, profile in (value.get("character_profiles") or {}).items()
    }
    raw = [
        _expand_compact_beat(item, profiles=profiles)
        for item in BEATS
    ]
    for index, item in enumerate(raw, start=1):
        item["shot_number"] = index
    shots = _parse_shots({"shots": raw})
    payloads = []
    for shot in shots:
        payload = shot.model_dump(mode="json")
        dialogue = shot.dialogue.strip()
        speaker, separator, text = dialogue.partition("：")
        audio = payload["audio_generation"]
        if dialogue:
            if separator and speaker and speaker != "旁白":
                audio.update(
                    {
                        "mode": "dialogue",
                        "speaker": speaker,
                        "text": text.strip(),
                    }
                )
            else:
                audio.update(
                    {
                        "mode": "auto_narration",
                        "speaker": "旁白",
                        "text": text.strip() if separator else dialogue,
                    }
                )
        payloads.append(payload)
    value["artifact_binding_policy"] = "explicit_only"
    value["character_visual_fingerprints"] = CURATED_FINGERPRINTS
    value["shots"] = payloads
    value["summary"] = (
        "重生归来的秦风在秦家药圃查明灵药枯萎是人为破坏，"
        "面对林浪的羞辱与夺权图谋，他稳住心境、调动护卫，"
        "顺势签下赌约，将林家铁矿视为复仇的第一份回礼。"
    )
    atomic_write_json(episode_path, value)
    print(
        f"updated={episode_path} shots={len(payloads)} "
        f"duration={sum(item['duration_seconds'] for item in payloads):.1f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
