# 报告 schema 迁移（0.1 → 0.2）

日期：2026-10-23。对应二期任务票 02、04。
三维六项指标列在 issue 04 接入（传入 lifter 时追加）；相位对象仍在 issue 12。

## 变化

- `schema`：`skibot.report/0.1` → `skibot.report/0.2`。
- `metrics[].metric`：`knee_valgus` → `knee_abduction`。数值不变，仍是二维
  投影口径（髋-膝-踝夹角，180° = 伸直）；三维 JCS 定义见 ADR-0001 §5.5。
- `metrics[]` 新增字段：
  - `basis`：`"2d"` 或 `"3d"`。没有 lifter 时全部是 `"2d"`；传入 lifter
    时在二维条目后追加 `"3d"`。
  - `definition`：写明口径。二维条目从注册表取 `definitions["2d"]`；
    `knee_abduction` 仍用 `"2d_projection"` 标记二维投影口径。
  - `legacy`：`true` 表示二维遗留项，没有单一三维替代。
  - `replaced_by`：遗留项被哪些三维指标取代（`shoulder_hip_align` →
    `hip_shoulder_separation` + `thoraco_pelvic_obliquity`）。
  - `note`：口径说明，给用户看的短句。
- `shoulder_hip_align` 保留为遗留项（`legacy: true`），值仍是旧合并口径。
  三维报告不再有这一项，改出横断面 H-SEP 与冠状面 obliquity 两项。
- `phases` 对象见下节（2026-11-02 起，传 lifter+fps 时写入）。

## 三维列（issue 04）

`assess(..., lifter=...)` 传入提升适配器时，`metrics[]` 在二维条目之后追加
六项 `basis: "3d"` 的条目。没有 lifter 时报告与 0.2 的二维版完全一致。

三维条目共有字段：

- `metric`、`name`、`basis: "3d"`、`definition`：规范名与 ADR-0001 口径。
- 报告顶层另有 `data_source`（`Skeleton3D.source`，如 `mock` / `motionbert`），
  只在传入 lifter 时出现，用来区分三维结果的来源。
- `reference`、`pct_in_range`、`band_percentile`、`verdict`：全部为 `null`。
  三维参考带要等 issue 09 验证与 issue 12 相位带，当前只作描述统计，不出判断。
- `median_deg`、`n_valid_frames`：全程有效帧中位数与帧数；无有效帧时
  `median_deg` 为 `null`，不写 0。
- `note`：口径与限制的短句。

分项字段：

| 指标 | 额外字段 |
|---|---|
| `hip_flexion` | `zero_deg: 180`、`helper.hip_flexion_3pt_deg`（同侧肩-髋-膝） |
| `knee_flexion` | `zero_deg: 0`、`helper.knee_interior_3pt_deg`（髋-膝-踝） |
| `trunk_lean` | `sagittal_deg`、`frontal_deg`、`total_deg`、`slope_normal_deg`、`gravity_source`、`slope_source`；`median_deg` 等于 `sagittal_deg` |
| `knee_abduction` | `left_deg`、`right_deg`、`side`；`median_deg` 是 `primary`（两侧更大值） |
| `hip_shoulder_separation` | `abs_median_deg`（带建在 \|ψ\| 上），`median_deg` 保留符号 |
| `thoraco_pelvic_obliquity` | `abs_median_deg`（带建在 \|φ\| 上），`median_deg` 保留符号 |

三维列不受视角门控：所有骨架有效帧都参与计算；视角分类只写进
`quality.view_frame_ratio`（sagittal / frontal / none 三档占比，只在传入
lifter 时出现）。缺数据帧
在 `Assessment.series3d` 里为 `NaN`，在报告条目里体现为 `median_deg: null`
与更小的 `n_valid_frames`。

`Assessment.series3d` 的键是逐帧序列，供动画与相位划分使用：
`hip_flexion`、`knee_flexion`、四个 `trunk_lean_*`、三个
`knee_abduction_*`、`hip_shoulder_separation`、`thoraco_pelvic_obliquity`、
`hip_flexion_3pt`、`knee_interior_3pt`。

`schema` 仍为 `skibot.report/0.2`；以上都是可选增量字段。读 0.2 旧报告的
代码需要容忍 `basis="3d"` 条目与 `reference: null`。

## 相位对象与 animation.json（issue 19 前置，2026-11-02）

传 lifter 且帧率已知时，report 顶层多一个可选 `phases` 摘要，同链多发布
`animation.json`（报告页回放卡的数据源，schema `skibot.animation/0.1`）：

- `phases`（report.json）：`n_turns`（合格弯数）、`fallback`（无合格弯时
  `"no_valid_turns"`）、`aggregate`（`{指标: {entry/control/exit:
  {median_deg, n_frames}}`，无合格弯为 null）、`note`（未验证口径注记）。
  逐弯明细不进报告——在 animation.json。
- `animation.json`（结果子目录，与报告同一次评估成对发布；降级二维重跑时
  删除）：`schema/fps/n_frames/source/gravity_source/note`、`joints`
  （Halpe-26 名，与 `skeleton.HALPE_NAMES` 同序）、`edges`（`skeleton.
  SKELETON_EDGES` 的索引对）、`phases`（`PhaseSegmentation.to_dict()` 全量
  含逐弯与弃弯）、`series`（三项矢状序列逐帧 1 位小数，null=缺测）、
  `confidence`（(T,26) 2 位小数）、`frames`（root-relative 骨架 3 位小数，
  null=缺测）。
- 兼容：旧报告无 `phases`、旧结果目录无 animation.json —— 报告页不渲染
  相位卡与回放卡（不渲染空壳）。
- Web 侧 `serve --lifter`（默认 motionbert）与上传表单三维开关控制是否走
  三维链；任务表 `lifter` 字段持久化，改选同参重跑。

## 字段映射

| 0.1 | 0.2 | 说明 |
|---|---|---|
| `metrics[].metric = "knee_valgus"` | `"knee_abduction"` | 旧名作别名保留 |
| — | `metrics[].basis = "2d"` | 新字段；旧报告缺失时按 `"2d"` 读 |
| — | `metrics[].definition` | 口径说明；二维列现已写入 |
| — | `metrics[].legacy` / `replaced_by` | 只有 `shoulder_hip_align` |
| — | `metrics[].note` | 口径说明 |
| — | `data_source` | 顶层；只在传入 lifter 时出现 |
| 其余字段 | 不变 | `reference`、`median_deg`、`pct_in_range`、`band_percentile`、`n_valid_frames`、`verdict` |

## 兼容

- 读旧报告：把 `knee_valgus` 当 `knee_abduction` 的别名；`basis` 缺失时按
  `"2d"` 解释。旧文件不自动改写。
- 读新报告：`basis` 缺失按 0.1 处理；`legacy` 缺失按 `false` 处理。
- 参考表查询：`reference.lookup("knee_valgus", ...)` 解析到
  `knee_abduction`；旧 TOML 只有 `[knee_valgus]` 时回退到旧键。
- `reference_values.toml` 的带全部是二维口径。三维带按 ADR-0002 在 issue 12
  生成，不写进本文件的二维节。

## 调用点

- 注册表：`skibot/metrics.py` 的 `METRICS`（六项规范指标）、`LEGACY_METRICS`
  （`shoulder_hip_align`）、`TWO_D_METRICS`（二维五键）、`metrics_for_basis`。
- 三维指标：`skibot/metrics3d.py` 的 `compute_series3d`，输入 `Skeleton3D`；
  `assess` 在传入 lifter 时调用，并放进 `Assessment.series3d`。
- 报告：`skibot/report.py` 按 `TWO_D_METRICS` 与 `metric_plane` 渲染二维列；
  `series3d` 非空时追加 `_build_metrics3d` 的六项。
- 渲染：`skibot/render.py` 同上；叠加层仍用指标键名（cv2 字体不支持中文）。
- Web 报告页：显示 `basis`、`definition`、`legacy`、`note`。
