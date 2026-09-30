'''"Executable documentation" for the pickle module.

Extensive comments about the pickle protocols and pickle-machine opcodes
can be found here.  Some functions meant for external use:

genops(pickle)
   Generate all the opcodes in a pickle, as (opcode, arg, position) triples.

dis(pickle, out=None, memo=None, indentlevel=4)
   Print a symbolic disassembly of a pickle.
'''

import codecs
import io
import pickle
import re
import sys

__all__ = ['dis', 'genops', 'optimize']

bytes_types = pickle.bytes_types

# Other ideas:
#
# - A pickle verifier:  read a pickle and check it exhaustively for
#   well-formedness.  dis() does a lot of this already.
#
# - A protocol identifier:  examine a pickle and return its protocol number
#   (== the highest .proto attr value among all the opcodes in the pickle).
#   dis() already prints this info at the end.
#
# - A pickle optimizer:  for example, tuple-building code is sometimes more
#   elaborate than necessary, catering for the possibility that the tuple
#   is recursive.  Or lots of times a PUT is generated that's never accessed
#   by a later GET.


# "A pickle" is a program for a virtual pickle machine (PM, but more accurately
# called an unpickling machine).  It's a sequence of opcodes, interpreted by the
# PM, building an arbitrarily complex Python object.
#
# For the most part, the PM is very simple:  there are no looping, testing, or
# conditional instructions, no arithmetic and no function calls.  Opcodes are
# executed once each, from first to last, until a STOP opcode is reached.
#
# The PM has two data areas, "the stack" and "the memo".
#
# Many opcodes push Python objects onto the stack; e.g., INT pushes a Python
# integer object on the stack, whose value is gotten from a decimal string
# literal immediately following the INT opcode in the pickle bytestream.  Other
# opcodes take Python objects off the stack.  The result of unpickling is
# whatever object is left on the stack when the final STOP opcode is executed.
#
# The memo is simply an array of objects, or it can be implemented as a dict
# mapping little integers to objects.  The memo serves as the PM's "long term
# memory", and the little integers indexing the memo are akin to variable
# names.  Some opcodes pop a stack object into the memo at a given index,
# and others push a memo object at a given index onto the stack again.
#
# At heart, that's all the PM has.  Subtleties arise for these reasons:
#
# + Object identity.  Objects can be arbitrarily complex, and subobjects
#   may be shared (for example, the list [a, a] refers to the same object a
#   twice).  It can be vital that unpickling recreate an isomorphic object
#   graph, faithfully reproducing sharing.
#
# + Recursive objects.  For example, after "L = []; L.append(L)", L is a
#   list, and L[0] is the same list.  This is related to the object identity
#   point, and some sequences of pickle opcodes are subtle in order to
#   get the right result in all cases.
#
# + Things pickle doesn't know everything about.  Examples of things pickle
#   does know everything about are Python's builtin scalar and container
#   types, like ints and tuples.  They generally have opcodes dedicated to
#   them.  For things like module references and instances of user-defined
#   classes, pickle's knowledge is limited.  Historically, many enhancements
#   have been made to the pickle protocol in order to do a better (faster,
#   and/or more compact) job on those.
#
# + Backward compatibility and micro-optimization.  As explained below,
#   pickle opcodes never go away, not even when better ways to do a thing
#   get invented.  The repertoire of the PM just keeps growing over time.
#   For example, protocol 0 had two opcodes for building Python integers (INT
#   and LONG), protocol 1 added three more for more-efficient pickling of short
#   integers, and protocol 2 added two more for more-efficient pickling of
#   long integers (before protocol 2, the only ways to pickle a Python long
#   took time quadratic in the number of digits, for both pickling and
#   unpickling).  "Opcode bloat" isn't so much a subtlety as a source of
#   wearying complication.
#
#
# Pickle protocols:
#
# For compatibility, the meaning of a pickle opcode never changes.  Instead new
# pickle opcodes get added, and each version's unpickler can handle all the
# pickle opcodes in all protocol versions to date.  So old pickles continue to
# be readable forever.  The pickler can generally be told to restrict itself to
# the subset of opcodes available under previous protocol versions too, so that
# users can create pickles under the current version readable by older
# versions.  However, a pickle does not contain its version number embedded
# within it.  If an older unpickler tries to read a pickle using a later
# protocol, the result is most likely an exception due to seeing an unknown (in
# the older unpickler) opcode.
#
# The original pickle used what's now called "protocol 0", and what was called
# "text mode" before Python 2.3.  The entire pickle bytestream is made up of
# printable 7-bit ASCII characters, plus the newline character, in protocol 0.
# That's why it was called text mode.  Protocol 0 is small and elegant, but
# sometimes painfully inefficient.
#
# The second major set of additions is now called "protocol 1", and was called
# "binary mode" before Python 2.3.  This added many opcodes with arguments
# consisting of arbitrary bytes, including NUL bytes and unprintable "high bit"
# bytes.  Binary mode pickles can be substantially smaller than equivalent
# text mode pickles, and sometimes faster too; e.g., BININT represents a 4-byte
# int as 4 bytes following the opcode, which is cheaper to unpickle than the
# (perhaps) 11-character decimal string attached to INT.  Protocol 1 also added
# a number of opcodes that operate on many stack elements at once (like APPENDS
# and SETITEMS), and "shortcut" opcodes (like EMPTY_DICT and EMPTY_TUPLE).
#
# The third major set of additions came in Python 2.3, and is called "protocol
# 2".  This added:
#
# - A better way to pickle instances of new-style classes (NEWOBJ).
#
# - A way for a pickle to identify its protocol (PROTO).
#
# - Time- and space- efficient pickling of long ints (LONG{1,4}).
#
# - Shortcuts for small tuples (TUPLE{1,2,3}}.
#
# - Dedicated opcodes for bools (NEWTRUE, NEWFALSE).
#
# - The "extension registry", a vector of popular objects that can be pushed
#   efficiently by index (EXT{1,2,4}).  This is akin to the memo and GET, but
#   the registry contents are predefined (there's nothing akin to the memo's
#   PUT).
#
# Another independent change with Python 2.3 is the abandonment of any
# pretense that it might be safe to load pickles received from untrusted
# parties -- no sufficient security analysis has been done to guarantee
# this and there isn't a use case that warrants the expense of such an
# analysis.
#
# To this end, all tests for __safe_for_unpickling__ or for
# copyreg.safe_constructors are removed from the unpickling code.
# References to these variables in the descriptions below are to be seen
# as describing unpickling in Python 2.2 and before.


# Meta-rule:  Descriptions are stored in instances of descriptor objects,
# with plain constructors.  No meta-language is defined from which
# descriptors could be constructed.  If you want, e.g., XML, write a little
# program to generate XML from the objects.

##############################################################################
# Some pickle opcodes have an argument, following the opcode in the
# bytestream.  An argument is of a specific type, described by an instance
# of ArgumentDescriptor.  These are not to be confused with arguments taken
# off the stack -- ArgumentDescriptor applies only to arguments embedded in
# the opcode stream, immediately following an opcode.

# Represents the number of bytes consumed by an argument delimited by the
# next newline character.
UP_TO_NEWLINE = -1

# Represents the number of bytes consumed by a two-argument opcode where
# the first argument gives the number of bytes in the second argument.
TAKEN_FROM_ARGUMENT1  = -2   # num bytes is 1-byte unsigned int
TAKEN_FROM_ARGUMENT4  = -3   # num bytes is 4-byte signed little-endian int
TAKEN_FROM_ARGUMENT4U = -4   # num bytes is 4-byte unsigned little-endian int
TAKEN_FROM_ARGUMENT8U = -5   # num bytes is 8-byte unsigned little-endian int

class ArgumentDescriptor(object):
    __slots__ = (
        # name of descriptor record, also a module global name; a string
        'name',

        # length of argument, in bytes; an int; UP_TO_NEWLINE and
        # TAKEN_FROM_ARGUMENT{1,4,8} are negative values for variable-length
        # cases
        'n',

        # a function taking a file-like object, reading this kind of argument
        # from the object at the current position, advancing the current
        # position by n bytes, and returning the value of the argument
        'reader',

        # human-readable docs for this arg descriptor; a string
        'doc',
    )

    def __init__(self, name, n, reader, doc):
        assert isinstance(name, str)
        self.name = name

        assert isinstance(n, int) and (n >= 0 or
                                       n in (UP_TO_NEWLINE,
                                             TAKEN_FROM_ARGUMENT1,
                                             TAKEN_FROM_ARGUMENT4,
                                             TAKEN_FROM_ARGUMENT4U,
                                             TAKEN_FROM_ARGUMENT8U))
        self.n = n

        self.reader = reader

        assert isinstance(doc, str)
        self.doc = doc

from struct import unpack as _unpack

def read_uint1(f):
    r"""
    >>> import io
    >>> read_uint1(io.BytesIO(b'\xff'))
    255
    """

    data = f.read(1)
    if data:
        return data[0]
    raise ValueError("not enough data in stream to read uint1")

uint1 = ArgumentDescriptor(
            name='uint1',
            n=1,
            reader=read_uint1,
            doc="One-byte unsigned integer.")


def read_uint2(f):
    r"""
    >>> import io
    >>> read_uint2(io.BytesIO(b'\xff\x00'))
    255
    >>> read_uint2(io.BytesIO(b'\xff\xff'))
    65535
    """

    data = f.read(2)
    if len(data) == 2:
        return _unpack("<H", data)[0]
    raise ValueError("not enough data in stream to read uint2")

uint2 = ArgumentDescriptor(
            name='uint2',
            n=2,
            reader=read_uint2,
            doc="Two-byte unsigned integer, little-endian.")


def read_int4(f):
    r"""
    >>> import io
    >>> read_int4(io.BytesIO(b'\xff\x00\x00\x00'))
    255
    >>> read_int4(io.BytesIO(b'\x00\x00\x00\x80')) == -(2**31)
    True
    """

    data = f.read(4)
    if len(data) == 4:
        return _unpack("<i", data)[0]
    raise ValueError("not enough data in stream to read int4")

int4 = ArgumentDescriptor(
           name='int4',
           n=4,
           reader=read_int4,
           doc="Four-byte signed integer, little-endian, 2's complement.")


def read_uint4(f):
    r"""
    >>> import io
    >>> read_uint4(io.BytesIO(b'\xff\x00\x00\x00'))
    255
    >>> read_uint4(io.BytesIO(b'\x00\x00\x00\x80')) == 2**31
    True
    """

    data = f.read(4)
    if len(data) == 4:
        return _unpack("<I", data)[0]
    raise ValueError("not enough data in stream to read uint4")

uint4 = ArgumentDescriptor(
            name='uint4',
            n=4,
            reader=read_uint4,
            doc="Four-byte unsigned integer, little-endian.")


def read_uint8(f):
    r"""
    >>> import io
    >>> read_uint8(io.BytesIO(b'\xff\x00\x00\x00\x00\x00\x00\x00'))
    255
    >>> read_uint8(io.BytesIO(b'\xff' * 8)) == 2**64-1
    True
    """

    data = f.read(8)
    if len(data) == 8:
        return _unpack("<Q", data)[0]
    raise ValueError("not enough data in stream to read uint8")

uint8 = ArgumentDescriptor(
            name='uint8',
            n=8,
            reader=read_uint8,
            doc="Eight-byte unsigned integer, little-endian.")


def read_stringnl(f, decode=True, stripquotes=True, *, encoding='latin-1'):
    r"""
    >>> import io
    >>> read_stringnl(io.BytesIO(b"'abcd'\nefg\n"))
    'abcd'

    >>> read_stringnl(io.BytesIO(b"\n"))
    Traceback (most recent call last):
    ...
    ValueError: no string quotes around b''

    >>> read_stringnl(io.BytesIO(b"\n"), stripquotes=False)
    ''

    >>> read_stringnl(io.BytesIO(b"''\n"))
    ''

    >>> read_stringnl(io.BytesIO(b'"abcd"'))
    Traceback (most recent call last):
    ...
    ValueError: no newline found when trying to read stringnl

    Embedded escapes are undone in the result.
    >>> read_stringnl(io.BytesIO(br"'a\n\\b\x00c\td'" + b"\n'e'"))
    'a\n\\b\x00c\td'
    """

    data = f.readline()
    if not data.endswith(b'\n'):
        raise ValueError("no newline found when trying to read stringnl")
    data = data[:-1]    # lose the newline

    if stripquotes:
        for q in (b'"', b"'"):
            if data.startswith(q):
                if not data.endswith(q):
                    raise ValueError("string quote %r not found at both "
                                     "ends of %r" % (q, data))
                data = data[1:-1]
                break
        else:
            raise ValueError("no string quotes around %r" % data)

    if decode:
        data = codecs.escape_decode(data)[0].decode(encoding)
    return data

stringnl = ArgumentDescriptor(
               name='stringnl',
               n=UP_TO_NEWLINE,
               reader=read_stringnl,
               doc="""A newline-terminated string.

                   This is a repr-style string, with embedded escapes, and
                   bracketing quotes.
                   """)

def read_stringnl_noescape(f):
    return read_stringnl(f, stripquotes=False, encoding='utf-8')

stringnl_noescape = ArgumentDescriptor(
                        name='stringnl_noescape',
                        n=UP_TO_NEWLINE,
                        reader=read_stringnl_noescape,
                        doc="""A newline-terminated string.

                        This is a str-style string, without embedded escapes,
                        or bracketing quotes.  It should consist solely of
                        printable ASCII characters.
                        """)

def read_stringnl_noescape_pair(f):
    r"""
    >>> import io
    >>> read_stringnl_noescape_pair(io.BytesIO(b"Queue\nEmpty\njunk"))
    'Queue Empty'
    """

    return "%s %s" % (read_stringnl_noescape(f), read_stringnl_noescape(f))

stringnl_noescape_pair = ArgumentDescriptor(
                             name='stringnl_noescape_pair',
                             n=UP_TO_NEWLINE,
                             reader=read_stringnl_noescape_pair,
                             doc="""A pair of newline-terminated strings.

                             These are str-style strings, without embedded
                             escapes, or bracketing quotes.  They should
                             consist solely of printable ASCII characters.
                             The pair is returned as a single string, with
                             a single blank separating the two strings.
                             """)


def read_string1(f):
    r"""
    >>> import io
    >>> read_string1(io.BytesIO(b"\x00"))
    ''
    >>> read_string1(io.BytesIO(b"\x03abcdef"))
    'abc'
    """

    n = read_uint1(f)
    assert n >= 0
    data = f.read(n)
    if len(data) == n:
        return data.decode("latin-1")
    raise ValueError("expected %d bytes in a string1, but only %d remain" %
                     (n, len(data)))

string1 = ArgumentDescriptor(
              name="string1",
              n=TAKEN_FROM_ARGUMENT1,
              reader=read_string1,
              doc="""A counted string.

              The first argument is a 1-byte unsigned int giving the number
              of bytes in the string, and the second argument is that many
              bytes.
              """)


def read_string4(f):
    r"""
    >>> import io
    >>> read_string4(io.BytesIO(b"\x00\x00\x00\x00abc"))
    ''
    >>> read_string4(io.BytesIO(b"\x03\x00\x00\x00abcdef"))
    'abc'
    >>> read_string4(io.BytesIO(b"\x00\x00\x00\x03abcdef"))
    Traceback (most recent call last):
    ...
