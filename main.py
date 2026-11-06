#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py —— 手势识别 -> 三个扩展引脚输出

流程：
    摄像头取帧 -> YOLO(ONNX) 推理 -> 取置信度最高的框 -> 手势名
              -> 连续 N 帧稳定才生效 -> 驱动对应 LED 引脚
              -> 同时把带框画面推给网页/本地窗口（可选）

用法：
    ./run.sh                              # 正常跑（默认开网页推流）
    ./run.sh --debug                      # 每帧打印检测结果
    ./run.sh --frames 300                 # 跑 300 帧后退出（测速用）
    ./run.sh --no-stream                  # 关掉网页推流
    ./run.sh --port 8081                  # 换端口
    ./run.sh --display                    # 在板上屏幕直接显示窗口
    ./run.sh --save out/                  # 保存带框的图片便于核对

网页查看：http://<板子IP>:8080/
"""
import argparse
import json
import os
import sys
import time

import cv2
import numpy as np

from led_out import GestureLed
from preview import PreviewServer, draw_frame
from yolo_onnx import YoloOnnx

HERE = os.path.dirname(os.path.abspath(__file__))


def load_config(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def open_camera(conf):
    c = conf["camera"]
    cap = cv2.VideoCapture(c["device"], cv2.CAP_V4L2)
    if not cap.isOpened():
        raise RuntimeError("打不开摄像头 %s" % c["device"])
    if c.get("use_mjpg", True):
        # USB2.0 带宽下 YUYV 只有几帧，必须用 MJPG
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, c["width"])
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, c["height"])
    cap.set(cv2.CAP_PROP_FPS, c["fps"])
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


def draw(frame, dets, gesture, fps):
    """兼容旧接口：统一走 preview.draw_frame"""
    return draw_frame(frame, dets, gesture, {"fps": fps} if fps else None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(HERE, "config.json"))
    ap.add_argument("--debug", action="store_true", help="每帧打印检测结果")
    ap.add_argument("--save", default=None, help="把带框图像存到该目录")
    ap.add_argument("--frames", type=int, default=0, help="跑多少帧后退出，0=无限")
    ap.add_argument("--no-led", action="store_true", help="只跑识别，不驱动引脚")

    # ---- 实时预览 ----
    ap.add_argument("--stream", dest="stream", action="store_true",
                    help="开网页实时推流（默认开）")
    ap.add_argument("--no-stream", dest="stream", action="store_false",
                    help="关掉网页推流")
    ap.add_argument("--port", type=int, default=None,
                    help="网页推流端口，默认取 config.json 里的 8080")
    ap.add_argument("--display", action="store_true",
                    help="在接有屏幕的板子上开本地窗口（需要 DISPLAY）")

    # ---- RTSP 推流 ----
    ap.add_argument("--rtsp", default=None,
                    help="推 RTSP 流，例：rtsp://192.168.1.100:8554/hand")
    ap.add_argument("--no-rtsp", action="store_true", help="关掉 RTSP 推流")
    ap.set_defaults(stream=None)
    args = ap.parse_args()

    conf = load_config(args.config)
    mconf = conf["model"]
    bconf = conf["behavior"]

    model_path = mconf["path"]
    if not os.path.isabs(model_path):
        model_path = os.path.join(HERE, model_path)

    if not os.path.exists(model_path):
        print("ERROR: 找不到模型 %s" % model_path)
        print("       请先把 YOLO 模型导出为 ONNX 放到 models/ 下（见 README.md）")
        return 2

    print("加载模型: %s" % model_path)
    det = YoloOnnx(model_path,
                   class_names=mconf["class_names"],
                   input_size=mconf["input_size"],
                   conf_thres=mconf["conf_thres"],
                   iou_thres=mconf["iou_thres"],
                   num_threads=mconf["num_threads"])
    print("类别: %s" % mconf["class_names"])

    led = None
    if not args.no_led:
        led = GestureLed(conf["gesture_to_led"])
        print("引脚映射: %s" % conf["gesture_to_led"])
        led.all_off()
    else:
        print("(--no-led) 不驱动引脚")

    save_dir = args.save
    if save_dir and not os.path.isdir(save_dir):
        os.makedirs(save_dir)

    cap = open_camera(conf)
    print("摄像头已打开: %s" % conf["camera"]["device"])

    # ---------------- 实时预览 ----------------
    pconf = conf.get("preview", {})
    want_stream = pconf.get("enabled", True) if args.stream is None else args.stream
    preview = None
    if want_stream:
        port = args.port or pconf.get("port", 8080)
        try:
            preview = PreviewServer(port=port,
                                    quality=pconf.get("quality", 70),
                                    max_fps=pconf.get("max_fps", 15)).start()
            ip = PreviewServer.local_ip()
            print("")
            print("  ╔══════════════════════════════════════════════╗")
            print("  ║  实时预览已启动                               ║")
            print("  ╚══════════════════════════════════════════════╝")
            print("   在电脑浏览器打开：  http://%s:%d/" % (ip, port))
            print("   纯视频流：          http://%s:%d/stream" % (ip, port))
            print("   单张快照：          http://%s:%d/snapshot" % (ip, port))
            print("   状态 JSON：         http://%s:%d/stats" % (ip, port))
            print("")
        except OSError as e:
            print("[WARN] 预览服务启动失败（端口 %d 被占用？）: %s" % (port, e))
            preview = None
    else:
        print("(预览已关闭)")

    use_display = args.display
    if use_display and not os.environ.get("DISPLAY"):
        print("[WARN] --display 需要 DISPLAY 环境变量，已忽略")
        use_display = False

    # ---------------- RTSP 推流 ----------------
    rconf = conf.get("rtsp", {})
    rtsp_url = args.rtsp or (rconf.get("url") if rconf.get("enabled") else None)
    if args.no_rtsp:
        rtsp_url = None
    publisher = None
    if rtsp_url:
        try:
            from rtsp import RtspPublisher
            publisher = RtspPublisher(
                rtsp_url,
                width=conf["camera"]["width"],
                height=conf["camera"]["height"],
                fps=rconf.get("fps", 8),
                bitrate=rconf.get("bitrate", "1500k"),
                annotate=rconf.get("annotate", True),
            ).start()
            print("RTSP 推流 -> %s  (%d fps, %s)" % (
                rtsp_url, rconf.get("fps", 8), rconf.get("bitrate", "1500k")))
            _hint = publisher.hint()
            if _hint:
                print("[WARN] %s" % _hint)
            print("   Windows 上用 VLC 打开这个地址；或 ffplay/其他设备拉流")
        except Exception as e:
            print("[WARN] RTSP 推流启动失败: %s" % e)
            publisher = None

    stable_need = bconf["stable_frames"]
    off_need = bconf["no_gesture_frames"]
    print_every = bconf["print_every_n_frames"]

    last_label = None      # 上一帧的原始识别结果
    same_count = 0         # 连续相同帧计数
    cur_gesture = None     # 当前已生效的手势
    empty_count = 0

    frame_idx = 0
    t_start = time.time()
    fps = 0.0

    print("\n开始识别（Ctrl+C 退出）...\n")
    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                print("读取帧失败，重试...")
                time.sleep(0.05)
                continue

            frame_idx += 1
            t0 = time.time()
            dets = det.detect(frame)
            infer_ms = (time.time() - t0) * 1000.0

            # 取置信度最高的一个框作为当前手势
            label = None
            if dets:
                best = max(dets, key=lambda d: d["score"])
                label = best["name"]

            # ---- 时间平滑：连续 stable_need 帧一致才切换 ----
            if label == last_label:
                same_count += 1
            else:
                last_label = label
                same_count = 1

            if label is None:
                empty_count += 1
                if empty_count >= off_need and cur_gesture is not None:
                    cur_gesture = None
                    if led:
                        led.all_off()
                    print("[%d] 无手势 -> 全部引脚置 0" % frame_idx)
            else:
                empty_count = 0
                if same_count >= stable_need and cur_gesture != label:
                    cur_gesture = label
                    if led:
                        led.set_gesture(label)
                    pin = conf["gesture_to_led"].get(label, "?")
                    print("[%d] 手势切换 -> %s  (%s = 1, 其余 = 0)" % (frame_idx, label, pin))

            if args.debug:
                print("  frame %d: %d 个框, 推理 %.1f ms, label=%s, stable=%d" % (
                    frame_idx, len(dets), infer_ms, label, same_count))

            if frame_idx % print_every == 0:
                el = time.time() - t_start
                fps = frame_idx / el if el > 0 else 0
                extra = ""
                if dets:
                    d = max(dets, key=lambda x: x["score"])
                    extra = "  best=%s %.2f" % (d["name"], d["score"])
                print("  ... %d 帧 | %.1f fps | 推理 %.1f ms | 当前手势=%s%s" % (
                    frame_idx, fps, infer_ms, cur_gesture or "-", extra))

            # ---- 推流 / 本地窗口 ----
            pins_state = {}
            if led:
                pins_state = {p: (1 if v == 1 else 0) for p, v in led.current().items()}
            stats = {
                "fps": round(fps, 1),
                "infer_ms": round(infer_ms, 1),
                "frame": frame_idx,
                "raw_label": label,
                "pins": pins_state,
            }
            if publisher:
                stats["rtsp"] = publisher.short_status()
            if preview:
                preview.update(frame, dets, cur_gesture, stats)
            if publisher:
                publisher.update(frame, dets, cur_gesture, stats)
            if use_display:
                cv2.imshow("HandGesture", draw_frame(frame, dets, cur_gesture, stats))
                if (cv2.waitKey(1) & 0xFF) == 27:   # ESC 退出
                    print("ESC -> 退出")
                    break

            if save_dir:
                if frame_idx % 30 == 1:
                    vis = draw(frame, dets, cur_gesture, fps)
                    cv2.imwrite(os.path.join(save_dir, "f%05d.jpg" % frame_idx), vis)

            if args.frames and frame_idx >= args.frames:
                print("\n达到 %d 帧，退出" % args.frames)
                break

    except KeyboardInterrupt:
        print("\n收到 Ctrl+C")
    finally:
        cap.release()
        if preview:
            preview.stop()
            print("预览服务已停止")
        if publisher:
            st = publisher.status()
            publisher.stop()
            print("RTSP 推流已停止（已推 %d 帧，丢 %d 帧，重启 %d 次）" % (
                st["pushed"], st["dropped"], st["restarts"]))
        if use_display:
            cv2.destroyAllWindows()
        if led:
            led.all_off()
            print("已把所有引脚置 0")
        el = time.time() - t_start
        if el > 0:
            print("平均 %.1f fps" % (frame_idx / el))
    return 0


if __name__ == "__main__":
    sys.exit(main())