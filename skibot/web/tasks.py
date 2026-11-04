"""任务表与后台执行：分析任务排队进 analyze_one、改选任务经 start_reselect
入队，单工作线程串行执行（改选优先——它只重跑评估与渲染，且用户正等结果）。

分析是分钟级 CPU 任务（Sports2D 子进程 + DML 推理），并发只会互相拖慢，
内部工具单线程排队足够。任务元数据（含改选子状态）原子写到
result_dir/tasks.json：重启后排队任务重新入队继续执行，运行中被中断的标
为失败、任务页可重试，已完成任务照旧可查看（状态 RUNNING 但报告已写完的
恢复为完成）；改选跑到一半重启的复位改选状态并标中断原因；JSON 缺失或
损坏时回退到扫描结果目录的 report.json，不阻塞启动。启动时回收孤儿上传
目录。分析函数可构造注入（analyze_fn），测试不跑 Sports2D。

结构上的三条纪律：
- 状态转移规则只有一张表（_transition）：Task 状态字段的每一次变更都经
  它的事件（requeue/start/complete/fail/reselect_start/reselect_finish），
  重启对账（_reconcile）也复用同一批事件，不另写一套字段赋值。
- 对外视图只有一个序列化出口（task_view）：API 与任务页模板消费同一份
  字典，显示口径（状态文案、档位中文名、时间格式化）也定在这里。
- 时钟可注入（clock），工作线程可不启（start_worker=False + run_pending
  同步驱动）：测试不再依赖 sleep 轮询与线程时序。
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .. import artifacts
from ..cli import AnalyzeOptions, analyze_one
from ..reference import LEVEL_CN
from ..reselect import reselect_one
from ..report_io import read_report_meta

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"

# 状态 → 显示文案：任务页模板与 JS 的唯一来源
STATUS_LABELS = {
    STATUS_QUEUED: "排队中",
    STATUS_RUNNING: "分析中…",
    STATUS_DONE: "完成",
    STATUS_FAILED: "失败",
}

STORE_VERSION = 1


@dataclass
class RetryResult:
    """retry() 的锁内快照：task 为 None 表示不存在；view 是同一时刻的
    序列化视图，API 直接返回它，不受 worker 在锁释放后抢跑的影响。"""

    task: Task | None
    requeued: bool
    status: str
    error: str
    view: dict | None = None


@dataclass
class Task:
    id: str
    video_path: Path   # 上传落盘位置（uploads/<id>/<id>.<ext>）
    video_name: str
    level: str
    blur: bool | None  # None = 未知（从 report.json 重建的旧任务）
    lifter: str = "none"  # 三维提升档（none/motionbert），改选重跑同参
    status: str = STATUS_QUEUED
    error: str = ""
    report_path: Path | None = None
    created_at: datetime = field(default_factory=datetime.now)
    started_at: datetime | None = None
    # 改选人物子状态：随任务表持久化，重启不丢（此前是 web 层手搓的
    # set+dict+裸线程影子状态机）。reselecting=True 意味着 worker 正在跑
    # 改选，报告与标注视频即将被成对替换。
    reselecting: bool = False
    reselect_error: str = ""

    @property
    def done(self) -> bool:
        """「完成且可查看」的唯一判据：状态 done 且报告文件在表。"""
        return self.status == STATUS_DONE and self.report_path is not None


def _transition(task: Task, event: str, *, report_path: Path | None = None,
                error: str = "", now: datetime | None = None) -> None:
    """唯一的转移规则表：Task 状态字段的每一次变更都从这里过。

    事件 → 字段规则：
    - requeue         → queued，清错误与开始时间（重试的重新入队）
    - start           → running，记 started_at（worker 取到排队分析）
    - complete        → done，记 report_path（分析成功 / mark_done /
                        重启发现报告已写完）
    - fail            → failed，记 error，清开始时间（分析异常 / 取消 /
                        视频丢失 / 重启中断）
    - reselect_start  置 reselecting，清改选错误
    - reselect_finish 清 reselecting，记改选错误（空串 = 成功）

    调用方负责持锁与 _save；这里只改内存字段，不做任何 IO。
    """
    if event == "requeue":
        task.status = STATUS_QUEUED
        task.error = ""
        task.started_at = None
    elif event == "start":
        task.status = STATUS_RUNNING
        task.started_at = now or datetime.now()
    elif event == "complete":
        task.status = STATUS_DONE
        task.error = ""
        if report_path is not None:
            task.report_path = report_path
    elif event == "fail":
        task.status = STATUS_FAILED
        task.error = error
        task.started_at = None
    elif event == "reselect_start":
        task.reselecting = True
        task.reselect_error = ""
    elif event == "reselect_finish":
        task.reselecting = False
        task.reselect_error = error
    else:
        raise ValueError(f"未知任务事件: {event}")


def _reconcile(task: Task, result_dir: Path) -> Task | None:
    """重启对账：tasks.json 快照 + 盘上文件事实 → 一致的内存状态。

    返回 None 表示丢弃该条。规则与 worker 的写回共用 _transition 的事件，
    不另写一套字段赋值。
    """
    if task.reselecting:
        # 改选跑到一半服务重启：报告与视频是成对原子替换的，仍是旧人物的
        # 有效产物；标中断原因，用户可再点一次改选
        _transition(task, "reselect_finish",
                    error="服务重启时改选被中断，可重试")
    if task.status == STATUS_RUNNING:
        # RUNNING 落盘但报告已写完：上一次的 _save 失败（文件被锁）才留下
        # 这种状态，报告是有效的，恢复成完成而不是当失败重跑
        rp = task.report_path
        if rp is None or not rp.exists():
            guess = artifacts.report_path(result_dir / task.id)
            rp = guess if guess.exists() else None
        if rp is not None:
            _transition(task, "complete", report_path=rp)
        else:
            _transition(task, "fail", error="服务重启时任务被中断，可重试")
    elif task.status == STATUS_DONE and (
            task.report_path is None or not task.report_path.exists()):
        # 已完成但报告文件已被删：跳过，与旧的重启行为一致
        return None
    elif task.status == STATUS_QUEUED and not task.video_path.exists():
        # 排队但视频已丢：标失败，不让 worker 去跑一个必然失败的子进程
        _transition(task, "fail", error="上传的视频文件已丢失，无法执行")
    return task


def task_view(t: Task, *, queue_position: int | None = None,
              now: datetime | None = None) -> dict:
    """Task → 对外视图（API 与任务页模板共用的唯一序列化出口）。

    显示口径也在这里定：状态文案、档位中文名、时间格式化。新增字段只进
    这一个函数，模板与 JS 只消费，不再各自推演状态。
    """
    if now is None:
        now = datetime.now()
    elapsed = None
    if t.status == STATUS_RUNNING and t.started_at is not None:
        elapsed = max(0, int((now - t.started_at).total_seconds()))
    return {
        "id": t.id,
        "video": t.video_name,
        "level": t.level,
        "level_label": LEVEL_CN.get(t.level, t.level),
        "status": t.status,
        "status_label": STATUS_LABELS.get(t.status, t.status),
        "error": t.error,
        "created": t.created_at.isoformat(timespec="seconds"),
        "created_short": f"{t.created_at:%m-%d %H:%M}",
        "started": (t.started_at.isoformat(timespec="seconds")
                    if t.started_at else None),
        "elapsed_sec": elapsed,
        "queue_position": queue_position,
        "reselecting": t.reselecting,
        "reselect_error": t.reselect_error or None,
        "report_url": f"/report/{t.id}" if t.done else None,
    }


def _task_dump(t: Task) -> dict:
    return {
        "id": t.id,
        "video_path": str(t.video_path),
        "video_name": t.video_name,
        "level": t.level,
        "blur": t.blur,
        "lifter": t.lifter,
        "status": t.status,
        "error": t.error,
        "report_path": str(t.report_path) if t.report_path else None,
        "created_at": t.created_at.isoformat(),
        "started_at": t.started_at.isoformat() if t.started_at else None,
        "reselecting": t.reselecting,
        "reselect_error": t.reselect_error,
    }


VALID_STATUSES = (STATUS_QUEUED, STATUS_RUNNING, STATUS_DONE, STATUS_FAILED)
VALID_LEVELS = ("novice", "intermediate", "advanced")
VALID_LIFTERS = ("none", "motionbert")


def _task_load(d: dict) -> Task:
    """一条 tasks.json 记录 → Task。字段类型不对抛 ValueError，由调用方
    跳过该条：手改或外部写入的坏记录不能把启动流程带崩（比如 id 是列表
    时，后面的字典查找会抛 unhashable）。"""
    if not isinstance(d, dict):
        raise ValueError("任务记录不是对象")
    tid = d.get("id")
    video_path = d.get("video_path")
    video_name = d.get("video_name")
    level = d.get("level")
    status = d.get("status")
    if not isinstance(tid, str) or not tid:
        raise ValueError("id 不是非空字符串")
    if not isinstance(video_path, str) or not video_path:
        raise ValueError("video_path 不是非空字符串")
    if not isinstance(video_name, str):
        raise ValueError("video_name 不是字符串")
    if status not in VALID_STATUSES:
        raise ValueError(f"未知状态 {status!r}")
    if level not in VALID_LEVELS:
        level = "intermediate"  # 水平档坏了不至于丢整条任务

    created = d.get("created_at")
    try:
        created_at = datetime.fromisoformat(created) if created else datetime.now()
    except (TypeError, ValueError):
        created_at = datetime.now()
    started = d.get("started_at")
    try:
        started_at = datetime.fromisoformat(started) if started else None
    except (TypeError, ValueError):
        started_at = None
    report = d.get("report_path")
    if report is not None and not isinstance(report, str):
        raise ValueError("report_path 不是字符串")
    blur = d.get("blur")
    if blur is not None and not isinstance(blur, bool):
        blur = None  # 不把 "false" 之类的字符串强转成 True，按未知处理
    reselecting = d.get("reselecting")
    reselecting = bool(reselecting) if isinstance(reselecting, bool) else False
    reselect_error = d.get("reselect_error")
    reselect_error = str(reselect_error) if isinstance(reselect_error, str) else ""
    lifter = d.get("lifter")
    # 旧记录没有 lifter 字段（二期前的二维任务）：按 none 读，重跑/改选
    # 仍走二维，不会把旧任务悄悄升级成三维
    lifter = lifter if lifter in VALID_LIFTERS else "none"
    return Task(
        id=tid, video_path=Path(video_path),
        video_name=video_name, level=level, blur=blur, lifter=lifter,
        status=status, error=str(d.get("error", "")),
        report_path=Path(report) if report else None, created_at=created_at,
        started_at=started_at,
        reselecting=reselecting, reselect_error=reselect_error)


class TaskBoard:
    """内存任务表 + JSON 持久化 + 单工作线程。enqueue 前调用方已把视频落盘。

    analyze_fn 可注入（测试用假分析替身，不用猴补模块属性）；clock 可注入
    （默认 datetime.now）；start_worker=False 时不启线程，测试用 run_pending
    同步驱动，转移规则与持久化走和 worker 完全相同的代码。
    """

    def __init__(self, result_dir: Path, *,
                 analyze_fn=None, clock=None, start_worker: bool = True):
        # 统一绝对路径：tasks.json 里存的是绝对路径，换个工作目录启动
        # （比如从项目根换成等价的绝对 --result-dir）不会把队列读丢
        self.result_dir = Path(result_dir).resolve()
        # None 哨兵而非默认参数：未注入时 worker 在调用时才解析模块命名
        # 空间里的 analyze_one，测试猴补模块属性（或直接注入）都生效
        self._analyze_fn = analyze_fn
        self._clock = clock or datetime.now
        self._store = self.result_dir / "tasks.json"
        self._tasks: dict[str, Task] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)
        self._stopping = False
        self._store_unreadable = False
        self._reselect_jobs: list[tuple[str, int]] = []
        self._load()
        self._restore_reports()
        self._cleanup_uploads()
        self._worker: threading.Thread | None = None
        if start_worker:
            self._worker = threading.Thread(target=self._loop, daemon=True)
            self._worker.start()

    def stop(self) -> None:
        """停止工作线程（测试与优雅关停用）。

        正在跑的分析不被打断，但结束后不再落盘、不再接新任务——旧实例
        若继续写表，会用过期快照覆盖新实例刚写的 tasks.json。
        """
        self._stopping = True
        with self._cv:
            self._cv.notify_all()
        if self._worker is not None:
            self._worker.join(timeout=0.2)

    def _load(self) -> None:
        """从 tasks.json 恢复任务元数据；文件缺失或损坏时不阻塞启动。

        排队任务保持排队（worker 会接手继续执行）；运行中任务说明上次
        服务重启时被中断，标为失败等待手动重试；已完成但报告文件已被删
        的任务跳过，与旧的重启行为一致；排队但视频文件已丢的标为失败，
        不让 worker 去跑一个必然失败的子进程。逐条规则在 _reconcile。
        """
        if not self._store.exists():
            return
        # 读失败（Windows 上杀毒/索引器短暂锁文件）与内容损坏要分开：
        # 读失败时保留原文件，不能被随后的扫描重建覆盖掉排队任务
        try:
            text = artifacts.read_text_retry(self._store)
        except OSError as e:
            print(f"[tasks] {self._store.name} 暂时无法读取（{e}），"
                  "本次不恢复也不覆盖该文件")
            self._store_unreadable = True
            return
        # 合法 JSON 但形状不对（tasks 为 null/数字/对象）与解析失败同样
        # 按损坏处理：启动不能因为一份坏文件崩在 create_app 里
        try:
            entries = json.loads(text)["tasks"]
            if not isinstance(entries, list):
                raise TypeError("tasks 不是列表")
        except (ValueError, TypeError, KeyError):
            print(f"[tasks] {self._store.name} 无法解析，忽略持久化状态，"
                  "改用结果目录扫描重建")
            return
        for d in entries:
            try:
                t = _task_load(d)
            except Exception:
                print("[tasks] 跳过一条无法解析的任务记录")
                continue
            if t.id in self._tasks:
                continue
            t = _reconcile(t, self.result_dir)
            if t is None:
                continue
            self._tasks[t.id] = t
            self._order.append(t.id)

    def _restore_reports(self) -> None:
        """兜底重建：结果目录里有 report.json 但不在 tasks.json 里的任务
        （历史结果、持久化文件损坏前写入的），按原行为重建为完成态。

        任务 id 取视频 stem（与 report.json 所在目录同名）；上传期任务
        （uploads/ 下）没有 report.json，不会在这里出现。只在 __init__
        中、worker 启动前调用；仍按 _save 的约定持锁操作。报告元数据走
        read_report_meta（恢复是元数据抢救，不设页面渲染判据）。
        """
        with self._lock:
            for rp in sorted(self.result_dir.glob(f"*/{artifacts.REPORT_NAME}")):
                stem = rp.parent.name
                if stem in self._tasks:
                    continue
                data = read_report_meta(rp)
                level = data.get("level", "intermediate")
                # 报告没记打码设置，blur=None：改选时按打码副本是否存在推断。
                # data_source 记了三维来源的，改选重跑要带同一个 lifter——
                # 一律按 none 会让改选把三维报告悄悄降级成二维
                lifter = ("motionbert" if data.get("data_source") == "motionbert"
                          else "none")
                task = Task(id=stem,
                            video_path=self._locate_video(stem, rp, data),
                            video_name=stem, level=level, blur=None,
                            lifter=lifter,
                            status=STATUS_DONE, report_path=rp)
                self._tasks[stem] = task
                self._order.append(stem)
            if not self._store_unreadable:
                self._save()

    def _locate_video(self, stem: str, report: Path, data: dict) -> Path:
        """恢复任务时找回源视频：报告里的 video → uploads/<id>/ → 结果目录猜名。

        上传落盘件在 uploads/<id>/<id>.<ext>，不在结果目录里；只按猜名会
        让改选与预览在恢复后全部打不开视频。
        """
        src = data.get("video")
        if isinstance(src, str) and src:
            cand = Path(src)
            if not cand.is_absolute():
                cand = cand.resolve()
            if cand.is_file():
                return cand
        uploads = self.result_dir / "uploads" / stem
        if uploads.is_dir():
            for p in sorted(uploads.iterdir()):
                if p.is_file() and p.suffix.lower() in (".mp4", ".mov",
                                                        ".m4v", ".avi"):
                    return p
        guess = report.parent / f"{stem}.mp4"
        return guess

    def _cleanup_uploads(self) -> None:
        """回收孤儿上传目录：uploads/<id> 既不在任务表里、也没有同名结果
        目录（从报告重建的旧任务会把源视频留在 uploads/，同名 work 目录
        在恢复时已进表），就是上次进程在落盘后、入队前中断留下的残骸，
        启动时清掉——此前没有任何人回收它们。"""
        uploads = self.result_dir / "uploads"
        if not uploads.is_dir():
            return
        for d in sorted(uploads.iterdir()):
            if not d.is_dir():
                continue
            if d.name in self._tasks or (self.result_dir / d.name).is_dir():
                continue
            shutil.rmtree(d, ignore_errors=True)
            print(f"[tasks] 回收孤儿上传目录 uploads/{d.name}")

    def enqueue(self, task_id: str, video_path: Path, video_name: str,
                level: str, blur: bool, lifter: str = "none") -> Task:
        if lifter not in VALID_LIFTERS:
            raise ValueError(f"未知 lifter: {lifter}，可选 {VALID_LIFTERS}")
        task = Task(id=task_id, video_path=video_path, video_name=video_name,
                    level=level, blur=blur, lifter=lifter,
                    created_at=self._clock())
        with self._cv:
            self._tasks[task_id] = task
            self._order.append(task_id)
            self._save()
            self._cv.notify()
        return task

    def retry(self, task_id: str) -> RetryResult:
        """失败任务重新入队，返回锁内快照（含序列化视图）。

        任务不存在时 task 为 None；非失败任务原样返回。调用方不要在锁外
        再看 task 的 status/error——worker 可能已经把入队任务改成 running，
        view 是锁内取的那一份，API 直接返回它。复用原视频与 level/blur 设置。
        """
        with self._cv:
            task = self._tasks.get(task_id)
            if task is None:
                return RetryResult(None, False, "", "", None)
            if task.status != STATUS_FAILED:
                return RetryResult(task, False, task.status, task.error,
                                   task_view(task, now=self._clock()))
            if not task.video_path.exists():
                # 重试也救不回来：直接给出可操作的提示，别让用户
                # 在"失败→重试→再失败"的循环里空转
                _transition(task, "fail", error="源视频已丢失，请重新上传")
                self._save()
                return RetryResult(task, False, task.status, task.error,
                                   task_view(task, now=self._clock()))
            _transition(task, "requeue")
            self._save()
            self._cv.notify()
            return RetryResult(task, True, STATUS_QUEUED, "",
                               task_view(task, now=self._clock()))

    def cancel(self, task_id: str) -> bool:
        """取消排队中的任务；运行中的分析不可中断，返回 False。"""
        with self._cv:
            task = self._tasks.get(task_id)
            if task is None or task.status != STATUS_QUEUED:
                return False
            _transition(task, "fail", error="已取消")
            self._save()
            return True

    def get(self, task_id: str) -> Task | None:
        with self._lock:
            return self._tasks.get(task_id)

    def all(self) -> list[Task]:
        with self._lock:
            return [self._tasks[i] for i in reversed(self._order)]

    def view(self, task_id: str) -> dict | None:
        """单个任务的序列化视图（锁内快照，含队列位置）；不存在返回 None。"""
        with self._lock:
            t = self._tasks.get(task_id)
            if t is None:
                return None
            return task_view(t, queue_position=self._positions_locked().get(task_id),
                             now=self._clock())

    def views(self) -> list[dict]:
        """全部任务的序列化视图（新→旧）。锁内快照，队列位置一并算好——
        API 与任务页模板消费同一份，不再各拼一遍。"""
        with self._lock:
            pos = self._positions_locked()
            return [task_view(self._tasks[i], queue_position=pos.get(i),
                              now=self._clock())
                    for i in reversed(self._order)]

    def _positions_locked(self) -> dict[str, int]:
        """排队任务在队列中的 1-based 位置；运行中的任务不计入。"""
        pos: dict[str, int] = {}
        n = 0
        for tid in self._order:
            t = self._tasks.get(tid)
            if t is not None and t.status == STATUS_QUEUED:
                n += 1
                pos[tid] = n
        return pos

    def mark_done(self, task_id: str, report_path: Path) -> None:
        """直接标记完成（测试与外部工具注入用）：报告文件须已存在。

        此前测试只能野改 Task 字段——任务表没有注入完成态的公开缝。
        """
        with self._lock:
            t = self._tasks.get(task_id)
            if t is None:
                raise KeyError(task_id)
            _transition(t, "complete", report_path=Path(report_path))
            self._save()

    def start_reselect(self, task_id: str, person: int) -> bool:
        """登记一次改选：置子状态并入队，worker 串行执行。

        任务不存在或已在改选中返回 False（调用方负责区分 404/409——
        任务是否可改选的判据是 Task.done，由调用方先行检查）。
        """
        with self._cv:
            t = self._tasks.get(task_id)
            if t is None or t.reselecting:
                return False
            _transition(t, "reselect_start")
            self._reselect_jobs.append((task_id, person))
            self._save()
            self._cv.notify()
        return True

    def run_pending(self, timeout: float = 5.0) -> bool:
        """同步执行一个待办（改选优先，其次最早的排队分析），返回是否执行。

        start_worker=False 的测试用它在确定性时序下驱动任务表：不靠线程
        调度与轮询，取任务、转移规则、持久化与 worker 走同一段代码。
        timeout 内没有待办返回 False。
        """
        with self._cv:
            job = self._cv.wait_for(self._take_job_locked, timeout)
        if job is None:
            return False
        task, person = job
        if person is None:
            self._run_analysis(task)
        else:
            self._run_reselect(task.id, person)
        return True

    def _save(self) -> None:
        """任务表整体原子落盘：写临时文件后 os.replace，进程崩溃不会留下
        半个文件。调用方需已持有 self._lock（_cv 的锁）。

        Windows 上杀毒/索引器可能短暂锁住刚写出的文件，重试两次短退避；
        仍失败则放弃本次落盘（内存任务表不受影响，下次状态变更重写）。
        """
        if self._store_unreadable:
            self._recover_unreadable_store()
            if self._store_unreadable:
                return  # 原文件仍不可读且无法备份：宁可不写，不覆盖队列
        payload = {"version": STORE_VERSION,
                   "tasks": [_task_dump(self._tasks[i]) for i in self._order]}
        try:
            artifacts.write_text_atomic(
                self._store, json.dumps(payload, ensure_ascii=False, indent=1))
        except OSError as e:
            print(f"[tasks] 持久化写入失败（内存任务表不受影响）: {e}")

    def _recover_unreadable_store(self) -> None:
        """启动时读不出来的 tasks.json，在下一次落盘前再试一次。

        读得出来就合并进内存（_load 会跳过已有 id）；仍读不出来则把原文件
        改名备份成 tasks.json.unreadable 再写新表——直接覆盖会把排队任务
        永久丢掉。备份也失败时保持标志，调用方跳过本次落盘。
        """
        self._store_unreadable = False
        self._load()
        if not self._store_unreadable:
            return
        backup = self._store.with_name(self._store.name + ".unreadable")
        try:
            os.replace(self._store, backup)
            print(f"[tasks] 原 {self._store.name} 无法读取，已备份为 "
                  f"{backup.name}，用当前内存表重建")
            self._store_unreadable = False
        except OSError as e:
            print(f"[tasks] {self._store.name} 仍不可读且无法备份（{e}），"
                  "本次不落盘以免覆盖排队任务")
            self._store_unreadable = True

    def _take_job_locked(self):
        """取下一个待办并置为运行中；无待办返回 None。调用方需持有 _cv。

        改选优先于排队分析：用户正盯着报告页等结果，且改选只重跑评估与
        渲染，比分钟级的分析快得多。
        """
        if self._reselect_jobs:
            task_id, person = self._reselect_jobs.pop(0)
            task = self._tasks[task_id]
            _transition(task, "reselect_start")  # 幂等：登记时已置位
            self._save()
            return task, person
        todo = [i for i in self._order
                if self._tasks[i].status == STATUS_QUEUED]
        if todo:
            task = self._tasks[todo[0]]
            _transition(task, "start", now=self._clock())
            self._save()
            return task, None
        return None

    def _loop(self) -> None:
        while True:
            with self._cv:
                job = None
                while job is None:
                    if self._stopping:
                        return
                    job = self._take_job_locked()
                    if job is None:
                        self._cv.wait()
            task, person = job
            if person is None:
                self._run_analysis(task)
            else:
                self._run_reselect(task.id, person)

    def _run_analysis(self, task: Task) -> None:
        try:
            # blur=None = 打码设置未知（手改的 tasks.json、坏字段降级）。
            # 按未知状态分析会以不打码跑、还会删掉已有的打码副本，拒绝执行：
            # 残缺产物清场（旧 trc）由 analyze_one 负责，属于管线层职责
            if task.blur is None:
                raise RuntimeError("任务缺打码设置（blur 未知），请重新上传分析")
            opts = AnalyzeOptions(level=task.level, blur=bool(task.blur),
                                  lifter=task.lifter,
                                  result_dir=self.result_dir)
            fn = self._analyze_fn if self._analyze_fn is not None else analyze_one
            report = fn(task.video_path, opts)
            with self._lock:
                if self._stopping:  # 停表后不再写，避免覆盖新实例
                    return
                _transition(task, "complete", report_path=report)
                self._save()
        except Exception as e:  # noqa: BLE001 —— 任务失败进表，不拖垮服务
            with self._lock:
                if self._stopping:
                    return
                _transition(task, "fail", error=f"{type(e).__name__}: {e}")
                self._save()
            traceback.print_exc()

    def _run_reselect(self, task_id: str, person: int) -> None:
        try:
            task = self.get(task_id)
            if task is None or task.report_path is None:
                raise RuntimeError("任务或报告不存在，无法改选")
            reselect_one(task.report_path.parent, person, level=task.level,
                         fallback_video=task.video_path, blur=task.blur,
                         lifter=task.lifter)
            error = ""
        except Exception as e:  # noqa: BLE001 —— 改选失败进表，由页面展示
            error = f"{type(e).__name__}: {e}"
        with self._lock:
            t = self._tasks.get(task_id)
            if t is None or self._stopping:
                return
            _transition(t, "reselect_finish", error=error)
            self._save()
