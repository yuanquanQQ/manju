# HANDOFF — novel2anime 交接文档

> 写给一个完全没有上下文的新会话看。涵盖项目概览、已完成工作、当前卡点、下一步计划和踩过的坑。

---

## 一、我们在做什么

**AI 漫剧生成系统 (novel2anime)**：输入长篇小说 (TXT/JSON)，输出完整的漫剧（图片 + 视频 + 字幕 + 配音）。

**四层架构：** CLI (Typer) → Pipeline → Agent → Data (SQLite + FAISS)

**当前项目：** `jueshi`（《重生十万年》），**2550 章**全部已导入 SQLite 数据库（`projects/jueshi/database/world.db`）。

---

## 二、已完成的工作

### 2.1 基础设施（全部正常可用）

| 模块 | 文件 | 状态 |
|---|---|---|
| 项目框架 | `main.py`, CLI 命令 | 可创建项目、初始化 SQLite |
| 小说导入 | `app/compiler/importer.py` | **2550 章全部导入** |
| LLM 适配器 | `app/adapters/llm.py` | OpenAI 兼容协议，qwen3.5-9b |
| ComfyUI 适配器 | `app/adapters/comfyui.py` | WebSocket + HTTP 轮询，生图成功 |
| 数据库 16 张表 | `app/database/models.py` | SQLite，schema v2 |
| LLM JSON 提取 | `app/adapters/llm.py::extract_json_object()` | 从思考过程中提取 JSON |
| 数据库浏览 | `app/ui/db_viewer.py` | **Streamlit 面板，`streamlit run app/ui/db_viewer.py`** |

### 2.2 核心流水线

```bash
# 小说导入（已全部完成，2550 章）
python main.py import-novel jueshi projects/jueshi/chapters

# 章节分析（第1章成功，约14分钟/章）
python main.py compile jueshi --limit 1          # 前 N 章
python main.py compile jueshi --start 2 --end 10 # 按范围

# 分镜生成
python main.py storyboard jueshi --list           # 查看进度
python main.py storyboard jueshi --start 2 --end 4

# ComfyUI 生图（已验证）
python main.py generate jueshi --type character --limit 1
python main.py generate-custom jueshi "your prompt here"

# 知识库导出
python main.py knowledge jueshi --action export
python main.py knowledge jueshi --action search --query "秦风"

# 验收抽查
python main.py validate jueshi --sample-count 1
```

### 2.3 Director Agent 提示词改造

`episode_001.json` 已手工重写为 9 个详细分镜，每个镜头包含：
- `environment`: `layout` / `lighting` / `color_palette` / `atmosphere`
- `characters[]`: 每人有 `appearance` / `clothing` / `pose` / `expression`
- `image_prompt`: 50-150 词英文 SDXL Prompt

代码已更新：[app/agents/director.py](file:///e:/work/cc/pingtai/video/video/app/agents/director.py) 和 [app/domain/storyboard.py](file:///e:/work/cc/pingtai/video/video/app/domain/storyboard.py)。

### 2.4 知识库 & 验收模块

- **知识库** [app/knowledge/knowledge_base.py](file:///e:/work/cc/pingtai/video/video/app/knowledge/knowledge_base.py)：JSON 导出 + FAISS 向量检索 + 全文检索兜底
- **验收工具** [app/validator/validator.py](file:///e:/work/cc/pingtai/video/video/app/validator/validator.py)：随机抽查章节分析准确性

### 2.5 命令范围控制

`compile` 和 `storyboard` 均支持 `--start` / `--end` / `--list`：
```bash
python main.py compile jueshi --start 2 --end 10
python main.py storyboard jueshi --list
```
修改涉及：[repository.py](file:///e:/work/cc/pingtai/video/video/app/compiler/repository.py)、[compile_novel.py](file:///e:/work/cc/pingtai/video/video/app/pipeline/compile_novel.py)、[storyboard.py](file:///e:/work/cc/pingtai/video/video/app/pipeline/storyboard.py)、[main.py](file:///e:/work/cc/pingtai/video/video/main.py)。

### 2.6 章节分析 JSON 文件保存

每章 compile 完成后自动保存分析结果到 `projects/<name>/production/analysis/ch_XXXXXX.json`。复用分析也会尝试保存。

### 2.7 Streamlit 数据库浏览面板

`app/ui/db_viewer.py` — 9 个 Tab 页展示全部数据库内容：
```
streamlit run app/ui/db_viewer.py
```
覆盖：章节、分析运行、实体、实体提及、事件、对白、状态变化、任务、生产文件。

### 2.8 全部 2550 章已导入

章节文件初始带 `.json.json.json.json.json.hold` 后缀，已批量重命名为 `.json` 后全部导入。

---

## 三、当前卡在哪里

### 3.1 LLM 响应过慢（最大瓶颈）

- **模型：** `qwen/qwen3.5-9b`，通过 LM Studio 运行在 `localhost:1234`
- **症状：** 每次非 trivial 调用，模型先输出巨量英文思考过程（"Thinking Process:"），消耗大部分 token 预算后才输出 JSON
- **后果：** 单章分析 14+ 分钟，2550 章编译完需要约 600 小时，不可行
- **用户态度：** 坚持使用 `qwen3.5-9b`，**绝对不要动模型配置**
- **已尝试的缓解：**
  - `LLM_MAX_TOKENS` 从 8192 提到 262144 — 有改善但不够
  - System Prompt 加"禁止输出任何思考过程" — 无效
  - [llm.py](file:///e:/work/cc/pingtai/video/video/app/adapters/llm.py) 的 `extract_json_object()` 扫所有 `{...}` 取 key 最多的 — 部分有效

### 3.2 章节分析只完成了第 1 章

仅 `ch_000001` 有 LLM 分析结果。第 2-5 章有旧分析缓存可复用，但从第 6 章起全部需要首次分析。2550 章中 2549 章等待编译。

### 3.3 人物一致性完全未实现

当前生图时每次独立生成，没有任何跨镜头的人物一致性控制。readme Stage 4 提到的 IPAdapter / ControlNet / LoRA 均未实现。

---

## 四、下一步计划

### 4.1 换写实模型（Juggernaut XL）

**当前模型：** `noobaiXLEpsilonPred_v11.safetensors`（动漫风）

**目标模型：** `Juggernaut_XI_byRunDiffusion.safetensors`（写实/半写实，约 6.6GB）

**下载（云端 GPU 服务器执行）：**
```bash
export HF_ENDPOINT=https://hf-mirror.com
pip install huggingface-hub
huggingface-cli download RunDiffusion/Juggernaut-XI-v11 \
  --local-dir /你的路径/ComfyUI/models/checkpoints/Juggernaut_XI \
  --local-dir-use-symlinks False
```

**代码改动：**
- [generate_image.py#L98](file:///e:/work/cc/pingtai/video/video/app/pipeline/generate_image.py#L98) — 改 checkpoint 名称
- [generate_image.py#L30-L35](file:///e:/work/cc/pingtai/video/video/app/pipeline/generate_image.py#L30-L35) — STYLE_TAGS 中 `anime style` → `photorealistic, cinematic, 8k`
- `episode_001.json` 中每个 `image_prompt` 的 `anime style` → `photorealistic, cinematic`

### 4.2 LoRA 人物一致性方案

**分三档：**

| 档位 | 角色类型 | 方案 | 成本 |
|---|---|---|---|
| **S 档** (2-5人) | 核心主角 | 训练专属 LoRA | 10-20 张定妆图 + Kohya SS 训练 |
| **A 档** (5-15人) | 重要配角 | IPAdapter + 单张参考 | 1 张定妆图 |
| **B 档** (其余) | 龙套 | 详细 Prompt 描述 | 零成本 |

**代码新增：** `build_sdxl_workflow()` 需支持可选 LoRA/IPAdapter 参数。

### 4.3 急需解决 LLM 慢的问题

不改模型的前提下可尝试：
- LM Studio 配置中设置 `"thinking": false` 或等效参数
- 降低 `LLM_MAX_TOKENS` 到 4096，强制更早输出 JSON
- 换用 vLLM 等更高效的后端替换 LM Studio

---

## 五、踩过的坑（绝对不要再踩）

### 5.1 不要动模型配置
- **用户明确拒绝换模型** — 不要改 `.env` 中 `LLM_MODEL`
- 曾尝试切 `qwen3-4b-2507`，被直接驳回
- 可以改 prompt、token 数、LM Studio 参数，**不要改模型名**

### 5.2 ComfyUI workflow 节点类型陷阱
- `CheckpointLoaderSimpleWithNoiseSelect` 是 AnimateDiff 节点，需要 `beta_schedule` 参数
- 标准 SDXL 生图用 `CheckpointLoaderSimple`
- 提交前用 `/object_info` API 确认节点可用性

### 5.3 LLM JSON 提取的复杂性
- qwen3.5-9b 输出极长英文思考过程（不是 `<think>` 标签）
- `response_format: json_object` 被 LM Studio 拒绝（只接受 `json_schema`）
- `extract_json_object()` 扫所有 `{...}` 取 key 最多者
- 仍可能误取思考文本中被引用的 JSON schema 片段

### 5.4 数据库字段名陷阱
- `Entity` 没有 `aliases` — 在独立表 `EntityAlias`
- `Entity` 没有 `mention_count`
- `NarrativeEventRecord` 没有 `chapter_order`/`chunk_index` — 用 `CompiledChapter` join
- `NarrativeEventRecord.participants_json` 是 JSON 字符串，需 `json.loads()` 解析

### 5.5 faiss-cpu 安装
- 清华源偶尔超时，换阿里云源：`pip install faiss-cpu -i https://mirrors.aliyun.com/pypi/simple/`
- 代码已容错：FAISS 不可用时 fallback 到全文检索

### 5.6 numpy / streamlit 版本兼容
- **numpy 2.x 与 streamlit 1.30 不兼容**，报错 `numpy.core.multiarray failed to import`
- 解决：`pip install "numpy<2,>=1.24"` 降级 numpy，再 `pip install streamlit --upgrade`
- 当前版本：numpy 1.26.4, streamlit 1.60.0

### 5.7 chapter 文件命名坑
- 章节原始文件名为 `chapter_XXX.json.json.json.json.json.hold`（5 层 `.json`）
- 需批量重名为 `chapter_XXX.json` 才能被 `import-novel` 识别
- 重命名 PowerShell 命令：
  ```powershell
  Get-ChildItem *.hold | ForEach-Object {
    $n = $_.BaseName -replace '\.json\.json\.json\.json\.json$', ''
    Rename-Item $_.FullName "$n.json"
  }
  ```

### 5.8 ComfyUI 连接
- 云端 GPU 隧道端口：`localhost:8189`
- 隧道下 WebSocket 可能超时，HTTP 轮询自动 fallback
- 健康检查：`python main.py doctor jueshi`

---

## 六、项目关键文件索引

| 文件 | 作用 |
|---|---|
| `main.py` | CLI 入口，所有命令定义 |
| `app/ui/db_viewer.py` | **Streamlit 数据库浏览面板** |
| `app/agents/director.py` | 分镜导演 Agent (LLM Prompt + 分镜解析) |
| `app/domain/storyboard.py` | 分镜/角色/环境领域模型 |
| `app/pipeline/storyboard.py` | 分镜生成 Pipeline |
| `app/pipeline/compile_novel.py` | 章节编译 Pipeline |
| `app/pipeline/generate_image.py` | 生图 Pipeline (Prompt 构建 + SDXL workflow) |
| `app/adapters/comfyui.py` | ComfyUI 客户端 |
| `app/adapters/llm.py` | LLM 客户端 + JSON 提取 |
| `app/compiler/analyzer.py` | 小说章节分析 Agent |
| `app/compiler/repository.py` | 数据库持久化 |
| `app/compiler/importer.py` | 章节导入 |
| `app/knowledge/knowledge_base.py` | 知识库导出+检索 |
| `app/validator/validator.py` | 分析质量抽查 |
| `app/database/models.py` | 16 张 SQLite 表定义 |
| `app/database/db.py` | 数据库引擎工厂 |
| `app/core/config.py` | 全局配置加载 |
| `.env` | 环境变量 (LLM URL/Model/Tokens) |
| `requirements.txt` | Python 依赖 |
| `HANDOFF.md` | 本文档 |
| `readme.md` | 10 阶段开发计划总览 |
