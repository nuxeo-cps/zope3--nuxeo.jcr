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
from nuxeo.capsule.interfaces import IListPropertyField
from nuxeo.capsule.interfaces import IObjectPropertyField

from nuxeo.jcr.impl import Document
from nuxeo.jcr.impl import ContainerBase
from nuxeo.jcr.impl import Children
from nuxeo.jcr.impl import NoChildrenYet
from nuxeo.jcr.impl import ListProperty
from nuxeo.jcr.impl import ObjectProperty

_MARKER = object()

class Root(object):
    """Base storage root that only allows traversal to the real root.

    A mount points always traverses to an explicit root in the storage,
    or by default to 'Application'.
    """
    def __init__(self, cnx):
        self.cnx = cnx
    def __getitem__(self, key):
        if key != 'Application':
            raise KeyError(key)
        return self.cnx.get(self.cnx.root_uuid)


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

    - a new object created when a node is added through
      _registerAdded(), it doesn't exist in the storage and is added to
      _added,

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
        self.connections = {self._db.database_name: self}

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

        # List of oids to remove (flushed immediately for now)
        self._removed = []

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

    def open(self, transaction_manager=None, mvcc=True, synch=True):
        """Open this connection for use.

        This method is called by the DB every time a Connection is
        opened. Any invalidations received while the Connection was
        closed will be processed.
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

    def cacheGC(self):
        """Reduce cache size to target size.

        Called by DB on connection open.
        """
        self._cache.incrgc()

    ##################################################

    # Capsule API XXX

    def getSchemaManager(self):
        return self._db._schema_manager

    def getTypeManager(self):
        return self._db._type_manager

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
                    raise NotImplementedError
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
                        old.setPythonValue(value)
                else:
                    # Updating a non-IProperty
                    assert not IProperty.providedBy(value), value
                    obj._props[name] = value
                    self._prop_changed(obj, name)
            else:
                # No previous value, create one
                field = obj.getSchema()[name]
                if ICapsuleField.providedBy(field):
                    if IProperty.providedBy(value):
                        # XXX do we allow this?
                        raise ValueError("Must create %r from simple types"
                                         % name)
                    if IListPropertyField.providedBy(field):
                        prop = ListProperty(name, field.schema)
                    elif IObjectPropertyField.providedBy(field):
                        prop = ObjectProperty(name, field.schema)
                    else:
                        raise ValueError("Unknown property: %r" % field)
                    self._registerAdded(prop, obj)
                    prop.setPythonValue(value)
                    obj._props[name] = prop
                else:
                    # Setting a new non-IProperty
                    assert not IProperty.providedBy(value), value
                    obj._props[name] = value
                    self._prop_changed(obj, name)

    def addValue(self, obj):
        """Create an item in a ListProperty.
        """
        assert IListProperty.providedBy(obj)
        assert obj._p_jar is self
        oid = obj._p_oid
        assert oid is not None

        name = str(randrange(0, 2<<30)) # XXX better random id?
        node = ObjectProperty(name, obj.getValueSchema())
        self._registerAdded(node, obj)
        obj._children[name] = node
        if obj._order is not None:
            obj._order.append(name)
        return node

    def createChild(self, container, name, node_type):
        """Create a child in a IContainerBase.

        Returns the created child.
        """
        assert IContainerBase.providedBy(container), container
        assert container._p_jar is self
        poid = container._p_oid
        assert poid is not None

        schema = self._db.getSchema(node_type)
        klass = self._db.getClass(node_type)
        child = klass(name, schema)
        self._registerAdded(child, container)
        return child

    def deleteNode(self, obj):
        """Delete a node.
        """
        assert obj._p_jar is self
        oid = obj._p_oid
        assert oid is not None

        self._maybeJoin()

        self._removed.append(oid)
        self.savepoint()

    def _maybeJoin(self):
        """Join the current transaction if not yet done.
        """
        if self._needs_to_join:
            self.transaction_manager.get().join(self)
            self._needs_to_join = False

    def _registerAdded(self, obj, parent):
        """Register a created node and give it a (temporary) oid.
        """
        self._maybeJoin()

        # Make a temporary oid
        oid = 'T%d' % self._next_tmp_uuid
        self._next_tmp_uuid += 1

        # Register the node
        obj.__parent__ = parent
        obj._p_oid = oid
        obj._p_jar = self
        self._added[oid] = obj
        self._added_order.append(oid)

        # Mark parent changed
        try:
            self._manual_register = parent._p_oid
            parent._p_changed = True
        finally:
            self._manual_register = None

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

    def remove(self, obj):
        raise NotImplementedError

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
        self.controller.commit()

    def tpc_vote(self, txn):
        """Verify that the transaction can be committed.

        This is the second half of the 'prepare' phase of the two-phase
        commit.
        """
        return

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
        self._registered = {}
        self._added = {}
        self._added_order = []
        self._removed = []

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
        return Root(self)

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
        elif issubclass(klass, Document): # or use interfaces?
            state = self._loadObjectState(oid, full_document=True)
        elif issubclass(klass, ContainerBase):
            state = self._loadContainerState(oid)
        elif klass is ObjectProperty:
            state = self._loadObjectState(oid)
        else:
            raise ValueError("Unknown class %s.%s" %
                             (klass.__module__, klass.__name__))

        # Put state on the object
        obj.__setstate__(state)


    def _loadObjectState(self, uuid, full_document=False):
        """Load the state of a Node from the JCR.

        This Node represents either an IObjectBase or a full document
        with children (IDocument).

        Property values are also loaded if they're cheap (no Binary).
        (The decision is made by the server.)
        """
        states = self.controller.getNodeStates([uuid])
        name, parent_uuid, jcrchildren, properties, deferred = states[uuid]
        assert deferred == [], deferred # XXX for now

        # Parent
        if parent_uuid is not None:
            parent = self.get(parent_uuid)
        else:
            parent = None

        # JCR properties
        prop_map = {}
        type_name = 'ecm:UnknownType' # XXX
        for prop_name, prop_value in properties:
            if prop_name == 'jcr:primaryType':
                type_name = prop_value
                # don't put jcr:primaryType in properties
            else:
                prop_map[prop_name] = prop_value
        schema = self._db.getSchema(type_name)

        # JCR children
        children = None
        for child_name, child_uuid, child_type in jcrchildren:
            child = self.get(child_uuid, node_type=child_type)
            if child_name == 'ecm:children':
                if full_document:
                    children = child
            else:
                # Complex property
                prop_map[child_name] = child

        # State
        state = {
            '__name__': name,
            '__parent__': parent,
            '_schema': schema,
            '_props': prop_map,
            }
        if full_document:
            if children is None:
                this = self._getFromCache(uuid)
                assert this is not None, ("Object not in cache", uuid)
                children = NoChildrenYet(this)
            state['_children'] = children

        return state

    def _loadContainerState(self, uuid):
        """Load the state for a JCR Node which is a container
        (Children or ListProperty)
        """
        states = self.controller.getNodeStates([uuid])
        name, parent_uuid, jcrchildren, properties, deferred = states[uuid]
        assert deferred == [], deferred # XXX for now

        # Parent
        if parent_uuid is not None:
            parent = self.get(parent_uuid)
        else:
            parent = None

        # JCR Children
        child_map = {}
        order = [] # XXX check if type is ordered in its schema
        for child_name, child_uuid, child_type in jcrchildren:
            child = self.get(child_uuid, node_type=child_type)
            child_map[child_name] = child
            order.append(child_name)
        # XXX _lazy, _missing

        # State
        state = {
            '__name__': name,
            '__parent__': parent,
            '_children': child_map,
            '_order': order,
            }
        return state


    ##################################################
    # Save

    def savepoint(self):
        """Send the current modifications to the JCR, and do a JCR save.

        This operation is needed before a commit, or before any JCR
        operation that works on the persistently saved data, like
        checkin, checkout, copy, move.
        """
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

        self._registered = {}
        self._added = {}
        self._added_order = []
        self._removed = []

        return NoRollbackSavepoint()

    def _saveCommands(self):
        """Generator returning the commands to save the modifications.

        Commands are a tuple, which can be:
        - 'add', parent_uuid, name, node_type, props_mapping, token
        - 'modify', uuid, props_mapping
        - 'remove', uuid XXX
        - 'order' XXX
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
        for oid in self._removed:
            yield ('remove', oid)

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
