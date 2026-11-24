"""Linux/amd64 child restrictions, installed before loading foreign native code.

The HTTP parent can use a private Docker network; the calculation child is
forbidden to open/use network sockets or spawn/exec another process. Docker
still supplies read-only mounts, no capabilities, cgroup memory/PID/CPU limits.
"""
import ctypes as C
import errno
import os
import platform
import resource
from pathlib import Path


def restrict_native_child(*, outer_offline_test=False):
    if platform.system() != 'Linux' or platform.machine() not in ('x86_64', 'amd64'):
        raise RuntimeError('Native worker requires Linux/amd64')
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_FSIZE, (32_000_000, 32_000_000))
    if outer_offline_test:
        # Explicit CLI-only witness for emulators that reject nested seccomp.
        # The HTTP service never passes this flag. Network none is checked
        # inside the worker as well as supplied by the owned Docker runner.
        interfaces = Path('/sys/class/net')
        # The kernel may expose dormant gre/tunnel pseudo-devices even with
        # Docker network none. No non-loopback interface may be UP or routed.
        for item in interfaces.iterdir():
            if item.name != 'lo' and (item / 'flags').is_file() and int((item / 'flags').read_text(), 16) & 1:
                raise RuntimeError('Offline witness has a non-loopback active interface')
        routes = Path('/proc/net/route').read_text().splitlines()[1:]
        routes_v6 = Path('/proc/net/ipv6_route').read_text().splitlines()
        if any(row.split()[0] != 'lo' for row in routes if row.split()) or any(row.split()[-1] != 'lo' for row in routes_v6 if row.split()):
            raise RuntimeError('Offline witness has a non-loopback route')
        return 'outer-container-network-none-witness'
    # AMD64 syscall numbers. Reject sockets, exec/fork and io_uring network paths.
    denied = [41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55,
              56, 57, 58, 59, 288, 299, 307, 322, 425, 426, 427, 435]

    class Filter(C.Structure):
        _fields_ = [('code', C.c_ushort), ('jt', C.c_ubyte), ('jf', C.c_ubyte), ('k', C.c_uint32)]

    class Program(C.Structure):
        _fields_ = [('length', C.c_ushort), ('instructions', C.POINTER(Filter))]

    instructions = [(0x20, 0, 0, 4), (0x15, 1, 0, 0xc000003e), (0x06, 0, 0, 0x80000000),
                    (0x20, 0, 0, 0), (0x45, 0, 1, 0x40000000), (0x06, 0, 0, 0x00050000 | errno.EPERM)]
    for number in denied:
        instructions += [(0x15, 0, 1, number), (0x06, 0, 0, 0x00050000 | errno.EPERM)]
    instructions += [(0x06, 0, 0, 0x7fff0000)]
    array = (Filter * len(instructions))(*(Filter(*row) for row in instructions))
    program = Program(len(instructions), array)
    libc = C.CDLL(None, use_errno=True)
    libc.prctl.argtypes = [C.c_int, C.c_ulong, C.c_ulong, C.c_ulong, C.c_ulong]
    libc.prctl.restype = C.c_int
    if libc.prctl(38, 1, 0, 0, 0) != 0:
        raise OSError(C.get_errno(), 'Native child no-new-privileges could not be installed')
    if libc.prctl(22, 2, C.cast(C.byref(program), C.c_void_p).value, 0, 0) != 0:
        raise OSError(C.get_errno(), 'Native child seccomp could not be installed')
    return 'linux-amd64-seccomp-network-and-spawn-denied'
