# 项目数据与版权隔离规范

## 原则

- Git 只保存程序、测试、空目录说明和可公开的小型合成样例。
- 小说原文、付费章节、授权文件、角色参考图、生成图像、音视频和模型均属于本地数据，不进入仓库。
- 每个制作项目放在 `projects/<slug>/`；临时原文可放在 `source-material/`，二者均已被 `.gitignore` 排除。
- 测试只能使用自行编写的短文本，不得截取真实作品作为 fixture。

## 推荐目录

```text
projects/<slug>/
  novel/chapters/          # 标准化原文
  production/analysis/     # 事实分析
  production/story_plans/  # 编剧计划
  production/episodes/     # 分镜
  production/quality/      # 质量报告
  assets/                  # 图片与模型引用
  outputs/                 # 音视频成品
```

## 迁移旧数据

先预览，不会移动文件：

```powershell
python scripts/migrate_local_project_data.py <旧目录> projects/<slug>
```

确认目标目录为空且预览正确后执行：

```powershell
python scripts/migrate_local_project_data.py <旧目录> projects/<slug> --apply
```

已被 Git 跟踪的版权数据不会因为 `.gitignore` 自动消失。应先备份，再由仓库负责人单独执行
`git rm --cached` 并审查变更；本迁移脚本不会修改 Git 索引。
