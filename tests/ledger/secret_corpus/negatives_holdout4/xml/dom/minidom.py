"""Simple implementation of the Level 1 DOM.

Namespaces and other minor Level 2 features are also supported.

parse("foo.xml")

parseString("<foo><bar/></foo>")

Todo:
=====
 * convenience methods for getting elements and text.
 * more testing
 * bring some of the writer and linearizer code into conformance with this
        interface
 * SAX 2 namespaces
"""

import io
import xml.dom

from xml.dom import EMPTY_NAMESPACE, EMPTY_PREFIX, XMLNS_NAMESPACE, domreg
from xml.dom.minicompat import *
from xml.dom.xmlbuilder import DOMImplementationLS, DocumentLS

# This is used by the ID-cache invalidation checks; the list isn't
# actually complete, since the nodes being checked will never be the
# DOCUMENT_NODE or DOCUMENT_FRAGMENT_NODE.  (The node being checked is
# the node being added or removed, not the node being modified.)
#
_nodeTypes_with_children = (xml.dom.Node.ELEMENT_NODE,
                            xml.dom.Node.ENTITY_REFERENCE_NODE)


class Node(xml.dom.Node):
    namespaceURI = None # this is non-null only for elements and attributes
    parentNode = None
    ownerDocument = None
    nextSibling = None
    previousSibling = None

    prefix = EMPTY_PREFIX # non-null only for NS elements and attributes

    def __bool__(self):
        return True

    def toxml(self, encoding=None, standalone=None):
        return self.toprettyxml("", "", encoding, standalone)

    def toprettyxml(self, indent="\t", newl="\n", encoding=None,
                    standalone=None):
        if encoding is None:
            writer = io.StringIO()
        else:
            writer = io.TextIOWrapper(io.BytesIO(),
                                      encoding=encoding,
                                      errors="xmlcharrefreplace",
                                      newline='\n')
        if self.nodeType == Node.DOCUMENT_NODE:
            # Can pass encoding only to document, to put it into XML header
            self.writexml(writer, "", indent, newl, encoding, standalone)
        else:
            self.writexml(writer, "", indent, newl)
        if encoding is None:
            return writer.getvalue()
        else:
            return writer.detach().getvalue()

    def hasChildNodes(self):
        return bool(self.childNodes)

    def _get_childNodes(self):
        return self.childNodes

    def _get_firstChild(self):
        if self.childNodes:
            return self.childNodes[0]

    def _get_lastChild(self):
        if self.childNodes:
            return self.childNodes[-1]

    def insertBefore(self, newChild, refChild):
        if newChild.nodeType == self.DOCUMENT_FRAGMENT_NODE:
            for c in tuple(newChild.childNodes):
                self.insertBefore(c, refChild)
            ### The DOM does not clearly specify what to return in this case
            return newChild
        if newChild.nodeType not in self._child_node_types:
            raise xml.dom.HierarchyRequestErr(
                "%s cannot be child of %s" % (repr(newChild), repr(self)))
        if newChild.parentNode is not None:
            newChild.parentNode.removeChild(newChild)
        if refChild is None:
            self.appendChild(newChild)
        else:
            try:
                index = self.childNodes.index(refChild)
            except ValueError:
                raise xml.dom.NotFoundErr()
            if newChild.nodeType in _nodeTypes_with_children:
                _clear_id_cache(self)
            self.childNodes.insert(index, newChild)
            newChild.nextSibling = refChild
            refChild.previousSibling = newChild
            if index:
                node = self.childNodes[index-1]
                node.nextSibling = newChild
                newChild.previousSibling = node
            else:
                newChild.previousSibling = None
            newChild.parentNode = self
        return newChild

    def appendChild(self, node):
        if node.nodeType == self.DOCUMENT_FRAGMENT_NODE:
            for c in tuple(node.childNodes):
                self.appendChild(c)
            ### The DOM does not clearly specify what to return in this case
            return node
        if node.nodeType not in self._child_node_types:
            raise xml.dom.HierarchyRequestErr(
                "%s cannot be child of %s" % (repr(node), repr(self)))
        elif node.nodeType in _nodeTypes_with_children:
            _clear_id_cache(self)
        if node.parentNode is not None:
            node.parentNode.removeChild(node)
        _append_child(self, node)
        node.nextSibling = None
        return node

    def replaceChild(self, newChild, oldChild):
        if newChild.nodeType == self.DOCUMENT_FRAGMENT_NODE:
            refChild = oldChild.nextSibling
            self.removeChild(oldChild)
            return self.insertBefore(newChild, refChild)
        if newChild.nodeType not in self._child_node_types:
            raise xml.dom.HierarchyRequestErr(
                "%s cannot be child of %s" % (repr(newChild), repr(self)))
        if newChild is oldChild:
            return
        if newChild.parentNode is not None:
            newChild.parentNode.removeChild(newChild)
        try:
            index = self.childNodes.index(oldChild)
        except ValueError:
            raise xml.dom.NotFoundErr()
        self.childNodes[index] = newChild
        newChild.parentNode = self
        oldChild.parentNode = None
        if (newChild.nodeType in _nodeTypes_with_children
            or oldChild.nodeType in _nodeTypes_with_children):
            _clear_id_cache(self)
        newChild.nextSibling = oldChild.nextSibling
        newChild.previousSibling = oldChild.previousSibling
        oldChild.nextSibling = None
        oldChild.previousSibling = None
        if newChild.previousSibling:
            newChild.previousSibling.nextSibling = newChild
        if newChild.nextSibling:
            newChild.nextSibling.previousSibling = newChild
        return oldChild

    def removeChild(self, oldChild):
        try:
            self.childNodes.remove(oldChild)
        except ValueError:
            raise xml.dom.NotFoundErr()
        if oldChild.nextSibling is not None:
            oldChild.nextSibling.previousSibling = oldChild.previousSibling
        if oldChild.previousSibling is not None:
            oldChild.previousSibling.nextSibling = oldChild.nextSibling
        oldChild.nextSibling = oldChild.previousSibling = None
        if oldChild.nodeType in _nodeTypes_with_children:
            _clear_id_cache(self)

        oldChild.parentNode = None
        return oldChild

    def normalize(self):
        L = []
        for child in self.childNodes:
            if child.nodeType == Node.TEXT_NODE:
                if not child.data:
                    # empty text node; discard
                    if L:
                        L[-1].nextSibling = child.nextSibling
                    if child.nextSibling:
                        child.nextSibling.previousSibling = child.previousSibling
                    child.unlink()
                elif L and L[-1].nodeType == child.nodeType:
                    # collapse text node
                    node = L[-1]
                    node.data = node.data + child.data
                    node.nextSibling = child.nextSibling
                    if child.nextSibling:
                        child.nextSibling.previousSibling = node
                    child.unlink()
                else:
                    L.append(child)
            else:
                L.append(child)
                if child.nodeType == Node.ELEMENT_NODE:
                    child.normalize()
        self.childNodes[:] = L

    def cloneNode(self, deep):
        return _clone_node(self, deep, self.ownerDocument or self)

    def isSupported(self, feature, version):
        return self.ownerDocument.implementation.hasFeature(feature, version)

    def _get_localName(self):
        # Overridden in Element and Attr where localName can be Non-Null
        return None

    # Node interfaces from Level 3 (WD 9 April 2002)

    def isSameNode(self, other):
        return self is other

    def getInterface(self, feature):
        if self.isSupported(feature, None):
            return self
        else:
            return None

    # The "user data" functions use a dictionary that is only present
    # if some user data has been set, so be careful not to assume it
    # exists.

    def getUserData(self, key):
        try:
            return self._user_data[key][0]
        except (AttributeError, KeyError):
            return None

    def setUserData(self, key, data, handler):
        old = None
        try:
            d = self._user_data
        except AttributeError:
            d = {}
            self._user_data = d
        if key in d:
            old = d[key][0]
        if data is None:
            # ignore handlers passed for None
            handler = None
            if old is not None:
                del d[key]
        else:
            d[key] = (data, handler)
        return old

    def _call_user_data_handler(self, operation, src, dst):
        if hasattr(self, "_user_data"):
            for key, (data, handler) in list(self._user_data.items()):
                if handler is not None:
                    handler.handle(operation, key, data, src, dst)

    # minidom-specific API:

    def unlink(self):
        self.parentNode = self.ownerDocument = None
        if self.childNodes:
            for child in self.childNodes:
                child.unlink()
            self.childNodes = NodeList()
        self.previousSibling = None
        self.nextSibling = None

    # A Node is its own context manager, to ensure that an unlink() call occurs.
    # This is similar to how a file object works.
    def __enter__(self):
        return self

    def __exit__(self, et, ev, tb):
        self.unlink()

defproperty(Node, "firstChild", doc="First child node, or None.")
defproperty(Node, "lastChild",  doc="Last child node, or None.")
defproperty(Node, "localName",  doc="Namespace-local name of this node.")


def _append_child(self, node):
    # fast path with less checks; usable by DOM builders if careful
    childNodes = self.childNodes
    if childNodes:
        last = childNodes[-1]
        node.previousSibling = last
        last.nextSibling = node
    childNodes.append(node)
    node.parentNode = self


def _write_data(writer, text, attr):
    "Writes datachars to writer."
    if not text:
        return
    # See the comments in ElementTree.py for behavior and
    # implementation details.
    if "&" in text:
        text = text.replace("&", "&amp;")
    if "<" in text:
        text = text.replace("<", "&lt;")
    if ">" in text:
        text = text.replace(">", "&gt;")
    if attr:
        if '"' in text:
            text = text.replace('"', "&quot;")
        if "\r" in text:
            text = text.replace("\r", "&#13;")
        if "\n" in text:
            text = text.replace("\n", "&#10;")
        if "\t" in text:
            text = text.replace("\t", "&#9;")
    writer.write(text)

def _get_elements_by_tagName_helper(parent, name, rc):
    for node in parent.childNodes:
        if node.nodeType == Node.ELEMENT_NODE and \
            (name == "*" or node.tagName == name):
            rc.append(node)
        _get_elements_by_tagName_helper(node, name, rc)
    return rc

def _get_elements_by_tagName_ns_helper(parent, nsURI, localName, rc):
    for node in parent.childNodes:
        if node.nodeType == Node.ELEMENT_NODE:
            if ((localName == "*" or node.localName == localName) and
                (nsURI == "*" or node.namespaceURI == nsURI)):
                rc.append(node)
            _get_elements_by_tagName_ns_helper(node, nsURI, localName, rc)
    return rc

class DocumentFragment(Node):
    nodeType = Node.DOCUMENT_FRAGMENT_NODE
    nodeName = "#document-fragment"
    nodeValue = None
    attributes = None
    parentNode = None
    _child_node_types = (Node.ELEMENT_NODE,
                         Node.TEXT_NODE,
                         Node.CDATA_SECTION_NODE,
                         Node.ENTITY_REFERENCE_NODE,
                         Node.PROCESSING_INSTRUCTION_NODE,
                         Node.COMMENT_NODE,
                         Node.NOTATION_NODE)

    def __init__(self):
        self.childNodes = NodeList()


class Attr(Node):
    __slots__=('_name', '_value', 'namespaceURI',
               '_prefix', 'childNodes', '_localName', 'ownerDocument', 'ownerElement')
    nodeType = Node.ATTRIBUTE_NODE
    attributes = None
    specified = False
    _is_id = False

    _child_node_types = (Node.TEXT_NODE, Node.ENTITY_REFERENCE_NODE)

    def __init__(self, qName, namespaceURI=EMPTY_NAMESPACE, localName=None,
                 prefix=None):
        self.ownerElement = None
        self.ownerDocument = None
        self._name = qName
        self.namespaceURI = namespaceURI
        self._prefix = prefix
        if localName is not None:
            self._localName = localName
        self.childNodes = NodeList()

        # Add the single child node that represents the value of the attr
        self.childNodes.append(Text())

        # nodeValue and value are set elsewhere

    def _get_localName(self):
        try:
            return self._localName
        except AttributeError:
            return self.nodeName.split(":", 1)[-1]

    def _get_specified(self):
        return self.specified

    def _get_name(self):
        return self._name

    def _set_name(self, value):
        self._name = value
        if self.ownerElement is not None:
            _clear_id_cache(self.ownerElement)

    nodeName = name = property(_get_name, _set_name)

    def _get_value(self):
        return self._value

    def _set_value(self, value):
        self._value = value
        self.childNodes[0].data = value
        if self.ownerElement is not None:
            _clear_id_cache(self.ownerElement)
        self.childNodes[0].data = value

    nodeValue = value = property(_get_value, _set_value)

    def _get_prefix(self):
        return self._prefix

    def _set_prefix(self, prefix):
        nsuri = self.namespaceURI
        if prefix == "xmlns":
            if nsuri and nsuri != XMLNS_NAMESPACE:
                raise xml.dom.NamespaceErr(
                    "illegal use of 'xmlns' prefix for the wrong namespace")
        self._prefix = prefix
        if prefix is None:
            newName = self.localName
        else:
            newName = "%s:%s" % (prefix, self.localName)
        if self.ownerElement:
            _clear_id_cache(self.ownerElement)
        self.name = newName

    prefix = property(_get_prefix, _set_prefix)

    def unlink(self):
        # This implementation does not call the base implementation
        # since most of that is not needed, and the expense of the
        # method call is not warranted.  We duplicate the removal of
        # children, but that's all we needed from the base class.
        elem = self.ownerElement
        if elem is not None:
            del elem._attrs[self.nodeName]
            del elem._attrsNS[(self.namespaceURI, self.localName)]
            if self._is_id:
                self._is_id = False
                elem._magic_id_nodes -= 1
                self.ownerDocument._magic_id_count -= 1
        for child in self.childNodes:
            child.unlink()
        del self.childNodes[:]

    def _get_isId(self):
        if self._is_id:
            return True
        doc = self.ownerDocument
        elem = self.ownerElement
        if doc is None or elem is None:
            return False

        info = doc._get_elem_info(elem)
        if info is None:
            return False
        if self.namespaceURI:
            return info.isIdNS(self.namespaceURI, self.localName)
        else:
            return info.isId(self.nodeName)

    def _get_schemaType(self):
        doc = self.ownerDocument
        elem = self.ownerElement
        if doc is None or elem is None:
            return _no_type

        info = doc._get_elem_info(elem)
        if info is None:
            return _no_type
        if self.namespaceURI:
            return info.getAttributeTypeNS(self.namespaceURI, self.localName)
        else:
            return info.getAttributeType(self.nodeName)

