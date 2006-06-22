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
"""Capsule JCR implementation.

Subclasses the default implementation to record JCR-specific
information.
"""

from persistent import Persistent
import zope.interface
from nuxeo.capsule.base import ObjectBase as CapsuleObjectBase
from nuxeo.capsule.base import Document as CapsuleDocument
from nuxeo.capsule.base import Workspace as CapsuleWorkspace
from nuxeo.capsule.base import ListProperty as CapsuleListProperty
from nuxeo.capsule.base import ObjectProperty as CapsuleObjectProperty


class ObjectBase(CapsuleObjectBase):
    """JCR-specific object.

    Deals with property addition.
    """

    def setProperty(self, name, value):
        """See `nuxeo.capsule.interfaces.IObjectBase`
        """
        try:
            self._p_jar.setProperty(self, name, value)
        except KeyError:
            raise
            raise KeyError("Schema has no property %r" % name)

class Document(ObjectBase, CapsuleDocument):
    """JCR-specific document.

    Deals with property addition.
    """

    def getUUID(self):
        """See `nuxeo.capsule.interfaces.IDocument`
        """
        return self._p_oid

    def addChild(self, name, type_name):
        """See `nuxeo.capsule.interfaces.IDocument`
        """
        child = self._p_jar.createChild(self, name, type_name)
        return child.__of__(self)

    def removeChild(self, name):
        """See `nuxeo.capsule.interfaces.IDocument`
        """
        raise NotImplementedError
        child = self._children.removeChild(name)
        child.__parent__ = None # Help the GC
        # Deregister UUID
        self.getWorkspace()._removeUUID(child.getUUID())

class Workspace(Document, CapsuleWorkspace):
    """JCR Workspace
    """

class ListProperty(CapsuleListProperty):
    """JCR-specific list property.
    """
    def _createItem(self):
        """Create one item for the list.
        """
        return self._p_jar.createItem(self)

class ObjectProperty(ObjectBase, CapsuleObjectProperty):
    """JCR-specific object property.

    Deals with property addition.
    """


