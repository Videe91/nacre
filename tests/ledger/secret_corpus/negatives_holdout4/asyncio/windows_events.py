"""Selector and proactor event loops for Windows."""

import sys

if sys.platform != 'win32':  # pragma: no cover
    raise ImportError('win32 only')

import _overlapped
import _winapi
import errno
from functools import partial
import math
import msvcrt
import socket
import struct
import time
import weakref

from . import events
from . import base_subprocess
from . import futures
from . import exceptions
from . import proactor_events
from . import selector_events
from . import tasks
from . import windows_utils
from .log import logger


__all__ = (
    'SelectorEventLoop', 'ProactorEventLoop', 'IocpProactor',
    '_DefaultEventLoopPolicy', '_WindowsSelectorEventLoopPolicy',
    '_WindowsProactorEventLoopPolicy', 'EventLoop',
)


NULL = _winapi.NULL
INFINITE = _winapi.INFINITE
ERROR_CONNECTION_REFUSED = 1225
ERROR_CONNECTION_ABORTED = 1236

# Initial delay in seconds for connect_pipe() before retrying to connect
CONNECT_PIPE_INIT_DELAY = 0.001

# Maximum delay in seconds for connect_pipe() before retrying to connect
CONNECT_PIPE_MAX_DELAY = 0.100


class _OverlappedFuture(futures.Future):
    """Subclass of Future which represents an overlapped operation.

    Cancelling it will immediately cancel the overlapped operation.
    """

    def __init__(self, ov, *, loop=None):
        super().__init__(loop=loop)
        if self._source_traceback:
            del self._source_traceback[-1]
        self._ov = ov

    def _repr_info(self):
        info = super()._repr_info()
        if self._ov is not None:
            state = 'pending' if self._ov.pending else 'completed'
            info.insert(1, f'overlapped=<{state}, {self._ov.address:#x}>')
        return info

    def _cancel_overlapped(self):
        if self._ov is None:
            return
        try:
            self._ov.cancel()
        except OSError as exc:
            context = {
                'message': 'Cancelling an overlapped future failed',
                'exception': exc,
                'future': self,
            }
            if self._source_traceback:
                context['source_traceback'] = self._source_traceback
            self._loop.call_exception_handler(context)
        self._ov = None

    def cancel(self, msg=None):
        self._cancel_overlapped()
        return super().cancel(msg=msg)

    def set_exception(self, exception):
        super().set_exception(exception)
        self._cancel_overlapped()

    def set_result(self, result):
        super().set_result(result)
        self._ov = None


class _BaseWaitHandleFuture(futures.Future):
    """Subclass of Future which represents a wait handle."""

    def __init__(self, ov, handle, wait_handle, *, loop=None):
        super().__init__(loop=loop)
        if self._source_traceback:
            del self._source_traceback[-1]
        # Keep a reference to the Overlapped object to keep it alive until the
        # wait is unregistered
        self._ov = ov
        self._handle = handle
        self._wait_handle = wait_handle

        # Should we call UnregisterWaitEx() if the wait completes
        # or is cancelled?
        self._registered = True

    def _poll(self):
        # non-blocking wait: use a timeout of 0 millisecond
        return (_winapi.WaitForSingleObject(self._handle, 0) ==
                _winapi.WAIT_OBJECT_0)

    def _repr_info(self):
        info = super()._repr_info()
        info.append(f'handle={self._handle:#x}')
        if self._handle is not None:
            state = 'signaled' if self._poll() else 'waiting'
            info.append(state)
        if self._wait_handle is not None:
            info.append(f'wait_handle={self._wait_handle:#x}')
        return info

    def _unregister_wait_cb(self, fut):
        # The wait was unregistered: it's not safe to destroy the Overlapped
        # object
        self._ov = None

    def _unregister_wait(self):
        if not self._registered:
            return
        self._registered = False

        wait_handle = self._wait_handle
        self._wait_handle = None
        try:
            _overlapped.UnregisterWait(wait_handle)
        except OSError as exc:
            if exc.winerror != _overlapped.ERROR_IO_PENDING:
                context = {
                    'message': 'Failed to unregister the wait handle',
                    'exception': exc,
                    'future': self,
                }
                if self._source_traceback:
                    context['source_traceback'] = self._source_traceback
                self._loop.call_exception_handler(context)
                return
            # ERROR_IO_PENDING means that the unregister is pending

        self._unregister_wait_cb(None)

    def cancel(self, msg=None):
        self._unregister_wait()
        return super().cancel(msg=msg)

    def set_exception(self, exception):
        self._unregister_wait()
        super().set_exception(exception)

    def set_result(self, result):
        self._unregister_wait()
        super().set_result(result)


class _WaitCancelFuture(_BaseWaitHandleFuture):
    """Subclass of Future which represents a wait for the cancellation of a
    _WaitHandleFuture using an event.
    """

    def __init__(self, ov, event, wait_handle, *, loop=None):
        super().__init__(ov, event, wait_handle, loop=loop)

        self._done_callback = None

    def cancel(self):
        raise RuntimeError("_WaitCancelFuture must not be cancelled")

    def set_result(self, result):
        super().set_result(result)
        if self._done_callback is not None:
            self._done_callback(self)

    def set_exception(self, exception):
        super().set_exception(exception)
        if self._done_callback is not None:
            self._done_callback(self)


class _WaitHandleFuture(_BaseWaitHandleFuture):
    def __init__(self, ov, handle, wait_handle, proactor, *, loop=None):
        super().__init__(ov, handle, wait_handle, loop=loop)
        self._proactor = proactor
        self._unregister_proactor = True
        self._event = _overlapped.CreateEvent(None, True, False, None)
        self._event_fut = None

    def _unregister_wait_cb(self, fut):
        if self._event is not None:
            _winapi.CloseHandle(self._event)
            self._event = None
            self._event_fut = None

        # If the wait was cancelled, the wait may never be signalled, so
        # it's required to unregister it. Otherwise, IocpProactor.close() will
        # wait forever for an event which will never come.
        #
        # If the IocpProactor already received the event, it's safe to call
        # _unregister() because we kept a reference to the Overlapped object
        # which is used as a unique key.
        self._proactor._unregister(self._ov)
        self._proactor = None

        super()._unregister_wait_cb(fut)

    def _unregister_wait(self):
        if not self._registered:
            return
        self._registered = False

        wait_handle = self._wait_handle
        self._wait_handle = None
        try:
            _overlapped.UnregisterWaitEx(wait_handle, self._event)
        except OSError as exc:
            if exc.winerror != _overlapped.ERROR_IO_PENDING:
                context = {
                    'message': 'Failed to unregister the wait handle',
                    'exception': exc,
                    'future': self,
                }
                if self._source_traceback:
                    context['source_traceback'] = self._source_traceback
                self._loop.call_exception_handler(context)
                return
            # ERROR_IO_PENDING is not an error, the wait was unregistered

        self._event_fut = self._proactor._wait_cancel(self._event,
                                                      self._unregister_wait_cb)


class PipeServer(object):
    """Class representing a pipe server.

    This is much like a bound, listening socket.
    """
    def __init__(self, address):
        self._address = address
        self._free_instances = weakref.WeakSet()
        # initialize the pipe attribute before calling _server_pipe_handle()
        # because this function can raise an exception and the destructor calls
        # the close() method
        self._pipe = None
        self._accept_pipe_future = None
        self._pipe = self._server_pipe_handle(True)

    def _get_unconnected_pipe(self):
        # Create new instance and return previous one.  This ensures
        # that (until the server is closed) there is always at least
        # one pipe handle for address.  Therefore if a client attempt
        # to connect it will not fail with FileNotFoundError.
        tmp, self._pipe = self._pipe, self._server_pipe_handle(False)
        return tmp

    def _server_pipe_handle(self, first):
        # Return a wrapper for a new pipe handle.
        if self.closed():
            return None
        flags = _winapi.PIPE_ACCESS_DUPLEX | _winapi.FILE_FLAG_OVERLAPPED
        if first:
            flags |= _winapi.FILE_FLAG_FIRST_PIPE_INSTANCE
        h = _winapi.CreateNamedPipe(
            self._address, flags,
            _winapi.PIPE_TYPE_MESSAGE | _winapi.PIPE_READMODE_MESSAGE |
            _winapi.PIPE_WAIT,
            _winapi.PIPE_UNLIMITED_INSTANCES,
            windows_utils.BUFSIZE, windows_utils.BUFSIZE,
            _winapi.NMPWAIT_WAIT_FOREVER, _winapi.NULL)
        pipe = windows_utils.PipeHandle(h)
        self._free_instances.add(pipe)
        return pipe

    def closed(self):
        return (self._address is None)

    def close(self):
        if self._accept_pipe_future is not None:
            self._accept_pipe_future.cancel()
            self._accept_pipe_future = None
        # Close all instances which have not been connected to by a client.
        if self._address is not None:
            for pipe in self._free_instances:
                pipe.close()
            self._pipe = None
            self._address = None
            self._free_instances.clear()

    __del__ = close


class _WindowsSelectorEventLoop(selector_events.BaseSelectorEventLoop):
    """Windows version of selector event loop."""


class ProactorEventLoop(proactor_events.BaseProactorEventLoop):
    """Windows version of proactor event loop using IOCP."""

    def __init__(self, proactor=None):
        if proactor is None:
            proactor = IocpProactor()
        super().__init__(proactor)

    def _run_forever_setup(self):
        assert self._self_reading_future is None
        self.call_soon(self._loop_self_reading)
        super()._run_forever_setup()

    def _run_forever_cleanup(self):
        super()._run_forever_cleanup()
        if self._self_reading_future is not None:
            ov = self._self_reading_future._ov
            self._self_reading_future.cancel()
            # self_reading_future always uses IOCP, so even though it's
            # been cancelled, we need to make sure that the IOCP message
            # is received so that the kernel is not holding on to the
            # memory, possibly causing memory corruption later. Only
            # unregister it if IO is complete in all respects. Otherwise
            # we need another _poll() later to complete the IO.
            if ov is not None and not ov.pending:
                self._proactor._unregister(ov)
            self._self_reading_future = None

    async def create_pipe_connection(self, protocol_factory, address):
        f = self._proactor.connect_pipe(address)
        pipe = await f
        protocol = protocol_factory()
        trans = self._make_duplex_pipe_transport(pipe, protocol,
                                                 extra={'addr': address})
        return trans, protocol

    async def start_serving_pipe(self, protocol_factory, address):
        server = PipeServer(address)

        def loop_accept_pipe(f=None):
            pipe = None
            try:
                if f:
                    pipe = f.result()
                    server._free_instances.discard(pipe)

                    if server.closed():
                        # A client connected before the server was closed:
                        # drop the client (close the pipe) and exit
                        pipe.close()
                        return

                    protocol = protocol_factory()
                    self._make_duplex_pipe_transport(
                        pipe, protocol, extra={'addr': address})

                pipe = server._get_unconnected_pipe()
                if pipe is None:
                    return

                f = self._proactor.accept_pipe(pipe)
            except BrokenPipeError:
                if pipe and pipe.fileno() != -1:
                    pipe.close()
                self.call_soon(loop_accept_pipe)
            except OSError as exc:
                if pipe and pipe.fileno() != -1:
                    self.call_exception_handler({
                        'message': 'Pipe accept failed',
                        'exception': exc,
                        'pipe': pipe,
                    })
                    pipe.close()
                elif self._debug:
                    logger.warning("Accept pipe failed on pipe %r",
                                   pipe, exc_info=True)
                self.call_soon(loop_accept_pipe)
            except exceptions.CancelledError:
                if pipe:
                    pipe.close()
            else:
                server._accept_pipe_future = f
                f.add_done_callback(loop_accept_pipe)

        self.call_soon(loop_accept_pipe)
        return [server]

    async def _make_subprocess_transport(self, protocol, args, shell,
                                         stdin, stdout, stderr, bufsize,
                                         extra=None, **kwargs):
        waiter = self.create_future()
        transp = _WindowsSubprocessTransport(self, protocol, args, shell,
                                             stdin, stdout, stderr, bufsize,
                                             waiter=waiter, extra=extra,
                                             **kwargs)
        try:
            await waiter
        except (SystemExit, KeyboardInterrupt):
            raise
        except BaseException:
            transp.close()
            await transp._wait()
            raise

        return transp


class IocpProactor:
    """Proactor implementation using IOCP."""

    def __init__(self, concurrency=INFINITE):
        self._loop = None
        self._results = []
        self._iocp = _overlapped.CreateIoCompletionPort(
            _overlapped.INVALID_HANDLE_VALUE, NULL, 0, concurrency)
        self._cache = {}
        self._registered = weakref.WeakSet()
        self._unregistered = []
        self._stopped_serving = weakref.WeakSet()

    def _check_closed(self):
        if self._iocp is None:
            raise RuntimeError('IocpProactor is closed')

    def __repr__(self):
        info = ['overlapped#=%s' % len(self._cache),
                'result#=%s' % len(self._results)]
        if self._iocp is None:
            info.append('closed')
        return '<%s %s>' % (self.__class__.__name__, " ".join(info))

    def set_loop(self, loop):
        self._loop = loop

    def select(self, timeout=None):
        if not self._results:
            self._poll(timeout)
        tmp = self._results
        self._results = []
        try:
            return tmp
        finally:
            # Needed to break cycles when an exception occurs.
            tmp = None

    def _result(self, value):
        fut = self._loop.create_future()
        fut.set_result(value)
        return fut

    @staticmethod
    def finish_socket_func(trans, key, ov):
        try:
            return ov.getresult()
        except OSError as exc:
            if exc.winerror in (_overlapped.ERROR_NETNAME_DELETED,
                                _overlapped.ERROR_OPERATION_ABORTED):
                raise ConnectionResetError(*exc.args)
            else:
                raise

    @classmethod
    def _finish_recvfrom(cls, trans, key, ov, *, empty_result):
        try:
            return cls.finish_socket_func(trans, key, ov)
        except OSError as exc:
            # WSARecvFrom will report ERROR_PORT_UNREACHABLE when the same
            # socket is used to send to an address that is not listening.
