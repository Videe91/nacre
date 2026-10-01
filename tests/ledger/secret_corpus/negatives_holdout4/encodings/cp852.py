""" Python Character Mapping Codec generated from 'VENDORS/MICSFT/PC/CP852.TXT' with gencodec.py.

"""#"

import codecs

### Codec APIs

class Codec(codecs.Codec):

    def encode(self,input,errors='strict'):
        return codecs.charmap_encode(input,errors,encoding_map)

    def decode(self,input,errors='strict'):
        return codecs.charmap_decode(input,errors,decoding_table)

class IncrementalEncoder(codecs.IncrementalEncoder):
    def encode(self, input, final=False):
        return codecs.charmap_encode(input,self.errors,encoding_map)[0]

class IncrementalDecoder(codecs.IncrementalDecoder):
    def decode(self, input, final=False):
        return codecs.charmap_decode(input,self.errors,decoding_table)[0]

class StreamWriter(Codec,codecs.StreamWriter):
    pass

class StreamReader(Codec,codecs.StreamReader):
    pass

### encodings module API

def getregentry():
    return codecs.CodecInfo(
        name='cp852',
        encode=Codec().encode,
        decode=Codec().decode,
        incrementalencoder=IncrementalEncoder,
        incrementaldecoder=IncrementalDecoder,
        streamreader=StreamReader,
        streamwriter=StreamWriter,
    )

### Decoding Map

decoding_map = codecs.make_identity_dict(range(256))
decoding_map.update({
    0x0080: 0x00c7,     #  LATIN CAPITAL LETTER C WITH CEDILLA
    0x0081: 0x00fc,     #  LATIN SMALL LETTER U WITH DIAERESIS
    0x0082: 0x00e9,     #  LATIN SMALL LETTER E WITH ACUTE
    0x0083: 0x00e2,     #  LATIN SMALL LETTER A WITH CIRCUMFLEX
    0x0084: 0x00e4,     #  LATIN SMALL LETTER A WITH DIAERESIS
    0x0085: 0x016f,     #  LATIN SMALL LETTER U WITH RING ABOVE
    0x0086: 0x0107,     #  LATIN SMALL LETTER C WITH ACUTE
    0x0087: 0x00e7,     #  LATIN SMALL LETTER C WITH CEDILLA
    0x0088: 0x0142,     #  LATIN SMALL LETTER L WITH STROKE
    0x0089: 0x00eb,     #  LATIN SMALL LETTER E WITH DIAERESIS
    0x008a: 0x0150,     #  LATIN CAPITAL LETTER O WITH DOUBLE ACUTE
    0x008b: 0x0151,     #  LATIN SMALL LETTER O WITH DOUBLE ACUTE
    0x008c: 0x00ee,     #  LATIN SMALL LETTER I WITH CIRCUMFLEX
    0x008d: 0x0179,     #  LATIN CAPITAL LETTER Z WITH ACUTE
    0x008e: 0x00c4,     #  LATIN CAPITAL LETTER A WITH DIAERESIS
    0x008f: 0x0106,     #  LATIN CAPITAL LETTER C WITH ACUTE
    0x0090: 0x00c9,     #  LATIN CAPITAL LETTER E WITH ACUTE
    0x0091: 0x0139,     #  LATIN CAPITAL LETTER L WITH ACUTE
    0x0092: 0x013a,     #  LATIN SMALL LETTER L WITH ACUTE
    0x0093: 0x00f4,     #  LATIN SMALL LETTER O WITH CIRCUMFLEX
    0x0094: 0x00f6,     #  LATIN SMALL LETTER O WITH DIAERESIS
    0x0095: 0x013d,     #  LATIN CAPITAL LETTER L WITH CARON
    0x0096: 0x013e,     #  LATIN SMALL LETTER L WITH CARON
    0x0097: 0x015a,     #  LATIN CAPITAL LETTER S WITH ACUTE
    0x0098: 0x015b,     #  LATIN SMALL LETTER S WITH ACUTE
    0x0099: 0x00d6,     #  LATIN CAPITAL LETTER O WITH DIAERESIS
    0x009a: 0x00dc,     #  LATIN CAPITAL LETTER U WITH DIAERESIS
    0x009b: 0x0164,     #  LATIN CAPITAL LETTER T WITH CARON
    0x009c: 0x0165,     #  LATIN SMALL LETTER T WITH CARON
    0x009d: 0x0141,     #  LATIN CAPITAL LETTER L WITH STROKE
    0x009e: 0x00d7,     #  MULTIPLICATION SIGN
    0x009f: 0x010d,     #  LATIN SMALL LETTER C WITH CARON
    0x00a0: 0x00e1,     #  LATIN SMALL LETTER A WITH ACUTE
    0x00a1: 0x00ed,     #  LATIN SMALL LETTER I WITH ACUTE
    0x00a2: 0x00f3,     #  LATIN SMALL LETTER O WITH ACUTE
    0x00a3: 0x00fa,     #  LATIN SMALL LETTER U WITH ACUTE
    0x00a4: 0x0104,     #  LATIN CAPITAL LETTER A WITH OGONEK
    0x00a5: 0x0105,     #  LATIN SMALL LETTER A WITH OGONEK
    0x00a6: 0x017d,     #  LATIN CAPITAL LETTER Z WITH CARON
    0x00a7: 0x017e,     #  LATIN SMALL LETTER Z WITH CARON
    0x00a8: 0x0118,     #  LATIN CAPITAL LETTER E WITH OGONEK
    0x00a9: 0x0119,     #  LATIN SMALL LETTER E WITH OGONEK
    0x00aa: 0x00ac,     #  NOT SIGN
    0x00ab: 0x017a,     #  LATIN SMALL LETTER Z WITH ACUTE
    0x00ac: 0x010c,     #  LATIN CAPITAL LETTER C WITH CARON
    0x00ad: 0x015f,     #  LATIN SMALL LETTER S WITH CEDILLA
    0x00ae: 0x00ab,     #  LEFT-POINTING DOUBLE ANGLE QUOTATION MARK
    0x00af: 0x00bb,     #  RIGHT-POINTING DOUBLE ANGLE QUOTATION MARK
    0x00b0: 0x2591,     #  LIGHT SHADE
    0x00b1: 0x2592,     #  MEDIUM SHADE
    0x00b2: 0x2593,     #  DARK SHADE
    0x00b3: 0x2502,     #  BOX DRAWINGS LIGHT VERTICAL
    0x00b4: 0x2524,     #  BOX DRAWINGS LIGHT VERTICAL AND LEFT
    0x00b5: 0x00c1,     #  LATIN CAPITAL LETTER A WITH ACUTE
    0x00b6: 0x00c2,     #  LATIN CAPITAL LETTER A WITH CIRCUMFLEX
    0x00b7: 0x011a,     #  LATIN CAPITAL LETTER E WITH CARON
    0x00b8: 0x015e,     #  LATIN CAPITAL LETTER S WITH CEDILLA
    0x00b9: 0x2563,     #  BOX DRAWINGS DOUBLE VERTICAL AND LEFT
    0x00ba: 0x2551,     #  BOX DRAWINGS DOUBLE VERTICAL
    0x00bb: 0x2557,     #  BOX DRAWINGS DOUBLE DOWN AND LEFT
    0x00bc: 0x255d,     #  BOX DRAWINGS DOUBLE UP AND LEFT
    0x00bd: 0x017b,     #  LATIN CAPITAL LETTER Z WITH DOT ABOVE
    0x00be: 0x017c,     #  LATIN SMALL LETTER Z WITH DOT ABOVE
    0x00bf: 0x2510,     #  BOX DRAWINGS LIGHT DOWN AND LEFT
    0x00c0: 0x2514,     #  BOX DRAWINGS LIGHT UP AND RIGHT
    0x00c1: 0x2534,     #  BOX DRAWINGS LIGHT UP AND HORIZONTAL
    0x00c2: 0x252c,     #  BOX DRAWINGS LIGHT DOWN AND HORIZONTAL
    0x00c3: 0x251c,     #  BOX DRAWINGS LIGHT VERTICAL AND RIGHT
    0x00c4: 0x2500,     #  BOX DRAWINGS LIGHT HORIZONTAL
    0x00c5: 0x253c,     #  BOX DRAWINGS LIGHT VERTICAL AND HORIZONTAL
    0x00c6: 0x0102,     #  LATIN CAPITAL LETTER A WITH BREVE
    0x00c7: 0x0103,     #  LATIN SMALL LETTER A WITH BREVE
    0x00c8: 0x255a,     #  BOX DRAWINGS DOUBLE UP AND RIGHT
    0x00c9: 0x2554,     #  BOX DRAWINGS DOUBLE DOWN AND RIGHT
    0x00ca: 0x2569,     #  BOX DRAWINGS DOUBLE UP AND HORIZONTAL
    0x00cb: 0x2566,     #  BOX DRAWINGS DOUBLE DOWN AND HORIZONTAL
    0x00cc: 0x2560,     #  BOX DRAWINGS DOUBLE VERTICAL AND RIGHT
    0x00cd: 0x2550,     #  BOX DRAWINGS DOUBLE HORIZONTAL
    0x00ce: 0x256c,     #  BOX DRAWINGS DOUBLE VERTICAL AND HORIZONTAL
    0x00cf: 0x00a4,     #  CURRENCY SIGN
    0x00d0: 0x0111,     #  LATIN SMALL LETTER D WITH STROKE
    0x00d1: 0x0110,     #  LATIN CAPITAL LETTER D WITH STROKE
    0x00d2: 0x010e,     #  LATIN CAPITAL LETTER D WITH CARON
    0x00d3: 0x00cb,     #  LATIN CAPITAL LETTER E WITH DIAERESIS
    0x00d4: 0x010f,     #  LATIN SMALL LETTER D WITH CARON
    0x00d5: 0x0147,     #  LATIN CAPITAL LETTER N WITH CARON
    0x00d6: 0x00cd,     #  LATIN CAPITAL LETTER I WITH ACUTE
    0x00d7: 0x00ce,     #  LATIN CAPITAL LETTER I WITH CIRCUMFLEX
    0x00d8: 0x011b,     #  LATIN SMALL LETTER E WITH CARON
    0x00d9: 0x2518,     #  BOX DRAWINGS LIGHT UP AND LEFT
    0x00da: 0x250c,     #  BOX DRAWINGS LIGHT DOWN AND RIGHT
    0x00db: 0x2588,     #  FULL BLOCK
    0x00dc: 0x2584,     #  LOWER HALF BLOCK
    0x00dd: 0x0162,     #  LATIN CAPITAL LETTER T WITH CEDILLA
    0x00de: 0x016e,     #  LATIN CAPITAL LETTER U WITH RING ABOVE
    0x00df: 0x2580,     #  UPPER HALF BLOCK
    0x00e0: 0x00d3,     #  LATIN CAPITAL LETTER O WITH ACUTE
    0x00e1: 0x00df,     #  LATIN SMALL LETTER SHARP S
    0x00e2: 0x00d4,     #  LATIN CAPITAL LETTER O WITH CIRCUMFLEX
    0x00e3: 0x0143,     #  LATIN CAPITAL LETTER N WITH ACUTE
    0x00e4: 0x0144,     #  LATIN SMALL LETTER N WITH ACUTE
    0x00e5: 0x0148,     #  LATIN SMALL LETTER N WITH CARON
    0x00e6: 0x0160,     #  LATIN CAPITAL LETTER S WITH CARON
    0x00e7: 0x0161,     #  LATIN SMALL LETTER S WITH CARON
    0x00e8: 0x0154,     #  LATIN CAPITAL LETTER R WITH ACUTE
    0x00e9: 0x00da,     #  LATIN CAPITAL LETTER U WITH ACUTE
    0x00ea: 0x0155,     #  LATIN SMALL LETTER R WITH ACUTE
    0x00eb: 0x0170,     #  LATIN CAPITAL LETTER U WITH DOUBLE ACUTE
    0x00ec: 0x00fd,     #  LATIN SMALL LETTER Y WITH ACUTE
    0x00ed: 0x00dd,     #  LATIN CAPITAL LETTER Y WITH ACUTE
    0x00ee: 0x0163,     #  LATIN SMALL LETTER T WITH CEDILLA
    0x00ef: 0x00b4,     #  ACUTE ACCENT
    0x00f0: 0x00ad,     #  SOFT HYPHEN
    0x00f1: 0x02dd,     #  DOUBLE ACUTE ACCENT
    0x00f2: 0x02db,     #  OGONEK
    0x00f3: 0x02c7,     #  CARON
    0x00f4: 0x02d8,     #  BREVE
    0x00f5: 0x00a7,     #  SECTION SIGN
    0x00f6: 0x00f7,     #  DIVISION SIGN
    0x00f7: 0x00b8,     #  CEDILLA
    0x00f8: 0x00b0,     #  DEGREE SIGN
    0x00f9: 0x00a8,     #  DIAERESIS
    0x00fa: 0x02d9,     #  DOT ABOVE
    0x00fb: 0x0171,     #  LATIN SMALL LETTER U WITH DOUBLE ACUTE
    0x00fc: 0x0158,     #  LATIN CAPITAL LETTER R WITH CARON
    0x00fd: 0x0159,     #  LATIN SMALL LETTER R WITH CARON
    0x00fe: 0x25a0,     #  BLACK SQUARE
    0x00ff: 0x00a0,     #  NO-BREAK SPACE
})

### Decoding Table

decoding_table = (
    '\x00'     #  0x0000 -> NULL
    '\x01'     #  0x0001 -> START OF HEADING
    '\x02'     #  0x0002 -> START OF TEXT
    '\x03'     #  0x0003 -> END OF TEXT
    '\x04'     #  0x0004 -> END OF TRANSMISSION
    '\x05'     #  0x0005 -> ENQUIRY
    '\x06'     #  0x0006 -> ACKNOWLEDGE
    '\x07'     #  0x0007 -> BELL
    '\x08'     #  0x0008 -> BACKSPACE
    '\t'       #  0x0009 -> HORIZONTAL TABULATION
    '\n'       #  0x000a -> LINE FEED
    '\x0b'     #  0x000b -> VERTICAL TABULATION
    '\x0c'     #  0x000c -> FORM FEED
    '\r'       #  0x000d -> CARRIAGE RETURN
    '\x0e'     #  0x000e -> SHIFT OUT
    '\x0f'     #  0x000f -> SHIFT IN
    '\x10'     #  0x0010 -> DATA LINK ESCAPE
    '\x11'     #  0x0011 -> DEVICE CONTROL ONE
    '\x12'     #  0x0012 -> DEVICE CONTROL TWO
    '\x13'     #  0x0013 -> DEVICE CONTROL THREE
    '\x14'     #  0x0014 -> DEVICE CONTROL FOUR
    '\x15'     #  0x0015 -> NEGATIVE ACKNOWLEDGE
    '\x16'     #  0x0016 -> SYNCHRONOUS IDLE
    '\x17'     #  0x0017 -> END OF TRANSMISSION BLOCK
    '\x18'     #  0x0018 -> CANCEL
    '\x19'     #  0x0019 -> END OF MEDIUM
    '\x1a'     #  0x001a -> SUBSTITUTE
    '\x1b'     #  0x001b -> ESCAPE
    '\x1c'     #  0x001c -> FILE SEPARATOR
    '\x1d'     #  0x001d -> GROUP SEPARATOR
    '\x1e'     #  0x001e -> RECORD SEPARATOR
    '\x1f'     #  0x001f -> UNIT SEPARATOR
    ' '        #  0x0020 -> SPACE
    '!'        #  0x0021 -> EXCLAMATION MARK
    '"'        #  0x0022 -> QUOTATION MARK
    '#'        #  0x0023 -> NUMBER SIGN
    '$'        #  0x0024 -> DOLLAR SIGN
    '%'        #  0x0025 -> PERCENT SIGN
    '&'        #  0x0026 -> AMPERSAND
    "'"        #  0x0027 -> APOSTROPHE
    '('        #  0x0028 -> LEFT PARENTHESIS
    ')'        #  0x0029 -> RIGHT PARENTHESIS
    '*'        #  0x002a -> ASTERISK
    '+'        #  0x002b -> PLUS SIGN
    ','        #  0x002c -> COMMA
    '-'        #  0x002d -> HYPHEN-MINUS
    '.'        #  0x002e -> FULL STOP
    '/'        #  0x002f -> SOLIDUS
    '0'        #  0x0030 -> DIGIT ZERO
    '1'        #  0x0031 -> DIGIT ONE
    '2'        #  0x0032 -> DIGIT TWO
    '3'        #  0x0033 -> DIGIT THREE
    '4'        #  0x0034 -> DIGIT FOUR
    '5'        #  0x0035 -> DIGIT FIVE
    '6'        #  0x0036 -> DIGIT SIX
    '7'        #  0x0037 -> DIGIT SEVEN
    '8'        #  0x0038 -> DIGIT EIGHT
    '9'        #  0x0039 -> DIGIT NINE
    ':'        #  0x003a -> COLON
    ';'        #  0x003b -> SEMICOLON
    '<'        #  0x003c -> LESS-THAN SIGN
    '='        #  0x003d -> EQUALS SIGN
    '>'        #  0x003e -> GREATER-THAN SIGN
    '?'        #  0x003f -> QUESTION MARK
    '@'        #  0x0040 -> COMMERCIAL AT
    'A'        #  0x0041 -> LATIN CAPITAL LETTER A
    'B'        #  0x0042 -> LATIN CAPITAL LETTER B
    'C'        #  0x0043 -> LATIN CAPITAL LETTER C
    'D'        #  0x0044 -> LATIN CAPITAL LETTER D
    'E'        #  0x0045 -> LATIN CAPITAL LETTER E
    'F'        #  0x0046 -> LATIN CAPITAL LETTER F
    'G'        #  0x0047 -> LATIN CAPITAL LETTER G
    'H'        #  0x0048 -> LATIN CAPITAL LETTER H
    'I'        #  0x0049 -> LATIN CAPITAL LETTER I
    'J'        #  0x004a -> LATIN CAPITAL LETTER J
    'K'        #  0x004b -> LATIN CAPITAL LETTER K
    'L'        #  0x004c -> LATIN CAPITAL LETTER L
    'M'        #  0x004d -> LATIN CAPITAL LETTER M
    'N'        #  0x004e -> LATIN CAPITAL LETTER N
    'O'        #  0x004f -> LATIN CAPITAL LETTER O
    'P'        #  0x0050 -> LATIN CAPITAL LETTER P
    'Q'        #  0x0051 -> LATIN CAPITAL LETTER Q
    'R'        #  0x0052 -> LATIN CAPITAL LETTER R
    'S'        #  0x0053 -> LATIN CAPITAL LETTER S
    'T'        #  0x0054 -> LATIN CAPITAL LETTER T
    'U'        #  0x0055 -> LATIN CAPITAL LETTER U
    'V'        #  0x0056 -> LATIN CAPITAL LETTER V
    'W'        #  0x0057 -> LATIN CAPITAL LETTER W
    'X'        #  0x0058 -> LATIN CAPITAL LETTER X
    'Y'        #  0x0059 -> LATIN CAPITAL LETTER Y
    'Z'        #  0x005a -> LATIN CAPITAL LETTER Z
    '['        #  0x005b -> LEFT SQUARE BRACKET
    '\\'       #  0x005c -> REVERSE SOLIDUS
    ']'        #  0x005d -> RIGHT SQUARE BRACKET
    '^'        #  0x005e -> CIRCUMFLEX ACCENT
    '_'        #  0x005f -> LOW LINE
    '`'        #  0x0060 -> GRAVE ACCENT
    'a'        #  0x0061 -> LATIN SMALL LETTER A
    'b'        #  0x0062 -> LATIN SMALL LETTER B
    'c'        #  0x0063 -> LATIN SMALL LETTER C
    'd'        #  0x0064 -> LATIN SMALL LETTER D
    'e'        #  0x0065 -> LATIN SMALL LETTER E
    'f'        #  0x0066 -> LATIN SMALL LETTER F
    'g'        #  0x0067 -> LATIN SMALL LETTER G
    'h'        #  0x0068 -> LATIN SMALL LETTER H
    'i'        #  0x0069 -> LATIN SMALL LETTER I
    'j'        #  0x006a -> LATIN SMALL LETTER J
    'k'        #  0x006b -> LATIN SMALL LETTER K
    'l'        #  0x006c -> LATIN SMALL LETTER L
    'm'        #  0x006d -> LATIN SMALL LETTER M
    'n'        #  0x006e -> LATIN SMALL LETTER N
    'o'        #  0x006f -> LATIN SMALL LETTER O
    'p'        #  0x0070 -> LATIN SMALL LETTER P
    'q'        #  0x0071 -> LATIN SMALL LETTER Q
    'r'        #  0x0072 -> LATIN SMALL LETTER R
    's'        #  0x0073 -> LATIN SMALL LETTER S
    't'        #  0x0074 -> LATIN SMALL LETTER T
    'u'        #  0x0075 -> LATIN SMALL LETTER U
    'v'        #  0x0076 -> LATIN SMALL LETTER V
    'w'        #  0x0077 -> LATIN SMALL LETTER W
    'x'        #  0x0078 -> LATIN SMALL LETTER X
    'y'        #  0x0079 -> LATIN SMALL LETTER Y
    'z'        #  0x007a -> LATIN SMALL LETTER Z
    '{'        #  0x007b -> LEFT CURLY BRACKET
    '|'        #  0x007c -> VERTICAL LINE
    '}'        #  0x007d -> RIGHT CURLY BRACKET
    '~'        #  0x007e -> TILDE
    '\x7f'     #  0x007f -> DELETE
    '\xc7'     #  0x0080 -> LATIN CAPITAL LETTER C WITH CEDILLA
    '\xfc'     #  0x0081 -> LATIN SMALL LETTER U WITH DIAERESIS
    '\xe9'     #  0x0082 -> LATIN SMALL LETTER E WITH ACUTE
    '\xe2'     #  0x0083 -> LATIN SMALL LETTER A WITH CIRCUMFLEX
    '\xe4'     #  0x0084 -> LATIN SMALL LETTER A WITH DIAERESIS
    '\u016f'   #  0x0085 -> LATIN SMALL LETTER U WITH RING ABOVE
    '\u0107'   #  0x0086 -> LATIN SMALL LETTER C WITH ACUTE
    '\xe7'     #  0x0087 -> LATIN SMALL LETTER C WITH CEDILLA
    '\u0142'   #  0x0088 -> LATIN SMALL LETTER L WITH STROKE
    '\xeb'     #  0x0089 -> LATIN SMALL LETTER E WITH DIAERESIS
    '\u0150'   #  0x008a -> LATIN CAPITAL LETTER O WITH DOUBLE ACUTE
    '\u0151'   #  0x008b -> LATIN SMALL LETTER O WITH DOUBLE ACUTE
    '\xee'     #  0x008c -> LATIN SMALL LETTER I WITH CIRCUMFLEX
    '\u0179'   #  0x008d -> LATIN CAPITAL LETTER Z WITH ACUTE
    '\xc4'     #  0x008e -> LATIN CAPITAL LETTER A WITH DIAERESIS
    '\u0106'   #  0x008f -> LATIN CAPITAL LETTER C WITH ACUTE
    '\xc9'     #  0x0090 -> LATIN CAPITAL LETTER E WITH ACUTE
    '\u0139'   #  0x0091 -> LATIN CAPITAL LETTER L WITH ACUTE
    '\u013a'   #  0x0092 -> LATIN SMALL LETTER L WITH ACUTE
    '\xf4'     #  0x0093 -> LATIN SMALL LETTER O WITH CIRCUMFLEX
    '\xf6'     #  0x0094 -> LATIN SMALL LETTER O WITH DIAERESIS
    '\u013d'   #  0x0095 -> LATIN CAPITAL LETTER L WITH CARON
    '\u013e'   #  0x0096 -> LATIN SMALL LETTER L WITH CARON
    '\u015a'   #  0x0097 -> LATIN CAPITAL LETTER S WITH ACUTE
    '\u015b'   #  0x0098 -> LATIN SMALL LETTER S WITH ACUTE
    '\xd6'     #  0x0099 -> LATIN CAPITAL LETTER O WITH DIAERESIS
    '\xdc'     #  0x009a -> LATIN CAPITAL LETTER U WITH DIAERESIS
    '\u0164'   #  0x009b -> LATIN CAPITAL LETTER T WITH CARON
    '\u0165'   #  0x009c -> LATIN SMALL LETTER T WITH CARON
    '\u0141'   #  0x009d -> LATIN CAPITAL LETTER L WITH STROKE
    '\xd7'     #  0x009e -> MULTIPLICATION SIGN
