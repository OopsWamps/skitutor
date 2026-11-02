# 19 — 网站前端集成（skitutor）

Status: claimed
Blocked by: （无——12/15/18 的验证门前置不动，本票只做 UI 与数据落盘的提前量）

## What to build

把二期剩余的产品前端提前到验证完成之前落地，站名 skitutor：

1. 品牌与导航：站名进 base 模板（标题/页头），新增「对比」页入口。
2. 管线落盘：report.json 增加可选 `phases` 对象（弯数、fallback、按相位聚合、
   口径注记）；新增 `animation.json` 产物（root-relative 骨架逐帧 + 相位 +
   三项矢状序列），与报告同一次评估成对发布（finalize 原子替换链）。
3. Web 管线开三维：serve 层 `--lifter`（默认 motionbert）、上传表单三维开关、
   任务表持久化 lifter 字段、改选路径同参数重跑。
4. 报告页：相位卡（入弯/控制/出弯聚合，null 带原因）+ 三维骨架回放卡
   （原片与可旋转骨架共享时间轴、相位着色、指标叠加开关、来源与误差口径）。
5. 对比页：两个已完成任务按相位对比，逐项判可比性，不可比项显示原因。

## Acceptance criteria

- [x] 站名 skitutor 出现在标题与页头，导航含上传/任务/对比
- [x] 传 lifter 时 report.phases 与 animation.json 成对落盘；不传 lifter 时
      旧产物语义逐位不变
- [x] 无三维数据的旧任务：报告页不渲染相位卡与空骨架（不渲染空壳）
- [x] 回放卡与标注视频出自同一次评估；骨架 root-relative、无画面纹理
- [x] 相位卡/回放卡标注未验证口径（issue 11 未通过、issue 09 未完成）
- [x] 对比页逐项可比性判定；不可比项显示「无对比数据」+ 原因
- [x] 数据不足相位为 null 带原因，不填 0
- [x] 测试覆盖：发布成对落盘、web 流（假分析）、对比可比性逻辑；mypy 干净

## Notes

12/15/18 的验收门（advanced 三维带、验证过的误差量级、包络）保持 open，
等 09 多机位验收与 11 人工标注完成后收。本票是它们的 UI/数据提前量，
页面口径注记如实写「未验证」。来源规格：`.scratch/phase-2/spec.md`。

## Comments

### 2026-11-02 完成前端集成与数据链（12/15/18 验收门保持 open）

- **品牌与导航**：base.html 站名 skitutor、各页标题统一
  「· skitutor」；导航新增「对比」。
- **lifter 接线**：`motionbert.make_lifter` 是唯一名字→适配器工厂（CLI/
  web/reselect 共用）；serve `--lifter`（默认 motionbert）是服务级上限，
  上传表单三维开关只在允许时生效；Task 增 `lifter` 字段（旧记录按 none
  读），enqueue 校验，worker 分析与改选重跑同参。CLI analyze 行为不变。
- **管线落盘**：`finalize.build_animation` 出 `animation.json`
  （skibot.animation/0.1：root-relative 骨架 3 位小数、null=缺测，相位
  全量 to_dict，三项矢状序列，置信度，joints/edges 元数据），
  assess_and_publish 按「视频→动画→报告」顺序成对原子发布、降级二维删
  旧动画、失败回滚动画。report 顶层新增可选 `phases` 摘要（n_turns/
  fallback/aggregate/note）。`render.ensure_browser_mp4` 把 OpenCV FMP4
  产物原地转 H.264（CRF 18/yuv420p/faststart，帧数 1:1；失败降级保留
  原编码并告警，不挡发布）。
- **报告页**：相位卡（聚合表，null 带「无有效帧」+ 未验证注记）；回放卡
  仅在有 animation.json 时渲染：左视频右 canvas 骨架，拖动旋转（yaw/
  pitch）、滚轮缩放，共享 currentTime×fps 时间轴，逐帧相位着色 + 底部
  相位条带，指标叠加开关（矢状三项逐帧读数），低置信关节灰显。旧任务
  无动画 → 纯视频卡。fps 语义统一为 trc 帧率（与时间轴对齐）。
- **对比页**：/compare?a=&b= 选两个完成态任务，`web/compare.py` 纯函数
  `compare_reports`：3×3 相位行 + 全程三维行，逐项判可比，不可比项
  「无对比数据 —— 原因」，假设说明（水平/视角/数据来源差异 + 相机坡度
  未测 + 相位未验证）。
- **端到端**：真实素材（BV1LM41137hm 完整版 1232 帧 trc + MotionBERT）
  8s 全链发布：report.phases 4 弯聚合、animation.json 0.91MB、标注视频
  h264 1232 帧；真机 serve 冒烟：报告页相位卡/回放卡/口径注记齐全、
  动画 200 OK、对比页选择器与 404 语义正确。
- **边界如实记录**：低帧率素材（15fps）动画以帧率播放会慢放，属素材
  属性；竞技 60fps 素材不受影响。验收门依赖（09 误差表、11 正式判定）
  未动。

### 2026-11-02 对抗测试（4 个视角 + 逐条复核）与修复

四个视角（JS 回放卡 / 发布链 / web 路由 / 数据对齐）共出 8 条，全部复核
成立并已修；每条都有对应回归测试或复现脚本（`.scratch/repro_*.py`）：

1. **上传开关取消无效**：FastAPI `Form("on")` 缺省让未勾选也当开。改
   `Form("")`（blur / lifter），显式断言「不发字段 = 关」。
2. **坏报告打 500**：report.json 缺失、写坏、或合法 JSON 但缺 `view`/
   `metrics` 时，报告页与对比页都崩。新增 `web.app._load_report` 统一
   判读：报告页渲染任务页 + 可操作提示（500 状态码），对比页指出坏的
   是哪一侧、另一侧数据不跟着消失。
3. **播放地址未编码**：结果目录名来自视频 stem，含 `& ' %` 时 `/files`
   404；`artifacts.files_url` 改为 `quote` 编码。回放卡 `fetch` 加
   `r.ok` 检查与 `.catch`，失败在页面上写明而不是静默空画布。
4. **标注视频转码两遍**：`render_annotated` 末尾无条件 `_transcode_h264`
   （CRF 23），finalize 又对同一临时文件 `ensure_browser_mp4`（CRF 18），
   同一段视频完整有损转码两次。`render_annotated` 增 `transcode_h264`
   开关，发布链传 False；复现脚本从 2 次 ffmpeg 降为 1 次（CRF 18）。
5. **`.h264.mp4` 临时文件泄漏**：`ensure_browser_mp4` 的替换在 try 外，
   重试耗尽时临时文件留在结果目录。替换移进 try/finally，失败降级保留
   原编码；`_transcode_h264` 的裸 `replace` 同样收进 try/finally 并改用
   锁重试。复现脚本确认不再残留。
6. **降级删动画失败触发错误回滚**：旧动画被句柄锁住时，`unlink` 抛错被
   except 接住，把已发布的新视频回滚成旧的，留下「新报告 + 旧视频 + 旧
   动画」。删旧动画移出 try（发布成功后的 else），改用 `unlink_with_retry`
   告警不抛；复现脚本复现不出错配态。
7. **历史结果改选丢三维**：`_restore_reports` 重建的任务一律 lifter=none，
   改选会把三维报告降级。按报告 `data_source` 推断（motionbert → 三维）。
8. **成功发布被清理失败翻成失败**：`.bak` / 旧动画删除失败走 except，
   任务标失败且触发视频回滚。新增 `finalize._quiet_unlink`，发布成功后
   的清理只告警。

附带的防御：报告页的回放卡除动画文件存在外，还要求报告本身带 `phases`
——降级二维时旧动画若因锁没删掉，不会再被配到二维报告上。

回归：新增 `test_corrupt_report_and_restore_lifter`（坏报告 / lifter
推断）、`test_publish_with_lifter_writes_animation` 断言只转码一次、
`files_url` 编码断言；全部 19 个测试脚本通过，mypy 干净。
