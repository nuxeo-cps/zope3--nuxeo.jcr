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
from nuxeo.treedelta import FullTreeDelta, ADD, REMOVE, MODIFY
from ZODB.POSException import ConflictError
from ZODB.POSException import ReadConflictError
from ZODB.POSException import ConnectionStateError
from ZODB.POSException import InvalidObjectReference
from ZODB.Connection import Connection as ZODBConnection

from nuxeo.capsule.interfaces import IObjectBase # XXX
from nuxeo.capsule.interfaces import IListPropertyField
from nuxeo.capsule.interfaces import IObjectPropertyField
from nuxeo.jcr.interfaces import INonPersistent

from nuxeo.capsule.base import Children
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

    Misc notes about ZODBConnection:

    _added is a dict of oid->obj added explicitely through add(). _added
    is used as a sort of preliminary cache until commit time where
    they're all moved to the real _cache. The object are moved to
    _creating at commit time.

    _registered_objects is the list of objects registered by Persistence
    when the object was first changed, or by add(). It also contains
    objects who ended up in a ReadConflictError, just to be able to
    clean them up from the cache on abort with the other modified
    objects.

    _creating is a dict of oid->flag of new objects (without serial),
    either added by add() or implicitely created (during commit). The
    flag is True for implicit adding. _creating is used during abort to
    remove created objects from the cache, and by persistent_id to check
    that a new object isn't reachable from multiple databases. It's
    filled at commit time.

    _modified is a list of oids modified, which have to be invalidated
    in the cache on abort and in other connections on finish. It's
    filled at commit time.

    During commit, all stored objects go into either _modified or
    _creating.


    Lifecycle of a persistent object
    --------------------------------

    A persistent object starts its life as either:

    - a ghost synthesized as the child of a previously fetched object,
      it exists in the storage and is added to _cache,

    - a new object created when a node is added through _addNode(), it
      doesn't exist in the storage and is added to _added,

    - a full object fetched from storage through the get(oid) API (used
      normally only for debugging), it is added to _cache.

    All objects in _cache and _added are also present by oid in
    _jcr_paths. More oids can also be present there if the _cache has
    been garbage collected of old ghosts. These additional oids don't
    matter because they refer to unreachable objects; when they become
    really unreachable from the storage it will send an invalidation
    message for them, and they will be purged from _cache and _jcr_paths
    if needed.

    An object in the _cache can be invalidated and turned into a ghost
    by a cache reduction (or manually). At its next access it will be
    refetched from storage through setstate(obj).

    At commit time, all objects in _added or all modified or deleted
    objects are written to storage by walking the _jcr_delta tree.
    Objects in _added are moved to the permanent _cache with their new
    permanent oid decided by the storage.

    JCR UUID
    --------

    JCR UUIDs become known in four ways:

    - the root, which has a known JCR path,

    - when a UUID is retrieved as the child of a node, then the JCR path
      is known if the current one is known,

    - when a UUID is retrived as the parent of a node, then the JCR path
      is known if the current one is known,

    - when get() is called with an explicit oid, in this case the JCR
      path is not known and has to be asked to the storage.


    """

    # Store the current tree delta, used to know in what order to
    # send modifications to the JCR; the paths are JCR paths. When
    # same-name siblings are involved they must *all* be impacted.
    _jcr_delta = None

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

        # Oid that is being just being marked _p_changed for which
        # we don't want register() to freak out
        self._pending_register = None

        # Map of oid -> path for all objects in _cache or in _added.
        # Paths and are used to fill _jcr_delta when an operation is done.
        # All objects with an oid are in this map. This means that when
        # setstate is called, we know we have a path for them.
        # A INonPersistent object also has a path (those of its children
        # for ListProperty (but it's not present in the _jcr_delta)).
        self._oid_to_path = {}
        # Inverse map, used when the delta is read
        self._path_to_oid = {}

        # oids of modified objects (to be invalidated on an abort).
        self._modified = []
        # oids of created objects (to be removed from cache on abort).
        # XXX differs from ZODB, where it's a dict oid->flag
        self._creating = []


    # Capsule API

    def getSchemaManager(self):
        return self._db._schema_manager

    def getTypeManager(self):
        return self._db._type_manager

    ##################################################
    # Add/Modify/Remove

    def _doDelta(self, op, path, info=None):
        """Add a delta to the JCR tree delta to replay at commit time.
        """
        if self._jcr_delta is None:
            self._jcr_delta = FullTreeDelta()
        self._jcr_delta.add(op, path, info)

    def _findPropertyNodeType(self, name, parent):
        """Find the node type of a given child in a parent.

        Returns a string, or None for pseudo-types (ListProperty).
        """
        node_type = parent._type_name
        if node_type is None:
            raise ValueError("Object must have a type")
        schema = self.getSchemaManager().getSchema(node_type)
        field = schema.get(name)
        if field is None:
            raise ValueError("Object %r has no property %r"%(node_type, name))
        # Find the child node type
        if IObjectPropertyField.providedBy(field):
            return field.schema.getName()
        elif IListPropertyField.providedBy(field):
            return None
        else:
            raise ValueError("Unknown node type for field %r" % field)

    def setSimpleProperty(self, name, obj):
        """Note that a simple property was changed.
        """
        if not IObjectBase.providedBy(obj):
            raise ValueError("Can only set a property on ObjectBase")
        if obj._p_jar is None:
            raise InvalidObjectReference("Object has no connection", obj)
        if obj._p_jar is not self:
            raise InvalidObjectReference("Object in another connection", obj)
        oid = obj._p_oid
        assert oid is not None, ("Object has no _p_oid", obj)

        try:
            self._pending_register = oid
            obj._p_changed = True
        finally:
            self._pending_register = None

        jcr_path = self._oid_to_path[oid]
        self._doDelta(MODIFY, jcr_path, {name: True})

    def setComplexProperty(self, name, parent):
        """Set a complex property.

        If the property already existed, it is overwritten (complex
        lists are emptied) and its oid is reused.

        Returns the property (newly created or not).
        """
        node_type = self._findPropertyNodeType(name, parent)

        obj = parent.getProperty(name, None)
        if obj is None:
            # No previous property, create one
            obj = self._addNode(name, parent, node_type)

    def _addNode(self, name, parent, node_type):
        """Add a new JCR node and assign it an oid.

        Does not actually talk to the JCR, but uses a temporary oid that
        will be turned into the final one at commit time.

        If a node already existed with the same name, it is destroyed.

        Returns the newly created object.
        """
        if parent is None:
            raise ValueError("No parent provided")
        if parent._p_jar is None:
            raise InvalidObjectReference("Parent has no connection")
        if parent._p_jar is not self:
            raise InvalidObjectReference("Parent is in another connection",
                                         parent)
        poid = parent._p_oid
        assert poid is not None, ("Parent has no _p_oid", parent)

        # Make a temporary oid
        oid = 'T%d' % self._next_tmp_uuid
        self._next_tmp_uuid += 1

        # Build and register the object
        klass = self._db.getClass(node_type)
        obj = klass.__new__(klass)
        obj._p_oid = oid
        obj._p_jar = self
        jcr_path = self._oid_to_path[poid] + (name,)
        self._oid_to_path[oid] = jcr_path
        self._path_to_oid[jcr_path] = oid
        self._register(obj)
        self._added[oid] = obj

        # Add to current delta
        self._doDelta(ADD, jcr_path)

        return obj

    def register(self, obj):
        """Register obj as modified.

        Called by the persistence machinery when an object's state
        changes to CHANGED.

        Does not actually record useful information, but is used to flag
        objects modified directly without going to the Capsule API,
        which is an error.
        """
        oid = obj._p_oid
        assert obj._p_jar is self, ("Object has bad _p_jar", obj)
        assert oid is not None, ("Object has no _p_oid", obj)

        if oid in self._added:
            # Was already added by hand
            return

        self._register(obj)

        # Know when internal code manually marks _p_changed = True
        # when we don't want __unknown__ flagged.
        if self._pending_register == oid:
            return

        # Add to current delta
        if '/' not in oid:
            jcr_path = self._oid_to_path[oid]
            self._doDelta(MODIFY, jcr_path, {'__unknown__': True})
            import traceback; traceback.print_stack()

    def _register(self, obj):
        # XXX needs also to be called by add() or equivalents
        if self._needs_to_join:
            self.transaction_manager.get().join(self)
            self._needs_to_join = False
        self._registered_objects.append(obj)


    def remove(self, obj):
        raise NotImplementedError

    def savepoint(self):
        # Shouldn't be there at all to not support savepoints,
        # but our base class already has this method.
        # XXX don't inherit from ZODBConnection at all!
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
        self.save()
        self._jcr_delta = None

        return


        # XXX _added_during_commit?
        for obj in self._registered_objects:
            oid = obj._p_oid
            assert oid is not None, "Object %r has no oid" % (obj,)
            assert obj._p_jar is self

            if oid in self._conflicts:
                # XXX
                raise ReadConflictError(object=obj)

            if oid in self._added:
                pass
            elif obj._p_changed:
                # XXX check _invalidated?
                self._modified.append(oid)
            else:
                # object was modified then un-changed by resetting _p_changed
                continue

            klass = obj.__class__

            if klass is Document: # XXX use interfaces
                state = self._saveObjectState(obj, full_document=True)
            elif klass is Children:
                state = self._saveChildrenState(obj)
            elif klass is ListProperty:
                state = self._saveListPropertyState(obj)
            elif klass is ObjectProperty:
                state = self._saveObjectState(obj)
            else:
                raise ValueError("Unknown class %s" % klass.__name__)

            obj._p_changed = False

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

        Called before tpc_abort in two-phase commit if this resource
        manager has not voted.
        """
        for obj in self._registered_objects:
            oid = obj._p_oid
            if oid in self._added:
                # Added, so not in cache yet; remove it
                print 'XXX ABORT unadd', oid
                del self._added[oid]
                del obj._p_jar
                del obj._p_oid
            else:
                # Normally modified, invalidate it
                #print 'XXX ABORT inval', oid
                self._cache.invalidate(oid)

        self._tpc_cleanup()

    def tpc_abort(self, txn):
        """Abort a transaction.

        Called when a two-phase commit aborts.
        """
        # Invalidate modified objects seen by a commit
        for oid in self._modified:
            print 'XXX TPCABORT inval', oid
        self._cache.invalidate(self._modified)

        # Remove created objects from the cache
        for oid in self._creating:
            obj = self._cache.get(oid)
            if obj is not None:
                print 'XXX TPCABORT decache', oid
                del self._cache[oid]
                jcr_path = self._oid_to_path[oid]
                del self._oid_to_path[oid]
                del self._path_to_oid[jcr_path]
                del obj._p_jar
                del obj._p_oid

        # Cleanup other added objects that haven't made it to _creating
        while self._added:
            oid, obj = self._added.popitem()
            print 'XXX TPCABORT unadd', oid
            del obj._p_oid
            del obj._p_jar

        self._tpc_cleanup()

    def _tpc_cleanup(self):
        """Cleanup after finish or abort.
        """
        self._modified = []
        self._creating = []

        self._conflicts.clear()
        #if not self._synch:
        #    self._flush_invalidations() # XXX invalidations
        self._needs_to_join = True
        self._registered_objects = []
        self._jcr_delta = None

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

    def get(self, oid):
        """Get the persistent object with a given oid.

        Returns the object from the cache if it's there. Otherwise
        returns a ghost, in which case a costly round-trip to the
        storage has to be made to get class and path information. This
        only happens for objects not accessed recently and evicted from
        the cache.
        """
        obj = self._getFromCache(oid)
        if obj is None:
            klass, jcr_path = self._getClassAndPath(oid)
            obj = self._makeGhost(oid, klass, jcr_path)
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

    def _getTyped(self, oid, node_type, jcr_path):
        """Get an object from an oid from the caches, or build a ghost.

        The ghost is built according to node_type and with a jcr_path.
        """
        obj = self._getFromCache(oid)
        if obj is None:
            # Get a ghost
            klass = self._getClass(oid, node_type)
            obj = self._makeGhost(oid, klass, jcr_path)
        return obj

    def _makeGhost(self, oid, klass, jcr_path):
        """Create a ghost object for a given oid, class and JCR path.

        The ghost is then put in the cache.
        """
        obj = klass.__new__(klass)
        obj._p_oid = oid
        obj._p_jar = self
        obj._p_deactivate() # Switch to ghost
        self._cache[oid] = obj
        self._oid_to_path[oid] = jcr_path
        self._path_to_oid[jcr_path] = oid
        return obj

    def _getClass(self, oid, node_type):
        """Find the class for this oid given its node type.
        """
        if '/' in oid:
            return ListProperty
        return self._db.getClass(node_type)

    def _getClassAndPath(self, oid):
        """Find the class and path for a totally unknown oid.

        Asks the storage for needed information.
        """
        if '/' in oid:
            uuid, name = oid.split('/', 1)
        else:
            uuid = oid
        node_type, jcr_path = self.controller.getNodeTypeAndPath(uuid)
        if '/' in oid:
            klass = ListProperty
        else:
            klass = self._db.getClass(node_type)
        return klass, jcr_path


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
        elif klass is Document: # XXX use interfaces
            state = self._loadObjectState(oid, full_document=True)
        elif klass is Children:
            state = self._loadChildrenState(oid)
        elif klass is ListProperty:
            state = self._loadListPropertyState(oid)
        elif klass is ObjectProperty:
            state = self._loadObjectState(oid)
        else:
            raise ValueError("Unknown class %s" % klass.__name__)

        # Put state on the object
        obj.__setstate__(state)


    def _isMultiple(self, node_type, name):
        """Check if a child name in a type has same-name siblings.
        """
        return self._db.isMultiple(node_type, name)


    def _loadObjectState(self, uuid, full_document=False):
        """Load the state of a Node from the JCR.

        This Node represents either an object (complex property) or a
        full document with children.

        Property values are also loaded if they're cheap (no Binary).
        (The decision is made by the server.)
        """
        states = self.controller.getNodeStates([uuid])
        name, parent_uuid, jcrchildren, properties, deferred = states[uuid]
        assert deferred == [], deferred # XXX for now

        # This object (we're setting its state so it should be in cache)
        if self._getFromCache(uuid) is None: # XXX
            # XXX check that
            print 'XXX object loaded but not in cache', uuid
        this = self.get(uuid)
        jcr_path = self._oid_to_path[uuid]

        # Name, parent
        if parent_uuid is not None:
            parent = self.get(parent_uuid)
        else:
            parent = None
        state = {
            '__name__': name,
            '__parent__': parent,
            }

        # JCR properties
        prop_map = {}
        type_name = 'cps:UnknownType' # XXX
        for prop_name, prop_value in properties:
            if prop_name == 'jcr:primaryType':
                type_name = prop_value
            prop_map[prop_name] = prop_value
        state['_props'] = prop_map
        state['_type_name'] = type_name

        # JCR children
        toskip = set()
        for child_name, child_uuid, child_type in jcrchildren:
            child_jcr_path = jcr_path + (child_name,)
            # Check if we have a ListProperty to create
            if (child_name not in prop_map and
                self._isMultiple(type_name, child_name)):
                oid = uuid+'/'+child_name
                lprop = self._getTyped(oid, '', child_jcr_path) # type unused
                if lprop._p_changed is None:
                    # Ghost, set state by indirect call to setstate()
                    lprop_state = {
                        '__name__': child_name,
                        '__parent__': this,
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
            child = self._getTyped(child_uuid, child_type, child_jcr_path)
            if child_name == 'cps:children' and full_document:
                state['_children'] = child
            elif child_name in prop_map:
                # Multiple type, add to ListProperty
                prop_map[child_name]._values.append(child)
            else:
                # Simple type
                prop_map[child_name] = child

        return state

    def _loadChildrenState(self, uuid):
        """Load the state for a JCR Node representing children.
        """
        states = self.controller.getNodeStates([uuid])
        name, parent_uuid, jcrchildren, properties, deferred = states[uuid]
        assert deferred == [], deferred # XXX for now

        # This object
        jcr_path = self._oid_to_path[uuid]

        # Name, parent
        if parent_uuid is not None:
            parent = self.get(parent_uuid)
        else:
            parent = None
        state = {
            '__name__': name,
            '__parent__': parent,
            }

        # JCR Children
        child_map = {}
        order = [] # XXX check if type is ordered in its schema
        for child_name, child_uuid, child_type in jcrchildren:
            child_jcr_path = jcr_path + (child_name,)
            child = self._getTyped(child_uuid, child_type, child_jcr_path)
            child_map[child_name] = child
            order.append(child_name)
        state['_children'] = child_map
        state['_order'] = order
        # XXX _lazy, _missing

        return state

    def _loadListPropertyState(self, oid):
        """Load the state of a ListProperty from the JCR.
        """
        uuid, prop_name = oid.split('/', 1)
        # XXX need a more specialized API here to get only prop_name children
        states = self.controller.getNodeStates([uuid])
        name, parent_uuid, jcrchildren, properties, deferred = states[uuid]
        assert deferred == [], deferred # XXX for now

        # Get parent and path
        parent = self.get(uuid)
        # All children have the same jcr path, which is also ours
        child_jcr_path = self._oid_to_path[oid]

        values = []
        state = {
            '__name__': name,
            '__parent__': parent,
            '_values': values,
            }

        # JCR Children
        for child_name, child_uuid, child_type in jcrchildren:
            if child_name != prop_name:
                # Only keep the wanted children # XXX optimize this
                continue
            child = self._getTyped(child_uuid, child_type, child_jcr_path)
            values.append(child)

        return state

    ##################################################
    # Save

    def save(self):
        self._save()
        self._jcr_delta = None

    def _save(self):
        """Send the current tree delta to the JCR, and do a JCR save.

        This operation is needed before a commit, or before any JCR
        operation that works on the persistently saved data, like
        checkin, checkout, copy, move.
        """
        commands = self._saveCommands()
        map = self.controller.sendCommands(commands)
        # remplace temporary oids with final ones



    def _saveCommands(self):
        """Generator returning the commands to save the tree delta.

        Commands are a tuple, which can be:
        - 'add', token, path, node_type, props_mapping
        - 'modify', uuid, props_mapping
        - 'remove', uuid
        """
        for op, path, info in self._jcr_delta:
            oid = self._path_to_oid[path]
            if op == ADD:
                # oid is a temporary one, we use it as a token
                obj = self._getFromCache(oid)
                assert obj is not None, ("ADD missing from cache", oid)
                node_type = obj._type_name
                props = self._collectProperties(info, obj, skip_none=True)
                command = ('add', oid, path, node_type, props)
            elif op == MODIFY:
                obj = self._getFromCache(oid)
                assert obj is not None, ("ADD missing from cache", oid)
                props = self._collectProperties(info, obj)
                command = ('modify', oid, props)
            elif op == REMOVE:
                command = ('remove', oid)
            yield command

    def _collectProperties(self, info, obj, skip_none=False):
        """Collect properties to send in a command.
        """
        props = {}
        if '__unknown__' in info:
            raise ValueError("info for %r with unknown %r" % (obj, info))
        for key in info.iterkeys():
            value = obj.getProperty(key, None)
            if value is None and skip_none:
                continue
            assert not isinstance(value, Persistent), ("Persistent "
                "value %r in property %r" % (value, key))
            props[key] = value
        return props


    def _saveObjectState(self, obj, full_document=False):
        """Save the state of an object to a JCR Node.
        """
        #if full_document is True:
        #    raise NotImplementedError

        uuid = obj._p_oid

        props = []
        for name in obj._props_changed or ():
            # Find all non-Persistent changed props
            value = obj._props[name]
            props.append((name, value))
        if props:
            self.controller.setNodeState(uuid, props)

        # XXX treat added/removed children and complex props





        # Clear changed attributes
        try: del obj._props_added
        except AttributeError: pass
        try: del obj._props_changed
        except AttributeError: pass

    def _saveChildrenState(self, obj):
        raise NotImplementedError

    def _saveListPropertyState(self, obj):
        raise NotImplementedError
