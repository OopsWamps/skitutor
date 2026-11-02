"""Web 壳：上传页 + 任务列表 + 报告页。

技术选型（docs/DECISIONS.md 二期待办"Web 壳"）：FastAPI + Jinja2 模板 +
原生 JS，无前端构建链；分析走 tasks.TaskBoard 后台线程。
"""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
from starlette.staticfiles import StaticFiles

from .. import artifacts
from ..precheck import PrecheckConfig, run_precheck
from ..reselect import (
    current_person, ensure_previews, list_candidates, source_video,
)
from .tasks import STATUS_FAILED, Task, TaskBoard
from .compare import PHASE_METRIC_LABELS, compare_reports

TEMPLATES = Path(__file__).parent / "templates"
LEVELS = ("novice", "intermediate", "advanced")


def create_app(result_dir: Path, *, enable_precheck: bool = True,
               precheck_config: PrecheckConfig | None = None,
               analyze_fn=None, lifter: str = "motionbert") -> FastAPI:
    """result_dir 同时是任务表、上传落盘与分析产物的根目录。

    enable_precheck=False 关掉上传预检（默认开）；precheck_config 覆盖判据
    默认值（默认见 PrecheckConfig 的 docstring）；analyze_fn 注入任务表的
    分析函数（测试用替身，不跑 Sports2D）；lifter 是三维提升的服务级上限
    （motionbert=默认开三维，none=把上传页的三维开关压到关——权重/显卡
    不在位时用）。
    """
    if lifter not in ("none", "motionbert"):
        raise ValueError(f"未知 lifter: {lifter}")
    lifter_enabled = lifter == "motionbert"
    # 先转绝对路径：上传落盘路径会原样写进 tasks.json，默认的相对
    # --result-dir（web_results）换个工作目录启动就找不到源视频了
    result_dir = Path(result_dir).resolve()
    result_dir.mkdir(parents=True, exist_ok=True)
    board = TaskBoard(result_dir, analyze_fn=analyze_fn)
    app = FastAPI(title="skibot", docs_url=None, redoc_url=None)
    app.state.board = board  # 测试与扩展的注入点
    tpl = Jinja2Templates(directory=str(TEMPLATES))
    # 未打码的上传原件（result_dir/uploads/）不对外：路由先于挂载注册，
    # /files/uploads/* 在这里就被 404，静态挂载只服务各结果子目录
    @app.get("/files/uploads/{rest:path}")
    def deny_uploads(rest: str):
        raise HTTPException(404, "源视频不对外提供；请查看报告页的标注视频")

    # 分析产物（标注视频等）静态挂载，报告页直接引用
    app.mount("/files", StaticFiles(directory=str(result_dir)), name="files")

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        # 服务级 lifter=none 时隐藏表单的三维开关（留着会是一个撒谎的勾选框）
        return tpl.TemplateResponse(request, "upload.html", {
            "active": "upload", "lifter_enabled": lifter_enabled})

    @app.post("/api/upload")
    async def upload(video: UploadFile = File(...),
                     level: str = Form("intermediate"), blur: str = Form(""),
                     lifter: str = Form("")):
        if level not in LEVELS:
            raise HTTPException(400, f"未知水平档: {level}")
        name = Path(video.filename or "").name
        suffix = Path(name).suffix.lower()
        if suffix not in (".mp4", ".mov", ".m4v", ".avi"):
            raise HTTPException(400, "仅支持 mp4/mov/m4v/avi 视频")
        task_id = uuid.uuid4().hex[:12]
        dest_dir = result_dir / "uploads" / task_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        # 落盘用 ASCII 名 <task_id>.<ext>：中文文件名会让 Sports2D 在中文系统
        # 上把 trc 头部按 GBK 写盘；带 task_id 保证 analyze_one 的结果子目录
        # （result_dir/<video.stem>）逐任务唯一，重启恢复时 id 与目录名一致。
        dest = dest_dir / f"{task_id}{suffix}"
        with dest.open("wb") as fh:  # 分块落盘，上传文件可达数百 MB
            while chunk := await video.read(1 << 20):
                fh.write(chunk)
        # 预检是秒级阻塞调用（OpenCV），丢线程池跑，别卡住事件循环；
        # 不合格的素材直接删掉落盘文件，不进分析队列
        notes: list[str] = []
        if enable_precheck:
            result = await run_in_threadpool(run_precheck, dest, precheck_config)
            if not result.passed:
                shutil.rmtree(dest_dir, ignore_errors=True)
                raise HTTPException(422, detail={
                    "message": "视频未通过上传预检，未进入分析队列",
                    "issues": [{"check": i.check, "problem": i.problem,
                                "advice": i.advice} for i in result.issues],
                })
            notes = result.notes
        # 表单三维开关只在服务级档位允许时生效（serve --lifter none 压到关）。
        # checkbox 未勾选时浏览器不发字段，缺省必须是"关"——缺省"开"会让
        # 取消勾选完全失效（2026-11-02 复核发现，blur 同病）
        effective_lifter = ("motionbert" if lifter == "on" and lifter_enabled
                            else "none")
        board.enqueue(task_id, dest, name, level, blur == "on",
                      effective_lifter)  # 显示名保留原名
        return {"task_id": task_id, "precheck_notes": notes}

    @app.get("/tasks", response_class=HTMLResponse)
    def tasks_page(request: Request, error: str = ""):
        return tpl.TemplateResponse(request, "tasks.html",
                                    {"active": "tasks", "tasks": board.all(),
                                     "error": error})

    @app.get("/api/tasks")
    def tasks_api():
        return [_task_json(t) for t in board.all()]

    @app.get("/api/tasks/{task_id}")
    def task_api(task_id: str):
        t = board.get(task_id)
        if t is None:
            raise HTTPException(404, "任务不存在")
        d = _task_json(t)
        d["reselecting"] = t.reselecting  # 页面轮询改选进度用
        d["reselect_error"] = t.reselect_error or None
        return d

    @app.post("/api/tasks/{task_id}/retry")
    def retry_api(task_id: str):
        res = board.retry(task_id)
        if res.task is None:
            raise HTTPException(404, "任务不存在")
        if not res.requeued:
            raise HTTPException(409, res.error or "只能重试失败的任务")
        d = _task_json(res.task)
        d["status"] = res.status      # 锁内快照，不受 worker 抢跑影响
        d["report_url"] = None
        return d

    @app.get("/api/tasks/{task_id}/candidates")
    def candidates_api(task_id: str):
        t = _done_task(board, task_id)
        work = t.report_path.parent
        cands = ensure_previews(work, source_video(work, t.video_path, t.blur))
        return {"current": current_person(work),
                "candidates": [{"id": c.pid, "n_valid_frames": c.n_valid,
                                "preview_url": _preview_url(work, c.pid)}
                               for c in cands]}

    @app.post("/api/tasks/{task_id}/person")
    def reselect_api(task_id: str, person: int = Form(...)):
        t = _done_task(board, task_id)
        work = t.report_path.parent
        pids = {c.pid for c in list_candidates(work)}
        if person not in pids:
            raise HTTPException(400, f"没有 person {person}，可选 {sorted(pids)}")
        # 改选作为任务的子状态进任务表，worker 串行执行；状态随 tasks.json
        # 持久化，重启不丢（此前是 web 层手搓的 set+dict+裸线程影子状态机）
        if not board.start_reselect(task_id, person):
            raise HTTPException(409, "该任务正在改选，请稍候")
        return {"status": "reselecting", "person": person}

    @app.get("/report/{task_id}", response_class=HTMLResponse)
    def report_page(request: Request, task_id: str):
        t = board.get(task_id)
        if t is None:
            raise HTTPException(404, "任务不存在")
        if t.status == STATUS_FAILED:
            return RedirectResponse("/tasks?" + urlencode({"error": t.error}),
                                    status_code=302)
        if not t.done:
            return RedirectResponse("/tasks", status_code=302)
        loaded = _load_report(t)
        if loaded is None:
            # 任务表说 done、报告却读不出来（被手删/写了一半/磁盘满）：
            # 渲染任务页给可操作提示，而不是让报告页 500
            return tpl.TemplateResponse(request, "tasks.html", {
                "active": "tasks", "tasks": board.all(),
                "error": f"任务 {task_id} 的报告文件缺失或损坏，无法查看；"
                         "可删除该结果或重新上传分析",
            }, status_code=500)
        report, report_file = loaded
        work = report_file.parent
        cands = ensure_previews(work, source_video(work, t.video_path, t.blur))
        # 回放卡数据源：动画与报告同链发布；旧任务没有就不渲染。还要看
        # 报告本身有没有三维相位——降级二维时旧动画若因文件锁没删掉，
        # 单看文件存在会把上一次三维评估的骨架配到本次二维报告上
        anim = artifacts.animation_path(work)
        animation_url = (artifacts.files_url(work, anim.name)
                         if anim.exists() and report.get("phases") is not None
                         else None)
        return tpl.TemplateResponse(request, "report.html", {
            "active": "tasks", "task": t, "report": report,
            "metrics": report.get("metrics", []),
            "phases": report.get("phases"),
            "phase_metric_labels": PHASE_METRIC_LABELS,
            "annotated_url": artifacts.files_url(
                work, artifacts.annotated_path(work).name),
            "animation_url": animation_url,
            "candidates": [{"id": c.pid, "n_valid_frames": c.n_valid,
                            "preview_url": _preview_url(work, c.pid)}
                           for c in cands],
            "current_person": current_person(work),
        })

    @app.get("/compare", response_class=HTMLResponse)
    def compare_page(request: Request, a: str = "", b: str = ""):
        done = [t for t in board.all() if t.done]
        opts = [{"id": t.id, "label": f"{t.video_name}（{t.level}，"
                                     f"{t.created_at:%Y-%m-%d}）"} for t in done]
        ctx: dict = {"active": "compare", "tasks": opts,
                     "a": a, "b": b, "result": None, "error": "",
                     "a_task": None, "b_task": None}
        if a and b:
            ta, tb = board.get(a), board.get(b)
            if ta is None or tb is None or not ta.done or not tb.done:
                raise HTTPException(404, "对比需要两个已完成的分析")
            loaded_a, loaded_b = _load_report(ta), _load_report(tb)
            if loaded_a is None or loaded_b is None:
                # 单侧报告坏掉：另一个任务仍是好的，不该整页 500；
                # 指出是哪个任务，用户可去任务页重跑
                bad = a if loaded_a is None else b
                ctx["error"] = f"任务 {bad} 的报告文件缺失或损坏，无法对比"
            else:
                ctx["result"] = compare_reports(loaded_a[0], loaded_b[0])
                ctx["a_task"] = ta
                ctx["b_task"] = tb
        return tpl.TemplateResponse(request, "compare.html", ctx)

    return app


def _load_report(t: Task) -> tuple[dict, Path] | None:
    """读任务的 report.json，返回（报告, 路径）；读不出来返回 None。

    「读不出来」包括文件被删、写了一半、JSON 损坏、顶层不是对象——这些
    状态任务表分辨不了（它只知道 done）。报告页与对比页都不能因此 500：
    一个坏文件不该把整个站点的既有结果变成错误页。
    """
    rp = t.report_path
    if rp is None:
        return None
    try:
        data = json.loads(artifacts.read_text_retry(rp))
    except (OSError, ValueError):
        return None
    # 合法 JSON 但形状不对（空对象、写了一半、别的文件冒充）：页面会
    # 在模板里取 report.view 时抛 UndefinedError，同样按坏报告处理
    if (not isinstance(data, dict) or not isinstance(data.get("view"), dict)
            or not isinstance(data.get("metrics"), list)):
        return None
    return data, rp


def _preview_url(work: Path, pid: int) -> str | None:
    """候选预览的静态地址；没写成功（轨迹无有效帧等）返回 None。"""
    p = artifacts.preview_path(work, pid)
    return artifacts.files_url(work, p.name) if p.exists() else None


def _done_task(board: TaskBoard, task_id: str):
    t = board.get(task_id)
    if t is None:
        raise HTTPException(404, "任务不存在")
    if not t.done:
        raise HTTPException(409, "任务尚未完成，无法改选人物")
    return t


def _task_json(t) -> dict:
    return {
        "id": t.id, "video": t.video_name, "level": t.level, "status": t.status,
        "error": t.error, "created": t.created_at.isoformat(timespec="seconds"),
        "report_url": f"/report/{t.id}" if t.done else None,
    }
