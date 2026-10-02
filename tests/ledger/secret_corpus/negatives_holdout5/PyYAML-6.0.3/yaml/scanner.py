
# Scanner produces tokens of the following types:
# STREAM-START
# STREAM-END
# DIRECTIVE(name, value)
# DOCUMENT-START
# DOCUMENT-END
# BLOCK-SEQUENCE-START
# BLOCK-MAPPING-START
# BLOCK-END
# FLOW-SEQUENCE-START
# FLOW-MAPPING-START
# FLOW-SEQUENCE-END
# FLOW-MAPPING-END
# BLOCK-ENTRY
# FLOW-ENTRY
# KEY
# VALUE
# ALIAS(value)
# ANCHOR(value)
# TAG(value)
# SCALAR(value, plain, style)
#
# Read comments in the Scanner code for more details.
#

__all__ = ['Scanner', 'ScannerError']

from .error import MarkedYAMLError
from .tokens import *

class ScannerError(MarkedYAMLError):
    pass

class SimpleKey:
    # See below simple keys treatment.

    def __init__(self, token_number, required, index, line, column, mark):
        self.token_number = token_number
        self.required = required
        self.index = index
        self.line = line
        self.column = column
        self.mark = mark

class Scanner:

    def __init__(self):
        """Initialize the scanner."""
        # It is assumed that Scanner and Reader will have a common descendant.
        # Reader do the dirty work of checking for BOM and converting the
        # input data to Unicode. It also adds NUL to the end.
        #
        # Reader supports the following methods
        #   self.peek(i=0)       # peek the next i-th character
        #   self.prefix(l=1)     # peek the next l characters
        #   self.forward(l=1)    # read the next l characters and move the pointer.

        # Had we reached the end of the stream?
        self.done = False

        # The number of unclosed '{' and '['. `flow_level == 0` means block
        # context.
        self.flow_level = 0

        # List of processed tokens that are not yet emitted.
        self.tokens = []

        # Add the STREAM-START token.
        self.fetch_stream_start()

        # Number of tokens that were emitted through the `get_token` method.
        self.tokens_taken = 0

        # The current indentation level.
        self.indent = -1

        # Past indentation levels.
        self.indents = []

        # Variables related to simple keys treatment.

        # A simple key is a key that is not denoted by the '?' indicator.
        # Example of simple keys:
        #   ---
        #   block simple key: value
        #   ? not a simple key:
        #   : { flow simple key: value }
        # We emit the KEY token before all keys, so when we find a potential
        # simple key, we try to locate the corresponding ':' indicator.
        # Simple keys should be limited to a single line and 1024 characters.

        # Can a simple key start at the current position? A simple key may
        # start:
        # - at the beginning of the line, not counting indentation spaces
        #       (in block context),
        # - after '{', '[', ',' (in the flow context),
        # - after '?', ':', '-' (in the block context).
        # In the block context, this flag also signifies if a block collection
        # may start at the current position.
        self.allow_simple_key = True

        # Keep track of possible simple keys. This is a dictionary. The key
        # is `flow_level`; there can be no more that one possible simple key
        # for each level. The value is a SimpleKey record:
        #   (token_number, required, index, line, column, mark)
        # A simple key may start with ALIAS, ANCHOR, TAG, SCALAR(flow),
        # '[', or '{' tokens.
        self.possible_simple_keys = {}

    # Public methods.

    def check_token(self, *choices):
        # Check if the next token is one of the given types.
        while self.need_more_tokens():
            self.fetch_more_tokens()
        if self.tokens:
            if not choices:
                return True
            for choice in choices:
                if isinstance(self.tokens[0], choice):
                    return True
        return False

    def peek_token(self):
        # Return the next token, but do not delete if from the queue.
        # Return None if no more tokens.
        while self.need_more_tokens():
            self.fetch_more_tokens()
        if self.tokens:
            return self.tokens[0]
        else:
            return None

    def get_token(self):
        # Return the next token.
        while self.need_more_tokens():
            self.fetch_more_tokens()
        if self.tokens:
            self.tokens_taken += 1
            return self.tokens.pop(0)

    # Private methods.

    def need_more_tokens(self):
        if self.done:
            return False
        if not self.tokens:
            return True
        # The current token may be a potential simple key, so we
        # need to look further.
        self.stale_possible_simple_keys()
        if self.next_possible_simple_key() == self.tokens_taken:
            return True

    def fetch_more_tokens(self):

        # Eat whitespaces and comments until we reach the next token.
        self.scan_to_next_token()

        # Remove obsolete possible simple keys.
        self.stale_possible_simple_keys()

        # Compare the current indentation and column. It may add some tokens
        # and decrease the current indentation level.
        self.unwind_indent(self.column)

        # Peek the next character.
        ch = self.peek()

        # Is it the end of stream?
        if ch == '\0':
            return self.fetch_stream_end()

        # Is it a directive?
        if ch == '%' and self.check_directive():
            return self.fetch_directive()

        # Is it the document start?
        if ch == '-' and self.check_document_start():
            return self.fetch_document_start()

        # Is it the document end?
        if ch == '.' and self.check_document_end():
            return self.fetch_document_end()

        # TODO: support for BOM within a stream.
        #if ch == '\uFEFF':
        #    return self.fetch_bom()    <-- issue BOMToken

        # Note: the order of the following checks is NOT significant.

        # Is it the flow sequence start indicator?
        if ch == '[':
            return self.fetch_flow_sequence_start()

        # Is it the flow mapping start indicator?
        if ch == '{':
            return self.fetch_flow_mapping_start()

        # Is it the flow sequence end indicator?
        if ch == ']':
            return self.fetch_flow_sequence_end()

        # Is it the flow mapping end indicator?
        if ch == '}':
            return self.fetch_flow_mapping_end()

        # Is it the flow entry indicator?
        if ch == ',':
            return self.fetch_flow_entry()

        # Is it the block entry indicator?
        if ch == '-' and self.check_block_entry():
            return self.fetch_block_entry()

        # Is it the key indicator?
        if ch == '?' and self.check_key():
            return self.fetch_key()

        # Is it the value indicator?
        if ch == ':' and self.check_value():
            return self.fetch_value()

        # Is it an alias?
        if ch == '*':
            return self.fetch_alias()

        # Is it an anchor?
        if ch == '&':
            return self.fetch_anchor()

        # Is it a tag?
        if ch == '!':
            return self.fetch_tag()

        # Is it a literal scalar?
        if ch == '|' and not self.flow_level:
            return self.fetch_literal()

        # Is it a folded scalar?
        if ch == '>' and not self.flow_level:
            return self.fetch_folded()

        # Is it a single quoted scalar?
        if ch == '\'':
            return self.fetch_single()

        # Is it a double quoted scalar?
        if ch == '\"':
            return self.fetch_double()

        # It must be a plain scalar then.
        if self.check_plain():
            return self.fetch_plain()

        # No? It's an error. Let's produce a nice error message.
        raise ScannerError("while scanning for the next token", None,
                "found character %r that cannot start any token" % ch,
                self.get_mark())

    # Simple keys treatment.

    def next_possible_simple_key(self):
        # Return the number of the nearest possible simple key. Actually we
        # don't need to loop through the whole dictionary. We may replace it
        # with the following code:
        #   if not self.possible_simple_keys:
        #       return None
        #   return self.possible_simple_keys[
        #           min(self.possible_simple_keys.keys())].token_number
        min_token_number = None
        for level in self.possible_simple_keys:
            key = self.possible_simple_keys[level]
            if min_token_number is None or key.token_number < min_token_number:
                min_token_number = key.token_number
        return min_token_number

    def stale_possible_simple_keys(self):
        # Remove entries that are no longer possible simple keys. According to
        # the YAML specification, simple keys
        # - should be limited to a single line,
        # - should be no longer than 1024 characters.
        # Disabling this procedure will allow simple keys of any length and
        # height (may cause problems if indentation is broken though).
        for level in list(self.possible_simple_keys):
            key = self.possible_simple_keys[level]
            if key.line != self.line  \
                    or self.index-key.index > 1024:
                if key.required:
                    raise ScannerError("while scanning a simple key", key.mark,
                            "could not find expected ':'", self.get_mark())
                del self.possible_simple_keys[level]

    def save_possible_simple_key(self):
        # The next token may start a simple key. We check if it's possible
        # and save its position. This function is called for
        #   ALIAS, ANCHOR, TAG, SCALAR(flow), '[', and '{'.

        # Check if a simple key is required at the current position.
        required = not self.flow_level and self.indent == self.column

        # The next token might be a simple key. Let's save it's number and
        # position.
        if self.allow_simple_key:
            self.remove_possible_simple_key()
            token_number = self.tokens_taken+len(self.tokens)
            key = SimpleKey(token_number, required,
                    self.index, self.line, self.column, self.get_mark())
            self.possible_simple_keys[self.flow_level] = key

    def remove_possible_simple_key(self):
        # Remove the saved possible key position at the current flow level.
        if self.flow_level in self.possible_simple_keys:
            key = self.possible_simple_keys[self.flow_level]
            
            if key.required:
                raise ScannerError("while scanning a simple key", key.mark,
                        "could not find expected ':'", self.get_mark())

            del self.possible_simple_keys[self.flow_level]

    # Indentation functions.

    def unwind_indent(self, column):

        ## In flow context, tokens should respect indentation.
        ## Actually the condition should be `self.indent >= column` according to
        ## the spec. But this condition will prohibit intuitively correct
        ## constructions such as
        ## key : {
        ## }
        #if self.flow_level and self.indent > column:
        #    raise ScannerError(None, None,
        #            "invalid indentation or unclosed '[' or '{'",
        #            self.get_mark())

        # In the flow context, indentation is ignored. We make the scanner less
        # restrictive then specification requires.
        if self.flow_level:
            return

        # In block context, we may need to issue the BLOCK-END tokens.
        while self.indent > column:
            mark = self.get_mark()
            self.indent = self.indents.pop()
            self.tokens.append(BlockEndToken(mark, mark))

    def add_indent(self, column):
        # Check if we need to increase indentation.
        if self.indent < column:
            self.indents.append(self.indent)
            self.indent = column
            return True
        return False

    # Fetchers.

    def fetch_stream_start(self):
        # We always add STREAM-START as the first token and STREAM-END as the
        # last token.

        # Read the token.
        mark = self.get_mark()
        
        # Add STREAM-START.
        self.tokens.append(StreamStartToken(mark, mark,
            encoding=self.encoding))
        

    def fetch_stream_end(self):

        # Set the current indentation to -1.
        self.unwind_indent(-1)

        # Reset simple keys.
        self.remove_possible_simple_key()
        self.allow_simple_key = False
        self.possible_simple_keys = {}

        # Read the token.
        mark = self.get_mark()
        
        # Add STREAM-END.
        self.tokens.append(StreamEndToken(mark, mark))

        # The steam is finished.
        self.done = True

    def fetch_directive(self):
        
        # Set the current indentation to -1.
        self.unwind_indent(-1)

        # Reset simple keys.
        self.remove_possible_simple_key()
        self.allow_simple_key = False

        # Scan and add DIRECTIVE.
        self.tokens.append(self.scan_directive())

    def fetch_document_start(self):
        self.fetch_document_indicator(DocumentStartToken)

    def fetch_document_end(self):
        self.fetch_document_indicator(DocumentEndToken)

    def fetch_document_indicator(self, TokenClass):

        # Set the current indentation to -1.
        self.unwind_indent(-1)

        # Reset simple keys. Note that there could not be a block collection
        # after '---'.
        self.remove_possible_simple_key()
        self.allow_simple_key = False

        # Add DOCUMENT-START or DOCUMENT-END.
        start_mark = self.get_mark()
        self.forward(3)
        end_mark = self.get_mark()
        self.tokens.append(TokenClass(start_mark, end_mark))

    def fetch_flow_sequence_start(self):
        self.fetch_flow_collection_start(FlowSequenceStartToken)

    def fetch_flow_mapping_start(self):
        self.fetch_flow_collection_start(FlowMappingStartToken)

    def fetch_flow_collection_start(self, TokenClass):

        # '[' and '{' may start a simple key.
        self.save_possible_simple_key()

        # Increase the flow level.
        self.flow_level += 1

        # Simple keys are allowed after '[' and '{'.
        self.allow_simple_key = True

        # Add FLOW-SEQUENCE-START or FLOW-MAPPING-START.
        start_mark = self.get_mark()
        self.forward()
        end_mark = self.get_mark()
        self.tokens.append(TokenClass(start_mark, end_mark))

    def fetch_flow_sequence_end(self):
        self.fetch_flow_collection_end(FlowSequenceEndToken)

    def fetch_flow_mapping_end(self):
        self.fetch_flow_collection_end(FlowMappingEndToken)

    def fetch_flow_collection_end(self, TokenClass):

        # Reset possible simple key on the current level.
        self.remove_possible_simple_key()

        # Decrease the flow level.
        self.flow_level -= 1

        # No simple keys after ']' or '}'.
        self.allow_simple_key = False

        # Add FLOW-SEQUENCE-END or FLOW-MAPPING-END.
        start_mark = self.get_mark()
        self.forward()
        end_mark = self.get_mark()
        self.tokens.append(TokenClass(start_mark, end_mark))

    def fetch_flow_entry(self):

        # Simple keys are allowed after ','.
        self.allow_simple_key = True

        # Reset possible simple key on the current level.
        self.remove_possible_simple_key()

        # Add FLOW-ENTRY.
        start_mark = self.get_mark()
        self.forward()
        end_mark = self.get_mark()
        self.tokens.append(FlowEntryToken(start_mark, end_mark))

    def fetch_block_entry(self):

        # Block context needs additional checks.
        if not self.flow_level:

            # Are we allowed to start a new entry?
            if not self.allow_simple_key:
                raise ScannerError(None, None,
                        "sequence entries are not allowed here",
                        self.get_mark())

            # We may need to add BLOCK-SEQUENCE-START.
            if self.add_indent(self.column):
                mark = self.get_mark()
                self.tokens.append(BlockSequenceStartToken(mark, mark))

