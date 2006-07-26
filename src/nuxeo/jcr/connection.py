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
"""Capsule Connection

The standard connection's dialogue with the JCR server is:

- login to a workspace, and get in result the root uuid to instanciate
  the root ghost,

- when a node is unghostified, ask for its state in an efficient manner,
  this includes all non-binary properties, and children,
  XXX later we'll avoid getting all children for big containers
  XXX later we'll avoid returning binary properties unless they're accessed
"""

import sys
import time
import logging
import threading
from random import randrange

from persistent import Persistent
from persistent import PickleCache
from ZODB.POSException import ConflictError
from ZODB.POSException import ReadConflictError
from ZODB.POSException import ConnectionStateError
from ZODB.POSException import InvalidObjectReference

from nuxeo.capsule.interfaces import IObjectBase
from nuxeo.capsule.interfaces import IContainerBase
from nuxeo.capsule.interfaces import IChildren
from nuxeo.capsule.interfaces import IProperty
from nuxeo.capsule.interfaces import IListProperty
from nuxeo.capsule.interfaces import ICapsuleField

from nuxeo.jcr.impl import Document
from nuxeo.jcr.impl import ObjectBase
from nuxeo.jcr.impl import ContainerBase
from nuxeo.jcr.impl import NoChildrenYet
from nuxeo.jcr.impl import ObjectProperty


_MARKER = object()


class Connection(object):
    """Capsule Connection.

    Connection to a JCR storage.

    JCR Connection differences from standard ZODB Connection
    --------------------------------------------------------

    - _registered_objects is not used, replaced by _registered.

    - _creating is not used, replaced by _created.

    - _registered is a mapping of oid to a set of modified attributes
      for object in the _cache. It doesn't include objects from _added.

    - _added_order is a list of oids of added objects, in the order they
      were added.

    - _created is a set, filled at commit/savepoint time with objects
      created.

    - _modified is a set, filled at commit/savepoint time with objects
      touched. Not that this can include object in _created from a
      previous savepoint.

    Lifecycle of a persistent object
    --------------------------------

    A persistent object starts its life as either:

    - a ghost synthesized as the child of a previously fetched object,
      it exists in the storage and is added to _cache,

    - a new object created when a node is added through _addNode(), it
      doesn't exist in the storage and is added to _added,

    - a full object fetched from storage through the get(oid) API (used
      normally only for debugging), it is added to _cache.

    An object in the _cache can be invalidated and turned into a ghost
    by a cache reduction (or manually). At its next access it will be
    refetched from storage through setstate(obj).

    At commit/savepoint time, all objects in _added or all modified or
    deleted objects are written to storage. Objects in _added are moved
    to the permanent _cache with their new permanent oid decided by the
    storage.

    JCR UUID
    --------

    JCR UUIDs become known in four ways:

    - the root,

    - when a UUID is retrieved as the child of a node,

    - when a UUID is retrived as the parent of a node,

    - when get() is called with an explicit oid.

    """

    # Temporary UUID counter for new objects. At commit time, their
    # UUID is replaced with the real one.
    _next_tmp_uuid = 1

    def __init__(self, db, version='', cache_size=1000):

        self._log = logging.getLogger('nuxeo.jcr.connection')

        self._db = db
        # Multi-database support
        self.connections = {db.database_name: self}

        self._needs_to_join = True
        self.transaction_manager = None

        self._opened = None # time.time() when DB.open() opened us
        self._load_count = 0   # Number of objects unghosted XXX
        self._store_count = 0  # Number of objects stored XXX

        controller = db.controller_class(db)
        self.controller = controller

        controller.connect()
        self.root_uuid = controller.login(db.workspace_name)
        db.loadSchemas(controller)

        # Cache of persisted objects
        self._cache = PickleCache(self, cache_size)

        # States loaded but that have to wait for a persistent setstate()
        # call to be put in their apropriate object. Removed after set.
        self._pending_states = {}

        # Mapping of oid to a set of changed properties
        self._registered = {}
        # Mapping of (temporary) oid to added objects
        self._added = {}
        # List of oids of added objects
        self._added_order = []

        # Additional commands to send after savepoint/commit
        # Used for removes or reorderings
        self._commands = []

        # Oid that is being just being marked _p_changed for which
        # we don't want register() to freak out.
        self._manual_register = None

        # _modified and _created are filled at savepoint time

        # oids of modified objects (to be invalidated on an abort).
        self._modified = set()
        # oids of created objects (to be removed from cache on abort).
        self._created = set()


        # XXX Invalidation

        self._inv_lock = threading.Lock()
        self._invalidated = d = {}

        # We intend to prevent committing a transaction in which
        # ReadConflictError occurs.  _conflicts is the set of oids that
        # experienced ReadConflictError.  Any time we raise ReadConflictError,
        # the oid should be added to this set, and we should be sure that the
        # object is registered.  Because it's registered, Connection.commit()
        # will raise ReadConflictError again (because the oid is in
        # _conflicts).
        self._conflicts = {}

        # If MVCC is enabled, then _mvcc is True and _txn_time stores
        # the upper bound on transactions visible to this connection.
        # That is, all object revisions must be written before _txn_time.
        # If it is None, then the current revisions are acceptable.
        # If the connection is in a version, mvcc will be disabled, because
        # loadBefore() only returns non-version data.
        self._txn_time = None

    def db(self):
        """Returns the databse.

        Called by serialize.ObjectWriter to check database in case of
        foreign database storage.
        """
        return self._db

    def _implicitlyAdding(self, oid):
        """Called by serialize.ObjectWriter.
        """
        return False

    def open(self, transaction_manager=None, mvcc=True, synch=True,
             delegate=None):
        """Open this connection for use.

        This method is called by the DB every time a Connection is
        opened. Any invalidations received while the Connection was
        closed will be processed.

        `delegate` is used by ZODB.Connection for multi-database opens.
        """
        self._opened = time.time()
        self._synch = synch
        self._mvcc = mvcc

        if transaction_manager is None:
            transaction_manager = transaction.manager
        self.transaction_manager = transaction_manager

        #self._flush_invalidations()

        if synch:
            transaction_manager.registerSynch(self)

        #if delegate:
        #    # delegate open to secondary connections
        #    for connection in self.connections.values():
        #        if connection is not self:
        #            connection.open(transaction_manager, mvcc, synch, False)

    def close(self, primary=None):
        """Called by App.ZApplication to cleanup.
        """
        if not self._needs_to_join:
            raise ConnectionStateError("Cannot close a connection joined to "
                                       "a transaction")
        self.cacheGC()

        if self._synch:
            self.transaction_manager.unregisterSynch(self)
            self._synch = False

        self._opened = None # XXX

    def cacheGC(self):
        """Reduce cache size to target size.

        Called by DB on connection open.
        """
        self._cache.incrgc()

    ##################################################

    # Capsule API

    def getSchemaManager(self):
        return self._db._schema_manager

    ##################################################
    # Add/Modify/Remove

    def setProperty(self, obj, name, value):
        """Set a property on an object.
        """
        assert IObjectBase.providedBy(obj)
        assert obj._p_jar is self
        oid = obj._p_oid
        assert oid is not None

        if value is None:
            if name in obj._props:
                # Remove
                old = obj._props[name]
                del obj._props[name]
                if IProperty.providedBy(old):
                    self.deleteNode(old)
                else:
                    self._prop_changed(obj, name)
        else:
            old = obj._props.get(name, _MARKER)
            if old is not _MARKER:
                # If there is a previous value, update it
                if IProperty.providedBy(old):
                    if IProperty.providedBy(value):
                        # Are we setting the same property?
                        if old._p_oid == value._p_oid:
                            raise NotImplementedError
                        else:
                            raise ValueError("Cannot replace property %r "
                                             "with %r" % (old, value))
                    else:
                        old.setDTO(value)
                else:
                    # Updating a non-IProperty
                    assert not IProperty.providedBy(value), value
                    obj._props[name] = value
                    self._prop_changed(obj, name)
            else:
                # No previous value, create one
                assert not IProperty.providedBy(value), value
                try:
                    field = obj.getSchema()[name]
                except KeyError:
                    raise KeyError("Schema %r has no property %r" %
                                   (obj.getSchema().getName(), name))
                if ICapsuleField.providedBy(field):
                    prop = self._addNode(obj, name, field.schema)
                    prop.setDTO(value)
                    obj._props[name] = prop
                else:
                    # Setting a new non-IProperty
                    obj._props[name] = value
                    self._prop_changed(obj, name)

    def newValue(self, obj, name=None):
        """Create a new item for a ListProperty.
        """
        assert IListProperty.providedBy(obj)
        assert obj._p_jar is self
        oid = obj._p_oid
        assert oid is not None

        if name is None:
            name = str(randrange(0, 2<<30)) # XXX better random id?
        schema = obj.getValueSchema()
        return self._addNode(obj, name, schema)

    def createChild(self, container, name, node_type):
        """Create a child in a IContainerBase.

        Returns the created child.
        """
        assert IContainerBase.providedBy(container), container
        assert container._p_jar is self
        poid = container._p_oid
        assert poid is not None

        schema = self._db.getSchema(node_type)
        return self._addNode(container, name, schema)

    def deleteNode(self, obj):
        """Delete a node.
        """
        assert obj._p_jar is self
        oid = obj._p_oid
        assert oid is not None

        self._maybeJoin()

        self._commands.append(('remove', oid))
        self.savepoint()

    def reorderChildren(self, obj, old, new):
        """Reorder children.

        `old` and `new` are lists of names.
        """
        assert IContainerBase.providedBy(obj), obj
        assert obj._p_jar is self
        oid = obj._p_oid
        assert oid is not None

        # Fast case, avoid extra work
        if old == new:
            return

        self._maybeJoin()

        # Mark object changed
        try:
            self._manual_register = oid
            obj._p_changed = True
        finally:
            self._manual_register = None

        inserts = findInserts(old, new)
        self._commands.append(('reorder', oid, inserts))
        self.savepoint()

    def _maybeJoin(self):
        """Join the current transaction if not yet done.
        """
        if self._needs_to_join:
            self.transaction_manager.get().join(self)
            self._needs_to_join = False

    def _addNode(self, parent, name, schema):
        """Create and register a new node

        Give it a (temporary) oid.
        """
        self._maybeJoin()

        # Create instance
        klass = self._db.getClass(schema.getName())
        obj = klass(name, schema)

        # Make a temporary oid
        oid = 'T%d' % self._next_tmp_uuid
        self._next_tmp_uuid += 1

        # Register the node
        obj.__parent__ = parent
        obj._p_oid = oid
        obj._p_jar = self
        self._added[oid] = obj
        self._added_order.append(oid)

        # Mark parent changed # XXX should mark only for invalidation
        try:
            self._manual_register = parent._p_oid
            parent._p_changed = True
        finally:
            self._manual_register = None

        return obj


    def register(self, obj):
        """Register obj as modified.

        Called by the persistence machinery when an object's state
        changes to CHANGED.

        Does not actually record useful information, but is used to flag
        objects modified directly without going to the Capsule API,
        which is an error.
        """
        assert obj._p_jar is self
        oid = obj._p_oid
        assert oid is not None
        assert oid not in self._registered

        # Modifying a just-created object
        if oid in self._added:
            return

        self._maybeJoin()

        self._registered[oid] = set()

        # Check for direct modifications not going through setProperty
        if self._manual_register != oid:
            print 'XXX illegal direct attr modification of', repr(obj)

    def _prop_changed(self, obj, name):
        """Register a property name as changed.
        """
        oid = obj._p_oid

        if oid in self._added:
            return

        try:
            self._manual_register = oid
            obj._p_changed = True
        finally:
            self._manual_register = None

        self._registered[oid].add(name)

    ##################################################
    # ISynchronizer (we register ourselves when the connection is opened)

    def beforeCompletion(self, txn):
        # We don't do anything before a commit starts.
        pass

    # Call the underlying storage's sync() method (if any), and process
    # pending invalidations regardless.  Of course this should only be
    # called at transaction boundaries.
    def _storage_sync(self, *ignored):
        return # XXX
        sync = getattr(self._storage, 'sync', 0)
        if sync:
            sync()
        self._flush_invalidations()

    afterCompletion =  _storage_sync
    newTransaction = _storage_sync

    ##################################################
    # Resource Manager: two-phase commit

    def sortKey(self):
        """Consistent sort key for this connection.
        """
        return self._db.workspace_name + ':' + str(id(self))

    def tpc_begin(self, txn):
        """Begin commit of a transaction, starting the two-phase commit.
        """
        pass

    def commit(self, txn):
        """Commit the modified objects and their dependents to the storage.

        This is half the 'prepare' phase of the two-phase commit, where
        the bulk of the objects are committed.
        """
        self.savepoint()
        self.controller.prepare()

    def tpc_vote(self, txn):
        """Verify that the transaction can be committed.

        This is the second half of the 'prepare' phase of the two-phase
        commit.
        """
        self.controller.commit()

    def tpc_finish(self, txn):
        """Finalize the transaction commit.

        This is the 'commit' phase of the two-phase commit, it is called
        when all resource managers have voted successfully.
        """
        # XXX invalidation callbacks
        self._tpc_cleanup()


    def abort(self, txn):
        """Abort a transaction.

        Called for explicit transaction aborts.

        Also called before tpc_abort in two-phase commit if this
        resource manager has not voted.
        """
        self.controller.abort()
        for oid in self._modified:
            self._cache.invalidate(oid)
        for oid in self._registered:
            self._cache.invalidate(oid)
        for obj in self._added.itervalues():
            del obj._p_jar
            del obj._p_oid
        for oid in self._created:
            obj = self._cache[oid]
            del self._cache[oid]
            del obj._p_jar
            del obj._p_oid

        self._tpc_cleanup()

    def tpc_abort(self, txn):
        """Abort a transaction.

        Called when a two-phase commit aborts.

        Invalidates objects savepointed.
        """
        self.abort(txn)

    def _tpc_cleanup(self):
        """Cleanup after finish or abort.
        """
        self._modified = set()
        self._created = set()

        self._conflicts.clear()
        #if not self._synch:
        #    self._flush_invalidations() # XXX invalidations

        self._needs_to_join = True

        self._cleanup_savepoint()

    ##################################################
    # Export/Import

    def exportFile(self, oid, f=None):
        raise NotImplementedError

    def importFile(self, f, clue='', customImporters=None):
        raise NotImplementedError

    def _importDuringCommit(self, transaction, f, return_oid_list):
        raise NotImplementedError

    ##################################################
    # Load

    def root(self):
        """Get database root object.
        """
        return self.get(self.root_uuid)

    def get(self, oid, node_type=None):
        """Get the persistent object with a given oid.

        Returns the object from the cache if it's there. Otherwise
        returns a ghost.

        If a ghost has to be built and node_type is passed, no
        round-trip to the server is needed to get class information.
        """
        obj = self._getFromCache(oid)
        if obj is None:
            obj = self._makeGhost(oid, node_type)
        return obj

    __getitem__ = get

    def _getFromCache(self, oid):
        """Get an object for an oid if we already have it.

        Otherwise, returns None.
        """
        obj = self._cache.get(oid)
        if obj is None:
            obj = self._added.get(oid)
        return obj

    def _makeGhost(self, oid, node_type):
        """Create a ghost object for a given oid and node type.

        The ghost is then put in the cache.

        If node_type is None, the storage will be queried.
        """
        if node_type is None:
            # XXX make sure we rarely call this
            node_type = self.controller.getNodeType(oid)
        klass = self._db.getClass(node_type)
        obj = klass.__new__(klass)
        obj._p_oid = oid
        obj._p_jar = self
        obj._p_deactivate() # Switch to ghost
        self._cache[oid] = obj
        return obj

    def setstate(self, obj):
        """Set the state on an object.

        This fills a ghost object with its proper state.

        Called by the persistence machinery to unghostifiy an object.
        """
        oid = obj._p_oid
        if self._opened is None:
            msg = ("Shouldn't load state for %s "
                   "when the connection is closed" % oid)
            self._log.error(msg)
            raise ConnectionStateError(msg)
        try:
            self._setstate(obj)
        except ConflictError:
            raise
        except:
            self._log.error("Couldn't load state for %s", oid,
                            exc_info=sys.exc_info())
            raise

    def _setstate(self, obj):
        """Set the state on an object.
        """
        oid = obj._p_oid
        klass = obj.__class__

        if oid in self._invalidated:
            # XXX here deal with manual MVCC loading and _p_independent
            # XXX ZODB uses _load_before_or_conflict
            raise ReadConflictError(object=obj)

        # Get state from JCR
        if oid in self._pending_states:
            # State was already loaded, needs to be set through a
            # setstate() call.
            state = self._pending_states.pop(oid)
        elif issubclass(klass, (ObjectBase, ContainerBase)):
            state = self._loadNodeState(obj, oid)
        else:
            raise ValueError("Unknown class %s.%s" %
                             (klass.__module__, klass.__name__))

        # Put state on the object
        obj.__setstate__(state)


    def _loadNodeState(self, obj, uuid):
        """Load the state of a Node from the JCR.

        The node may have Object aspects (holds properties), and
        Container aspect (holds children).
        """
        states = self.controller.getNodeStates([uuid])
        name, parent_uuid, jcrchildren, properties, deferred = states[uuid]
        assert deferred == [], deferred # XXX for now

        # Parent
        if parent_uuid is not None:
            parent = self.get(parent_uuid)
        else:
            parent = None

        state = {}

        # JCR properties
        prop_map = {}
        type_name = 'ecm:UnknownType' # XXX
        for prop_name, prop_value in properties:
            if prop_name == 'jcr:primaryType':
                type_name = prop_value
                # don't put jcr:primaryType in properties
            else:
                prop_map[prop_name] = prop_value
                if isinstance(obj, Document):
                    # Magic properties to map security
                    func = obj.__setattr_special_properties__.get(prop_name)
                    if func is not None:
                        func(obj, prop_value, state)

        schema = self._db.getSchema(type_name)

        # JCR children
        if isinstance(obj, ContainerBase):
            # Children node are put in _children
            children = {}
            order = [] # XXX check if type is ordered in its schema
            for child_name, child_uuid, child_type in jcrchildren:
                if child_type == 'nt:unstructured': # XXX skip debug stuff
                    continue
                child = self.get(child_uuid, node_type=child_type)
                children[child_name] = child
                order.append(child_name)
        else:
            # Children node are complex properties except ecm:children
            children = None
            order = None
            for child_name, child_uuid, child_type in jcrchildren:
                child = self.get(child_uuid, node_type=child_type)
                if child_name == 'ecm:children':
                    children = child
                else:
                    # Complex property
                    prop_map[child_name] = child
            if children is None:
                this = self._getFromCache(uuid)
                assert this is not None, ("Object not in cache", uuid)
                children = NoChildrenYet(this)

        # State
        state.update({
            '__name__': name,
            '__parent__': parent,
            '_schema': schema,
            '_props': prop_map,
            '_children': children,
            '_order': order,
            })

        return state

    ##################################################
    # Search

    def locateUUID(self, uuid):
        """Get the path of a doc with a given UUID.

        The path is relative to JCR workspace root and translated
        to remove 'ecm:children' components.
        """
        path = self.controller.getPath(uuid)
        if path is None:
            return None
        path = path.replace('/ecm:children/', '/')
        if path[0] != '/':
            raise ValueError(path)
        return path[1:]

    def searchProperty(self, prop_name, value):
        """Search the JCR for nodes where prop_name = 'value'.

        Returns a sequence of (uuid, path).

        The paths are relative to JCR workspace root and translated
        to remove 'ecm:children' components.
        """
        results = self.controller.searchProperty(prop_name, value)
        res = []
        for uuid, path in results:
            path = path.replace('/ecm:children/', '/')
            if path[0] != '/':
                raise ValueError(path)
            res.append((uuid, path[1:]))
        return res

    ##################################################
    # Versioning

    def checkin(self, obj):
        assert obj._p_jar is self
        self.savepoint()
        oid = obj._p_oid
        assert oid is not None
        self.controller.checkin(oid)
        # Deactivate the node, some properties have changed
        obj._p_deactivate()
        # Deactivate the version history, its children have changed
        # (this refetches obj as a side effect)
        vhuuid = obj.getProperty('jcr:versionHistory').getTargetUUID()
        vh = self._cache.get(vhuuid)
        if vh is not None:
            vh._p_deactivate()

    def checkout(self, obj):
        assert obj._p_jar is self
        self.savepoint()
        oid = obj._p_oid
        assert oid is not None
        self.controller.checkout(oid)
        # Deactivate the node, some properties have changed
        obj._p_deactivate()

    ##################################################
    # Save

    def savepoint(self):
        """Send the current modifications to the JCR, and do a JCR save.

        This operation is needed before a commit, or before any JCR
        operation that works on the persistently saved data, like
        copy or move.
        """
        self._maybeJoin()

        commands = self._saveCommands()
        map = self.controller.sendCommands(commands)

        # Replace temporary oids with final ones, and put new objects in cache
        for toid, obj in self._added.iteritems():
            oid = map[toid]
            obj._p_oid = oid
            obj._p_changed = False
            self._cache[oid] = obj
            self._created.add(oid)

        # Remember modified objects
        for oid in self._registered.iterkeys():
            obj = self._getFromCache(oid)
            obj._p_changed = False
            self._modified.add(oid)

        self._cleanup_savepoint()

        return NoRollbackSavepoint()

    def _cleanup_savepoint(self):
        self._registered = {}
        self._added = {}
        self._added_order = []
        self._commands = []

    def _saveCommands(self):
        """Generator returning the commands to save the modifications.

        Commands are a tuple, which can be:
        - 'add', parent_uuid, name, node_type, props_mapping, token
        - 'modify', uuid, props_mapping
        - 'remove', uuid
        - 'reorder', uuid, reordering_list
        """
        for oid in self._added_order:
            obj = self._added[oid]
            puuid = obj.__parent__._p_oid
            name = obj.__name__
            node_type = obj.getTypeName()
            props = self._collectSimpleProperties(obj)
            yield ('add', puuid, name, node_type, props, oid)
        for oid, keys in self._registered.iteritems():
            obj = self._getFromCache(oid)
            props = self._collectProperties(obj, keys)
            yield ('modify', oid, props)
        for command in self._commands:
            yield command

    def _collectProperties(self, obj, keys, skip_none=False):
        """Collect properties to send in a command.

        ``keys`` is a set of property names.
        """
        props = {}
        if '__unknown__' in keys:
            raise ValueError("info for %r with unknown %r" % (obj, keys))
        for key in keys:
            value = obj._props.get(key, None)
            if value is None and skip_none:
                continue
            assert not isinstance(value, Persistent), ("Persistent "
                "value %r in property %r" % (value, key))
            props[key] = value
        return props

    def _collectSimpleProperties(self, obj):
        """Get the simple properties from an object.
        """
        if IChildren.providedBy(obj):
            return {}
        props = {}
        for key, value in obj._props.iteritems():
            if IProperty.providedBy(value):
                continue
            assert not isinstance(value, Persistent), ("Persistent "
                "value %r in property %r" % (value, key))
            props[key] = value
        return props


class NoRollbackSavepoint(object):
    def rollback(self):
        raise TypeError("Savepoint rollback unsupported")


def findInserts(old, new):
    """Find the 'insertBefore' commands needed to turn `old` into `new`.
    """
    if set(old) != set(new):
        raise ValueError("Names mismatch (%r to %r)" % (old, new))
    old = list(old)
    new = list(new)
    inserts = []
    # Change old until it's equal to new
    # FIXME: stupid quadratic algorithm
    while old != new:
        for i, name in enumerate(new):
            n = old[i]
            # Find first difference
            if n != name:
                # Put name at position i in old
                inserts.append((name, n)) # insert name before n
                # Replay that in old
                assert old.index(name) > i
                old.remove(name)
                old.insert(i, name)
                break
    return inserts

