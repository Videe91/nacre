# Copyright (C) 2001 Python Software Foundation
# Author: Barry Warsaw
# Contact: email-sig@python.org

"""Basic message object for the email package object model."""

__all__ = ['Message', 'EmailMessage']

import binascii
import re
import quopri
from io import BytesIO, StringIO

# Intrapackage imports
from email import utils
from email import errors
from email._policybase import compat32
from email import charset as _charset
from email._encoded_words import decode_b
Charset = _charset.Charset

SEMISPACE = '; '

# Regular expression that matches 'special' characters in parameters, the
# existence of which force quoting of the parameter value.
tspecials = re.compile(r'[ \(\)<>@,;:\\"/\[\]\?=]')


def _splitparam(param):
    # Split header parameters.  BAW: this may be too simple.  It isn't
    # strictly RFC 2045 (section 5.1) compliant, but it catches most headers
    # found in the wild.  We may eventually need a full fledged parser.
    # RDM: we might have a Header here; for now just stringify it.
    a, sep, b = str(param).partition(';')
    if not sep:
        return a.strip(), None
    return a.strip(), b.strip()

def _formatparam(param, value=None, quote=True):
    """Convenience function to format and return a key=value pair.

    This will quote the value if needed or if quote is true.  If value is a
    three tuple (charset, language, value), it will be encoded according
    to RFC2231 rules.  If it contains non-ascii characters it will likewise
    be encoded according to RFC2231 rules, using the utf-8 charset and
    a null language.
    """
    if value is not None and len(value) > 0:
        # A tuple is used for RFC 2231 encoded parameter values where items
        # are (charset, language, value).  charset is a string, not a Charset
        # instance.  RFC 2231 encoded values are never quoted, per RFC.
        if isinstance(value, tuple):
            # Encode as per RFC 2231
            param += '*'
            value = utils.encode_rfc2231(value[2], value[0], value[1])
            return '%s=%s' % (param, value)
        else:
            try:
                value.encode('ascii')
            except UnicodeEncodeError:
                param += '*'
                value = utils.encode_rfc2231(value, 'utf-8', '')
                return '%s=%s' % (param, value)
        # BAW: Please check this.  I think that if quote is set it should
        # force quoting even if not necessary.
        if quote or tspecials.search(value):
            return '%s="%s"' % (param, utils.quote(value))
        else:
            return '%s=%s' % (param, value)
    else:
        return param

def _parseparam(s):
    # RDM This might be a Header, so for now stringify it.
    s = ';' + str(s)
    plist = []
    start = 0
    while s.find(';', start) == start:
        start += 1
        end = s.find(';', start)
        ind, diff = start, 0
        while end > 0:
            diff += s.count('"', ind, end) - s.count('\\"', ind, end)
            if diff % 2 == 0:
                break
            end, ind = ind, s.find(';', end + 1)
        if end < 0:
            end = len(s)
        i = s.find('=', start, end)
        if i == -1:
            f = s[start:end]
        else:
            f = s[start:i].rstrip().lower() + '=' + s[i+1:end].lstrip()
        plist.append(f.strip())
        start = end
    return plist


def _unquotevalue(value):
    # This is different than utils.collapse_rfc2231_value() because it doesn't
    # try to convert the value to a unicode.  Message.get_param() and
    # Message.get_params() are both currently defined to return the tuple in
    # the face of RFC 2231 parameters.
    if isinstance(value, tuple):
        return value[0], value[1], utils.unquote(value[2])
    else:
        return utils.unquote(value)


def _decode_uu(encoded):
    """Decode uuencoded data."""
    decoded_lines = []
    encoded_lines_iter = iter(encoded.splitlines())
    for line in encoded_lines_iter:
        if line.startswith(b"begin "):
            mode, _, path = line.removeprefix(b"begin ").partition(b" ")
            try:
                int(mode, base=8)
            except ValueError:
                continue
            else:
                break
    else:
        raise ValueError("`begin` line not found")
    for line in encoded_lines_iter:
        if not line:
            raise ValueError("Truncated input")
        elif line.strip(b' \t\r\n\f') == b'end':
            break
        try:
            decoded_line = binascii.a2b_uu(line)
        except binascii.Error:
            # Workaround for broken uuencoders by /Fredrik Lundh
            nbytes = (((line[0]-32) & 63) * 4 + 5) // 3
            decoded_line = binascii.a2b_uu(line[:nbytes])
        decoded_lines.append(decoded_line)

    return b''.join(decoded_lines)


class Message:
    """Basic message object.

    A message object is defined as something that has a bunch of RFC 5322
    headers and a payload.  It may optionally have an envelope header
    (a.k.a. Unix-From or From_ header).  If the message is a container (i.e. a
    multipart or a message/rfc822), then the payload is a list of Message
    objects, otherwise it is a string.

    Message objects implement part of the 'mapping' interface, which assumes
    there is exactly one occurrence of the header per message.  Some headers
    do in fact appear multiple times (e.g. Received) and for those headers,
    you must use the explicit API to set or get all the headers.  Not all of
    the mapping methods are implemented.
    """
    def __init__(self, policy=compat32):
        self.policy = policy
        self._headers = []
        self._unixfrom = None
        self._payload = None
        self._charset = None
        # Defaults for multipart messages
        self.preamble = self.epilogue = None
        self.defects = []
        # Default content type
        self._default_type = 'text/plain'

    def __str__(self):
        """Return the entire formatted message as a string.
        """
        return self.as_string()

    def as_string(self, unixfrom=False, maxheaderlen=0, policy=None):
        """Return the entire formatted message as a string.

        Optional 'unixfrom', when true, means include the Unix From_ envelope
        header.  For backward compatibility reasons, if maxheaderlen is
        not specified it defaults to 0, so you must override it explicitly
        if you want a different maxheaderlen.  'policy' is passed to the
        Generator instance used to serialize the message; if it is not
        specified the policy associated with the message instance is used.

        If the message object contains binary data that is not encoded
        according to RFC standards, the non-compliant data will be replaced by
        unicode "unknown character" code points.
        """
        from email.generator import Generator
        policy = self.policy if policy is None else policy
        fp = StringIO()
        g = Generator(fp,
                      mangle_from_=False,
                      maxheaderlen=maxheaderlen,
                      policy=policy)
        g.flatten(self, unixfrom=unixfrom)
        return fp.getvalue()

    def __bytes__(self):
        """Return the entire formatted message as a bytes object.
        """
        return self.as_bytes()

    def as_bytes(self, unixfrom=False, policy=None):
        """Return the entire formatted message as a bytes object.

        Optional 'unixfrom', when true, means include the Unix From_ envelope
        header.  'policy' is passed to the BytesGenerator instance used to
        serialize the message; if not specified the policy associated with
        the message instance is used.
        """
        from email.generator import BytesGenerator
        policy = self.policy if policy is None else policy
        fp = BytesIO()
        g = BytesGenerator(fp, mangle_from_=False, policy=policy)
        g.flatten(self, unixfrom=unixfrom)
        return fp.getvalue()

    def is_multipart(self):
        """Return True if the message consists of multiple parts."""
        return isinstance(self._payload, list)

    #
    # Unix From_ line
    #
    def set_unixfrom(self, unixfrom):
        self._unixfrom = unixfrom

    def get_unixfrom(self):
        return self._unixfrom

    #
    # Payload manipulation.
    #
    def attach(self, payload):
        """Add the given payload to the current payload.

        The current payload will always be a list of objects after this method
        is called.  If you want to set the payload to a scalar object, use
        set_payload() instead.
        """
        if self._payload is None:
            self._payload = [payload]
        else:
            try:
                self._payload.append(payload)
            except AttributeError:
                raise TypeError("Attach is not valid on a message with a"
                                " non-multipart payload")

    def get_payload(self, i=None, decode=False):
        """Return a reference to the payload.

        The payload will either be a list object or a string.  If you mutate
        the list object, you modify the message's payload in place.  Optional
        i returns that index into the payload.

        Optional decode is a flag indicating whether the payload should be
        decoded or not, according to the Content-Transfer-Encoding header
        (default is False).

        When True and the message is not a multipart, the payload will be
        decoded if this header's value is `quoted-printable' or `base64'.  If
        some other encoding is used, or the header is missing, or if the
        payload has bogus data (i.e. bogus base64 or uuencoded data), the
        payload is returned as-is.

        If the message is a multipart and the decode flag is True, then None
        is returned.
        """
        # Here is the logic table for this code, based on the email5.0.0 code:
        #   i     decode  is_multipart  result
        # ------  ------  ------------  ------------------------------
        #  None   True    True          None
        #   i     True    True          None
        #  None   False   True          _payload (a list)
        #   i     False   True          _payload element i (a Message)
        #   i     False   False         error (not a list)
        #   i     True    False         error (not a list)
        #  None   False   False         _payload
        #  None   True    False         _payload decoded (bytes)
        # Note that Barry planned to factor out the 'decode' case, but that
        # isn't so easy now that we handle the 8 bit data, which needs to be
        # converted in both the decode and non-decode path.
        if self.is_multipart():
            if decode:
                return None
            if i is None:
                return self._payload
            else:
                return self._payload[i]
        # For backward compatibility, Use isinstance and this error message
        # instead of the more logical is_multipart test.
        if i is not None and not isinstance(self._payload, list):
            raise TypeError('Expected list, got %s' % type(self._payload))
        payload = self._payload
        cte = self.get('content-transfer-encoding', '')
        if hasattr(cte, 'cte'):
            cte = cte.cte
        else:
            # cte might be a Header, so for now stringify it.
            cte = str(cte).strip().lower()
        # payload may be bytes here.
        if not decode:
            if isinstance(payload, str) and utils._has_surrogates(payload):
                try:
                    bpayload = payload.encode('ascii', 'surrogateescape')
                    try:
                        payload = bpayload.decode(self.get_content_charset('ascii'), 'replace')
                    except LookupError:
                        payload = bpayload.decode('ascii', 'replace')
                except UnicodeEncodeError:
                    pass
            return payload
        if isinstance(payload, str):
            try:
                bpayload = payload.encode('ascii', 'surrogateescape')
            except UnicodeEncodeError:
                # This won't happen for RFC compliant messages (messages
                # containing only ASCII code points in the unicode input).
                # If it does happen, turn the string into bytes in a way
                # guaranteed not to fail.
                bpayload = payload.encode('raw-unicode-escape')
        else:
            bpayload = payload
        if cte == 'quoted-printable':
            return quopri.decodestring(bpayload)
        elif cte == 'base64':
            # XXX: this is a bit of a hack; decode_b should probably be factored
            # out somewhere, but I haven't figured out where yet.
            value, defects = decode_b(b''.join(bpayload.splitlines()))
            for defect in defects:
                self.policy.handle_defect(self, defect)
            return value
        elif cte in ('x-uuencode', 'uuencode', 'uue', 'x-uue'):
            try:
                return _decode_uu(bpayload)
            except ValueError:
                # Some decoding problem.
                return bpayload
        if isinstance(payload, str):
            return bpayload
        return payload

    def set_payload(self, payload, charset=None):
        """Set the payload to the given value.

        Optional charset sets the message's default character set.  See
        set_charset() for details.
        """
        if hasattr(payload, 'encode'):
            if charset is None:
                self._payload = payload
                return
            if not isinstance(charset, Charset):
                charset = Charset(charset)
            payload = payload.encode(charset.output_charset, 'surrogateescape')
        if hasattr(payload, 'decode'):
            self._payload = payload.decode('ascii', 'surrogateescape')
        else:
            self._payload = payload
        if charset is not None:
            self.set_charset(charset)

    def set_charset(self, charset):
        """Set the charset of the payload to a given character set.

        charset can be a Charset instance, a string naming a character set, or
        None.  If it is a string it will be converted to a Charset instance.
        If charset is None, the charset parameter will be removed from the
        Content-Type field.  Anything else will generate a TypeError.

        The message will be assumed to be of type text/* encoded with
        charset.input_charset.  It will be converted to charset.output_charset
        and encoded properly, if needed, when generating the plain text
        representation of the message.  MIME headers (MIME-Version,
        Content-Type, Content-Transfer-Encoding) will be added as needed.
        """
        if charset is None:
            self.del_param('charset')
            self._charset = None
            return
        if not isinstance(charset, Charset):
            charset = Charset(charset)
        self._charset = charset
        if 'MIME-Version' not in self:
            self.add_header('MIME-Version', '1.0')
        if 'Content-Type' not in self:
            self.add_header('Content-Type', 'text/plain',
                            charset=charset.get_output_charset())
        else:
            self.set_param('charset', charset.get_output_charset())
        if charset != charset.get_output_charset():
            self._payload = charset.body_encode(self._payload)
        if 'Content-Transfer-Encoding' not in self:
            cte = charset.get_body_encoding()
            try:
                cte(self)
            except TypeError:
                # This 'if' is for backward compatibility, it allows unicode
                # through even though that won't work correctly if the
                # message is serialized.
                payload = self._payload
                if payload:
                    try:
                        payload = payload.encode('ascii', 'surrogateescape')
                    except UnicodeError:
                        payload = payload.encode(charset.output_charset)
                self._payload = charset.body_encode(payload)
                self.add_header('Content-Transfer-Encoding', cte)

    def get_charset(self):
