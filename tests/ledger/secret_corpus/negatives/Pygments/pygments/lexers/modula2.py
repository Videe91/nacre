"""
    pygments.lexers.modula2
    ~~~~~~~~~~~~~~~~~~~~~~~

    Multi-Dialect Lexer for Modula-2.

    :copyright: Copyright 2006-present by the Pygments team, see AUTHORS.
    :license: BSD, see LICENSE for details.
"""

import re

from pygments.lexer import RegexLexer, include
from pygments.util import get_bool_opt, get_list_opt
from pygments.token import Text, Comment, Operator, Keyword, Name, \
    String, Number, Punctuation, Error

__all__ = ['Modula2Lexer']


# Multi-Dialect Modula-2 Lexer
class Modula2Lexer(RegexLexer):
    """
    For Modula-2 source code.

    The Modula-2 lexer supports several dialects.  By default, it operates in
    fallback mode, recognising the *combined* literals, punctuation symbols
    and operators of all supported dialects, and the *combined* reserved words
    and builtins of PIM Modula-2, ISO Modula-2 and Modula-2 R10, while not
    differentiating between library defined identifiers.

    To select a specific dialect, a dialect option may be passed
    or a dialect tag may be embedded into a source file.

    Dialect Options:

    `m2pim`
        Select PIM Modula-2 dialect.
    `m2iso`
        Select ISO Modula-2 dialect.
    `m2r10`
        Select Modula-2 R10 dialect.
    `objm2`
        Select Objective Modula-2 dialect.

    The PIM and ISO dialect options may be qualified with a language extension.

    Language Extensions:

    `+aglet`
        Select Aglet Modula-2 extensions, available with m2iso.
    `+gm2`
        Select GNU Modula-2 extensions, available with m2pim.
    `+p1`
        Select p1 Modula-2 extensions, available with m2iso.
    `+xds`
        Select XDS Modula-2 extensions, available with m2iso.


    Passing a Dialect Option via Unix Commandline Interface

    Dialect options may be passed to the lexer using the `dialect` key.
    Only one such option should be passed. If multiple dialect options are
    passed, the first valid option is used, any subsequent options are ignored.

    Examples:

    `$ pygmentize -O full,dialect=m2iso -f html -o /path/to/output /path/to/input`
        Use ISO dialect to render input to HTML output
    `$ pygmentize -O full,dialect=m2iso+p1 -f rtf -o /path/to/output /path/to/input`
        Use ISO dialect with p1 extensions to render input to RTF output


    Embedding a Dialect Option within a source file

    A dialect option may be embedded in a source file in form of a dialect
    tag, a specially formatted comment that specifies a dialect option.

    Dialect Tag EBNF::

       dialectTag :
           OpeningCommentDelim Prefix dialectOption ClosingCommentDelim ;

       dialectOption :
           'm2pim' | 'm2iso' | 'm2r10' | 'objm2' |
           'm2iso+aglet' | 'm2pim+gm2' | 'm2iso+p1' | 'm2iso+xds' ;

       Prefix : '!' ;

       OpeningCommentDelim : '(*' ;

       ClosingCommentDelim : '*)' ;

    No whitespace is permitted between the tokens of a dialect tag.

    In the event that a source file contains multiple dialect tags, the first
    tag that contains a valid dialect option will be used and any subsequent
    dialect tags will be ignored.  Ideally, a dialect tag should be placed
    at the beginning of a source file.

    An embedded dialect tag overrides a dialect option set via command line.

    Examples:

    ``(*!m2r10*) DEFINITION MODULE Foobar; ...``
        Use Modula2 R10 dialect to render this source file.
    ``(*!m2pim+gm2*) DEFINITION MODULE Bazbam; ...``
        Use PIM dialect with GNU extensions to render this source file.


    Algol Publication Mode:

    In Algol publication mode, source text is rendered for publication of
    algorithms in scientific papers and academic texts, following the format
    of the Revised Algol-60 Language Report.  It is activated by passing
    one of two corresponding styles as an option:

    `algol`
        render reserved words lowercase underline boldface
        and builtins lowercase boldface italic
    `algol_nu`
        render reserved words lowercase boldface (no underlining)
        and builtins lowercase boldface italic

    The lexer automatically performs the required lowercase conversion when
    this mode is activated.

    Example:

    ``$ pygmentize -O full,style=algol -f latex -o /path/to/output /path/to/input``
        Render input file in Algol publication mode to LaTeX output.


    Rendering Mode of First Class ADT Identifiers:

    The rendering of standard library first class ADT identifiers is controlled
    by option flag "treat_stdlib_adts_as_builtins".

    When this option is turned on, standard library ADT identifiers are rendered
    as builtins.  When it is turned off, they are rendered as ordinary library
    identifiers.

    `treat_stdlib_adts_as_builtins` (default: On)

    The option is useful for dialects that support ADTs as first class objects
    and provide ADTs in the standard library that would otherwise be built-in.

    At present, only Modula-2 R10 supports library ADTs as first class objects
    and therefore, no ADT identifiers are defined for any other dialects.

    Example:

    ``$ pygmentize -O full,dialect=m2r10,treat_stdlib_adts_as_builtins=Off ...``
        Render standard library ADTs as ordinary library types.

    .. versionchanged:: 2.1
       Added multi-dialect support.
    """
    name = 'Modula-2'
    url = 'http://www.modula2.org/'
    aliases = ['modula2', 'm2']
    filenames = ['*.def', '*.mod']
    mimetypes = ['text/x-modula2']
    version_added = '1.3'

    flags = re.MULTILINE | re.DOTALL

    tokens = {
        'whitespace': [
            (r'\n+', Text),  # blank lines
            (r'\s+', Text),  # whitespace
        ],
        'dialecttags': [
            # PIM Dialect Tag
            (r'\(\*!m2pim\*\)', Comment.Special),
            # ISO Dialect Tag
            (r'\(\*!m2iso\*\)', Comment.Special),
            # M2R10 Dialect Tag
            (r'\(\*!m2r10\*\)', Comment.Special),
            # ObjM2 Dialect Tag
            (r'\(\*!objm2\*\)', Comment.Special),
            # Aglet Extensions Dialect Tag
            (r'\(\*!m2iso\+aglet\*\)', Comment.Special),
            # GNU Extensions Dialect Tag
            (r'\(\*!m2pim\+gm2\*\)', Comment.Special),
            # p1 Extensions Dialect Tag
            (r'\(\*!m2iso\+p1\*\)', Comment.Special),
            # XDS Extensions Dialect Tag
            (r'\(\*!m2iso\+xds\*\)', Comment.Special),
        ],
        'identifiers': [
            (r'([a-zA-Z_$][\w$]*)', Name),
        ],
        'prefixed_number_literals': [
            #
            # Base-2, whole number
            (r'0b[01]+(\'[01]+)*', Number.Bin),
            #
            # Base-16, whole number
            (r'0[ux][0-9A-F]+(\'[0-9A-F]+)*', Number.Hex),
        ],
        'plain_number_literals': [
            #
            # Base-10, real number with exponent
            (r'[0-9]+(\'[0-9]+)*'  # integral part
             r'\.[0-9]+(\'[0-9]+)*'  # fractional part
             r'[eE][+-]?[0-9]+(\'[0-9]+)*',  # exponent
             Number.Float),
            #
            # Base-10, real number without exponent
            (r'[0-9]+(\'[0-9]+)*'  # integral part
             r'\.[0-9]+(\'[0-9]+)*',  # fractional part
             Number.Float),
            #
            # Base-10, whole number
            (r'[0-9]+(\'[0-9]+)*', Number.Integer),
        ],
        'suffixed_number_literals': [
            #
            # Base-8, whole number
            (r'[0-7]+B', Number.Oct),
            #
            # Base-8, character code
            (r'[0-7]+C', Number.Oct),
            #
            # Base-16, number
            (r'[0-9A-F]+H', Number.Hex),
        ],
        'string_literals': [
            (r'"(\\\\|\\[^\\]|[^"\\])*"', String.Double),
            (r"'(\\\\|\\[^\\]|[^'\\])*'", String.Single),
        ],
        'digraph_operators': [
            # Dot Product Operator
            (r'\*\.', Operator),
            # Array Concatenation Operator
            (r'\+>', Operator),  # M2R10 + ObjM2
            # Inequality Operator
            (r'<>', Operator),  # ISO + PIM
            # Less-Or-Equal, Subset
            (r'<=', Operator),
            # Greater-Or-Equal, Superset
            (r'>=', Operator),
            # Identity Operator
            (r'==', Operator),  # M2R10 + ObjM2
            # Type Conversion Operator
            (r'::', Operator),  # M2R10 + ObjM2
            # Assignment Symbol
            (r':=', Operator),
            # Postfix Increment Mutator
            (r'\+\+', Operator),  # M2R10 + ObjM2
            # Postfix Decrement Mutator
            (r'--', Operator),  # M2R10 + ObjM2
        ],
        'unigraph_operators': [
            # Arithmetic Operators
            (r'[+-]', Operator),
            (r'[*/]', Operator),
            # ISO 80000-2 compliant Set Difference Operator
            (r'\\', Operator),  # M2R10 + ObjM2
            # Relational Operators
            (r'[=#<>]', Operator),
            # Dereferencing Operator
            (r'\^', Operator),
            # Dereferencing Operator Synonym
            (r'@', Operator),  # ISO
            # Logical AND Operator Synonym
            (r'&', Operator),  # PIM + ISO
            # Logical NOT Operator Synonym
            (r'~', Operator),  # PIM + ISO
            # Smalltalk Message Prefix
            (r'`', Operator),  # ObjM2
        ],
        'digraph_punctuation': [
            # Range Constructor
            (r'\.\.', Punctuation),
            # Opening Chevron Bracket
            (r'<<', Punctuation),  # M2R10 + ISO
            # Closing Chevron Bracket
            (r'>>', Punctuation),  # M2R10 + ISO
            # Blueprint Punctuation
            (r'->', Punctuation),  # M2R10 + ISO
            # Distinguish |# and # in M2 R10
            (r'\|#', Punctuation),
            # Distinguish ## and # in M2 R10
            (r'##', Punctuation),
            # Distinguish |* and * in M2 R10
            (r'\|\*', Punctuation),
        ],
        'unigraph_punctuation': [
            # Common Punctuation
            (r'[()\[\]{},.:;|]', Punctuation),
            # Case Label Separator Synonym
            (r'!', Punctuation),  # ISO
            # Blueprint Punctuation
            (r'\?', Punctuation),  # M2R10 + ObjM2
        ],
        'comments': [
            # Single Line Comment
            (r'^//.*?\n', Comment.Single),  # M2R10 + ObjM2
            # Block Comment
            (r'\(\*([^$].*?)\*\)', Comment.Multiline),
            # Template Block Comment
            (r'/\*(.*?)\*/', Comment.Multiline),  # M2R10 + ObjM2
        ],
        'pragmas': [
            # ISO Style Pragmas
            (r'<\*.*?\*>', Comment.Preproc),  # ISO, M2R10 + ObjM2
            # Pascal Style Pragmas
            (r'\(\*\$.*?\*\)', Comment.Preproc),  # PIM
        ],
        'root': [
            include('whitespace'),
            include('dialecttags'),
            include('pragmas'),
            include('comments'),
            include('identifiers'),
            include('suffixed_number_literals'),  # PIM + ISO
            include('prefixed_number_literals'),  # M2R10 + ObjM2
            include('plain_number_literals'),
            include('string_literals'),
            include('digraph_punctuation'),
            include('digraph_operators'),
            include('unigraph_punctuation'),
            include('unigraph_operators'),
        ]
    }

#  C o m m o n   D a t a s e t s

    # Common Reserved Words Dataset
    common_reserved_words = (
        # 37 common reserved words
        'AND', 'ARRAY', 'BEGIN', 'BY', 'CASE', 'CONST', 'DEFINITION', 'DIV',
        'DO', 'ELSE', 'ELSIF', 'END', 'EXIT', 'FOR', 'FROM', 'IF',
        'IMPLEMENTATION', 'IMPORT', 'IN', 'LOOP', 'MOD', 'MODULE', 'NOT',
        'OF', 'OR', 'POINTER', 'PROCEDURE', 'RECORD', 'REPEAT', 'RETURN',
        'SET', 'THEN', 'TO', 'TYPE', 'UNTIL', 'VAR', 'WHILE',
    )

    # Common Builtins Dataset
    common_builtins = (
        # 16 common builtins
        'ABS', 'BOOLEAN', 'CARDINAL', 'CHAR', 'CHR', 'FALSE', 'INTEGER',
        'LONGINT', 'LONGREAL', 'MAX', 'MIN', 'NIL', 'ODD', 'ORD', 'REAL',
        'TRUE',
    )

    # Common Pseudo-Module Builtins Dataset
    common_pseudo_builtins = (
        # 4 common pseudo builtins
        'ADDRESS', 'BYTE', 'WORD', 'ADR'
    )

#  P I M   M o d u l a - 2   D a t a s e t s

    # Lexemes to Mark as Error Tokens for PIM Modula-2
    pim_lexemes_to_reject = (
        '!', '`', '@', '$', '%', '?', '\\', '==', '++', '--', '::', '*.',
        '+>', '->', '<<', '>>', '|#', '##',
    )

    # PIM Modula-2 Additional Reserved Words Dataset
    pim_additional_reserved_words = (
        # 3 additional reserved words
        'EXPORT', 'QUALIFIED', 'WITH',
    )

    # PIM Modula-2 Additional Builtins Dataset
    pim_additional_builtins = (
        # 16 additional builtins
        'BITSET', 'CAP', 'DEC', 'DISPOSE', 'EXCL', 'FLOAT', 'HALT', 'HIGH',
        'INC', 'INCL', 'NEW', 'NIL', 'PROC', 'SIZE', 'TRUNC', 'VAL',
    )

    # PIM Modula-2 Additional Pseudo-Module Builtins Dataset
    pim_additional_pseudo_builtins = (
        # 5 additional pseudo builtins
        'SYSTEM', 'PROCESS', 'TSIZE', 'NEWPROCESS', 'TRANSFER',
    )

#  I S O   M o d u l a - 2   D a t a s e t s

    # Lexemes to Mark as Error Tokens for ISO Modula-2
    iso_lexemes_to_reject = (
        '`', '$', '%', '?', '\\', '==', '++', '--', '::', '*.', '+>', '->',
        '<<', '>>', '|#', '##',
    )

    # ISO Modula-2 Additional Reserved Words Dataset
    iso_additional_reserved_words = (
        # 9 additional reserved words (ISO 10514-1)
        'EXCEPT', 'EXPORT', 'FINALLY', 'FORWARD', 'PACKEDSET', 'QUALIFIED',
        'REM', 'RETRY', 'WITH',
        # 10 additional reserved words (ISO 10514-2 & ISO 10514-3)
        'ABSTRACT', 'AS', 'CLASS', 'GUARD', 'INHERIT', 'OVERRIDE', 'READONLY',
        'REVEAL', 'TRACED', 'UNSAFEGUARDED',
    )

    # ISO Modula-2 Additional Builtins Dataset
    iso_additional_builtins = (
        # 26 additional builtins (ISO 10514-1)
        'BITSET', 'CAP', 'CMPLX', 'COMPLEX', 'DEC', 'DISPOSE', 'EXCL', 'FLOAT',
        'HALT', 'HIGH', 'IM', 'INC', 'INCL', 'INT', 'INTERRUPTIBLE',  'LENGTH',
        'LFLOAT', 'LONGCOMPLEX', 'NEW', 'PROC', 'PROTECTION', 'RE', 'SIZE',
        'TRUNC', 'UNINTERRUBTIBLE', 'VAL',
        # 5 additional builtins (ISO 10514-2 & ISO 10514-3)
        'CREATE', 'DESTROY', 'EMPTY', 'ISMEMBER', 'SELF',
    )

    # ISO Modula-2 Additional Pseudo-Module Builtins Dataset
    iso_additional_pseudo_builtins = (
        # 14 additional builtins (SYSTEM)
        'SYSTEM', 'BITSPERLOC', 'LOCSPERBYTE', 'LOCSPERWORD', 'LOC',
        'ADDADR', 'SUBADR', 'DIFADR', 'MAKEADR', 'ADR',
        'ROTATE', 'SHIFT', 'CAST', 'TSIZE',
        # 13 additional builtins (COROUTINES)
        'COROUTINES', 'ATTACH', 'COROUTINE', 'CURRENT', 'DETACH', 'HANDLER',
        'INTERRUPTSOURCE', 'IOTRANSFER', 'IsATTACHED', 'LISTEN',
        'NEWCOROUTINE', 'PROT', 'TRANSFER',
        # 9 additional builtins (EXCEPTIONS)
        'EXCEPTIONS', 'AllocateSource', 'CurrentNumber', 'ExceptionNumber',
        'ExceptionSource', 'GetMessage', 'IsCurrentSource',
        'IsExceptionalExecution', 'RAISE',
        # 3 additional builtins (TERMINATION)
        'TERMINATION', 'IsTerminating', 'HasHalted',
        # 4 additional builtins (M2EXCEPTION)
        'M2EXCEPTION', 'M2Exceptions', 'M2Exception', 'IsM2Exception',
        'indexException', 'rangeException', 'caseSelectException',
        'invalidLocation', 'functionException', 'wholeValueException',
        'wholeDivException', 'realValueException', 'realDivException',
        'complexValueException', 'complexDivException', 'protException',
        'sysException', 'coException', 'exException',
    )

#  M o d u l a - 2   R 1 0   D a t a s e t s

    # Lexemes to Mark as Error Tokens for Modula-2 R10
    m2r10_lexemes_to_reject = (
        '!', '`', '@', '$', '%', '&', '<>',
    )

    # Modula-2 R10 reserved words in addition to the common set
    m2r10_additional_reserved_words = (
        # 12 additional reserved words
        'ALIAS', 'ARGLIST', 'BLUEPRINT', 'COPY', 'GENLIB', 'INDETERMINATE',
        'NEW', 'NONE', 'OPAQUE', 'REFERENTIAL', 'RELEASE', 'RETAIN',
        # 2 additional reserved words with symbolic assembly option
        'ASM', 'REG',
    )

