"""Observe the exact live Main parent. This handle has no kill/takeover authority."""
import asyncio
import os
import sys

from forge.application.models import ContractError


class ParentLease:
    def __init__(self, handle, kernel=None):
        self.handle, self.kernel = handle, kernel

    @classmethod
    def open(cls, pid):
        if type(pid) is not int or pid <= 0 or pid == os.getpid():
            raise ContractError('Main owner is not this Engine parent',kind='UNAUTHORIZED',code=-32010)
        if sys.platform=='win32':
            import ctypes
            from ctypes import wintypes
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
            kernel.OpenProcess.restype=wintypes.HANDLE
            kernel.GetCurrentProcess.restype=wintypes.HANDLE
            kernel.GetProcessTimes.argtypes=[wintypes.HANDLE,*([ctypes.POINTER(wintypes.FILETIME)]*4)]
            kernel.GetProcessTimes.restype=wintypes.BOOL
            kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
            kernel.WaitForSingleObject.restype=wintypes.DWORD
            kernel.CloseHandle.argtypes=[wintypes.HANDLE]
            kernel.CloseHandle.restype=wintypes.BOOL
            handle=kernel.OpenProcess(0x00100000|0x1000,False,pid) # SYNCHRONIZE + QUERY_LIMITED_INFORMATION only.
            if not handle:
                raise ContractError('Main parent handle is unavailable',kind='INDETERMINATE',code=-32010)
            try:
                def created(process):
                    fields=[wintypes.FILETIME() for _ in range(4)]
                    if not kernel.GetProcessTimes(process,*[ctypes.byref(x) for x in fields]):
                        raise OSError('Process identity cannot be verified')
                    return (fields[0].dwHighDateTime<<32)|fields[0].dwLowDateTime
                parent=os.getppid()
                if parent!=pid:
                    # Windows venv/one-file bootloaders introduce one exact executable relay.
                    # Check its image, creation order and actual parent; never walk arbitrary ancestors.
                    from pathlib import Path
                    class Entry(ctypes.Structure):
                        _fields_=[('size',wintypes.DWORD),('usage',wintypes.DWORD),('pid',wintypes.DWORD),
                            ('heap',ctypes.c_size_t),('module',wintypes.DWORD),('threads',wintypes.DWORD),
                            ('parent',wintypes.DWORD),('priority',wintypes.LONG),('flags',wintypes.DWORD),('exe',wintypes.WCHAR*260)]
                    kernel.CreateToolhelp32Snapshot.argtypes=[wintypes.DWORD,wintypes.DWORD]
                    kernel.CreateToolhelp32Snapshot.restype=wintypes.HANDLE
                    kernel.Process32FirstW.argtypes=[wintypes.HANDLE,ctypes.POINTER(Entry)]
                    kernel.Process32NextW.argtypes=[wintypes.HANDLE,ctypes.POINTER(Entry)]
                    kernel.QueryFullProcessImageNameW.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD)]
                    relay=kernel.OpenProcess(0x00100000|0x1000,False,parent)
                    snapshot=kernel.CreateToolhelp32Snapshot(2,0)
                    try:
                        if not relay or snapshot==ctypes.c_void_p(-1).value:
                            raise OSError('Main relay identity is unavailable')
                        buffer=ctypes.create_unicode_buffer(32768);size=wintypes.DWORD(len(buffer))
                        if not kernel.QueryFullProcessImageNameW(relay,0,buffer,ctypes.byref(size)) or Path(buffer.value).resolve()!=Path(sys.executable).resolve():
                            raise OSError('Main relay is not the exact Engine executable')
                        entry=Entry();entry.size=ctypes.sizeof(entry)
                        found=kernel.Process32FirstW(snapshot,ctypes.byref(entry))
                        while found and entry.pid!=parent:
                            found=kernel.Process32NextW(snapshot,ctypes.byref(entry))
                        if not found or entry.parent!=pid or kernel.WaitForSingleObject(relay,0)!=258 or not created(handle)<=created(relay)<=created(kernel.GetCurrentProcess()):
                            raise OSError('Main relay ownership changed')
                    finally:
                        if relay:kernel.CloseHandle(relay)
                        if snapshot and snapshot!=ctypes.c_void_p(-1).value:kernel.CloseHandle(snapshot)
                if created(handle)>created(kernel.GetCurrentProcess()):
                    raise OSError('Main PID was reused')
                return cls(handle,kernel)
            except BaseException:
                kernel.CloseHandle(handle)
                raise
        if sys.platform.startswith('linux') and hasattr(os,'pidfd_open'):
            if os.getppid()!=pid:
                raise ContractError('Main owner is not this Engine parent',kind='UNAUTHORIZED',code=-32010)
            handle=os.pidfd_open(pid,0)
            if os.getppid()!=pid:
                os.close(handle)
                raise ContractError('Main parent ownership changed',kind='INDETERMINATE',code=-32010)
            return cls(handle)
        raise ContractError('Live Main monitoring is unavailable',kind='UNSUPPORTED_PLATFORM',code=-32010)

    def alive(self):
        if self.handle is None:
            return False
        if self.kernel:
            return self.kernel.WaitForSingleObject(self.handle,0)==258
        import select
        return not select.select([self.handle],[],[],0)[0]

    async def wait(self):
        while self.alive():
            await asyncio.sleep(0.05)

    def close(self):
        if self.handle is not None:
            self.kernel.CloseHandle(self.handle) if self.kernel else os.close(self.handle)
            self.handle=None
