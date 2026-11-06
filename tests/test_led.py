#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_led.py —— 验证三个手势输出引脚（走 /sys/class/leds）

引脚对应（K1 MINI 出厂 DTS 已注册为 LED 节点）：
    gpio3b6 -> 40Pin Pin32 (GPIO3_B6)
    gpio4c6 -> 40Pin Pin25 (GPIO4_C6)
    gpio4c4 -> 40Pin Pin27 (GPIO4_C4)

用法：
    sudo python3 test_led.py            # 依次点亮三个脚
    sudo python3 test_led.py gpio3b6    # 只点一个
"""
import os
import sys
import time

LED_ROOT = "/sys/class/leds"
LEDS = ["gpio3b6", "gpio4c4", "gpio4c6"]


def set_led(name, on):
    path = os.path.join(LED_ROOT, name, "brightness")
    with open(path, "w") as f:
        f.write("1" if on else "0")


def main():
    names = sys.argv[1:] if len(sys.argv) > 1 else LEDS

    # 先检查节点存在且可写
    for n in names:
        p = os.path.join(LED_ROOT, n, "brightness")
        if not os.path.exists(p):
            print("ERROR: %s 不存在" % p)
            return 1
        if not os.access(p, os.W_OK):
            print("ERROR: %s 不可写，请用 sudo 运行" % p)
            return 1
    print("三个 LED 节点均可写，开始测试...")

    for n in names:
        print("  %s -> 1" % n)
        set_led(n, 1)
        time.sleep(1.0)
        print("  %s -> 0" % n)
        set_led(n, 0)
        time.sleep(0.3)

    # 收尾确保全灭
    for n in names:
        set_led(n, 0)
    print("全部熄灭，OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())