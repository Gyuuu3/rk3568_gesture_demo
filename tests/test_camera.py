#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_camera.py —— 验证 UVC 摄像头能否正常取帧"""
import sys
import time

import cv2

DEV = sys.argv[1] if len(sys.argv) > 1 else "/dev/video10"
W, H = 640, 480


def open_camera(dev, use_mjpg=True):
    cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)
    if not cap.isOpened():
        return None
    if use_mjpg:
        # UVC 在 USB2.0 下，YUYV 640x480 只有 ~7fps；MJPG 才能到 30fps
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, W)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, H)
    cap.set(cv2.CAP_PROP_FPS, 30)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


def main():
    print("打开 %s ..." % DEV)
    cap = open_camera(DEV, use_mjpg=True)
    if cap is None:
        print("ERROR: 打不开 %s" % DEV)
        return 1

    fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
    tag = "".join([chr((fourcc >> 8 * i) & 0xFF) for i in range(4)])
    print("实际格式: %s  分辨率: %dx%d  FPS: %.1f" % (
        tag,
        cap.get(cv2.CAP_PROP_FRAME_WIDTH),
        cap.get(cv2.CAP_PROP_FRAME_HEIGHT),
        cap.get(cv2.CAP_PROP_FPS)))

    n = 30
    t0 = time.time()
    ok_count = 0
    last = None
    for _ in range(n):
        ret, frame = cap.read()
        if ret and frame is not None:
            ok_count += 1
            last = frame
    dt = time.time() - t0
    cap.release()

    print("读 %d 帧: 成功 %d, 用时 %.2fs, 约 %.1f fps" % (n, ok_count, dt, ok_count / dt if dt else 0))
    if last is not None:
        cv2.imwrite("/tmp/camera_test.jpg", last)
        print("已保存一帧到 /tmp/camera_test.jpg  (%dx%d)" % (last.shape[1], last.shape[0]))
    if ok_count == 0:
        print("ERROR: 一帧都没读到")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())