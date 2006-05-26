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
from zope.schema.interfaces import IList
from nuxeo.capsule.schema import SchemaManager
from nuxeo.capsule.type import TypeManager
from nuxeo.capsule.type import Type
from nuxeo.jcr.impl import Document
from nuxeo.capsule import Children
from nuxeo.jcr.impl import ObjectProperty
from nuxeo.jcr.connection import Connection
from nuxeo.jcr.protocol import JCRController
from nuxeo.jcr import cnd

class DB(ZODBDB):
    """Capsule JCR DB
    """

    klass = Connection
    controller_class = JCRController

    # this lock protects schema creation
    _schemas_load_lock = None # threading.Lock
    _interfaces = None # mapping node type to interface
    _classes = None # mapping node type to class
    _basic_classes = {
        'rep:root': Document,
        'nt:unstructured': Document,
        }
    _schema_manager = None # SchemaManager
    _type_manager = None # TypeManager

    def __init__(self,
                 database_name='unnamed-jcr',
                 databases=None,
                 cache_size=20000,
                 pool_size=7,
                 server=None,
                 workspace_name='default',
                 controller_class_name='',
                 slice_file='',
                 ice_config='',
                 ):
        """Create a database which connects to a JCR.
        """
        self._schemas_load_lock = threading.Lock()

        self.server = server # ZConfig.datatypes.SocketConnectionAddress
        self.workspace_name = workspace_name
        self.slice_file = slice_file # Path to Ice slice file
        self.ice_config = ice_config # Path to Ice config
        index = controller_class_name.rindex('.')
        mname = controller_class_name[:index]
        cname = controller_class_name[index+1:]
        module = __import__(mname, globals(), locals(), cname)
        self.controller_class = getattr(module, cname)
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
            if self._classes is None:
                # First connection to load them
                self._loadSchemas(controller)
        finally:
            self._schemas_load_lock.release()

    def _loadSchemas(self, controller):
        # Called with the lock held
        schema_manager = SchemaManager()
        self._schema_manager = schema_manager
        type_manager = TypeManager()
        self._type_manager = type_manager

        # Get node type definitions as CND, and parse that
        defs = controller.getNodeTypeDefs()
        parser = cnd.Parser(defs)
        namespaces, schemas = parser.getData()
        interfaces = parser.buildSchemas(schemas)

        # Build classes and register schemas and types
        try:
            ICPSType = interfaces['cpsnt:type']
            ICPSSchema = interfaces['cpsnt:schema']
            ICPSDocument = interfaces['cpsnt:document']
        except KeyError:
            # The JCR isn't prepped for CPS
            from zope.interface import Interface
            ICPSSchema = Interface
            ICPSDocument = Interface
        classes = self._basic_classes.copy()
        for node_type, interface in interfaces.iteritems():
            # TODO: use namespace URIs
            if (node_type.startswith('mix:') or
                node_type.startswith('rep:') or
                node_type.startswith('nt:')):
                continue
            if (node_type.startswith('cpsnt:') and
                node_type != 'cpsnt:children'):
                continue
            if (interface.isOrExtends(ICPSSchema) or
                interface.isOrExtends(ICPSType) or
                node_type == 'cpsnt:children'):
                schema_manager.addSchema(node_type, interface)
                if interface.isOrExtends(ICPSDocument):
                    type = Type(node_type, interface,
                                container=True, ordered=False)
                    type_manager.addType(type)
                    classes[node_type] = Document # XXX
                elif interface.isOrExtends(ICPSType):
                    classes[node_type] = ObjectProperty
                elif node_type == 'cpsnt:children':
                    classes[node_type] = Children

        # Set last to ensure locking
        self._classes = classes


    def getClass(self, node_type):
        """Get the class for a given JCR node type.
        """
        klass = self._classes.get(node_type)
        if klass is not None:
            return klass
        raise ValueError("Unknown node type: %r" % node_type)

    def isMultiple(self, node_type, name):
        """Check if a node type has multiple siblings `name` and must
        therefore be represented as a list.
        """
        schema = self._schema_manager.getSchema(node_type, None)
        if schema is None:
            multiple = False
        else:
            multiple = IList.providedBy(schema[name])
        return multiple

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
