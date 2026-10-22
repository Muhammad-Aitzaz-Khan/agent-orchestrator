"""Loaded at start-up by every Python process the agents run when SANDBOX=guarded.

Used where Docker is not available, such as a public demo server. Python's audit hooks
(PEP 578) see every sensitive operation and cannot be removed once installed, so code
written by a model cannot:

- open network connections,
- start other programs or fork,
- load native libraries through ctypes,
- read /proc or /sys (where another process's environment, and so an API key, could be),
  or anything under RELAY_GUARD_DENY,
- write files outside its working folder and the temp folder.

Starting programs is also stopped below Python: the server sets the child's process limit
(RLIMIT_NPROC) to zero, so even a direct call to the low-level fork function fails. The
server removes secrets from every child's environment and limits memory, file sizes, and
run time.
"""
import os
import sys
import tempfile

_WORK = os.path.realpath(os.getcwd())
_TMP = os.path.realpath(tempfile.gettempdir())
_DENY_READ = ["/proc", "/sys"] + [
    os.path.realpath(p) for p in os.environ.get("RELAY_GUARD_DENY", "").split(os.pathsep) if p
]
_BLOCKED_EVENTS = {
    "socket.connect", "socket.bind", "socket.sendto", "socket.sendmsg", "socket.getaddrinfo",
    "subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.fork",
    "os.forkpty", "os.kill", "os.killpg", "pty.spawn", "ctypes.dlopen", "ctypes.dlsym",
    "ctypes.call_function", "ctypes.cdata", "ctypes.string_at", "ctypes.wstring_at", "sys.addaudithook", "sys.setprofile", "sys.settrace",
    "webbrowser.open", "urllib.Request", "ftplib.connect", "smtplib.connect", "http.client.connect",
}
_BLOCKED_IMPORTS = {"_ctypes", "ctypes", "_winapi"}


def _inside(path: str, folder: str) -> bool:
    return path == folder or path.startswith(folder + os.sep)


def _hook(event: str, args: tuple) -> None:
    if event in _BLOCKED_EVENTS:
        raise PermissionError(f"Blocked by the sandbox: {event}")
    if event == "import" and args and args[0] in _BLOCKED_IMPORTS:
        raise ImportError(f"Blocked by the sandbox: import {args[0]}")
    if event == "open" and args:
        path, mode = args[0], args[1] if len(args) > 1 else "r"
        if isinstance(path, int):
            return
        if isinstance(path, bytes):
            path = os.fsdecode(path)
        real = os.path.realpath(os.fspath(path))
        own = _inside(real, _WORK) or _inside(real, _TMP)
        if not own and any(_inside(real, d) for d in _DENY_READ):
            raise PermissionError(f"Blocked by the sandbox: reading {real}")
        flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
        writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
            flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC)
        )
        if writing and real != os.devnull and not (_inside(real, _WORK) or _inside(real, _TMP)):
            raise PermissionError(f"Blocked by the sandbox: writing {real}")
    if event in ("os.remove", "os.rename", "os.rmdir", "os.mkdir", "shutil.rmtree", "os.chmod"):
        for value in args:
            if isinstance(value, (str, bytes, os.PathLike)):
                real = os.path.realpath(os.fsdecode(os.fspath(value)))
                if not (_inside(real, _WORK) or _inside(real, _TMP)):
                    raise PermissionError(f"Blocked by the sandbox: {event} {real}")


sys.addaudithook(_hook)
