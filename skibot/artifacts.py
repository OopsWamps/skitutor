"""分析产物（artifacts）：结果目录布局与原子落盘的唯一事实源。

结果子目录 = result_dir/<视频 stem>（web 流里 stem == task_id），内含
report.json、<stem>_annotated.mp4（标注视频）、<stem>_blurred.mp4（打码
副本）、candidate_person<pid>.jpg（改选预览）。此前这套命名散在 cli /
web.tasks / web.app / reselect 四处各写一份字面量——cli 与 reselect 指向
同一个打码副本，只因 work 目录名恰好等于 stem；现在只此一处。

打码源解析（source_video）与 Windows 文件锁重试（replace_with_retry /
write_text_atomic / read_text_retry）也在这里。blur 的三态语义：
- True：必须用打码副本；副本丢了就报错，绝不静默回退到未打码原片
  （否则标注视频会把已打过码的脸重新暴露）；
- False：用上传原件，即使目录里还留着上一次打码留下的旧副本；
- None（旧任务或 CLI 未传）：用副本是否存在推断。
"""

from __future__ import annotations

import re
import time
from pathlib import Path


REPORT_NAME = "report.json"
ANIMATION_NAME = "animation.json"
PREVIEW_GLOB = "candidate_person*.jpg"


def report_path(work: Path) -> Path:
    return Path(work) / REPORT_NAME


def animation_path(work: Path) -> Path:
    """三维骨架回放数据（root-relative 逐帧 + 相位 + 序列），与报告成对发布。"""
    return Path(work) / ANIMATION_NAME


def annotated_path(work: Path) -> Path:
    return Path(work) / f"{work.name}_annotated.mp4"


def blurred_path(work: Path) -> Path:
    return Path(work) / f"{work.name}_blurred.mp4"


def preview_path(work: Path, pid: int) -> Path:
    return Path(work) / f"candidate_person{pid}.jpg"


# 预览文件名 → pid（render_previews 清理幽灵预览用）
PREVIEW_RE = re.compile(r"candidate_person(\d+)\.jpg")


def files_url(work: Path, filename: str) -> str:
    """产物在 /files 静态挂载下的地址（web 壳把 result_dir 挂在 /files）。

    目录名来自任务 id 或视频 stem（CLI 历史结果可能是任意文件名，含 & ' %
    等字符），百分号编码后再进 URL；模板 <script> 内注入的地址不解码
    HTML 实体，不编码会 404。
    """
    from urllib.parse import quote

    return f"/files/{quote(work.name)}/{quote(filename)}"


def source_video(work: Path, fallback_video: Path,
                 blur: bool | None = None) -> Path:
    """首次分析用的视频源：打过码就用打码副本，否则用上传原件。

    blur 由调用方从任务表/CLI 传入（首次分析的真实设置），三态语义见
    module docstring。改选与预览都读同一个源，保证报告与叠加层一致。
    """
    blurred = blurred_path(work)
    if blur is True:
        if not blurred.exists():
            raise FileNotFoundError(
                f"首次分析开了人脸打码，但打码副本 {blurred.name} 已丢失；"
                "请重新分析或显式传入未打码源")
        return blurred
    if blur is False:
        return fallback_video
    return blurred if blurred.exists() else fallback_video


def replace_with_retry(src: Path, dst: Path, attempts: int = 5) -> None:
    """os.replace 覆盖：Windows 上浏览器/杀毒可能短暂持有目标文件的读句柄，
    短退避重试几次再报错。"""
    for attempt in range(attempts):
        try:
            Path(src).replace(dst)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.1 * (attempt + 1))


def write_text_atomic(path: Path, text: str, attempts: int = 3) -> None:
    """文本原子落盘：写临时文件后 os.replace，进程崩溃不会留下半个文件。

    写与替换都在重试环内：Windows 上杀毒/索引器可能短暂锁住刚写出的
    文件。仍失败则清掉临时文件、抛出最后一次 OSError，由调用方决定
    放弃还是上抛。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    for attempt in range(attempts):
        try:
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(path)
            return
        except OSError:
            if attempt == attempts - 1:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.05 * (attempt + 1))


def read_text_retry(path: Path, attempts: int = 3) -> str:
    """读文本，容忍 Windows 上杀毒/索引器对文件的短暂锁定。"""
    for attempt in range(attempts):
        try:
            return Path(path).read_text(encoding="utf-8")
        except OSError as e:
            if attempt == attempts - 1:
                raise
            time.sleep(0.05 * (attempt + 1))
    raise AssertionError("unreachable")


def unlink_with_retry(path: Path, attempts: int = 3) -> None:
    """删除文件，容忍 Windows 上杀毒/索引器/静态请求对文件的短暂锁定。

    与 replace_with_retry 同一语义：重试耗尽抛最后一次 OSError，由调用方
    决定降级还是上抛——发布成功后的清理失败不该把已成功的任务标成失败。
    """
    for attempt in range(attempts):
        try:
            Path(path).unlink(missing_ok=True)
            return
        except OSError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.1 * (attempt + 1))
    raise AssertionError("unreachable")
