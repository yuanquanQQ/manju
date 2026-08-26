# 阶段 06：Prompt Builder

## 1. 阶段目标

把已审核的镜头脚本和视觉资产编译为稳定、可验证、可复现的渲染规格。Prompt Builder 不是自由写作 Agent，而是“镜头语义 → 引擎无关 RenderSpec → 引擎参数”的编译器。

这样可以在不修改导演数据的情况下切换基础模型、ComfyUI 工作流或视频引擎，也可以准确比较 Prompt 和参数变化带来的结果。

## 2. 输入与输出

### 输入

- 已审核 `ShotPlan`。
- 锁定版本的 Art Bible。
- 人物、场景、服装、道具和参考资产。
- 目标引擎能力 Profile。
- 输出尺寸、画幅、质量和成本配置。

### 中间输出：RenderSpec

RenderSpec 保留业务语义，不包含具体 ComfyUI 节点号：

```json
{
  "schema_version": "1.0",
  "shot_id": "shot_001_003",
  "mode": "text_to_image",
  "composition": {},
  "subjects": [],
  "environment": {},
  "style": {},
  "camera": {},
  "lighting": {},
  "motion_intent": {},
  "negative_constraints": [],
  "references": [],
  "seed_policy": "derive_from_shot",
  "output": {
    "width": 1280,
    "height": 720
  }
}
```

### 最终输出：EngineRenderRequest

- 正向/负向 Prompt。
- 模型、VAE、采样器、步数、CFG、种子和分辨率。
- ControlNet、IP-Adapter 等参考输入及权重。
- ComfyUI 工作流 ID、版本和参数映射。
- 预期输出槽位、超时和重试策略。

## 3. Prompt DSL

建议将 Prompt 分为固定槽位：

```text
[style]
[quality]
[shot purpose]
[camera/composition]
[subjects]
[actions/expression]
[environment/time/weather]
[lighting/color]
[continuity constraints]
```

槽位由结构化数据生成，顺序和权重由引擎 Profile 决定。不要直接把整个 Shot JSON 序列化给模型。

### 正向约束

- 只描述当前镜头可见的信息。
- 人物描述引用 VisualSpec 的稳定特征和当前服装。
- 同一属性只在一个权威槽位生成，避免互相矛盾。
- 用镜头目的决定画面重点，而不是堆叠所有世界设定。

### 负向约束

分三层管理：

- 全局：低质量、文字水印、错误画幅等。
- 工作流/模型：特定模型常见缺陷。
- 镜头：禁止多余人物、错误服装、错误武器、错误时间等。

负向 Prompt 不承担身份控制；关键一致性必须依赖参考资产和工作流条件。

## 4. 模板与适配器

### 模板

- 使用 Jinja2 或等价模板，但开启严格未定义变量。
- 模板有语义版本、测试样例和变更记录。
- 模板只负责格式，不包含数据库查询或文件复制。
- 用户可复制内置模板形成项目模板，避免直接改全局默认。

### 引擎 Profile

每个 Profile 声明：

- 支持的生成模式、尺寸、参考图数量和控制类型。
- 模型/工作流要求。
- Prompt 语法、权重写法、长度限制和默认负向项。
- RTX 3090 的安全参数范围。

如果镜头需求超出 Profile 能力，应返回明确的编译错误或降级建议，不能静默忽略。

## 5. 连续性与确定性

- 根据 `project_id + shot_id + render_version` 派生默认种子。
- 同一人物的身份参考和权重来自发布资产，不由每个 Prompt 自行决定。
- 相邻镜头继承服装、伤势、时间、天气和场景状态。
- Prompt、RenderSpec、工作流和输入资产均计算哈希。
- 任一有效输入改变时生成新版本，并把旧渲染标为 `STALE`。

## 6. 静态校验

编译前检查：

- 引用的实体和资产是否存在且已发布。
- 人物是否重复、漏掉或超出工作流支持数量。
- 人物服装、道具、场景时间与 ShotPlan 是否一致。
- Prompt 是否超过 Profile 限制。
- 尺寸是否满足模型倍数、画幅和显存约束。
- 所需参考文件、模型和工作流是否存在。
- 正向与负向是否包含明显冲突词。

## 7. 实施任务

1. 定义 RenderSpec、EngineProfile、EngineRenderRequest 和 CompileIssue Schema。
2. 建立规范化 Prompt DSL 和字段优先级。
3. 实现模板注册、版本、继承和 StrictUndefined 校验。
4. 实现至少一个图片工作流 Profile，并预留视频 Profile。
5. 实现人物、场景、道具和风格资产解析。
6. 实现种子、哈希、版本和缓存键计算。
7. 实现 Prompt 静态检查、能力检查和降级建议。
8. 实现单镜头、单场景和整集批量编译 CLI。
9. 建立 Golden Files，防止模板修改造成无意漂移。
10. 生成便于人工查看的 Prompt 报告。

## 8. 测试计划

- 同一输入重复编译产生完全一致的 RenderSpec 和哈希。
- 缺失人物资产、过期版本、无效尺寸和不支持能力能明确报错。
- 中英文标点、引号、特殊字符不会破坏模板或 JSON。
- 相邻镜头连续性字段正确继承。
- 切换引擎 Profile 不改变 ShotPlan，只改变 EngineRenderRequest。
- 100 个镜头全部可编译，且无空 Prompt、空引用或非法参数。

## 9. 交付物

- 版本化 Prompt DSL 与 RenderSpec。
- 模板注册中心和引擎 Profile。
- Prompt 编译器、静态检查器和报告。
- Golden Files 与批量编译测试。

## 10. Definition of Done

- [ ] 每个审核通过的镜头都能生成完整 RenderSpec。
- [ ] 100 镜头批量编译成功率为 100%，阻断问题有清晰定位。
- [ ] 所有 Prompt 可追溯到镜头、模板、资产和配置版本。
- [ ] 相同输入可重现相同请求和默认种子。
- [ ] 切换工作流只需新增/修改 Profile，不修改导演 Schema。
- [ ] 缺失资产和能力不匹配会在调用 GPU 前被发现。
- [ ] Golden Files 审核通过并纳入回归测试。

## 11. 风险与退出条件

- 如果 Prompt 模板仍依赖大量手工特例，应回到 VisualSpec 和 ShotPlan 补结构化字段。
- 如果一个 Profile 同时承担多种差异巨大的模型，拆分 Profile，避免条件分支失控。
- 如果人物一致性主要靠文本调整仍不稳定，应回到第四阶段改进参考资产和工作流。

