"""Which OS process owns a session, and whether it is still running.

No hook payload carries a PID, so a session killed without `SessionEnd` -- a
closed window, an ended process -- could only be inferred from silence, and
silence cannot tell a killed session from a parked one. The only exit such a row
had was the 24-hour ghost cutoff.

The PID is found here, not sent by the hooks. The forwarder is `curl.exe`, and
it stays connected while the daemon handles its request, so the TCP table names
its PID; walking up from curl reaches the Claude Code process that ran the hook.
Nothing about the hooks changes and no hook pays for it beyond the first event
of each session.

Two rules keep a wrong answer harmless:

- PIDs are recycled, so a PID is only ever trusted together with its process's
  creation time. The ancestor walk also stops at any parent created after its
  child, which is a parent PID that has since been reused.
- Anything inconclusive is None, never "dead". A wrong "alive" costs nothing
  new, because the 24-hour cutoff still applies. A wrong "dead" hides a session
  that may be waiting on you, which is the one mistake this must not make.

The I/O lives here rather than in state.py, which stays pure. Every function
returns None off Windows and never raises.
"""

from __future__ import annotations

import logging
import socket
import sys
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Process image names that own a Claude Code session: the native build, and the
# npm build, which runs under node.
SESSION_EXES = frozenset({"claude.exe", "node.exe"})
# Expected chain is curl -> shell -> claude.exe. The slack covers wrappers
# and nested shells without walking out into unrelated parents.
MAX_DEPTH = 8

_TCP_TABLE_OWNER_PID_CONNECTIONS = 4
_TH32CS_SNAPPROCESS = 0x2
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259
_ERROR_INVALID_PARAMETER = 87
_ERROR_INSUFFICIENT_BUFFER = 122
# Seconds between 1601-01-01 (FILETIME) and 1970-01-01 (time.time()).
_EPOCH_DELTA = 11644473600


@dataclass(frozen=True)
class SessionProcess:
    pid: int
    created: float        # time.time() scale; the half of the key that survives reuse
    chain: tuple[str, ...]  # image names from the hook up to the owner, for logs


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _iphlp = ctypes.WinDLL("iphlpapi")

    _k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _k32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    _k32.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
    _k32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    _k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE

    class _PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    _k32.Process32FirstW.argtypes = (wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W))
    _k32.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W))

    class _MIB_TCPROW_OWNER_PID(ctypes.Structure):
        _fields_ = [
            ("dwState", wintypes.DWORD),
            ("dwLocalAddr", wintypes.DWORD),
            ("dwLocalPort", wintypes.DWORD),
            ("dwRemoteAddr", wintypes.DWORD),
            ("dwRemotePort", wintypes.DWORD),
            ("dwOwningPid", wintypes.DWORD),
        ]

    _iphlp.GetExtendedTcpTable.argtypes = (
        ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), wintypes.BOOL,
        wintypes.ULONG, ctypes.c_int, wintypes.ULONG)
    _iphlp.GetExtendedTcpTable.restype = wintypes.DWORD

    _INVALID_HANDLE = wintypes.HANDLE(-1).value


def _creation_time(handle) -> float | None:
    times = [wintypes.FILETIME() for _ in range(4)]
    if not _k32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
        return None
    ft = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
    return ft / 1e7 - _EPOCH_DELTA


def process_created(pid: int) -> float | None:
    """Creation time of `pid`, or None if it cannot be opened."""
    if sys.platform != "win32":
        return None
    try:
        h = _k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return None
        try:
            return _creation_time(h)
        finally:
            _k32.CloseHandle(h)
    except Exception:  # noqa: BLE001
        return None


def alive(pid: int, created: float) -> bool | None:
    """True if `pid` is still the process created at `created`, False if it is
    certainly gone, None if that cannot be told.

    "Gone" is only ever one of three definite answers: no such PID, a process
    that has exited (a handle someone still holds keeps its PID), or a PID now
    belonging to a different process. Access denied and every other failure is
    None, and the caller must treat None as alive.
    """
    if sys.platform != "win32" or pid <= 0:
        return None
    try:
        h = _k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return False if ctypes.get_last_error() == _ERROR_INVALID_PARAMETER else None
        try:
            code = wintypes.DWORD()
            if not _k32.GetExitCodeProcess(h, ctypes.byref(code)):
                return None
            if code.value != _STILL_ACTIVE:
                return False
            now_created = _creation_time(h)
            if now_created is None:
                return None
            # Same FILETIME converted the same way, so equal to the bit; the
            # tolerance only absorbs a float that went through JSON.
            return abs(now_created - created) < 0.001
        finally:
            _k32.CloseHandle(h)
    except Exception:  # noqa: BLE001
        return None


def _tcp_peer_pid(client_port: int, server_port: int) -> int | None:
    """PID owning the IPv4 connection from `client_port` to `server_port`."""
    size = wintypes.DWORD(0)
    buf = None
    for _ in range(3):
        buf = ctypes.create_string_buffer(size.value or 16384)
        size = wintypes.DWORD(len(buf))
        rc = _iphlp.GetExtendedTcpTable(buf, ctypes.byref(size), False, socket.AF_INET,
                                        _TCP_TABLE_OWNER_PID_CONNECTIONS, 0)
        if rc == 0:
            break
        if rc != _ERROR_INSUFFICIENT_BUFFER:
            return None
    else:
        return None
    n = wintypes.DWORD.from_buffer(buf).value
    rows = (_MIB_TCPROW_OWNER_PID * n).from_buffer(buf, ctypes.sizeof(wintypes.DWORD))
    for r in rows:
        # Ports are in network byte order in the low 16 bits.
        if (socket.ntohs(r.dwLocalPort & 0xFFFF) == client_port
                and socket.ntohs(r.dwRemotePort & 0xFFFF) == server_port):
            return r.dwOwningPid
    return None


def _process_table() -> dict[int, tuple[int, str]]:
    """pid -> (parent pid, image name) for every process."""
    snap = _k32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if snap in (None, _INVALID_HANDLE):
        return {}
    out: dict[int, tuple[int, str]] = {}
    try:
        e = _PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(e)
        ok = _k32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            out[e.th32ProcessID] = (e.th32ParentProcessID, e.szExeFile)
            ok = _k32.Process32NextW(snap, ctypes.byref(e))
    finally:
        _k32.CloseHandle(snap)
    return out


def peer_session_process(client_port: int, server_port: int) -> SessionProcess | None:
    """The Claude Code process behind the hook connected from `client_port`.

    Must be called while that connection is still open: once curl disconnects
    its row leaves the TCP table. Returns None if the peer is not found, no
    ancestor within MAX_DEPTH is a SESSION_EXES image, or the chain breaks on a
    reused parent PID.
    """
    if sys.platform != "win32":
        return None
    try:
        pid = _tcp_peer_pid(client_port, server_port)
        if pid is None:
            return None
        table = _process_table()
        if pid not in table:
            return None
        chain = [table[pid][1]]
        child_created = process_created(pid)
        cur = pid
        for _ in range(MAX_DEPTH):
            parent = table[cur][0]
            if parent not in table or parent == cur:
                return None
            parent_created = process_created(parent)
            # Toolhelp reports the PID a process was started by, even after that
            # parent has exited and the PID gone to someone else. A "parent"
            # younger than its child is that stranger.
            if parent_created is None or child_created is None \
                    or parent_created > child_created:
                return None
            name = table[parent][1]
            chain.append(name)
            if name.lower() in SESSION_EXES:
                return SessionProcess(parent, parent_created, tuple(chain))
            cur, child_created = parent, parent_created
        return None
    except Exception as e:  # noqa: BLE001
        log.debug("could not resolve the process behind port %d (%s)", client_port, e)
        return None
