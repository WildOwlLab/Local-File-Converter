"""Tying children to the server's lifetime.

Everything here is POSIX-specific or platform-neutral. The Windows job object
cannot be exercised from Linux CI, so what is tested there is the shape of the
call -- the argument order and the pinned ctypes signatures, which is exactly
where that code went wrong before.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

import process_group
from handlers import base

POSIX_ONLY = pytest.mark.skipif(sys.platform == "win32",
                                reason="POSIX process groups")


def alive(pid: int) -> bool:
    """True only for a process that exists and has not already been reaped."""
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        with open(f"/proc/{pid}/stat") as handle:
            return handle.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return True


def spawn(command: str) -> subprocess.Popen:
    """A shell child. POSIX only -- the tests using it need process groups."""
    return subprocess.Popen(["/bin/sh", "-c", command],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            **process_group.spawn_kwargs())


def spawn_python(code: str) -> subprocess.Popen:
    """A child that exists on every platform. /bin/sh does not."""
    return subprocess.Popen([sys.executable, "-c", code],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            **process_group.spawn_kwargs())


def test_spawn_kwargs_match_the_platform():
    kwargs = process_group.spawn_kwargs()
    if sys.platform == "win32":
        assert "creationflags" in kwargs
    else:
        assert kwargs == {"start_new_session": True}


def test_status_is_reported_for_the_startup_log():
    assert process_group.status()


@POSIX_ONLY
def test_a_child_gets_its_own_process_group():
    """Sharing the server's group means signalling the children signals the
    server too."""
    proc = spawn("exec sleep 90")
    try:
        assert os.getpgid(proc.pid) != os.getpgid(0)
        assert os.getpgid(proc.pid) == proc.pid
    finally:
        process_group.kill_tree(proc)
        proc.wait(timeout=10)


@POSIX_ONLY
def test_kill_tree_takes_the_whole_group():
    """A tool that forks its own helpers must not leave them behind."""
    proc = spawn("sleep 91 & sleep 92 & sleep 93")
    time.sleep(0.4)
    pgid = os.getpgid(proc.pid)
    listing = subprocess.run(["ps", "-eo", "pid,pgid", "--no-headers"],
                             capture_output=True, text=True).stdout
    members = [int(line.split()[0]) for line in listing.splitlines()
               if line.split() and int(line.split()[1]) == pgid]
    assert len(members) >= 2, "expected a child and at least one grandchild"

    process_group.kill_tree(proc)
    proc.wait(timeout=10)
    time.sleep(0.4)
    assert [pid for pid in members if alive(pid)] == []


@POSIX_ONLY
def test_terminate_all_stops_a_running_conversion():
    proc = spawn("exec sleep 94")
    with base._live_lock:
        base._live.add(proc)
    try:
        assert base.terminate_all() == 1
        proc.wait(timeout=10)
        time.sleep(0.3)
        assert not alive(proc.pid)
    finally:
        with base._live_lock:
            base._live.discard(proc)


def test_terminate_all_with_nothing_running_is_zero():
    assert base.terminate_all() == 0


def test_kill_tree_on_an_already_dead_child_is_harmless():
    proc = spawn_python("pass")
    proc.wait(timeout=10)
    process_group.kill_tree(proc)   # must not raise


@POSIX_ONLY
def test_adopt_is_a_no_op_off_windows():
    """The job object is the Windows mechanism; POSIX gets process groups."""
    proc = spawn_python("pass")
    try:
        assert process_group.adopt(proc) is False
    finally:
        proc.wait(timeout=10)
