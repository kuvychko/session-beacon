"""Process liveness. These talk to the real OS, so they run on Windows only."""

import os
import socket
import sys

import pytest

from beacon_host import procwatch

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Win32 APIs")


def test_a_running_process_is_alive_only_under_its_own_creation_time():
    """The creation time is what stops a recycled PID reading as the session."""
    created = procwatch.process_created(os.getpid())
    assert created is not None
    assert procwatch.alive(os.getpid(), created) is True
    assert procwatch.alive(os.getpid(), created + 1) is False


def test_a_pid_nobody_holds_is_gone():
    # Windows PIDs are multiples of four, well below this.
    assert procwatch.alive(0x3FFFFFFC, 0.0) is False


def test_an_exited_process_is_gone(tmp_path):
    import subprocess
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    created = procwatch.process_created(p.pid)
    p.wait()
    # Popen still holds the handle, so the PID is not free: the exit code is
    # what has to answer.
    assert created is not None and procwatch.alive(p.pid, created) is False


def test_peer_lookup_never_raises():
    """The peer here is this process, whose ancestors may or may not include a
    Claude Code process, depending on who ran pytest. Either answer is fine;
    raising is not, because it runs in the hook's request handler."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen()
    with srv, socket.create_connection(srv.getsockname()) as c:
        conn, (_, client_port) = srv.accept()
        with conn:
            assert c.getsockname()[1] == client_port
            proc = procwatch.peer_session_process(client_port, srv.getsockname()[1])
    if proc is not None:
        assert proc.chain[-1].lower() in procwatch.SESSION_EXES
        assert procwatch.alive(proc.pid, proc.created) is True
    assert procwatch.peer_session_process(1, 2) is None
