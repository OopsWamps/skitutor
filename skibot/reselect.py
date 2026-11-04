"""改选人物：分析完成后从已落盘的全部人物轨迹换人，只重跑评估与渲染
（issue 16，spec「Web 与产品」）。

候选来自全部人物 px trc（backend.find_all_trc_px，Sports2D 本就产出全
人数轨迹）；改选复用同一条轨迹重跑 assess + render_annotated，不重跑
Sports2D。报告与标注视频原地覆盖，仍出自同一次评估——"报告与叠加层
一致"的契约不变。
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from . import artifacts
from . import runprep
from .backend import find_all_trc_px
from .render import _draw_skeleton
from .report_io import read_report, read_report_meta
from .trc_io import load_kp


@dataclass
class Candidate:
    pid: int
    n_valid: int   # 有任一关键点置信度 > 0 的帧数
    trc: Path


def list_candidates(work: Path) -> list[Candidate]:
    """结果子目录里全部人物轨迹 → 候选列表（pid 升序）。单人素材也返回
    这一个候选，页面据此决定要不要显示选择器。"""
    out = []
    for pid, trc in find_all_trc_px(work):
        try:
            kp = load_kp(trc)
        except Exception as e:  # noqa: BLE001 —— 单条坏轨迹不拖垮整页
            print(f"[reselect] 跳过无法解析的轨迹 {trc.name}: {e}")
            continue
        out.append(Candidate(pid=pid, trc=trc,
                             n_valid=int((kp[:, :, 2] > 0).any(axis=1).sum())))
    return out


def reselect_one(work: Path, person: int, *, level: str,
                 fallback_video: Path, refs_path: str | None = None,
                 blur: bool | None = None, lifter: str = "none") -> Path:
    """改选为 person：复用已落盘轨迹重跑评估与渲染，不重跑姿态检测。

    work 是结果子目录（result_dir/<stem>），内含全部人物 px trc；报告与
    <stem>_annotated.mp4 原地覆盖。视频源与首次分析一致：blur=True 用打码
    副本（缺了就报错），blur=False 用 fallback_video，None 时按副本是否存在
    推断。lifter 与首次分析同参（none/motionbert）：改选后的报告与
    animation.json 必须出自同一套三维设置，否则相位卡与回放卡会在改选后
    悄悄消失。返回报告路径。
    """
    tracks = dict(find_all_trc_px(work))
    if person not in tracks:
        raise ValueError(f"指定了 person {person}，但只有 {sorted(tracks)}")

    src = artifacts.source_video(work, fallback_video, blur)

    kp = load_kp(tracks[person])
    prev = artifacts.report_path(work)
    # 报告里的 "video" 是首次分析时的源标签；沿用它能保持改选前后一致。
    # 旧报告可能渲染不出（没有 view/metrics）但标签仍有效：这是元数据
    # 抢救，走 read_report_meta，不设页面判据的门槛
    label = str(fallback_video)
    if prev.exists():
        label = str(read_report_meta(prev).get("video") or label)
    # refs / fps / 身高 / lifter 的前置与首次分析同一条链（runprep）；
    # 报告与标注视频成对原子发布（评估收尾 module）：任何一步失败都保持
    # 旧人物，报告与标注视频不会错配，也不会读到半截 JSON
    runprep.publish(
        kp, src, level=level, label=label,
        report_path=prev, video_path=artifacts.annotated_path(work),
        person={"id": person, "selection": f"人工改选 person {person}"},
        refs_path=refs_path, lifter=lifter)
    return prev


def _nearest_valid_frame(kp: np.ndarray, mid: int) -> int | None:
    """离 mid 最近、且有任一关键点存在的帧号；整条轨迹无有效帧返回 None。"""
    valid = np.flatnonzero((kp[:, :, 2] > 0).any(axis=1))
    if valid.size == 0:
        return None
    return int(valid[np.abs(valid - mid).argmin()])


def current_person(work: Path) -> int | None:
    """当前报告选中的 person id；报告缺失或不可读返回 None。

    report schema 的 person 约定由改选模块认领，web 层不再自己解 JSON；
    可读判据全站唯一（report_io.read_report），与报告页同一标准。
    """
    data = read_report(artifacts.report_path(work))
    if data is None:
        return None
    person = data.get("person")
    return person.get("id") if isinstance(person, dict) else None


_preview_lock = threading.Lock()


def ensure_previews(work: Path, src: Path) -> list[Candidate]:
    """按需生成候选预览，返回最新候选列表。

    页面每次刷新都会走到这里：只在「多候选且有缺图」时才全量重生成；
    零有效帧的候选永远写不出图、不算缺图，否则每次请求都会全量重生成。
    加模块锁——两个页面并发触发时不对同一 work 目录同时写预览。
    预览只是挑选辅助，失败（打码副本丢失、容器不能 seek 等）不阻塞
    改选——缺图的候选在页面上只显示编号。
    """
    with _preview_lock:
        cands = list_candidates(work)
        if len(cands) <= 1:
            return cands
        if any(c.n_valid > 0 and not artifacts.preview_path(work, c.pid).exists()
               for c in cands):
            try:
                render_previews(work, src)
            except Exception as e:  # noqa: BLE001 —— 预览失败不阻塞改选
                print(f"[reselect] 候选预览生成失败：{type(e).__name__}: {e}")
        return cands


def render_previews(work: Path, src: Path, n_frames: int | None = None) -> list[Path]:
    """每个候选一张编号骨架预览，供报告页挑选改选对象。

    逐候选找帧：从视频中点向两侧搜该轨迹最近的有有效关键点的帧（雪场素材
    里人物常只出现半段，统一取中点会画出没有骨架的空预览）。整条轨迹零
    有效帧的候选跳过，不写空图。预览写 work/candidate_person<pid>.jpg
    （经 /files 静态挂载展示）；读帧失败返回已写出的部分，不阻塞改选。
    """
    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        return []
    total = n_frames or int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    mid = max(total // 2, 0)

    cands = list_candidates(work)
    pids = {c.pid for c in cands}
    # 人数变少时清掉已不存在候选的旧预览，静态目录里不留幽灵文件
    for stale in work.glob(artifacts.PREVIEW_GLOB):
        m = artifacts.PREVIEW_RE.fullmatch(stale.name)
        if m and int(m.group(1)) not in pids:
            stale.unlink(missing_ok=True)
    outs = []
    try:
        for cand in cands:
            kp = load_kp(cand.trc)
            out = artifacts.preview_path(work, cand.pid)
            # 骨架帧号必须落在视频实际帧数内：trc 比视频长时（抢救路径会
            # 留下短 trc，反之亦然）只在前 total 帧里找有效帧
            limit = kp.shape[0] if total <= 0 else min(kp.shape[0], total)
            f = _nearest_valid_frame(kp[:limit], min(mid, limit - 1))
            if f is None or not cap.set(cv2.CAP_PROP_POS_FRAMES, f):
                out.unlink(missing_ok=True)  # 没有有效帧/定位失败，别留旧图
                continue
            ok, frame = cap.read()
            if not ok:
                out.unlink(missing_ok=True)
                continue
            _draw_skeleton(frame, kp[f])
            cv2.putText(frame, f"person {cand.pid}", (12, 32),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, (60, 60, 230), 2,
                        cv2.LINE_AA)
            tmp_img = out.with_name(out.name + ".tmp.jpg")
            if cv2.imwrite(str(tmp_img), frame):
                try:
                    artifacts.replace_with_retry(tmp_img, out)  # 写成功才替换，不留半截
                except OSError:
                    tmp_img.unlink(missing_ok=True)
                    continue
                outs.append(out)
            else:
                tmp_img.unlink(missing_ok=True)
    finally:
        cap.release()
    return outs
