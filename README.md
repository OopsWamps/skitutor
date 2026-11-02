<p align="center">
  <picture>
    <img src="docs/assets/skitutor-logo.svg" width="560" alt="SKITUTOR">
  </picture>
</p>

# skibot

滑雪视频体态分析：上传一段滑雪视频，输出关键点轨迹、五项体态指标和结构化报告。

架构基于 Sports2D（BSD-3-Clause，JOSS 2024），
在其管线上叠自建模块：主轨迹选择、视角分类（帧级门控）、五项指标、参考区间判断、
报告生成、标注视频渲染、人脸打码。

## 快速开始

```bash
uv venv .venv --python 3.11
uv pip install -e .
.venv/Scripts/python.exe scripts/patch_rtmlib_dml.py   # Windows NVIDIA 卡走 DML GPU 推理
skibot analyze video.mp4 --level intermediate
```

产物在 `results/<视频名>/`：`report.json`（结构化报告）、`*_annotated.mp4`
（骨架叠加标注视频）、`*_Sports2D/`（轨迹 trc 与运动学文件）。

## 命令行

```bash
skibot analyze <video...> [--level novice|intermediate|advanced]
              [--no-blur] [--result-dir DIR] [--color pink] [--person 3] [--refs TOML]
skibot render --video v.mp4 --trc t.trc --out o.mp4 [--view sagittal|frontal]
skibot serve [--port 8000] [--host 127.0.0.1] [--result-dir web_results]
```

## Web 壳

`skibot serve` 后浏览器开 `127.0.0.1:8000`：上传页（视频 + 水平档 +
打码开关）→ 任务页（排队/分析中/完成/失败，自动刷新）→ 报告页（五指标卡、
带内百分位条、质量提示、标注视频播放）。分析在后台单线程排队；任务表
持久化到结果目录下的 `tasks.json`（`serve` 默认即 `web_results/tasks.json`，
原子写）：重启后排队任务继续执行，被中断的运行中任务标为失败、任务页
可重试，历史结果目录照旧恢复。

- 上传即预检：容器/编码、分辨率（短边 ≥480、长边 ≥720）、时长（3s~600s /
  10000 帧）、抽帧人物占比（HOG，人高 <18% 拒绝；检不出、帧率/帧数读不出、
  检测器故障都按“无法判定”放行；元数据可读但一帧都解不出按不可解码拒绝）。
  不合格返回 422、页面列出原因与重拍建议，素材不进分析队列。
  `--no-precheck` 关闭，判据见 `skibot/precheck.py`
- 报告页可改选人物：候选是已落盘的全部人物轨迹，带编号骨架预览；改选只
  重跑评估与渲染，不重跑姿态检测，单人素材不显示选择器

- 默认开启人脸打码（Haar，雪镜/面罩召回有限，见 `privacy.py` 说明）
- 长视频（>10000 帧）建议先降采样到 15fps 再分析（`ffmpeg -vf fps=15`），
  角度曲线 15Hz 对 1~2 秒一个弯的滑行够用
- 跟拍镜头（正侧面来回摆）按帧级视角门控处理：报告判 `mixed`，
  侧面指标取自侧面帧、正面指标取自正面帧
- 报告默认带"参考值未校准"水印——novice/intermediate 区间是占位值，
  待真人教练确认（评审材料见 `docs/`）

## 结构

```text
skibot/
├── cli.py            # 命令行入口，串起整个流程
├── backend.py        # Sports2D 适配：调用（DML GPU/限 8 人/7200s 超时）、产物发现、抢救契约
├── trc_io.py         # trc 格式解析 + Halpe-26 重排
├── skeleton.py       # Halpe-26 布局契约：索引、别名、肢体链、conf 语义
├── skeleton3d.py     # 三维骨架契约（root-relative/重力/置信度）+ 提升适配器接口
├── motionbert.py     # MotionBERT 三维提升适配器（issue 05）；mbmodel/ 为 vendored 模型定义
├── select_person.py  # 主轨迹选择（尺寸/位移/颜色打分）
├── view.py           # 视角分类：整视频判定 + 帧级归属（跟拍混合视角）
├── metrics.py        # 五项二维指标计算 + 指标注册表（支持帧级视角门控）
├── metrics3d.py      # 六项三维 JCS 指标 + 三点夹角辅助量（ADR-0001）
├── phases.py         # 三维侧倾过零切弯与入弯/控制/出弯相位（issue 10）
├── multiview.py      # 多机位拍手同步、标定、bundle adjustment、三角化真值（issue 06）
├── precheck.py       # 上传预检：容器/分辨率/时长/抽帧人物占比（issue 13）
├── reselect.py       # 报告页改选人物：复用落盘轨迹重跑评估与渲染（issue 16）
├── assess.py         # 评估流程：关键点 → 报告（主路径与抢救路径共用）
├── reference.py      # 三档水平参考区间加载与查询
├── report.py         # 结构化报告（质量提示：深蹲 caveat、小人物、核心点缺失；带内百分位）
├── render.py         # 自绘标注视频（只画选中者，指标按区间着色）
└── privacy.py        # 人脸打码
reference_values.toml   # 参考区间表（novice/intermediate 为占位值；advanced 为 EPFL 竞技 p25~p75）
docs/DECISIONS.md       # 全部产品与技术决策记录
docs/adr/               # 架构决策记录（三维指标定义、参考带口径）
docs/report_schema.md   # 报告 schema 迁移（0.1 → 0.2，旧名兼容）
docs/coach_review_simulated.md   # AI 模拟教练评审（待真人终审）
docs/lit_angle_values.md         # 文献角度数值汇集
docs/GLOSSARY.md        # 领域词汇表
```

## 素材工具链（scripts/）

B站公开视频的采集与分析链路（内部验证用，见 docs/DECISIONS.md 第 12 条）：
按关键词或 UP 主频道采集元数据 → 人工筛 → 下载 → 预览拼图 → 批量分析 →
trc 反查连续滑行段。入口：`fetch_user_footage.py`、`fetch_channel_footage.py`、
`download_user_footage.py`、`preview_user_footage.py`、`verify_user_footage.py`、
`find_continuous_runs.py`、`_run_batch.py`（批量驱动）。

## 验证状态

- 竞技素材（EPFL Ski2DPose，158 clip）：关节误差与五指标误差归因见 docs/DECISIONS.md 第 5 条
- 大众素材（B站，43 条分析、20+ 连续滑行段）：管线验证结论见
  `datasets/recreational_ski/recreational_validation_report.md`

## 一期范围

分析五项二维指标：屈髋角、膝弯曲角、躯干前倾角（侧面视频）；膝盖内扣角、肩髋对齐（正面视频）。
一期口径是图像平面角度，相机方位会进入读数（见 docs/DECISIONS.md #3）。

## 二期（进行中）

规格与任务票在 `.scratch/phase-2/`。指标迁到三维骨架，按入弯/控制/出弯分相位，
参考带增加相位维度；三维定义与参考带口径已冻结在 `docs/adr/`。
六项三维指标与辅助量已实现（`metrics3d.py`，issue 04），相位切分已实现
（`phases.py`，issue 10），多机位拍手同步/标定/三角化离线工具已实现
（`multiview.py`，issue 06）；评估流程传入 lifter（相位另需 fps）时同时
产出二维与三维两套结果。MotionBERT 提升适配器已接入（`motionbert.py`，
issue 05：`skibot analyze --lifter motionbert` 开启，权重离线放
`models/motionbert/`，torch 为可选依赖 `[lift3d]` extra）；三维参考带仍在
后续票里；报告的 `phases` 对象在 issue 12。Web 侧任务表持久化（issue 17）、
上传预检（issue 13）、报告页改选人物（issue 16）已完成，都不依赖三维口径。
评分制仍不做。
