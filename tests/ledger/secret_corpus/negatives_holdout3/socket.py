# Wrapper module for _socket, providing some additional facilities
# implemented in Python.

"""\
This module provides socket operations and some related functions.
On Unix, it supports IP (Internet Protocol) and Unix domain sockets.
On other systems, it only supports IP. Functions specific for a
socket are available as methods of the socket object.

Functions:

socket() -- create a new socket object
socketpair() -- create a pair of new socket objects [*]
fromfd() -- create a socket object from an open file descriptor [*]
send_fds() -- Send file descriptor to the socket.
recv_fds() -- Receive file descriptors from the socket.
fromshare() -- create a socket object from data received from socket.share() [*]
gethostname() -- return the current hostname
gethostbyname() -- map a hostname to its IP number
gethostbyaddr() -- map an IP number or hostname to DNS info
getservbyname() -- map a service name and a protocol name to a port number
getprotobyname() -- map a protocol name (e.g. 'tcp') to a number
ntohs(), ntohl() -- convert 16, 32 bit int from network to host byte order
htons(), htonl() -- convert 16, 32 bit int from host to network byte order
inet_aton() -- convert IP addr string (123.45.67.89) to 32-bit packed format
inet_ntoa() -- convert 32-bit packed format IP to string (123.45.67.89)
socket.getdefaulttimeout() -- get the default timeout value
socket.setdefaulttimeout() -- set the default timeout value
create_connection() -- connects to an address, with an optional timeout and
                       optional source address.
create_server() -- create a TCP socket and bind it to a specified address.

 [*] not available on all platforms!

Special objects:

SocketType -- type object for socket objects
error -- exception raised for I/O errors
has_ipv6 -- boolean value indicating if IPv6 is supported

IntEnum constants:

AF_INET, AF_UNIX -- socket domains (first argument to socket() call)
SOCK_STREAM, SOCK_DGRAM, SOCK_RAW -- socket types (second argument)

Integer constants:

Many other constants may be defined; these may be used in calls to
the setsockopt() and getsockopt() methods.
"""

import _socket
from _socket import *

import io
import os
import sys
from enum import IntEnum, IntFlag

try:
    import errno
except ImportError:
    errno = None
EBADF = getattr(errno, 'EBADF', 9)
EAGAIN = getattr(errno, 'EAGAIN', 11)
EWOULDBLOCK = getattr(errno, 'EWOULDBLOCK', 11)

__all__ = ["fromfd", "getfqdn", "create_connection", "create_server",
           "has_dualstack_ipv6", "AddressFamily", "SocketKind"]
__all__.extend(os._get_exports_list(_socket))

# Set up the socket.AF_* socket.SOCK_* constants as members of IntEnums for
# nicer string representations.
# Note that _socket only knows about the integer values. The public interface
# in this module understands the enums and translates them back from integers
# where needed (e.g. .family property of a socket object).

IntEnum._convert_(
        'AddressFamily',
        __name__,
        lambda C: C.isupper() and C.startswith('AF_'))

IntEnum._convert_(
        'SocketKind',
        __name__,
        lambda C: C.isupper() and C.startswith('SOCK_'))

IntFlag._convert_(
        'MsgFlag',
        __name__,
        lambda C: C.isupper() and C.startswith('MSG_'))

IntFlag._convert_(
        'AddressInfo',
        __name__,
        lambda C: C.isupper() and C.startswith('AI_'))

_LOCALHOST    = '127.0.0.1'
_LOCALHOST_V6 = '::1'


def _intenum_converter(value, enum_klass):
    """Convert a numeric family value to an IntEnum member.

    If it's not a known member, return the numeric value itself.
    """
    try:
        return enum_klass(value)
    except ValueError:
        return value


# WSA error codes
if sys.platform.lower().startswith("win"):
    errorTab = {
        6: "Specified event object handle is invalid.",
        8: "Insufficient memory available.",
        87: "One or more parameters are invalid.",
        995: "Overlapped operation aborted.",
        996: "Overlapped I/O event object not in signaled state.",
        997: "Overlapped operation will complete later.",
        10004: "The operation was interrupted.",
        10009: "A bad file handle was passed.",
        10013: "Permission denied.",
        10014: "A fault occurred on the network??",
        10022: "An invalid operation was attempted.",
        10024: "Too many open files.",
        10035: "The socket operation would block.",
        10036: "A blocking operation is already in progress.",
        10037: "Operation already in progress.",
        10038: "Socket operation on nonsocket.",
        10039: "Destination address required.",
        10040: "Message too long.",
        10041: "Protocol wrong type for socket.",
        10042: "Bad protocol option.",
        10043: "Protocol not supported.",
        10044: "Socket type not supported.",
        10045: "Operation not supported.",
        10046: "Protocol family not supported.",
        10047: "Address family not supported by protocol family.",
        10048: "The network address is in use.",
        10049: "Cannot assign requested address.",
        10050: "Network is down.",
        10051: "Network is unreachable.",
        10052: "Network dropped connection on reset.",
        10053: "Software caused connection abort.",
        10054: "The connection has been reset.",
        10055: "No buffer space available.",
        10056: "Socket is already connected.",
        10057: "Socket is not connected.",
        10058: "The network has been shut down.",
        10059: "Too many references.",
        10060: "The operation timed out.",
        10061: "Connection refused.",
        10062: "Cannot translate name.",
        10063: "The name is too long.",
        10064: "The host is down.",
        10065: "The host is unreachable.",
        10066: "Directory not empty.",
        10067: "Too many processes.",
        10068: "User quota exceeded.",
        10069: "Disk quota exceeded.",
        10070: "Stale file handle reference.",
        10071: "Item is remote.",
        10091: "Network subsystem is unavailable.",
        10092: "Winsock.dll version out of range.",
        10093: "Successful WSAStartup not yet performed.",
        10101: "Graceful shutdown in progress.",
        10102: "No more results from WSALookupServiceNext.",
        10103: "Call has been canceled.",
        10104: "Procedure call table is invalid.",
        10105: "Service provider is invalid.",
        10106: "Service provider failed to initialize.",
        10107: "System call failure.",
        10108: "Service not found.",
        10109: "Class type not found.",
        10110: "No more results from WSALookupServiceNext.",
        10111: "Call was canceled.",
        10112: "Database query was refused.",
        11001: "Host not found.",
        11002: "Nonauthoritative host not found.",
        11003: "This is a nonrecoverable error.",
        11004: "Valid name, no data record requested type.",
        11005: "QoS receivers.",
        11006: "QoS senders.",
        11007: "No QoS senders.",
        11008: "QoS no receivers.",
        11009: "QoS request confirmed.",
        11010: "QoS admission error.",
        11011: "QoS policy failure.",
        11012: "QoS bad style.",
        11013: "QoS bad object.",
        11014: "QoS traffic control error.",
        11015: "QoS generic error.",
        11016: "QoS service type error.",
        11017: "QoS flowspec error.",
        11018: "Invalid QoS provider buffer.",
        11019: "Invalid QoS filter style.",
        11020: "Invalid QoS filter style.",
        11021: "Incorrect QoS filter count.",
        11022: "Invalid QoS object length.",
        11023: "Incorrect QoS flow count.",
        11024: "Unrecognized QoS object.",
        11025: "Invalid QoS policy object.",
        11026: "Invalid QoS flow descriptor.",
        11027: "Invalid QoS provider-specific flowspec.",
        11028: "Invalid QoS provider-specific filterspec.",
        11029: "Invalid QoS shape discard mode object.",
        11030: "Invalid QoS shaping rate object.",
        11031: "Reserved policy QoS element type."
    }
    __all__.append("errorTab")


class _GiveupOnSendfile(Exception): pass


class socket(_socket.socket):

    """A subclass of _socket.socket adding the makefile() method."""

    __slots__ = ["__weakref__", "_io_refs", "_closed"]

    def __init__(self, family=-1, type=-1, proto=-1, fileno=None):
        # For user code address family and type values are IntEnum members, but
        # for the underlying _socket.socket they're just integers. The
        # constructor of _socket.socket converts the given argument to an
        # integer automatically.
        if fileno is None:
            if family == -1:
                family = AF_INET
            if type == -1:
                type = SOCK_STREAM
            if proto == -1:
                proto = 0
        _socket.socket.__init__(self, family, type, proto, fileno)
        self._io_refs = 0
        self._closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        if not self._closed:
            self.close()

    def __repr__(self):
        """Wrap __repr__() to reveal the real class name and socket
        address(es).
        """
        closed = getattr(self, '_closed', False)
        s = "<%s.%s%s fd=%i, family=%s, type=%s, proto=%i" \
            % (self.__class__.__module__,
               self.__class__.__qualname__,
               " [closed]" if closed else "",
               self.fileno(),
               self.family,
               self.type,
               self.proto)
        if not closed:
            # getsockname and getpeername may not be available on WASI.
            try:
                laddr = self.getsockname()
                if laddr:
                    s += ", laddr=%s" % str(laddr)
            except (error, AttributeError):
                pass
            try:
                raddr = self.getpeername()
                if raddr:
                    s += ", raddr=%s" % str(raddr)
            except (error, AttributeError):
                pass
        s += '>'
        return s

    def __getstate__(self):
        raise TypeError(f"cannot pickle {self.__class__.__name__!r} object")

    def dup(self):
        """dup() -> socket object

        Duplicate the socket. Return a new socket object connected to the same
        system resource. The new socket is non-inheritable.
        """
        fd = dup(self.fileno())
        sock = self.__class__(self.family, self.type, self.proto, fileno=fd)
        sock.settimeout(self.gettimeout())
        return sock

    def accept(self):
        """accept() -> (socket object, address info)

        Wait for an incoming connection.  Return a new socket
        representing the connection, and the address of the client.
        For IP sockets, the address info is a pair (hostaddr, port).
        """
        fd, addr = self._accept()
        sock = socket(self.family, self.type, self.proto, fileno=fd)
        # Issue #7995: if no default timeout is set and the listening
        # socket had a (non-zero) timeout, force the new socket in blocking
        # mode to override platform-specific socket flags inheritance.
        if getdefaulttimeout() is None and self.gettimeout():
            sock.setblocking(True)
        return sock, addr

    def makefile(self, mode="r", buffering=None, *,
                 encoding=None, errors=None, newline=None):
        """makefile(...) -> an I/O stream connected to the socket

        The arguments are as for io.open() after the filename, except the only
        supported mode values are 'r' (default), 'w', 'b', or a combination of
        those.
        """
        # XXX refactor to share code?
        if not set(mode) <= {"r", "w", "b"}:
            raise ValueError("invalid mode %r (only r, w, b allowed)" % (mode,))
        writing = "w" in mode
        reading = "r" in mode or not writing
        assert reading or writing
        binary = "b" in mode
        rawmode = ""
        if reading:
            rawmode += "r"
        if writing:
            rawmode += "w"
        raw = SocketIO(self, rawmode)
        self._io_refs += 1
        if buffering is None:
            buffering = -1
        if buffering < 0:
            buffering = io.DEFAULT_BUFFER_SIZE
        if buffering == 0:
            if not binary:
                raise ValueError("unbuffered streams must be binary")
            return raw
        if reading and writing:
            buffer = io.BufferedRWPair(raw, raw, buffering)
        elif reading:
            buffer = io.BufferedReader(raw, buffering)
        else:
            assert writing
            buffer = io.BufferedWriter(raw, buffering)
        if binary:
            return buffer
        encoding = io.text_encoding(encoding)
        text = io.TextIOWrapper(buffer, encoding, errors, newline)
        text.mode = mode
        return text

    if hasattr(os, 'sendfile'):

        def _sendfile_use_sendfile(self, file, offset=0, count=None):
            # Lazy import to improve module import time
            import selectors

            self._check_sendfile_params(file, offset, count)
            sockno = self.fileno()
            try:
                fileno = file.fileno()
            except (AttributeError, io.UnsupportedOperation) as err:
                raise _GiveupOnSendfile(err)  # not a regular file
            try:
                fsize = os.fstat(fileno).st_size
            except OSError as err:
                raise _GiveupOnSendfile(err)  # not a regular file
            if not fsize:
                return 0  # empty file
            # Truncate to 1GiB to avoid OverflowError, see bpo-38319.
            blocksize = min(count or fsize, 2 ** 30)
            timeout = self.gettimeout()
            if timeout == 0:
                raise ValueError("non-blocking sockets are not supported")
            # poll/select have the advantage of not requiring any
            # extra file descriptor, contrarily to epoll/kqueue
            # (also, they require a single syscall).
            if hasattr(selectors, 'PollSelector'):
                selector = selectors.PollSelector()
            else:
                selector = selectors.SelectSelector()
            selector.register(sockno, selectors.EVENT_WRITE)

            total_sent = 0
            # localize variable access to minimize overhead
            selector_select = selector.select
            os_sendfile = os.sendfile
            try:
                while True:
                    if timeout and not selector_select(timeout):
                        raise TimeoutError('timed out')
                    if count:
                        blocksize = min(count - total_sent, blocksize)
                        if blocksize <= 0:
                            break
                    try:
                        sent = os_sendfile(sockno, fileno, offset, blocksize)
                    except BlockingIOError:
                        if not timeout:
                            # Block until the socket is ready to send some
                            # data; avoids hogging CPU resources.
                            selector_select()
                        continue
                    except OSError as err:
                        if total_sent == 0:
                            # We can get here for different reasons, the main
                            # one being 'file' is not a regular mmap(2)-like
                            # file, in which case we'll fall back on using
                            # plain send().
                            raise _GiveupOnSendfile(err)
                        raise err from None
                    else:
                        if sent == 0:
                            break  # EOF
                        offset += sent
                        total_sent += sent
                return total_sent
            finally:
                if total_sent > 0 and hasattr(file, 'seek'):
                    file.seek(offset)
    else:
        def _sendfile_use_sendfile(self, file, offset=0, count=None):
            raise _GiveupOnSendfile(
                "os.sendfile() not available on this platform")

    def _sendfile_use_send(self, file, offset=0, count=None):
