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

from copy import deepcopy

import zope.interface
from nuxeo.jcr.interfaces import IJCRController
from nuxeo.jcr.interfaces import ProtocolError
from nuxeo.jcr.interfaces import ConflictError


STORAGES = {}


class FakeJCRNode(object):
    def __init__(self, name, type, parent_uuid, children, properties):
        self.name = name
        self.type = type
        self.parent_uuid = parent_uuid
        self.children = children
        self.properties = properties
        properties['jcr:primaryType'] = type


class FakeJCR(object):

    def __init__(self):
        self.root_uuid = 'cafe-babe'
        root = FakeJCRNode('', 'rep:root', None, [], {})
        self.data = {self.root_uuid: root}
        self._next_uuid = 1

    def newUUID(self):
        uuid = 'cafe-%04d' % self._next_uuid
        self._next_uuid += 1
        return uuid

    def modifyProperties(self, uuid, props):
        try:
            node = self.data[uuid]
        except KeyError:
            raise ProtocolError(uuid)
        for key, value in props.iteritems():
            if value is None:
                if key in node.properties:
                    del node.properties[key]
            else:
                node.properties[key] = value

    def addChild(self, parent_uuid, uuid, name, type, children, properties):
        if uuid in self.data:
            raise ProtocolError("Already has a node %r" % uuid)
        try:
            parent = self.data[parent_uuid]
        except KeyError:
            raise ProtocolError("No parent %r" % parent_uuid)
        # Create node
        node = FakeJCRNode(name, type, parent_uuid, children, properties)
        self.data[uuid] = node
        # Add to parent's children
        parent.children.append((name, uuid))

    def removeNode(self, uuid):
        try:
            node = self.data[uuid]
        except KeyError:
            raise ProtocolError(uuid)
        puuid = node.parent_uuid
        del self.data[uuid]
        # Remove from parent's children
        if puuid is not None:
            parent = self.data[puuid]
            parent.children = [n for n in parent.children
                               if n[1] != uuid]

    def reorderChildren(self, uuid, inserts):
        try:
            children = self.data[uuid].children
        except KeyError:
            raise ProtocolError(uuid)
        names = [c[0] for c in children]
        for name, before in inserts:
            # Move `name` before `before`
            i = names.index(name)
            j = names.index(before)
            assert j < i
            x = children.pop(i)
            children.insert(j, x)
            # Do that in names too
            names.pop(i)
            names.insert(j, name)

    def getPath(self, uuid, name=None):
        """For error display.
        """
        path = []
        if name is not None:
            path.append(name)
        while uuid is not None:
            node = self.data[uuid]
            path.append(node.name)
            uuid = node.parent_uuid
        return '/'.join(reversed(path))


class Merger(object):
    """3-way merge of storages.
    """
    def __init__(self, initial, current, new):
        self.initial = initial
        self.current = current
        self.new = new

    def merge(self):
        self._merge(self.initial.root_uuid)

    def _merge(self, uuid):
        try:
            ini = self.initial.data[uuid]
            cur = self.current.data[uuid]
            new = self.new.data[uuid]
        except KeyError:
            raise # XXX
        self._mergeProperties(uuid, ini.properties, cur.properties,
                              new.properties)
        self._mergeChildren(uuid, ini.children, cur.children, new.children)

        # Recurse to merge children
        for name, child_uuid in new.children:
            if child_uuid in self.initial.data:
                # Node existed before, merge needed
                self._merge(child_uuid)

    def _mergeProperties(self, uuid, ini, cur, new):
        # No changes in cur, keep new
        if ini == cur:
            return

        # Added in cur
        for k in set(cur) - set(ini):
            if k in new:
                raise ConflictError("Adds of property %r" %
                                    self.current.getPath(uuid, k))
            new[k] = cur[k]

        # Removed in cur
        for k in set(ini) - set(cur):
            if k in new:
                if new[k] != ini[k]:
                    raise ConflictError("Change/remove of property %r" %
                                        self.initial.getPath(uuid, k))
                del new[k]

        # Changed
        for k, vcur in cur.iteritems():
            if k not in ini:
                continue
            vini = ini[k]
            if vini == vcur:
                continue
            if k not in new:
                raise ConflictError("Change/remove of property %r" %
                                    self.current.getPath(uuid, k))
            vnew = new[k]
            if vnew != vini:
                raise ConflictError("Changes of property %r" %
                                    self.current.getPath(uuid, k))
            new[k] = vcur

    def _mergeChildren(self, uuid, ini, cur, new):
        """Merge and returns impacted UUIDs in which to recurse.
        """
        # No changes in cur, keep new
        if ini == cur:
            return

        # No changes in new, use cur as new
        if ini == new:
            # Move added uuids from cur to new
            new_uuids = set(i[1] for i in cur) - set(i[1] for i in new)
            for child_uuid in new_uuids:
                self._moveUUID(child_uuid, self.current, self.new)
            new[:] = cur[:]
            return

        # Only adds in cur and new, merge them
        lini = len(ini)
        if cur[:lini] == ini and new[:lini] == ini:
            # Check disjoint names added
            added_cur = set(i[0] for i in cur[lini:])
            added_new = set(i[0] for i in new[lini:])
            conflicts = added_cur & added_new
            if conflicts:
                raise ConflictError("Adds of child %r" %
                                self.current.getPath(uuid, conflicts.pop()))
            # Move added from cur to new
            for name, child_uuid in cur[lini:]:
                self._moveUUID(child_uuid, self.current, self.new)
                new.append((name, child_uuid))
            return

        raise ConflictError("Unknown children merge situation")


    def _moveUUID(self, uuid, src, dst):
        """Move uuid and its children recursively from src to dst storages.
        """
        if uuid in dst.data:
            raise ValueError("UUID %r already in destination", uuid)
        dst.data[uuid] = src.data[uuid]
        for name, uuid in dst.data[uuid].children:
            self._moveUUID(uuid, src, dst)


class FakeJCRController(object):
    """Fake JCR Controller.
    """
    zope.interface.implements(IJCRController)

    def __init__(self, db=None):
        self.db = db
        key = self._getKey()
        if key not in STORAGES:
            STORAGES[key] = FakeJCR()

    def _getKey(self):
        return (self.db.database_name, self.db.workspace_name)

    #
    # API
    #

    def connect(self):
        pass

    def login(self, workspaceName):
        key = self._getKey()
        self.real_storage = STORAGES[key]
        self._begin()
        return self.storage.root_uuid

    def _begin(self):
        # Would be synchronized in real life but we're single threaded here
        self.initial_storage = deepcopy(self.real_storage)
        self.storage = deepcopy(self.real_storage)

    def prepare(self):
        # Apply all changes, may raise a conflict error
        # Would be synchronized in real life but we're single threaded here
        initial = self.initial_storage
        current = self.storage
        new = self.real_storage
        # Apply to `new` the changes from `initial` to `current`
        # This is really a 3-way merge
        Merger(initial, current, new).merge()
        self._begin()

    def commit(self):
        # Each storage is single-threaded, prepare did all the work
        pass

    def abort(self):
        self._begin()

    def newUUID(self):
        return self.real_storage.newUUID()

    def getNodeTypeDefs(self):
        return self.db._nodetypedefs

    def getNodeType(self, uuid):
        try:
            node = self.storage.data[uuid]
        except KeyError:
            raise ProtocolError(uuid)
        return node.type

    def getNodeStates(self, uuids):
        infos = {}
        for uuid in uuids:
            try:
                node = self.storage.data[uuid]
            except KeyError:
                raise ProtocolError(uuid)
            children = [(name, cuuid, self.storage.data[cuuid].type)
                        for name, cuuid in node.children]
            infos[uuid] = (node.name, node.parent_uuid,
                           children,
                           node.properties.items(),
                           [])
        return infos

    def sendCommands(self, commands):
        map = {} # token -> uuid
        for command in commands:
            op = command[0]
            if op == 'add':
                puuid, name, node_type, props, token = command[1:]
                if puuid in map:
                    puuid = map[puuid]
                uuid = self.newUUID()
                self.storage.addChild(puuid, uuid, name, node_type, [], props)
                map[token] = uuid
            elif op == 'modify':
                uuid, props = command[1:]
                if uuid in map:
                    uuid = map[uuid]
                self.storage.modifyProperties(uuid, props)
            elif op == 'remove':
                uuid = command[1]
                if uuid in map:
                    uuid = map[uuid]
                self.storage.removeNode(uuid)
            elif op == 'reorder':
                uuid, inserts = command[1:]
                if uuid in map:
                    uuid = map[uuid]
                self.storage.reorderChildren(uuid, inserts)
            else:
                raise ProtocolError("invalid op %r" % (op,))
        return map

    def getNodeProperties(self, uuid, names):
        raise NotImplementedError('Unused')

    def getPendingEvents(self):
        raise NotImplementedError('Unused')
