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
"""JCR schema management.
"""

import zope.testing.cleanup
from nuxeo.capsule.schema import SchemaManager as BaseSchemaManager
from nuxeo.jcr.cnd import InterfaceMaker
from nuxeo.capsule.interfaces import IDocument
from nuxeo.capsule.interfaces import IProxy
from nuxeo.capsule.interfaces import IWorkspace
from nuxeo.capsule.interfaces import IChildren
from nuxeo.capsule.interfaces import IResourceProperty
from nuxeo.capsule.interfaces import IObjectBase


_schema_manager = None

def _cleanup():
    global _schema_manager
    _schema_manager = None
zope.testing.cleanup.addCleanUp(_cleanup)


def getGlobalSchemaManager():
    """Get the global schema manager.
    """
    global _schema_manager
    if _schema_manager is None:
        _schema_manager = SchemaManager()
    return _schema_manager


class SchemaManager(BaseSchemaManager):
    """A Schema Manager knows about registered schemas.

    It builds the schemas and interfaces from a CND definition.
    """

    def __init__(self, predefined=None):
        super(SchemaManager, self).__init__()
        default = {
            'rep:root': IWorkspace,
            'ecmnt:document': IDocument,
            'ecmnt:proxy': IProxy,
            'ecmnt:schema': IObjectBase,
            'ecmnt:children': IChildren,
            'nt:resource': IResourceProperty,
            }
        if predefined is not None:
            default.update(predefined)
        self._interfaces = InterfaceMaker(predefined=default)

    def addCND(self, cnd):
        """Build zope 3 schemas from CND definitions.

        ``cnd`` is a string or a stream.
        """
        interfaces = self._interfaces
        type_names = interfaces.addData(cnd)
        for node_type in type_names:
            iface = interfaces[node_type]
            if (iface.isOrExtends(IObjectBase) or
                iface is IChildren):
                self.addSchema(node_type, iface)

    def getInterface(self, name):
        """Return the interface registered for a name.
        """
        return self._interfaces[name]
