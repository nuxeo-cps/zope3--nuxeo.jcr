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
"""Fake JCR server.
"""

import zope.interface
from nuxeo.jcr.interfaces import IJCRController
from nuxeo.jcr.interfaces import ProtocolError


class FakeJCRNode(object):
    def __init__(self, name, type, parent_uuid, children, properties):
        self.name = name
        self.type = type
        self.parent_uuid = parent_uuid
        self.children = children
        self.properties = properties
        properties['jcr:primaryType'] = type

class FakeJCRController(object):
    """Fake JCR Controller.
    """
    zope.interface.implements(IJCRController)

    def __init__(self, db=None):
        self._db = db
        self._root_uuid = 'cafe-babe'
        root = FakeJCRNode('', 'rep:root', None, [], {})
        self._data = {self._root_uuid: root}
        self._next_uuid = 1

    def _addProperty(self, uuid, name, value):
        try:
            node = self._data[uuid]
        except KeyError:
            raise ValueError("No node %r" % uuid)
        if name in node.properties:
            raise ValueError("Node already has a property %r" % name)
        node.properties[name] = value

    def _addChild(self, parent_uuid, uuid, name, type, children, properties):
        if uuid in self._data:
            raise ValueError("Already has a node %r" % uuid)
        try:
            parent = self._data[parent_uuid]
        except KeyError:
            raise ValueError("No parent %r" % parent_uuid)
        # Create node
        node = FakeJCRNode(name, type, parent_uuid, children, properties)
        self._data[uuid] = node
        # Add to parent's children
        parent.children.append((name, uuid))

    def _getUUIDfromPath(self, path, uuid=None):
        if uuid is None:
            uuid = self._root_uuid
        if not path:
            return uuid
        node = self._data[uuid]
        name, next_path = path[0], path[1:]
        for child_name, child_uuid in node.children:
            if child_name == name:
                return self._getUUIDfromPath(next_path, child_uuid)
        raise ProtocolError

    #
    # API
    #

    def connect(self):
        pass

    def login(self, workspaceName):
        return self._root_uuid

    def getNodeTypeDefs(self):
        return self._db._nodetypedefs

    def getNodeType(self, uuid):
        try:
            node = self._data[uuid]
        except KeyError:
            raise ProtocolError(uuid)
        return node.type

    def getNodeTypeAndPath(self, uuid):
        try:
            node = self._data[uuid]
        except KeyError:
            raise ProtocolError(uuid)
        path = []
        current = node
        while current.name:
            path.append(current.name)
            current = current.parent
        path.reverse()
        return node.type, tuple(path)

    def getNodeStates(self, uuids):
        infos = {}
        for uuid in uuids:
            try:
                node = self._data[uuid]
            except KeyError:
                raise ProtocolError(uuid)
            children = [(name, cuuid, self._data[cuuid].type)
                        for name, cuuid in node.children]
            infos[uuid] = (node.name, node.parent_uuid,
                           children,
                           node.properties.items(),
                           [])
        return infos

    def sendCommands(self, commands):
        map = {}
        for command in commands:
            op = command[0]
            if op == 'add':
                token, path, node_type, props = command[1:]
                ppath, name = path[:-1], path[-1]
                try:
                    puuid = self._getUUIDfromPath(ppath)
                except ProtocolError:
                    raise ProtocolError("Unknown path %r" % '/'.join(ppath))
                uuid = 'cafe-%04d' % self._next_uuid
                self._next_uuid += 1
                self._addChild(puuid, uuid, name, node_type, [], props)
                map[token] = uuid
            elif op == 'modify':
                uuid, props = command[1:]
                try:
                    node = self._data[uuid]
                except KeyError:
                    raise ProtocolError(uuid)
                for key, value in props.iteritems():
                    if value is None:
                        if key in node.properties:
                            del node.properties[key]
                    else:
                        node.properties[key] = value
            elif op == 'remove':
                uuid = command[1]
                try:
                    node = self._data[uuid]
                except KeyError:
                    raise ProtocolError(uuid)
                puuid = node.parent_uuid
                del self._data[uuid]
                # Remove from parent's children
                if puuid is not None:
                    parent = self._data[puuid]
                    parent.children = [n for n in parent.children
                                       if n[1] != uuid]
            else:
                raise ProtocolError("invalid op %r" % (op,))
        return map

    def setNodeState(self, uuid, props):
        try:
            node = self._data[uuid]
        except KeyError:
            raise ProtocolError(uuid)
        for name, value in props:
            node.properties[name] = value

    def addNode(self, uuid, name, node_type):
        raise NotImplementedError

    def removeNode(self, uuid, name):
        raise NotImplementedError
