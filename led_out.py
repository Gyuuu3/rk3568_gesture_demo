#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""led_out.py —— 把「手势」映射成三个扩展引脚的输出电平

硬件（K1 MINI 出厂 DTS 已把这几个脚注册为 LED 节点）：
    gpio3b6 -> 40Pin Pin32  (GPIO3_B6)
    gpio1d0 -> 40Pin Pin36  (GPIO1_D0)
    gpio1d4 -> 40Pin Pin38  (GPIO1_D4)

规则：
    识别到某个手势 -> 对应引脚输出 1，其余两个输出 0
    没识别到任何手势 -> 三个都输出 0
"""
import os

LED_ROOT = "/sys/class/leds"


class GestureLed:
    def __init__(self, mapping):
        """
        mapping: dict  手势名 -> LED 节点名
            例：{"rock": "gpio1d0", "paper": "gpio1d4", "scissors": "gpio3b6"}
        """
        self.mapping = dict(mapping)
        self.pins = sorted(set(self.mapping.values()))
        self._cache = {}
        self._check()

    def _path(self, pin):
        return os.path.join(LED_ROOT, pin, "brightness")

    def _check(self):
        missing = [p for p in self.pins if not os.path.exists(self._path(p))]
        if missing:
            raise RuntimeError("以下 LED 节点不存在: %s（可用: %s）" % (
                missing, sorted(os.listdir(LED_ROOT))))
        nowritable = [p for p in self.pins if not os.access(self._path(p), os.W_OK)]
        if nowritable:
            raise RuntimeError("%s 不可写，请用 sudo 运行本程序" % nowritable)

    def _write(self, pin, val):
        if self._cache.get(pin) == val:
            return  # 避免无意义写 sysfs
        with open(self._path(pin), "w") as f:
            f.write("1" if val else "0")
        self._cache[pin] = val

    def set_gesture(self, gesture):
        """gesture 为 None 表示未识别到，全部输出 0"""
        target = self.mapping.get(gesture) if gesture else None
        for pin in self.pins:
            self._write(pin, 1 if pin == target else 0)

    def all_off(self):
        for pin in self.pins:
            self._write(pin, 0)

    def current(self):
        return dict(self._cache)