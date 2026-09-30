"""Event loop using a proactor and related classes.

A proactor is a "notify-on-completion" multiplexer.  Currently a
proactor is only implemented on Windows with IOCP.
"""

__all__ = 'BaseProactorEventLoop',

import io
import os
import socket
import warnings
import signal
import threading
import collections

from . import base_events
from . import constants
from . import futures
from . import exceptions
from . import protocols
from . import sslproto
from . import transports
from . import trsock
from .log import logger


def _set_socket_extra(transport, sock):
    transport._extra['socket'] = trsock.TransportSocket(sock)

    try:
        transport._extra['sockname'] = sock.getsockname()
    except socket.error:
        if transport._loop.get_debug():
            logger.warning(
                "getsockname() failed on %r", sock, exc_info=True)

    if 'peername' not in transport._extra:
        try:
            transport._extra['peername'] = sock.getpeername()
        except socket.error:
            # UDP sockets may not have a peer name
            transport._extra['peername'] = None


class _ProactorBasePipeTransport(transports._FlowControlMixin,
                                 transports.BaseTransport):
    """Base class for pipe and socket transports."""

    def __init__(self, loop, sock, protocol, waiter=None,
                 extra=None, server=None):
        super().__init__(extra, loop)
        self._set_extra(sock)
        self._sock = sock
        self.set_protocol(protocol)
        self._server = server
        self._buffer = None  # None or bytearray.
        self._read_fut = None
        self._write_fut = None
        self._pending_write = 0
        self._conn_lost = 0
        self._closing = False  # Set when close() called.
        self._called_connection_lost = False
        self._eof_written = False
        if self._server is not None:
            self._server._attach(self)
        self._loop.call_soon(self._protocol.connection_made, self)
        if waiter is not None:
            # only wake up the waiter when connection_made() has been called
            self._loop.call_soon(futures._set_result_unless_cancelled,
                                 waiter, None)

    def __repr__(self):
        info = [self.__class__.__name__]
        if self._sock is None:
            info.append('closed')
        elif self._closing:
            info.append('closing')
        if self._sock is not None:
            info.append(f'fd={self._sock.fileno()}')
        if self._read_fut is not None:
            info.append(f'read={self._read_fut!r}')
        if self._write_fut is not None:
            info.append(f'write={self._write_fut!r}')
        if self._buffer:
            info.append(f'write_bufsize={len(self._buffer)}')
        if self._eof_written:
            info.append('EOF written')
        return '<{}>'.format(' '.join(info))

    def _set_extra(self, sock):
        self._extra['pipe'] = sock

    def set_protocol(self, protocol):
        self._protocol = protocol

    def get_protocol(self):
        return self._protocol

    def is_closing(self):
        return self._closing

    def close(self):
        if self._closing:
            return
        self._closing = True
        self._conn_lost += 1
        if not self._buffer and self._write_fut is None:
            self._loop.call_soon(self._call_connection_lost, None)
        if self._read_fut is not None:
            self._read_fut.cancel()
            self._read_fut = None

    def __del__(self, _warn=warnings.warn):
        if self._sock is not None:
            _warn(f"unclosed transport {self!r}", ResourceWarning, source=self)
            self._sock.close()

    def _fatal_error(self, exc, message='Fatal error on pipe transport'):
        try:
            if isinstance(exc, OSError):
                if self._loop.get_debug():
                    logger.debug("%r: %s", self, message, exc_info=True)
            else:
                self._loop.call_exception_handler({
                    'message': message,
                    'exception': exc,
                    'transport': self,
                    'protocol': self._protocol,
                })
        finally:
            self._force_close(exc)

    def _force_close(self, exc):
        if self._empty_waiter is not None and not self._empty_waiter.done():
            if exc is None:
                self._empty_waiter.set_result(None)
            else:
                self._empty_waiter.set_exception(exc)
        if self._closing and self._called_connection_lost:
            return
        self._closing = True
        self._conn_lost += 1
        if self._write_fut:
            self._write_fut.cancel()
            self._write_fut = None
        if self._read_fut:
            self._read_fut.cancel()
            self._read_fut = None
        self._pending_write = 0
        self._buffer = None
        self._loop.call_soon(self._call_connection_lost, exc)

    def _call_connection_lost(self, exc):
        if self._called_connection_lost:
            return
        try:
            self._protocol.connection_lost(exc)
        finally:
            # XXX If there is a pending overlapped read on the other
            # end then it may fail with ERROR_NETNAME_DELETED if we
            # just close our end.  First calling shutdown() seems to
            # cure it, but maybe using DisconnectEx() would be better.
            if hasattr(self._sock, 'shutdown') and self._sock.fileno() != -1:
                self._sock.shutdown(socket.SHUT_RDWR)
            self._sock.close()
            self._sock = None
            server = self._server
            if server is not None:
                server._detach(self)
                self._server = None
            self._called_connection_lost = True

    def get_write_buffer_size(self):
        size = self._pending_write
        if self._buffer is not None:
            size += len(self._buffer)
        return size


class _ProactorReadPipeTransport(_ProactorBasePipeTransport,
                                 transports.ReadTransport):
    """Transport for read pipes."""

    def __init__(self, loop, sock, protocol, waiter=None,
                 extra=None, server=None, buffer_size=65536):
        self._pending_data_length = -1
        self._paused = True
        super().__init__(loop, sock, protocol, waiter, extra, server)

        self._data = bytearray(buffer_size)
        self._loop.call_soon(self._loop_reading)
        self._paused = False

    def is_reading(self):
        return not self._paused and not self._closing

    def pause_reading(self):
        if self._closing or self._paused:
            return
        self._paused = True

        # bpo-33694: Don't cancel self._read_fut because cancelling an
        # overlapped WSASend() loss silently data with the current proactor
        # implementation.
        #
        # If CancelIoEx() fails with ERROR_NOT_FOUND, it means that WSASend()
        # completed (even if HasOverlappedIoCompleted() returns 0), but
        # Overlapped.cancel() currently silently ignores the ERROR_NOT_FOUND
        # error. Once the overlapped is ignored, the IOCP loop will ignores the
        # completion I/O event and so not read the result of the overlapped
        # WSARecv().

        if self._loop.get_debug():
            logger.debug("%r pauses reading", self)

    def resume_reading(self):
        if self._closing or not self._paused:
            return

        self._paused = False
        if self._read_fut is None:
            self._loop.call_soon(self._loop_reading, None)

        length = self._pending_data_length
        self._pending_data_length = -1
        if length > -1:
            # Call the protocol method after calling _loop_reading(),
            # since the protocol can decide to pause reading again.
            self._loop.call_soon(self._data_received, self._data[:length], length)

        if self._loop.get_debug():
            logger.debug("%r resumes reading", self)

    def _eof_received(self):
        if self._loop.get_debug():
            logger.debug("%r received EOF", self)

        try:
            keep_open = self._protocol.eof_received()
        except (SystemExit, KeyboardInterrupt):
            raise
        except BaseException as exc:
            self._fatal_error(
                exc, 'Fatal error: protocol.eof_received() call failed.')
            return

        if not keep_open:
            self.close()

    def _data_received(self, data, length):
        if self._paused:
            # Don't call any protocol method while reading is paused.
            # The protocol will be called on resume_reading().
            assert self._pending_data_length == -1
            self._pending_data_length = length
            return

        if length == 0:
            self._eof_received()
            return

        if isinstance(self._protocol, protocols.BufferedProtocol):
            try:
                protocols._feed_data_to_buffered_proto(self._protocol, data)
            except (SystemExit, KeyboardInterrupt):
                raise
            except BaseException as exc:
                self._fatal_error(exc,
                                  'Fatal error: protocol.buffer_updated() '
                                  'call failed.')
                return
        else:
            self._protocol.data_received(data)

    def _loop_reading(self, fut=None):
        length = -1
        data = None
        try:
            if fut is not None:
                assert self._read_fut is fut or (self._read_fut is None and
                                                 self._closing)
                self._read_fut = None
                if fut.done():
                    # deliver data later in "finally" clause
                    length = fut.result()
                    if length == 0:
                        # we got end-of-file so no need to reschedule a new read
                        return

                    # It's a new slice so make it immutable so protocols upstream don't have problems
                    data = bytes(memoryview(self._data)[:length])
                else:
                    # the future will be replaced by next proactor.recv call
                    fut.cancel()

            if self._closing:
                # since close() has been called we ignore any read data
                return

            # bpo-33694: buffer_updated() has currently no fast path because of
            # a data loss issue caused by overlapped WSASend() cancellation.

            if not self._paused:
                # reschedule a new read
                self._read_fut = self._loop._proactor.recv_into(self._sock, self._data)
        except ConnectionAbortedError as exc:
            if not self._closing:
                self._fatal_error(exc, 'Fatal read error on pipe transport')
            elif self._loop.get_debug():
                logger.debug("Read error on pipe transport while closing",
                             exc_info=True)
        except ConnectionResetError as exc:
            self._force_close(exc)
        except OSError as exc:
            self._fatal_error(exc, 'Fatal read error on pipe transport')
        except exceptions.CancelledError:
            if not self._closing:
                raise
        else:
            if not self._paused:
                self._read_fut.add_done_callback(self._loop_reading)
        finally:
            if length > -1:
                self._data_received(data, length)


class _ProactorBaseWritePipeTransport(_ProactorBasePipeTransport,
                                      transports.WriteTransport):
    """Transport for write pipes."""

    _start_tls_compatible = True

    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self._empty_waiter = None

    def write(self, data):
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError(
                f"data argument must be a bytes-like object, "
                f"not {type(data).__name__}")
        if self._eof_written:
            raise RuntimeError('write_eof() already called')
        if self._empty_waiter is not None:
            raise RuntimeError('unable to write; sendfile is in progress')

        if not data:
            return

        if self._conn_lost:
            if self._conn_lost >= constants.LOG_THRESHOLD_FOR_CONNLOST_WRITES:
                logger.warning('socket.send() raised exception.')
            self._conn_lost += 1
            return

        # Observable states:
        # 1. IDLE: _write_fut and _buffer both None
        # 2. WRITING: _write_fut set; _buffer None
        # 3. BACKED UP: _write_fut set; _buffer a bytearray
        # We always copy the data, so the caller can't modify it
        # while we're still waiting for the I/O to happen.
        if self._write_fut is None:  # IDLE -> WRITING
            assert self._buffer is None
            # Pass a copy, except if it's already immutable.
            self._loop_writing(data=bytes(data))
        elif not self._buffer:  # WRITING -> BACKED UP
            # Make a mutable copy which we can extend.
            self._buffer = bytearray(data)
            self._maybe_pause_protocol()
        else:  # BACKED UP
            # Append to buffer (also copies).
            self._buffer.extend(data)
            self._maybe_pause_protocol()

    def _loop_writing(self, f=None, data=None):
        try:
            if f is not None and self._write_fut is None and self._closing:
                # XXX most likely self._force_close() has been called, and
                # it has set self._write_fut to None.
                return
            assert f is self._write_fut
            self._write_fut = None
            self._pending_write = 0
            if f:
                f.result()
            if data is None:
                data = self._buffer
                self._buffer = None
            if not data:
                if self._closing:
                    self._loop.call_soon(self._call_connection_lost, None)
                if self._eof_written:
                    self._sock.shutdown(socket.SHUT_WR)
                # Now that we've reduced the buffer size, tell the
                # protocol to resume writing if it was paused.  Note that
                # we do this last since the callback is called immediately
                # and it may add more data to the buffer (even causing the
                # protocol to be paused again).
                self._maybe_resume_protocol()
            else:
                self._write_fut = self._loop._proactor.send(self._sock, data)
                if not self._write_fut.done():
                    assert self._pending_write == 0
                    self._pending_write = len(data)
                    self._write_fut.add_done_callback(self._loop_writing)
                    self._maybe_pause_protocol()
                else:
                    self._write_fut.add_done_callback(self._loop_writing)
            if self._empty_waiter is not None and self._write_fut is None:
                self._empty_waiter.set_result(None)
        except ConnectionResetError as exc:
            self._force_close(exc)
        except OSError as exc:
            self._fatal_error(exc, 'Fatal write error on pipe transport')

    def can_write_eof(self):
        return True

    def write_eof(self):
        self.close()

    def abort(self):
        self._force_close(None)

    def _make_empty_waiter(self):
        if self._empty_waiter is not None:
            raise RuntimeError("Empty waiter is already set")
        self._empty_waiter = self._loop.create_future()
        if self._write_fut is None:
            self._empty_waiter.set_result(None)
        return self._empty_waiter

    def _reset_empty_waiter(self):
        self._empty_waiter = None


class _ProactorWritePipeTransport(_ProactorBaseWritePipeTransport):
    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self._read_fut = self._loop._proactor.recv(self._sock, 16)
        self._read_fut.add_done_callback(self._pipe_closed)

    def _pipe_closed(self, fut):
        if fut.cancelled():
            # the transport has been closed
            return
        assert fut.result() == b''
        if self._closing:
