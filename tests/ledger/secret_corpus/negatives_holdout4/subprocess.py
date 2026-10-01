# subprocess - Subprocesses with accessible I/O streams
#
# For more information about this module, see PEP 324.
#
# Copyright (c) 2003-2005 by Peter Astrand <astrand@lysator.liu.se>
#
# Licensed to PSF under a Contributor Agreement.

r"""Subprocesses with accessible I/O streams

This module allows you to spawn processes, connect to their
input/output/error pipes, and obtain their return codes.

For a complete description of this module see the Python documentation.

Main API
========
run(...): Runs a command, waits for it to complete, then returns a
          CompletedProcess instance.
Popen(...): A class for flexibly executing a command in a new process

Constants
---------
DEVNULL: Special value that indicates that os.devnull should be used
PIPE:    Special value that indicates a pipe should be created
STDOUT:  Special value that indicates that stderr should go to stdout


Older API
=========
call(...): Runs a command, waits for it to complete, then returns
    the return code.
check_call(...): Same as call() but raises CalledProcessError()
    if return code is not 0
check_output(...): Same as check_call() but returns the contents of
    stdout instead of a return code
getoutput(...): Runs a command in the shell, waits for it to complete,
    then returns the output
getstatusoutput(...): Runs a command in the shell, waits for it to complete,
    then returns a (exitcode, output) tuple
"""

import builtins
import errno
import io
import locale
import os
import time
import signal
import sys
import threading
import warnings
import contextlib
from time import monotonic as _time
import types

try:
    import fcntl
except ImportError:
    fcntl = None


__all__ = ["Popen", "PIPE", "STDOUT", "call", "check_call", "getstatusoutput",
           "getoutput", "check_output", "run", "CalledProcessError", "DEVNULL",
           "SubprocessError", "TimeoutExpired", "CompletedProcess"]
           # NOTE: We intentionally exclude list2cmdline as it is
           # considered an internal implementation detail.  issue10838.

# use presence of msvcrt to detect Windows-like platforms (see bpo-8110)
try:
    import msvcrt
except ModuleNotFoundError:
    _mswindows = False
else:
    _mswindows = True

# some platforms do not support subprocesses
_can_fork_exec = sys.platform not in {"emscripten", "wasi", "ios", "tvos", "watchos"}

if _mswindows:
    import _winapi
    from _winapi import (CREATE_NEW_CONSOLE, CREATE_NEW_PROCESS_GROUP,  # noqa: F401
                         STD_INPUT_HANDLE, STD_OUTPUT_HANDLE,
                         STD_ERROR_HANDLE, SW_HIDE,
                         STARTF_USESTDHANDLES, STARTF_USESHOWWINDOW,
                         STARTF_FORCEONFEEDBACK, STARTF_FORCEOFFFEEDBACK,
                         ABOVE_NORMAL_PRIORITY_CLASS, BELOW_NORMAL_PRIORITY_CLASS,
                         HIGH_PRIORITY_CLASS, IDLE_PRIORITY_CLASS,
                         NORMAL_PRIORITY_CLASS, REALTIME_PRIORITY_CLASS,
                         CREATE_NO_WINDOW, DETACHED_PROCESS,
                         CREATE_DEFAULT_ERROR_MODE, CREATE_BREAKAWAY_FROM_JOB)

    __all__.extend(["CREATE_NEW_CONSOLE", "CREATE_NEW_PROCESS_GROUP",
                    "STD_INPUT_HANDLE", "STD_OUTPUT_HANDLE",
                    "STD_ERROR_HANDLE", "SW_HIDE",
                    "STARTF_USESTDHANDLES", "STARTF_USESHOWWINDOW",
                    "STARTF_FORCEONFEEDBACK", "STARTF_FORCEOFFFEEDBACK",
                    "STARTUPINFO",
                    "ABOVE_NORMAL_PRIORITY_CLASS", "BELOW_NORMAL_PRIORITY_CLASS",
                    "HIGH_PRIORITY_CLASS", "IDLE_PRIORITY_CLASS",
                    "NORMAL_PRIORITY_CLASS", "REALTIME_PRIORITY_CLASS",
                    "CREATE_NO_WINDOW", "DETACHED_PROCESS",
                    "CREATE_DEFAULT_ERROR_MODE", "CREATE_BREAKAWAY_FROM_JOB"])
else:
    if _can_fork_exec:
        from _posixsubprocess import fork_exec as _fork_exec
        # used in methods that are called by __del__
        class _del_safe:
            waitpid = os.waitpid
            waitstatus_to_exitcode = os.waitstatus_to_exitcode
            WIFSTOPPED = os.WIFSTOPPED
            WSTOPSIG = os.WSTOPSIG
            WNOHANG = os.WNOHANG
            ECHILD = errno.ECHILD
    else:
        class _del_safe:
            waitpid = None
            waitstatus_to_exitcode = None
            WIFSTOPPED = None
            WSTOPSIG = None
            WNOHANG = None
            ECHILD = errno.ECHILD

    import select
    import selectors


# Exception classes used by this module.
class SubprocessError(Exception): pass


class CalledProcessError(SubprocessError):
    """Raised when run() is called with check=True and the process
    returns a non-zero exit status.

    Attributes:
      cmd, returncode, stdout, stderr, output
    """
    def __init__(self, returncode, cmd, output=None, stderr=None):
        self.returncode = returncode
        self.cmd = cmd
        self.output = output
        self.stderr = stderr

    def __str__(self):
        if self.returncode and self.returncode < 0:
            try:
                return "Command '%s' died with %r." % (
                        self.cmd, signal.Signals(-self.returncode))
            except ValueError:
                return "Command '%s' died with unknown signal %d." % (
                        self.cmd, -self.returncode)
        else:
            return "Command '%s' returned non-zero exit status %d." % (
                    self.cmd, self.returncode)

    @property
    def stdout(self):
        """Alias for output attribute, to match stderr"""
        return self.output

    @stdout.setter
    def stdout(self, value):
        # There's no obvious reason to set this, but allow it anyway so
        # .stdout is a transparent alias for .output
        self.output = value


class TimeoutExpired(SubprocessError):
    """This exception is raised when the timeout expires while waiting for a
    child process.

    Attributes:
        cmd, output, stdout, stderr, timeout
    """
    def __init__(self, cmd, timeout, output=None, stderr=None):
        self.cmd = cmd
        self.timeout = timeout
        self.output = output
        self.stderr = stderr

    def __str__(self):
        return ("Command '%s' timed out after %s seconds" %
                (self.cmd, self.timeout))

    @property
    def stdout(self):
        return self.output

    @stdout.setter
    def stdout(self, value):
        # There's no obvious reason to set this, but allow it anyway so
        # .stdout is a transparent alias for .output
        self.output = value


if _mswindows:
    class STARTUPINFO:
        def __init__(self, *, dwFlags=0, hStdInput=None, hStdOutput=None,
                     hStdError=None, wShowWindow=0, lpAttributeList=None):
            self.dwFlags = dwFlags
            self.hStdInput = hStdInput
            self.hStdOutput = hStdOutput
            self.hStdError = hStdError
            self.wShowWindow = wShowWindow
            self.lpAttributeList = lpAttributeList or {"handle_list": []}

        def copy(self):
            attr_list = self.lpAttributeList.copy()
            if 'handle_list' in attr_list:
                attr_list['handle_list'] = list(attr_list['handle_list'])

            return STARTUPINFO(dwFlags=self.dwFlags,
                               hStdInput=self.hStdInput,
                               hStdOutput=self.hStdOutput,
                               hStdError=self.hStdError,
                               wShowWindow=self.wShowWindow,
                               lpAttributeList=attr_list)


    class Handle(int):
        closed = False

        def Close(self, CloseHandle=_winapi.CloseHandle):
            if not self.closed:
                self.closed = True
                CloseHandle(self)

        def Detach(self):
            if not self.closed:
                self.closed = True
                return int(self)
            raise ValueError("already closed")

        def __repr__(self):
            return "%s(%d)" % (self.__class__.__name__, int(self))

        __del__ = Close
else:
    # When select or poll has indicated that the file is writable,
    # we can write up to _PIPE_BUF bytes without risk of blocking.
    # POSIX defines PIPE_BUF as >= 512.
    _PIPE_BUF = getattr(select, 'PIPE_BUF', 512)

    # poll/select have the advantage of not requiring any extra file
    # descriptor, contrarily to epoll/kqueue (also, they require a single
    # syscall).
    if hasattr(selectors, 'PollSelector'):
        _PopenSelector = selectors.PollSelector
    else:
        _PopenSelector = selectors.SelectSelector


if _mswindows:
    # On Windows we just need to close `Popen._handle` when we no longer need
    # it, so that the kernel can free it. `Popen._handle` gets closed
    # implicitly when the `Popen` instance is finalized (see `Handle.__del__`,
    # which is calling `CloseHandle` as requested in [1]), so there is nothing
    # for `_cleanup` to do.
    #
    # [1] https://docs.microsoft.com/en-us/windows/desktop/ProcThread/
    # creating-processes
    _active = None

    def _cleanup():
        pass
else:
    # This lists holds Popen instances for which the underlying process had not
    # exited at the time its __del__ method got called: those processes are
    # wait()ed for synchronously from _cleanup() when a new Popen object is
    # created, to avoid zombie processes.
    _active = []

    def _cleanup():
        if _active is None:
            return
        for inst in _active[:]:
            res = inst._internal_poll(_deadstate=sys.maxsize)
            if res is not None:
                try:
                    _active.remove(inst)
                except ValueError:
                    # This can happen if two threads create a new Popen instance.
                    # It's harmless that it was already removed, so ignore.
                    pass

PIPE = -1
STDOUT = -2
DEVNULL = -3


# XXX This function is only used by multiprocessing and the test suite,
# but it's here so that it can be imported when Python is compiled without
# threads.

def _optim_args_from_interpreter_flags():
    """Return a list of command-line arguments reproducing the current
    optimization settings in sys.flags."""
    args = []
    value = sys.flags.optimize
    if value > 0:
        args.append('-' + 'O' * value)
    return args


def _args_from_interpreter_flags():
    """Return a list of command-line arguments reproducing the current
    settings in sys.flags, sys.warnoptions and sys._xoptions."""
    flag_opt_map = {
        'debug': 'd',
        # 'inspect': 'i',
        # 'interactive': 'i',
        'dont_write_bytecode': 'B',
        'no_site': 'S',
        'verbose': 'v',
        'bytes_warning': 'b',
        'quiet': 'q',
        # -O is handled in _optim_args_from_interpreter_flags()
    }
    args = _optim_args_from_interpreter_flags()
    for flag, opt in flag_opt_map.items():
        v = getattr(sys.flags, flag)
        if v > 0:
            args.append('-' + opt * v)

    if sys.flags.isolated:
        args.append('-I')
    else:
        if sys.flags.ignore_environment:
            args.append('-E')
        if sys.flags.no_user_site:
            args.append('-s')
        if sys.flags.safe_path:
            args.append('-P')

    # -W options
    warnopts = sys.warnoptions[:]
    xoptions = getattr(sys, '_xoptions', {})
    bytes_warning = sys.flags.bytes_warning
    dev_mode = sys.flags.dev_mode

    if bytes_warning > 1:
        warnopts.remove("error::BytesWarning")
    elif bytes_warning:
        warnopts.remove("default::BytesWarning")
    if dev_mode:
        warnopts.remove('default')
    for opt in warnopts:
        args.append('-W' + opt)

    # -X options
    if dev_mode:
        args.extend(('-X', 'dev'))
    for opt in ('faulthandler', 'tracemalloc', 'importtime',
                'frozen_modules', 'showrefcount', 'utf8', 'gil'):
        if opt in xoptions:
            value = xoptions[opt]
            if value is True:
                arg = opt
            else:
                arg = '%s=%s' % (opt, value)
            args.extend(('-X', arg))

    return args


def _text_encoding():
    # Return default text encoding and emit EncodingWarning if
    # sys.flags.warn_default_encoding is true.
    if sys.flags.warn_default_encoding:
        f = sys._getframe()
        filename = f.f_code.co_filename
        stacklevel = 2
        while f := f.f_back:
            if f.f_code.co_filename != filename:
                break
            stacklevel += 1
        warnings.warn("'encoding' argument not specified.",
                      EncodingWarning, stacklevel)

    if sys.flags.utf8_mode:
        return "utf-8"
    else:
        return locale.getencoding()


def call(*popenargs, timeout=None, **kwargs):
    """Run command with arguments.  Wait for command to complete or
    for timeout seconds, then return the returncode attribute.

    The arguments are the same as for the Popen constructor.  Example:

    retcode = call(["ls", "-l"])
    """
    with Popen(*popenargs, **kwargs) as p:
        try:
            return p.wait(timeout=timeout)
        except:  # Including KeyboardInterrupt, wait handled that.
            p.kill()
            # We don't call p.wait() again as p.__exit__ does that for us.
            raise


def check_call(*popenargs, **kwargs):
    """Run command with arguments.  Wait for command to complete.  If
    the exit code was zero then return, otherwise raise
    CalledProcessError.  The CalledProcessError object will have the
    return code in the returncode attribute.

    The arguments are the same as for the call function.  Example:

    check_call(["ls", "-l"])
    """
    retcode = call(*popenargs, **kwargs)
    if retcode:
        cmd = kwargs.get("args")
        if cmd is None:
            cmd = popenargs[0]
        raise CalledProcessError(retcode, cmd)
    return 0


def check_output(*popenargs, timeout=None, **kwargs):
    r"""Run command with arguments and return its output.

    If the exit code was non-zero it raises a CalledProcessError.  The
    CalledProcessError object will have the return code in the returncode
    attribute and output in the output attribute.

    The arguments are the same as for the Popen constructor.  Example:

    >>> check_output(["ls", "-l", "/dev/null"])
    b'crw-rw-rw- 1 root root 1, 3 Oct 18  2007 /dev/null\n'

    The stdout argument is not allowed as it is used internally.
    To capture standard error in the result, use stderr=STDOUT.

    >>> check_output(["/bin/sh", "-c",
    ...               "ls -l non_existent_file ; exit 0"],
    ...              stderr=STDOUT)
    b'ls: non_existent_file: No such file or directory\n'

    There is an additional optional argument, "input", allowing you to
    pass a string to the subprocess's stdin.  If you use this argument
    you may not also use the Popen constructor's "stdin" argument, as
    it too will be used internally.  Example:

    >>> check_output(["sed", "-e", "s/foo/bar/"],
    ...              input=b"when in the course of fooman events\n")
    b'when in the course of barman events\n'

    By default, all communication is in bytes, and therefore any "input"
    should be bytes, and the return value will be bytes.  If in text mode,
    any "input" should be a string, and the return value will be a string
    decoded according to locale encoding, or by "encoding" if set. Text mode
    is triggered by setting any of text, encoding, errors or universal_newlines.
    """
    for kw in ('stdout', 'check'):
        if kw in kwargs:
            raise ValueError(f'{kw} argument not allowed, it will be overridden.')

    if 'input' in kwargs and kwargs['input'] is None:
        # Explicitly passing input=None was previously equivalent to passing an
        # empty string. That is maintained here for backwards compatibility.
        if kwargs.get('universal_newlines') or kwargs.get('text') or kwargs.get('encoding') \
                or kwargs.get('errors'):
            empty = ''
        else:
            empty = b''
        kwargs['input'] = empty

    return run(*popenargs, stdout=PIPE, timeout=timeout, check=True,
               **kwargs).stdout


class CompletedProcess(object):
    """A process that has finished running.

    This is returned by run().

    Attributes:
