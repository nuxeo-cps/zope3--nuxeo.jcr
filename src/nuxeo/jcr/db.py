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
"""Capsule DB
"""

import threading
from ZODB.DB import DB as ZODBDB
from zope.app.container.interfaces import IContainer
from nuxeo.capsule.interfaces import IDocument
from nuxeo.capsule.interfaces import IWorkspace
from nuxeo.capsule.interfaces import IObjectBase
from nuxeo.capsule.interfaces import IChildren # XXX IRoot
from nuxeo.capsule.interfaces import IVersionHistory
from nuxeo.capsule.interfaces import IVersion
from nuxeo.capsule.interfaces import IFrozenDocument
import nuxeo.jcr.schema
from nuxeo.jcr.impl import Children
from nuxeo.jcr.impl import Document
from nuxeo.jcr.impl import Workspace
from nuxeo.jcr.impl import ObjectProperty
from nuxeo.jcr.impl import ListProperty
from nuxeo.jcr.connection import Connection
from nuxeo.jcr.controller import JCRController


class DB(ZODBDB):
    """Capsule JCR DB
    """

    klass = Connection
    controller_class = JCRController

    # this lock protects schema creation
    _schemas_load_lock = None # threading.Lock
    _schema_manager = None # SchemaManager

    def __init__(self,
                 database_name='unnamed-jcr',
                 databases=None,
                 cache_size=20000,
                 pool_size=7,
                 server=None,
                 workspace_name='default',
                 ):
        """Create a database which connects to a JCR.
        """
        self._schemas_load_lock = threading.Lock()

        self.server = server # ZConfig.datatypes.SocketConnectionAddress
        self.workspace_name = workspace_name
        super(DB, self).__init__(NoStorage(),
                                 pool_size=pool_size,
                                 cache_size=cache_size,
                                 database_name=database_name,
                                 databases=databases)

    def loadSchemas(self, controller):
        """Load the schemas from the database, and synthesizes
        the needed interfaces and classes.
        """
        self._schemas_load_lock.acquire()
        try:
            if self._schema_manager is None:
                # First connection to load them
                self._schema_manager = self._loadSchemas(controller)
        finally:
            self._schemas_load_lock.release()

    def _loadSchemas(self, controller):
        # Called with the lock held
        sm = nuxeo.jcr.schema.getGlobalSchemaManager()

        try:
            # Add node type definitions from the JCR
            defs = controller.getNodeTypeDefs()
            sm.addCND(defs)

            # XXX use a schema of ours, and distinguish dict from list
            sm.addSchema('IContainer', IContainer)
            sm.addSchema('ecmnt:children', IChildren)
            sm.addSchema('rep:root', IChildren) # XXX IRoot
            sm.addSchema('nt:versionHistory', IVersionHistory)
            sm.addSchema('nt:version', IVersion)
            sm.addSchema('nt:frozenNode', IFrozenDocument)

            # Set base classes
            sm.setClass('rep:root', Children) # XXX Workspace? Root?

            # XXX must be done only in unit tests
            #sm.setClass('ecmnt:workspace', Workspace)
            #sm.setClass('ecmnt:document', Document)
            #sm.setClass('ecmnt:schema', ObjectProperty)
            #sm.setClass('ecmnt:children', Children)
            #sm.setClass('IContainer', ListProperty)

            return sm
        except:
            # Schema loading failed, clean them up
            nuxeo.jcr.schema._cleanup()
            raise

    def getSchema(self, node_type):
        """Get the schema for a given JCR node type.
        """
        schema = self._schema_manager.getSchema(node_type, None)
        if schema is None:
            raise ValueError("Unknown node type: %r" % node_type)
        return schema

    def getClass(self, node_type):
        """Get the class for a given JCR node type.
        """
        if node_type == 'rep:root':
            return Children # XXX
        klass = self._schema_manager.getClass(node_type, None)
        if klass is None:
            raise ValueError("Unknown node type: %r" % node_type)
        return klass


class NoStorage(object):
    """Dummy storage.
    """
    # Compat methods so that ZODB.DB and ZODB.Connection are happy
    def registerDB(self, db, limit):
        pass
    def tpc_vote(self, txn):
        pass
    def load(self, oid, version):
        from ZODB.utils import z64
        if oid == z64:
            return None
        raise NotImplementedError
    def history(self, *args):
        raise NotImplementedError
    def supportsUndo(self):
        return False
    def supportsVersions(self):
        return False
    def undoLog(self, first, last, filter=None):
        return ()
    def versionEmpty(self, version):
        return True
    def versions(self, max=None):
        return ()
    def new_oid(self):
        raise NotImplementedError
    def isReadOnly(self):
        return False
