"""Halpe-26 骨架布局契约：关键点索引、trc 别名、肢体链、conf 语义、几何原语。

全项目以 (frames, 26, 3) 的 [x, y, conf] 数组说话（trc_io.keypoints_halpe 产出）。
conf 语义：trc 不含置信度，keypoints_halpe 把"点存在"记 1、缺失记 0 ——
连续 0~1 值只出现在合成测试数据里；CONF_MIN 这类阈值在真实管线上恒为真，
真正起作用的是 0/1 存在性。

二期换 pose model（一期 #5 的 body_with_feet 换代）时只改这个文件，
trc 别名表一并在此核对补全。
"""

from __future__ import annotations

import numpy as np

# Halpe-26 关键点索引（Sports2D 默认 pose_model=body_with_feet）
NOSE = 0
L_EYE, R_EYE = 1, 2
L_EAR, R_EAR = 3, 4
L_SHOULDER, R_SHOULDER = 5, 6
L_ELBOW, R_ELBOW = 7, 8
L_WRIST, R_WRIST = 9, 10
L_HIP, R_HIP = 11, 12
L_KNEE, R_KNEE = 13, 14
L_ANKLE, R_ANKLE = 15, 16
# Halpe 扩展点（body_with_feet 模型的 26 点里 17~25）：
HEAD_TOP, NECK, HIP_CENTER = 17, 18, 19

# 参与角度计算的最低置信度门槛（view 与 metrics 共用）
CONF_MIN = 0.3

# 每侧肢体链：视角分类的置信度不对称信号按此取点
L_LIMB = (L_SHOULDER, L_ELBOW, L_HIP, L_KNEE, L_ANKLE)
R_LIMB = (R_SHOULDER, R_ELBOW, R_HIP, R_KNEE, R_ANKLE)

# 矢状面指标按侧计算：选置信度高的一侧，取对应四点（肩髋膝踝）
SIDE_JOINTS: dict[str, tuple[int, int, int, int]] = {
    "left": (L_SHOULDER, L_HIP, L_KNEE, L_ANKLE),
    "right": (R_SHOULDER, R_HIP, R_KNEE, R_ANKLE),
}


def pick_side(left_conf: float, right_conf: float) -> str:
    """选侧规则的唯一实现：置信度高的一侧，平票取左。

    二维（metrics.select_side）与三维（metrics3d.select_side）共用——规则
    写两份时，平票语义漂移不会有任何测试失败，只在左右置信度打平的真实
    帧上悄悄分歧。
    """
    return "left" if left_conf >= right_conf else "right"

# 质量统计口径的核心八点（验证进展 #3 的人物偏小/低置信度提示用）：
# 躯干与下肢大关节。雪场远景缺脸/手脚趾很常见，全点口径会让提示常驻失去区分度。
CORE_JOINTS = (L_SHOULDER, R_SHOULDER, L_HIP, R_HIP, L_KNEE, R_KNEE, L_ANKLE, R_ANKLE)

# Halpe-26 规范名（按索引），animation.json 的 joints 列表与此同序
HALPE_NAMES: tuple[str, ...] = (
    "Nose", "L_Eye", "R_Eye", "L_Ear", "R_Ear",
    "L_Shoulder", "R_Shoulder", "L_Elbow", "R_Elbow", "L_Wrist", "R_Wrist",
    "L_Hip", "R_Hip", "L_Knee", "R_Knee", "L_Ankle", "R_Ankle",
    "Head_Top", "Neck", "Hip_Center",
    "L_Big_Toe", "L_Small_Toe", "L_Heel",
    "R_Big_Toe", "R_Small_Toe", "R_Heel",
)

# 全身骨架边（索引对）：三维回放的画线依据。与 render.EDGES（二维叠加只画
# 躯干八点）不同，这里含手臂与头颈；缺测关节（眼耳脚趾）画线时跳过。
SKELETON_EDGES: tuple[tuple[int, int], ...] = (
    (L_SHOULDER, R_SHOULDER), (L_SHOULDER, L_HIP), (R_SHOULDER, R_HIP),
    (L_HIP, R_HIP), (HIP_CENTER, NECK), (NECK, HEAD_TOP), (NECK, NOSE),
    (L_SHOULDER, L_ELBOW), (L_ELBOW, L_WRIST),
    (R_SHOULDER, R_ELBOW), (R_ELBOW, R_WRIST),
    (L_HIP, L_KNEE), (L_KNEE, L_ANKLE),
    (R_HIP, R_KNEE), (R_KNEE, R_ANKLE),
)


def size_score(kp: np.ndarray) -> float:
    """中位骨架包围盒主轴长度 px。选人尺寸分与"人物偏小"质量统计共用。
    仅统计关键点齐全（≥6 点）的帧。
    """
    lens = []
    for f in range(kp.shape[0]):
        pts = kp[f][kp[f][:, 2] > 0][:, :2]
        if len(pts) < 6:
            continue
        w = np.nanmax(pts[:, 0]) - np.nanmin(pts[:, 0])
        h = np.nanmax(pts[:, 1]) - np.nanmin(pts[:, 1])
        lens.append(max(w, h))
    return float(np.median(lens)) if lens else 0.0

# Halpe-26 名字 → trc 里可能出现的写法。2026-10-26 已按 Sports2D
# body_with_feet 的实际输出（px trc 22 marker：Hip/RHip/…/Neck/Head/Nose/
# RShoulder/…）核对补全——此前只有 12 个映射，肘腕头颈髋中与十个脚点
# 全部落空（NaN），MotionBERT 有 4 个输入通道恒为零。眼耳（1~4）在该
# marker 集里不存在，保持缺测。
NAME_ALIASES: dict[int, tuple[str, ...]] = {
    0: ("Nose", "Head_gesture", "nose"),
    3: ("LEar", "L_Ear", "lear"),
    4: ("REar", "R_Ear", "rear"),
    5: ("LShoulder", "L_Shoulder", "lshoulder"),
    6: ("RShoulder", "R_Shoulder", "rshoulder"),
    7: ("LElbow", "L_Elbow", "lelbow"),
    8: ("RElbow", "R_Elbow", "relbow"),
    9: ("LWrist", "L_Wrist", "lwrist"),
    10: ("RWrist", "R_Wrist", "rwrist"),
    11: ("LHip", "L_Hip", "lhip"),
    12: ("RHip", "R_Hip", "rhip"),
    13: ("LKnee", "L_Knee", "lknee"),
    14: ("RKnee", "R_Knee", "rknee"),
    15: ("LAnkle", "L_Ankle", "lankle"),
    16: ("RAnkle", "R_Ankle", "rankle"),
    17: ("Head", "head"),                # 头顶
    18: ("Neck", "neck"),
    19: ("Hip", "hip"),                  # 髋中（Sports2D 中央髋 marker）
    20: ("LBigToe", "L_BigToe", "lbigtoe"),
    21: ("RBigToe", "R_BigToe", "rbigtoe"),
    22: ("LSmallToe", "L_SmallToe", "lsmalltoe"),
    23: ("RSmallToe", "R_SmallToe", "rsmalltoe"),
    24: ("LHeel", "L_Heel", "lheel"),
    25: ("RHeel", "R_Heel", "rheel"),
}
