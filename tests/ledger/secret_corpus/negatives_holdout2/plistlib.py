r"""plistlib.py -- a tool to generate and parse MacOSX .plist files.

The property list (.plist) file format is a simple XML pickle supporting
basic object types, like dictionaries, lists, numbers and strings.
Usually the top level object is a dictionary.

To write out a plist file, use the dump(value, file)
function. 'value' is the top level object, 'file' is
a (writable) file object.

To parse a plist from a file, use the load(file) function,
with a (readable) file object as the only argument. It
returns the top level object (again, usually a dictionary).

To work with plist data in bytes objects, you can use loads()
and dumps().

Values can be strings, integers, floats, booleans, tuples, lists,
dictionaries (but only with string keys), Data, bytes, bytearray, or
datetime.datetime objects.

Generate Plist example:

    import datetime
    import plistlib

    pl = dict(
        aString = "Doodah",
        aList = ["A", "B", 12, 32.1, [1, 2, 3]],
        aFloat = 0.1,
        anInt = 728,
        aDict = dict(
            anotherString = "<hello & hi there!>",
            aThirdString = "M\xe4ssig, Ma\xdf",
            aTrueValue = True,
            aFalseValue = False,
        ),
        someData = b"<binary gunk>",
        someMoreData = b"<lots of binary gunk>" * 10,
        aDate = datetime.datetime.now()
    )
    print(plistlib.dumps(pl).decode())

Parse Plist example:

    import plistlib

    plist = b'''<plist version="1.0">
    <dict>
        <key>foo</key>
        <string>bar</string>
    </dict>
    </plist>'''
    pl = plistlib.loads(plist)
    print(pl["foo"])
"""
__all__ = [
    "InvalidFileException", "FMT_XML", "FMT_BINARY", "load", "dump", "loads", "dumps", "UID"
]

import binascii
import codecs
import datetime
import enum
from io import BytesIO
import itertools
import os
import re
import struct
from xml.parsers.expat import ParserCreate


PlistFormat = enum.Enum('PlistFormat', 'FMT_XML FMT_BINARY', module=__name__)
globals().update(PlistFormat.__members__)

# Data larger than this will be read in chunks, to prevent extreme
# overallocation.
_MIN_READ_BUF_SIZE = 1 << 20

class UID:
    def __init__(self, data):
        if not isinstance(data, int):
            raise TypeError("data must be an int")
        if data >= 1 << 64:
            raise ValueError("UIDs cannot be >= 2**64")
        if data < 0:
            raise ValueError("UIDs must be positive")
        self.data = data

    def __index__(self):
        return self.data

    def __repr__(self):
        return "%s(%s)" % (self.__class__.__name__, repr(self.data))

    def __reduce__(self):
        return self.__class__, (self.data,)

    def __eq__(self, other):
        if not isinstance(other, UID):
            return NotImplemented
        return self.data == other.data

    def __hash__(self):
        return hash(self.data)

#
# XML support
#


# XML 'header'
PLISTHEADER = b"""\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
"""


# Regex to find any control chars, except for \t \n and \r
_controlCharPat = re.compile(
    r"[\x00\x01\x02\x03\x04\x05\x06\x07\x08\x0b\x0c\x0e\x0f"
    r"\x10\x11\x12\x13\x14\x15\x16\x17\x18\x19\x1a\x1b\x1c\x1d\x1e\x1f]")

def _encode_base64(s, maxlinelength=76):
    # copied from base64.encodebytes(), with added maxlinelength argument
    maxbinsize = (maxlinelength//4)*3
    pieces = []
    for i in range(0, len(s), maxbinsize):
        chunk = s[i : i + maxbinsize]
        pieces.append(binascii.b2a_base64(chunk))
    return b''.join(pieces)

def _decode_base64(s):
    if isinstance(s, str):
        return binascii.a2b_base64(s.encode("utf-8"))

    else:
        return binascii.a2b_base64(s)

# Contents should conform to a subset of ISO 8601
# (in particular, YYYY '-' MM '-' DD 'T' HH ':' MM ':' SS 'Z'.  Smaller units
# may be omitted with #  a loss of precision)
_dateParser = re.compile(r"(?P<year>\d\d\d\d)(?:-(?P<month>\d\d)(?:-(?P<day>\d\d)(?:T(?P<hour>\d\d)(?::(?P<minute>\d\d)(?::(?P<second>\d\d))?)?)?)?)?Z", re.ASCII)


def _date_from_string(s, aware_datetime):
    order = ('year', 'month', 'day', 'hour', 'minute', 'second')
    gd = _dateParser.match(s).groupdict()
    lst = []
    for key in order:
        val = gd[key]
        if val is None:
            break
        lst.append(int(val))
    if aware_datetime:
        return datetime.datetime(*lst, tzinfo=datetime.UTC)
    return datetime.datetime(*lst)


def _date_to_string(d, aware_datetime):
    if aware_datetime:
        d = d.astimezone(datetime.UTC)
    return '%04d-%02d-%02dT%02d:%02d:%02dZ' % (
        d.year, d.month, d.day,
        d.hour, d.minute, d.second
    )

def _escape(text):
    m = _controlCharPat.search(text)
    if m is not None:
        raise ValueError("strings can't contain control characters; "
                         "use bytes instead")
    text = text.replace("\r\n", "\n")       # convert DOS line endings
    text = text.replace("\r", "\n")         # convert Mac line endings
    text = text.replace("&", "&amp;")       # escape '&'
    text = text.replace("<", "&lt;")        # escape '<'
    text = text.replace(">", "&gt;")        # escape '>'
    return text

class _PlistParser:
    def __init__(self, dict_type, aware_datetime=False):
        self.stack = []
        self.current_key = None
        self.root = None
        self._dict_type = dict_type
        self._aware_datetime = aware_datetime

    def parse(self, fileobj):
        self.parser = ParserCreate()
        self.parser.StartElementHandler = self.handle_begin_element
        self.parser.EndElementHandler = self.handle_end_element
        self.parser.CharacterDataHandler = self.handle_data
        self.parser.EntityDeclHandler = self.handle_entity_decl
        self.parser.ParseFile(fileobj)
        return self.root

    def handle_entity_decl(self, entity_name, is_parameter_entity, value, base, system_id, public_id, notation_name):
        # Reject plist files with entity declarations to avoid XML vulnerabilities in expat.
        # Regular plist files don't contain those declarations, and Apple's plutil tool does not
        # accept them either.
        raise InvalidFileException("XML entity declarations are not supported in plist files")

    def handle_begin_element(self, element, attrs):
        self.data = []
        handler = getattr(self, "begin_" + element, None)
        if handler is not None:
            handler(attrs)

    def handle_end_element(self, element):
        handler = getattr(self, "end_" + element, None)
        if handler is not None:
            handler()

    def handle_data(self, data):
        self.data.append(data)

    def add_object(self, value):
        if self.current_key is not None:
            if not isinstance(self.stack[-1], dict):
                raise ValueError("unexpected element at line %d" %
                                 self.parser.CurrentLineNumber)
            self.stack[-1][self.current_key] = value
            self.current_key = None
        elif not self.stack:
            # this is the root object
            self.root = value
        else:
            if not isinstance(self.stack[-1], list):
                raise ValueError("unexpected element at line %d" %
                                 self.parser.CurrentLineNumber)
            self.stack[-1].append(value)

    def get_data(self):
        data = ''.join(self.data)
        self.data = []
        return data

    # element handlers

    def begin_dict(self, attrs):
        d = self._dict_type()
        self.add_object(d)
        self.stack.append(d)

    def end_dict(self):
        if self.current_key:
            raise ValueError("missing value for key '%s' at line %d" %
                             (self.current_key,self.parser.CurrentLineNumber))
        self.stack.pop()

    def end_key(self):
        if self.current_key or not isinstance(self.stack[-1], dict):
            raise ValueError("unexpected key at line %d" %
                             self.parser.CurrentLineNumber)
        self.current_key = self.get_data()

    def begin_array(self, attrs):
        a = []
        self.add_object(a)
        self.stack.append(a)

    def end_array(self):
        self.stack.pop()

    def end_true(self):
        self.add_object(True)

    def end_false(self):
        self.add_object(False)

    def end_integer(self):
        raw = self.get_data()
        if raw.startswith('0x') or raw.startswith('0X'):
            self.add_object(int(raw, 16))
        else:
            self.add_object(int(raw))

    def end_real(self):
        self.add_object(float(self.get_data()))

    def end_string(self):
        self.add_object(self.get_data())

    def end_data(self):
        self.add_object(_decode_base64(self.get_data()))

    def end_date(self):
        self.add_object(_date_from_string(self.get_data(),
                                          aware_datetime=self._aware_datetime))


class _DumbXMLWriter:
    def __init__(self, file, indent_level=0, indent="\t"):
        self.file = file
        self.stack = []
        self._indent_level = indent_level
        self.indent = indent

    def begin_element(self, element):
        self.stack.append(element)
        self.writeln("<%s>" % element)
        self._indent_level += 1

    def end_element(self, element):
        assert self._indent_level > 0
        assert self.stack.pop() == element
        self._indent_level -= 1
        self.writeln("</%s>" % element)

    def simple_element(self, element, value=None):
        if value is not None:
            value = _escape(value)
            self.writeln("<%s>%s</%s>" % (element, value, element))

        else:
            self.writeln("<%s/>" % element)

    def writeln(self, line):
        if line:
            # plist has fixed encoding of utf-8

            # XXX: is this test needed?
            if isinstance(line, str):
                line = line.encode('utf-8')
            self.file.write(self._indent_level * self.indent)
            self.file.write(line)
        self.file.write(b'\n')


class _PlistWriter(_DumbXMLWriter):
    def __init__(
            self, file, indent_level=0, indent=b"\t", writeHeader=1,
            sort_keys=True, skipkeys=False, aware_datetime=False):

        if writeHeader:
            file.write(PLISTHEADER)
        _DumbXMLWriter.__init__(self, file, indent_level, indent)
        self._sort_keys = sort_keys
        self._skipkeys = skipkeys
        self._aware_datetime = aware_datetime

    def write(self, value):
        self.writeln("<plist version=\"1.0\">")
        self.write_value(value)
        self.writeln("</plist>")

    def write_value(self, value):
        if isinstance(value, str):
            self.simple_element("string", value)

        elif value is True:
            self.simple_element("true")

        elif value is False:
            self.simple_element("false")

        elif isinstance(value, int):
            if -1 << 63 <= value < 1 << 64:
                self.simple_element("integer", "%d" % value)
            else:
                raise OverflowError(value)

        elif isinstance(value, float):
            self.simple_element("real", repr(value))

        elif isinstance(value, dict):
            self.write_dict(value)

        elif isinstance(value, (bytes, bytearray)):
            self.write_bytes(value)

        elif isinstance(value, datetime.datetime):
            self.simple_element("date",
                                _date_to_string(value, self._aware_datetime))

        elif isinstance(value, (tuple, list)):
            self.write_array(value)

        else:
            raise TypeError("unsupported type: %s" % type(value))

    def write_bytes(self, data):
        self.begin_element("data")
        self._indent_level -= 1
        maxlinelength = max(
            16,
            76 - len((self.indent * self._indent_level).expandtabs()))

        for line in _encode_base64(data, maxlinelength).split(b"\n"):
            if line:
                self.writeln(line)
        self._indent_level += 1
        self.end_element("data")

    def write_dict(self, d):
        if d:
            self.begin_element("dict")
            if self._sort_keys:
                items = sorted(d.items())
            else:
                items = d.items()

            for key, value in items:
                if not isinstance(key, str):
                    if self._skipkeys:
                        continue
                    raise TypeError("keys must be strings")
                self.simple_element("key", key)
                self.write_value(value)
            self.end_element("dict")

        else:
            self.simple_element("dict")

    def write_array(self, array):
        if array:
            self.begin_element("array")
            for value in array:
                self.write_value(value)
            self.end_element("array")

        else:
            self.simple_element("array")


def _is_fmt_xml(header):
    prefixes = (b'<?xml', b'<plist')

    for pfx in prefixes:
        if header.startswith(pfx):
            return True

    # Also check for alternative XML encodings, this is slightly
    # overkill because the Apple tools (and plistlib) will not
    # generate files with these encodings.
    for bom, encoding in (
                (codecs.BOM_UTF8, "utf-8"),
                (codecs.BOM_UTF16_BE, "utf-16-be"),
                (codecs.BOM_UTF16_LE, "utf-16-le"),
                # expat does not support utf-32
                #(codecs.BOM_UTF32_BE, "utf-32-be"),
                #(codecs.BOM_UTF32_LE, "utf-32-le"),
            ):
        if not header.startswith(bom):
            continue

        for start in prefixes:
            prefix = bom + start.decode('ascii').encode(encoding)
            if header[:len(prefix)] == prefix:
                return True

    return False

#
# Binary Plist
#


class InvalidFileException (ValueError):
    def __init__(self, message="Invalid file"):
        ValueError.__init__(self, message)

_BINARY_FORMAT = {1: 'B', 2: 'H', 4: 'L', 8: 'Q'}

_undefined = object()

class _BinaryPlistParser:
    """
    Read or write a binary plist file, following the description of the binary
    format.  Raise InvalidFileException in case of error, otherwise return the
    root object.

    see also: http://opensource.apple.com/source/CF/CF-744.18/CFBinaryPList.c
    """
    def __init__(self, dict_type, aware_datetime=False):
        self._dict_type = dict_type
        self._aware_datime = aware_datetime

    def parse(self, fp):
        try:
            # The basic file format:
            # HEADER
            # object...
            # refid->offset...
            # TRAILER
            self._fp = fp
            self._fp.seek(-32, os.SEEK_END)
            trailer = self._fp.read(32)
            if len(trailer) != 32:
                raise InvalidFileException()
            (
                offset_size, self._ref_size, num_objects, top_object,
                offset_table_offset
            ) = struct.unpack('>6xBBQQQ', trailer)
            self._fp.seek(offset_table_offset)
            self._object_offsets = self._read_ints(num_objects, offset_size)
            self._objects = [_undefined] * num_objects
            return self._read_object(top_object)

        except (OSError, IndexError, struct.error, OverflowError,
                ValueError):
            raise InvalidFileException()

    def _get_size(self, tokenL):
        """ return the size of the next object."""
        if tokenL == 0xF:
            m = self._fp.read(1)[0] & 0x3
            s = 1 << m
            f = '>' + _BINARY_FORMAT[s]
            return struct.unpack(f, self._fp.read(s))[0]

        return tokenL

    def _read(self, size):
        cursize = min(size, _MIN_READ_BUF_SIZE)
        data = self._fp.read(cursize)
        while True:
            if len(data) != cursize:
                raise InvalidFileException
            if cursize == size:
                return data
            delta = min(cursize, size - cursize)
            data += self._fp.read(delta)
            cursize += delta

    def _read_ints(self, n, size):
        data = self._read(size * n)
        if size in _BINARY_FORMAT:
            return struct.unpack(f'>{n}{_BINARY_FORMAT[size]}', data)
