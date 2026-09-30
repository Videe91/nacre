"""IMAP4 client.

Based on RFC 2060.

Public class:           IMAP4
Public variable:        Debug
Public functions:       Internaldate2tuple
                        Int2AP
                        ParseFlags
                        Time2Internaldate
"""

# Author: Piers Lauder <piers@cs.su.oz.au> December 1997.
#
# Authentication code contributed by Donn Cave <donn@u.washington.edu> June 1998.
# String method conversion by ESR, February 2001.
# GET/SETACL contributed by Anthony Baxter <anthony@interlink.com.au> April 2001.
# IMAP4_SSL contributed by Tino Lange <Tino.Lange@isg.de> March 2002.
# GET/SETQUOTA contributed by Andreas Zeidler <az@kreativkombinat.de> June 2002.
# PROXYAUTH contributed by Rick Holbert <holbert.13@osu.edu> November 2002.
# GET/SETANNOTATION contributed by Tomas Lindroos <skitta@abo.fi> June 2005.
# IDLE contributed by Forest <forestix@nom.one> August 2024.

__version__ = "2.60"

import binascii, errno, random, re, socket, subprocess, sys, time, calendar
from datetime import datetime, timezone, timedelta
from io import DEFAULT_BUFFER_SIZE

try:
    import ssl
    HAVE_SSL = True
except ImportError:
    HAVE_SSL = False

__all__ = ["IMAP4", "IMAP4_stream", "Internaldate2tuple",
           "Int2AP", "ParseFlags", "Time2Internaldate"]

#       Globals

CRLF = b'\r\n'
Debug = 0
IMAP4_PORT = 143
IMAP4_SSL_PORT = 993
AllowedVersions = ('IMAP4REV1', 'IMAP4')        # Most recent first

# Maximal line length when calling readline(). This is to prevent
# reading arbitrary length lines. RFC 3501 and 2060 (IMAP 4rev1)
# don't specify a line length. RFC 2683 suggests limiting client
# command lines to 1000 octets and that servers should be prepared
# to accept command lines up to 8000 octets, so we used to use 10K here.
# In the modern world (eg: gmail) the response to, for example, a
# search command can be quite large, so we now use 1M.
_MAXLINE = 1000000


#       Commands

Commands = {
        # name            valid states
        'APPEND':       ('AUTH', 'SELECTED'),
        'AUTHENTICATE': ('NONAUTH',),
        'CAPABILITY':   ('NONAUTH', 'AUTH', 'SELECTED', 'LOGOUT'),
        'CHECK':        ('SELECTED',),
        'CLOSE':        ('SELECTED',),
        'COPY':         ('SELECTED',),
        'CREATE':       ('AUTH', 'SELECTED'),
        'DELETE':       ('AUTH', 'SELECTED'),
        'DELETEACL':    ('AUTH', 'SELECTED'),
        'ENABLE':       ('AUTH', ),
        'EXAMINE':      ('AUTH', 'SELECTED'),
        'EXPUNGE':      ('SELECTED',),
        'FETCH':        ('SELECTED',),
        'GETACL':       ('AUTH', 'SELECTED'),
        'GETANNOTATION':('AUTH', 'SELECTED'),
        'GETQUOTA':     ('AUTH', 'SELECTED'),
        'GETQUOTAROOT': ('AUTH', 'SELECTED'),
        'IDLE':         ('AUTH', 'SELECTED'),
        'MYRIGHTS':     ('AUTH', 'SELECTED'),
        'LIST':         ('AUTH', 'SELECTED'),
        'LOGIN':        ('NONAUTH',),
        'LOGOUT':       ('NONAUTH', 'AUTH', 'SELECTED', 'LOGOUT'),
        'LSUB':         ('AUTH', 'SELECTED'),
        'MOVE':         ('SELECTED',),
        'NAMESPACE':    ('AUTH', 'SELECTED'),
        'NOOP':         ('NONAUTH', 'AUTH', 'SELECTED', 'LOGOUT'),
        'PARTIAL':      ('SELECTED',),                                  # NB: obsolete
        'PROXYAUTH':    ('AUTH',),
        'RENAME':       ('AUTH', 'SELECTED'),
        'SEARCH':       ('SELECTED',),
        'SELECT':       ('AUTH', 'SELECTED'),
        'SETACL':       ('AUTH', 'SELECTED'),
        'SETANNOTATION':('AUTH', 'SELECTED'),
        'SETQUOTA':     ('AUTH', 'SELECTED'),
        'SORT':         ('SELECTED',),
        'STARTTLS':     ('NONAUTH',),
        'STATUS':       ('AUTH', 'SELECTED'),
        'STORE':        ('SELECTED',),
        'SUBSCRIBE':    ('AUTH', 'SELECTED'),
        'THREAD':       ('SELECTED',),
        'UID':          ('SELECTED',),
        'UNSUBSCRIBE':  ('AUTH', 'SELECTED'),
        'UNSELECT':     ('SELECTED',),
        }

#       Patterns to match server responses

Continuation = re.compile(br'\+( (?P<data>.*))?')
Flags = re.compile(br'.*FLAGS \((?P<flags>[^\)]*)\)')
InternalDate = re.compile(br'.*INTERNALDATE "'
        br'(?P<day>[ 0123][0-9])-(?P<mon>[A-Z][a-z][a-z])-(?P<year>[0-9][0-9][0-9][0-9])'
        br' (?P<hour>[0-9][0-9]):(?P<min>[0-9][0-9]):(?P<sec>[0-9][0-9])'
        br' (?P<zonen>[-+])(?P<zoneh>[0-9][0-9])(?P<zonem>[0-9][0-9])'
        br'"')
# Literal is no longer used; kept for backward compatibility.
Literal = re.compile(br'.*{(?P<size>\d+)}$', re.ASCII)
MapCRLF = re.compile(br'\r\n|\r|\n')
# We no longer exclude the ']' character from the data portion of the response
# code, even though it violates the RFC.  Popular IMAP servers such as Gmail
# allow flags with ']', and there are programs (including imaplib!) that can
# produce them.  The problem with this is if the 'text' portion of the response
# includes a ']' we'll parse the response wrong (which is the point of the RFC
# restriction).  However, that seems less likely to be a problem in practice
# than being unable to correctly parse flags that include ']' chars, which
# was reported as a real-world problem in issue #21815.
Response_code = re.compile(br'\[(?P<type>[A-Z-]+)( (?P<data>.*))?\]')
Untagged_response = re.compile(br'\* (?P<type>[A-Z-]+)( (?P<data>.*))?')
# Untagged_status is no longer used; kept for backward compatibility
Untagged_status = re.compile(
    br'\* (?P<data>\d+) (?P<type>[A-Z-]+)( (?P<data2>.*))?', re.ASCII)
# We compile these in _mode_xxx.
_Literal = br'.*{(?P<size>\d+)}$'
_Untagged_status = br'\* (?P<data>\d+) (?P<type>[A-Z-]+)( (?P<data2>.*))?'



class IMAP4:

    r"""IMAP4 client class.

    Instantiate with: IMAP4([host[, port[, timeout=None]]])

            host - host's name (default: localhost);
            port - port number (default: standard IMAP4 port).
            timeout - socket timeout (default: None)
                      If timeout is not given or is None,
                      the global default socket timeout is used

    All IMAP4rev1 commands are supported by methods of the same
    name (in lowercase).

    All arguments to commands are converted to strings, except for
    AUTHENTICATE, and the last argument to APPEND which is passed as
    an IMAP4 literal.  If necessary (the string contains any
    non-printing characters or white-space and isn't enclosed with
    either parentheses or double quotes) each string is quoted.
    However, the 'password' argument to the LOGIN command is always
    quoted.  If you want to avoid having an argument string quoted
    (eg: the 'flags' argument to STORE) then enclose the string in
    parentheses (eg: "(\Deleted)").

    Each command returns a tuple: (type, [data, ...]) where 'type'
    is usually 'OK' or 'NO', and 'data' is either the text from the
    tagged response, or untagged results from command. Each 'data'
    is either a string, or a tuple. If a tuple, then the first part
    is the header of the response, and the second part contains
    the data (ie: 'literal' value).

    Errors raise the exception class <instance>.error("<reason>").
    IMAP4 server errors raise <instance>.abort("<reason>"),
    which is a sub-class of 'error'. Mailbox status changes
    from READ-WRITE to READ-ONLY raise the exception class
    <instance>.readonly("<reason>"), which is a sub-class of 'abort'.

    "error" exceptions imply a program error.
    "abort" exceptions imply the connection should be reset, and
            the command re-tried.
    "readonly" exceptions imply the command should be re-tried.

    Note: to use this module, you must read the RFCs pertaining to the
    IMAP4 protocol, as the semantics of the arguments to each IMAP4
    command are left to the invoker, not to mention the results. Also,
    most IMAP servers implement a sub-set of the commands available here.
    """

    class error(Exception): pass    # Logical errors - debug required
    class abort(error): pass        # Service errors - close and retry
    class readonly(abort): pass     # Mailbox status changed to READ-ONLY
    class _responsetimeout(TimeoutError): pass # No response during IDLE

    def __init__(self, host='', port=IMAP4_PORT, timeout=None):
        self.debug = Debug
        self.state = 'LOGOUT'
        self.literal = None             # A literal argument to a command
        self.tagged_commands = {}       # Tagged commands awaiting response
        self.untagged_responses = {}    # {typ: [data, ...], ...}
        self.continuation_response = '' # Last continuation response
        self._idle_responses = []       # Response queue for idle iteration
        self._idle_capture = False      # Whether to queue responses for idle
        self.is_readonly = False        # READ-ONLY desired state
        self.tagnum = 0
        self._tls_established = False
        self._mode_ascii()
        self._readbuf = []

        # Open socket to server.

        self.open(host, port, timeout)

        try:
            self._connect()
        except Exception:
            try:
                self.shutdown()
            except OSError:
                pass
            raise

    def _mode_ascii(self):
        self.utf8_enabled = False
        self._encoding = 'ascii'
        self.Literal = re.compile(_Literal, re.ASCII)
        self.Untagged_status = re.compile(_Untagged_status, re.ASCII)


    def _mode_utf8(self):
        self.utf8_enabled = True
        self._encoding = 'utf-8'
        self.Literal = re.compile(_Literal)
        self.Untagged_status = re.compile(_Untagged_status)


    def _connect(self):
        # Create unique tag for this session,
        # and compile tagged response matcher.

        self.tagpre = Int2AP(random.randint(4096, 65535))
        self.tagre = re.compile(br'(?P<tag>'
                        + self.tagpre
                        + br'\d+) (?P<type>[A-Z]+) (?P<data>.*)', re.ASCII)

        # Get server welcome message,
        # request and store CAPABILITY response.

        if __debug__:
            self._cmd_log_len = 10
            self._cmd_log_idx = 0
            self._cmd_log = {}           # Last '_cmd_log_len' interactions
            if self.debug >= 1:
                self._mesg('imaplib version %s' % __version__)
                self._mesg('new IMAP4 connection, tag=%s' % self.tagpre)

        self.welcome = self._get_response()
        if 'PREAUTH' in self.untagged_responses:
            self.state = 'AUTH'
        elif 'OK' in self.untagged_responses:
            self.state = 'NONAUTH'
        else:
            raise self.error(self.welcome)

        self._get_capabilities()
        if __debug__:
            if self.debug >= 3:
                self._mesg('CAPABILITIES: %r' % (self.capabilities,))

        for version in AllowedVersions:
            if not version in self.capabilities:
                continue
            self.PROTOCOL_VERSION = version
            return

        raise self.error('server not IMAP4 compliant')


    def __getattr__(self, attr):
        #       Allow UPPERCASE variants of IMAP4 command methods.
        if attr in Commands:
            return getattr(self, attr.lower())
        raise AttributeError("Unknown IMAP4 command: '%s'" % attr)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        if self.state == "LOGOUT":
            return

        try:
            self.logout()
        except OSError:
            pass


    #       Overridable methods


    def _create_socket(self, timeout):
        # Default value of IMAP4.host is '', but socket.getaddrinfo()
        # (which is used by socket.create_connection()) expects None
        # as a default value for host.
        if timeout is not None and not timeout:
            raise ValueError('Non-blocking socket (timeout=0) is not supported')
        host = None if not self.host else self.host
        sys.audit("imaplib.open", self, self.host, self.port)
        address = (host, self.port)
        if timeout is not None:
            return socket.create_connection(address, timeout)
        return socket.create_connection(address)

    def open(self, host='', port=IMAP4_PORT, timeout=None):
        """Setup connection to remote server on "host:port"
            (default: localhost:standard IMAP4 port).
        This connection will be used by the routines:
            read, readline, send, shutdown.
        """
        self.host = host
        self.port = port
        self.sock = self._create_socket(timeout)
        self._file = self.sock.makefile('rb')


    @property
    def file(self):
        # The old 'file' attribute is no longer used now that we do our own
        # read() and readline() buffering, with which it conflicts.
        # As an undocumented interface, it should never have been accessed by
        # external code, and therefore does not warrant deprecation.
        # Nevertheless, we provide this property for now, to avoid suddenly
        # breaking any code in the wild that might have been using it in a
        # harmless way.
        import warnings
        warnings.warn(
            'IMAP4.file is unsupported, can cause errors, and may be removed.',
            RuntimeWarning,
            stacklevel=2)
        return self._file


    def read(self, size):
        """Read 'size' bytes from remote."""
        # We need buffered read() to continue working after socket timeouts,
        # since we use them during IDLE. Unfortunately, the standard library's
        # SocketIO implementation makes this impossible, by setting a permanent
        # error condition instead of letting the caller decide how to handle a
        # timeout. We therefore implement our own buffered read().
        # https://github.com/python/cpython/issues/51571
        #
        # Reading in chunks instead of delegating to a single
        # BufferedReader.read() call also means we avoid its preallocation
        # of an unreasonably large memory block if a malicious server claims
        # it will send a huge literal without actually sending one.
        # https://github.com/python/cpython/issues/119511

        parts = []

        while size > 0:

            if len(parts) < len(self._readbuf):
                buf = self._readbuf[len(parts)]
            else:
                try:
                    buf = self.sock.recv(DEFAULT_BUFFER_SIZE)
                except ConnectionError:
                    break
                if not buf:
                    break
                self._readbuf.append(buf)

            if len(buf) >= size:
                parts.append(buf[:size])
                self._readbuf = [buf[size:]] + self._readbuf[len(parts):]
                break
            parts.append(buf)
            size -= len(buf)

        return b''.join(parts)


    def readline(self):
        """Read line from remote."""
        # The comment in read() explains why we implement our own readline().

        LF = b'\n'
        parts = []
        length = 0

        while length < _MAXLINE:

            if len(parts) < len(self._readbuf):
                buf = self._readbuf[len(parts)]
            else:
                try:
                    buf = self.sock.recv(DEFAULT_BUFFER_SIZE)
                except ConnectionError:
                    break
                if not buf:
                    break
                self._readbuf.append(buf)

            pos = buf.find(LF)
            if pos != -1:
                pos += 1
                parts.append(buf[:pos])
                self._readbuf = [buf[pos:]] + self._readbuf[len(parts):]
                break
            parts.append(buf)
            length += len(buf)

        line = b''.join(parts)
        if len(line) > _MAXLINE:
            raise self.error("got more than %d bytes" % _MAXLINE)
        return line


    def send(self, data):
        """Send data to remote."""
        sys.audit("imaplib.send", self, data)
        self.sock.sendall(data)


    def shutdown(self):
        """Close I/O established in "open"."""
        self._file.close()
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError as exc:
            # The server might already have closed the connection.
            # On Windows, this may result in WSAEINVAL (error 10022):
            # An invalid operation was attempted.
            if (exc.errno != errno.ENOTCONN
