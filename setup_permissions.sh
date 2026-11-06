#!/bin/bash
# setup_permissions.sh —— 让普通用户也能写 LED 节点（免 sudo）
#
# 默认 /sys/class/leds/<name>/brightness 是 root:root 644，重启后权限会重置，
# 所以用 udev 规则固化。这里对所有 gpio* 节点统一下发。
#
# 用法：bash setup_permissions.sh    （内部会用 sudo 提权）
set -e

RULE=/etc/udev/rules.d/99-kickpi-led-acc.rules

if [ "$(id -u)" -ne 0 ]; then
    echo "需要 root，改用 sudo 重新执行..."
    exec sudo bash "$0" "$@"
fi

cat > "$RULE" <<'EOF'
# K1 MINI 40Pin 上被注册为 LED 的引脚，允许普通用户写 brightness
SUBSYSTEM=="leds", KERNEL=="gpio*", RUN+="/bin/chmod 666 /sys/class/leds/%k/brightness"
EOF

echo "已写入 $RULE"
udevadm control --reload-rules 2>/dev/null || true
udevadm trigger --subsystem-match=leds 2>/dev/null || true
sleep 1

# 当前这次也立刻生效（不用重启）
for p in /sys/class/leds/gpio*/brightness; do
    [ -e "$p" ] && chmod 666 "$p"
done

echo "当前权限："
ls -l /sys/class/leds/gpio*/brightness
echo
echo "完成。之后可以直接 python3 main.py 运行（无需 sudo）。"