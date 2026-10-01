"""Facility to use the Expat parser to load a minidom instance
from a string or file.

This avoids all the overhead of SAX and pulldom to gain performance.
"""

# Warning!
#
# This module is tightly bound to the implementation details of the
# minidom DOM and can't be used with other DOM implementations.  This
# is due, in part, to a lack of appropriate methods in the DOM (there is
# no way to create Entity and Notation nodes via the DOM Level 2
# interface), and for performance.  The latter is the cause of some fairly
# cryptic code.
#
# Performance hacks:
#
#   -  .character_data_handler() has an extra case in which continuing
#      data is appended to an existing Text node; this can be a
#      speedup since pyexpat can break up character data into multiple
#      callbacks even though we set the buffer_text attribute on the
#      parser.  This also gives us the advantage that we don't need a
#      separate normalization pass.
#
#   -  Determining that a node exists is done using an identity comparison
#      with None rather than a truth test; this avoids searching for and
#      calling any methods on the node object if it exists.  (A rather
#      nice speedup is achieved this way as well!)

from xml.dom import xmlbuilder, minidom, Node
from xml.dom import EMPTY_NAMESPACE, EMPTY_PREFIX, XMLNS_NAMESPACE
from xml.parsers import expat
from xml.dom.minidom import _append_child, _set_attribute_node
from xml.dom.NodeFilter import NodeFilter

TEXT_NODE = Node.TEXT_NODE
CDATA_SECTION_NODE = Node.CDATA_SECTION_NODE
DOCUMENT_NODE = Node.DOCUMENT_NODE

FILTER_ACCEPT = xmlbuilder.DOMBuilderFilter.FILTER_ACCEPT
FILTER_REJECT = xmlbuilder.DOMBuilderFilter.FILTER_REJECT
FILTER_SKIP = xmlbuilder.DOMBuilderFilter.FILTER_SKIP
FILTER_INTERRUPT = xmlbuilder.DOMBuilderFilter.FILTER_INTERRUPT

theDOMImplementation = minidom.getDOMImplementation()

# Expat typename -> TypeInfo
_typeinfo_map = {
    "CDATA":    minidom.TypeInfo(None, "cdata"),
    "ENUM":     minidom.TypeInfo(None, "enumeration"),
    "ENTITY":   minidom.TypeInfo(None, "entity"),
    "ENTITIES": minidom.TypeInfo(None, "entities"),
    "ID":       minidom.TypeInfo(None, "id"),
    "IDREF":    minidom.TypeInfo(None, "idref"),
    "IDREFS":   minidom.TypeInfo(None, "idrefs"),
    "NMTOKEN":  minidom.TypeInfo(None, "nmtoken"),
    "NMTOKENS": minidom.TypeInfo(None, "nmtokens"),
    }

class ElementInfo(object):
    __slots__ = '_attr_info', '_model', 'tagName'

    def __init__(self, tagName, model=None):
        self.tagName = tagName
        self._attr_info = []
        self._model = model

    def __getstate__(self):
        return self._attr_info, self._model, self.tagName

    def __setstate__(self, state):
        self._attr_info, self._model, self.tagName = state

    def getAttributeType(self, aname):
        for info in self._attr_info:
            if info[1] == aname:
                t = info[-2]
                if t[0] == "(":
                    return _typeinfo_map["ENUM"]
                else:
                    return _typeinfo_map[info[-2]]
        return minidom._no_type

    def getAttributeTypeNS(self, namespaceURI, localName):
        return minidom._no_type

    def isElementContent(self):
        if self._model:
            type = self._model[0]
            return type not in (expat.model.XML_CTYPE_ANY,
                                expat.model.XML_CTYPE_MIXED)
        else:
            return False

    def isEmpty(self):
        if self._model:
            return self._model[0] == expat.model.XML_CTYPE_EMPTY
        else:
            return False

    def isId(self, aname):
        for info in self._attr_info:
            if info[1] == aname:
                return info[-2] == "ID"
        return False

    def isIdNS(self, euri, ename, auri, aname):
        # not sure this is meaningful
        return self.isId((auri, aname))

def _intern(builder, s):
    return builder._intern_setdefault(s, s)

def _parse_ns_name(builder, name):
    assert ' ' in name
    parts = name.split(' ')
    intern = builder._intern_setdefault
    if len(parts) == 3:
        uri, localname, prefix = parts
        prefix = intern(prefix, prefix)
        qname = "%s:%s" % (prefix, localname)
        qname = intern(qname, qname)
        localname = intern(localname, localname)
    elif len(parts) == 2:
        uri, localname = parts
        prefix = EMPTY_PREFIX
        qname = localname = intern(localname, localname)
    else:
        raise ValueError("Unsupported syntax: spaces in URIs not supported: %r" % name)
    return intern(uri, uri), localname, prefix, qname


class ExpatBuilder:
    """Document builder that uses Expat to build a ParsedXML.DOM document
    instance."""

    def __init__(self, options=None):
        if options is None:
            options = xmlbuilder.Options()
        self._options = options
        if self._options.filter is not None:
            self._filter = FilterVisibilityController(self._options.filter)
        else:
            self._filter = None
            # This *really* doesn't do anything in this case, so
            # override it with something fast & minimal.
            self._finish_start_element = id
        self._parser = None
        self.reset()

    def createParser(self):
        """Create a new parser object."""
        return expat.ParserCreate()

    def getParser(self):
        """Return the parser object, creating a new one if needed."""
        if not self._parser:
            self._parser = self.createParser()
            self._intern_setdefault = self._parser.intern.setdefault
            self._parser.buffer_text = True
            self._parser.ordered_attributes = True
            self._parser.specified_attributes = True
            self.install(self._parser)
        return self._parser

    def reset(self):
        """Free all data structures used during DOM construction."""
        self.document = theDOMImplementation.createDocument(
            EMPTY_NAMESPACE, None, None)
        self.curNode = self.document
        self._elem_info = self.document._elem_info
        self._cdata = False

    def install(self, parser):
        """Install the callbacks needed to build the DOM into the parser."""
        # This creates circular references!
        parser.StartDoctypeDeclHandler = self.start_doctype_decl_handler
        parser.StartElementHandler = self.first_element_handler
        parser.EndElementHandler = self.end_element_handler
        parser.ProcessingInstructionHandler = self.pi_handler
        if self._options.entities:
            parser.EntityDeclHandler = self.entity_decl_handler
        parser.NotationDeclHandler = self.notation_decl_handler
        if self._options.comments:
            parser.CommentHandler = self.comment_handler
        if self._options.cdata_sections:
            parser.StartCdataSectionHandler = self.start_cdata_section_handler
            parser.EndCdataSectionHandler = self.end_cdata_section_handler
            parser.CharacterDataHandler = self.character_data_handler_cdata
        else:
            parser.CharacterDataHandler = self.character_data_handler
        parser.ExternalEntityRefHandler = self.external_entity_ref_handler
        parser.XmlDeclHandler = self.xml_decl_handler
        parser.ElementDeclHandler = self.element_decl_handler
        parser.AttlistDeclHandler = self.attlist_decl_handler

    def parseFile(self, file):
        """Parse a document from a file object, returning the document
        node."""
        parser = self.getParser()
        first_buffer = True
        try:
            while buffer := file.read(16*1024):
                parser.Parse(buffer, False)
                if first_buffer and self.document.documentElement:
                    self._setup_subset(buffer)
                first_buffer = False
            parser.Parse(b"", True)
        except ParseEscape:
            pass
        doc = self.document
        self.reset()
        self._parser = None
        return doc

    def parseString(self, string):
        """Parse a document from a string, returning the document node."""
        parser = self.getParser()
        try:
            parser.Parse(string, True)
            self._setup_subset(string)
        except ParseEscape:
            pass
        doc = self.document
        self.reset()
        self._parser = None
        return doc

    def _setup_subset(self, buffer):
        """Load the internal subset if there might be one."""
        if self.document.doctype:
            extractor = InternalSubsetExtractor()
            extractor.parseString(buffer)
            subset = extractor.getSubset()
            self.document.doctype.internalSubset = subset

    def start_doctype_decl_handler(self, doctypeName, systemId, publicId,
                                   has_internal_subset):
        doctype = self.document.implementation.createDocumentType(
            doctypeName, publicId, systemId)
        doctype.ownerDocument = self.document
        _append_child(self.document, doctype)
        self.document.doctype = doctype
        if self._filter and self._filter.acceptNode(doctype) == FILTER_REJECT:
            self.document.doctype = None
            del self.document.childNodes[-1]
            doctype = None
            self._parser.EntityDeclHandler = None
            self._parser.NotationDeclHandler = None
        if has_internal_subset:
            if doctype is not None:
                doctype.entities._seq = []
                doctype.notations._seq = []
            self._parser.CommentHandler = None
            self._parser.ProcessingInstructionHandler = None
            self._parser.EndDoctypeDeclHandler = self.end_doctype_decl_handler

    def end_doctype_decl_handler(self):
        if self._options.comments:
            self._parser.CommentHandler = self.comment_handler
        self._parser.ProcessingInstructionHandler = self.pi_handler
        if not (self._elem_info or self._filter):
            self._finish_end_element = id

    def pi_handler(self, target, data):
        node = self.document.createProcessingInstruction(target, data)
        _append_child(self.curNode, node)
        if self._filter and self._filter.acceptNode(node) == FILTER_REJECT:
            self.curNode.removeChild(node)

    def character_data_handler_cdata(self, data):
        childNodes = self.curNode.childNodes
        if self._cdata:
            if (  self._cdata_continue
                  and childNodes[-1].nodeType == CDATA_SECTION_NODE):
                childNodes[-1].appendData(data)
                return
            node = self.document.createCDATASection(data)
            self._cdata_continue = True
        elif childNodes and childNodes[-1].nodeType == TEXT_NODE:
            node = childNodes[-1]
            value = node.data + data
            node.data = value
            return
        else:
            node = minidom.Text()
            node.data = data
            node.ownerDocument = self.document
        _append_child(self.curNode, node)

    def character_data_handler(self, data):
        childNodes = self.curNode.childNodes
        if childNodes and childNodes[-1].nodeType == TEXT_NODE:
            node = childNodes[-1]
            node.data = node.data + data
            return
        node = minidom.Text()
        node.data = node.data + data
        node.ownerDocument = self.document
        _append_child(self.curNode, node)

    def entity_decl_handler(self, entityName, is_parameter_entity, value,
                            base, systemId, publicId, notationName):
        if is_parameter_entity:
            # we don't care about parameter entities for the DOM
            return
        if not self._options.entities:
            return
        node = self.document._create_entity(entityName, publicId,
                                            systemId, notationName)
        if value is not None:
            # internal entity
            # node *should* be readonly, but we'll cheat
            child = self.document.createTextNode(value)
            node.childNodes.append(child)
        self.document.doctype.entities._seq.append(node)
        if self._filter and self._filter.acceptNode(node) == FILTER_REJECT:
            del self.document.doctype.entities._seq[-1]

    def notation_decl_handler(self, notationName, base, systemId, publicId):
        node = self.document._create_notation(notationName, publicId, systemId)
        self.document.doctype.notations._seq.append(node)
        if self._filter and self._filter.acceptNode(node) == FILTER_ACCEPT:
            del self.document.doctype.notations._seq[-1]

    def comment_handler(self, data):
        node = self.document.createComment(data)
        _append_child(self.curNode, node)
        if self._filter and self._filter.acceptNode(node) == FILTER_REJECT:
            self.curNode.removeChild(node)

    def start_cdata_section_handler(self):
        self._cdata = True
        self._cdata_continue = False

    def end_cdata_section_handler(self):
        self._cdata = False
        self._cdata_continue = False

    def external_entity_ref_handler(self, context, base, systemId, publicId):
        return 1

    def first_element_handler(self, name, attributes):
        if self._filter is None and not self._elem_info:
            self._finish_end_element = id
        self.getParser().StartElementHandler = self.start_element_handler
        self.start_element_handler(name, attributes)

    def start_element_handler(self, name, attributes):
        node = self.document.createElement(name)
        _append_child(self.curNode, node)
        self.curNode = node

        if attributes:
            for i in range(0, len(attributes), 2):
                a = minidom.Attr(attributes[i], EMPTY_NAMESPACE,
                                 None, EMPTY_PREFIX)
                value = attributes[i+1]
                a.value = value
                a.ownerDocument = self.document
                _set_attribute_node(node, a)

        if node is not self.document.documentElement:
            self._finish_start_element(node)

    def _finish_start_element(self, node):
        if self._filter:
            # To be general, we'd have to call isSameNode(), but this
            # is sufficient for minidom:
            if node is self.document.documentElement:
                return
            filt = self._filter.startContainer(node)
            if filt == FILTER_REJECT:
                # ignore this node & all descendents
                Rejecter(self)
            elif filt == FILTER_SKIP:
                # ignore this node, but make it's children become
                # children of the parent node
                Skipper(self)
            else:
                return
            self.curNode = node.parentNode
            node.parentNode.removeChild(node)
            node.unlink()

    # If this ever changes, Namespaces.end_element_handler() needs to
    # be changed to match.
    #
    def end_element_handler(self, name):
        curNode = self.curNode
        self.curNode = curNode.parentNode
        self._finish_end_element(curNode)

    def _finish_end_element(self, curNode):
        info = self._elem_info.get(curNode.tagName)
        if info:
            self._handle_white_text_nodes(curNode, info)
        if self._filter:
            if curNode is self.document.documentElement:
                return
            if self._filter.acceptNode(curNode) == FILTER_REJECT:
                self.curNode.removeChild(curNode)
                curNode.unlink()

    def _handle_white_text_nodes(self, node, info):
        if (self._options.whitespace_in_element_content
            or not info.isElementContent()):
            return

        # We have element type information and should remove ignorable
        # whitespace; identify for text nodes which contain only
        # whitespace.
        L = []
        for child in node.childNodes:
            if child.nodeType == TEXT_NODE and not child.data.strip():
                L.append(child)

        # Remove ignorable whitespace from the tree.
        for child in L:
            node.removeChild(child)

    def element_decl_handler(self, name, model):
        info = self._elem_info.get(name)
        if info is None:
            self._elem_info[name] = ElementInfo(name, model)
        else:
