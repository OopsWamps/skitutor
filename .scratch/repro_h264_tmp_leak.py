"""Repro: does a failed final replace in ensure_browser_mp4 / _transcode_h264
leave the .h264.mp4 temp file behind, and does assess_and_publish's finally
fail to clean it?

Mode 1: artifacts.replace_with_retry raises PermissionError after 5 exhausted
attempts (artifacts.py:90-92 `raise`). Simulate the exhausted state on the
ensure_browser_mp4 call only, run assess_and_publish end to end, then look at
the work dir after its finally block.

Mode 2: _transcode_h264's bare `tmp.replace(out_path)` (render.py:146) with a
real external handle held on the destination (what an AV/indexer reader does:
open without FILE_SHARE_DELETE). No retry at all on that call.
"""
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(r"D:\vibecoding\skibot")
sys.path.insert(0, str(ROOT))

import cv2
import numpy as np

from skibot import artifacts, finalize, render
from skibot.reference import load

REFS = load(ROOT / "reference_values.toml")


def tiny_video(path: Path, n: int = 12) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 30, (64, 48))
    assert w.isOpened()
    for _ in range(n):
        w.write(np.full((48, 64, 3), 40, np.uint8))
    w.release()
    return path


def mode1_ensure_browser_mp4_leaks_on_exhausted_retry():
    d = Path(tempfile.mkdtemp(prefix="repro_m1_"))
    p = tiny_video(d / "x_annotated.tmp.mp4")
    real = artifacts.replace_with_retry

    def exhausted(src, dst, attempts=5):  # what the real one does on attempt 5
        raise PermissionError(f"[repro] simulated exhausted retries: {src} -> {dst}")

    artifacts.replace_with_retry = exhausted
    try:
        try:
            render.ensure_browser_mp4(p)
            print("MODE1: no exception (unexpected)")
        except PermissionError as e:
            print(f"MODE1: PermissionError propagated out of ensure_browser_mp4: {e}")
    finally:
        artifacts.replace_with_retry = real

    leaked = p.with_name(p.name + ".h264.mp4")
    print(f"MODE1: leaked tmp exists after raise: {leaked.exists()} ({leaked.name})")

    # simulate assess_and_publish's finally: it only unlinks these three names
    tmp_video = p
    tmp_anim = d / "animation.json.tmp"
    tmp_report = d / "report.json.tmp"
    tmp_video.unlink(missing_ok=True)
    tmp_anim.unlink(missing_ok=True)
    tmp_report.unlink(missing_ok=True)
    print(f"MODE1: after finally-style cleanup, leaked file still there: {leaked.exists()}")


def mode1_full_assess_and_publish():
    d = Path(tempfile.mkdtemp(prefix="repro_m1full_"))
    src = tiny_video(d / "src.mp4")
    rp, vp = d / "report.json", d / "t_annotated.mp4"
    real = artifacts.replace_with_retry

    def picky(src, dst, attempts=5):
        if str(src).endswith(".tmp.mp4.h264.mp4"):  # ensure_browser_mp4's call
            raise PermissionError(f"[repro] exhausted retries: {src} -> {dst}")
        real(src, dst, attempts)

    finalize.artifacts.replace_with_retry = picky
    try:
        try:
            finalize.assess_and_publish(
                np.stack([np.array([[10, 10, .9]] * 26).reshape(26, 3) for _ in range(12)]),
                src, refs=REFS, level="novice", label="t.mp4",
                report_path=rp, video_path=vp)
            print("MODE1FULL: no exception (unexpected)")
        except Exception as e:
            print(f"MODE1FULL: assess_and_publish raised {type(e).__name__} (task fails, old pair kept)")
    finally:
        finalize.artifacts.replace_with_retry = real

    leftover = [q.name for q in d.iterdir() if ".h264.mp4" in q.name]
    print(f"MODE1FULL: work dir after finally: {sorted(q.name for q in d.iterdir())}")
    print(f"MODE1FULL: h264 tmp leftovers: {leftover}")


def mode2_transcode_h264_bare_replace():
    d = Path(tempfile.mkdtemp(prefix="repro_m2_"))
    out = tiny_video(d / "t_annotated.tmp.mp4")
    stop = threading.Event()

    def hold():  # external reader: python open() has no FILE_SHARE_DELETE
        fh = open(out, "rb")
        stop.wait()
        fh.close()

    t = threading.Thread(target=hold, daemon=True)
    t.start()
    time.sleep(0.2)
    try:
        try:
            render._transcode_h264(out)
            print("MODE2: replace succeeded despite held handle (unexpected)")
        except PermissionError as e:
            print(f"MODE2: PermissionError from bare tmp.replace: winerror={e.winerror}")
            leaked = out.with_suffix(".h264.mp4")
            print(f"MODE2: leaked tmp exists after raise: {leaked.exists()} ({leaked.name})")
    finally:
        stop.set()
        t.join()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    mode1_ensure_browser_mp4_leaks_on_exhausted_retry()
    mode1_full_assess_and_publish()
    mode2_transcode_h264_bare_replace()
