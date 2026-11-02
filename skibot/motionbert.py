"""MotionBERT 三维提升适配器：Halpe-26 二维关键点 → Skeleton3D（issue 05）。

模型定义 vendor 在 skibot.mbmodel（Apache-2.0）；权重离线放在
models/motionbert/FT_MB_release_MB_ft_h36m/best_epoch.bin（170MB，官方
HuggingFace 仓 walterzhu/MotionBERT），不进版本库。环境自检：
scripts/check_motionbert.py。

预处理复刻官方 wild 推理路径（infer_wild.py + dataset_wild.py）：
- halpe2h36m 把 26 点折到 H36M-17（脊点 = 颈与髋中点的中点）；
- 整段 bbox 归一化到 [-1,1]（crop_scale ratio=1，确定性）；
- 按 243 帧切窗（尾窗可短，DSTformer 的 temp_embed 按帧数切片）；
- 输出与输入同尺度，不再反归一化——指标只用角度，尺度不变
  （spec「三维提升」：不输出世界轨迹与绝对位置）。

输出帧的两处修正（都在真实素材上核对过，见 issue 05 Comments）：
- 模型输出 y 朝下（图像惯例，鼻在髋上方为负）。适配器把 y、z 取反
  （绕 x 轴转 180°，保持手性）转到契约朝向，gravity_up 回到 (0,1,0)。
- H36M-17 的 0 号关节（pelvis）在输出里不是解剖位点：偏离髋中点可达
  身高的 2 倍、且波动是其他关节的 4 倍（平滑非噪声，疑为根通道残留）。
  Halpe 的髋中点（19）改由左右髋（h36m 1/4）中点重建——与契约的
  root-relative 原点定义一致。

17 点映射回 Halpe-26 时无对应的点（眼耳、脚趾脚跟）保持 NaN + conf 0，
契约缺测语义不变。重力继承训练数据的坐标约定，如实标
gravity_source="adapter"：单目提升的重力不是逐帧测量。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from .mbmodel import MODEL_PARAMS
from .skeleton import HIP_CENTER, L_HIP, R_HIP
from .skeleton3d import Lifter, Skeleton3D

if TYPE_CHECKING:
    from torch.nn import Module

CLIP_LEN = 243


def make_lifter(name: str) -> Lifter | None:
    """lifter 名 → 适配器：none→None（二维基线），motionbert→MotionBERTLifter。

    cli / web / reselect 共用的唯一解析点——同一份名字语义（AnalyzeOptions
    .lifter、serve --lifter、任务表的 lifter 字段）不许各写一份。
    """
    if name == "none":
        return None
    if name == "motionbert":
        return MotionBERTLifter()
    raise ValueError(f"未知 lifter：{name!r}，可选 none / motionbert")
# 权重默认位置（相对仓库根；与 scripts/check_motionbert.py 一致）
DEFAULT_CKPT = Path("models/motionbert/FT_MB_release_MB_ft_h36m/best_epoch.bin")

# Halpe-26 → H36M-17（官方 dataset_wild.halpe2h36m 的索引表）。
# H36M 顺序：0 髋中 1 右髋 2 右膝 3 右踝 4 左髋 5 左膝 6 左踝 7 脊 8 颈
# 9 鼻 10 头顶 11-13 左肩肘腕 14-16 右肩肘腕。脊点是派生（颈+髋中的中点）。
_H36M_FROM_HALPE: tuple[tuple[int, int | None], ...] = (
    (0, 19), (1, 12), (2, 14), (3, 16), (4, 11), (5, 13), (6, 15),
    (7, None), (8, 18), (9, 0), (10, 17), (11, 5), (12, 7), (13, 9),
    (14, 6), (15, 8), (16, 10))
# 上面映射的逆：Halpe 索引 ← H36M 索引（派生脊点不回填）。
_HALPE_FROM_H36M = {v: k for k, v in _H36M_FROM_HALPE if v is not None}


def halpe_to_h36m(kp: np.ndarray) -> np.ndarray:
    """(T,26,C) → (T,17,C)，坐标与置信通道同公式（官方实现即如此）。"""
    t, _, c = kp.shape
    out = np.zeros((t, 17, c), dtype=kp.dtype)
    for j36, src in _H36M_FROM_HALPE:
        if src is None:  # 脊点 = 颈(18) 与髋中(19) 的中点
            out[:, j36] = (kp[:, 18] + kp[:, 19]) / 2.0
        else:
            out[:, j36] = kp[:, src]
    return out


def _normalize_bbox(seq: np.ndarray) -> np.ndarray:
    """整段 bbox 归一化到 [-1,1]（crop_scale ratio=1 的确定性版本）。

    有效点（conf!=0）少于 4 个或跨度为 0 时按官方实现返回全零——下游
    valid_mask 会把全零帧判为无效。
    """
    out = np.array(seq, dtype=np.float32, copy=True)
    valid = seq[seq[..., 2] != 0][:, :2]
    if len(valid) < 4:
        return np.zeros(seq.shape, dtype=np.float32)
    xmin, ymin = valid.min(axis=0)
    xmax, ymax = valid.max(axis=0)
    scale = max(xmax - xmin, ymax - ymin)
    if scale == 0:
        return np.zeros(seq.shape, dtype=np.float32)
    xs = (xmin + xmax - scale) / 2
    ys = (ymin + ymax - scale) / 2
    out[..., :2] = (seq[..., :2] - [xs, ys]) / scale
    out[..., :2] = (out[..., :2] - 0.5) * 2
    return out


class MotionBERTLifter:
    """Lifter 适配器：二维 Halpe-26 → root-relative 的 26 关节 Skeleton3D。

    infer_fn 注入点（测试替身）：输入 (F,17,3) float32，输出 (F,17,3)。
    不注入时首次 lift 惰性加载 torch 与权重（torch 是可选依赖，导入本模块
    不触发）。device 默认 cuda 可用则用 cuda。
    """

    name = "motionbert"

    def __init__(self, checkpoint: Path | None = None,
                 device: str | None = None,
                 infer_fn: Callable[[np.ndarray], np.ndarray] | None = None):
        self.checkpoint = Path(checkpoint) if checkpoint else DEFAULT_CKPT
        self._device = device
        self._model: Module | None = None
        self._infer_fn = infer_fn
    def _ensure_loaded(self):
        if self._model is not None:
            return
        import torch

        from .mbmodel import build_lift_backbone
        if not self.checkpoint.exists():
            raise FileNotFoundError(
                f"找不到 MotionBERT 权重 {self.checkpoint}；下载与放置说明见 "
                "scripts/check_motionbert.py 与 issue 05")
        model = build_lift_backbone()
        ckpt = torch.load(self.checkpoint, map_location="cpu",
                          weights_only=False)
        sd = ckpt["model_pos"]
        if any(k.startswith("module.") for k in sd):  # DataParallel 存档
            sd = {k.removeprefix("module."): v for k, v in sd.items()}
        model.load_state_dict(sd, strict=True)
        model.eval()
        if self._device is None:
            self._device = "cuda" if torch.cuda.is_available() else "cpu"
        self._model = model.to(self._device)

    def _infer(self, motion: np.ndarray) -> np.ndarray:
        """单窗口推理：(F,17,3) → (F,17,3)，无翻转平均（确定性单遍）。"""
        if self._infer_fn is not None:
            return self._infer_fn(motion)
        self._ensure_loaded()
        import torch  # 首次 lift 已验证过可导入

        model = self._model
        assert model is not None  # _ensure_loaded 已加载
        x = torch.from_numpy(motion.astype(np.float32)).unsqueeze(0)
        if self._device and self._device != "cpu":
            x = x.to(self._device)
        with torch.no_grad():
            y = model(x)
        return y.squeeze(0).cpu().numpy()

    def lift(self, keypoints: np.ndarray) -> Skeleton3D:
        kp = np.asarray(keypoints, dtype=float)
        if kp.ndim != 3 or kp.shape[1] < 26 or kp.shape[2] < 3:
            raise ValueError(f"keypoints 形状必须是 (T, >=26, >=3)，收到 {kp.shape}")
        seq = halpe_to_h36m(kp[:, :26, :3])
        # trc 缺测关节坐标是 NaN（官方 AlphaPose json 记 0）；NaN 进网络会
        # 污染整个窗口，置 0 后由 conf=0 与下游 valid_mask 排除
        bad = ~np.isfinite(seq)
        if bad.any():
            seq = np.where(bad, 0.0, seq)
        seq = _normalize_bbox(seq)
        n = seq.shape[0]
        if n == 0:
            raise ValueError("关键点序列为空，无法提升")
        preds = np.concatenate([
            self._infer(seq[i:i + CLIP_LEN])
            for i in range(0, n, CLIP_LEN)], axis=0)
        # 模型输出 y 朝下；绕 x 轴转 180°（y、z 取反）到契约朝向
        preds = preds.astype(float, copy=True)
        preds[:, :, 1] *= -1.0
        preds[:, :, 2] *= -1.0

        joints = np.full((n, 26, 3), np.nan)
        conf_in = np.clip(seq[:, :, 2], 0.0, 1.0)
        conf = np.zeros((n, 26))
        for j_h, j36 in _HALPE_FROM_H36M.items():
            joints[:, j_h] = preds[:, j36]
            conf[:, j_h] = conf_in[:, j36]  # 置信度沿用输入二维检测
        # h36m 0 号关节（pelvis）输出非解剖位点：髋中点由左右髋重建
        joints[:, HIP_CENTER] = (joints[:, L_HIP] + joints[:, R_HIP]) / 2.0
        conf[:, HIP_CENTER] = np.minimum(conf[:, L_HIP], conf[:, R_HIP])
        return Skeleton3D(joints=joints, confidence=conf, source=self.name,
                          gravity_source="adapter",
                          meta={"adapter": type(self).__name__,
                                "checkpoint": self.checkpoint.name,
                                "clip_len": CLIP_LEN, **MODEL_PARAMS})
