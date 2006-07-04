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
from nuxeo.capsule.base import IChildren
from nuxeo.capsule.base import ObjectBase as CapsuleObjectBase
from nuxeo.capsule.base import ContainerBase as CapsuleContainerBase
from nuxeo.capsule.base import Children as CapsuleChildren
from nuxeo.capsule.base import Document as CapsuleDocument
from nuxeo.capsule.base import Workspace as CapsuleWorkspace
from nuxeo.capsule.base import ListProperty as CapsuleListProperty
from nuxeo.capsule.base import ObjectProperty as CapsuleObjectProperty


_MARKER = object()


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


class ContainerBase(CapsuleContainerBase):
    """JCR-specific children holder.
    """
    def addChild(self, name, type_name):
        """See `nuxeo.capsule.interfaces.IContainerBase`
        """
        if name in self._children:
            raise KeyError("Child %r already exists" % name)
        child = self._p_jar.createChild(self, name, type_name)
        self._children[name] = child
        if self._order is not None:
            self._order.append(name)
        return child

    def removeChild(self, name):
        """See `nuxeo.capsule.interfaces.IContainerBase`
        """
        self._p_jar.deleteChild(self, name)
        del self._children[name]
        if self._order is not None:
            self._order.remove(name)

    def reorder(self, names):
        """See `nuxeo.capsule.interfaces.IContainerBase`
        """
        if self._order == names:
            return
        raise NotImplementedError # XXX call _p_jar


class NoChildrenYet(object):
    """No children exist yet.
    """
    zope.interface.implements(IChildren)

    __name__ = 'ecm:children'

    def __init__(self, parent):
        self.__parent__ = parent

    def getName(self):
        return self.__name__

    def getTypeName(self):
        raise TypeError("Can't get type name of NoChildrenYet")

    def _getPath(self, first=False):
        ppath = self.__parent__._getPath()
        if first:
            return ppath + (self.__name__,)
        else:
            return ppath

    def __repr__(self):
        path = '/'.join(self._getPath(True))
        return '<%s at %s>' % (self.__class__.__name__, path)

    def getChild(self, name, default=_MARKER):
        """See `nuxeo.capsule.interfaces.IContainerBase`
        """
        if default is not _MARKER:
            return default
        raise KeyError(name)

    def __getitem__(self, name):
        """See `nuxeo.capsule.interfaces.IContainerBase`
        """
        raise KeyError(name)

    def getChildren(self):
        """See `nuxeo.capsule.interfaces.IContainerBase`
        """
        return []

    def __iter__(self):
        """See `nuxeo.capsule.interfaces.IContainerBase`
        """
        return iter(())

    def hasChild(self, name):
        """See `nuxeo.capsule.interfaces.IContainerBase`
        """
        return False

    def __contains__(self, name):
        return False

    def __len__(self):
        return 0

    def hasChildren(self):
        """See `nuxeo.capsule.interfaces.IContainerBase`
        """
        return False

    def addChild(self, name, type_name):
        """See `nuxeo.capsule.interfaces.IContainerBase`
        """
        raise TypeError("Can't add a child to NoChildrenYet")

    def removeChild(self, name):
        """See `nuxeo.capsule.interfaces.IContainerBase`
        """
        raise KeyError(name)

    def __delitem__(self, name):
        """See `nuxeo.capsule.interfaces.IContainerBase`
        """
        raise KeyError(name)

    def clear(self):
        """See `nuxeo.capsule.interfaces.IContainerBase`
        """
        pass

    def reorder(self, names):
        """See `nuxeo.capsule.interfaces.IContainerBase`
        """
        if names:
            raise ValueError("Names mismatch")


class Children(ContainerBase, CapsuleChildren):
    """JCR-specific Children class.
    """
    def getTypeName(self):
        return 'ecmnt:children'


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
        children = self._children

        # Are we creating the first child?
        if isinstance(children, NoChildrenYet):
            children = self._p_jar.createChild(self, 'ecm:children',
                                               'ecmnt:children')
            self.__dict__['_children'] = children # (avoid changing self)

        child = children.addChild(name, type_name)
        return child.__of__(self)


class Workspace(Document, CapsuleWorkspace):
    """JCR Workspace
    """

class ListProperty(ContainerBase, ObjectBase, CapsuleListProperty):
    """JCR-specific list property.
    """

    def addValue(self):
        """Create one item for the list.
        """
        return self._p_jar.addValue(self)

class ObjectProperty(ObjectBase, CapsuleObjectProperty):
    """JCR-specific object property.

    Deals with property addition.
    """
