#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rtsp.py —— 把带检测框的画面推成 RTSP 流（H.264）

架构：
    主循环 ──update(frame)──► [最新帧缓冲] ──► 编码线程 ──► ffmpeg 子进程 ──► RTSP 服务器

为什么要单独一个线程：
    ffmpeg 的 stdin 是阻塞写。如果主循环直接写，一旦网络慢或编码慢，
    推理就被拖住，帧率会掉到 1fps 以下。
    所以主循环只做「覆盖最新帧」（拷贝一次，不阻塞），编码线程按固定帧率取最新帧去写，
    写不动就丢帧 —— 保证"实时性优先于完整性"。

典型用法（MediaMTX 跑在 Windows 上）：

    Windows：  mediamtx.exe                     （默认监听 8554）
    板子：     ./run.sh --rtsp rtsp://192.168.1.100:8554/hand
    客户端：   VLC 打开 rtsp://192.168.1.100:8554/hand

⚠️ 端口一定要写。RTSP 标准默认端口是 554，而 MediaMTX 默认是 8554。
   写成 rtsp://IP/hand 会去连 IP:554，连不上，服务器上也就没有这条流。
"""
import shutil
import socket
import subprocess
import threading
import time
from urllib.parse import urlparse

import cv2

from preview import draw_frame

# RTSP 标准默认端口（MediaMTX 默认用 8554，所以这个默认值经常是坑）
DEFAULT_RTSP_PORT = 554


class RtspPublisher:
    def __init__(self, url, width=640, height=480, fps=8,
                 bitrate="1500k", preset="ultrafast", annotate=True,
                 ffmpeg="ffmpeg"):
        """
        url      : rtsp://<服务器IP>:<端口>/<流名>
        fps      : 推流帧率上限（主循环更慢时以主循环为准）
        bitrate  : 码率，局域网可用 1500k~3000k
        annotate : 是否把检测框烧进画面
        """
        self.url = url
        self.width = width
        self.height = height
        self.fps = max(1, int(fps))
        self.bitrate = bitrate
        self.preset = preset
        self.annotate = annotate
        self.ffmpeg = ffmpeg

        # ---- 解析 URL，尽早暴露"没写端口"这种错误 ----
        parsed = urlparse(url)
        if parsed.scheme not in ("rtsp", "rtsps"):
            raise ValueError("不是 RTSP 地址: %s" % url)
        self._host = parsed.hostname
        self._port = parsed.port
        self._path = parsed.path or "/"
        self.port_missing = self._port is None
        if self._port is None:
            self._port = DEFAULT_RTSP_PORT

        self._lock = threading.Lock()
        self._latest = None          # 最新原始帧（已拷贝，避免被主循环覆写）
        self._dets = []
        self._gesture = None
        self._stats = {}
        self._has_new = False

        self.stopping = False
        self._proc = None
        self._thread = None
        self._backoff = 0.0
        self._last_error = ""
        self._warned_unreachable = False

        self.pushed = 0
        self.dropped = 0
        self.restarts = 0

    # ------------------------------------------------------------------
    def hint(self):
        """给用户看的诊断提示（启动时打印一次）"""
        if self.port_missing:
            return ("URL 未指定端口，将使用 RTSP 默认端口 %d；"
                    "但 MediaMTX 默认监听 8554。建议改成：\n"
                    "        rtsp://%s:8554%s" % (DEFAULT_RTSP_PORT, self._host, self._path))
        return None

    def _reachable(self, timeout=2.0):
        """推流前先探一下服务器在不在，比让 ffmpeg 反复失败要清楚得多"""
        try:
            s = socket.create_connection((self._host, self._port), timeout)
            s.close()
            return True
        except OSError as e:
            self._last_error = "连不上 %s:%d (%s)" % (self._host, self._port, e.strerror or e)
            return False

    # ------------------------------------------------------------------
    def _spawn(self):
        """启动 ffmpeg：从 stdin 读 rawvideo(bgr24)，编码成 H.264 推到 RTSP"""
        if shutil.which(self.ffmpeg) is None:
            raise RuntimeError("找不到 ffmpeg，可 sudo apt install ffmpeg")

        try:
            br = int(str(self.bitrate).rstrip("kK"))
        except ValueError:
            br = 1500

        cmd = [
            self.ffmpeg,
            "-hide_banner", "-loglevel", "error",
            # 输入：原始 BGR 帧
            "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-s", "%dx%d" % (self.width, self.height),
            "-r", str(self.fps),
            "-i", "-",
            # 编码：低延迟优先
            "-c:v", "libx264",
            "-preset", self.preset,
            "-tune", "zerolatency",
            "-profile:v", "baseline",     # 兼容性最好，老播放器也能解
            "-pix_fmt", "yuv420p",
            "-g", str(self.fps * 2),      # 2 秒一个关键帧，中途拉流不用等太久
            "-b:v", self.bitrate,
            "-maxrate", self.bitrate,
            "-bufsize", "%dk" % (br * 2),
            # 输出
            "-f", "rtsp", "-rtsp_transport", "tcp",
            self.url,
        ]
        # 注意：stderr 用 DEVNULL。若用 PIPE 又不读，ffmpeg 输出一多就会写满管道卡死
        return subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)

    # ------------------------------------------------------------------
    def update(self, frame, dets=None, gesture=None, stats=None):
        """主循环调用：只覆盖最新帧，拷贝一次避免编码线程读到半张图"""
        with self._lock:
            if frame is not None:
                self._latest = frame.copy()
            self._dets = dets or []
            self._gesture = gesture
            self._stats = stats or {}
            self._has_new = True

    # ------------------------------------------------------------------
    def _writer_loop(self):
        interval = 1.0 / self.fps
        next_t = time.time()
        while not self.stopping:
            now = time.time()
            if now < next_t:
                time.sleep(min(0.01, next_t - now))
                continue

            if self._backoff > 0:
                time.sleep(self._backoff)
                self._backoff = 0.0
                continue

            next_t = now + interval

            with self._lock:
                if not self._has_new or self._latest is None:
                    continue
                frame = self._latest
                dets = self._dets
                gesture = self._gesture
                stats = self._stats
                self._has_new = False

            out = draw_frame(frame, dets, gesture, stats) if self.annotate else frame
            if out.shape[1] != self.width or out.shape[0] != self.height:
                out = cv2.resize(out, (self.width, self.height))

            try:
                if self._proc is None or self._proc.poll() is not None:
                    # 先探连通性：服务器不在就别反复起进程
                    if not self._reachable():
                        if not self._warned_unreachable:
                            print("[RTSP] %s" % self._last_error)
                            self._warned_unreachable = True
                        self.dropped += 1
                        self._backoff = min(5.0, max(1.0, self._backoff * 2 or 1.0))
                        continue
                    self._warned_unreachable = False
                    self._proc = self._spawn()
                    self.restarts += 1

                self._proc.stdin.write(out.tobytes())
                self._proc.stdin.flush()
                self.pushed += 1
                self._backoff = 0.0
            except (BrokenPipeError, OSError, ValueError, RuntimeError) as e:
                self.dropped += 1
                self._last_error = str(e)[:100]
                try:
                    if self._proc:
                        self._proc.kill()
                        self._proc.wait(timeout=1)
                except Exception:
                    pass
                self._proc = None
                self._backoff = min(5.0, max(0.5, self._backoff * 2 or 0.5))

    # ------------------------------------------------------------------
    def start(self):
        self._thread = threading.Thread(target=self._writer_loop,
                                        name="rtsp-writer", daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self.stopping = True
        if self._thread:
            self._thread.join(timeout=2)
        if self._proc:
            try:
                self._proc.stdin.close()
            except Exception:
                pass
            try:
                self._proc.wait(timeout=3)
            except Exception:
                self._proc.kill()

    def status(self):
        alive = bool(self._proc and self._proc.poll() is None)
        return {
            "url": self.url,
            "fps": self.fps,
            "bitrate": self.bitrate,
            "pushed": self.pushed,
            "dropped": self.dropped,
            "restarts": self.restarts,
            "alive": alive,
            "last_error": self._last_error,
        }

    def short_status(self):
        st = self.status()
        if st["alive"]:
            return "rtsp: on (%d)" % st["pushed"]
        if st["restarts"]:
            return "rtsp: retry (%d)" % st["restarts"]
        return "rtsp: off"