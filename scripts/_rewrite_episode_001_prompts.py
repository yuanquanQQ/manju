from __future__ import annotations

import json
from pathlib import Path

from app.pipeline.audio_timing import optimize_episode_audio_timing

ROOT = Path(__file__).resolve().parents[1]
EPISODE_PATH = ROOT / "projects/jueshi/production/episodes/episode_001.json"
CHARACTER_PATH = ROOT / "projects/jueshi/assets/characters/renwu_qingdan.json"

STYLE = (
    "premium photorealistic live-action Chinese xianxia drama, real East Asian actors, "
    "cinematic natural light, realistic skin and fabric, restrained color grade, "
    "physically coherent anatomy, 16:9 frame"
)
IMAGE_GUARD = (
    "same locked cast identity and costume as the approved three-view reference sheets, "
    "no face blending, no gender change, no costume drift, no extra foreground person, "
    "no anime, no illustration, no CGI, no text, no logo, no watermark"
)
VIDEO_NEGATIVE = (
    "identity morphing, face swap, age change, gender change, costume change, hairstyle change, "
    "extra person, duplicate person, extra limbs, deformed hands, floating feet, teleportation, "
    "jump cut inside shot, crossed screen axis, sudden camera jump, fast head turn, exaggerated "
    "gesture, flicker, frame jitter, warped background, moving buildings, unreadable face, text, "
    "logo, watermark"
)

PROFILES = {
    "秦风": (
        "exceptionally handsome 18-year-old Chinese male protagonist, outwardly youthful but "
        "carrying the stillness and authority of an ancient soul; unmistakably masculine refined "
        "oval face with subtle angular cheek planes, straight dark brows, medium phoenix-shaped "
        "eyes, straight nose, firm jaw and compact squared chin; healthy fair natural skin, clean "
        "shaven; lean athletic build; long black hair in a high ponytail fixed by one pale-cyan jade "
        "clasp; pale-cyan cross-collar inner robe and deep-teal outer robe with restrained silver "
        "bamboo embroidery, dark belt and black cloth boots; calm, observant, never boyish, feminine "
        "or ornate"
    ),
    "林浪": (
        "strikingly handsome 21-year-old Chinese male noble heir, polished and privileged; "
        "unmistakably masculine long aristocratic face, high cheekbones, narrow fox-shaped eyes, "
        "straight brows, elegant aquiline nose, sharp jaw and thin controlled lips; fair natural "
        "skin, clean shaven; tall slim build; long black hair in a precise high ponytail with one "
        "dark-blue jade clasp; royal-blue silk brocade robe with restrained silver cloud pattern, "
        "dark-blue belt, jade thumb ring and black boots; cold vanity and practiced contempt, no fan "
        "unless explicitly required"
    ),
    "秦三秋": (
        "handsome 28-year-old Chinese male guard commander, dependable and battle-ready; broad "
        "rectangular masculine face, strong square jaw, thick level brows, deep-set alert eyes, "
        "straight medium-width nose, lightly sun-touched natural skin, clean shaven; powerful "
        "athletic build with broad shoulders; practical high black ponytail; fitted dark-brown "
        "leather lamellar armor over charcoal robes, plain bracers, dark boots and one sheathed "
        "straight sword; disciplined rather than glamorous, clearly distinct from both young nobles"
    ),
    "林淑婉": (
        "breathtakingly beautiful 18-year-old Chinese female swordswoman, ethereal at first glance "
        "but emotionally cold; delicate heart-shaped face, luminous almond eyes, fine arched brows, "
        "small straight nose, rose-toned lips and luminous fair natural skin; willowy athletic build; "
        "waist-length black hair in a restrained half-up style with two small white-jade blossoms; "
        "pure-white layered silk hanfu with fine silver embroidery, narrow silver belt and white boots; "
        "one elegant straight sword, refined minimal makeup, no oversized jewelry"
    ),
}

APPEARANCE = {
    "秦风": "18岁俊美但明确阳刚的青年，凤眼、直眉、利落下颌和方正短下巴；黑发高束，神态沉静，少年外貌中带十万年阅历的压迫感",
    "林浪": "21岁俊美贵公子，长脸、高颧骨、狭长狐眼、鹰直鼻和锋利下颌；黑发高束，笑意轻慢，五官与秦风明确不同",
    "秦三秋": "28岁英武护卫统领，宽方脸、浓直眉、深眼窝、强壮下颌和日晒肤色；高马尾，体格宽厚，忠诚而警觉",
    "林淑婉": "18岁绝美女剑客，心形脸、明亮杏眼、细眉、小巧直鼻和淡玫瑰唇；黑发半挽，气质清冷危险",
}

CLOTHING = {
    "秦风": "浅青交领内袍、深青外袍、克制银竹纹、深色腰带、黑布靴；严格沿用三视图的层次、颜色和玉扣",
    "林浪": "皇家蓝锦袍、克制银云纹、深蓝腰带、玉扳指、黑靴；严格沿用三视图，不擅自增加折扇与首饰",
    "秦三秋": "深棕皮质札甲、炭黑内袍、素面护腕、深色靴、腰悬直剑；严格沿用三视图",
    "林淑婉": "纯白多层丝质劲装式汉服、细银纹、窄银腰带、白靴、直剑；严格沿用三视图",
}

VOICE_BASE = {
    "旁白": "成熟男声，低沉温厚但不苍老，近距离影视旁白；像亲历者冷静讲故事，不朗诵、不拖腔，重音落在剧情信息上，句尾自然收住并留半拍呼吸。",
    "秦风": "十八岁青年男声，清朗中带稳定低音；外表少年、内里是十万年强者，冷静克制、不喊叫、不故作沧桑，语速从容，关键字轻压重音，句尾果断。",
    "林浪": "二十一岁贵公子男声，音色清亮偏冷、咬字讲究；自信傲慢而非粗暴老反派，带轻微笑意和居高临下感，避免嘶哑、油腻与夸张邪笑。",
    "秦三秋": "二十八岁护卫统领男声，中低音结实，尊敬少爷但有军伍利落感；短句干净，紧张时气息略急但不破音、不吼叫。",
    "护卫": "年轻男护卫，气息稍急、禀报清楚，先稳住呼吸再说话；简短利落，不播报、不喊口号。",
}

VOICE_CONFIG = {
    "旁白": ("zh-CN-YunyangNeural", "edge_narrator", "-6%", "-10Hz"),
    "秦风": ("zh-CN-YunxiNeural", "edge_young_hero_cool", "-2%", "+3Hz"),
    "林浪": ("zh-CN-YunjianNeural", "edge_villain_male", "-12%", "-18Hz"),
    "秦三秋": ("zh-CN-YunxiaNeural", "edge_guard_male", "+9%", "-5Hz"),
    "护卫": ("zh-CN-YunxiNeural", "edge_calm_male", "-5%", "-10Hz"),
}


# duration, dialogue, speaker, cast, scene, image detail, camera, movement,
# beat, emotion, exit state, screen direction, transition out, performance note
SHOTS = [
    (5.5, "旁白：云影镇，秦家药圃。", "旁白", [], "清晨薄雾越过山谷，秦家药圃沿山势层层铺开；近处七叶灵草枯黄，远处木屋与灵泉渠构成固定地标。", "high wide establishing view over a misty mountain herb garden; terraced plots, irrigation channel, east gate and wooden sheds form clear recurring landmarks; patches of withered medicinal plants in foreground", "panoramic high angle", "slow_crane_down", "establish", "宿命重启前的寂静", "镜头落到通往药圃深处的土径，右侧枯黄灵田成为下一镜入口", "left_to_right", "cut", "开场平静而有悬念，‘秦家药圃’略停顿，像把观众带入地点。"),
    (6.0, "旁白：秦风重生回到十万年前，第一件事，却是为自己寻找疗伤灵药。", "旁白", ["秦风"], "秦风沿土径从左向右缓步进入，右手轻按心口；他看见枯黄药草后停步，晨光始终从右后方照来。", "wide full-body shot of Qin Feng walking left-to-right on the established dirt path, right hand pressed to his injured chest, stopping before the withered plot; east gate behind left, sheds behind right", "wide shot", "slow_tracking_right", "action", "疼痛、警醒、克制", "秦风停在枯萎灵田左侧，右手仍按心口，视线落向右下方药草", "left_to_right", "dissolve", "‘重生回到十万年前’压低声音，‘第一件事’轻顿，末句不煽情。"),
    (5.5, "旁白：一个月前，未婚妻林淑婉在书院大比中，一剑刺穿了他的心肺。", "旁白", ["秦风", "林淑婉"], "记忆闪回：冷色月光下的书院擂台，秦风收势，林淑婉的剑锋已抵入他胸前衣料；两人身体、双手与剑身清楚分离。", "cold moonlit academy arena flashback; Qin Feng on screen left has lowered his sword, Lin Shuwan on screen right completes one short controlled thrust toward his chest; readable betrayal, separated bodies and hands, no gore", "medium wide shot", "slow_push", "flashback", "猝然背叛", "林淑婉维持刺剑终点，秦风向左后方失去半步重心，画面冻结在胸口受击的轮廓", "right_to_left", "dissolve", "叙述事实，不渲染血腥；‘未婚妻’与‘一剑’各给轻重音。"),
    (5.0, "旁白：前世的悲剧，绝不能再度上演。", "旁白", ["秦风"], "回到同一药圃位置，按胸动作与闪回受击位置做姿态匹配；秦风闭眼吐气，再睁眼时目光坚定。", "medium close-up back in the same herb garden, Qin Feng in the exact chest-holding pose carried from the flashback, morning side light, his eyes reopen with ancient calm and determination", "medium close-up", "slow_push", "reaction", "决意", "秦风放下按胸的手，双脚站稳，目光从右下方药草抬至前方", "static", "cut", "‘绝不能’压住重音，语气坚定但不高昂。"),
    (6.0, "旁白：心脉受损，让他的修为从淬体一重跌到气血五重；再拖下去，便会沦为武道废人。", "旁白", ["秦风"], "秦风以两指按住腕脉，另一手仍护住心口；脸色略显苍白，但站姿没有崩塌，背景保持同一片枯田。", "waist-up profile of Qin Feng checking his wrist pulse beside the same withered plot, slightly pale from internal injury yet standing straight, restrained pain, no magical interface or glowing body diagram", "medium shot", "static", "reaction", "危机逼近", "秦风确认脉象后松开手指，转头看向药圃边缘的七叶星珑草种植区", "left_to_right", "cut", "数字与境界读清楚，语速略慢；‘武道废人’低落收尾。"),
    (5.5, "旁白：以他十万年的炼丹阅历，只要找到七叶星珑草，就能先压住伤势。", "旁白", ["秦风"], "秦风沿田埂向右寻找，手指掠过不同灵草，最后望向边缘灵地；动作像经验丰富的炼丹师，而非慌乱病人。", "medium-wide tracking shot of Qin Feng moving right along the herb rows, calmly identifying plants with an expert glance, hand hovering above leaves without touching, edge plot visible ahead", "medium wide shot", "slow_tracking_right", "action", "从容自救", "秦风在边缘灵地前停下并缓慢蹲身，右手伸向一株枯萎七叶草", "left_to_right", "cut", "‘十万年的炼丹阅历’从容，‘七叶星珑草’清晰，不堆砌传奇腔。"),
    (5.0, "旁白：可眼前的灵药，竟已大半枯萎。", "旁白", ["秦风"], "边缘灵地大片发黄，秦风蹲在画面左侧，右侧成片灵药萎蔫；他的手停在一株七叶草上方。", "wide environmental shot of the edge plot, Qin Feng crouched on the left and a broad patch of withered medicinal herbs filling the right, his reaching hand suspended above one seven-leaf plant", "wide shot", "slow_push", "reaction", "异常显现", "秦风的指尖落到七叶草旁，轻轻拨开最上层枯叶", "static", "match_cut", "短句带一点意外，‘大半枯萎’压低。"),
    (5.0, "旁白：本该救命的七叶星珑草，如今叶卷茎裂，生机将绝。", "旁白", ["秦风"], "特写一株恰有七片叶的星珑草，卷叶、裂茎和湿润泥土清晰；秦风浅青袖口与两根手指从左侧进入。", "macro insert of one botanically coherent spirit herb with exactly seven curled yellowing leaves and a cracked stem in damp dark soil; Qin Feng's pale-cyan sleeve and two careful fingers enter from left", "macro close-up", "static", "reaction", "救命药将枯", "两根手指拨开枯叶，镜头露出松动泥土与外露根系", "left_to_right", "match_cut", "像观察关键证据，平静中带紧迫感。"),
    (4.5, "旁白：他俯身细看，很快发现了异样。", "旁白", ["秦风"], "顺着上一镜手的位置，秦风单膝蹲下，指尖沿松土向根部移动；侧脸与根系同时清楚。", "medium close-up continuing the same hand position, Qin Feng kneels on one knee and traces disturbed soil toward exposed roots; his masculine side profile and the plant remain in the same focal plane", "medium close-up", "gentle_tilt_down", "action", "抽丝剥茧", "秦风托起一小段外露草根，目光锁住新鲜的移植划痕", "left_to_right", "cut", "语气转入推理，‘异样’轻收。"),
    (4.0, "秦风：草根外露，还有移植痕迹。", "秦风", ["秦风"], "秦风保持单膝姿势，托住外露根系，用只有身边人能听清的音量下判断；嘴部无遮挡。", "dialogue-safe medium close-up of Qin Feng kneeling beside the exposed roots, clear three-quarter masculine face and unobstructed mouth, fingertips supporting the root without pulling it", "medium close-up", "static", "dialogue", "专业、冷静", "秦风说完后把根系轻放回土面，视线沿右侧灌溉沟移动", "left_to_right", "match_cut", "像炼丹师给出诊断，语速平稳，‘移植痕迹’轻压重音。"),
    (5.0, "旁白：再看灵泉，碎石和淤泥恰好堵住了半条水道。", "旁白", ["秦风"], "低位视角沿同一条灵泉沟向右，水流绕过碎石淤泥；秦风刚放下根系的手顺势进入画面指出堵塞。", "low-angle insert following the established irrigation channel to the right, weak water curling around deliberate stones and silt; Qin Feng's same hand continues into frame and points at the blockage", "low angle insert", "slow_pan_right", "action", "证据累积", "指尖停在最大碎石旁，细弱水流继续向右绕行", "left_to_right", "match_cut", "客观说明线索，不用惊叹语气。"),
    (5.5, "旁白：而本该种在背阳处的离阳草，竟被混进了其他灵药中央。", "旁白", ["秦风"], "镜头顺水沟右移，停在一簇红艳离阳草；秦风从左后方抬眼，红草周围尽是枯黄植株。", "medium shot continuing the rightward eyeline from the channel to a vivid cluster of red Liyang grass planted among dying herbs; Qin Feng leans in from left background and recognizes it", "medium shot", "slow_push", "action", "真相出现", "秦风由水沟转向红草，身体停在左侧三分位，眼神依次扫过周围枯田", "left_to_right", "cut", "‘本该’和‘竟被混进’形成因果重音。"),
    (5.5, "旁白：离阳草会夺走相邻灵地的水性，移植、堵泉、混种，三处手脚正好连成一线。", "旁白", ["秦风"], "红草、堵塞水沟与外露根系以真实前中后景同时入画，秦风在左侧完成视线串联，不使用魔法图示。", "layered evidence composition in the same garden: red Liyang grass foreground, blocked channel midground, disturbed roots background, Qin Feng on left connecting all three clues with his gaze; no diagrams or fantasy UI", "medium wide shot", "slow_arc_right", "reaction", "推理闭环", "秦风的视线从三处证据收回，最终看向来路，眉峰微抬", "right_to_left", "cut", "逻辑清晰，三个线索之间各停半拍，像推理而非科普朗读。"),
    (4.0, "秦风：原来如此。动手的人，手法并不高明。", "秦风", ["秦风"], "秦风半蹲在红草旁，清晰三分之四侧脸；他看向画面左侧来路，露出极淡的了然神情。", "dialogue-safe close-up of Qin Feng beside the red grass, clear masculine three-quarter face, eyes aimed to the left path, restrained knowing expression rather than a smile", "close-up", "slow_push", "dialogue", "洞悉、轻蔑", "秦风说完起身，肩线转向左后方护卫所在位置", "right_to_left", "cut", "前半句恍然，后半句淡淡轻蔑；不要得意笑声。"),
    (3.0, "秦风：秦三秋！", "秦风", ["秦风"], "秦风从红草旁起身，朝左后方抬手一次；他不拔剑，声音穿过晨雾。", "medium full-body shot of Qin Feng rising from the same red-grass plot and giving one controlled summons toward off-camera left, feet planted, no weapon drawn", "medium full shot", "static", "action", "下令", "秦风的手落回身侧，站在左侧等待来人从右侧进入", "static", "cut", "短促、有主从分寸，不怒吼；名字第二字略加重。"),
    (7.0, "秦三秋：林家大公子半个月前才把药圃交割给属下。属下每日只按吩咐灌溉，其他一概不知。", "秦三秋", ["秦风", "秦三秋"], "秦三秋应声从右侧快步进入，在秦风前方两步抱拳站定并说明交割经过；秦风左侧安静听着，背景保留离阳草与堵塞水沟。", "dialogue-safe two-shot, Qin Sanqiu enters from right, stops two steps before Qin Feng with a crisp salute and reports with a clear three-quarter face; Qin Feng listens left, red grass and blocked channel fixed behind", "medium two-shot", "slow_push", "dialogue", "应声利落，随后尊敬中带自责，前半句交代事实，‘一概不知’声音略低。", "秦三秋说完缓慢放下拳，秦风不回应，转身朝画面右侧药圃深处迈步", "left_to_right", "cut", "先用利落气息表现‘属下在’，随后尊敬中带自责；‘一概不知’声音略低。"),
    (5.0, "旁白：秦风一言不发，径直走向药圃深处。秦三秋只能紧随其后。", "旁白", ["秦风", "秦三秋"], "二人沿主路持续从左向右行走，秦风领先半步，秦三秋跟随；同一木屋逐渐退到左后方，保持空间方向。", "wide lateral tracking shot, Qin Feng leads half a step and Qin Sanqiu follows along the established main path from left to right; the same wooden shed recedes behind left", "wide full-body shot", "tracking_right", "action", "沉默施压", "二人走到更高一层灵田，秦风先停，秦三秋在其右后方停下", "left_to_right", "cut", "叙述节奏略加快，‘一言不发’留出短停顿。"),
    (5.0, "旁白：越往里走，枯萎、落叶和断流便越发严重。", "旁白", ["秦风", "秦三秋"], "前景枯叶掠过，连续受损灵田沿右侧展开；二人保持上一镜的左右位置和步速，不做蒙太奇跳位。", "wide tracking continuation through successive damaged plots, foreground dry leaves pass camera, Qin Feng remains left-front and Qin Sanqiu right-rear, both moving steadily right without position swap", "wide shot", "tracking_right", "action", "灾情加深", "一片黄叶从右上落下，秦风停步伸手接住，秦三秋随之看向前方果林", "left_to_right", "match_cut", "层层递进，‘越发严重’不拖长。"),
    (5.5, "旁白：直到重点保护的二品灵药——天罗果，也出现了大面积枯黄。", "旁白", ["秦风", "秦三秋"], "黄叶落入秦风掌心，镜头抬起显露整片天罗果林；枝叶枯黄，青涩果实落地，秦三秋在右后方脸色骤变。", "match on a yellow leaf landing in Qin Feng's palm, then reveal the protected Tianluo fruit grove with broad yellowing foliage and fallen green fruit; Qin Sanqiu reacts behind right", "medium wide shot", "tilt_up_slow", "reaction", "损失升级", "秦风低头检查掌中叶片，秦三秋上前半步，额角见汗", "static", "cut", "‘二品灵药’与‘天罗果’清晰，破折号位置自然停顿。"),
    (7.0, "秦三秋：少爷，必须马上奏报主族，请真正的炼丹师前来治理！", "秦三秋", ["秦风", "秦三秋"], "秦三秋位于右侧前景，汗珠明显但不过度，急切看向秦风；秦风左侧仍在检查落叶，冷静形成反差。", "dialogue-safe medium two-shot in the Tianluo grove, anxious Qin Sanqiu foreground right with clear face, calm Qin Feng left examining one fallen leaf, fixed morning light and grove geography", "medium shot", "slow_push", "dialogue", "惶急、担责", "秦三秋说完仍看着秦风等待命令；秦风捏住叶柄，目光转向谷口左侧", "right_to_left", "cut", "气息比平时快但保持统领的控制，‘马上’和‘真正的炼丹师’加重。"),
    (4.0, "护卫：报！林家大公子林浪，已至谷外。", "护卫", ["秦风", "秦三秋"], "一名护卫从远处左侧入画，在二人外侧停步抱拳；秦风只转动眼神，秦三秋回头。", "medium-wide shot in the same grove, one background Qin guard enters from far left and salutes; Qin Feng turns only his eyes toward the gate while Qin Sanqiu turns his head, no crowd", "medium wide shot", "static", "dialogue", "突发消息", "护卫保持抱拳，秦风的眼神定在左侧谷口，掌中黄叶不动", "right_to_left", "cut", "先有一口赶路气，再清楚禀报；不要持续大喊。"),
    (4.5, "旁白：听见林浪的名字，秦风尘封的痛苦骤然苏醒。", "旁白", ["秦风"], "秦风面部特写，掌中黄叶虚化在下方；眼神由平静转冷，画面不插入无关幻象。", "tight masculine close-up of Qin Feng, the yellow leaf blurred low in frame, his eyes cooling as buried memory returns; same side light and teal collar, no fantasy overlays", "close-up", "slow_push", "reaction", "旧恨苏醒", "秦风保持面向左侧谷口，眼神彻底冷定，呼吸恢复平稳", "right_to_left", "cut", "声音更贴近人物内心，‘林浪’和‘骤然苏醒’轻压。"),
    (6.0, "林浪：自然是来替你们解决问题的。", "林浪", ["林浪", "秦风", "秦三秋"], "谷口木门从左侧打开，林浪率四名随从踏入；他停在道路左侧，秦风和秦三秋在右侧远处，形成固定对峙轴线。", "wide confrontation at the established east gate, Lin Lang enters from screen left with four subdued male followers and stops left of the path; Qin Feng and Qin Sanqiu wait on screen right, clear opposing axis", "wide shot", "slow_push", "action", "傲慢入场", "林浪在左侧站定并示意随从停下；秦风与秦三秋保持右侧位置", "left_to_right", "cut", "带礼貌外壳的傲慢，像早已掌控局面，不要阴森。"),
    (4.0, "秦风：药圃与林家已无瓜葛。你来做什么？", "秦风", ["秦风", "林浪"], "越过林浪蓝色肩线看秦风，秦风位于右侧，清晰三分之四脸，保持原地质问。", "dialogue-safe reverse over-shoulder shot, blurred royal-blue male shoulder on left foreground, Qin Feng clear on right with calm masculine three-quarter face, established gate eyeline", "over-shoulder medium close-up", "static", "dialogue", "冷静拒绝", "秦风问完不移步，目光继续越过画面左侧对准林浪", "static", "cut", "平静而疏离，第二句短促，拒绝给对方情绪空间。"),
    (4.5, "林浪：这几年一直是本公子打理药圃。论治理，何必舍近求远？", "林浪", ["林浪", "秦风"], "正向反打林浪，他在左侧清晰，秦风只留右前景青色肩背；林浪轻抬下巴，手中没有凭空出现的道具。", "dialogue-safe over-shoulder shot, Qin Feng's blurred teal shoulder on right foreground, Lin Lang clear on left with narrow fox eyes and a restrained superior smile, empty hands visible", "over-shoulder medium close-up", "static", "dialogue", "自负、试探", "林浪说完将视线越过秦风投向枯黄果林，右手轻抬准备指示灾情", "right_to_left", "cut", "表面讲理、实则夺权；‘本公子’自然，不唱腔。"),
    (4.0, "秦三秋：秦家的产业，你们也敢擅闯！", "秦三秋", ["秦三秋", "林浪"], "秦三秋从右侧向轴线前踏半步，剑只拔出三分；林浪仍在左侧，双手、剑柄和剑身均清楚。", "medium two-shot across the same axis, Qin Sanqiu steps half a pace from screen right and draws one-third of his straight sword, Lin Lang remains left, hands and blade clearly separated", "medium shot", "static", "dialogue", "警告", "秦三秋在右侧拔剑三分后停住；林浪左侧仅微抬下巴", "static", "cut", "低音、有军伍威慑力，句尾收紧而非拖长吼叫。"),
    (7.0, "林浪：我若真走了，等主族收药时出了大事，你秦三秋跪着求我，也没用。", "林浪", ["林浪", "秦三秋", "秦风"], "林浪从左侧指向右后方枯黄天罗果，秦三秋的剑手略沉；秦风仍在右侧后景观察，不交换站位。", "medium-wide pressure shot, Lin Lang left gestures once toward the ruined Tianluo grove, Qin Sanqiu right lowers his sword hand slightly, Qin Feng remains still in right background; preserve all positions", "medium wide shot", "slow_push", "dialogue", "拿捏、威胁", "林浪收回指向果林的手，秦三秋右侧迟疑，剑仍未完全入鞘", "static", "cut", "轻笑着施压，‘跪着求我’放慢但不夸张，句尾冷冷落下。"),
    (4.0, "旁白：秦三秋顾忌天罗果，气势顿时弱了三分。", "旁白", ["秦三秋", "秦风"], "秦三秋右侧近景，握剑的手缓慢下沉，额角有汗；秦风在后方看见他的迟疑，却没有责骂。", "reaction shot, Qin Sanqiu foreground right lowers the partly drawn sword a few centimeters, sweat at temple, Qin Feng softly focused behind him observing without anger", "medium close-up", "static", "reaction", "压力与迟疑", "秦三秋保持剑身半出状态，视线短暂落向枯黄果林", "static", "cut", "克制描述，不嘲讽秦三秋；‘顾忌天罗果’是原因。"),
    (5.0, "林浪：你一个心脉受损的废人，就别在这里碍事，回去抱你的药罐子吧。", "林浪", ["林浪", "秦风"], "林浪把视线从秦三秋转向秦风；越过秦风青色肩线，林浪狭长眼中带讥诮，嘴部无遮挡。", "dialogue-safe over Qin Feng's teal shoulder, Lin Lang clear on left turns only his gaze toward Qin Feng, contempt in narrow fox eyes, unobstructed mouth, royal-blue robe stable", "over-shoulder close-up", "static", "dialogue", "恶意刺激", "林浪说完维持轻蔑笑意，目光牢牢锁住画面右侧秦风", "static", "cut", "恶毒但仍是贵公子口吻，‘废人’和‘药罐子’加重，不咆哮。"),
    (5.5, "秦风：我的心脉，正是你的好妹妹、我名义上的未婚妻，亲手所伤。", "秦风", ["秦风", "林浪"], "反打秦风位于右侧，林浪仅留左前景蓝色肩背；秦风放下护胸的手，眼神有怒意却站得很稳。", "dialogue-safe reverse over-shoulder, Lin Lang's blurred blue back left foreground, Qin Feng clear right lowers his hand from his chest and meets the eyeline, controlled anger", "over-shoulder medium close-up", "slow_push", "dialogue", "压住怒火", "秦风的手完全落回身侧，目光与林浪保持同一高度，身体不前冲", "static", "cut", "每个关系词都说清，怒意压在声音下面；‘亲手所伤’冷硬收尾。"),
    (7.0, "林浪：那又如何？武道大比，生死各安天命。你受伤，只怪技不如人！", "林浪", ["林浪"], "林浪单人中近景，下巴微昂，摊开右手后向下轻压；保持年轻俊美，不生成苍老或凶恶面孔。", "dialogue-safe solo medium close-up of young handsome Lin Lang, masculine aristocratic face clear, chin slightly raised, one dismissive palm opens then presses down, same gate background", "medium close-up", "static", "dialogue", "无耻、理直气壮", "林浪右手压回腰侧，下巴仍高，嘴角保留极淡笑意", "static", "cut", "‘那又如何’轻而冷，后半理直气壮；不要变成愤怒喊叫。"),
    (4.0, "旁白：四周护卫神色怪异。连他们也没想到，林浪竟能无耻至此。", "旁白", ["秦三秋"], "秦三秋与两名背景护卫交换短暂目光，表情克制；所有人仍在原站位，不形成喧闹人群。", "restrained reaction shot of Qin Sanqiu and two background male guards exchanging brief disbelieving looks, same right-side formation, no crowd movement", "medium reaction shot", "static", "reaction", "鄙夷、错愕", "秦三秋重新看向林浪，握剑手恢复稳定，背景护卫收回目光", "static", "dissolve", "像替观众点破无耻，语气冷，不做喜剧吐槽。"),
    (6.0, "旁白：当日擂台上，秦风明明在最后关头收了剑，林淑婉却趁隙刺穿了他的心脉。", "旁白", ["秦风", "林淑婉"], "再次闪回同一月夜擂台和同一站位：秦风先收剑，林淑婉从右侧完成短促突刺；与第三镜身份、服装、光线完全一致。", "same moonlit academy arena and locked identities as the earlier flashback; Qin Feng left visibly lowers his sword in mercy, Lin Shuwan right uses the opening for one short thrust; no gore, no embrace", "medium wide shot", "slow_push", "flashback", "真相回放", "秦风收剑的手停在身侧，林淑婉剑锋到达胸前，秦风向左后失去重心", "right_to_left", "dissolve", "先强调‘收了剑’，再冷静落到背叛结果；不煽情哭诉。"),
    (5.0, "旁白：可这一次，十万年的心境让他没有再中激将之计。", "旁白", ["秦风"], "从闪回胸口位置匹配回药圃，秦风单人近景；他缓慢吸气，眼中怒意沉入平静。", "match back to the herb garden on Qin Feng's chest position, clear masculine close-up, one slow breath settles anger into vast calm, teal collar and morning light unchanged", "close-up", "slow_pull", "reaction", "古老心境、克制", "秦风呼吸归稳，视线平静越过左侧林浪，嘴角没有笑", "static", "cut", "‘这一次’轻顿，‘十万年的心境’不故作玄虚，收束平稳。"),
    (7.0, "林浪：小妹已得月摇仙宫长老看重，日后成就不可限量。你们的婚约，就不要再提了。", "林浪", ["林浪", "秦风"], "林浪左、秦风右，二人隔着灵泉沟对峙；林浪微向前压，秦风不退，保持完整左右视线。", "profile two-shot across the established irrigation channel, Lin Lang left leans forward slightly while Qin Feng right remains still, opposing eyelines and costume colors clear", "medium profile two-shot", "static", "dialogue", "炫耀、退婚施压", "林浪说完站直并等待反应；秦风右侧只缓慢眨眼一次，位置不变", "static", "cut", "先炫耀前途，再把退婚说得轻描淡写；尾句带刻意羞辱。"),
    (7.0, "秦风：擂台之事，自有秦家长老公断。药圃已经收回，你从哪里来，便回哪里去。", "秦风", ["秦风", "林浪"], "反打秦风右侧中近景，林浪仅作左侧蓝色肩背；秦风声线平静，先垂眼看一瞬灵泉，再抬眼送客。", "dialogue-safe reverse over-shoulder, Qin Feng clear on right, Lin Lang only blurred blue shoulder left; Qin Feng briefly glances at the channel then raises calm eyes to dismiss him", "over-shoulder medium close-up", "slow_push", "dialogue", "从容反制", "秦风抬起右手，掌心朝向左侧谷口，送客手势停稳", "right_to_left", "cut", "不像少年争吵，像强者宣告边界；两句层层推进，‘回哪里去’干脆。"),
    (5.0, "秦风：再敢踏入半步，便请秦家执法队处置！", "秦风", ["秦风", "秦三秋", "林浪"], "中广景恢复双方站位，秦风右侧送客手势指向左侧谷口；秦三秋与护卫同步收紧阵形，林浪仍在左。", "heroic medium-wide confrontation, Qin Feng right holds one clear dismissal gesture toward the gate on left, Qin Sanqiu and guards tighten a controlled semicircle, Lin Lang remains left", "medium wide shot", "slow_push", "dialogue", "威严落锤", "秦风手势停在谷口方向；护卫包围完成但无人挥剑，林浪身体轻微一僵", "right_to_left", "cut", "‘执法队’清晰压重音，声音不大却有不容置疑的力度。"),
    (5.0, "旁白：锵然声中，秦家护卫齐齐拔剑。林浪的脸色终于变了。", "旁白", ["秦三秋", "林浪"], "沿秦三秋拔剑动作切入，数柄剑只完成一次整齐出鞘；林浪左侧后退半步，随从不乱跑不穿帮。", "cut-on-action medium wide shot, Qin Sanqiu and Qin guards complete one synchronized sword draw on right, Lin Lang left recoils half a step, followers stay grouped behind him", "medium wide shot", "static", "impact", "局势逆转", "所有剑锋停在安全警戒位，林浪左脚后撤落地并重新站稳", "static", "cut", "先让‘锵然声’有画面感，后句降低音量突出林浪失势。"),
    (7.0, "林浪：你们父子难道要为一己之私，葬送整座药圃，牵累云影镇吗？", "林浪", ["林浪", "秦风"], "林浪稳住后指向枯黄天罗果，试图反客为主；秦风在右侧保持送客后的站位，护卫只作虚化背景。", "dialogue-safe medium two-shot, Lin Lang left regains composure and points once toward the ruined grove, Qin Feng right remains calm, guards blurred and still behind", "medium shot", "slow_push", "dialogue", "道德绑架、最后施压", "林浪收回手，向前探身半步盯住秦风；秦风没有退让", "left_to_right", "cut", "带急切但仍维持体面，把指责说成大道理；避免歇斯底里。"),
    (4.0, "秦风：无知。药圃这点小事，也想难住本少爷？", "秦风", ["秦风"], "秦风单人中近景，手中黄叶轻落回地面；他看向左侧林浪，神情像故意露出一点少年傲气。", "dialogue-safe solo medium close-up of Qin Feng, one yellow leaf slips from his fingers, clear masculine face turns slightly toward off-camera left with deliberate youthful disdain", "medium close-up", "static", "dialogue", "故意示弱、轻蔑", "黄叶落地，秦风保持视线，嘴角出现极淡的不屑", "static", "cut", "‘无知’很轻；后句故意带一点少年意气，实则在下套。"),
    (6.0, "林浪：大言不惭！离主族收药已不到一个月，你从哪里请得来丹师？", "林浪", ["林浪"], "林浪单人中近景，听见秦风上钩后眼神发亮，随即把兴奋藏进嘲讽；手势克制。", "dialogue-safe solo medium close-up of Lin Lang, a brief spark of excitement in his fox-shaped eyes before he masks it as contempt, one restrained open-hand challenge", "medium close-up", "slow_push", "dialogue", "窃喜、激将", "林浪手掌停在胸前，注视右侧秦风，等待他继续逞强", "static", "cut", "开头斥责，后半故意逼问；眼底窃喜但声音不要提前泄露阴谋。"),
    (5.0, "秦风：书院自有炼药之法。指挥护卫用灵泉灌溉，我还是会的。", "秦风", ["秦风", "秦三秋"], "秦风右侧平静作答，秦三秋在后方露出担忧；秦风语气刻意显得经验不足，眼神却很清醒。", "dialogue-safe medium shot, Qin Feng foreground right answers with deliberate youthful confidence, Qin Sanqiu behind him looks concerned; Qin Feng's eyes remain quietly calculating", "medium shot", "static", "dialogue", "佯装无知、引敌入局", "秦风说完看向枯黄果林，给林浪留下主动提出赌约的空隙", "right_to_left", "cut", "表面认真、略显少年轻率，内里平稳；不能演成真愚蠢。"),
    (8.0, "林浪：你若完成今年的灵药收缴，我便把林家铁矿拱手相让；若完不成，药圃未来三年归我管理。你可敢赌？", "林浪", ["林浪", "秦风"], "林浪左侧提出条件，秦风右侧安静听完；两人之间留出石桌位置，背景护卫保持不动，谈判轴线明确。", "tense bargaining two-shot, Lin Lang left presents the wager with one measured gesture, Qin Feng right listens without interruption, empty stone table between them, ruined grove behind", "medium two-shot", "slow_push", "dialogue", "贪念、笃定", "林浪最后向石桌方向伸出手，秦风目光从他的手移到脸上，准备答复", "left_to_right", "cut", "条件分两层说清楚，中间停顿；‘铁矿’与‘三年’加重，‘敢赌’带胜券在握。"),
    (6.0, "秦风：当真？好。林家铁矿，我要定了。", "秦风", ["秦风", "林浪"], "石桌中近景，赌约纸、笔和印泥摆放清楚但无可读文字；秦风右侧按稳纸张落下指印，林浪左侧刚收笔。", "dialogue-safe over-shoulder medium close-up at the stone table, Qin Feng clear on right presses one fingerprint onto an unreadable wager sheet, Lin Lang only blue shoulder and writing hand left, no legible text", "over-shoulder medium close-up", "static", "dialogue", "收网、笃定", "秦风抬起按印手指，赌约留在桌面；林浪收笔并转身朝左侧谷口", "right_to_left", "dissolve", "‘当真’像最后确认猎物入局；‘我要定了’低沉、肯定，不喊。"),
    (7.0, "秦风：林家铁矿，便是我回报你们兄妹的第一份大礼。", "秦风", ["秦风", "林浪"], "林浪只以远处皇家蓝背影向左离谷，秦风留在右侧前景，清晰三分之四脸；他望着背影露出克制冷笑。", "final dialogue-safe medium close-up, Qin Feng clear right foreground with a restrained knowing smile, Lin Lang only a small distant royal-blue male back walking left toward the gate, no visible face, no crowd", "medium close-up", "slow_pull", "dialogue", "复仇序幕、冷静胜券", "林浪背影继续远离至谷口，秦风不移步，笑意收住后画面稳定停留", "right_to_left", "fade_black", "像只说给自己听，声音轻而冷；‘第一份大礼’放慢半拍，余味收束。"),
]


def audio_payload(speaker: str, text: str, performance_note: str) -> dict[str, object]:
    voice_id, profile_id, rate, pitch = VOICE_CONFIG[speaker]
    spoken = text.split("：", 1)[1] if "：" in text else text
    mode = "auto_narration" if speaker == "旁白" else "dialogue"
    return {
        "enabled": True,
        "mode": mode,
        "speaker": speaker,
        "text": spoken,
        "voice_id": voice_id,
        "engine": "edge_tts",
        "reference_audio": "",
        "reference_text": "",
        "instruct_text": f"{VOICE_BASE[speaker]} 本句表演：{performance_note}",
        "fallback_to_edge": True,
        "rate": rate,
        "volume": "+0%",
        "pitch": pitch,
        "subtitle_enabled": True,
        "preserve_source_audio": True,
        "source_audio_gain_db": -6.0,
        "ducking_gain_db": -12.0,
        "voice_profile_id": profile_id,
        "voice_assignment_mode": "auto",
        "audio_file": "",
        "subtitle_file": "",
        "manifest_file": "",
        "estimated_duration_seconds": 0.0,
        "planned_timeline_duration_seconds": 0.0,
        "timing_status": "unplanned",
        "recommended_segments": 1,
        "segments": [],
    }


def image_generation_payload() -> dict[str, object]:
    return {
        "selected_image": "",
        "selected_source": "",
        "manifest": "",
        "candidates": [],
        "qc_status": "pending",
        "qc_note": "",
        "qc_checked_at": "",
        "qc_selected_image": "",
    }


def lip_sync_payload(speaker: str) -> dict[str, object]:
    enabled = speaker in {"秦风", "林浪", "秦三秋"}
    return {
        "enabled": enabled,
        "engine": "latentsync_1_6",
        "target_character": speaker if enabled else "",
        "mode": "speaker_tracking",
        "status": "pending" if enabled else "disabled",
        "source_video": "",
        "audio_file": "",
        "output_file": "",
        "previous_output_file": "",
        "manifest_file": "",
        "generated_at": "",
        "elapsed_seconds": 0.0,
        "error": "",
        "sync_score": 0.0,
    }


def main() -> None:
    episode = json.loads(EPISODE_PATH.read_text(encoding="utf-8-sig"))
    new_shots: list[dict[str, object]] = []
    last_exit_by_group: dict[str, str] = {}
    last_number_by_group: dict[str, int] = {}
    last_cast_by_group: dict[str, str] = {}

    for number, raw in enumerate(SHOTS, start=1):
        (
            duration,
            dialogue,
            speaker,
            cast,
            scene,
            image_detail,
            camera,
            camera_movement,
            beat,
            emotion,
            exit_state,
            screen_direction,
            transition_out,
            performance_note,
        ) = raw
        group_id = "academy_flashback" if beat == "flashback" else "garden_present"
        if group_id == "academy_flashback":
            entry_state = (
                "同一月夜书院擂台：秦风在左、林淑婉在右，冷色月光，剑与双手清晰"
            )
        else:
            entry_state = last_exit_by_group.get(
                group_id,
                "清晨秦家药圃，固定东门、土径、灵泉沟、木屋与右后方主光方向",
            )
        cast_signature = "|".join(cast)
        previous_number = last_number_by_group.get(group_id, 0)
        previous_cast = last_cast_by_group.get(group_id, "")
        reference_allowed = bool(previous_number and cast_signature == previous_cast and cast_signature)
        bridge = (
            f"开头先稳定保持“{entry_state}”约0.4秒；全镜只完成一个主要动作，"
            f"结束于“{exit_state}”，随后稳定停留约0.6秒作为剪辑余量。动作不中途反向，"
            "人物不跳位、不跨180度轴线，切点前不眨眼、不转头、不遮脸。"
        )
        keyframe = (
            "START KEYFRAME FOR A CONTINUOUS EDITABLE SHOT. Preserve the locked three-view cast "
            "identity, exact costume construction, established location geography, light direction, "
            f"eyeline and screen axis. Entry state: {entry_state}. Frame the anticipatory instant "
            f"before this single action completes: {exit_state}. Use natural asymmetrical body "
            "weight, readable hands and open lead room. No centered lineup, no completed final pose, "
            "no action overlap and no unrelated background performance."
        )
        transition_strategy = (
            "dissolve"
            if transition_out == "dissolve"
            else "fade_black"
            if transition_out == "fade_black"
            else "eyeline_cut"
            if beat == "dialogue"
            else "cut_on_action"
            if beat in {"action", "impact"}
            else "match_cut"
            if transition_out == "match_cut"
            else "cut"
        )
        continuity = (
            "Lock every named actor to the approved three-view sheet: identical facial geometry, "
            "age, body proportions, hair, costume layers, colors and accessories. Preserve the "
            "established garden landmarks, right-rear morning key light, left/right positions, "
            f"eyeline and {screen_direction} screen direction. Begin from: {entry_state}. "
            f"Settle at: {exit_state}."
        )
        motion = (
            f"先稳定0.4秒承接上一镜状态：{entry_state}。随后只完成这一动作：{exit_state}。"
            "动作幅度克制、速度真实、重心与脚底接触可信；镜头末尾稳定0.6秒，供下一镜匹配剪辑。"
            "不得追加第二动作，不突然转身，不跨轴，不改变人物脸、发型、服装、道具、光线和背景地标。"
        )
        end_prompt = (
            "END KEYFRAME OF THE SAME UNBROKEN SHOT. The one requested action is complete and the "
            f"actors have settled naturally: {exit_state}. Preserve the exact same cast identities "
            "from the three-view sheets, wardrobe, props, location, light direction, lens, camera "
            "height, composition, eyelines and screen axis as the start frame. Show a physically "
            "reachable balanced pose with correct hands and grounded feet. No new action, no new "
            "person, no teleportation, no cut and no identity or costume change."
        )
        image_prompt = f"{STYLE}; {image_detail}; {IMAGE_GUARD}"
        characters = [
            {
                "name": name,
                "appearance": APPEARANCE[name],
                "clothing": CLOTHING[name],
                "pose": exit_state[:200],
                "expression": emotion[:150],
            }
            for name in cast
        ]
        new_shots.append(
            {
                "shot_number": number,
                "scene_description": scene,
                "environment": {
                    "layout": "云影镇秦家药圃；东门在画面左侧，主土径向右深入，灵泉沟沿路延伸，木屋居右后方；闪回镜头除外",
                    "lighting": "现实线为清晨柔和侧光，固定从画面右后方照入；闪回为冷色月光",
                    "color_palette": "现实线以青、棕、枯黄为主，离阳草作克制红色线索；闪回为冷蓝与白",
                    "atmosphere": "现实线仅低处薄雾与叶片微动；闪回仅有稀薄尘屑，不使用夸张法术粒子",
                },
                "characters": characters,
                "camera_angle": camera,
                "camera_movement": camera_movement,
                "emotion": emotion,
                "dialogue": dialogue,
                "sound_effect": "",
                "duration_seconds": duration,
                "transition": "fade" if transition_out == "fade_black" else transition_out,
                "image_prompt": image_prompt[:600],
                "style_preset": "真人电影",
                "image_generation": image_generation_payload(),
                "video_generation": {
                    "engine_profile": "minimax_h3_fl2va",
                    "subject_motion": exit_state,
                    "environment_motion": "低处晨雾和近处叶片持续同方向轻微运动，幅度恒定；背景建筑、山体和远景人物保持稳定",
                    "continuity_constraints": continuity[:1600],
                    "negative_prompt": VIDEO_NEGATIVE,
                    "motion_prompt": motion[:1600],
                    "end_frame_prompt": end_prompt[:2400],
                    "end_frame_prompt_version": 6,
                    "routing_reason": "按重写后的单动作镜头与首尾稳定帧生成，优先保证身份、轴线和剪辑余量",
                    "routing_version": 5,
                    "native_audio_mode": "ambience_sfx_music",
                    "dialogue_prompt": dialogue,
                    "sound_effect_prompt": "延续全片同一清晨药圃底噪：微风、叶片、细弱水流与真实衣料声；只为画面可见动作增加一次对应声音，不做夸张撞击，不在切点突然静音或重置环境声。",
                    "music_prompt": "不生成独立旋律、鼓点、片头或片尾式收束；仅保留极轻、连续、无歌词的低频氛围底色，为后期整集统一配乐和对白留出空间，镜头切点前后响度与音色一致。",
                    "camera_movement": camera_movement,
                    "motion_strength": "medium" if beat in {"action", "impact", "flashback"} else "low",
                    "screen_direction": screen_direction,
                    "transition_out": transition_out,
                    "transition_frames": 12 if transition_out == "dissolve" else 10 if transition_out == "fade_black" else 8,
                    "handle_frames": 12,
                    "candidate_count": 1,
                    "duration_seconds": duration,
                    "source_image": "",
                    "end_image": "",
                    "selected_video": "",
                    "manifest_file": "",
                },
                "continuity_plan": {
                    "group_id": group_id,
                    "beat_type": beat if beat != "impact" else "action",
                    "action_phase": "impact" if beat == "impact" else "interaction" if beat == "dialogue" else "reaction" if beat == "reaction" else "setup" if beat == "establish" else "anticipation",
                    "entry_state": entry_state[:600],
                    "exit_state": exit_state[:600],
                    "match_anchor": continuity[:800],
                    "cast_signature": cast_signature,
                    "reference_mode": "previous_in_group" if reference_allowed else "none",
                    "reference_shot_number": previous_number if reference_allowed else 0,
                    "reference_denoise": 0.82 if beat in {"dialogue", "reaction"} else 0.86,
                    "transition_strategy": transition_strategy,
                    "match_action": f"上一镜稳定落点“{entry_state}”直接承接本镜，完成“{exit_state}”后再切。"[:500],
                    "eyeline": "对话双方始终沿既定左右视线相望；单人镜头保持看向上一镜对手所在的画外方向",
                    "screen_axis": "药圃现实线固定东门在左、深处在右；秦风一方主要在右、林浪一方在左，未经建立镜头不得跨轴",
                    "bridge_prompt": bridge[:600],
                    "keyframe_prompt": keyframe[:1600],
                },
                "audio_generation": audio_payload(speaker, dialogue, performance_note),
                "lip_sync": lip_sync_payload(speaker),
            }
        )
        last_exit_by_group[group_id] = exit_state
        last_number_by_group[group_id] = number
        last_cast_by_group[group_id] = cast_signature

    episode["episode_title"] = "重生十万年"
    episode["character_profiles"] = PROFILES
    episode["character_visual_fingerprints"] = {
        name: f"{APPEARANCE[name]}；{CLOTHING[name]}；正面、严格左侧面、背面三视图身份必须完全一致"
        for name in PROFILES
    }
    episode["character_styles"] = {name: "真人电影" for name in PROFILES}
    episode["character_generation_presets"] = {
        name: "turnaround_no_bg" for name in PROFILES
    }
    episode["shots"] = new_shots
    episode["summary"] = (
        "约四分五十秒短剧：重生归来的秦风带伤进入秦家药圃，凭十万年炼丹阅历查出移植、堵泉与离阳草混种的连环破坏。"
        "林浪闯入夺权，又以旧伤和退婚羞辱刺激秦风；秦风稳住心境，借秦家执法队反制，顺势诱其立下铁矿赌约，开启复仇第一局。"
    )
    episode.pop("dubbing", None)
    optimize_episode_audio_timing(
        episode,
        minimum_episode_seconds=180.0,
        preferred_max_shot_seconds=8.0,
        hard_max_shot_seconds=15.0,
    )
    EPISODE_PATH.write_text(
        json.dumps(episode, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    character_data = json.loads(CHARACTER_PATH.read_text(encoding="utf-8-sig"))
    for character in character_data.get("characters", []):
        name = str(character.get("name") or "")
        if name in PROFILES:
            character["profile"] = PROFILES[name]
    character_data["updated_at"] = "2026-08-25T00:00:00+08:00"
    CHARACTER_PATH.write_text(
        json.dumps(character_data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
