"""复现：降级二维删旧动画的裸 unlink 失败 → except 回滚视频，报告无法回滚。

触发路径：CLI 同目录重跑（--lifter motionbert 后不带 lifter 重跑），此刻
animation.json 被外部进程持有读句柄（杀毒/索引器/浏览器经 /files 拉取）。
Windows 上 os.unlink 对打开中的文件抛 PermissionError（replace_with_retry
的 docstring 明说这类句柄存在，模块内所有替换都有重试，唯独这个删除没有）。

预期观察到的最终磁盘状态：
  report.json = 新（二维本次评估）  <- 已替换，无备份无法回滚
  标注视频     = 旧（上一次三维评估的叠加）  <- except 块用 .bak 回滚
  animation.json = 旧（上一次三维运行残留）  <- 删除失败
即模块 docstring 承诺要防的「报告与视频错配」状态。
"""

import hashlib
import json
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skibot.finalize import assess_and_publish  # noqa: E402
from skibot.reference import load  # noqa: E402
from skibot.skeleton import L_HIP, L_KNEE, L_SHOULDER, NOSE  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REFS = load(ROOT / "reference_values.toml")


def side_kp(n: int = 12, dx: float = 0.0):
    import numpy as np
    frames = []
    for _ in range(n):
        kp = np.zeros((26, 3))
        kp[L_SHOULDER] = [10 + dx, 10, 0.9]
        kp[L_HIP] = [11 + dx, 20, 0.9]
        kp[L_KNEE] = [11 + dx, 30, 0.9]
        kp[NOSE] = [14 + dx, 6, 0.9]
        frames.append(kp)
    return np.stack(frames)


def tiny_video(path: Path, n: int = 12) -> Path:
    import cv2
    path.parent.mkdir(parents=True, exist_ok=True)
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 30, (64, 48))
    assert w.isOpened()
    for _ in range(n):
        w.write(np.full((48, 64, 3), 40, np.uint8))
    w.release()
    return path


import numpy as np  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="skibot_repro_downgrade_unlink_"))
src = tiny_video(tmp / "src.mp4")
rp, vp = tmp / "report.json", tmp / "v_annotated.mp4"

# 第 1 步：三维发布，animation.json 落盘
assess_and_publish(side_kp(), src, refs=REFS, level="novice",
                   label="run1-3d", report_path=rp, video_path=vp,
                   fps=30.0, lifter=None)  # 先用 lifter=None 占位看行为
anim1 = (tmp / "animation.json").exists()
print(f"[1] lifter=None 首次发布：animation.json 存在 = {anim1}")
if not anim1:
    from skibot.skeleton3d import MockLifter
    assess_and_publish(side_kp(), src, refs=REFS, level="novice",
                       label="run1-3d", report_path=rp, video_path=vp,
                       fps=30.0, lifter=MockLifter())
    print(f"[1b] MockLifter 重新发布：animation.json 存在 = "
          f"{(tmp / 'animation.json').exists()}")

old_video_bytes = vp.read_bytes()
old_video_sha = hashlib.sha256(old_video_bytes).hexdigest()[:12]
old_report = json.loads(rp.read_text(encoding="utf-8"))
print(f"[2] 旧报告 video = {old_report.get('video')}，"
      f"旧视频 sha = {old_video_sha}")

# 第 3 步：外部进程持有 animation.json 读句柄（同进程句柄在 unlink 语义上
# 与杀毒/浏览器句柄一致：无 FILE_SHARE_DELETE 就删不掉）。
# 第 4 步用位移过的骨架（dx=5）重跑，让本次渲染的视频字节与旧的不同，
# 「回滚成旧视频」与「新渲染恰好相同字节」可区分。
lock_fh = open(tmp / "animation.json", "rb")
try:
    # 降级二维重跑（不带 lifter）→ 发布成功后 unlink 旧动画
    try:
        assess_and_publish(side_kp(dx=5.0), src, refs=REFS, level="novice",
                           label="run2-2d", report_path=rp, video_path=vp,
                           fps=30.0)
        print("[4] 降级发布未抛错（句柄未锁住文件，复现不成立）")
    except PermissionError as e:
        print(f"[4] 降级发布抛出 PermissionError：{e}")
    except Exception as e:  # noqa: BLE001
        print(f"[4] 降级发布抛出 {type(e).__name__}: {e}")
finally:
    lock_fh.close()

# 第 5 步：检查最终磁盘状态
new_report = json.loads(rp.read_text(encoding="utf-8"))
video_now_sha = hashlib.sha256(vp.read_bytes()).hexdigest()[:12]
anim_still = (tmp / "animation.json").exists()
bak_exists = (tmp / "v_annotated.mp4.bak").exists()
print("\n===== 最终磁盘状态 =====")
print(f"report.json video      = {new_report.get('video')}  "
      f"（run2-2d = 新报告；run1-3d = 旧报告）")
print(f"标注视频 sha           = {video_now_sha}  "
      f"（旧 {old_video_sha}；相同 = 视频被回滚成旧产物）")
print(f"animation.json 残留    = {anim_still}  "
      f"（True = 旧三维动画未被删除）")
print(f".bak 残留              = {bak_exists}")
mismatch = (new_report.get("video") == "run2-2d" and video_now_sha == old_video_sha
            and anim_still)
print(f"\n错配态成立（新报告 + 旧视频 + 旧动画）: {mismatch}")
print(f"临时目录：{tmp}")
