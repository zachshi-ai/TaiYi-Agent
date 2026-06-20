#!/bin/bash
# Helix Demo 一键运行脚本
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=========================================="
echo "  Helix Demo - PDCA Loop 可行性验证"
echo "=========================================="
echo ""

# 清理上次运行
rm -rf /tmp/helix_demo* 2>/dev/null || true

# 跑主程序
python3 src/main.py

echo ""
echo "=========================================="
echo "  Demo 运行完成"
echo "=========================================="
echo ""
echo "产物在:"
echo "  /tmp/helix_demo/memory/    - 5 层记忆"
echo "  /tmp/helix_demo/skills/    - 技能库"
echo "  /tmp/helix_demo/scenarios/ - 场景库"
echo ""
