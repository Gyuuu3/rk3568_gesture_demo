#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""preview.py —— 实时画面预览（网页 MJPEG 推流 + 本地窗口）

设计要点：
  1. 主循环只做「存一帧」，不做编码 —— 编码放在服务线程里按需做。
     这样没人看的时候几乎不占 CPU，看的时候也不拖慢推理。
  2. 用标准库 http.server，不引入额外依赖。
  3. 支持多客户端同时看（ThreadingHTTPServer）。

访问方式（浏览器）：
    http://<板子IP>:8080/          -> 带统计信息的页面
    http://<板子IP>:8080/stream    -> 纯 MJPEG 流
    http://<板子IP>:8080/snapshot  -> 单张图（可做调试）
    http://<板子IP>:8080/stats     -> JSON 状态
"""
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2

# 类别 -> 框的颜色（BGR）
BOX_COLORS = [(0, 200, 0), (0, 128, 255), (255, 128, 0), (200, 0, 200)]


def draw_frame(frame, dets, gesture, stats=None):
    """在图上画框、类别、手势、帧率等，返回新图（不修改原图）"""
    vis = frame.copy()

    for d in dets:
        x1, y1, x2, y2 = [int(v) for v in d["box"]]
        color = BOX_COLORS[d["cls"] % len(BOX_COLORS)]
        cv2.rectangle(vis, (x1, y1), (x2, y2), color, 2)
        label = "%s %.2f" % (d["name"], d["score"])
        # 文字带底色，暗背景下更清楚
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
        cv2.rectangle(vis, (x1, max(y1 - th - 8, 0)), (x1 + tw + 6, max(y1, th + 8)), color, -1)
        cv2.putText(vis, label, (x1 + 3, max(y1 - 5, th + 2)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 2)

    # 左上角状态
    lines = []
    lines.append("gesture: %s" % (gesture or "-"))
    if stats:
        if "fps" in stats:
            lines.append("fps: %.1f" % stats["fps"])
        if "infer_ms" in stats:
            lines.append("infer: %.0f ms" % stats["infer_ms"])
        if "pins" in stats:
            on = [k for k, v in stats["pins"].items() if v == 1]
            lines.append("pin: %s" % (on[0] if on else "all off"))
        if stats.get("rtsp"):
            lines.append(str(stats["rtsp"]))

    y = 26
    for i, t in enumerate(lines):
        color = (0, 255, 255) if i == 0 else (220, 220, 220)
        cv2.putText(vis, t, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (0, 0, 0), 4)
        cv2.putText(vis, t, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.62, color, 2)
        y += 26

    return vis


PAGE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>HandGesture 实时预览</title>
<style>
  body {{ margin:0; background:#111; color:#ddd;
         font-family: system-ui, "Microsoft YaHei", sans-serif; }}
  header {{ padding:10px 16px; background:#1b1b1b; border-bottom:1px solid #333;
            display:flex; align-items:center; gap:20px; flex-wrap:wrap; }}
  h1 {{ font-size:15px; margin:0; font-weight:600; }}
  #stats {{ font-size:13px; color:#8f8; font-family: ui-monospace, Consolas, monospace; }}
  .wrap {{ padding:12px; display:flex; justify-content:center; }}
  img {{ max-width:100%; border:1px solid #333; border-radius:4px; }}
</style></head>
<body>
<header>
  <h1>HandGesture 实时预览</h1>
  <div id="stats">连接中...</div>
</header>
<div class="wrap"><img src="/stream" alt="stream"></div>
<script>
function tick() {{
  fetch('/stats').then(r => r.json()).then(d => {{
    const pins = Object.entries(d.pins || {{}})
        .map(([k, v]) => k + '=' + v).join('  ');
    document.getElementById('stats').textContent =
      `fps ${{d.fps ? d.fps.toFixed(1) : '-'}} | ` +
      `推理 ${{d.infer_ms ? d.infer_ms.toFixed(0) : '-'}} ms | ` +
      `手势 ${{d.gesture || '-'}} | ${{pins}} | 帧 ${{d.frame_id}}`;
  }}).catch(() => {{}});
}}
setInterval(tick, 500); tick();
</script>
</body></html>
"""


class _Handler(BaseHTTPRequestHandler):
    server_version = "HandGesturePreview/1.0"

    # 静默访问日志，避免刷屏
    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            body = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/stream":
            self._stream()
        elif path == "/snapshot":
            self._snapshot()
        elif path == "/stats":
            self._stats()
        else:
            self.send_error(404)

    # ------------------------------------------------------------------
    def _stats(self):
        d = self.server.preview.get_stats()
        body = json.dumps(d, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _snapshot(self):
        jpg = self.server.preview.get_jpeg()
        if jpg is None:
            self.send_error(503, "no frame yet")
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(jpg)))
        self.end_headers()
        self.wfile.write(jpg)

    def _stream(self):
        preview = self.server.preview
        self.send_response(200)
        self.send_header("Age", "0")
        self.send_header("Cache-Control", "no-cache, private")
        self.send_header("Pragma", "no-cache")
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.end_headers()

        preview.add_client(1)
        interval = 1.0 / max(preview.max_fps, 1)
        try:
            while not preview.stopping:
                jpg = preview.get_jpeg()
                if jpg is None:
                    time.sleep(0.1)
                    continue
                self.wfile.write(b"--frame\r\n")
                self.wfile.write(b"Content-Type: image/jpeg\r\n")
                self.wfile.write(("Content-Length: %d\r\n\r\n" % len(jpg)).encode())
                self.wfile.write(jpg)
                self.wfile.write(b"\r\n")
                time.sleep(interval)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # 客户端关掉页面很正常
        finally:
            preview.add_client(-1)


class PreviewServer:
    """把最新一帧暴露成 HTTP 推流服务

    主线程每帧调用 update()（很便宜：一次拷贝 + 存引用），
    真正的画框和 JPEG 编码在请求到达时由服务线程完成。
    """

    def __init__(self, port=8080, quality=70, max_fps=15):
        self.port = port
        self.quality = quality
        self.max_fps = max_fps
        self.stopping = False

        self._lock = threading.Lock()
        self._frame = None
        self._dets = []
        self._gesture = None
        self._stats = {}
        self._frame_id = 0
        self._clients = 0
        self._httpd = None
        self._thread = None

    # ---------------- 主线程调用 ----------------
    def update(self, frame, dets, gesture, stats):
        with self._lock:
            self._frame = frame.copy()
            self._dets = dets
            self._gesture = gesture
            self._stats = dict(stats)
            self._frame_id += 1

    # ---------------- 服务线程调用 ----------------
    def get_jpeg(self):
        with self._lock:
            if self._frame is None:
                return None
            img = draw_frame(self._frame, self._dets, self._gesture, self._stats)
        ok, buf = cv2.imencode(".jpg", img,
                               [int(cv2.IMWRITE_JPEG_QUALITY), self.quality])
        return buf.tobytes() if ok else None

    def get_stats(self):
        with self._lock:
            d = dict(self._stats)
            d["gesture"] = self._gesture
            d["frame_id"] = self._frame_id
            d["clients"] = self._clients
            return d

    def add_client(self, delta):
        with self._lock:
            self._clients = max(0, self._clients + delta)

    # ---------------- 生命周期 ----------------
    def start(self):
        self._httpd = ThreadingHTTPServer(("0.0.0.0", self.port), _Handler)
        self._httpd.daemon_threads = True
        self._httpd.preview = self
        self._thread = threading.Thread(target=self._httpd.serve_forever,
                                        name="preview-http", daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self.stopping = True
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None

    @staticmethod
    def local_ip():
        """拿到本机在局域网里的 IP，方便拼出访问地址"""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"