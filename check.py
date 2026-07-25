import os
from pathlib import Path

# 替换为你的项目路径
root = Path(r"C:\Users\guhao\PyCharmMiscProject")

print("=== 🔍 路径诊断开始 ===")
print(f"当前扫描根目录: {root.absolute()}")

# 获取所有子文件夹
subdirs = [d for d in root.iterdir() if d.is_dir()]
print(f"找到子文件夹总数: {len(subdirs)}\n")

for d in subdirs:
    # 忽略 Python 虚拟环境和 IDE 配置
    if d.name.startswith('.') or d.name in ['venv', '__pycache__']:
        continue

    # 递归查找该文件夹下的所有文件
    all_files = [f for f in d.rglob("*") if f.is_file()]
    # 获取所有的后缀名集合
    suffixes = set(f.suffix.lower() for f in all_files)

    print(f"📁 文件夹: {d.name}")
    print(f"   ├─ 内部文件总数: {len(all_files)}")
    print(f"   ├─ 包含的文件后缀: {suffixes if suffixes else '无后缀/空文件夹'}")
    if all_files:
        print(f"   └─ 示例文件名: {all_files[0].name}")
    print("-" * 50)