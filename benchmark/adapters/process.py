"""Actual owned runner process with bounded output and cancellation."""
import asyncio
import os
from pathlib import Path
import signal
import subprocess


async def run_process(argv, *, cwd, env, output_dir, timeout_seconds):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    options = {'creationflags':subprocess.CREATE_NEW_PROCESS_GROUP} if os.name=='nt' else {'start_new_session':True}
    process = await asyncio.create_subprocess_exec(*argv,cwd=cwd,env=env,
        stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,**options)
    truncated = {'stdout':False,'stderr':False}

    async def drain(stream, name):
        remaining = 4 * 1024 * 1024
        with (output_dir / (name+'.log')).open('wb') as destination:
            while value := await stream.read(65536):
                destination.write(value[:remaining])
                remaining = max(0,remaining-len(value))
                if remaining == 0:
                    truncated[name] = True
    readers = [asyncio.create_task(drain(process.stdout,'stdout')),asyncio.create_task(drain(process.stderr,'stderr'))]
    try:
        async with asyncio.timeout(timeout_seconds):
            code = await process.wait()
            await asyncio.gather(*readers)
        return {'exit_code':code,'pid':process.pid,'output_truncated':truncated}
    finally:
        if process.returncode is None or os.name!='nt' and any(not reader.done() for reader in readers):
            if os.name=='nt':
                killer = await asyncio.create_subprocess_exec('taskkill','/PID',str(process.pid),'/T','/F',
                    stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
                await killer.wait()
            else:
                try:
                    os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:
                    pass
            await process.wait()
        for reader in readers:
            if not reader.done():
                reader.cancel()
        await asyncio.gather(*readers,return_exceptions=True)
