"""Windows job ownership and nonblocking anonymous-pipe reads (no console)."""
import ctypes
from ctypes import wintypes as w
import msvcrt
import os
import time

kernel = ctypes.WinDLL("kernel32", use_last_error=True)
kernel.CreateJobObjectW.argtypes = [w.LPVOID, w.LPCWSTR]
kernel.CreateJobObjectW.restype = w.HANDLE
kernel.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD]
kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
kernel.TerminateJobObject.argtypes = [w.HANDLE, w.UINT]
kernel.CloseHandle.argtypes = [w.HANDLE]
kernel.QueryInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD, w.LPVOID]
kernel.GetProcessId.argtypes = [w.HANDLE]
kernel.GetProcessId.restype = w.DWORD
kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
kernel.OpenProcess.restype = w.HANDLE
kernel.IsProcessInJob.argtypes = [w.HANDLE, w.HANDLE, ctypes.POINTER(w.BOOL)]
kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
kernel.WaitForSingleObject.restype = w.DWORD
kernel.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
kernel.CreateToolhelp32Snapshot.restype = w.HANDLE
kernel.OpenThread.argtypes = [w.DWORD, w.BOOL, w.DWORD]
kernel.OpenThread.restype = w.HANDLE
kernel.ResumeThread.argtypes = [w.HANDLE]
kernel.ResumeThread.restype = w.DWORD
kernel.PeekNamedPipe.argtypes = [w.HANDLE, w.LPVOID, w.DWORD, w.LPVOID,
                               ctypes.POINTER(w.DWORD), w.LPVOID]


class BasicLimits(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong), ("LimitFlags", w.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", w.DWORD), ("Affinity", ctypes.c_size_t),
                ("PriorityClass", w.DWORD), ("SchedulingClass", w.DWORD)]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", BasicLimits), ("IoInfo", ctypes.c_ulonglong * 6),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


class ThreadEntry(ctypes.Structure):
    _fields_ = [("dwSize", w.DWORD), ("cntUsage", w.DWORD), ("th32ThreadID", w.DWORD),
                ("th32OwnerProcessID", w.DWORD), ("tpBasePri", w.LONG),
                ("tpDeltaPri", w.LONG), ("dwFlags", w.DWORD)]


kernel.Thread32First.argtypes = [w.HANDLE, ctypes.POINTER(ThreadEntry)]
kernel.Thread32Next.argtypes = [w.HANDLE, ctypes.POINTER(ThreadEntry)]


class ProcessJob:
    def __init__(self, process, *, allow_browser_breakaway=False):
        self.handle = kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            limits = ExtendedLimits()
            limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
            if allow_browser_breakaway:
                # Only explicitly detached WPS browser launches leave this job.
                limits.BasicLimitInformation.LimitFlags |= 0x0800  # BREAKAWAY_OK
            if not kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise ctypes.WinError(ctypes.get_last_error())
            if not kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            self.close()
            raise

    def terminate(self):
        if self.handle:
            # Capture before termination: the job PID list can remove a dying
            # process before its kernel process object becomes signalled.
            self._capture_for_cleanup()
            if not kernel.TerminateJobObject(self.handle, 1):
                raise ctypes.WinError(ctypes.get_last_error())
            self._capture_for_cleanup()

    def _owned_process_handles(self):
        capacity = 64
        while True:
            class Pids(ctypes.Structure):
                _fields_ = [("assigned", w.DWORD), ("count", w.DWORD),
                            ("pids", ctypes.c_size_t * capacity)]
            listing = Pids()
            if kernel.QueryInformationJobObject(self.handle, 3, ctypes.byref(listing), ctypes.sizeof(listing), None):
                break
            error = ctypes.get_last_error()
            if error != 234:
                raise ctypes.WinError(error)
            capacity *= 2
        handles = []
        try:
            for pid in listing.pids[:listing.count]:
                handle = kernel.OpenProcess(0x00100000 | 0x1000, False, pid)
                if not handle:
                    error = ctypes.get_last_error()
                    if error == 87:
                        continue
                    raise ctypes.WinError(error)
                handles.append(handle)
                member = w.BOOL()
                if not kernel.IsProcessInJob(handle, self.handle, ctypes.byref(member)):
                    raise ctypes.WinError(ctypes.get_last_error())
                if not member.value:
                    kernel.CloseHandle(handles.pop())
            return handles
        except BaseException:
            for handle in handles:
                kernel.CloseHandle(handle)
            raise

    def _capture_for_cleanup(self):
        self._cleanup_handles = getattr(self, "_cleanup_handles", [])
        identities = set()
        for handle in self._cleanup_handles:
            pid = kernel.GetProcessId(handle)
            if not pid:
                raise ctypes.WinError(ctypes.get_last_error())
            identities.add(pid)
        captured = self._owned_process_handles()
        while captured:
            handle = captured.pop()
            pid = kernel.GetProcessId(handle)
            if not pid:
                error = ctypes.get_last_error()
                kernel.CloseHandle(handle)
                for remaining in captured:
                    kernel.CloseHandle(remaining)
                raise ctypes.WinError(error)
            if pid in identities:
                kernel.CloseHandle(handle)
            else:
                self._cleanup_handles.append(handle)
                identities.add(pid)

    def wait_empty(self, timeout):
        deadline = time.monotonic() + timeout
        handles = getattr(self, "_cleanup_handles", None)
        retained = handles is not None
        if handles is None:
            handles = self._owned_process_handles()
        try:
            while True:
                if retained:
                    self._capture_for_cleanup()
                live = False
                for handle in handles:
                    result = kernel.WaitForSingleObject(handle, 0)
                    if result == 258:
                        live = True
                    elif result != 0:
                        raise ctypes.WinError(ctypes.get_last_error())
                if not live:
                    return
                if time.monotonic() >= deadline:
                    raise TimeoutError("后台进程清理尚未完成")
                time.sleep(.01)
        finally:
            if not retained:
                for handle in handles:
                    kernel.CloseHandle(handle)

    def resume(self, pid):
        # Popen closes the initial thread handle. The process was created with
        # CREATE_SUSPENDED, so enumerate its owned initial thread and resume it
        # only after assignment. No application code can escape the job first.
        snapshot = kernel.CreateToolhelp32Snapshot(4, 0)  # TH32CS_SNAPTHREAD
        if snapshot == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        resumed = False
        try:
            entry = ThreadEntry()
            entry.dwSize = ctypes.sizeof(entry)
            found = kernel.Thread32First(snapshot, ctypes.byref(entry))
            while found:
                if entry.th32OwnerProcessID == pid:
                    thread = kernel.OpenThread(2, False, entry.th32ThreadID)
                    if not thread:
                        raise ctypes.WinError(ctypes.get_last_error())
                    try:
                        previous = kernel.ResumeThread(thread)
                        if previous == 0xFFFFFFFF:
                            raise ctypes.WinError(ctypes.get_last_error())
                        resumed = resumed or previous > 0
                    finally:
                        kernel.CloseHandle(thread)
                entry.dwSize = ctypes.sizeof(entry)
                found = kernel.Thread32Next(snapshot, ctypes.byref(entry))
            if not resumed:
                raise RuntimeError("无法恢复后台任务的初始线程")
        finally:
            kernel.CloseHandle(snapshot)

    def close(self):
        if self.handle:
            for handle in getattr(self, "_cleanup_handles", []):
                kernel.CloseHandle(handle)
            self._cleanup_handles = []
            kernel.CloseHandle(self.handle)
            self.handle = None


def read_available(descriptor):
    available = w.DWORD()
    if not kernel.PeekNamedPipe(msvcrt.get_osfhandle(descriptor), None, 0, None,
                               ctypes.byref(available), None):
        error = ctypes.get_last_error()
        if error in (109, 232):  # broken pipe / no data after close
            return b""
        raise ctypes.WinError(error)
    return os.read(descriptor, min(available.value, 65536)) if available.value else None
