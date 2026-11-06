#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_mapping.py —— 不接摄像头，直接验证「手势名 -> 引脚」映射

依次模拟 石头/布/剪刀/无手势，每次读取三个引脚的当前电平打印出来。

用法（在仓库根目录）:
    python3 tests/test_mapping.py
"""
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from led_out import GestureLed  # noqa: E402


def read_all(pins):
    out = {}
    for p in pins:
        try:
            with open("/sys/class/leds/%s/brightness" % p) as f:
                out[p] = f.read().strip()
        except Exception:
            out[p] = "?"
    return out


def main():
    with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as f:
        conf = json.load(f)

    led = GestureLed(conf["gesture_to_led"])
    pins = led.pins
    print("引脚集合: %s" % pins)
    print("映射: %s\n" % conf["gesture_to_led"])

    ok = True
    for g in ["rock", "paper", "scissors", None]:
        led.set_gesture(g)
        time.sleep(0.6)
        state = read_all(pins)
        expect = {p: "0" for p in pins}
        if g:
            expect[conf["gesture_to_led"][g]] = "1"
        good = (state == expect)
        ok = ok and good
        tag = g if g else "(无手势)"
        print("%-10s 期望=%s 实际=%s  %s" % (tag, expect, state, "OK" if good else "FAIL"))

    led.all_off()
    print("\n结果: %s" % ("全部通过" if ok else "有失败"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())