"""
    pygments.lexers.objective
    ~~~~~~~~~~~~~~~~~~~~~~~~~

    Lexers for Objective-C family languages.

    :copyright: Copyright 2006-present by the Pygments team, see AUTHORS.
    :license: BSD, see LICENSE for details.
"""

import re

from pygments.lexer import RegexLexer, include, bygroups, using, this, words, \
    inherit, default
from pygments.token import Text, Keyword, Name, String, Operator, \
    Number, Punctuation, Literal, Comment, Whitespace

from pygments.lexers.c_cpp import CLexer, CppLexer

__all__ = ['ObjectiveCLexer', 'ObjectiveCppLexer', 'LogosLexer', 'SwiftLexer']


def objective(baselexer):
    """
    Generate a subclass of baselexer that accepts the Objective-C syntax
    extensions.
    """

    # Have to be careful not to accidentally match JavaDoc/Doxygen syntax here,
    # since that's quite common in ordinary C/C++ files.  It's OK to match
    # JavaDoc/Doxygen keywords that only apply to Objective-C, mind.
    #
    # The upshot of this is that we CANNOT match @class or @interface
    _oc_keywords = re.compile(r'@(?:end|implementation|protocol)')

    # Matches [ <ws>? identifier <ws> ( identifier <ws>? ] |  identifier? : )
    # (note the identifier is *optional* when there is a ':'!)
    _oc_message = re.compile(r'\[\s*[a-zA-Z_]\w*\s+'
                             r'(?:[a-zA-Z_]\w*\s*\]|'
                             r'(?:[a-zA-Z_]\w*)?:)')

    class GeneratedObjectiveCVariant(baselexer):
        """
        Implements Objective-C syntax on top of an existing C family lexer.
        """

        tokens = {
            'statements': [
                (r'@"', String, 'string'),
                (r'@(YES|NO)', Number),
                (r"@'(\\.|\\[0-7]{1,3}|\\x[a-fA-F0-9]{1,2}|[^\\\'\n])'", String.Char),
                (r'@(\d+\.\d*|\.\d+|\d+)[eE][+-]?\d+[lL]?', Number.Float),
                (r'@(\d+\.\d*|\.\d+|\d+[fF])[fF]?', Number.Float),
                (r'@0x[0-9a-fA-F]+[Ll]?', Number.Hex),
                (r'@0[0-7]+[Ll]?', Number.Oct),
                (r'@\d+[Ll]?', Number.Integer),
                (r'@\(', Literal, 'literal_number'),
                (r'@\[', Literal, 'literal_array'),
                (r'@\{', Literal, 'literal_dictionary'),
                (words((
                    '@selector', '@private', '@protected', '@public', '@encode',
                    '@synchronized', '@try', '@throw', '@catch', '@finally',
                    '@end', '@property', '@synthesize', '__bridge', '__bridge_transfer',
                    '__autoreleasing', '__block', '__weak', '__strong', 'weak', 'strong',
                    'copy', 'retain', 'assign', 'unsafe_unretained', 'atomic', 'nonatomic',
                    'readonly', 'readwrite', 'setter', 'getter', 'typeof', 'in',
                    'out', 'inout', 'release', 'class', '@dynamic', '@optional',
                    '@required', '@autoreleasepool', '@import'), suffix=r'\b'),
                 Keyword),
                (words(('id', 'instancetype', 'Class', 'IMP', 'SEL', 'BOOL',
                        'IBOutlet', 'IBAction', 'unichar'), suffix=r'\b'),
                 Keyword.Type),
                (r'@(true|false|YES|NO)\n', Name.Builtin),
                (r'(YES|NO|nil|self|super)\b', Name.Builtin),
                # Carbon types
                (r'(Boolean|UInt8|SInt8|UInt16|SInt16|UInt32|SInt32)\b', Keyword.Type),
                # Carbon built-ins
                (r'(TRUE|FALSE)\b', Name.Builtin),
                (r'(@interface|@implementation)(\s+)', bygroups(Keyword, Text),
                 ('#pop', 'oc_classname')),
                (r'(@class|@protocol)(\s+)', bygroups(Keyword, Text),
                 ('#pop', 'oc_forward_classname')),
                # @ can also prefix other expressions like @{...} or @(...)
                (r'@', Punctuation),
                inherit,
            ],
            'oc_classname': [
                # interface definition that inherits
                (r'([a-zA-Z$_][\w$]*)(\s*:\s*)([a-zA-Z$_][\w$]*)?(\s*)(\{)',
                 bygroups(Name.Class, Text, Name.Class, Text, Punctuation),
                 ('#pop', 'oc_ivars')),
                (r'([a-zA-Z$_][\w$]*)(\s*:\s*)([a-zA-Z$_][\w$]*)?',
                 bygroups(Name.Class, Text, Name.Class), '#pop'),
                # interface definition for a category
                (r'([a-zA-Z$_][\w$]*)(\s*)(\([a-zA-Z$_][\w$]*\))(\s*)(\{)',
                 bygroups(Name.Class, Text, Name.Label, Text, Punctuation),
                 ('#pop', 'oc_ivars')),
                (r'([a-zA-Z$_][\w$]*)(\s*)(\([a-zA-Z$_][\w$]*\))',
                 bygroups(Name.Class, Text, Name.Label), '#pop'),
                # simple interface / implementation
                (r'([a-zA-Z$_][\w$]*)(\s*)(\{)',
                 bygroups(Name.Class, Text, Punctuation), ('#pop', 'oc_ivars')),
                (r'([a-zA-Z$_][\w$]*)', Name.Class, '#pop')
            ],
            'oc_forward_classname': [
                (r'([a-zA-Z$_][\w$]*)(\s*,\s*)',
                 bygroups(Name.Class, Text), 'oc_forward_classname'),
                (r'([a-zA-Z$_][\w$]*)(\s*;?)',
                 bygroups(Name.Class, Text), '#pop')
            ],
            'oc_ivars': [
                include('whitespace'),
                include('statements'),
                (';', Punctuation),
                (r'\{', Punctuation, '#push'),
                (r'\}', Punctuation, '#pop'),
            ],
            'root': [
                # methods
                (r'^([-+])(\s*)'                         # method marker
                 r'(\(.*?\))?(\s*)'                      # return type
                 r'([a-zA-Z$_][\w$]*:?)',        # begin of method name
                 bygroups(Punctuation, Text, using(this),
                          Text, Name.Function),
                 'method'),
                inherit,
            ],
            'method': [
                include('whitespace'),
                # TODO unsure if ellipses are allowed elsewhere, see
                # discussion in Issue 789
                (r',', Punctuation),
                (r'\.\.\.', Punctuation),
                (r'(\(.*?\))(\s*)([a-zA-Z$_][\w$]*)',
                 bygroups(using(this), Text, Name.Variable)),
                (r'[a-zA-Z$_][\w$]*:', Name.Function),
                (';', Punctuation, '#pop'),
                (r'\{', Punctuation, 'function'),
                default('#pop'),
            ],
            'literal_number': [
                (r'\(', Punctuation, 'literal_number_inner'),
                (r'\)', Literal, '#pop'),
                include('statement'),
            ],
            'literal_number_inner': [
                (r'\(', Punctuation, '#push'),
                (r'\)', Punctuation, '#pop'),
                include('statement'),
            ],
            'literal_array': [
                (r'\[', Punctuation, 'literal_array_inner'),
                (r'\]', Literal, '#pop'),
                include('statement'),
            ],
            'literal_array_inner': [
                (r'\[', Punctuation, '#push'),
                (r'\]', Punctuation, '#pop'),
                include('statement'),
            ],
            'literal_dictionary': [
                (r'\}', Literal, '#pop'),
                include('statement'),
            ],
        }

        def analyse_text(text):
            if _oc_keywords.search(text):
                return 1.0
            elif '@"' in text:  # strings
                return 0.8
            elif re.search('@[0-9]+', text):
                return 0.7
            elif _oc_message.search(text):
                return 0.8
            return 0

        def get_tokens_unprocessed(self, text, stack=('root',)):
            from pygments.lexers._cocoa_builtins import COCOA_INTERFACES, \
                COCOA_PROTOCOLS, COCOA_PRIMITIVES

            for index, token, value in \
                    baselexer.get_tokens_unprocessed(self, text, stack):
                if token is Name or token is Name.Class:
                    if value in COCOA_INTERFACES or value in COCOA_PROTOCOLS \
                       or value in COCOA_PRIMITIVES:
                        token = Name.Builtin.Pseudo

                yield index, token, value

    return GeneratedObjectiveCVariant


class ObjectiveCLexer(objective(CLexer)):
    """
    For Objective-C source code with preprocessor directives.
    """

    name = 'Objective-C'
    url = 'https://developer.apple.com/library/archive/documentation/Cocoa/Conceptual/ProgrammingWithObjectiveC/Introduction/Introduction.html'
    aliases = ['objective-c', 'objectivec', 'obj-c', 'objc']
    filenames = ['*.m', '*.h']
    mimetypes = ['text/x-objective-c']
    version_added = ''
    priority = 0.05    # Lower than C


class ObjectiveCppLexer(objective(CppLexer)):
    """
    For Objective-C++ source code with preprocessor directives.
    """

    name = 'Objective-C++'
    aliases = ['objective-c++', 'objectivec++', 'obj-c++', 'objc++']
    filenames = ['*.mm', '*.hh']
    mimetypes = ['text/x-objective-c++']
    version_added = ''
    priority = 0.05    # Lower than C++


class LogosLexer(ObjectiveCppLexer):
    """
    For Logos + Objective-C source code with preprocessor directives.
    """

    name = 'Logos'
    aliases = ['logos']
    filenames = ['*.x', '*.xi', '*.xm', '*.xmi']
    mimetypes = ['text/x-logos']
    version_added = '1.6'
    priority = 0.25

    tokens = {
        'statements': [
            (r'(%orig|%log)\b', Keyword),
            (r'(%c)\b(\()(\s*)([a-zA-Z$_][\w$]*)(\s*)(\))',
             bygroups(Keyword, Punctuation, Text, Name.Class, Text, Punctuation)),
            (r'(%init)\b(\()',
             bygroups(Keyword, Punctuation), 'logos_init_directive'),
            (r'(%init)(?=\s*;)', bygroups(Keyword)),
            (r'(%hook|%group)(\s+)([a-zA-Z$_][\w$]+)',
             bygroups(Keyword, Text, Name.Class), '#pop'),
            (r'(%subclass)(\s+)', bygroups(Keyword, Text),
             ('#pop', 'logos_classname')),
            inherit,
        ],
        'logos_init_directive': [
            (r'\s+', Text),
            (',', Punctuation, ('logos_init_directive', '#pop')),
            (r'([a-zA-Z$_][\w$]*)(\s*)(=)(\s*)([^);]*)',
             bygroups(Name.Class, Text, Punctuation, Text, Text)),
            (r'([a-zA-Z$_][\w$]*)', Name.Class),
            (r'\)', Punctuation, '#pop'),
        ],
        'logos_classname': [
            (r'([a-zA-Z$_][\w$]*)(\s*:\s*)([a-zA-Z$_][\w$]*)?',
             bygroups(Name.Class, Text, Name.Class), '#pop'),
            (r'([a-zA-Z$_][\w$]*)', Name.Class, '#pop')
        ],
        'root': [
            (r'(%subclass)(\s+)', bygroups(Keyword, Text),
             'logos_classname'),
            (r'(%hook|%group)(\s+)([a-zA-Z$_][\w$]+)',
             bygroups(Keyword, Text, Name.Class)),
            (r'(%config)(\s*\(\s*)(\w+)(\s*=)(.*?)(\)\s*)',
             bygroups(Keyword, Text, Name.Variable, Text, String, Text)),
            (r'(%ctor)(\s*)(\{)', bygroups(Keyword, Text, Punctuation),
             'function'),
            (r'(%new)(\s*)(\()(.*?)(\))',
             bygroups(Keyword, Text, Keyword, String, Keyword)),
            (r'(\s*)(%end)(\s*)', bygroups(Text, Keyword, Text)),
            inherit,
        ],
    }

    _logos_keywords = re.compile(r'%(?:hook|ctor|init|c\()')

    def analyse_text(text):
        if LogosLexer._logos_keywords.search(text):
            return 1.0
        return 0


class SwiftLexer(RegexLexer):
    """
    For Swift source.
    """
    name = 'Swift'
    url = 'https://www.swift.org/'
    filenames = ['*.swift']
    aliases = ['swift']
    mimetypes = ['text/x-swift']
    version_added = '2.0'

    tokens = {
        'root': [
            # Whitespace and Comments
            (r'\n', Text),
            (r'\s+', Whitespace),
            (r'//', Comment.Single, 'comment-single'),
            (r'/\*', Comment.Multiline, 'comment-multi'),
            (r'#(if|elseif|else|endif|available)\b', Comment.Preproc, 'preproc'),

            # Keywords
            include('keywords'),

            # Global Types
            (words((
                'Array', 'AutoreleasingUnsafeMutablePointer', 'BidirectionalReverseView',
                'Bit', 'Bool', 'CFunctionPointer', 'COpaquePointer', 'CVaListPointer',
                'Character', 'ClosedInterval', 'CollectionOfOne', 'ContiguousArray',
                'Dictionary', 'DictionaryGenerator', 'DictionaryIndex', 'Double',
                'EmptyCollection', 'EmptyGenerator', 'EnumerateGenerator',
                'EnumerateSequence', 'FilterCollectionView',
                'FilterCollectionViewIndex', 'FilterGenerator', 'FilterSequenceView',
                'Float', 'Float80', 'FloatingPointClassification', 'GeneratorOf',
                'GeneratorOfOne', 'GeneratorSequence', 'HalfOpenInterval', 'HeapBuffer',
                'HeapBufferStorage', 'ImplicitlyUnwrappedOptional', 'IndexingGenerator',
                'Int', 'Int16', 'Int32', 'Int64', 'Int8', 'LazyBidirectionalCollection',
                'LazyForwardCollection', 'LazyRandomAccessCollection',
                'LazySequence', 'MapCollectionView', 'MapSequenceGenerator',
                'MapSequenceView', 'MirrorDisposition', 'ObjectIdentifier', 'OnHeap',
                'Optional', 'PermutationGenerator', 'QuickLookObject',
                'RandomAccessReverseView', 'Range', 'RangeGenerator', 'RawByte', 'Repeat',
                'ReverseBidirectionalIndex', 'ReverseRandomAccessIndex', 'SequenceOf',
                'SinkOf', 'Slice', 'StaticString', 'StrideThrough', 'StrideThroughGenerator',
                'StrideTo', 'StrideToGenerator', 'String', 'UInt', 'UInt16', 'UInt32',
                'UInt64', 'UInt8', 'UTF16', 'UTF32', 'UTF8', 'UnicodeDecodingResult',
                'UnicodeScalar', 'Unmanaged', 'UnsafeBufferPointer',
                'UnsafeBufferPointerGenerator', 'UnsafeMutableBufferPointer',
                'UnsafeMutablePointer', 'UnsafePointer', 'Zip2', 'ZipGenerator2',
                # Protocols
                'AbsoluteValuable', 'AnyObject', 'ArrayLiteralConvertible',
                'BidirectionalIndexType', 'BitwiseOperationsType',
                'BooleanLiteralConvertible', 'BooleanType', 'CVarArgType',
                'CollectionType', 'Comparable', 'DebugPrintable',
                'DictionaryLiteralConvertible', 'Equatable',
                'ExtendedGraphemeClusterLiteralConvertible',
                'ExtensibleCollectionType', 'FloatLiteralConvertible',
                'FloatingPointType', 'ForwardIndexType', 'GeneratorType', 'Hashable',
                'IntegerArithmeticType', 'IntegerLiteralConvertible', 'IntegerType',
                'IntervalType', 'MirrorType', 'MutableCollectionType', 'MutableSliceable',
                'NilLiteralConvertible', 'OutputStreamType', 'Printable',
                'RandomAccessIndexType', 'RangeReplaceableCollectionType',
                'RawOptionSetType', 'RawRepresentable', 'Reflectable', 'SequenceType',
                'SignedIntegerType', 'SignedNumberType', 'SinkType', 'Sliceable',
                'Streamable', 'Strideable', 'StringInterpolationConvertible',
                'StringLiteralConvertible', 'UnicodeCodecType',
                'UnicodeScalarLiteralConvertible', 'UnsignedIntegerType',
                '_ArrayBufferType', '_BidirectionalIndexType', '_CocoaStringType',
                '_CollectionType', '_Comparable', '_ExtensibleCollectionType',
                '_ForwardIndexType', '_Incrementable', '_IntegerArithmeticType',
                '_IntegerType', '_ObjectiveCBridgeable', '_RandomAccessIndexType',
                '_RawOptionSetType', '_SequenceType', '_Sequence_Type',
                '_SignedIntegerType', '_SignedNumberType', '_Sliceable', '_Strideable',
                '_SwiftNSArrayRequiredOverridesType', '_SwiftNSArrayType',
                '_SwiftNSCopyingType', '_SwiftNSDictionaryRequiredOverridesType',
                '_SwiftNSDictionaryType', '_SwiftNSEnumeratorType',
                '_SwiftNSFastEnumerationType', '_SwiftNSStringRequiredOverridesType',
                '_SwiftNSStringType', '_UnsignedIntegerType',
                # Variables
                'C_ARGC', 'C_ARGV', 'Process',
                # Typealiases
                'Any', 'AnyClass', 'BooleanLiteralType', 'CBool', 'CChar', 'CChar16',
