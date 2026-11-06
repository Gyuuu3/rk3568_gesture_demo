#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""yolo_onnx.py —— 用 onnxruntime 跑 YOLO 检测（支持 YOLOv5 / YOLOv8 导出格式）

不依赖 torch，只用 numpy + onnxruntime，因此在 RK3568 这种 ARM 板上也能跑。
"""
import numpy as np

try:
    import onnxruntime as ort
except ImportError:  # pragma: no cover
    ort = None


def letterbox(img, new_shape=320, color=(114, 114, 114)):
    """等比缩放 + 灰边填充到正方形，返回 (图, 缩放比, 左侧填充, 上侧填充)"""
    import cv2

    h, w = img.shape[:2]
    r = min(new_shape / h, new_shape / w)
    nh, nw = int(round(h * r)), int(round(w * r))
    if (nh, nw) != (h, w):
        img = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)

    dw = (new_shape - nw) / 2.0
    dh = (new_shape - nh) / 2.0
    top, bottom = int(round(dh - 0.1)), int(round(dh + 0.1))
    left, right = int(round(dw - 0.1)), int(round(dw + 0.1))

    out = np.full((new_shape, new_shape, 3), 114, dtype=np.uint8)
    out[top:top + nh, left:left + nw] = img
    return out, r, left, top


def nms(boxes, scores, iou_thres):
    """纯 numpy 实现的 NMS，返回保留的下标"""
    if len(boxes) == 0:
        return []
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = (x2 - x1) * (y2 - y1)
    order = scores.argsort()[::-1]
    keep = []
    while order.size > 0:
        i = order[0]
        keep.append(int(i))
        if order.size == 1:
            break
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        w = np.maximum(0.0, xx2 - xx1)
        h = np.maximum(0.0, yy2 - yy1)
        inter = w * h
        iou = inter / np.maximum(areas[i] + areas[order[1:]] - inter, 1e-9)
        order = order[1:][iou <= iou_thres]
    return keep


class YoloOnnx:
    """YOLO ONNX 推理封装"""

    def __init__(self, model_path, class_names, input_size=320,
                 conf_thres=0.45, iou_thres=0.45, num_threads=4):
        if ort is None:
            raise RuntimeError("未安装 onnxruntime，请先 pip install onnxruntime")

        so = ort.SessionOptions()
        so.intra_op_num_threads = num_threads
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.sess = ort.InferenceSession(model_path, so, providers=["CPUExecutionProvider"])
        self.input_name = self.sess.get_inputs()[0].name
        self.input_shape = self.sess.get_inputs()[0].shape
        self.class_names = class_names
        self.nc = len(class_names)
        self.input_size = input_size
        self.conf = conf_thres
        self.iou = iou_thres

        # 判断是 v5 还是 v8 格式，只需看一次
        self.fmt = None

    # ------------------------------------------------------------------
    def _decode(self, out, r, left, top, orig_shape):
        """把网络输出解码成 box 列表，坐标还原到原图"""
        if out.ndim == 3:
            out = out[0]

        # v8: (4+nc, N)  -> 转置成 (N, 4+nc)
        # v5: (N, 5+nc)
        if out.shape[0] < out.shape[1] and out.shape[0] <= 4 + self.nc + 1:
            out = out.T
            self.fmt = "v8"
        else:
            self.fmt = "v5"

        if self.fmt == "v8":
            # 无 objectness
            scores = out[:, 4:]
            cls_ids = scores.argmax(axis=1)
            cls_scores = scores[np.arange(len(scores)), cls_ids]
            boxes = out[:, :4]
        else:
            scores = out[:, 4:]
            cls_ids = scores.argmax(axis=1)
            cls_scores = scores[np.arange(len(scores)), cls_ids] * out[:, 4]
            boxes = out[:, :4]

        mask = cls_scores >= self.conf
        if not np.any(mask):
            return []
        boxes, cls_ids, cls_scores = boxes[mask], cls_ids[mask], cls_scores[mask]

        # cxcywh -> xyxy
        cx, cy, w, h = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
        x1 = cx - w / 2.0
        y1 = cy - h / 2.0
        x2 = cx + w / 2.0
        y2 = cy + h / 2.0
        boxes_xyxy = np.stack([x1, y1, x2, y2], axis=1)

        # letterbox 还原
        boxes_xyxy[:, [0, 2]] = (boxes_xyxy[:, [0, 2]] - left) / r
        boxes_xyxy[:, [1, 3]] = (boxes_xyxy[:, [1, 3]] - top) / r

        H, W = orig_shape[:2]
        boxes_xyxy[:, [0, 2]] = np.clip(boxes_xyxy[:, [0, 2]], 0, W - 1)
        boxes_xyxy[:, [1, 3]] = np.clip(boxes_xyxy[:, [1, 3]], 0, H - 1)

        keep = nms(boxes_xyxy, cls_scores, self.iou)
        dets = []
        for i in keep:
            dets.append({
                "box": [float(v) for v in boxes_xyxy[i]],
                "score": float(cls_scores[i]),
                "cls": int(cls_ids[i]),
                "name": self.class_names[int(cls_ids[i])],
            })
        return dets

    # ------------------------------------------------------------------
    def detect(self, frame_bgr):
        """输入 BGR 原图，返回检测列表"""
        import cv2

        img, r, left, top = letterbox(frame_bgr, self.input_size)
        blob = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        blob = np.transpose(blob, (2, 0, 1))[None]  # 1,3,H,W
        out = self.sess.run(None, {self.input_name: blob})[0]
        return self._decode(out, r, left, top, frame_bgr.shape)