##############################################################################
#
# Copyright (c) 2006 Nuxeo and Contributors.
# All Rights Reserved.
#
# This software is subject to the provisions of the Zope Public License,
# Version 2.1 (ZPL).  A copy of the ZPL should accompany this distribution.
# THIS SOFTWARE IS PROVIDED "AS IS" AND ANY AND ALL EXPRESS OR IMPLIED
# WARRANTIES ARE DISCLAIMED, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF TITLE, MERCHANTABILITY, AGAINST INFRINGEMENT, AND FITNESS
# FOR A PARTICULAR PURPOSE.
#
##############################################################################
# Author: Florent Guillaume <fg@nuxeo.com>
# $Id$
"""Read CND schemas and produce zope 3 schemas.

XXX TODO:

- constraints for properties
- default values for properties

- autocreated -> illegal
- default primary node type -> illegal
- protected -> ?
- mandatory -> ?
- version -> ?
"""

import re
from StringIO import StringIO
import zope.interface
import zope.schema
from zope.app.container.constraints import ItemTypePrecondition
from zope.app.container.interfaces import IContainer
from zope.interface.interface import InterfaceClass
from nuxeo.capsule.field import ListPropertyField
from nuxeo.capsule.field import ObjectPropertyField
from nuxeo.capsule.field import BlobField
from nuxeo.capsule.field import ReferenceField

from nuxeo.capsule.interfaces import IObjectBase


_MARKER = object()
IDINITIAL = re.compile('[a-z]', re.IGNORECASE)
IDENTIFIER = re.compile('[a-z0-9:_]', re.IGNORECASE)


def topologicalSort(graph):
    """Compute a topological sort of a graph.

    A graph is a mapping of node -> [dependent nodes]. Nodes must be
    hashable and comparable.

    If the graph has a loop or is missing nodes, a ValueError is raised.

    Returns a list of nodes where all nodes are before their dependents.
    """
    return _TopologicalSorter(graph).sorted()

class _TopologicalSorter(object):
    """
    Topological sort helper class::

      >>> from nuxeo.jcr.cnd import topologicalSort
      >>> graph = {'a': ['b', 'c'], 'b': ['c'], 'c': []}
      >>> topologicalSort(graph)
      ['c', 'b', 'a']
      >>> graph = {'a': ['b', 'c'], 'c': [], 'b': ['c']}
      >>> topologicalSort(graph)
      ['c', 'b', 'a']
      >>> graph = {'a': ['b', 'c'], 'c': ['b'], 'b': []}
      >>> topologicalSort(graph)
      ['b', 'c', 'a']
      >>> graph = {'a': ['b'], 'b': ['c'], 'c': []}
      >>> topologicalSort(graph)
      ['c', 'b', 'a']
      >>> graph = {'a': ['c'], 'c': ['b'], 'b': []}
      >>> topologicalSort(graph)
      ['b', 'c', 'a']
      >>> graph = {'a': [], 'c': ['b'], 'b': ['a']}
      >>> topologicalSort(graph)
      ['a', 'b', 'c']

    Loops produce errors::

      >>> graph = {'a': ['a']}
      >>> topologicalSort(graph)
      Traceback (most recent call last):
        ...
      ValueError: Loop involving 'a'
      >>> graph = {'a': ['b'], 'b': ['a']}
      >>> topologicalSort(graph)
      Traceback (most recent call last):
        ...
      ValueError: Loop involving 'a', 'b'
      >>> graph = {'a': ['b'], 'b': ['c'], 'c': ['a']}
      >>> topologicalSort(graph)
      Traceback (most recent call last):
        ...
      ValueError: Loop involving 'a', 'b', 'c'

    If some dependent is not defined, there is an error::

      >>> graph = {'a': ['b']}
      >>> topologicalSort(graph)
      Traceback (most recent call last):
        ...
      ValueError: Missing dependent 'b' in 'a'

    """

    def __init__(self, graph):
        self.graph = graph
        self.traversed = []
        self.done = set() # like traversed but for containment tests
        self.ancestors = set() # to check for loops

    def _visitLeaves(self, node):
        if node in self.done:
            return
        self.ancestors.add(node)
        for dep in self.graph[node]:
            if dep not in self.graph:
                raise ValueError("Missing dependent %r in %r" % (dep, node))
            if dep in self.ancestors:
                loop = ', '.join(repr(a) for a in sorted(self.ancestors))
                raise ValueError("Loop involving %s" % loop)
            self._visitLeaves(dep)
        self.ancestors.remove(node)
        if node not in self.done:
            self.traversed.append(node)
            self.done.add(node)

    def sorted(self):
        for node in self.graph.iterkeys():
            self._visitLeaves(node)
        return self.traversed


class LexerString(object):
    def __init__(self, value):
        self.value = value
    def __repr__(self):
        return 'LexerString(%r)' % self.value

class LexerQName(object):
    def __init__(self, value):
        self.value = value
    def __repr__(self):
        return 'LexerQName(%r)' % self.value

def lexerGen(stream):
    """Lexer distinguishing
    - ''-quoted strings
    - non-identifier characters < > = [ ] - + ( ) , * !
    - identifiers including colons
    - returns None at EOF
    """
    pushback = _MARKER
    while True:
        if pushback != _MARKER:
            c = pushback
            pushback = _MARKER
        else:
            c = stream.read(1)
        # EOF
        if not c:
            break
        # spaces
        elif c in ' \t\n\r':
            pass
        # comments
        elif c == '#':
            stream.readline()
        elif c == '/':
            c = stream.read(1)
            if c == '/':
                stream.readline()
            else:
                pushback = c
                yield '/'
        # single-char tokens
        elif c in '<>=[]-+(),*!':
            yield c
        # identifiers
        elif IDINITIAL.match(c):
            got = [c]
            while True:
                c = stream.read(1)
                if not c: # EOF
                    break
                if not IDENTIFIER.match(c):
                    stream.seek(-1, 1) # back 1 char
                    break
                got.append(c)
            yield LexerQName(''.join(got))
        # string
        elif c in '\'"':
            initial = c
            got = []
            while True:
                c = stream.read(1)
                if not c: # EOF
                    break
                if c == initial:
                    break
                got.append(c)
            yield LexerString(''.join(got))
        else:
            raise ValueError(c)
    # EOF token
    yield None

class Lexer(object):
    def __init__(self, stream):
        if isinstance(stream, str):
            stream = StringIO(stream)
        self.lexer = lexerGen(stream)
        self.stack = []
    def __iter__(self):
        return self
    def next(self):
        if self.stack:
            return self.stack.pop()
        return self.lexer.next()
    def pushBack(self, token):
        self.stack.append(token)




class Parser(object):
    """CND parser, produces a Zope 3 schema.
    """

    def __init__(self, input):
        """ """
        self.lexer = Lexer(input)

    def getNamespace(self):
        next = self.lexer.next
        token = next()
        if not isinstance(token, (LexerQName, LexerString)):
            raise ValueError(token)
        ns = token.value
        token = next()
        if token != '=':
            raise ValueError(token)
        token = next()
        if not isinstance(token, LexerString):
            raise ValueError(token)
        uri = token.value
        token = next()
        if token != '>':
            raise ValueError(token)
        return (ns, uri)

    def getSuperTypes(self):
        next = self.lexer.next
        token = next()
        if token != '>':
            self.lexer.pushBack(token)
            return []
        supertypes = []
        while True:
            token = next()
            if not isinstance(token, (LexerQName, LexerString)):
                raise ValueError(token)
            supertypes.append(token.value)
            token = next()
            if token != ',':
                self.lexer.pushBack(token)
                return supertypes

    def getNodeTypeOptions(self):
        options = {
            'orderable': False,
            'mixin': False,
            }
        while True:
            token = self.lexer.next()
            if not isinstance(token, LexerQName):
                self.lexer.pushBack(token)
                return options
            if token.value.lower() in ('o', 'ord', 'orderable'):
                options['orderable'] = True
            elif token.value.lower() in ('m', 'mix', 'mixin'):
                options['mixin'] = True
            else:
                self.lexer.pushBack(token)
                return options

    def getStringList(self, t=LexerString):
        strings = []
        while True:
            token = self.lexer.next()
            if not isinstance(token, t):
                raise ValueError(token)
            strings.append(token.value)
            token = self.lexer.next()
            if token != ',':
                self.lexer.pushBack(token)
                return strings

    def getQNameList(self):
        return self.getStringList((LexerQName, LexerString))


    ok_type_names = ('string', 'binary', 'long', 'double', 'boolean',
                     'date', 'name', 'path', 'reference', 'undefined', '*')

    ok_version = ('copy', 'version', 'initialize',
                  'compute', 'ignore', 'abort')

    option_aliases = {
        'primary': 'primary',
        'pri': 'primary',
        '!': 'primary',
        'autocreated': 'autocreated',
        'aut': 'autocreated',
        'a': 'autocreated',
        'mandatory': 'mandatory',
        'man': 'mandatory',
        'm': 'mandatory',
        'multiple': 'multiple',
        'mul': 'multiple',
        '*': 'multiple',
        'protected': 'protected',
        }

    def getOptions(self):
        options = {
            'primary': False,
            'mandatory': False,
            'autocreated': False,
            'protected': False,
            'multiple': False,
            'version': 'copy',
            }
        while True:
            token = self.lexer.next()
            if token in ('!', '*'):
                value = token
            elif isinstance(token, LexerQName):
                value = token.value.lower()
            else:
                self.lexer.pushBack(token)
                break
            if value in self.ok_version:
                options['version'] = value
            elif value in self.option_aliases:
                v = self.option_aliases[value]
                options[v] = True
            else:
                raise ValueError(token)
        return options

    def getNode(self):
        next = self.lexer.next
        token = next()
        if token == '*':
            node_name = '*'
        elif isinstance(token, (LexerQName, LexerString)):
            node_name = token.value
        else:
            raise ValueError(token)

        # required types
        token = next()
        if token == '(':
            required_types = self.getQNameList()
            token = next()
            if token != ')':
                raise ValueError(token)
        else:
            self.lexer.pushBack(token)
            required_types = []

        # default type
        token = next()
        if token == '=':
            token = next()
            if not isinstance(token, (LexerQName, LexerString)):
                raise ValueError(token)
            default_type = token.value
        else:
            self.lexer.pushBack(token)
            default_type = None

        # options
        options = self.getOptions()

        return {
            'name': node_name,
            'required_types': required_types,
            'default_type': default_type,
            'options': options,
            }

    def getProperty(self):
        next = self.lexer.next
        token = next()
        if token == '*':
            name = '*'
        elif isinstance(token, (LexerQName, LexerString)):
            name = token.value
        else:
            raise ValueError(token)

        # property type
        token = next()
        if token == '(':
            token = next()
            if not isinstance(token, LexerQName): # XXX check *
                raise ValueError(token)
            type_name = token.value.lower()
            if type_name not in self.ok_type_names:
                raise ValueError(token)
            token = next()
            if token != ')':
                raise ValueError(token)
        else:
            self.lexer.pushBack(token)
            type_name = 'string'

        # default values
        token = next()
        if token == '=':
            default_values = self.getStringList()
        else:
            self.lexer.pushBack(token)
            default_values = []

        # options
        options = self.getOptions()

        # constraints
        token = next()
        if token == '<':
            constraints = self.getStringList()
        else:
            self.lexer.pushBack(token)
            constraints = []

        return {
            'name': name,
            'type_name': type_name,
            'default_values': default_values,
            'options': options,
            'constraints': constraints,
            }


    def getData(self):
        next = self.lexer.next
        schemas = {}
        namespaces = {}
        while True:
            token = next()
            if token is None:
                break

            # Namespace
            if token == '<':
                ns, uri = self.getNamespace()
                namespaces[ns] = uri
                continue

            if token != '[':
                raise ValueError(token)

            # Type definition
            token = next()
            if not isinstance(token, (LexerQName, LexerString)):
                raise ValueError(token)
            node_type = token.value
            token = next()
            if token != ']':
                raise ValueError(token)

            supertypes = self.getSuperTypes()
            options = self.getNodeTypeOptions()
            properties = []
            nodes = [] # Don't use a dict, we can have duplicates (*)

            while True:
                token = next()
                if token == '-':
                    property = self.getProperty()
                    properties.append(property)
                elif token == '+':
                    node = self.getNode()
                    nodes.append(node)
                elif token in (None, '<', '['):
                    self.lexer.pushBack(token)
                    break
                else:
                    raise ValueError(token)

            schemas[node_type] = {
                'supertypes': supertypes,
                'options': options,
                'properties': properties,
                'nodes': nodes,
                }

        return namespaces, schemas


class InterfaceMaker(object):
    """An incremental interface maker that can be fed new data later.
    """

    def __init__(self, input=None, predefined=None):
        self._namespaces = {}
        self._infos = {}
        self._interfaces = {}
        if predefined is None:
            predefined = {}
        self._predefined = predefined
        self._interfaces.update(predefined)
        if input is not None:
            self.addData(input)

    def addData(self, input):
        """Add new schemas to the currently defined data.

        `f` is a file or a string.

        Returns the list of interface names that were added.
        """
        parser = Parser(input)
        namespaces, infos = parser.getData()

        for ns, uri in namespaces.iteritems():
            if ns in self._namespaces and self._namespaces[ns] != uri:
                raise ValueError("Namespace %r redefined (%r != %r)" %
                                 (ns, self._namespaces[ns], uri))
        for node_type, info in infos.iteritems():
            if node_type in self._infos:
                raise ValueError("Node type %r redefined" % node_type)
        self._namespaces.update(namespaces)
        self._infos.update(infos)

        type_names = infos.keys()
        self._buildSchemas(type_names)
        return type_names

    def keys(self):
        return self._interfaces.keys()

    def iteritems(self):
        return self._interfaces.iteritems()

    def __getitem__(self, name):
        return self._interfaces[name]

    def getNamespaces(self):
        return self._namespaces

    #
    # Internal API
    #

    def makeString(self, info):
        """Makes a field from a JCR strings property."""
        f = zope.schema.Text(__name__=info['name'])
        return f

    def makeBinary(self, info):
        f = BlobField(__name__=info['name'])
        return f

    def makeBoolean(self, info):
        f = zope.schema.Bool(__name__=info['name'])
        return f

    def makeDate(self, info):
        f = zope.schema.Datetime(__name__=info['name'])
        return f

    def makeLong(self, info):
        f = zope.schema.Int(__name__=info['name'])
        return f

    def makeDouble(self, info):
        f = zope.schema.Float(__name__=info['name'])
        return f

    def makeName(self, info): # XXX
        f = zope.schema.Text(__name__=info['name'])
        return f

    def makeReference(self, info): # XXX
        f = ReferenceField(__name__=info['name'])
        return f

    def makeUndefined(self, info): # XXX
        constraint = None
        f = zope.schema.Field(__name__=info['name'],
                              constraint=constraint)
        return f

    type_makers = {
        'string': makeString,
        'binary': makeBinary,
        'boolean': makeBoolean,
        'date': makeDate,
        'long': makeLong,
        'double': makeDouble,
        'name': makeName,
        'reference': makeReference,
        'undefined': makeUndefined,
        }

    def makeStringList(self, info):
        """Makes a field from a JCR string multiple property."""
        f = zope.schema.List(__name__=info['name'],
                             value_type=zope.schema.Text(),
                             )
        return f

    def makeLongList(self, info):
        f = zope.schema.List(__name__=info['name'],
                             value_type=zope.schema.Int(),
                             )
        return f


    def makeDateList(self, info):
        f = zope.schema.List(__name__=info['name'],
                             value_type=zope.schema.Datetime(),
                             )
        return f

    def makeDoubleList(self, info):
        f = zope.schema.List(__name__=info['name'],
                             value_type=zope.schema.Float(),
                             )
        return f

    def makeBinaryList(self, info):
        f = zope.schema.List(__name__=info['name'],
                             value_type=BlobField(),
                             )
        return f

    def makeBooleanList(self, info):
        f = zope.schema.List(__name__=info['name'],
                             value_type=zope.schema.Bool(),
                             )
        return f

    def makeNameList(self, info): # XXX
        f = zope.schema.List(__name__=info['name'],
                             value_type=zope.schema.Text(),
                             )
        return f

    def makeReferenceList(self, info): # XXX
        f = zope.schema.List(__name__=info['name'],
                             value_type=zope.schema.Text(),
                             )
        return f

    def makeUndefinedList(self, info): # XXX
        f = zope.schema.List(__name__=info['name'])
        return f


    multiple_type_makers = {
        'string': makeStringList,
        'long': makeLongList,
        'date': makeDateList,
        'double': makeDoubleList,
        'binary': makeBinaryList,
        'boolean': makeBooleanList,
        'name': makeNameList,
        'reference': makeReferenceList,
        'undefined': makeUndefinedList,
        }

    def _makeInterfaces(self, type_names, tops):
        """Build empty interfaces.

        They will be mutated later to add fields. This allows fields to
        reference other interfaces without having to care about
        definition order or loops.

        CND schemas having one or more '+ * (T)' node definitions will
        be turned into IContainer interfaces whose __setitem__ method
        has a precondition that checks the type.
        """
        # Compute a topological sort of the available types
        # so that dependents are done first
        graph = dict((type_name, info['supertypes'])
                     for type_name, info in self._infos.iteritems())
        for top in tops:
            graph[top] = []

        try:
            sorted_type_names = topologicalSort(graph)
        except ValueError, e:
            raise ValueError("%s in type inheritance" % e.args[0])

        new_fields = {}
        for type_name in sorted_type_names:
            if type_name not in type_names:
                # Only new ones
                continue

            # Hardcoded interface, use it
            iface = self._predefined.get(type_name)
            if iface is not None:
                self._interfaces[type_name] = iface
                self._interfaces[iface.getName()] = iface # Real name
                new_fields[type_name] = iface._InterfaceClass__attrs
                continue

            info = self._infos[type_name]
            # Will be mutated later.
            # It's ok to do so in the current implementation of interfaces
            # if we only add new fields, and don't change existing ones.
            attrs = {}

            # Are we a container?
            is_container = bool([1 for node in info['nodes']
                                 if node['name'] == '*'])

            # Find which bases to use.
            bases = tuple([self._interfaces[sup]
                           for sup in info['supertypes']
                           if sup != 'nt:base' and not sup.startswith('mix:')])
            # Containers use IContainer, the rest Interface
            # XXX differentiate between container (dict) and list
            if is_container:
                bases += (IContainer,)
                # Add a __setitem__ so that we can use its precondition
                def __setitem__(name, obj):
                    """See zope.app.container.interfaces.IWriteContainer."""
                attrs['__setitem__'] = __setitem__
            elif not bases:
                bases = (zope.interface.Interface,)
            iface = InterfaceClass(type_name, bases, attrs,
                                   __module__='nuxeo.jcr.dynamic') # XXX
            self._interfaces[type_name] = iface
            new_fields[type_name] = attrs

        return new_fields

    _excluded_type_names = (
        'rep:system', # multiple * child nodes
        'rep:versionStorage', # multiple * child nodes
        'nt:frozenNode', # * properties
        'nt:unstructured', # * properties
        'nt:versionLabels', # * properties
        )

    _tops = (
        'nt:base',
        'mix:versionable',
        'mix:referenceable',
        )

    def _buildSchemas(self, type_names):
        """Build the full schemas for the given type names.
        """
        new_fields = self._makeInterfaces(type_names, self._tops)

        # Preconditions for container types checked first

        for type_name in type_names:
            if type_name in self._excluded_type_names:
                continue
            info = self._infos[type_name]
            iface = self._interfaces[type_name]
            fields = new_fields[type_name]

            for nodeinfo in info['nodes']:
                nodename = nodeinfo['name']
                if nodename != '*':
                    continue

                if nodeinfo['options']['multiple']:
                    raise ValueError("Multiple * child nodes are "
                                     "disallowed for [%s]" % type_name)

                req = nodeinfo['required_types']
                if not req:
                    schema = zope.interface.Interface
                elif len(req) == 1:
                    t = req[0]
                    try:
                        schema = self._interfaces[t]
                    except KeyError:
                        raise ValueError("Unknown type %s referenced by [%s] "
                                         "+ %s" % (t, type_name, nodename))
                else:
                    raise ValueError("Can't have more than one required type "
                                     "for [%s] + %s" % (type_name, nodename))

                try:
                    setitem = iface['__setitem__']
                except KeyError:
                    raise ValueError("Predefined interface %r must be "
                                     "a container" % iface)
                precondition = setitem.queryTaggedValue('precondition')
                if precondition is None:
                    precondition = ItemTypePrecondition()
                    setitem.setTaggedValue('precondition', precondition)
                precondition.types += (schema,)

        # Properties and single nodes

        for type_name in type_names:
            if type_name in self._excluded_type_names:
                continue
            info = self._infos[type_name]
            iface = self._interfaces[type_name]
            fields = new_fields[type_name]

            # Properties

            for propinfo in info['properties']:
                propname = propinfo['name']
                if propname == '*':
                    raise ValueError("* properties are disallowed for [%s]"
                                     % type_name)
                t = propinfo['type_name']
                if propinfo['options']['multiple']:
                    field = self.multiple_type_makers[t](self, propinfo)
                else:
                    field = self.type_makers[t](self, propinfo)
                fields[propname] = field

            # Single nodes

            for nodeinfo in info['nodes']:
                nodename = nodeinfo['name']
                if nodename == '*':
                    continue

                if nodeinfo['options']['multiple']:
                    raise ValueError("Same-name siblings are disallowed for "
                                     "[%s] + %s *" % (type_name, nodename))

                req = nodeinfo['required_types']
                if not req:
                    schema = zope.interface.Interface
                elif len(req) == 1:
                    t = req[0]
                    try:
                        schema = self._interfaces[t]
                    except KeyError:
                        raise ValueError("Unknown type %s referenced by [%s] "
                                         "+ %s" % (t, type_name, nodename))
                else:
                    raise ValueError("Can't have more than one required type "
                                     "for [%s] + %s" % (type_name, nodename))

                if schema.extends(IContainer):
                    names = (set(schema.names(all=True))
                             - set(IContainer.names(all=True))
                             - set(IObjectBase.names(all=True)))
                    if names:
                        from zope.interface import providedBy
                        raise ValueError(
                            "Cannot have container with properties (%s) "
                            "for [%s] + %s" %
                            (', '.join(sorted(names)),
                             type_name, nodename))
                    types = schema['__setitem__'].getTaggedValue(
                        'precondition').types
                    if len(types) > 1:
                        raise ValueError("List cannot hold more than one "
                                         "type for [%s] + %s" %
                                         (type_name, nodename))
                    # XXX check that we're orderable here, otherwise
                    # it's not really a List but a Dict
                    field = ListPropertyField(schema, __name__=nodename)
                else:
                    field = ObjectPropertyField(schema, __name__=nodename)

                fields[nodename] = field
