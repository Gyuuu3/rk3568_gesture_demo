#!/bin/bash
# run.sh —— 手势识别 demo 启动脚本
#
# 用法：
#   ./run.sh                 正常跑
#   ./run.sh --debug         每帧打印检测细节
#   ./run.sh --save out/     保存带框图像
#   ./run.sh --frames 300    跑 300 帧后退出
#   ./run.sh --no-led        只识别不驱动引脚
#
# 权限说明：
#   程序要写 /sys/class/leds/*/brightness。执行过 setup_permissions.sh 后
#   普通用户就能写，直接跑；否则自动改用 sudo（并把用户库通过 PYTHONPATH 带进去，
#   因为 pip --user 装的包 root 看不到）。
set -e
cd "$(dirname "$0")"

if [ ! -f models/hand_gesture.onnx ]; then
    echo "ERROR: 缺少模型 models/hand_gesture.onnx"
    echo "       先执行: python3 export_onnx.py"
    exit 1
fi

LED_PATH="/sys/class/leds/gpio1d0/brightness"

if [ -w "$LED_PATH" ]; then
    exec python3 main.py "$@"
fi

echo "[info] LED 节点不可写，改用 sudo 运行"
echo "       想免 sudo：bash setup_permissions.sh"
PYVER=$(python3 -c 'import sys;print("python%d.%d" % sys.version_info[:2])')
USER_SITE="$HOME/.local/lib/$PYVER/site-packages"
if [ -d "$USER_SITE" ]; then
    exec sudo env "PYTHONPATH=$USER_SITE" python3 main.py "$@"
else
    exec sudo python3 main.py "$@"
fi