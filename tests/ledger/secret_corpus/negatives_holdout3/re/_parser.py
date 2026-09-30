#
# Secret Labs' Regular Expression Engine
#
# convert re-style regular expression to sre pattern
#
# Copyright (c) 1998-2001 by Secret Labs AB.  All rights reserved.
#
# See the __init__.py file for information on usage and redistribution.
#

"""Internal support module for sre"""

# XXX: show string offset and offending character for all errors

from ._constants import *

SPECIAL_CHARS = ".\\[{()*+?^$|"
REPEAT_CHARS = "*+?{"

DIGITS = frozenset("0123456789")

OCTDIGITS = frozenset("01234567")
HEXDIGITS = frozenset("0123456789abcdefABCDEF")
ASCIILETTERS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")

WHITESPACE = frozenset(" \t\n\r\v\f")

_REPEATCODES = frozenset({MIN_REPEAT, MAX_REPEAT, POSSESSIVE_REPEAT})
_UNITCODES = frozenset({ANY, RANGE, IN, LITERAL, NOT_LITERAL, CATEGORY})

ESCAPES = {
    r"\a": (LITERAL, ord("\a")),
    r"\b": (LITERAL, ord("\b")),
    r"\f": (LITERAL, ord("\f")),
    r"\n": (LITERAL, ord("\n")),
    r"\r": (LITERAL, ord("\r")),
    r"\t": (LITERAL, ord("\t")),
    r"\v": (LITERAL, ord("\v")),
    r"\\": (LITERAL, ord("\\"))
}

CATEGORIES = {
    r"\A": (AT, AT_BEGINNING_STRING), # start of string
    r"\b": (AT, AT_BOUNDARY),
    r"\B": (AT, AT_NON_BOUNDARY),
    r"\d": (IN, [(CATEGORY, CATEGORY_DIGIT)]),
    r"\D": (IN, [(CATEGORY, CATEGORY_NOT_DIGIT)]),
    r"\s": (IN, [(CATEGORY, CATEGORY_SPACE)]),
    r"\S": (IN, [(CATEGORY, CATEGORY_NOT_SPACE)]),
    r"\w": (IN, [(CATEGORY, CATEGORY_WORD)]),
    r"\W": (IN, [(CATEGORY, CATEGORY_NOT_WORD)]),
    r"\z": (AT, AT_END_STRING), # end of string
    r"\Z": (AT, AT_END_STRING), # end of string (obsolete)
}

FLAGS = {
    # standard flags
    "i": SRE_FLAG_IGNORECASE,
    "L": SRE_FLAG_LOCALE,
    "m": SRE_FLAG_MULTILINE,
    "s": SRE_FLAG_DOTALL,
    "x": SRE_FLAG_VERBOSE,
    # extensions
    "a": SRE_FLAG_ASCII,
    "u": SRE_FLAG_UNICODE,
}

TYPE_FLAGS = SRE_FLAG_ASCII | SRE_FLAG_LOCALE | SRE_FLAG_UNICODE
GLOBAL_FLAGS = SRE_FLAG_DEBUG

# Maximal value returned by SubPattern.getwidth().
# Must be larger than MAXREPEAT, MAXCODE and sys.maxsize.
MAXWIDTH = 1 << 64

class State:
    # keeps track of state for parsing
    def __init__(self):
        self.flags = 0
        self.groupdict = {}
        self.groupwidths = [None]  # group 0
        self.lookbehindgroups = None
        self.grouprefpos = {}
    @property
    def groups(self):
        return len(self.groupwidths)
    def opengroup(self, name=None):
        gid = self.groups
        self.groupwidths.append(None)
        if self.groups > MAXGROUPS:
            raise error("too many groups")
        if name is not None:
            ogid = self.groupdict.get(name, None)
            if ogid is not None:
                raise error("redefinition of group name %r as group %d; "
                            "was group %d" % (name, gid,  ogid))
            self.groupdict[name] = gid
        return gid
    def closegroup(self, gid, p):
        self.groupwidths[gid] = p.getwidth()
    def checkgroup(self, gid):
        return gid < self.groups and self.groupwidths[gid] is not None

    def checklookbehindgroup(self, gid, source):
        if self.lookbehindgroups is not None:
            if not self.checkgroup(gid):
                raise source.error('cannot refer to an open group')
            if gid >= self.lookbehindgroups:
                raise source.error('cannot refer to group defined in the same '
                                   'lookbehind subpattern')

class SubPattern:
    # a subpattern, in intermediate form
    def __init__(self, state, data=None):
        self.state = state
        if data is None:
            data = []
        self.data = data
        self.width = None

    def dump(self, level=0):
        seqtypes = (tuple, list)
        for op, av in self.data:
            print(level*"  " + str(op), end='')
            if op is IN:
                # member sublanguage
                print()
                for op, a in av:
                    print((level+1)*"  " + str(op), a)
            elif op is BRANCH:
                print()
                for i, a in enumerate(av[1]):
                    if i:
                        print(level*"  " + "OR")
                    a.dump(level+1)
            elif op is GROUPREF_EXISTS:
                condgroup, item_yes, item_no = av
                print('', condgroup)
                item_yes.dump(level+1)
                if item_no:
                    print(level*"  " + "ELSE")
                    item_no.dump(level+1)
            elif isinstance(av, SubPattern):
                print()
                av.dump(level+1)
            elif isinstance(av, seqtypes):
                nl = False
                for a in av:
                    if isinstance(a, SubPattern):
                        if not nl:
                            print()
                        a.dump(level+1)
                        nl = True
                    else:
                        if not nl:
                            print(' ', end='')
                        print(a, end='')
                        nl = False
                if not nl:
                    print()
            else:
                print('', av)
    def __repr__(self):
        return repr(self.data)
    def __len__(self):
        return len(self.data)
    def __delitem__(self, index):
        del self.data[index]
    def __getitem__(self, index):
        if isinstance(index, slice):
            return SubPattern(self.state, self.data[index])
        return self.data[index]
    def __setitem__(self, index, code):
        self.data[index] = code
    def insert(self, index, code):
        self.data.insert(index, code)
    def append(self, code):
        self.data.append(code)
    def getwidth(self):
        # determine the width (min, max) for this subpattern
        if self.width is not None:
            return self.width
        lo = hi = 0
        for op, av in self.data:
            if op is BRANCH:
                i = MAXWIDTH
                j = 0
                for av in av[1]:
                    l, h = av.getwidth()
                    i = min(i, l)
                    j = max(j, h)
                lo = lo + i
                hi = hi + j
            elif op is ATOMIC_GROUP:
                i, j = av.getwidth()
                lo = lo + i
                hi = hi + j
            elif op is SUBPATTERN:
                i, j = av[-1].getwidth()
                lo = lo + i
                hi = hi + j
            elif op in _REPEATCODES:
                i, j = av[2].getwidth()
                lo = lo + i * av[0]
                if av[1] == MAXREPEAT and j:
                    hi = MAXWIDTH
                else:
                    hi = hi + j * av[1]
            elif op in _UNITCODES:
                lo = lo + 1
                hi = hi + 1
            elif op is GROUPREF:
                i, j = self.state.groupwidths[av]
                lo = lo + i
                hi = hi + j
            elif op is GROUPREF_EXISTS:
                i, j = av[1].getwidth()
                if av[2] is not None:
                    l, h = av[2].getwidth()
                    i = min(i, l)
                    j = max(j, h)
                else:
                    i = 0
                lo = lo + i
                hi = hi + j
            elif op is SUCCESS:
                break
        self.width = min(lo, MAXWIDTH), min(hi, MAXWIDTH)
        return self.width

class Tokenizer:
    def __init__(self, string):
        self.istext = isinstance(string, str)
        self.string = string
        if not self.istext:
            string = str(string, 'latin1')
        self.decoded_string = string
        self.index = 0
        self.next = None
        self.__next()
    def __next(self):
        index = self.index
        try:
            char = self.decoded_string[index]
        except IndexError:
            self.next = None
            return
        if char == "\\":
            index += 1
            try:
                char += self.decoded_string[index]
            except IndexError:
                raise error("bad escape (end of pattern)",
                            self.string, len(self.string) - 1) from None
        self.index = index + 1
        self.next = char
    def match(self, char):
        if char == self.next:
            self.__next()
            return True
        return False
    def get(self):
        this = self.next
        self.__next()
        return this
    def getwhile(self, n, charset):
        result = ''
        for _ in range(n):
            c = self.next
            if c not in charset:
                break
            result += c
            self.__next()
        return result
    def getuntil(self, terminator, name):
        result = ''
        while True:
            c = self.next
            self.__next()
            if c is None:
                if not result:
                    raise self.error("missing " + name)
                raise self.error("missing %s, unterminated name" % terminator,
                                 len(result))
            if c == terminator:
                if not result:
                    raise self.error("missing " + name, 1)
                break
            result += c
        return result
    @property
    def pos(self):
        return self.index - len(self.next or '')
    def tell(self):
        return self.index - len(self.next or '')
    def seek(self, index):
        self.index = index
        self.__next()

    def error(self, msg, offset=0):
        if not self.istext:
            msg = msg.encode('ascii', 'backslashreplace').decode('ascii')
        return error(msg, self.string, self.tell() - offset)

    def checkgroupname(self, name, offset):
        if not (self.istext or name.isascii()):
            msg = "bad character in group name %a" % name
            raise self.error(msg, len(name) + offset)
        if not name.isidentifier():
            msg = "bad character in group name %r" % name
            raise self.error(msg, len(name) + offset)

def _class_escape(source, escape):
    # handle escape code inside character class
    code = ESCAPES.get(escape)
    if code:
        return code
    code = CATEGORIES.get(escape)
    if code and code[0] is IN:
        return code
    try:
        c = escape[1:2]
        if c == "x":
            # hexadecimal escape (exactly two digits)
            escape += source.getwhile(2, HEXDIGITS)
            if len(escape) != 4:
                raise source.error("incomplete escape %s" % escape, len(escape))
            return LITERAL, int(escape[2:], 16)
        elif c == "u" and source.istext:
            # unicode escape (exactly four digits)
            escape += source.getwhile(4, HEXDIGITS)
            if len(escape) != 6:
                raise source.error("incomplete escape %s" % escape, len(escape))
            return LITERAL, int(escape[2:], 16)
        elif c == "U" and source.istext:
            # unicode escape (exactly eight digits)
            escape += source.getwhile(8, HEXDIGITS)
            if len(escape) != 10:
                raise source.error("incomplete escape %s" % escape, len(escape))
            c = int(escape[2:], 16)
            chr(c) # raise ValueError for invalid code
            return LITERAL, c
        elif c == "N" and source.istext:
            import unicodedata
            # named unicode escape e.g. \N{EM DASH}
            if not source.match('{'):
                raise source.error("missing {")
            charname = source.getuntil('}', 'character name')
            try:
                c = ord(unicodedata.lookup(charname))
            except (KeyError, TypeError):
                raise source.error("undefined character name %r" % charname,
                                   len(charname) + len(r'\N{}')) from None
            return LITERAL, c
        elif c in OCTDIGITS:
            # octal escape (up to three digits)
            escape += source.getwhile(2, OCTDIGITS)
            c = int(escape[1:], 8)
            if c > 0o377:
                raise source.error('octal escape value %s outside of '
                                   'range 0-0o377' % escape, len(escape))
            return LITERAL, c
        elif c in DIGITS:
            raise ValueError
        if len(escape) == 2:
            if c in ASCIILETTERS:
                raise source.error('bad escape %s' % escape, len(escape))
            return LITERAL, ord(escape[1])
    except ValueError:
        pass
    raise source.error("bad escape %s" % escape, len(escape))

def _escape(source, escape, state):
    # handle escape code in expression
    code = CATEGORIES.get(escape)
    if code:
        return code
    code = ESCAPES.get(escape)
    if code:
        return code
    try:
        c = escape[1:2]
        if c == "x":
            # hexadecimal escape
            escape += source.getwhile(2, HEXDIGITS)
            if len(escape) != 4:
                raise source.error("incomplete escape %s" % escape, len(escape))
            return LITERAL, int(escape[2:], 16)
        elif c == "u" and source.istext:
            # unicode escape (exactly four digits)
            escape += source.getwhile(4, HEXDIGITS)
            if len(escape) != 6:
                raise source.error("incomplete escape %s" % escape, len(escape))
            return LITERAL, int(escape[2:], 16)
        elif c == "U" and source.istext:
            # unicode escape (exactly eight digits)
            escape += source.getwhile(8, HEXDIGITS)
            if len(escape) != 10:
                raise source.error("incomplete escape %s" % escape, len(escape))
            c = int(escape[2:], 16)
            chr(c) # raise ValueError for invalid code
            return LITERAL, c
        elif c == "N" and source.istext:
            import unicodedata
            # named unicode escape e.g. \N{EM DASH}
            if not source.match('{'):
                raise source.error("missing {")
            charname = source.getuntil('}', 'character name')
            try:
                c = ord(unicodedata.lookup(charname))
            except (KeyError, TypeError):
                raise source.error("undefined character name %r" % charname,
                                   len(charname) + len(r'\N{}')) from None
            return LITERAL, c
        elif c == "0":
            # octal escape
            escape += source.getwhile(2, OCTDIGITS)
            return LITERAL, int(escape[1:], 8)
        elif c in DIGITS:
            # octal escape *or* decimal group reference (sigh)
            if source.next in DIGITS:
                escape += source.get()
                if (escape[1] in OCTDIGITS and escape[2] in OCTDIGITS and
                    source.next in OCTDIGITS):
                    # got three octal digits; this is an octal escape
                    escape += source.get()
                    c = int(escape[1:], 8)
                    if c > 0o377:
                        raise source.error('octal escape value %s outside of '
                                           'range 0-0o377' % escape,
                                           len(escape))
                    return LITERAL, c
            # not an octal escape, so this is a group reference
            group = int(escape[1:])
            if group < state.groups:
                if not state.checkgroup(group):
                    raise source.error("cannot refer to an open group",
                                       len(escape))
                state.checklookbehindgroup(group, source)
                return GROUPREF, group
            raise source.error("invalid group reference %d" % group, len(escape) - 1)
        if len(escape) == 2:
            if c in ASCIILETTERS:
                raise source.error("bad escape %s" % escape, len(escape))
            return LITERAL, ord(escape[1])
    except ValueError:
        pass
    raise source.error("bad escape %s" % escape, len(escape))

def _uniq(items):
    return list(dict.fromkeys(items))

def _parse_sub(source, state, verbose, nested):
    # parse an alternation: a|b|c

    items = []
    itemsappend = items.append
    sourcematch = source.match
    start = source.tell()
    while True:
        itemsappend(_parse(source, state, verbose, nested + 1,
                           not nested and not items))
        if not sourcematch("|"):
            break
