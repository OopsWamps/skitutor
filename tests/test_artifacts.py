"""分析产物布局测试：python tests/test_artifacts.py

产物布局此前散在 cli / web.tasks / web.app / reselect 四处各写一份字面量，
收敛进 skibot.artifacts 后在这里钉住契约：命名、blur 三态源解析、原子
落盘与 Windows 锁重试。
"""

import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # Windows GBK 控制台兜底

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import skibot.artifacts as A  # noqa: E402


def test_naming_contract():
    """一套命名只此一处：报告、标注视频、打码副本、预览、URL。"""
    work = Path("web_results") / "t1"
    assert A.report_path(work) == work / "report.json"
    assert A.annotated_path(work).name == "t1_annotated.mp4"
    assert A.blurred_path(work).name == "t1_blurred.mp4"
    assert A.preview_path(work, 3).name == "candidate_person3.jpg"
    m = A.PREVIEW_RE.fullmatch("candidate_person12.jpg")
    assert m and int(m.group(1)) == 12
    assert A.files_url(work, "t1_annotated.mp4") == "/files/t1/t1_annotated.mp4"
    # 历史结果的目录名可能来自任意文件名：& ' % 必须编码，否则静态路由 404
    odd = Path("web_results") / "a&b'c%d"
    assert A.files_url(odd, "x y.mp4") == "/files/a%26b%27c%25d/x%20y.mp4"
    print("  artifacts：命名契约 ✓")


def test_source_video_tri_state():
    """blur 三态：True 必须用副本（丢了报错）、False 用原件、None 推断。"""
    tmp = Path(tempfile.mkdtemp(prefix="skibot_test_artifacts_"))
    work = tmp / "t1"
    work.mkdir(parents=True)
    original = tmp / "original.mp4"
    original.write_bytes(b"orig")
    blurred = A.blurred_path(work)
    blurred.write_bytes(b"blur")

    assert A.source_video(work, original, blur=True) == blurred
    assert A.source_video(work, original, blur=False) == original  # 留着旧副本也用原件
    assert A.source_video(work, original, blur=None) == blurred    # 有副本推断为打过码

    blurred.unlink()
    assert A.source_video(work, original, blur=None) == original   # 无副本推断为未打码
    try:
        A.source_video(work, original, blur=True)
        raise AssertionError("blur=True 且副本丢失应报错")
    except FileNotFoundError as e:
        assert "打码副本" in str(e)
    print("  artifacts：blur 三态源解析 ✓")


def test_write_text_atomic_and_read_retry():
    tmp = Path(tempfile.mkdtemp(prefix="skibot_test_artifacts_"))
    p = tmp / "store.json"
    A.write_text_atomic(p, "{\"a\": 1}")
    assert p.read_text(encoding="utf-8") == "{\"a\": 1}"
    assert not p.with_name(p.name + ".tmp").exists()  # 临时文件不留
    A.write_text_atomic(p, "{\"a\": 2}")  # 覆盖已有文件
    assert A.read_text_retry(p) == "{\"a\": 2}"
    print("  artifacts：原子写 / 容错读 ✓")


def test_replace_with_retry_gives_up():
    """锁重试有尽头：目标一直被锁就重试到上限，抛最后的 PermissionError。"""
    from pathlib import Path as _Path
    from unittest import mock

    orig_sleep = A.time.sleep
    A.time.sleep = lambda s: None  # 测试不等真实退避
    calls = {"n": 0}

    def always_locked(self, dst):
        calls["n"] += 1
        raise PermissionError("被占用")

    try:
        with mock.patch.object(_Path, "replace", always_locked):
            try:
                A.replace_with_retry(_Path("a"), _Path("b"), attempts=3)
                raise AssertionError("重试耗尽应抛 PermissionError")
            except PermissionError:
                pass
        assert calls["n"] == 3  # 恰好尝试 attempts 次
    finally:
        A.time.sleep = orig_sleep
    print("  artifacts：锁重试有尽头 ✓")


if __name__ == "__main__":
    test_naming_contract()
    test_source_video_tri_state()
    test_write_text_atomic_and_read_retry()
    test_replace_with_retry_gives_up()
    print("全部通过")
