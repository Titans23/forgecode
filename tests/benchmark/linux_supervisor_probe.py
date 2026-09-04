'''Run directly in a disposable Linux container; no model/network needed.'''

import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time


with tempfile.TemporaryDirectory() as directory:
    for mode in ('success', 'cancel', 'timeout'):
        pid_file = Path(directory) / mode
        child_code = (
            'import os,time; from pathlib import Path; '
            f'Path({str(pid_file)!r}).write_text(str(os.getpid())); time.sleep(60)'
        )
        parent_code = (
            'import subprocess,sys,time; '
            f'subprocess.Popen([sys.executable,"-c",{child_code!r}], start_new_session=True); '
            + ('time.sleep(0.3)' if mode == 'success' else 'time.sleep(60)')
        )
        argv = [sys.executable, '-m', 'benchmark.harbor.process_supervisor', '--',
                sys.executable, '-c', parent_code]
        if mode == 'timeout':
            argv = ['timeout', '--kill-after=10s', '1', *argv]
        process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        deadline = time.monotonic() + 5
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert pid_file.exists(), mode
        if mode == 'cancel':
            process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=8)
        assert process.returncode == {'success': 0, 'cancel': 143, 'timeout': 124}[mode], stderr
        assert not Path(f'/proc/{pid_file.read_text()}').exists(), mode
        print(f'{mode}: detached descendant reaped, inherited pipes closed')
