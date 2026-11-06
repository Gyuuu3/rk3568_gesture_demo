#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_image.py —— 用静态图片验证 ONNX 推理 + 后处理是否正确

不接摄像头也能确认模型是好的，是排查"识别不准"时的第一道关。

用法（在仓库根目录）:
    python3 tests/test_image.py                  # 从摄像头抓一帧来测
    python3 tests/test_image.py a.jpg b.jpg      # 测指定图片
    python3 tests/test_image.py samples/         # 测一个目录

结果图保存在 out_img/ 下。
"""
import json
import os
import sys

import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from yolo_onnx import YoloOnnx  # noqa: E402


def grab_from_camera(conf):
    """从摄像头抓一帧（没有测试图时的兜底）"""
    c = conf["camera"]
    cap = cv2.VideoCapture(c["device"], cv2.CAP_V4L2)
    if not cap.isOpened():
        return None
    if c.get("use_mjpg", True):
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, c["width"])
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, c["height"])
    frame = None
    for _ in range(10):          # 前几帧常是空的，多读几次
        ok, f = cap.read()
        if ok and f is not None:
            frame = f
    cap.release()
    return frame


def main():
    with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as f:
        conf = json.load(f)

    mconf = conf["model"]
    model = mconf["path"]
    if not os.path.isabs(model):
        model = os.path.join(ROOT, model)
    if not os.path.exists(model):
        print("ERROR: 模型不存在 %s" % model)
        return 1

    det = YoloOnnx(model, mconf["class_names"],
                   input_size=mconf["input_size"], conf_thres=0.25,
                   iou_thres=mconf["iou_thres"])
    print("模型: %s" % model)
    print("类别: %s\n" % mconf["class_names"])

    outdir = os.path.join(ROOT, "out_img")
    os.makedirs(outdir, exist_ok=True)

    args = sys.argv[1:]
    images = []
    if args:
        for a in args:
            p = a if os.path.isabs(a) else os.path.abspath(a)
            if os.path.isdir(p):
                images += [os.path.join(p, x) for x in sorted(os.listdir(p))
                           if x.lower().endswith((".jpg", ".jpeg", ".png"))]
            else:
                images.append(p)
    else:
        print("没给图片，从摄像头抓一帧...")
        frame = grab_from_camera(conf)
        if frame is None:
            print("ERROR: 摄像头也打不开，请直接传图片路径")
            return 1
        p = os.path.join(outdir, "capture.jpg")
        cv2.imwrite(p, frame)
        images = [p]
        print("已保存 %s\n" % p)

    for path in images:
        img = cv2.imread(path)
        if img is None:
            print("读不了 %s" % path)
            continue
        dets = det.detect(img)
        print("%s  (%dx%d)  输出格式=%s" % (
            os.path.basename(path), img.shape[1], img.shape[0], det.fmt))
        if not dets:
            print("   未检测到目标")
        for d in dets:
            print("   %-9s score=%.3f  box=%s" % (
                d["name"], d["score"], [round(v) for v in d["box"]]))
            x1, y1, x2, y2 = [int(v) for v in d["box"]]
            cv2.rectangle(img, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.putText(img, "%s %.2f" % (d["name"], d["score"]),
                        (x1, max(y1 - 5, 12)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
        out = os.path.join(outdir, "res_" + os.path.basename(path))
        cv2.imwrite(out, img)
        print("   -> %s\n" % out)

    return 0


if __name__ == "__main__":
    sys.exit(main())