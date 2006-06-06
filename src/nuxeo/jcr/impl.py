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
from nuxeo.capsule.base import ListProperty as CapsuleListProperty
from nuxeo.capsule.base import ObjectProperty as CapsuleObjectProperty
from nuxeo.jcr.interfaces import INonPersistent


class ObjectBase(CapsuleObjectBase):
    """JCR-specific object.

    Deals with property addition.
    """

    def setProperty(self, name, value):
        """See `nuxeo.capsule.interfaces.IObject`
        """
        if isinstance(value, Persistent):
            # If a new persistent property is added, it has to be created
            # as a node in the backend right now.
            # XXXX
            obj = self._p_jar.setComplexProperty(name, self)
            # XXX ... set values
        else:
            self._p_jar.setSimpleProperty(name, self)
            self._props[name] = value

    def addProperty(self, name):
        """See `nuxeo.capsule.interfaces.IObject`
        """
        if self._p_jar is None:
            raise ValueError("Cannot add inside a non-persisted property")
        return self._p_jar.setComplexProperty(name, self)


class Document(ObjectBase, CapsuleDocument):
    """JCR-specific document.

    Deals with property addition.
    """

class ListProperty(CapsuleListProperty):
    """JCR-specific list property.
    """
    zope.interface.implements(INonPersistent)

class ObjectProperty(ObjectBase, CapsuleObjectProperty):
    """JCR-specific object property.

    Deals with property addition.
    """
