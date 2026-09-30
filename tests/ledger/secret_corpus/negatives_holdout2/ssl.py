# Wrapper module for _ssl, providing some additional facilities
# implemented in Python.  Written by Bill Janssen.

"""This module provides some more Pythonic support for SSL.

Object types:

  SSLSocket -- subtype of socket.socket which does SSL over the socket

Exceptions:

  SSLError -- exception raised for I/O errors

Functions:

  cert_time_to_seconds -- convert time string used for certificate
                          notBefore and notAfter functions to integer
                          seconds past the Epoch (the time values
                          returned from time.time())

  get_server_certificate (addr, ssl_version, ca_certs, timeout) -- Retrieve the
                          certificate from the server at the specified
                          address and return it as a PEM-encoded string


Integer constants:

SSL_ERROR_ZERO_RETURN
SSL_ERROR_WANT_READ
SSL_ERROR_WANT_WRITE
SSL_ERROR_WANT_X509_LOOKUP
SSL_ERROR_SYSCALL
SSL_ERROR_SSL
SSL_ERROR_WANT_CONNECT

SSL_ERROR_EOF
SSL_ERROR_INVALID_ERROR_CODE

The following group define certificate requirements that one side is
allowing/requiring from the other side:

CERT_NONE - no certificates from the other side are required (or will
            be looked at if provided)
CERT_OPTIONAL - certificates are not required, but if provided will be
                validated, and if validation fails, the connection will
                also fail
CERT_REQUIRED - certificates are required, and will be validated, and
                if validation fails, the connection will also fail

The following constants identify various SSL protocol variants:

PROTOCOL_SSLv2
PROTOCOL_SSLv3
PROTOCOL_SSLv23
PROTOCOL_TLS
PROTOCOL_TLS_CLIENT
PROTOCOL_TLS_SERVER
PROTOCOL_TLSv1
PROTOCOL_TLSv1_1
PROTOCOL_TLSv1_2

The following constants identify various SSL alert message descriptions as per
http://www.iana.org/assignments/tls-parameters/tls-parameters.xml#tls-parameters-6

ALERT_DESCRIPTION_CLOSE_NOTIFY
ALERT_DESCRIPTION_UNEXPECTED_MESSAGE
ALERT_DESCRIPTION_BAD_RECORD_MAC
ALERT_DESCRIPTION_RECORD_OVERFLOW
ALERT_DESCRIPTION_DECOMPRESSION_FAILURE
ALERT_DESCRIPTION_HANDSHAKE_FAILURE
ALERT_DESCRIPTION_BAD_CERTIFICATE
ALERT_DESCRIPTION_UNSUPPORTED_CERTIFICATE
ALERT_DESCRIPTION_CERTIFICATE_REVOKED
ALERT_DESCRIPTION_CERTIFICATE_EXPIRED
ALERT_DESCRIPTION_CERTIFICATE_UNKNOWN
ALERT_DESCRIPTION_ILLEGAL_PARAMETER
ALERT_DESCRIPTION_UNKNOWN_CA
ALERT_DESCRIPTION_ACCESS_DENIED
ALERT_DESCRIPTION_DECODE_ERROR
ALERT_DESCRIPTION_DECRYPT_ERROR
ALERT_DESCRIPTION_PROTOCOL_VERSION
ALERT_DESCRIPTION_INSUFFICIENT_SECURITY
ALERT_DESCRIPTION_INTERNAL_ERROR
ALERT_DESCRIPTION_USER_CANCELLED
ALERT_DESCRIPTION_NO_RENEGOTIATION
ALERT_DESCRIPTION_UNSUPPORTED_EXTENSION
ALERT_DESCRIPTION_CERTIFICATE_UNOBTAINABLE
ALERT_DESCRIPTION_UNRECOGNIZED_NAME
ALERT_DESCRIPTION_BAD_CERTIFICATE_STATUS_RESPONSE
ALERT_DESCRIPTION_BAD_CERTIFICATE_HASH_VALUE
ALERT_DESCRIPTION_UNKNOWN_PSK_IDENTITY
"""

import sys
import os
from collections import namedtuple
from enum import Enum as _Enum, IntEnum as _IntEnum, IntFlag as _IntFlag
from enum import _simple_enum

import _ssl             # if we can't import it, let the error propagate

from _ssl import OPENSSL_VERSION_NUMBER, OPENSSL_VERSION_INFO, OPENSSL_VERSION
from _ssl import _SSLContext, MemoryBIO, SSLSession
from _ssl import (
    SSLError, SSLZeroReturnError, SSLWantReadError, SSLWantWriteError,
    SSLSyscallError, SSLEOFError, SSLCertVerificationError
    )
from _ssl import txt2obj as _txt2obj, nid2obj as _nid2obj
from _ssl import RAND_status, RAND_add, RAND_bytes
try:
    from _ssl import RAND_egd
except ImportError:
    # RAND_egd is not supported on some platforms
    pass


from _ssl import (
    HAS_SNI, HAS_ECDH, HAS_NPN, HAS_ALPN, HAS_SSLv2, HAS_SSLv3, HAS_TLSv1,
    HAS_TLSv1_1, HAS_TLSv1_2, HAS_TLSv1_3, HAS_PSK, HAS_PHA
)
from _ssl import _DEFAULT_CIPHERS, _OPENSSL_API_VERSION

_IntEnum._convert_(
    '_SSLMethod', __name__,
    lambda name: name.startswith('PROTOCOL_') and name != 'PROTOCOL_SSLv23',
    source=_ssl)

_IntFlag._convert_(
    'Options', __name__,
    lambda name: name.startswith('OP_'),
    source=_ssl)

_IntEnum._convert_(
    'AlertDescription', __name__,
    lambda name: name.startswith('ALERT_DESCRIPTION_'),
    source=_ssl)

_IntEnum._convert_(
    'SSLErrorNumber', __name__,
    lambda name: name.startswith('SSL_ERROR_'),
    source=_ssl)

_IntFlag._convert_(
    'VerifyFlags', __name__,
    lambda name: name.startswith('VERIFY_'),
    source=_ssl)

_IntEnum._convert_(
    'VerifyMode', __name__,
    lambda name: name.startswith('CERT_'),
    source=_ssl)

PROTOCOL_SSLv23 = _SSLMethod.PROTOCOL_SSLv23 = _SSLMethod.PROTOCOL_TLS
_PROTOCOL_NAMES = {value: name for name, value in _SSLMethod.__members__.items()}

_SSLv2_IF_EXISTS = getattr(_SSLMethod, 'PROTOCOL_SSLv2', None)


@_simple_enum(_IntEnum)
class TLSVersion:
    MINIMUM_SUPPORTED = _ssl.PROTO_MINIMUM_SUPPORTED
    SSLv3 = _ssl.PROTO_SSLv3
    TLSv1 = _ssl.PROTO_TLSv1
    TLSv1_1 = _ssl.PROTO_TLSv1_1
    TLSv1_2 = _ssl.PROTO_TLSv1_2
    TLSv1_3 = _ssl.PROTO_TLSv1_3
    MAXIMUM_SUPPORTED = _ssl.PROTO_MAXIMUM_SUPPORTED


@_simple_enum(_IntEnum)
class _TLSContentType:
    """Content types (record layer)

    See RFC 8446, section B.1
    """
    CHANGE_CIPHER_SPEC = 20
    ALERT = 21
    HANDSHAKE = 22
    APPLICATION_DATA = 23
    # pseudo content types
    HEADER = 0x100
    INNER_CONTENT_TYPE = 0x101


@_simple_enum(_IntEnum)
class _TLSAlertType:
    """Alert types for TLSContentType.ALERT messages

    See RFC 8446, section B.2
    """
    CLOSE_NOTIFY = 0
    UNEXPECTED_MESSAGE = 10
    BAD_RECORD_MAC = 20
    DECRYPTION_FAILED = 21
    RECORD_OVERFLOW = 22
    DECOMPRESSION_FAILURE = 30
    HANDSHAKE_FAILURE = 40
    NO_CERTIFICATE = 41
    BAD_CERTIFICATE = 42
    UNSUPPORTED_CERTIFICATE = 43
    CERTIFICATE_REVOKED = 44
    CERTIFICATE_EXPIRED = 45
    CERTIFICATE_UNKNOWN = 46
    ILLEGAL_PARAMETER = 47
    UNKNOWN_CA = 48
    ACCESS_DENIED = 49
    DECODE_ERROR = 50
    DECRYPT_ERROR = 51
    EXPORT_RESTRICTION = 60
    PROTOCOL_VERSION = 70
    INSUFFICIENT_SECURITY = 71
    INTERNAL_ERROR = 80
    INAPPROPRIATE_FALLBACK = 86
    USER_CANCELED = 90
    NO_RENEGOTIATION = 100
    MISSING_EXTENSION = 109
    UNSUPPORTED_EXTENSION = 110
    CERTIFICATE_UNOBTAINABLE = 111
    UNRECOGNIZED_NAME = 112
    BAD_CERTIFICATE_STATUS_RESPONSE = 113
    BAD_CERTIFICATE_HASH_VALUE = 114
    UNKNOWN_PSK_IDENTITY = 115
    CERTIFICATE_REQUIRED = 116
    NO_APPLICATION_PROTOCOL = 120


@_simple_enum(_IntEnum)
class _TLSMessageType:
    """Message types (handshake protocol)

    See RFC 8446, section B.3
    """
    HELLO_REQUEST = 0
    CLIENT_HELLO = 1
    SERVER_HELLO = 2
    HELLO_VERIFY_REQUEST = 3
    NEWSESSION_TICKET = 4
    END_OF_EARLY_DATA = 5
    HELLO_RETRY_REQUEST = 6
    ENCRYPTED_EXTENSIONS = 8
    CERTIFICATE = 11
    SERVER_KEY_EXCHANGE = 12
    CERTIFICATE_REQUEST = 13
    SERVER_DONE = 14
    CERTIFICATE_VERIFY = 15
    CLIENT_KEY_EXCHANGE = 16
    FINISHED = 20
    CERTIFICATE_URL = 21
    CERTIFICATE_STATUS = 22
    SUPPLEMENTAL_DATA = 23
    KEY_UPDATE = 24
    NEXT_PROTO = 67
    MESSAGE_HASH = 254
    CHANGE_CIPHER_SPEC = 0x0101


if sys.platform == "win32":
    from _ssl import enum_certificates, enum_crls

from socket import socket, SOCK_STREAM, create_connection
from socket import SOL_SOCKET, SO_TYPE, _GLOBAL_DEFAULT_TIMEOUT
import socket as _socket
import base64        # for DER-to-PEM translation
import errno
import warnings


socket_error = OSError  # keep that public name in module namespace

CHANNEL_BINDING_TYPES = ['tls-unique']

HAS_NEVER_CHECK_COMMON_NAME = hasattr(_ssl, 'HOSTFLAG_NEVER_CHECK_SUBJECT')


_RESTRICTED_SERVER_CIPHERS = _DEFAULT_CIPHERS

CertificateError = SSLCertVerificationError


def _dnsname_match(dn, hostname):
    """Matching according to RFC 6125, section 6.4.3

    - Hostnames are compared lower-case.
    - For IDNA, both dn and hostname must be encoded as IDN A-label (ACE).
    - Partial wildcards like 'www*.example.org', multiple wildcards, sole
      wildcard or wildcards in labels other then the left-most label are not
      supported and a CertificateError is raised.
    - A wildcard must match at least one character.
    """
    if not dn:
        return False

    wildcards = dn.count('*')
    # speed up common case w/o wildcards
    if not wildcards:
        return dn.lower() == hostname.lower()

    if wildcards > 1:
        raise CertificateError(
            "too many wildcards in certificate DNS name: {!r}.".format(dn))

    dn_leftmost, sep, dn_remainder = dn.partition('.')

    if '*' in dn_remainder:
        # Only match wildcard in leftmost segment.
        raise CertificateError(
            "wildcard can only be present in the leftmost label: "
            "{!r}.".format(dn))

    if not sep:
        # no right side
        raise CertificateError(
            "sole wildcard without additional labels are not support: "
            "{!r}.".format(dn))

    if dn_leftmost != '*':
        # no partial wildcard matching
        raise CertificateError(
            "partial wildcards in leftmost label are not supported: "
            "{!r}.".format(dn))

    hostname_leftmost, sep, hostname_remainder = hostname.partition('.')
    if not hostname_leftmost or not sep:
        # wildcard must match at least one char
        return False
    return dn_remainder.lower() == hostname_remainder.lower()


def _inet_paton(ipname):
    """Try to convert an IP address to packed binary form

    Supports IPv4 addresses on all platforms and IPv6 on platforms with IPv6
    support.
    """
    # inet_aton() also accepts strings like '1', '127.1', some also trailing
    # data like '127.0.0.1 whatever'.
    try:
        addr = _socket.inet_aton(ipname)
    except OSError:
        # not an IPv4 address
        pass
    else:
        if _socket.inet_ntoa(addr) == ipname:
            # only accept injective ipnames
            return addr
        else:
            # refuse for short IPv4 notation and additional trailing data
            raise ValueError(
                "{!r} is not a quad-dotted IPv4 address.".format(ipname)
            )

    try:
        return _socket.inet_pton(_socket.AF_INET6, ipname)
    except OSError:
        raise ValueError("{!r} is neither an IPv4 nor an IP6 "
                         "address.".format(ipname))
    except AttributeError:
        # AF_INET6 not available
        pass

    raise ValueError("{!r} is not an IPv4 address.".format(ipname))


def _ipaddress_match(cert_ipaddress, host_ip):
    """Exact matching of IP addresses.

    RFC 6125 explicitly doesn't define an algorithm for this
    (section 1.7.2 - "Out of Scope").
    """
    # OpenSSL may add a trailing newline to a subjectAltName's IP address,
    # commonly with IPv6 addresses. Strip off trailing \n.
    ip = _inet_paton(cert_ipaddress.rstrip())
    return ip == host_ip


DefaultVerifyPaths = namedtuple("DefaultVerifyPaths",
    "cafile capath openssl_cafile_env openssl_cafile openssl_capath_env "
    "openssl_capath")

def get_default_verify_paths():
    """Return paths to default cafile and capath.
    """
    parts = _ssl.get_default_verify_paths()

    # environment vars shadow paths
    cafile = os.environ.get(parts[0], parts[1])
    capath = os.environ.get(parts[2], parts[3])

    return DefaultVerifyPaths(cafile if os.path.isfile(cafile) else None,
                              capath if os.path.isdir(capath) else None,
                              *parts)


class _ASN1Object(namedtuple("_ASN1Object", "nid shortname longname oid")):
    """ASN.1 object identifier lookup
    """
    __slots__ = ()

    def __new__(cls, oid):
        return super().__new__(cls, *_txt2obj(oid, name=False))

    @classmethod
    def fromnid(cls, nid):
        """Create _ASN1Object from OpenSSL numeric ID
        """
        return super().__new__(cls, *_nid2obj(nid))

    @classmethod
    def fromname(cls, name):
        """Create _ASN1Object from short name, long name or OID
        """
        return super().__new__(cls, *_txt2obj(name, name=True))


class Purpose(_ASN1Object, _Enum):
    """SSLContext purpose flags with X509v3 Extended Key Usage objects
    """
    SERVER_AUTH = '1.3.6.1.5.5.7.3.1'
    CLIENT_AUTH = '1.3.6.1.5.5.7.3.2'


class SSLContext(_SSLContext):
    """An SSLContext holds various SSL-related configuration options and
    data, such as certificates and possibly a private key."""
    _windows_cert_stores = ("CA", "ROOT")

    sslsocket_class = None  # SSLSocket is assigned later.
    sslobject_class = None  # SSLObject is assigned later.

    def __new__(cls, protocol=None, *args, **kwargs):
        if protocol is None:
            warnings.warn(
                "ssl.SSLContext() without protocol argument is deprecated.",
                category=DeprecationWarning,
                stacklevel=2
            )
            protocol = PROTOCOL_TLS
        self = _SSLContext.__new__(cls, protocol)
        return self

    def _encode_hostname(self, hostname):
        if hostname is None:
            return None
        elif isinstance(hostname, str):
            return hostname.encode('idna').decode('ascii')
        else:
            return hostname.decode('ascii')

    def wrap_socket(self, sock, server_side=False,
                    do_handshake_on_connect=True,
                    suppress_ragged_eofs=True,
                    server_hostname=None, session=None):
        # SSLSocket class handles server_hostname encoding before it calls
        # ctx._wrap_socket()
        return self.sslsocket_class._create(
            sock=sock,
            server_side=server_side,
            do_handshake_on_connect=do_handshake_on_connect,
            suppress_ragged_eofs=suppress_ragged_eofs,
            server_hostname=server_hostname,
            context=self,
            session=session
        )

    def wrap_bio(self, incoming, outgoing, server_side=False,
                 server_hostname=None, session=None):
        # Need to encode server_hostname here because _wrap_bio() can only
        # handle ASCII str.
        return self.sslobject_class._create(
            incoming, outgoing, server_side=server_side,
            server_hostname=self._encode_hostname(server_hostname),
            session=session, context=self,
        )

    def set_npn_protocols(self, npn_protocols):
        warnings.warn(
            "ssl NPN is deprecated, use ALPN instead",
            DeprecationWarning,
            stacklevel=2
        )
        protos = bytearray()
        for protocol in npn_protocols:
            b = bytes(protocol, 'ascii')
            if len(b) == 0 or len(b) > 255:
                raise SSLError('NPN protocols must be 1 to 255 in length')
            protos.append(len(b))
            protos.extend(b)

        self._set_npn_protocols(protos)

    def set_servername_callback(self, server_name_callback):
        if server_name_callback is None:
            self.sni_callback = None
        else:
            if not callable(server_name_callback):
                raise TypeError("not a callable object")

            def shim_cb(sslobj, servername, sslctx):
                servername = self._encode_hostname(servername)
                return server_name_callback(sslobj, servername, sslctx)

            self.sni_callback = shim_cb

    def set_alpn_protocols(self, alpn_protocols):
        protos = bytearray()
        for protocol in alpn_protocols:
            b = bytes(protocol, 'ascii')
            if len(b) == 0 or len(b) > 255:
                raise SSLError('ALPN protocols must be 1 to 255 in length')
            protos.append(len(b))
            protos.extend(b)

        self._set_alpn_protocols(protos)

    def _load_windows_store_certs(self, storename, purpose):
        try:
            for cert, encoding, trust in enum_certificates(storename):
                # CA certs are never PKCS#7 encoded
                if encoding == "x509_asn":
                    if trust is True or purpose.oid in trust:
                        try:
                            self.load_verify_locations(cadata=cert)
                        except SSLError as exc:
                            warnings.warn(f"Bad certificate in Windows certificate store: {exc!s}")
        except PermissionError:
