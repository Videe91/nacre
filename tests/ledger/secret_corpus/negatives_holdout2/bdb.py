"""Debugger basics"""

import fnmatch
import sys
import threading
import os
import weakref
from contextlib import contextmanager
from inspect import CO_GENERATOR, CO_COROUTINE, CO_ASYNC_GENERATOR

__all__ = ["BdbQuit", "Bdb", "Breakpoint"]

GENERATOR_AND_COROUTINE_FLAGS = CO_GENERATOR | CO_COROUTINE | CO_ASYNC_GENERATOR


class BdbQuit(Exception):
    """Exception to give up completely."""


E = sys.monitoring.events

class _MonitoringTracer:
    EVENT_CALLBACK_MAP = {
        E.PY_START: 'call',
        E.PY_RESUME: 'call',
        E.PY_THROW: 'call',
        E.LINE: 'line',
        E.JUMP: 'jump',
        E.PY_RETURN: 'return',
        E.PY_YIELD: 'return',
        E.PY_UNWIND: 'unwind',
        E.RAISE: 'exception',
        E.STOP_ITERATION: 'exception',
        E.INSTRUCTION: 'opcode',
    }

    GLOBAL_EVENTS = E.PY_START | E.PY_RESUME | E.PY_THROW | E.PY_UNWIND | E.RAISE
    LOCAL_EVENTS = E.LINE | E.JUMP | E.PY_RETURN | E.PY_YIELD | E.STOP_ITERATION

    def __init__(self):
        self._tool_id = sys.monitoring.DEBUGGER_ID
        self._name = 'bdbtracer'
        self._tracefunc = None
        self._disable_current_event = False
        self._tracing_thread = None
        self._enabled = False

    def start_trace(self, tracefunc):
        self._tracefunc = tracefunc
        self._tracing_thread = threading.current_thread()
        curr_tool = sys.monitoring.get_tool(self._tool_id)
        if curr_tool is None:
            sys.monitoring.use_tool_id(self._tool_id, self._name)
        elif curr_tool == self._name:
            sys.monitoring.clear_tool_id(self._tool_id)
        else:
            raise ValueError('Another debugger is using the monitoring tool')
        E = sys.monitoring.events
        all_events = 0
        for event, cb_name in self.EVENT_CALLBACK_MAP.items():
            callback = self.callback_wrapper(getattr(self, f'{cb_name}_callback'), event)
            sys.monitoring.register_callback(self._tool_id, event, callback)
            if event != E.INSTRUCTION:
                all_events |= event
        self.update_local_events()
        sys.monitoring.set_events(self._tool_id, self.GLOBAL_EVENTS)
        self._enabled = True

    def stop_trace(self):
        self._enabled = False
        self._tracing_thread = None
        curr_tool = sys.monitoring.get_tool(self._tool_id)
        if curr_tool != self._name:
            return
        sys.monitoring.clear_tool_id(self._tool_id)
        sys.monitoring.free_tool_id(self._tool_id)

    def disable_current_event(self):
        self._disable_current_event = True

    def restart_events(self):
        if sys.monitoring.get_tool(self._tool_id) == self._name:
            sys.monitoring.restart_events()

    def callback_wrapper(self, func, event):
        import functools

        @functools.wraps(func)
        def wrapper(*args):
            if self._tracing_thread != threading.current_thread():
                return
            try:
                frame = sys._getframe().f_back
                ret = func(frame, *args)
                if self._enabled and frame.f_trace:
                    self.update_local_events()
                if (
                    self._disable_current_event
                    and event not in (E.PY_THROW, E.PY_UNWIND, E.RAISE)
                ):
                    return sys.monitoring.DISABLE
                else:
                    return ret
            except BaseException:
                self.stop_trace()
                sys._getframe().f_back.f_trace = None
                raise
            finally:
                self._disable_current_event = False

        return wrapper

    def call_callback(self, frame, code, *args):
        local_tracefunc = self._tracefunc(frame, 'call', None)
        if local_tracefunc is not None:
            frame.f_trace = local_tracefunc
            if self._enabled:
                sys.monitoring.set_local_events(self._tool_id, code, self.LOCAL_EVENTS)

    def return_callback(self, frame, code, offset, retval):
        if frame.f_trace:
            frame.f_trace(frame, 'return', retval)

    def unwind_callback(self, frame, code, *args):
        if frame.f_trace:
            frame.f_trace(frame, 'return', None)

    def line_callback(self, frame, code, *args):
        if frame.f_trace and frame.f_trace_lines:
            frame.f_trace(frame, 'line', None)

    def jump_callback(self, frame, code, inst_offset, dest_offset):
        if dest_offset > inst_offset:
            return sys.monitoring.DISABLE
        inst_lineno = self._get_lineno(code, inst_offset)
        dest_lineno = self._get_lineno(code, dest_offset)
        if inst_lineno != dest_lineno:
            return sys.monitoring.DISABLE
        if frame.f_trace and frame.f_trace_lines:
            frame.f_trace(frame, 'line', None)

    def exception_callback(self, frame, code, offset, exc):
        if frame.f_trace:
            if exc.__traceback__ and hasattr(exc.__traceback__, 'tb_frame'):
                tb = exc.__traceback__
                while tb:
                    if tb.tb_frame.f_locals.get('self') is self:
                        return
                    tb = tb.tb_next
            frame.f_trace(frame, 'exception', (type(exc), exc, exc.__traceback__))

    def opcode_callback(self, frame, code, offset):
        if frame.f_trace and frame.f_trace_opcodes:
            frame.f_trace(frame, 'opcode', None)

    def update_local_events(self, frame=None):
        if sys.monitoring.get_tool(self._tool_id) != self._name:
            return
        if frame is None:
            frame = sys._getframe().f_back
        while frame is not None:
            if frame.f_trace is not None:
                if frame.f_trace_opcodes:
                    events = self.LOCAL_EVENTS | E.INSTRUCTION
                else:
                    events = self.LOCAL_EVENTS
                sys.monitoring.set_local_events(self._tool_id, frame.f_code, events)
            frame = frame.f_back

    def _get_lineno(self, code, offset):
        import dis
        last_lineno = None
        for start, lineno in dis.findlinestarts(code):
            if offset < start:
                return last_lineno
            last_lineno = lineno
        return last_lineno


class Bdb:
    """Generic Python debugger base class.

    This class takes care of details of the trace facility;
    a derived class should implement user interaction.
    The standard debugger class (pdb.Pdb) is an example.

    The optional skip argument must be an iterable of glob-style
    module name patterns.  The debugger will not step into frames
    that originate in a module that matches one of these patterns.
    Whether a frame is considered to originate in a certain module
    is determined by the __name__ in the frame globals.
    """

    def __init__(self, skip=None, backend='settrace'):
        self.skip = set(skip) if skip else None
        self.breaks = {}
        self.fncache = {}
        self.frame_trace_lines_opcodes = {}
        self.frame_returning = None
        self.trace_opcodes = False
        self.enterframe = None
        self.cmdframe = None
        self.cmdlineno = None
        self.code_linenos = weakref.WeakKeyDictionary()
        self.backend = backend
        if backend == 'monitoring':
            self.monitoring_tracer = _MonitoringTracer()
        elif backend == 'settrace':
            self.monitoring_tracer = None
        else:
            raise ValueError(f"Invalid backend '{backend}'")

        self._load_breaks()

    def canonic(self, filename):
        """Return canonical form of filename.

        For real filenames, the canonical form is a case-normalized (on
        case insensitive filesystems) absolute path.  'Filenames' with
        angle brackets, such as "<stdin>", generated in interactive
        mode, are returned unchanged.
        """
        if filename == "<" + filename[1:-1] + ">":
            return filename
        canonic = self.fncache.get(filename)
        if not canonic:
            canonic = os.path.abspath(filename)
            canonic = os.path.normcase(canonic)
            self.fncache[filename] = canonic
        return canonic

    def start_trace(self):
        if self.monitoring_tracer:
            self.monitoring_tracer.start_trace(self.trace_dispatch)
        else:
            sys.settrace(self.trace_dispatch)

    def stop_trace(self):
        if self.monitoring_tracer:
            self.monitoring_tracer.stop_trace()
        else:
            sys.settrace(None)

    def reset(self):
        """Set values of attributes as ready to start debugging."""
        import linecache
        linecache.checkcache()
        self.botframe = None
        self._set_stopinfo(None, None)

    @contextmanager
    def set_enterframe(self, frame):
        self.enterframe = frame
        yield
        self.enterframe = None

    def trace_dispatch(self, frame, event, arg):
        """Dispatch a trace function for debugged frames based on the event.

        This function is installed as the trace function for debugged
        frames. Its return value is the new trace function, which is
        usually itself. The default implementation decides how to
        dispatch a frame, depending on the type of event (passed in as a
        string) that is about to be executed.

        The event can be one of the following:
            line: A new line of code is going to be executed.
            call: A function is about to be called or another code block
                  is entered.
            return: A function or other code block is about to return.
            exception: An exception has occurred.
            c_call: A C function is about to be called.
            c_return: A C function has returned.
            c_exception: A C function has raised an exception.

        For the Python events, specialized functions (see the dispatch_*()
        methods) are called.  For the C events, no action is taken.

        The arg parameter depends on the previous event.
        """

        with self.set_enterframe(frame):
            if self.quitting:
                return # None
            if event == 'line':
                return self.dispatch_line(frame)
            if event == 'call':
                return self.dispatch_call(frame, arg)
            if event == 'return':
                return self.dispatch_return(frame, arg)
            if event == 'exception':
                return self.dispatch_exception(frame, arg)
            if event == 'c_call':
                return self.trace_dispatch
            if event == 'c_exception':
                return self.trace_dispatch
            if event == 'c_return':
                return self.trace_dispatch
            if event == 'opcode':
                return self.dispatch_opcode(frame, arg)
            print('bdb.Bdb.dispatch: unknown debugging event:', repr(event))
            return self.trace_dispatch

    def dispatch_line(self, frame):
        """Invoke user function and return trace function for line event.

        If the debugger stops on the current line, invoke
        self.user_line(). Raise BdbQuit if self.quitting is set.
        Return self.trace_dispatch to continue tracing in this scope.
        """
        # GH-136057
        # For line events, we don't want to stop at the same line where
        # the latest next/step command was issued.
        if (self.stop_here(frame) or self.break_here(frame)) and not (
            self.cmdframe == frame and self.cmdlineno == frame.f_lineno
        ):
            self.user_line(frame)
            self.restart_events()
            if self.quitting: raise BdbQuit
        elif not self.get_break(frame.f_code.co_filename, frame.f_lineno):
            self.disable_current_event()
        return self.trace_dispatch

    def dispatch_call(self, frame, arg):
        """Invoke user function and return trace function for call event.

        If the debugger stops on this function call, invoke
        self.user_call(). Raise BdbQuit if self.quitting is set.
        Return self.trace_dispatch to continue tracing in this scope.
        """
        # XXX 'arg' is no longer used
        if self.botframe is None:
            # First call of dispatch since reset()
            self.botframe = frame.f_back # (CT) Note that this may also be None!
            return self.trace_dispatch
        if not (self.stop_here(frame) or self.break_anywhere(frame)):
            # We already know there's no breakpoint in this function
            # If it's a next/until/return command, we don't need any CALL event
            # and we don't need to set the f_trace on any new frame.
            # If it's a step command, it must either hit stop_here, or skip the
            # whole module. Either way, we don't need the CALL event here.
            self.disable_current_event()
            return # None
        # Ignore call events in generator except when stepping.
        if self.stopframe and frame.f_code.co_flags & GENERATOR_AND_COROUTINE_FLAGS:
            return self.trace_dispatch
        self.user_call(frame, arg)
        self.restart_events()
        if self.quitting: raise BdbQuit
        return self.trace_dispatch

    def dispatch_return(self, frame, arg):
        """Invoke user function and return trace function for return event.

        If the debugger stops on this function return, invoke
        self.user_return(). Raise BdbQuit if self.quitting is set.
        Return self.trace_dispatch to continue tracing in this scope.
        """
        if self.stop_here(frame) or frame == self.returnframe:
            # Ignore return events in generator except when stepping.
            if self.stopframe and frame.f_code.co_flags & GENERATOR_AND_COROUTINE_FLAGS:
                # It's possible to trigger a StopIteration exception in
                # the caller so we must set the trace function in the caller
                self._set_caller_tracefunc(frame)
                return self.trace_dispatch
            try:
                self.frame_returning = frame
                self.user_return(frame, arg)
                self.restart_events()
            finally:
                self.frame_returning = None
            if self.quitting: raise BdbQuit
            # The user issued a 'next' or 'until' command.
            if self.stopframe is frame and self.stoplineno != -1:
                self._set_stopinfo(None, None)
            # The previous frame might not have f_trace set, unless we are
            # issuing a command that does not expect to stop, we should set
            # f_trace
            if self.stoplineno != -1:
                self._set_caller_tracefunc(frame)
        return self.trace_dispatch

    def dispatch_exception(self, frame, arg):
        """Invoke user function and return trace function for exception event.

        If the debugger stops on this exception, invoke
        self.user_exception(). Raise BdbQuit if self.quitting is set.
        Return self.trace_dispatch to continue tracing in this scope.
        """
        if self.stop_here(frame):
            # When stepping with next/until/return in a generator frame, skip
            # the internal StopIteration exception (with no traceback)
            # triggered by a subiterator run with the 'yield from' statement.
            if not (frame.f_code.co_flags & GENERATOR_AND_COROUTINE_FLAGS
                    and arg[0] is StopIteration and arg[2] is None):
                self.user_exception(frame, arg)
                self.restart_events()
                if self.quitting: raise BdbQuit
        # Stop at the StopIteration or GeneratorExit exception when the user
        # has set stopframe in a generator by issuing a return command, or a
        # next/until command at the last statement in the generator before the
        # exception.
        elif (self.stopframe and frame is not self.stopframe
                and self.stopframe.f_code.co_flags & GENERATOR_AND_COROUTINE_FLAGS
                and arg[0] in (StopIteration, GeneratorExit)):
            self.user_exception(frame, arg)
            self.restart_events()
            if self.quitting: raise BdbQuit

        return self.trace_dispatch

    def dispatch_opcode(self, frame, arg):
        """Invoke user function and return trace function for opcode event.
