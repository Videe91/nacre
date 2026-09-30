"""Event loop and event loop policy."""

# Contains code from https://github.com/MagicStack/uvloop/tree/v0.16.0
# SPDX-License-Identifier: PSF-2.0 AND (MIT OR Apache-2.0)
# SPDX-FileCopyrightText: Copyright (c) 2015-2021 MagicStack Inc.  http://magic.io

__all__ = (
    "AbstractEventLoop",
    "AbstractServer",
    "Handle",
    "TimerHandle",
    "get_event_loop_policy",
    "set_event_loop_policy",
    "get_event_loop",
    "set_event_loop",
    "new_event_loop",
    "_set_running_loop",
    "get_running_loop",
    "_get_running_loop",
)

import contextvars
import os
import signal
import socket
import subprocess
import sys
import threading
import warnings

from . import format_helpers


class Handle:
    """Object returned by callback registration methods."""

    __slots__ = ('_callback', '_args', '_cancelled', '_loop',
                 '_source_traceback', '_repr', '__weakref__',
                 '_context')

    def __init__(self, callback, args, loop, context=None):
        if context is None:
            context = contextvars.copy_context()
        self._context = context
        self._loop = loop
        self._callback = callback
        self._args = args
        self._cancelled = False
        self._repr = None
        if self._loop.get_debug():
            self._source_traceback = format_helpers.extract_stack(
                sys._getframe(1))
        else:
            self._source_traceback = None

    def _repr_info(self):
        info = [self.__class__.__name__]
        if self._cancelled:
            info.append('cancelled')
        if self._callback is not None:
            info.append(format_helpers._format_callback_source(
                self._callback, self._args,
                debug=self._loop.get_debug()))
        if self._source_traceback:
            frame = self._source_traceback[-1]
            info.append(f'created at {frame[0]}:{frame[1]}')
        return info

    def __repr__(self):
        if self._repr is not None:
            return self._repr
        info = self._repr_info()
        return '<{}>'.format(' '.join(info))

    def get_context(self):
        return self._context

    def cancel(self):
        if not self._cancelled:
            self._cancelled = True
            if self._loop.get_debug():
                # Keep a representation in debug mode to keep callback and
                # parameters. For example, to log the warning
                # "Executing <Handle...> took 2.5 second"
                self._repr = repr(self)
            self._callback = None
            self._args = None

    def cancelled(self):
        return self._cancelled

    def _run(self):
        try:
            self._context.run(self._callback, *self._args)
        except (SystemExit, KeyboardInterrupt):
            raise
        except BaseException as exc:
            cb = format_helpers._format_callback_source(
                self._callback, self._args,
                debug=self._loop.get_debug())
            msg = f'Exception in callback {cb}'
            context = {
                'message': msg,
                'exception': exc,
                'handle': self,
            }
            if self._source_traceback:
                context['source_traceback'] = self._source_traceback
            self._loop.call_exception_handler(context)
        self = None  # Needed to break cycles when an exception occurs.

# _ThreadSafeHandle is used for callbacks scheduled with call_soon_threadsafe
# and is thread safe unlike Handle which is not thread safe.
class _ThreadSafeHandle(Handle):

    __slots__ = ('_lock',)

    def __init__(self, callback, args, loop, context=None):
        super().__init__(callback, args, loop, context)
        self._lock = threading.RLock()

    def cancel(self):
        with self._lock:
            return super().cancel()

    def cancelled(self):
        with self._lock:
            return super().cancelled()

    def _run(self):
        # The event loop checks for cancellation without holding the lock
        # It is possible that the handle is cancelled after the check
        # but before the callback is called so check it again after acquiring
        # the lock and return without calling the callback if it is cancelled.
        with self._lock:
            if self._cancelled:
                return
            return super()._run()


class TimerHandle(Handle):
    """Object returned by timed callback registration methods."""

    __slots__ = ['_scheduled', '_when']

    def __init__(self, when, callback, args, loop, context=None):
        super().__init__(callback, args, loop, context)
        if self._source_traceback:
            del self._source_traceback[-1]
        self._when = when
        self._scheduled = False

    def _repr_info(self):
        info = super()._repr_info()
        pos = 2 if self._cancelled else 1
        info.insert(pos, f'when={self._when}')
        return info

    def __hash__(self):
        return hash(self._when)

    def __lt__(self, other):
        if isinstance(other, TimerHandle):
            return self._when < other._when
        return NotImplemented

    def __le__(self, other):
        if isinstance(other, TimerHandle):
            return self._when < other._when or self.__eq__(other)
        return NotImplemented

    def __gt__(self, other):
        if isinstance(other, TimerHandle):
            return self._when > other._when
        return NotImplemented

    def __ge__(self, other):
        if isinstance(other, TimerHandle):
            return self._when > other._when or self.__eq__(other)
        return NotImplemented

    def __eq__(self, other):
        if isinstance(other, TimerHandle):
            return (self._when == other._when and
                    self._callback == other._callback and
                    self._args == other._args and
                    self._cancelled == other._cancelled)
        return NotImplemented

    def cancel(self):
        if not self._cancelled:
            self._loop._timer_handle_cancelled(self)
        super().cancel()

    def when(self):
        """Return a scheduled callback time.

        The time is an absolute timestamp, using the same time
        reference as loop.time().
        """
        return self._when


class AbstractServer:
    """Abstract server returned by create_server()."""

    def close(self):
        """Stop serving.  This leaves existing connections open."""
        raise NotImplementedError

    def close_clients(self):
        """Close all active connections."""
        raise NotImplementedError

    def abort_clients(self):
        """Close all active connections immediately."""
        raise NotImplementedError

    def get_loop(self):
        """Get the event loop the Server object is attached to."""
        raise NotImplementedError

    def is_serving(self):
        """Return True if the server is accepting connections."""
        raise NotImplementedError

    async def start_serving(self):
        """Start accepting connections.

        This method is idempotent, so it can be called when
        the server is already being serving.
        """
        raise NotImplementedError

    async def serve_forever(self):
        """Start accepting connections until the coroutine is cancelled.

        The server is closed when the coroutine is cancelled.
        """
        raise NotImplementedError

    async def wait_closed(self):
        """Coroutine to wait until service is closed."""
        raise NotImplementedError

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.close()
        await self.wait_closed()


class AbstractEventLoop:
    """Abstract event loop."""

    # Running and stopping the event loop.

    def run_forever(self):
        """Run the event loop until stop() is called."""
        raise NotImplementedError

    def run_until_complete(self, future):
        """Run the event loop until a Future is done.

        Return the Future's result, or raise its exception.
        """
        raise NotImplementedError

    def stop(self):
        """Stop the event loop as soon as reasonable.

        Exactly how soon that is may depend on the implementation, but
        no more I/O callbacks should be scheduled.
        """
        raise NotImplementedError

    def is_running(self):
        """Return whether the event loop is currently running."""
        raise NotImplementedError

    def is_closed(self):
        """Returns True if the event loop was closed."""
        raise NotImplementedError

    def close(self):
        """Close the loop.

        The loop should not be running.

        This is idempotent and irreversible.

        No other methods should be called after this one.
        """
        raise NotImplementedError

    async def shutdown_asyncgens(self):
        """Shutdown all active asynchronous generators."""
        raise NotImplementedError

    async def shutdown_default_executor(self):
        """Schedule the shutdown of the default executor."""
        raise NotImplementedError

    # Methods scheduling callbacks.  All these return Handles.

    def _timer_handle_cancelled(self, handle):
        """Notification that a TimerHandle has been cancelled."""
        raise NotImplementedError

    def call_soon(self, callback, *args, context=None):
        return self.call_later(0, callback, *args, context=context)

    def call_later(self, delay, callback, *args, context=None):
        raise NotImplementedError

    def call_at(self, when, callback, *args, context=None):
        raise NotImplementedError

    def time(self):
        raise NotImplementedError

    def create_future(self):
        raise NotImplementedError

    # Method scheduling a coroutine object: create a task.

    def create_task(self, coro, **kwargs):
        raise NotImplementedError

    # Methods for interacting with threads.

    def call_soon_threadsafe(self, callback, *args, context=None):
        raise NotImplementedError

    def run_in_executor(self, executor, func, *args):
        raise NotImplementedError

    def set_default_executor(self, executor):
        raise NotImplementedError

    # Network I/O methods returning Futures.

    async def getaddrinfo(self, host, port, *,
                          family=0, type=0, proto=0, flags=0):
        raise NotImplementedError

    async def getnameinfo(self, sockaddr, flags=0):
        raise NotImplementedError

    async def create_connection(
            self, protocol_factory, host=None, port=None,
            *, ssl=None, family=0, proto=0,
            flags=0, sock=None, local_addr=None,
            server_hostname=None,
            ssl_handshake_timeout=None,
            ssl_shutdown_timeout=None,
            happy_eyeballs_delay=None, interleave=None):
        raise NotImplementedError

    async def create_server(
            self, protocol_factory, host=None, port=None,
            *, family=socket.AF_UNSPEC,
            flags=socket.AI_PASSIVE, sock=None, backlog=100,
            ssl=None, reuse_address=None, reuse_port=None,
            keep_alive=None,
            ssl_handshake_timeout=None,
            ssl_shutdown_timeout=None,
            start_serving=True):
        """A coroutine which creates a TCP server bound to host and port.

        The return value is a Server object which can be used to stop
        the service.

        If host is an empty string or None all interfaces are assumed
        and a list of multiple sockets will be returned (most likely
        one for IPv4 and another one for IPv6). The host parameter can also be
        a sequence (e.g. list) of hosts to bind to.

        family can be set to either AF_INET or AF_INET6 to force the
        socket to use IPv4 or IPv6. If not set it will be determined
        from host (defaults to AF_UNSPEC).

        flags is a bitmask for getaddrinfo().

        sock can optionally be specified in order to use a preexisting
        socket object.

        backlog is the maximum number of queued connections passed to
        listen() (defaults to 100).

        ssl can be set to an SSLContext to enable SSL over the
        accepted connections.

        reuse_address tells the kernel to reuse a local socket in
        TIME_WAIT state, without waiting for its natural timeout to
        expire. If not specified will automatically be set to True on
        UNIX.

        reuse_port tells the kernel to allow this endpoint to be bound to
        the same port as other existing endpoints are bound to, so long as
        they all set this flag when being created. This option is not
        supported on Windows.

        keep_alive set to True keeps connections active by enabling the
        periodic transmission of messages.

        ssl_handshake_timeout is the time in seconds that an SSL server
        will wait for completion of the SSL handshake before aborting the
        connection. Default is 60s.

        ssl_shutdown_timeout is the time in seconds that an SSL server
        will wait for completion of the SSL shutdown procedure
        before aborting the connection. Default is 30s.

        start_serving set to True (default) causes the created server
        to start accepting connections immediately.  When set to False,
        the user should await Server.start_serving() or Server.serve_forever()
        to make the server to start accepting connections.
        """
        raise NotImplementedError

    async def sendfile(self, transport, file, offset=0, count=None,
                       *, fallback=True):
        """Send a file through a transport.

        Return an amount of sent bytes.
        """
        raise NotImplementedError

    async def start_tls(self, transport, protocol, sslcontext, *,
                        server_side=False,
                        server_hostname=None,
                        ssl_handshake_timeout=None,
                        ssl_shutdown_timeout=None):
        """Upgrade a transport to TLS.

        Return a new transport that *protocol* should start using
        immediately.
        """
        raise NotImplementedError

    async def create_unix_connection(
            self, protocol_factory, path=None, *,
            ssl=None, sock=None,
            server_hostname=None,
            ssl_handshake_timeout=None,
            ssl_shutdown_timeout=None):
        raise NotImplementedError

    async def create_unix_server(
            self, protocol_factory, path=None, *,
            sock=None, backlog=100, ssl=None,
            ssl_handshake_timeout=None,
            ssl_shutdown_timeout=None,
            start_serving=True):
        """A coroutine which creates a UNIX Domain Socket server.

        The return value is a Server object, which can be used to stop
        the service.

        path is a str, representing a file system path to bind the
        server socket to.

        sock can optionally be specified in order to use a preexisting
        socket object.

        backlog is the maximum number of queued connections passed to
        listen() (defaults to 100).

        ssl can be set to an SSLContext to enable SSL over the
        accepted connections.

        ssl_handshake_timeout is the time in seconds that an SSL server
        will wait for the SSL handshake to complete (defaults to 60s).

        ssl_shutdown_timeout is the time in seconds that an SSL server
        will wait for the SSL shutdown to finish (defaults to 30s).

        start_serving set to True (default) causes the created server
        to start accepting connections immediately.  When set to False,
        the user should await Server.start_serving() or Server.serve_forever()
        to make the server to start accepting connections.
        """
        raise NotImplementedError

    async def connect_accepted_socket(
            self, protocol_factory, sock,
            *, ssl=None,
            ssl_handshake_timeout=None,
            ssl_shutdown_timeout=None):
        """Handle an accepted connection.

        This is used by servers that accept connections outside of
        asyncio, but use asyncio to handle connections.

        This method is a coroutine.  When completed, the coroutine
        returns a (transport, protocol) pair.
        """
        raise NotImplementedError

