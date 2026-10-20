"""从已拆好的场景和高清裁片重建示例；不描摹，也不调用图像API。"""
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[2]
case = Path(__file__).resolve().parent
out = case / 'output'
steps = [
    ['build_native_layout.py', case/'scene.json', out/'layout.pptx'],
    ['insert_assets_pptx.py', out/'layout.pptx', case/'assets.json', out/'with-assets.pptx'],
    ['group_and_connect.py', out/'with-assets.pptx', case/'scene.json', out/'figure.pptx'],
    ['audit_pptx.py', out/'figure.pptx', '--expect', case/'scene.json', '--assets-report', out/'with-assets.assets.json', '--output', out/'audit.json'],
]
for script, *args in steps:
    subprocess.run([sys.executable, str(root/'scripts'/script), *map(str,args)], check=True)
