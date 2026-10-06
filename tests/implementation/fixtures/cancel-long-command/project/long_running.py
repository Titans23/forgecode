import json
import os
from pathlib import Path
import subprocess
import sys
import time

child = subprocess.Popen([sys.executable, str(Path(__file__).with_name('child.py'))])
Path('processes.json').write_text(json.dumps({'parent': os.getpid(), 'child': child.pid}), encoding='utf-8')
try:
    while True:
        time.sleep(1)
finally:
    child.terminate()
    child.wait(timeout=5)
