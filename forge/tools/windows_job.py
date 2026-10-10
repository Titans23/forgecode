'''Windows process ownership, without relying on a live parent PID.

The gated worker cannot launch the requested command before job assignment.
Closing the non-inheritable job handle kills every descendant, including when
the worker or an intermediate shell has already exited.
https://learn.microsoft.com/windows/win32/procthread/job-objects
'''

import ctypes
from ctypes import wintypes


class BasicLimits(ctypes.Structure):
    _fields_ = [
        ('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64),
        ('flags', wintypes.DWORD), ('minimum_ws', ctypes.c_size_t),
        ('maximum_ws', ctypes.c_size_t), ('active_processes', wintypes.DWORD),
        ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD),
        ('scheduling', wintypes.DWORD),
    ]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ('basic', BasicLimits), ('io_counters', ctypes.c_uint64 * 6),
        ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
        ('peak_process_memory', ctypes.c_size_t), ('peak_job_memory', ctypes.c_size_t),
    ]


class BasicAccounting(ctypes.Structure):
    _fields_ = [('user_time', ctypes.c_int64), ('kernel_time', ctypes.c_int64),
        ('period_user_time', ctypes.c_int64), ('period_kernel_time', ctypes.c_int64),
        ('page_faults', wintypes.DWORD), ('total_processes', wintypes.DWORD),
        ('active_processes', wintypes.DWORD), ('terminated_processes', wintypes.DWORD)]


class WindowsJob:
    def __init__(self):
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        signatures = {
            'CreateJobObjectW': ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            'SetInformationJobObject': ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
            'OpenProcess': ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            'AssignProcessToJobObject': ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            'TerminateJobObject': ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            'QueryInformationJobObject': ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p], wintypes.BOOL),
            'CloseHandle': ([wintypes.HANDLE], wintypes.BOOL),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.api, name)
            function.argtypes, function.restype = args, result
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def assign(self, pid: int):
        process = self.api.OpenProcess(0x0101, False, pid)  # SET_QUOTA | TERMINATE
        if not process:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not self.api.AssignProcessToJobObject(self.handle, process):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            self.api.CloseHandle(process)

    def terminate(self):
        if not self.handle or not self.api.TerminateJobObject(self.handle, 125):
            raise ctypes.WinError(ctypes.get_last_error())

    def active_processes(self):
        if not self.handle:
            raise ValueError('Closed job has no observable ownership')
        accounting = BasicAccounting()
        if not self.api.QueryInformationJobObject(self.handle, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None):
            raise ctypes.WinError(ctypes.get_last_error())
        return accounting.active_processes

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


# Raw os.read consumes exactly the gate byte, without buffering user stdin.
GATED_WORKER = (
    'import os,sys,json,subprocess; '
    'gate=os.read(0,1); '
    'sys.exit(subprocess.call(json.loads(sys.argv[1]), shell=sys.argv[2]=="1") '
    'if gate==b"!" else 125)'
)


class AnonymousPipeProcess:
    """Async access to stdlib anonymous pipes for the DSH restricted token.

    Python's Proactor subprocess transport creates duplex named pipes, whose
    reopening fails under this token. Reading anonymous handles in threads
    preserves bounded output and lets the existing Job terminate all writers.
    """
    class Reader:
        def __init__(self, stream):
            self.stream = stream

        async def read(self, size):
            import asyncio
            return await asyncio.to_thread(self.stream.read, size)

    class Writer:
        def __init__(self, stream):
            self.stream, self.pending = stream, bytearray()

        def write(self, raw):
            self.pending.extend(raw)

        async def drain(self):
            import asyncio
            raw = bytes(self.pending)
            self.pending.clear()
            await asyncio.to_thread(self.stream.write, raw)
            await asyncio.to_thread(self.stream.flush)

        def close(self):
            self.stream.close()

    def __init__(self, process):
        self.process, self.pid = process, process.pid
        self.stdin = self.Writer(process.stdin) if process.stdin else None
        self.stdout, self.stderr = self.Reader(process.stdout), self.Reader(process.stderr)

    @classmethod
    async def start(cls, command, **options):
        import asyncio
        import subprocess
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
        process = await asyncio.to_thread(subprocess.Popen, command, startupinfo=startup, **options)
        return cls(process)

    @property
    def returncode(self):
        return self.process.poll()

    async def wait(self):
        import asyncio
        return await asyncio.to_thread(self.process.wait)

    def kill(self):
        self.process.kill()

    async def communicate(self):
        import asyncio
        return await asyncio.to_thread(self.process.communicate)
