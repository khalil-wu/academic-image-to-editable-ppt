"""Build the public draw.io example from the shared Live scene and lossless assets."""
from pathlib import Path
import subprocess
import sys

case = Path(__file__).resolve().parent
root = case.parents[1]
live = root / 'examples' / 'live'
output = case / 'output' / 'EEGAgent-Live-editable.drawio'
command = [sys.executable, str(root / 'scripts' / 'build_drawio.py'),
           str(live / 'scene.json'), str(output), '--assets', str(live / 'assets.json'), '--bind-nearby']
for font in sorted((case / 'fonts').glob('*.ttf')):
    command.extend(['--embed-font', str(font)])
subprocess.run(command, check=True)
subprocess.run([sys.executable, str(root / 'scripts' / 'audit_drawio.py'), str(output),
                '--output', str(output.with_suffix('.audit.json'))], check=True)
