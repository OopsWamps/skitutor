"""Repro: assess_and_publish 是否对标注视频做两次完整 H.264 转码。

render_annotated 末尾无条件 _transcode_h264（render.py:121），finalize.py:129
又对同一 tmp_video 调 ensure_browser_mp4。数一次发布触发几次 ffmpeg，并打出
每次调用的编码参数；再单独验证 _transcode_h264 的产物编码已经是 H.264。
"""
import subprocess as sp
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(r"D:\vibecoding\skibot")
sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from skibot import finalize, render  # noqa: E402
from skibot.reference import load  # noqa: E402
from skibot.skeleton import (  # noqa: E402
    L_HIP, L_KNEE, L_SHOULDER, NOSE, R_HIP, R_KNEE, R_SHOULDER,
)

REFS = load(ROOT / "reference_values.toml")

calls: list[list[str]] = []
real_run = sp.run


def counting_run(argv, *a, **kw):
    if isinstance(argv, list) and "ffmpeg" in str(argv[0]).lower():
        calls.append([str(x) for x in argv])
    return real_run(argv, *a, **kw)


sp.run = counting_run  # _transcode_h264 与 ensure_browser_mp4 共用同一模块对象


def side_kp(n: int = 12) -> np.ndarray:
    frames = []
    for _ in range(n):
        kp = np.zeros((26, 3))
        kp[L_SHOULDER] = [10, 10, 0.9]
        kp[R_SHOULDER] = [11, 10, 0.9]
        kp[L_HIP] = [11, 20, 0.9]
        kp[R_HIP] = [12, 20, 0.9]
        kp[L_KNEE] = [11, 30, 0.9]
        kp[R_KNEE] = [12, 30, 0.9]
        kp[NOSE] = [14, 6, 0.9]
        frames.append(kp)
    return np.stack(frames)


def tiny_video(path: Path, n: int = 12) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 30, (64, 48))
    assert w.isOpened()
    for _ in range(n):
        w.write(np.full((48, 64, 3), 40, np.uint8))
    w.release()
    return path


def fourcc_of(path: Path) -> str:
    cap = cv2.VideoCapture(str(path))
    v = int(cap.get(cv2.CAP_PROP_FOURCC))
    cap.release()
    return "".join(chr((v >> (8 * i)) & 0xFF) for i in range(4))


# 阶段 1：只跑 _transcode_h264，确认 render_annotated 的产物编码已是 H.264
d1 = Path(tempfile.mkdtemp(prefix="repro_2x_stage1_"))
p1 = tiny_video(d1 / "t_annotated.tmp.mp4")
print(f"stage1: before _transcode_h264 fourcc={fourcc_of(p1)!r}")
render._transcode_h264(p1)
print(f"stage1: after  _transcode_h264 fourcc={fourcc_of(p1)!r}")

# 阶段 2：端到端一次 assess_and_publish，只数本阶段的 ffmpeg 调用
base = len(calls)
d2 = Path(tempfile.mkdtemp(prefix="repro_2x_full_"))
src = tiny_video(d2 / "src.mp4")
rp, vp = d2 / "report.json", d2 / "t_annotated.mp4"
t0 = time.perf_counter()
finalize.assess_and_publish(side_kp(), src, refs=REFS, level="novice",
                            label="t.mp4", report_path=rp, video_path=vp)
elapsed = time.perf_counter() - t0
published = calls[base:]
print(f"stage2: assess_and_publish done in {elapsed:.2f}s, "
      f"ffmpeg calls={len(published)}, published fourcc={fourcc_of(vp)!r}")
for i, argv in enumerate(published):
    args = argv[1:]
    crf = [args[j + 1] for j, a in enumerate(args) if a == "-crf"]
    cv_ = args[args.index("-c:v") + 1] if "-c:v" in args else "?"
    src_arg = args[args.index("-i") + 1] if "-i" in args else "?"
    print(f"  call{i + 1}: -c:v {cv_} -crf {crf or ['(default 23)']} "
          f"input={Path(src_arg).name}")
