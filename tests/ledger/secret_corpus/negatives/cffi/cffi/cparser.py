from . import model
from .commontypes import COMMON_TYPES, resolve_common_type
from .error import FFIError, CDefError
try:
    from . import _pycparser as pycparser
except ImportError:
    import pycparser
import weakref, re, sys

try:
    if sys.version_info < (3,):
        import thread as _thread
    else:
        import _thread
    lock = _thread.allocate_lock()
except ImportError:
    lock = None

def _workaround_for_static_import_finders():
    # Issue #392: packaging tools like cx_Freeze can not find these
    # because pycparser uses exec dynamic import.  This is an obscure
    # workaround.  This function is never called.
    import pycparser.yacctab
    import pycparser.lextab

CDEF_SOURCE_STRING = "<cdef source string>"
_r_comment = re.compile(r"/\*.*?\*/|//([^\n\\]|\\.)*?$",
                        re.DOTALL | re.MULTILINE)
_r_define  = re.compile(r"^\s*#\s*define\s+([A-Za-z_][A-Za-z_0-9]*)"
                        r"\b((?:[^\n\\]|\\.)*?)$",
                        re.DOTALL | re.MULTILINE)
_r_line_directive = re.compile(r"^[ \t]*#[ \t]*(?:line|\d+)\b.*$", re.MULTILINE)
_r_partial_enum = re.compile(r"=\s*\.\.\.\s*[,}]|\.\.\.\s*\}")
_r_enum_dotdotdot = re.compile(r"__dotdotdot\d+__$")
_r_partial_array = re.compile(r"\[\s*\.\.\.\s*\]")
_r_words = re.compile(r"\w+|\S")
_parser_cache = None
_r_int_literal = re.compile(r"-?0?x?[0-9a-f]+[lu]*$", re.IGNORECASE)
_r_stdcall1 = re.compile(r"\b(__stdcall|WINAPI)\b")
_r_stdcall2 = re.compile(r"[(]\s*(__stdcall|WINAPI)\b")
_r_cdecl = re.compile(r"\b__cdecl\b")
_r_extern_python = re.compile(r'\bextern\s*"'
                              r'(Python|Python\s*\+\s*C|C\s*\+\s*Python)"\s*.')
_r_star_const_space = re.compile(       # matches "* const "
    r"[*]\s*((const|volatile|restrict)\b\s*)+")
_r_int_dotdotdot = re.compile(r"(\b(int|long|short|signed|unsigned|char)\s*)+"
                              r"\.\.\.")
_r_float_dotdotdot = re.compile(r"\b(double|float)\s*\.\.\.")

def _get_parser():
    global _parser_cache
    if _parser_cache is None:
        _parser_cache = pycparser.CParser()
    return _parser_cache

def _workaround_for_old_pycparser(csource):
    # Workaround for a pycparser issue (fixed between pycparser 2.10 and
    # 2.14): "char*const***" gives us a wrong syntax tree, the same as
    # for "char***(*const)".  This means we can't tell the difference
    # afterwards.  But "char(*const(***))" gives us the right syntax
    # tree.  The issue only occurs if there are several stars in
    # sequence with no parenthesis in between, just possibly qualifiers.
    # Attempt to fix it by adding some parentheses in the source: each
    # time we see "* const" or "* const *", we add an opening
    # parenthesis before each star---the hard part is figuring out where
    # to close them.
    parts = []
    while True:
        match = _r_star_const_space.search(csource)
        if not match:
            break
        #print repr(''.join(parts)+csource), '=>',
        parts.append(csource[:match.start()])
        parts.append('('); closing = ')'
        parts.append(match.group())   # e.g. "* const "
        endpos = match.end()
        if csource.startswith('*', endpos):
            parts.append('('); closing += ')'
        level = 0
        i = endpos
        while i < len(csource):
            c = csource[i]
            if c == '(':
                level += 1
            elif c == ')':
                if level == 0:
                    break
                level -= 1
            elif c in ',;=':
                if level == 0:
                    break
            i += 1
        csource = csource[endpos:i] + closing + csource[i:]
        #print repr(''.join(parts)+csource)
    parts.append(csource)
    return ''.join(parts)

def _preprocess_extern_python(csource):
    # input: `extern "Python" int foo(int);` or
    #        `extern "Python" { int foo(int); }`
    # output:
    #     void __cffi_extern_python_start;
    #     int foo(int);
    #     void __cffi_extern_python_stop;
    #
    # input: `extern "Python+C" int foo(int);`
    # output:
    #     void __cffi_extern_python_plus_c_start;
    #     int foo(int);
    #     void __cffi_extern_python_stop;
    parts = []
    while True:
        match = _r_extern_python.search(csource)
        if not match:
            break
        endpos = match.end() - 1
        #print
        #print ''.join(parts)+csource
        #print '=>'
        parts.append(csource[:match.start()])
        if 'C' in match.group(1):
            parts.append('void __cffi_extern_python_plus_c_start; ')
        else:
            parts.append('void __cffi_extern_python_start; ')
        if csource[endpos] == '{':
            # grouping variant
            closing = csource.find('}', endpos)
            if closing < 0:
                raise CDefError("'extern \"Python\" {': no '}' found")
            if csource.find('{', endpos + 1, closing) >= 0:
                raise NotImplementedError("cannot use { } inside a block "
                                          "'extern \"Python\" { ... }'")
            parts.append(csource[endpos+1:closing])
            csource = csource[closing+1:]
        else:
            # non-grouping variant
            semicolon = csource.find(';', endpos)
            if semicolon < 0:
                raise CDefError("'extern \"Python\": no ';' found")
            parts.append(csource[endpos:semicolon+1])
            csource = csource[semicolon+1:]
        parts.append(' void __cffi_extern_python_stop;')
        #print ''.join(parts)+csource
        #print
    parts.append(csource)
    return ''.join(parts)

def _warn_for_string_literal(csource):
    if '"' not in csource:
        return
    for line in csource.splitlines():
        if '"' in line and not line.lstrip().startswith('#'):
            import warnings
            warnings.warn("String literal found in cdef() or type source. "
                          "String literals are ignored here, but you should "
                          "remove them anyway because some character sequences "
                          "confuse pre-parsing.")
            break

def _warn_for_non_extern_non_static_global_variable(decl):
    if not decl.storage:
        import warnings
        warnings.warn("Global variable '%s' in cdef(): for consistency "
                      "with C it should have a storage class specifier "
                      "(usually 'extern')" % (decl.name,))

def _remove_line_directives(csource):
    # _r_line_directive matches whole lines, without the final \n, if they
    # start with '#line' with some spacing allowed, or '#NUMBER'.  This
    # function stores them away and replaces them with exactly the string
    # '#line@N', where N is the index in the list 'line_directives'.
    line_directives = []
    def replace(m):
        i = len(line_directives)
        line_directives.append(m.group())
        return '#line@%d' % i
    csource = _r_line_directive.sub(replace, csource)
    return csource, line_directives

def _put_back_line_directives(csource, line_directives):
    def replace(m):
        s = m.group()
        if not s.startswith('#line@'):
            raise AssertionError("unexpected #line directive "
                                 "(should have been processed and removed")
        return line_directives[int(s[6:])]
    return _r_line_directive.sub(replace, csource)

def _preprocess(csource):
    # First, remove the lines of the form '#line N "filename"' because
    # the "filename" part could confuse the rest
    csource, line_directives = _remove_line_directives(csource)
    # Remove comments.  NOTE: this only work because the cdef() section
    # should not contain any string literals (except in line directives)!
    def replace_keeping_newlines(m):
        return ' ' + m.group().count('\n') * '\n'
    csource = _r_comment.sub(replace_keeping_newlines, csource)
    # Remove the "#define FOO x" lines
    macros = {}
    for match in _r_define.finditer(csource):
        macroname, macrovalue = match.groups()
        macrovalue = macrovalue.replace('\\\n', '').strip()
        macros[macroname] = macrovalue
    csource = _r_define.sub('', csource)
    #
    if pycparser.__version__ < '2.14':
        csource = _workaround_for_old_pycparser(csource)
    #
    # BIG HACK: replace WINAPI or __stdcall with "volatile const".
    # It doesn't make sense for the return type of a function to be
    # "volatile volatile const", so we abuse it to detect __stdcall...
    # Hack number 2 is that "int(volatile *fptr)();" is not valid C
    # syntax, so we place the "volatile" before the opening parenthesis.
    csource = _r_stdcall2.sub(' volatile volatile const(', csource)
    csource = _r_stdcall1.sub(' volatile volatile const ', csource)
    csource = _r_cdecl.sub(' ', csource)
    #
    # Replace `extern "Python"` with start/end markers
    csource = _preprocess_extern_python(csource)
    #
    # Now there should not be any string literal left; warn if we get one
    _warn_for_string_literal(csource)
    #
    # Replace "[...]" with "[__dotdotdotarray__]"
    csource = _r_partial_array.sub('[__dotdotdotarray__]', csource)
    #
    # Replace "...}" with "__dotdotdotNUM__}".  This construction should
    # occur only at the end of enums; at the end of structs we have "...;}"
    # and at the end of vararg functions "...);".  Also replace "=...[,}]"
    # with ",__dotdotdotNUM__[,}]": this occurs in the enums too, when
    # giving an unknown value.
    matches = list(_r_partial_enum.finditer(csource))
    for number, match in enumerate(reversed(matches)):
        p = match.start()
        if csource[p] == '=':
            p2 = csource.find('...', p, match.end())
            assert p2 > p
            csource = '%s,__dotdotdot%d__ %s' % (csource[:p], number,
                                                 csource[p2+3:])
        else:
            assert csource[p:p+3] == '...'
            csource = '%s __dotdotdot%d__ %s' % (csource[:p], number,
                                                 csource[p+3:])
    # Replace "int ..." or "unsigned long int..." with "__dotdotdotint__"
    csource = _r_int_dotdotdot.sub(' __dotdotdotint__ ', csource)
    # Replace "float ..." or "double..." with "__dotdotdotfloat__"
    csource = _r_float_dotdotdot.sub(' __dotdotdotfloat__ ', csource)
    # Replace all remaining "..." with the same name, "__dotdotdot__",
    # which is declared with a typedef for the purpose of C parsing.
    csource = csource.replace('...', ' __dotdotdot__ ')
    # Finally, put back the line directives
    csource = _put_back_line_directives(csource, line_directives)
    return csource, macros

def _common_type_names(csource):
    # Look in the source for what looks like usages of types from the
    # list of common types.  A "usage" is approximated here as the
    # appearance of the word, minus a "definition" of the type, which
    # is the last word in a "typedef" statement.  Approximative only
    # but should be fine for all the common types.
    look_for_words = set(COMMON_TYPES)
    look_for_words.add(';')
    look_for_words.add(',')
    look_for_words.add('(')
    look_for_words.add(')')
    look_for_words.add('typedef')
    words_used = set()
    is_typedef = False
    paren = 0
    previous_word = ''
    for word in _r_words.findall(csource):
        if word in look_for_words:
            if word == ';':
                if is_typedef:
                    words_used.discard(previous_word)
                    look_for_words.discard(previous_word)
                    is_typedef = False
            elif word == 'typedef':
                is_typedef = True
                paren = 0
            elif word == '(':
                paren += 1
            elif word == ')':
                paren -= 1
            elif word == ',':
                if is_typedef and paren == 0:
                    words_used.discard(previous_word)
                    look_for_words.discard(previous_word)
            else:   # word in COMMON_TYPES
                words_used.add(word)
        previous_word = word
    return words_used


class Parser:

    def __init__(self):
        self._declarations = {}
        self._included_declarations = set()
        self._anonymous_counter = 0
        self._structnode2type = weakref.WeakKeyDictionary()
        self._options = {}
        self._int_constants = {}
        self._recomplete = []
        self._uses_new_feature = None

    def _parse(self, csource):
        csource, macros = _preprocess(csource)
        # XXX: for more efficiency we would need to poke into the
        # internals of CParser...  the following registers the
        # typedefs, because their presence or absence influences the
        # parsing itself (but what they are typedef'ed to plays no role)
        ctn = _common_type_names(csource)
        typenames = []
        for name in sorted(self._declarations):
            if name.startswith('typedef '):
                name = name[8:]
                typenames.append(name)
                ctn.discard(name)
        typenames += sorted(ctn)
        #
        csourcelines = []
        csourcelines.append('# 1 "<cdef automatic initialization code>"')
        for typename in typenames:
            csourcelines.append('typedef int %s;' % typename)
        csourcelines.append('typedef int __dotdotdotint__, __dotdotdotfloat__,'
                            ' __dotdotdot__;')
        # this forces pycparser to consider the following in the file
        # called <cdef source string> from line 1
        csourcelines.append('# 1 "%s"' % (CDEF_SOURCE_STRING,))
        csourcelines.append(csource)
        csourcelines.append('')   # see test_missing_newline_bug
        fullcsource = '\n'.join(csourcelines)
        if lock is not None:
            lock.acquire()     # pycparser is not thread-safe...
        try:
            ast = _get_parser().parse(fullcsource)
        except pycparser.c_parser.ParseError as e:
            self.convert_pycparser_error(e, csource)
        finally:
            if lock is not None:
                lock.release()
        # csource will be used to find buggy source text
        return ast, macros, csource

    def _convert_pycparser_error(self, e, csource):
        # xxx look for "<cdef source string>:NUM:" at the start of str(e)
        # and interpret that as a line number.  This will not work if
        # the user gives explicit ``# NUM "FILE"`` directives.
        line = None
        msg = str(e)
        match = re.match(r"%s:(\d+):" % (CDEF_SOURCE_STRING,), msg)
        if match:
            linenum = int(match.group(1), 10)
            csourcelines = csource.splitlines()
            if 1 <= linenum <= len(csourcelines):
                line = csourcelines[linenum-1]
        return line

    def convert_pycparser_error(self, e, csource):
        line = self._convert_pycparser_error(e, csource)

        msg = str(e)
        if line:
            msg = 'cannot parse "%s"\n%s' % (line.strip(), msg)
        else:
            msg = 'parse error\n%s' % (msg,)
        raise CDefError(msg)

    def parse(self, csource, override=False, packed=False, pack=None,
                    dllexport=False):
        if packed:
            if packed != True:
                raise ValueError("'packed' should be False or True; use "
                                 "'pack' to give another value")
            if pack:
                raise ValueError("cannot give both 'pack' and 'packed'")
            pack = 1
        elif pack:
            if pack & (pack - 1):
                raise ValueError("'pack' must be a power of two, not %r" %
                    (pack,))
        else:
            pack = 0
        prev_options = self._options
        try:
            self._options = {'override': override,
                             'packed': pack,
                             'dllexport': dllexport}
            self._internal_parse(csource)
        finally:
            self._options = prev_options

    def _internal_parse(self, csource):
        ast, macros, csource = self._parse(csource)
        # add the macros
        self._process_macros(macros)
        # find the first "__dotdotdot__" and use that as a separator
        # between the repeated typedefs and the real csource
        iterator = iter(ast.ext)
        for decl in iterator:
