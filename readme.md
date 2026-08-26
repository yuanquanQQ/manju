AI漫剧生成系统（V1）开发方案

> **从这里开始：**[项目启动说明](docs/00-项目启动说明.md)  
> 包含日常启动、首次安装、3090 连接、完整制作顺序、输出目录和故障排查。

> 完善后的分阶段计划见：[AI 漫剧生成系统 V1：开发计划总览](docs/开发计划/README.md)。十个开发阶段均已拆分为独立 Markdown 文档。

桌面应用

本地简约桌面制作界面已经提供项目总览、角色定妆、声音角色库、本地资源包、分镜浏览、
镜头视频生成、整集无声预览合成、逐镜头配音、字幕烧录、带声成片、任务日志
和 GPU 服务器控制。

“本地资源包”可一键整理并打开人物、场景、人声和合成内容目录。用户可交付文件
统一保存在 `outputs/episodes/<项目拼音_集号>/`，视频、字幕、逐镜头音频、生成
清单和质检图分目录保存；文件名只使用拼音、数字和下划线。

```powershell
.\.venv\Scripts\python.exe main.py gui
```

也可以双击 `start_gui.bat`。详细操作见：[桌面应用使用指南](docs/02-桌面应用使用指南.md)。
声音克隆、合成音色和对外发布前的检查见：
[声音版权与授权说明](docs/03-声音版权与授权说明.md)。

## 源码交付、安装与补充依赖

源码压缩包不包含 Python 虚拟环境、本地大模型、生成素材、项目数据、日志和任何
密码。解压后按以下步骤在新电脑安装；推荐使用 **Python 3.11 或 3.12（64 位）**。

```powershell
# 1. 打开 PowerShell，进入解压后的项目目录
cd <解压后的项目目录>

# 2. 创建独立 Python 环境
py -3.11 -m venv .venv

# 3. 升级安装工具并安装全部运行依赖
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -e .

# 4. （可选）安装测试与代码检查工具
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"

# 5. 创建本机配置文件
Copy-Item .env.example .env

# 6. 启动桌面应用
.\start_gui.bat
```

如果没有 `py -3.11`，请先从 [Python 官网](https://www.python.org/downloads/)
安装 Python 3.11/3.12，并在安装界面勾选“Add Python to PATH”；随后将上述命令中的
`py -3.11` 改为 `python`。首次安装 `llama-cpp-python` 可能需要数分钟。

### 补充或更新依赖

新增 Python 第三方库时，请同时把它写入 `requirements.txt`；若这是应用运行时必需的
库，也要同步写入 `pyproject.toml` 的 `project.dependencies`，然后在本机执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -e .
```

若要让其他人严格复现当前 Windows + Python 3.12 环境，优先使用锁定文件：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install -e .
```

`requirements.lock` 仅适用于其文件开头标注的平台与 Python 版本；在其他 Python
版本或操作系统上，请改用 `requirements.txt`。更新依赖版本后，应在目标环境重新生成并
验证锁定文件，再将新的 `requirements.lock` 一并提交。

`.env` 中的 `LLM_MODEL_PATH` 与 `LOCAL_AI_ROOT` 必须改成新电脑的实际路径。小说
自动处理还需要自行准备 Qwen GGUF 文本模型；本机生图/视频模型也需自行下载。
若使用远程 GPU，只需在应用的“连接设置”填写服务器地址、端口、用户名和密码，
密码不要写入 `.env` 或发送给他人。远程模型部署方式见
[项目启动说明](docs/00-项目启动说明.md)。

可用下面命令验证安装（可选）：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

自动化制作链路

小说处理完成后，应用会为每个镜头同时准备生图提示词、人物动作、环境动作、
连续性约束、负面提示词、运镜、时长和转场。默认勾选“分镜完成后自动生成
缺失首帧并回填到视频任务”，GPU 与 ComfyUI 可用时会直接批量生图并写回
对应镜头；条件不满足时保留分镜并明确显示“待自动生成”。

“分镜脚本”页提供“自动补全缺失画面”，用于已有项目或失败后的断点补全；
“重做连续首帧”会把整集拆成连续组，保存入镜/出镜状态、动作阶段、匹配锚点
和参考镜头。同一场景且出场人物集合相同的镜头会按顺序使用上一镜头做
图生图承接；人物阵容变化、闪回或换场时自动断开图像引用，防止错误人物累积。
首帧默认取主要动作发生前一刻，避免正面对称站桩和定妆照式构图。

角色图或分镜首帧局部效果不好时，点击图片上的“不满意 · 修改此图”。应用会
使用 FLUX.1 Kontext 读取原图，根据“问题与修改要求”、目标提示词和明确排除项
生成 1～4 个候选；可选择严格保留、平衡修改或较大调整。只有点击“使用这张”
才会切换当前图，原图、其他候选、模型、时间、种子和修改说明都会保留。分镜页
可用“历史版本”随时比较或切回。完整说明见：
[图片不满意时的修改功能](docs/04-图片不满意时的修改功能.md)。

“视频生成”页默认开启“按镜头动作自动选择模型”：表情、呼吸、视线和小幅手势
使用 Wan2.2 TI2V 5B；行走、后退、起身、拔剑和多人位移等具有明确动作终点的
镜头使用 Wan2.2 FLF2V 14B。FLF2V 缺少结束帧时，应用会先从起始帧自动生成
两个结束关键帧候选，通过 SSIM 连续性检查后自动绑定，再继续视频生成；过度漂移
或几乎冻结的候选只保留供检查，不会进入成片。“漫画动效”仅用于静态推拉预览。

FLF2V 使用官方高噪声、低噪声两阶段 14B FP8 工作流。可在“连接与设置”点击
“安装/修复 Wan FLF2V”，或运行 `scripts/gpu/wan22_flf2v/install.sh`。安装器固定
支持 `HF_ENDPOINT=https://hf-mirror.com`、断点续传、文件大小校验，并强制保留
至少 12GiB 服务器空间；不会自动删除 H3 或其他用途不确定的模型。

动作结束帧使用专门的 `FLUX.1 Kontext Dev FP8` 参考图编辑器，不再用 Krea 或
Juggernaut 的普通 img2img 冒充姿态编辑。可在“连接与设置”点击“安装/修复
FLUX.1 Kontext”，或运行 `scripts/gpu/flux_kontext/install.sh`；模型位于
`/root/autodl-tmp/ComfyUI/models/diffusion_models/flux1-dev-kontext_fp8_scaled.safetensors`
（精确大小 11,904,640,136 字节）。自动尾帧会优先执行明确的动作姿态变化、输出回
视频目标分辨率，并同时检查细节差异与低频构图一致性；清单会标注
`model_id=flux_kontext`、模型文件名和生成时间。

配音与字幕

左侧“声音角色库”是独立的选角与音色管理界面：

- “声音库”页签可导入本人、已获授权或原创合成的参考声音，保存逐字参考台词、
  年龄感、性别、气质、音高、语速、标签和默认表演指令；
- “人物自动选声”会读取各集人物资料、出场描述和对白说话人，推断人物特征并
  推荐声音；任意人物均可手动改配，手动结果会锁定，不被下一次自动匹配覆盖；
- “应用到全部分镜”才会把选择写入镜头配音参数。若声音发生变化，应用会保留
  旧口型结果作为历史文件、恢复干净的 Wan 源视频，并把相关口型任务标记为待重做；
- 共用声音库存放在 `projects/_voice_library/`，项目内的分配表存放在
  `projects/<项目名>/production/voice_assignments.json`。

为避免冒充和未授权使用，导入克隆音色前必须声明声音属于本人、已获明确授权
或为原创合成音色，并确认用途。建议使用 3–15 秒、无音乐、无混响、单人清晰
语音，且参考台词必须与录音逐字一致。

“视频生成”页的“配音与字幕”标签支持逐镜头设置：

- 自动旁白、角色对白/自定义文案、静音三种模式；
- Edge 中文音色或 CosyVoice 3 本地角色音色、语速和字幕开关；
- CosyVoice 参考音频、逐字参考台词、情绪/表演指令和单句试听；
- 同名说话人在一批镜头中自动复用同一角色音色；
- 未提供参考音频时自动创建基础音色，服务器异常时可回退 Edge TTS；
- 文案留空时自动使用镜头画面描述；
- 生成 MP3、逐镜头 SRT、整集 SRT 和带 AAC 音轨的 MP4；
- 视频生成前可点击“按配音规划时长”，无需 GPU 即可估算整集时长、标记长对白
  拆镜，并把目标时长写回下一次视频任务；
- 历史视频短于语音时不再直接冻结末帧，而是将已有动作平滑铺满时间线；真实
  语音时长同时回写为“需重生成/需拆镜”，供下一轮生成正确长度的视频；
- 输出前统一到 1280×720、24fps、48kHz 双声道，并进行响度标准化；
- 中文字幕直接烧录进画面，普通播放器无需额外加载字幕文件。

Edge TTS 需要联网但不需要 API Key。高质量本地引擎使用
`Fun-CosyVoice3-0.5B-2512`，在 RTX 3090 上进行中文零样本音色克隆；服务只监听
服务器 `127.0.0.1:50000`，桌面应用通过 SSH 上传参考音频、提交文案并下载 WAV。

服务器目录：

```text
/root/cosyvoice-runtime/CosyVoice                 # 官方推理代码
/root/cosyvoice-env                               # 独立 Python 3.10 环境
/root/cosyvoice-models/Fun-CosyVoice3-0.5B        # 推理必需模型
/root/cosyvoice-service                           # HTTP 包装、日志与启停脚本
```

可复用部署文件位于 `scripts/gpu/cosyvoice/`。模型下载脚本固定读取
`HF_ENDPOINT=https://hf-mirror.com`，只下载单卡推理必需文件，不下载 RL、
批处理 tokenizer 和 TensorRT 权重。开始生图或 Wan 视频任务前，应用会自动停止
CosyVoice 释放显存；需要配音时再按需启动。

每个对白镜头会保存独立的口型任务参数，包括 LatentSync 版本、目标人物和
目标脸模式。“连接与设置”可以检测或安装官方 LatentSync 1.6；模型就绪后，
“配音与字幕 → 生成当前镜头口型”会自动生成当前对白音频、释放 ComfyUI/
CosyVoice 显存、执行口型推理、下载结果并选为当前镜头视频。多人镜头的
“按说话人自动跟踪”会读取已锁定定妆照，通过 InsightFace 身份向量选择目标脸；
匹配分数低于安全阈值时直接停止，不回退到面积最大的人脸。“批量生成整集口型”
会跳过旁白和已完成镜头，列出缺少视频、定妆照或画面目标人物的阻塞项，并支持
失败后从未完成镜头续跑。

LatentSync 服务器目录：

```text
/root/autodl-tmp/LatentSync          # 官方代码和 1.6 权重
/root/autodl-tmp/latentsync-env      # 独立 Python 3.10/CUDA 环境
/root/autodl-tmp/huggingface         # 持久化 VAE/HF 缓存
```

可复用安装与运行脚本位于 `scripts/gpu/latentsync/`，统一使用
`HF_ENDPOINT=https://hf-mirror.com`。

锁定定妆照会真正进入分镜生图流程，而不再只保存在界面状态中。服务器端的
SDXL 人脸身份参考使用 IP-Adapter Plus Face，文件位于：

```text
/root/autodl-tmp/ComfyUI/models/clip_vision/CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors
/root/autodl-tmp/ComfyUI/models/ipadapter/ip-adapter-plus-face_sdxl_vit-h.safetensors
```

可在“连接与设置”点击“安装人脸身份参考”，或运行
`scripts/gpu/ipadapter/install.sh`。脚本使用 HF 镜像、断点续传和 SHA-256
校验。实测 IP-Adapter Plus Face 会把过于柔美的男性参考放大成女性特征，因此
男性角色默认使用经过验证的性别、五官和服装文本身份锁；女性单人镜头才启用
SDXL 身份适配。Flux Krea 当前使用文本身份锁，避免普通图生图复制定妆照构图。

“连接与设置”中的“本地生成模型中心”会检测本机 GPU、显存、磁盘、ComfyUI、
模型文件和必要节点。未填写服务器密码时，角色定妆会尝试调用本机 ComfyUI；
模型未安装或显存不足时会显示明确原因，不会把“已下载”误报为“可调用”。

安装或更新依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

也可以从命令行重新生成某一集的配音成片：

```powershell
.\.venv\Scripts\python.exe -m scripts.generate_episode_dubbing --project jueshi --episode 1
```

目标

输入一本长篇小说（TXT/Markdown）

输出一部完整的漫剧（图片 + 视频 + 字幕 + 配音）

全程本地运行（RTX3090）

开发语言：Python

第一阶段：项目框架（Project Foundation）
目标

建立整个工程框架。

这一阶段不需要AI生成任何内容。

技术
Python 3.11
Conda
Pydantic
Typer（CLI）
Loguru
SQLite
SQLAlchemy
Poetry/uv
目录
novel2anime/

    app/

        core/
        agents/
        compiler/
        database/
        renderer/
        pipeline/

    models/

    workflows/

    projects/

    tests/

    docs/
输出

能够执行：

python main.py

能够创建一个新项目：

python main.py create my_project

输出：

projects/

    my_project/

        novel/

        database/

        assets/

        outputs/
验收

✅ 可以创建项目

✅ 可以读取配置

✅ SQLite初始化成功

第二阶段：Novel Compiler（小说编译器）

这是整个项目最重要的一步。

输入
chapter001.md

chapter002.md

...

chapter300.md
工作

逐章读取。

每章执行：

读取章节

↓

LLM解析

↓

输出JSON

↓

数据库更新
输出

例如：

{
    "chapter":35,

    "new_character":[...],

    "new_scene":[...],

    "new_event":[...],

    "summary":"..."
}
技术

Python

建议：

Instructor

OpenAI SDK

Pydantic Output Parser

全部强制JSON输出。

数据库存储

SQLite

例如：

characters

scenes

events

chapters

relations
验收

例如：

输入：

100章小说

输出：

数据库：

人物

57个

地点

32个

事件

845个

章节摘要

100条

抽查：

随机检查：

第58章

人物是否正确

地点是否正确

摘要是否正确

准确率达到90%以上。

第三阶段：知识数据库（Knowledge Base）

这里不再解析小说。

而是建立整个世界。

输出
world.json

characters.json

timeline.json

scene.json
技术

SQLite

JSON Cache

FAISS

为什么需要FAISS？

以后：

例如：

林凡第一次遇见小医仙？

直接：

Embedding

↓

检索

↓

找到章节

不用LLM全局搜索。

验收

输入：

林凡什么时候获得异火？

系统：

5秒内定位。

第四阶段：资产生成（Assets）

这里开始生成图片。

人物Agent

输入：

Character001

输出：

标准立绘

头像

三视图

各种表情

各种动作
场景Agent

例如：

青云宗

输出：

白天

夜晚

远景

大厅

山门
技术

ComfyUI API

Flux

ControlNet

IPAdapter

LoRA

验收

随机：

20张图片。

人物一致率：

95%

第五阶段：导演Agent（Director）

输入：

事件

例如：

林凡进入宗门

输出：

Episode001

↓

Shot001

Shot002

Shot003

每个镜头：

人物

动作

镜头

情绪

持续时间

对白
技术

LLM

JSON

Story Planner

验收

人工检查：

是否符合影视节奏。

第六阶段：Prompt Builder

输入：

Shot001

自动生成：

Prompt

Negative Prompt

不用人工写。

技术

Jinja2 Template

Prompt DSL

验收

100个镜头。

全部生成Prompt。

第七阶段：图片生成

调用：

ComfyUI

输出：

shot001.png
技术

Python

WebSocket

REST API

ComfyUI

验收

100张图片：

全部生成。

失败率：

<2%

第八阶段：视频生成

输入：

shot001.png

输出：

shot001.mp4
推荐模型

Wan2.2 I2V（如果3090显存允许）

或

CogVideoX I2V

验收

连续：

100个镜头。

全部生成。

第九阶段：自动剪辑

Python：

MoviePy

FFmpeg

生成：

Episode001.mp4
功能

自动：

字幕

转场

配乐

片尾
验收

输出：

完整视频。

第十阶段：GUI

推荐：

PySide6

不要Electron。

原因：

全部Python。

调用方便。

性能高。

功能：

项目管理

小说导入

解析进度

人物浏览

场景浏览

镜头浏览

开始生成

继续生成

导出视频
每个阶段的验收标准（Definition of Done）
阶段	输出	验收标准
01 项目框架	可运行项目	能创建项目、初始化数据库
02 小说编译	世界数据库	100章小说可解析，结构化数据准确率 ≥90%
03 知识库	检索系统	人物、事件、地点可快速查询
04 资产生成	人物/场景素材	人物一致性 ≥95%
05 导演Agent	分集+分镜	每个事件生成合理镜头脚本
06 Prompt Builder	Prompt	每个镜头自动生成完整Prompt
07 图片生成	PNG	批量生成成功率 ≥98%
08 视频生成	MP4	每个镜头成功生成视频
09 自动剪辑	完整剧集	自动输出带字幕的剧集
10 GUI	桌面软件	支持完整项目管理流程
我建议采用"四层架构"

不要把所有功能都放在一个 Agent 中，而是采用分层设计：

┌─────────────────────────────┐
│           GUI               │
│       PySide6 Desktop       │
└──────────────┬──────────────┘
               │
┌──────────────▼──────────────┐
│         Pipeline Layer      │
│  负责任务编排、状态管理、恢复 │
└──────────────┬──────────────┘
               │
┌──────────────▼──────────────┐
│         Agent Layer         │
│ Novel │ Director │ Prompt   │
│ Asset │ Render │ Composer   │
└──────────────┬──────────────┘
               │
┌──────────────▼──────────────┐
│       Data Layer            │
│ SQLite + JSON + FAISS       │
│ 世界状态、时间线、资产索引    │
└─────────────────────────────┘

如果这是一个长期项目，我建议不要急着编码。 下一步应该先完成 《系统架构设计（Architecture Design）》，然后依次编写：

《01-项目规范.md》
《02-数据库设计.md》
《03-Agent设计.md》
《04-小说编译器设计.md》
《05-导演Agent设计.md》
《06-ComfyUI工作流设计.md》
《07-前后端通信设计.md》
《08-开发路线图.md》

这 8 份文档将作为整个项目的开发规范，后续所有 Python 代码都严格按照文档实现，这样项目规模扩展后仍然能够保持清晰、可维护。
