#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""export_onnx.py —— 把 YOLO 的 .pt 导出成 ONNX（开发机上跑，不是板子上）

⚠️ 需要 torch + ultralytics，体积较大（几百 MB），只在你要换模型时才需要。
   仓库里已经带了导出好的 models/hand_gesture.onnx，日常运行不用跑这个脚本。

用法（在仓库根目录）:
    pip install torch ultralytics
    python3 scripts/export_onnx.py                    # 用默认候选
    python3 scripts/export_onnx.py path/to/best.pt    # 指定权重

产出：
    models/hand_gesture.onnx   推理用模型
    models/labels.txt          类别名清单（顺序必须与 config.json 的 class_names 一致）
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS = os.path.join(ROOT, "models")
IMGSZ = 320

# 默认候选（放在 models/ 下即可）
DEFAULT_CANDIDATES = ["best.pt", "yolo11n_rps.pt", "yolov5_rps.pt"]


def export_one(pt_path, out_name="hand_gesture.onnx"):
    if not os.path.exists(pt_path):
        print("[skip] 不存在: %s" % pt_path)
        return None

    try:
        import ultralytics
        from ultralytics import YOLO
    except ImportError:
        print("ERROR: 未安装 ultralytics，请先 pip install ultralytics")
        return None

    print("=" * 66)
    print("权重 : %s" % pt_path)
    print("版本 : ultralytics %s" % ultralytics.__version__)

    t0 = time.time()
    try:
        model = YOLO(pt_path)
        names = model.names
        print("类别 : %s" % names)
    except Exception as e:
        print("[FAIL] 加载失败: %s: %s" % (type(e).__name__, e))
        return None

    try:
        out = model.export(format="onnx", imgsz=IMGSZ, opset=12, simplify=False)
        print("导出 : %.1f s" % (time.time() - t0))
    except Exception as e:
        print("[FAIL] 导出失败: %s: %s" % (type(e).__name__, e))
        return None

    final = os.path.join(MODELS, out_name)
    if os.path.abspath(out) != os.path.abspath(final):
        os.replace(out, final)
    print("产物 : %s (%.2f MB)" % (final, os.path.getsize(final) / 1048576.0))

    labels = os.path.join(MODELS, "labels.txt")
    idx = sorted(names.keys()) if isinstance(names, dict) else range(len(names))
    with open(labels, "w", encoding="utf-8") as f:
        for i in idx:
            f.write("%s\n" % names[i])
    print("标签 : %s" % labels)
    print()
    print("!! 记得确认 config.json 里的 class_names 顺序与 labels.txt 一致 !!")
    return final


def main():
    os.makedirs(MODELS, exist_ok=True)

    if len(sys.argv) > 1:
        candidates = [sys.argv[1]]
    else:
        candidates = [os.path.join(MODELS, c) for c in DEFAULT_CANDIDATES]

    for pt in candidates:
        if export_one(pt):
            return 0

    print("全部失败。请把 .pt 放到 models/ 下，或直接传路径：")
    print("    python3 scripts/export_onnx.py models/my_best.pt")
    return 1


if __name__ == "__main__":
    sys.exit(main())