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
from persistent import Persistent
from ZODB.POSException import ConflictError
from ZODB.POSException import ReadConflictError
from ZODB.POSException import ConnectionStateError
from ZODB.POSException import InvalidObjectReference
from ZODB.Connection import Connection as ZODBConnection

from nuxeo.capsule.interfaces import IObjectBase
from nuxeo.capsule.interfaces import IChildren
from nuxeo.capsule.interfaces import IProperty
from nuxeo.capsule.interfaces import IListProperty
from nuxeo.capsule.interfaces import ICapsuleField
from nuxeo.capsule.interfaces import IListPropertyField
from nuxeo.capsule.interfaces import IObjectPropertyField

from nuxeo.jcr.impl import Children
from nuxeo.jcr.impl import NoChildrenYet
from nuxeo.jcr.impl import ListProperty
from nuxeo.jcr.impl import ObjectProperty
from nuxeo.jcr.impl import Document

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


class Connection(ZODBConnection):
    """Capsule Connection.

    Connection to a JCR storage.

    Misc notes about base ZODBConnection
    ------------------------------------

    - _cache is a PickleCache, a cache which can ghostify objects not
      recently used. Its API is roughly that of a dict, with additional
      gc-related and invalidation-related methods.

    - _added is a dict of oid->obj added explicitly through add().
      _added is used as a preliminary cache until commit time when
      objects are all moved to the real _cache. The objects are moved to
      _creating at commit time.

    - _registered_objects is the list registered objects. Objects can be
      registered by add(), when they are modified (and are not already
      registered) or when their access caused a ReadConflictError (just
      to be able to clean them up from the cache on abort with the other
      modified objects). All objects of this list are either in _cache
      or in _added.

    During commit, all objects go to either _modified or _creating:

    - _creating is a dict of oid->flag of new objects (without serial),
      either added by add() or implicitely added (discovered by the
      serializer during commit). The flag is True for implicit adding.
      _creating is used during abort to remove created objects from the
      _cache, and by persistent_id to check that a new object isn't
      reachable from multiple databases.

    - _modified is a list of oids modified, which have to be invalidated
      in the cache on abort and in other connections on finish.

    JCR Connection differences
    --------------------------

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

    At commit time, all objects in _added or all modified or deleted
    objects are written to storage. Objects in _added are moved to the
    permanent _cache with their new permanent oid decided by the
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
        super(Connection, self).__init__(db, version, cache_size)
        del self.new_oid # was set by ZODBConnection.__init__

        controller = db.controller_class(db)
        self.controller = controller

        controller.connect()
        self.root_uuid = controller.login(db.workspace_name)
        db.loadSchemas(controller)

        # States loaded but that have to wait for a persistent setstate()
        # call to be put in their apropriate object. Removed after set.
        self._pending_states = {}

        # Mapping of oid to a set of changed properties
        self._registered = {}
        # Mapping of oid to added objects
        self._added = {}
        # List of oids of added objects
        self._added_order = []

        # Oid that is being just being marked _p_changed for which
        # we don't want register() to freak out.
        self._manual_register = None

        # _modified and _created are filled at savepoint time

        # oids of modified objects (to be invalidated on an abort).
        self._modified = set()
        # oids of created objects (to be removed from cache on abort).
        # XXX differs from ZODB, where it's a dict oid->flag
        self._created = set()


    # Capsule API

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
                # Fast case: there is a previous value, update it
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
                    # Replacing a non-IProperty, assume the new one is the same
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
                    if IObjectPropertyField.providedBy(field):
                        prop = ObjectProperty(name, field.schema)
                    elif IListPropertyField.providedBy(field):
                        prop = ListProperty(name, field.value_type.schema)
                    else:
                        raise ValueError("Unknown property: %r" % field)
                    self._addNode(prop, obj)
                    prop.setPythonValue(value)
                    obj._props[name] = prop
                else:
                    # Setting a new non-IProperty
                    obj._props[name] = value
                    self._prop_changed(obj, name)

    def createItem(self, obj):
        """Create an item in a ListProperty.
        """
        assert IListProperty.providedBy(obj)
        assert obj._p_jar is self
        oid = obj._p_oid
        assert oid is not None
        assert '/' in oid

        name = oid.split('/', 1)[1]
        node = ObjectProperty(name, obj.getValueSchema())
        self._addNode(node, obj)
        return node

    def createChild(self, parent, name, node_type):
        """Create a child in a Document.

        The ``parent`` is the document, not the Children holder class.
        """
        assert IObjectBase.providedBy(parent)
        assert parent._p_jar is self
        poid = parent._p_oid
        assert poid is not None
        children = parent._children
        assert IChildren.providedBy(children)

        if name in children:
            raise KeyError("Child %r already exists" % name)

        # Are we creating the first child?
        if isinstance(children, NoChildrenYet):
            children = Children(parent)
            self._addNode(children, parent)
            parent.__dict__['_children'] = children

        # Create the child
        schema = self._db.getSchema(node_type)
        klass = self._db.getClass(node_type)
        child = klass(name, schema)
        self._addNode(child, children)
        children._children[name] = child
        if children._order is not None:
            children._order.append(name)
        return child

    def _maybeJoin(self):
        """Join the current transaction if not yet done.
        """
        if self._needs_to_join:
            self.transaction_manager.get().join(self)
            self._needs_to_join = False

    def _addNode(self, obj, parent):
        """Add a created node, give it an oid and register it.
        """
        self._maybeJoin()

        if IListProperty.providedBy(obj):
            oid = parent._p_oid + '/' + obj.getName()
        else:
            # Make a temporary oid
            oid = 'T%d' % self._next_tmp_uuid
            self._next_tmp_uuid += 1
        # Create the node
        obj.__parent__ = parent
        obj._p_oid = oid
        obj._p_jar = self
        self._added[oid] = obj
        self._added_order.append(oid)

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

        if oid in self._added:
            # Modifying a just-created object
            return

        self._maybeJoin()

        self._registered[oid] = set()

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
    # Resource Manager: two-phase commit

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
        # XXX send save/commit command


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
        for oid in self._modified:
            self._cache.invalidate(oid)
        for oid in self._registered:
            self._cache.invalidate(oid)
        for oid, obj in self._added.iteritems():
            del self._added[oid]
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
        if '/' in oid:
            klass = ListProperty
        else:
            if node_type is None:
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
        elif klass is Children:
            state = self._loadChildrenState(oid)
        elif klass is ListProperty:
            state = self._loadListPropertyState(oid)
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

        # This object (we're setting its state so it should be in cache)
        this = self._getFromCache(uuid)
        assert this is not None, ("Object loaded but not in cache", uuid)

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
        toskip = set()
        for child_name, child_uuid, child_type in jcrchildren:
            # Check if we have a ListProperty to create
            if child_name not in prop_map:
                field = schema.get(child_name)
                if IListPropertyField.providedBy(field):
                    oid = uuid + '/' + child_name
                    lprop = self.get(oid) # type not needed
                    if lprop._p_changed is None:
                        # Ghost, set state by indirect call to setstate()
                        lprop_state = {
                            '__name__': child_name,
                            '__parent__': this,
                            '_value_schema': field.value_type.schema,
                            '_values': [],
                            }
                        self._pending_states[oid] = lprop_state
                        lprop._p_activate()
                    else:
                        # We alreay have a non-ghost for the list property,
                        # ignore these children because their info is
                        # already taken care of.
                        toskip.add(child_name)
                    prop_map[child_name] = lprop
            if child_name in toskip:
                continue
            # Get child
            child = self.get(child_uuid, node_type=child_type)
            if child_name == 'ecm:children':
                if full_document:
                    children = child
            elif child_name in prop_map:
                # Multiple type, add to ListProperty
                prop_map[child_name]._values.append(child)
            else:
                # Simple type
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
                children = NoChildrenYet(this)
            state['_children'] = children

        return state

    def _loadChildrenState(self, uuid):
        """Load the state for a JCR Node representing children.
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
            '__name__': name, # XXX name is actually constant, and in class
            '__parent__': parent,
            '_children': child_map,
            '_order': order,
            }
        return state

    def _loadListPropertyState(self, oid):
        """Load the state of a ListProperty from the JCR.
        """
        uuid, prop_name = oid.split('/', 1)
        # XXX need a more specialized API here to get only prop_name children
        states = self.controller.getNodeStates([uuid])
        name, parent_uuid, jcrchildren, properties, deferred = states[uuid]
        assert deferred == [], deferred # XXX for now

        # Parent
        parent = self.get(uuid)

        # Properties to get type_name and schema
        type_name = 'ecm:UnknownType' # XXX
        for n, v in properties:
            if n == 'jcr:primaryType':
                type_name = v
                break
        schema = self._db.getSchema(type_name)
        value_schema = schema[prop_name].value_type.schema

        # JCR Children
        values = []
        for child_name, child_uuid, child_type in jcrchildren:
            if child_name != prop_name:
                # Only keep the wanted children # XXX optimize this
                continue
            child = self.get(child_uuid, node_type=child_type)
            values.append(child)

        # State
        state = {
            '__name__': name,
            '__parent__': parent,
            '_value_schema': value_schema,
            '_values': values,
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
            if '/' in toid:
                continue #XXX
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

        self._added = {}
        self._added_order = []
        self._registered = {}

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
            if '/' in oid:
                # ListProperty, no real existence in the JCR
                continue
            obj = self._added[oid]
            poid = obj.__parent__._p_oid
            if '/' in poid:
                # Parent is a ListProperty, add to its base UUID
                puuid, name = poid.split('/', 1)
            else:
                puuid = poid
                name = obj.__name__
            node_type = obj.getTypeName()
            props = self._collectSimpleProperties(obj)
            command = ('add', puuid, name, node_type, props, oid)
            yield command
        for oid, keys in self._registered.iteritems():
            obj = self._getFromCache(oid)
            props = self._collectProperties(obj, keys)
            command = ('modify', oid, props)
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
        raise TypeError("Savepoints unsupported")
