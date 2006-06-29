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

from nuxeo.capsule.interfaces import IObjectBase
from nuxeo.capsule.interfaces import IChildren
from nuxeo.capsule.schema import SchemaManager as BaseSchemaManager
from nuxeo.jcr.cnd import InterfaceMaker


class SchemaManager(BaseSchemaManager):
    """A Schema Manager knows about registered schemas.

    It builds the schemas and interfaces from a CND definition.
    """

    def __init__(self):
        super(SchemaManager, self).__init__()
        self._interfaces = InterfaceMaker()

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
