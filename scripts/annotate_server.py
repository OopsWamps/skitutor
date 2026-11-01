"""相位人工标注工具：帧级标记换刃与弯顶点（issue 11）。

起一个本地小服务，浏览器里逐帧看视频、按键打标：

    python scripts/annotate_server.py <video.mp4>... [--port 8765]

页面操作：←/→ 逐帧，↑/↓ 或 PgUp/PgDn 跳 10 帧，空格播放/暂停；
E = 标换刃，A = 标弯顶点，Backspace = 撤销最近一个标记；
点「保存」把 marks 写到 <video 同目录>/<stem>_phase_annotation.json。

标注 JSON 结构（scripts/check_phase_annotation.py 的输入）：
    {"video": str, "fps": float, "annotator": str,
     "marks": [{"frame": int, "kind": "edge" | "apex"}]}

OpenCV 写出的 mp4 是 FMP4（MPEG-4 Part 2）编码，浏览器 <video> 解不了；
启动时自动把这类视频转成 H.264 副本（<stem>_h264.mp4，CRF 18 近无损，
帧数与原片一致）再发送，原片不动。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PAGE = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<title>skibot 相位标注</title>
<style>
 body { font-family: system-ui, sans-serif; margin: 16px; background: #111; color: #eee; }
 video { max-width: 90vw; display: block; }
 .row { margin: 8px 0; }
 button { font-size: 15px; margin-right: 6px; padding: 4px 10px; }
 .marks { font-family: monospace; font-size: 13px; white-space: pre-wrap; }
 .frame { font-size: 20px; font-family: monospace; }
 kbd { background:#333; padding: 1px 5px; border-radius: 3px; }
</style></head><body>
<h3 id="name"></h3>
<video id="v" controls></video>
<div class="row">
 <span class="frame" id="cur">f=-</span>
 <button onclick="step(-10)">⏪10</button><button onclick="step(-1)">◀1</button>
 <button onclick="step(1)">1▶</button><button onclick="step(10)">10⏩</button>
 <button onclick="mark('edge')">换刃 (E)</button>
 <button onclick="mark('apex')">顶点 (A)</button>
 <button onclick="undo()">撤销 (Backspace)</button>
 <button onclick="save()">保存</button>
 <span id="msg"></span>
</div>
<div class="marks" id="marks"></div>
<script>
const fps = __FPS__;
let marks = __MARKS__;
const v = document.getElementById('v');
v.src = '/video/__IDX__';
document.getElementById('name').textContent = '__NAME__';

function frame() { return Math.round(v.currentTime * fps); }
function show() {
  document.getElementById('cur').textContent = 'f=' + frame();
  const edges = marks.filter(m => m.kind === 'edge').length;
  document.getElementById('marks').textContent =
    marks.map(m => m.kind === 'edge' ? `换刃@${m.frame}` : `顶点@${m.frame}`).join('\\n')
    + `\\n（${edges} 个换刃，${marks.length - edges} 个顶点）`;
}
function step(d) { v.pause(); v.currentTime = Math.max(0, (frame() + d)) / fps; }
function mark(kind) {
  marks = marks.filter(m => !(m.frame === frame() && m.kind === kind));
  marks.push({frame: frame(), kind});
  marks.sort((a, b) => a.frame - b.frame);
  show();
}
function undo() { marks.pop(); show(); }
async function save() {
  const r = await fetch('/save/__IDX__', {method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({marks})});
  document.getElementById('msg').textContent = r.ok ? '已保存 ✓' : '保存失败';
}
document.addEventListener('keydown', e => {
  if (e.key === 'ArrowLeft') step(-1);
  else if (e.key === 'ArrowRight') step(1);
  else if (e.key === 'ArrowUp' || e.key === 'PageUp') step(10);
  else if (e.key === 'ArrowDown' || e.key === 'PageDown') step(-10);
  else if (e.key === ' ') { v.paused ? v.play() : v.pause(); e.preventDefault(); }
  else if (e.key === 'e' || e.key === 'E') mark('edge');
  else if (e.key === 'a' || e.key === 'A') mark('apex');
  else if (e.key === 'Backspace') undo();
});
v.addEventListener('seeked', show);
show();
</script></body></html>"""


# 浏览器 <video> 能解的编码（四字符码小写）；其余一律转 H.264
PLAYABLE_FOURCC = {"avc1", "h264", "hev1", "hvc1"}


def _fourcc(path: Path) -> str:
    import cv2

    cap = cv2.VideoCapture(str(path))
    try:
        n = int(cap.get(cv2.CAP_PROP_FOURCC))
        return "".join(chr((n >> 8 * i) & 0xFF) for i in range(4)).strip().lower()
    finally:
        cap.release()


def _frame_count(path: Path) -> int:
    import cv2

    cap = cv2.VideoCapture(str(path))
    try:
        return int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    finally:
        cap.release()


def _ensure_h264(path: Path) -> Path:
    """非浏览器可解编码的转 H.264 副本（已存在则复用），返回可服务的路径。"""
    if _fourcc(path) in PLAYABLE_FOURCC:
        return path
    out = path.with_name(path.stem + "_h264.mp4")
    if out.exists() and _frame_count(out) == _frame_count(path):
        return out
    from imageio_ffmpeg import get_ffmpeg_exe

    print(f"[skibot] {path.name}: {_fourcc(path)} 编码，转 H.264 副本……")
    subprocess.run(
        [get_ffmpeg_exe(), "-y", "-i", str(path),
         "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
         "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", str(out)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    n_src, n_out = _frame_count(path), _frame_count(out)
    if n_src != n_out:
        print(f"[skibot] 警告：{out.name} 帧数 {n_out} != 原片 {n_src}，"
              f"标注请以页面显示帧号为准", file=sys.stderr)
    return out


def build_app(videos: list[Path]):
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import FileResponse, HTMLResponse

    from skibot.render import probe_video

    infos = []
    for p in videos:
        n_frames, _, fps = probe_video(p)
        if not fps:
            raise ValueError(f"{p} 读不出帧率")
        serve_path = _ensure_h264(p)
        if serve_path != p:
            # FMP4 源的 cv2 元数据帧数不可信（截断文件虚报 1280），
            # 以转码副本的准确元数据为准
            n_frames, _, fps2 = probe_video(serve_path)
            if fps2:
                fps = fps2
        ann = p.with_name(p.stem + "_phase_annotation.json")
        marks = []
        if ann.exists():
            marks = json.loads(ann.read_text(encoding="utf-8")).get("marks", [])
        infos.append({"path": p, "serve_path": serve_path, "fps": fps,
                      "ann": ann, "marks": marks, "n_frames": n_frames})

    app = FastAPI(docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    def index():
        links = "".join(f'<p><a href="/annotate/{i}">{i}: {p.name}</a></p>'
                        for i, p in enumerate(videos))
        return HTMLResponse(f"<h3>相位标注</h3>{links}")

    @app.get("/annotate/{idx}", response_class=HTMLResponse)
    def annotate(idx: int):
        if idx < 0 or idx >= len(infos):
            raise HTTPException(404)
        info = infos[idx]
        html = (PAGE.replace("__FPS__", repr(info["fps"]))
                .replace("__IDX__", str(idx))
                .replace("__NAME__", info["path"].name)
                .replace("__MARKS__", json.dumps(info["marks"])))
        return HTMLResponse(html)

    @app.get("/video/{idx}")
    def video(idx: int):
        if idx < 0 or idx >= len(infos):
            raise HTTPException(404)
        return FileResponse(infos[idx]["serve_path"])

    @app.post("/save/{idx}")
    async def save(idx: int, body: dict):
        if idx < 0 or idx >= len(infos):
            raise HTTPException(404)
        info = infos[idx]
        # 落盘前校验：帧号在片内、kind 合法、同帧同 kind 去重
        clean: list[dict] = []
        seen: set[tuple[int, str]] = set()
        for m in body.get("marks", []):
            try:
                frame, kind = int(m["frame"]), str(m["kind"])
            except (KeyError, TypeError, ValueError):
                continue
            if not (0 <= frame < info["n_frames"]) or kind not in ("edge", "apex"):
                continue
            if (frame, kind) in seen:
                continue
            seen.add((frame, kind))
            clean.append({"frame": frame, "kind": kind})
        clean.sort(key=lambda m: m["frame"])
        payload = {"video": str(info["path"]), "fps": info["fps"],
                   "annotator": "human", "marks": clean}
        info["ann"].write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                               encoding="utf-8")
        return {"saved": str(info["ann"]), "n_marks": len(clean)}

    return app


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="相位人工标注服务器")
    ap.add_argument("videos", nargs="*", type=Path, help="待标注视频")
    ap.add_argument("--manifest", type=Path, default=None,
                    help="素材清单（逐段取 video 字段，与 videos 二选一）")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args(argv)
    videos = list(args.videos)
    if args.manifest:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        videos.extend(Path(c["video"]) for c in manifest["clips"])
    if not videos:
        ap.error("需要 videos 或 --manifest")
    for v in videos:
        if not v.exists():
            print(f"[skibot] 找不到视频：{v}", file=sys.stderr)
            return 1

    import uvicorn

    uvicorn.run(build_app(videos), host="127.0.0.1", port=args.port,
                log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
