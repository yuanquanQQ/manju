# novel2anime — AI 漫剧生成系统

**输入一部中文长篇小说,输出一部完整的漫剧**(图片 + 视频 + 字幕 + 配音)。

novel2anime 是一条**本地 AI 漫剧生成流水线**:由 Windows 桌面应用(PySide6)驱动,
通过 SSH 调度远程 RTX 3090 GPU 服务器完成生图、AI 视频与音色克隆配音,全程本地
可控、可断点续跑。当前以《绝世丹神》(项目名 `jueshi`,2550 章)为实际制作样例。

> **从这里开始**:[项目启动说明](docs/00-项目启动说明.md) —— 包含日常启动、首次安装、
> 3090 连接、完整制作顺序、输出目录与故障排查。
>
> **开发蓝图**:[AI 漫剧生成系统 V1 开发计划总览](docs/开发计划/README.md)。

---

## 目录

- [项目简介](#项目简介)
- [特性一览](#特性一览)
- [系统架构](#系统架构)
- [制作流水线](#制作流水线)
- [环境要求](#环境要求)
- [安装](#安装)
- [配置](#配置)
- [启动](#启动)
- [命令行参考](#命令行参考)
- [从小说到成片:制作流程](#从小说到成片制作流程)
- [外部依赖与模型](#外部依赖与模型)
- [项目目录结构](#项目目录结构)
- [数据与产物](#数据与产物)
- [测试](#测试)
- [文档索引](#文档索引)
- [项目现状与已知限制](#项目现状与已知限制)

---

## 项目简介

novel2anime 将一部长篇小说自动转化为分镜、画面、配音与成片。系统把传统动画/漫剧
制作拆成一条可审计、可恢复的流水线:

```text
长篇小说 (TXT / Markdown / 章节 JSON)
        │
        ▼
小说导入 → 结构化分析 → 分镜剧本 → 角色定妆 → 关键帧生图 → 配音 → 视频生成 → 对口型 → 合成成片
```

核心思路是 **「先建世界,再拍戏」**:

1. **建世界**:LLM 逐章抽取人物、地点、事件、对白与状态变化,连同原文证据写入 SQLite,
   并导出为知识库(人物档案 / 世界观 / 时间线)。
2. **写剧本**:导演 Agent 把每章拆成 18-28 个镜头,为每个镜头生成画面描写、人物动作、
   环境细节、英文生图提示词、运镜、转场与连续性约束。
3. **拍画面**:以人物定妆照与视觉身份指纹锁跨镜头一致,经 ComfyUI 生图、MiniMax H3
   FL2VA 生成视频、CosyVoice / Edge TTS 配音、LatentSync 对口型,最后合成带中文字幕
   的成片。

---

## 特性一览

**小说处理**
- 支持 TXT、Markdown、章节 JSON 目录导入,自动编码探测(UTF-8 / UTF-16 / GB18030)。
- 按“第X章 / 序章 / 楔子 / chapter N”自动切分章节,产出版本化标准章节。
- LLM 结构化分析:实体、事件、对白、状态变化全部带原文证据区间与置信度,严格 Schema。
- 分析结果可复用(`input_hash` 去重),支持 `--start/--end` 范围控制与断点续跑。

**分镜与导演 Agent**
- 每章自动生成 18-28 个镜头,总时长 60-90 秒,符合影视节奏。
- 每镜头包含场景描写、人物刻画、环境细节、英文生图提示词、运镜、时长与转场。
- 镜头间连续性规划:入镜/出镜状态、动作阶段、匹配锚点、参考帧,跨镜保持人脸/服装/站位。

**角色一致性**
- 为每个角色构建「不可变身份指纹」文本锁(面容 / 眼型 / 发型 / 服装配色 / 标志配件)。
- 支持 SDXL IP-Adapter Plus Face 人脸身份参考(单人女性镜头)与文本身份锁(男性角色)。
- 角色定妆候选图记录生成模型、时间与种子,可“设为定妆 / 解除定妆”。

**图片生成**
- ComfyUI 后端,预设模型:FLUX.1 Krea Dev FP8(默认)与 Juggernaut XI(SDXL)。
- 6 种视觉风格预设(真人电影 / 简笔画 / 油画 / 中国水墨 / 迪士尼动画感 / 游戏 CG)。
- **图片不满意时可用 FLUX.1 Kontext Dev FP8 修改**:读取原图按“问题与修改要求”生成
  1-4 个候选,支持严格保留 / 平衡 / 较大调整三档,原图与修改历史全程保留。

**视频生成**
- 默认引擎 **MiniMax H3 FL2VA + T8 音视频增强包**(本地权重经 ComfyUI 运行,非云 API)。
  T8 图(`comfyui-minimax-h3-audio-T8`)以联合音视频 conditioning + 双钟采样器替换官方图:
  视频与音频 latent 各按独立 schedule 去噪,原生对白 / 音效 / 配乐由提示词直接合成,
  对白清晰度与声画同步优于官方节点;支持首尾帧输入与可选参考音频(`drive_audio`)。
- 三种音频模式不变:`off` 静音、`ambience_sfx_music`(默认)环境音 + 音效 + 音乐、
  `native_full` 原生对白。`--engine official` 可一键回退官方节点图。
- 生成速度由官方图 20 步放开为参数化(`--steps`,T8 基线 4–8 步),CLI 与镜头规格均可调。
- 备选 **漫画动效**(确定性 FFmpeg 渲染器)用于静态推拉预览,含 8 种运镜预设。

**配音与字幕**
- 逐镜头自动旁白 / 角色对白 / 自定义文案 / 静音四种模式。
- 音色引擎:Edge TTS(在线,免 Key)或 **Fun-CosyVoice3-0.5B 本地音色克隆**(RTX 3090)。
- 声音角色库:导入本人 / 已授权 / 原创合成的参考音色,自动/手动选角并锁定。
- 输出 MP3、逐镜头 SRT、整集 SRT、带 AAC 音轨的 MP4;统一 1280×720 / 24fps / 48kHz
  双声道,响度标准化,中文字幕直接烧录进画面。

**对口型 (LatentSync 1.6)**
- 逐镜头生成口型,多人镜头按说话人自动跟踪目标脸(InsightFace 身份向量)。
- 匹配分数低于安全阈值即停止,不回退到“面积最大的人脸”。
- 批量整集口型可续跑,自动跳过旁白与已完成镜头。

**任务系统**
- SQLite 持久化任务状态机(PENDING → RUNNING → SUCCEEDED / FAILED / RETRYING /
  PAUSED / CANCELED / STALE),支持暂停、取消、恢复、心跳与中断检测。
- 长任务可断点续跑;崩溃后 `recover` 识别失去心跳的任务并安全暂停。

**桌面应用**
- PySide6 图形界面:项目总览、小说处理、角色定妆、声音角色库、本地资源包、分镜浏览、
  镜头视频生成、整集无声预览、逐镜头配音、字幕烧录、带声成片、任务日志、GPU 服务器控制。

---

## 系统架构

项目采用 **四层架构**:`CLI / GUI → Pipeline → Agent → Data`。

```text
┌───────────────────────────────────────────────────────────────┐
│  展示层  Typer CLI (main.py) │ PySide6 桌面应用 (app/ui)      │
└──────────────────────────────┬────────────────────────────────┘
                               │
┌──────────────────────────────▼────────────────────────────────┐
│  Pipeline 层  任务编排、状态管理、断点恢复                     │
│   app/pipeline: ingest · compile_novel · storyboard · pacing · │
│   character_identity · audio_timing · continuity · generate    │
└──────────────────────────────┬────────────────────────────────┘
                               │
┌──────────────────────────────▼────────────────────────────────┐
│  Agent 层  LLM 智能体                                         │
│   novel_extractor(抽取) · director(导演分镜) · validator(验收) │
└──────────────────────────────┬────────────────────────────────┘
                               │
┌──────────────────────────────▼────────────────────────────────┐
│  Data 层  SQLite + JSON + FAISS                               │
│   源文档 → 编译章节 → 分析运行 → 实体/事件/对白/状态 → 分镜 JSON │
└───────────────────────────────────────────────────────────────┘
```

**本地 / 远程分工**:桌面应用(Windows)负责操作、保存与合成;远程 RTX 3090 服务器负责
计算密集型生成(ComfyUI 生图 / MiniMax H3 视频 / CosyVoice 配音 / LatentSync 口型),
通过 SSH(paramiko)通信,服务器密码只保存在应用内存、不写入磁盘。

**核心代码包**:

| 目录 | 职责 |
|---|---|
| `app/adapters/` | 外部服务适配器:LLM(OpenAI 兼容协议)、ComfyUI(工作流提交/进度/下载) |
| `app/agents/` | LLM 智能体:novel_extractor(抽取)、director(导演分镜) |
| `app/compiler/` | 小说导入、分块、结构化分析与 SQLite 持久化 |
| `app/core/` | 基础能力:配置、日志、原子文件读写、环境诊断、命名 |
| `app/database/` | SQLite(SQLAlchemy):引擎 / ORM 模型 / 迁移 |
| `app/domain/` | Pydantic 数据契约:小说、分镜、视频、音频、项目、任务 |
| `app/knowledge/` | 知识库导出(world / characters / timeline)+ FAISS 检索 |
| `app/pipeline/` | 业务流水线编排(见上) |
| `app/services/` | 领域服务与渲染后端:任务、项目、配音、口型、GPU 等 |
| `app/ui/` | PySide6 桌面页面 + Streamlit 数据库浏览面板 |
| `app/validator/` | 分析质量随机抽查与准确率报告 |

---

## 制作流水线

以 CLI 视角,一条完整流水线如下(`<project>` 为项目名,如 `jueshi`):

```text
main.py import-novel <project> <source>    # 1. 导入小说 → 版本化标准章节
main.py compile       <project>             # 2. 逐章结构化分析 → SQLite + analysis JSON
main.py storyboard    <project>             # 3. 分镜剧本 → production/episodes/episode_N.json
main.py generate      <project> --type character  # 4. (可选)CLI 生图;角色定妆多在 GUI 完成
     …（GUI:关键帧生图 → 视频 H3 → 配音 → 对口型 → 合成成片）
```

| 阶段 | 实现模块 | 产物 |
|---|---|---|
| 项目脚手架 | `app/services/project_service.py:create_project` | `projects/<slug>/`、`project.json`、`config.yaml` |
| 小说导入 | `app/compiler/importer.py:import_novel` | `novel/chapters/ch_*.json` + `CompiledChapter` |
| 结构化分析 | `app/compiler/analyzer.py:analyze_chapter` | `ChapterAnalysis` → `production/analysis/*.json` |
| 分镜生成 | `app/agents/director.py` + `app/pipeline/storyboard.py` | `production/episodes/episode_*.json` |
| 节奏 / 时长 | `app/pipeline/pacing.py` | 目标镜头数、60-90 秒集时长 |
| 角色一致性 | `app/pipeline/character_identity.py` | 每角色「视觉身份指纹」文本 |
| 生图 | ComfyUI(本地/远端)+ `image_models` | 定妆照、分镜首帧 |
| 视频 | MiniMax H3 FL2VA / 漫画动效 | 逐镜头 MP4 |
| 配音 | `audio_service`(Edge/CosyVoice) | 逐镜头音频 + SRT + 带声成片 |
| 对口型 | LatentSync 1.6 | 逐镜头口型视频 |
| 合成 | `video_service.VideoRenderService` | `outputs/episodes/<拼音>_<集号>/` 成片包 |

---

## 环境要求

- **操作系统**:Windows 10 / 11(桌面应用面向 Windows)
- **Python**:3.11 或 3.12(64 位),推荐 3.11
- **磁盘**:至少 15 GB 本地空间(不含模型)
- **FFmpeg**:可通过 `imageio-ffmpeg` 使用随包二进制,无需单独安装
- **远程 GPU 服务器**(可选但推荐):RTX 3090 及以上的 SSH 可达实例,用于生图 / H3 视频 /
  CosyVoice / LatentSync;本地仅做文本分析与 FFmpeg 合成
- **网络**:安装依赖需联网;Edge TTS 运行时需联网(不需要 API Key);远端模型下载
  统一走 `HF_ENDPOINT=https://hf-mirror.com` 镜像

> 本地不要求 NVIDIA 显卡,也不要在本机安装 CUDA——计算在远程 3090 上进行。

---

## 安装

在 PowerShell 中进入项目根目录执行:

```powershell
# 1. 创建独立虚拟环境（需已安装 Python 3.11/3.12）
py -3.11 -m venv .venv

# 2. 升级 pip 并安装运行依赖
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -e .

# 3.（可选）安装开发工具：pytest / pytest-cov / ruff
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"

# 4. 创建本机配置文件
Copy-Item .env.example .env
```

如果没有 `py -3.11`,请从 [Python 官网](https://www.python.org/downloads/) 安装
Python 3.11/3.12 并在安装界面勾选 **Add Python to PATH**,然后把命令里的 `py -3.11`
改为 `python`。首次安装 `llama-cpp-python` 可能需要数分钟。

### 严格复现安装

`requirements.lock` 为 **Windows / CPython 3.12 验证锁**(2026-07-23 生成,63 个精确版本),
仅适用于其开头标注的平台与 Python 版本;其他环境请使用 `requirements.txt`。

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install -e .
```

### 补充或更新依赖

新增 Python 第三方库时,同时写入 `requirements.txt`;若为运行时必需库,也同步写入
`pyproject.toml` 的 `project.dependencies`,然后在本机重新安装。

---

## 配置

配置优先级:**环境变量 > 项目根目录 `.env` > 代码默认值**(自带极简 `.env` 解析器)。

`.env.example` 中全部变量:

| 变量 | 含义 | 默认值 |
|---|---|---|
| `LLM_BASE_URL` | OpenAI 兼容 LLM 地址(LM Studio / Ollama / vLLM / llama.cpp) | `http://127.0.0.1:1234` |
| `LLM_MODEL` | LLM 模型名 | `qwen/qwen3.5-9b` |
| `LLM_TIMEOUT` | LLM 请求超时(秒) | `600` |
| `LLM_MAX_RETRIES` | 最大重试次数 | `3` |
| `LLM_MAX_TOKENS` | 单次生成最大 token 数 | `4096` |
| `LLM_CONTEXT_SIZE` | 上下文长度(llama.cpp `--n_ctx`) | `8192` |
| `LLM_MODEL_PATH` | 本地 GGUF 模型路径;留空表示不在本机启动 llama.cpp | `models/llm/Qwen.Qwen3.5-9B.Q4_K_M.gguf` |
| `EXTRACT_MAX_CHARS` | 单章抽取最大字符数 | `6000` |
| `EXTRACT_CONCURRENCY` | 抽取并发数 | `1` |
| `COMFYUI_URL` | ComfyUI 服务地址 | `localhost:8189` |
| `COMFYUI_TIMEOUT` | ComfyUI 执行超时(秒) | `600` |
| `LOCAL_AI_ROOT` | 本地生成模型根目录(模型中心扫描) | `models/generative` |
| `GPU_SSH_HOST` | GPU 服务器 SSH 地址 | *(空)* |
| `GPU_SSH_PORT` | SSH 端口 | `22` |
| `GPU_SSH_USER` | SSH 用户 | `root` |
| `SQLITE_BUSY_TIMEOUT_MS` | SQLite busy timeout | `30000` |
| `PIPELINE_STALE_AFTER_SECONDS` | 任务心跳超时(判定 STALE) | `300` |

**要点**:

- `.env` 中的 `LLM_MODEL_PATH` 与 `LOCAL_AI_ROOT` 必须改成新电脑的实际路径。
- GPU 服务器 **SSH 密码不要写入 `.env`**,请在桌面应用“连接与设置”中输入(仅存内存)。
- 旧变量名 `OLLAMA_URL` / `OLLAMA_MODEL` / `OLLAMA_TIMEOUT` / `OLLAMA_MAX_RETRIES`
  仍然兼容(优先读 `LLM_*`)。
- 所有生成模型/提示词配置有安全默认值,不配置即可运行(文本分析需自行准备 Qwen GGUF)。

---

## 启动

### 桌面应用(推荐)

双击 `start_gui.bat`,或:

```powershell
.\.venv\Scripts\python.exe main.py gui
```

### 环境诊断

```powershell
.\.venv\Scripts\python.exe main.py doctor --skip-llm
.\.venv\Scripts\python.exe main.py info
```

`doctor` 检查 Python 版本、SQLite、ffmpeg/ffprobe、NVIDIA GPU、磁盘空间、LLM 连通性与
项目数据库完整性。

---

## 命令行参考

`main.py` 使用 Typer,提供 19 个命令(`python main.py` 显示帮助)。

### 项目与运维

| 命令 | 说明 |
|---|---|
| `create NAME [--display-name]` | 新建项目目录(含 project.json / config.yaml / 数据库迁移) |
| `gui` | 启动本地桌面制作应用 |
| `info [NAME]` | 显示 LLM 配置与目录状态 |
| `doctor [NAME] [--skip-llm]` | 本地环境体检 |
| `status NAME [--state] [--limit]` | 查看任务状态 |
| `pause NAME JOB_ID` | 暂停等待中任务,或向运行中任务发送协作暂停 |
| `cancel NAME JOB_ID` | 取消等待中任务,或发送协作取消 |
| `resume NAME JOB_ID` | 把 PAUSED / FAILED / STALE 任务恢复为 PENDING |
| `recover NAME [--stale-after]` | 识别失去心跳的任务并安全暂停 |
| `clean-cache NAME [--yes]` | 清理可重建的项目 `cache/` |
| `restore-db NAME BACKUP [--yes]` | 从 `database/` 内备份恢复数据库(恢复前自动安全备份) |

### 小说处理与分析

| 命令 | 说明 |
|---|---|
| `import-novel NAME SOURCE [--limit]` | 导入 TXT / Markdown / 章节 JSON 目录 → 版本化标准章节 |
| `ingest NAME [--chapters-dir] [--limit] [--force]` | (旧流程)扫描 `chapters/*.json` 调 LLM 抽取入库 |
| `compile NAME [--limit / --start / --end] [--force]` | 对标准章节执行带原文证据的结构化分析 |
| `storyboard NAME [--limit / --start / --end] [--list]` | 将已分析章节转成分镜脚本 |
| `validate NAME [--sample-count] [--seed]` | 随机抽查分析质量,计算准确率 |
| `knowledge NAME --action export\|search --query` | 导出知识库或语义搜索 |

### 图片生成

| 命令 | 说明 |
|---|---|
| `generate NAME [--type] [--limit] [--width/--height/--steps/--cfg/--seed]` | 从数据库实体取描述调 ComfyUI 生图 |
| `generate-custom NAME PROMPT [--negative] [...]` | 用自定义提示词调 ComfyUI 生图 |

> CLI 覆盖项目管理、导入、分析、分镜、生图、验收与运维;配音、视频、口型、合成等
> 生产步骤主要在桌面应用中操作,也可通过 `scripts/` 下的一键脚本调用。

---

## 从小说到成片:制作流程

### 1. 小说处理

进入桌面应用「小说处理」:导入 TXT / Markdown / 已有章节数据 → 点击处理 → 等待章节切分、
人物抽取与分镜生成。结果不满意时可“重新处理”,旧结果先备份到 `production/backups/`。

本地文本模型默认:
```text
models/llm/Qwen.Qwen3.5-9B.Q4_K_M.gguf   # LLM_MODEL_PATH
http://localhost:1234/v1                  # LLM_BASE_URL（本机文本分析，非 3090）
```

### 2. 角色定妆

「角色定妆」:选择视觉风格 → 修改正向/负向提示词与定妆预设 → 选择一个或多个生图模型
生成候选 → 对满意候选「设为定妆」。每张候选图都记录生成模型与精确到秒的生成时间。

### 3. 分镜脚本与首帧

「分镜脚本」:检查每镜头的画面描述、动作、环境动作、运镜、时长与转场 → 直接修改提示词
→ 保存 → 缺图镜头「自动补全缺失画面」→ 已有首帧僵硬时「重做连续首帧」。

连续首帧采用**保守引用**:仅当同场景且出场人物集合完全一致时,下一镜头才以上一镜头做
图生图参考;人物阵容改变 / 闪回 / 换场自动断开引用,只继承场景轴线、光向、道具等文字
锚点,防止错误人物累积。首帧优先取动作发生前一刻,避免正面对称站桩。

### 4. MiniMax H3 视频

「视频生成」视频标签:选择分集与镜头 → 确认首帧(需要动作终点时可准备尾帧,支持首尾帧
输入)→ 默认引擎 MiniMax H3 FL2VA(T8 音视频增强)→ 检查自动填入的人物动作 / 环境动作 /
负面提示词 / 运镜 / 时长 → 生成 → 合成整集预览。

H3 自带原生音效 / 对白:`off` 静音、`ambience_sfx_music`(默认)环境音 + 音效 + 音乐、
`native_full` 原生对白。T8 图以联合音视频 conditioning + 双钟采样器生成,对白与音效直接
由提示词合成,声画同步优于官方节点;回退官方图用 `--engine official`。提示:首帧脸部清晰、
四肢完整,动作提示词一次只描述一个主要动作;桌面端「连接与设置」需先安装/修复 T8 音频包。

### 5. 配音、字幕与对口型

「视频生成」→「配音与字幕」:选择自动旁白 / 角色对白 / 自定义文案 / 静音 → 选择
CosyVoice 3·3090 本地音色克隆或 Edge 中文音色 → (CosyVoice)选择 3-15 秒清晰单人参考音频
并填写逐字参考台词、情绪 / 表演指令 → 试听 → 生成带声成片。

对白镜头在 LatentSync 就绪后可「生成当前镜头口型」;多人镜头按说话人自动跟踪目标脸。
最终输出逐镜头音频、逐镜头 SRT、整集 SRT 与带 AAC 音轨的 MP4,字幕直接烧录进画面。

### 6. 交付产物

可交付文件统一保存在:

```text
outputs/episodes/<项目拼音>_<集号>/
├── shipin/    # 最终 MP4
├── zimu/      # 整集与分段 SRT
├── yinpin/    # 逐镜头配音
├── qingdan/   # 整集与分段清单
└── zhijian/   # 质检图片
```

文件名只使用拼音、数字和下划线。桌面应用「本地资源包」页可一键整理并打开这些目录。

---

## 外部依赖与模型

| 服务 | 角色 | 连接方式 | 位置 |
|---|---|---|---|
| **LLM**(OpenAI 兼容) | 结构化抽取 / 导演分镜 / 验收 | HTTP `/v1/chat/completions`,无鉴权头 | 本机 `localhost:1234`(llama.cpp / LM Studio / Ollama / vLLM) |
| **ComfyUI** | 生图 / H3 视频 / 口型工作流后端 | HTTP + WebSocket,无 API Key | 本机 `localhost:8189` 或远程 3090 |
| **GPU 服务器** | 远程计算(RTX 3090) | SSH(paramiko),密码会话内输入 | `GPU_SSH_HOST:GPU_SSH_PORT` |
| **MiniMax H3 FL2VA** | AI 视频(本地权重,非云 API) | 经远程 ComfyUI 运行 | 3090 ComfyUI 模型目录(≈42 GB,4 个权重) |
| **MiniMax H3 Audio T8 包** | H3 音视频增强自定义节点(纯 Python,无 pip 依赖) | 经远程 ComfyUI 运行 | 3090 `custom_nodes/comfyui-minimax-h3-audio-T8`,桌面端「连接与设置」一键安装/修复 |
| **FLUX.1 Kontext Dev FP8** | 图片修改 / 动作尾帧编辑器 | 经 ComfyUI 运行 | 3090(11.9 GB) |
| **Fun-CosyVoice3-0.5B** | 本地音色克隆 TTS | HTTP `127.0.0.1:50000` | 3090 `/root/cosyvoice-models/` |
| **Edge TTS** | 在线 TTS(免 Key) | 网络 | 内置 6 个中文音色预设 |
| **LatentSync 1.6** | 对口型 | 远程 SSH + ComfyUI 工作流 | 3090(需 ≥18 GB 显存) |
| **IP-Adapter Plus Face** | SDXL 人脸身份参考 | 经 ComfyUI 运行 | 3090 |

**部署脚本**位于 `scripts/gpu/`,全部使用 `HF_ENDPOINT=https://hf-mirror.com` 镜像、
断点续传与文件大小校验;桌面应用「连接与设置」可一键检测 / 安装 / 修复。

**显存协调**:CosyVoice 与视频 / 图片引擎会争用 3090 显存。应用在开始生图或 H3 视频前
自动停止 CosyVoice,需要配音时再按需启动;对口型运行前也会释放 ComfyUI / CosyVoice。

---

## 项目目录结构

```text
novel2anime/
├── main.py                  # Typer CLI 入口（19 个命令）
├── pyproject.toml           # 项目元数据、依赖、入口点、工具配置
├── requirements.txt         # 运行依赖
├── requirements.lock        # Windows / CPython 3.12 验证锁
├── .env.example             # 配置模板（复制为 .env 使用）
├── start_gui.bat            # Windows 一键启动桌面应用
├── app/                     # 应用代码（见“系统架构”表格）
│   ├── adapters/  agents/  compiler/  core/
│   ├── database/  domain/  knowledge/ pipeline/
│   ├── services/  ui/  validator/
├── scripts/                 # 制作 / 批处理 / 一次性脚本
│   ├── generate_episode_dubbing.py     # 整集配音 + 字幕 + 带声成片
│   ├── generate_episode_lipsync.py     # 批量对口型（可续跑）
│   ├── generate_episode_h3.py          # 整集 H3 生成（可续跑）
│   ├── generate_storyboard_keyframes.py# 整集首帧关键帧
│   ├── regenerate_episode_storyboard.py / regenerate_shot_keyframe.py
│   ├── generate_high_quality_cast.py   # 真人选角（审核门禁式）
│   ├── generate_high_quality_angles.py / generate_cast_turnarounds.py
│   ├── approve_cast_angles.py          # 批准三视图并发布身份锚点
│   ├── download_local_llm.ps1          # 下载本地 Qwen GGUF
│   └── gpu/                            # 远端部署（cosyvoice / latentsync / minimax_h3 / minimax_h3_t8 / flux_kontext / ipadapter）
├── workflows/               # ComfyUI 工作流脚本
│   ├── krea/                # FLUX.1 Krea Dev 生图 / 修订（generate_samples / revise_image / generate_shots）
│   ├── chinese_cast/        # Z-Image + Qwen 多角度选角
│   ├── high_quality_image/  # 两阶段身份锁定关键帧
│   └── minimax_h3/          # H3 FL2VA 视频任务（build_prompt 官方图 / build_t8_prompt T8 图，--engine 切换）
├── docs/                    # 文档（见“文档索引”）
├── tests/                   # 160 个 pytest 单元测试
├── projects/                # （运行时）项目数据，不入库
├── models/                  # （运行时）本地模型
├── logs/                    # （运行时）日志
└── 绝世丹神-校对版全本-作者-网络黑侠/  # 示例小说源数据（jueshi 项目，2550 章）
```

---

## 数据与产物

**数据库**(每个项目独立):`projects/<slug>/database/world.db`
- SQLite(SQLAlchemy),WAL 模式,busy_timeout 可配置。
- 22 张表覆盖全链路:章节、人物/场景/事件、实体与提及、叙事事件、对白、状态变化、
  分析运行、任务、任务依赖、产物、审核、设置快照、源文档与编译章节。
- 迁移前自动备份(`world.db.pre-schema-vN.bak`);当前 schema v2。

**知识库导出**:`projects/<slug>/production/knowledge/`
- `world.json`(世界观)、`characters.json`(人物档案)、`timeline.json`(事件时间线);
  语义检索使用 FAISS 索引(不可用时回退全文检索)。

**项目内关键目录**(以 `jueshi` 第 1 集为例):

```text
projects/jueshi/
├── novel/                     # 源文档与标准章节
├── assets/                    # characters / locations / voices 等资源包
├── production/
│   ├── episodes/              # 分镜脚本 episode_001.json
│   ├── analysis/              # 章节分析 JSON
│   ├── knowledge/             # 知识库导出
│   ├── shots/                 # 分镜图片与生成记录
│   ├── video_inputs/          # 视频任务首帧
│   ├── videos/                # 单镜头视频与历史产物
│   ├── audio/                 # 音频产物
│   ├── cast/                  # 定妆 / 角度 / 身份锚点
│   └── backups/               # 重新处理前的自动备份
├── outputs/episodes/jueshi_001/   # 可交付成片包
└── database/world.db          # SQLite 数据库
```

**日志**:`logs/app.log`(DEBUG,20 MB 轮转,保留 10 天)+ 控制台(INFO)。

**任务系统**:长任务写入 `jobs` 表,带状态机、优先级、重试、心跳与依赖;`input_hash`
实现结果复用与去重。

---

## 测试

38 个本地 pytest 单元测试,覆盖导入、分析、分镜、连续性、生图工作流、配音、口型规划、
任务系统、GUI(offscreen Qt)等模块。**无需 GPU、外部服务或真实 LLM**——远程调用均被
模拟 / monkeypatch。

```powershell
.\.venv\Scripts\python.exe -m pytest -q          # 运行全部测试
.\.venv\Scripts\python.exe -m pytest -q -k voice  # 按关键字过滤
```

代码检查(dev 依赖):

```powershell
.\.venv\Scripts\python.exe -m ruff check .
```

> 注意:Windows 上运行测试请使用 `.venv` 解释器;涉及磁盘的测试请使用 pytest 的
> `--basetemp` 指向临时目录,避免污染项目目录。

---

## 文档索引

| 文档 | 说明 |
|---|---|
| [docs/00-项目启动说明.md](docs/00-项目启动说明.md) | **权威启动文档**:日常启动、首次安装、3090 连接、制作顺序、输出目录、故障排查 |
| [docs/02-桌面应用使用指南.md](docs/02-桌面应用使用指南.md) | 桌面应用全页面操作手册 |
| [docs/03-声音版权与授权说明.md](docs/03-声音版权与授权说明.md) | 声音克隆与授权合规说明 |
| [docs/04-图片不满意时的修改功能.md](docs/04-图片不满意时的修改功能.md) | 图片修改 / 重生成的界面操作与写法建议 |
| [docs/开发计划/README.md](docs/开发计划/README.md) | 开发计划总览(产品目标、架构、十阶段、里程碑) |
| [docs/high_quality_personal_workflow.md](docs/high_quality_personal_workflow.md) | 个人高质量生产工作流(六阶段、审核门禁、当前模型) |
| [HANDOFF.md](HANDOFF.md) | 交接文档(部分内容已过时,仅作工程史参考) |

> `docs/01-启动指南.md` 与两份 `docs/工作记录_*.md` 明确标注为历史 / 时点快照,不应视为
> 当前现状。

---

## 项目现状与已知限制

**当前样例**:项目 `jueshi`(《绝世丹神》/《重生十万年》,2550 章)已全部导入 SQLite,
第 1 集分镜与制作流程已打通;HANDOFF.md 记录的单章分析耗时(约 14 分钟)反映早期
LLM 后端状态,可通过更换更快的 OpenAI 兼容后端 / 调整 `LLM_MAX_TOKENS` 优化。

**已知限制**:
- 文本分析依赖本地 LLM(默认 qwen3.5-9b 风格),抽取质量与模型能力相关;可用
  `main.py validate` 抽查准确率。
- 视频 / 配音 / 口型依赖远程 3090 与对应模型权重(约 42 GB H3 + 各辅助模型),首次部署
  耗时较长;可用 `doctor` 与「连接与设置」逐步体检。
- 人物一致性格外依赖身份指纹与参考帧机制,复杂场景仍需人工在 GUI 中审批候选。
- 项目早期技术选型(Wan2.2 等)已迁移至 MiniMax H3 FL2VA;`workflows/wan22/` 仅余缓存。
- H3 视频已迁移到 T8 音视频增强图(`h3_t8_chained_v1`):联合 conditioning + 双钟采样器,
  对白/音效由提示词直接合成;依赖 `comfyui-minimax-h3-audio-T8` 自定义节点,首次使用前
  需在「连接与设置」安装/修复 T8 音频包。
- **镜头间像素级连贯**:`generate_episode_h3.py` 默认开启末帧→首帧链接——本地用 ffmpeg
  从镜头 N-1 的已生成视频抽取末帧,作为镜头 N 的 `source_image`,使相邻镜头在像素级精确
  交接(`--chain-shots`,传 `--no-chain-shots` 退回独立首帧)。抽帧失败自动回退故事板关键帧,
  不阻塞生成。该机制仅作用于脚本路径,不影响桌面端批量调用。
- **跨镜语音连贯**:链路开启时,同时抽上一镜末尾约 2 秒音频作为镜头 N 的
  `reference_audio`(T8 `drive_audio`),让模型承接上一镜的音色与语流;prompt 首段追加
  "Voice continuity" 指令锁定同一说话人。`off` 模式不下发语音指令。
- 长视频多镜头跨镜上下文(LongVideo 家族)与 OpenVDN 8 步加速为后续规划项,暂未接入。

---

*License / 版权提示:本系统仅用于生成本人、已获明确授权或原创合成的音色与内容。声音克隆
请先阅读 [docs/03-声音版权与授权说明.md](docs/03-声音版权与授权说明.md)。*
