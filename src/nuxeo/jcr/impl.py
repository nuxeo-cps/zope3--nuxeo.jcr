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

import logging
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


logger = logging.getLogger('nuxeo.jcr.impl')

_MARKER = object()


class ObjectBase(CapsuleObjectBase):
    """JCR-specific object.

    Deals with property addition.
    """

    def setProperty(self, name, value):
        """See `nuxeo.capsule.interfaces.IObjectBase`
        """
        self._p_jar.setProperty(self, name, value)

        # Special properties
        func = self.__setattr_special_properties__.get(name)
        if func is not None:
            func(self, value, self.__dict__)

    #
    # Special attribute (security mapping)
    #

    __ac_local_roles__ = None
    __ac_local_group_roles__ = None

    def _map_localroles_to_prop(self):
        d = {}
        for k, v in (self.__ac_local_roles__ or {}).iteritems():
            d['user:'+k] = sorted(v)
        for k, v in (self.__ac_local_group_roles__ or {}).iteritems():
            d['group:'+k] = sorted(v)
        l = []
        for k, v in sorted(d.iteritems()):
            l.append(k + '=' + ','.join(v))
        s = unicode(';'.join(l)) or None
        return 'ecm:localroles', s

    def _map_security_to_prop(self):
        # Collect all security
        l = []
        for k, v in self.__dict__.iteritems():
            if k.startswith('_') and k.endswith('_Permission'):
                if isinstance(v, list):
                    l.append(k[1:-11] + '+=' + ','.join(sorted(v)))
                else:
                    l.append(k[1:-11] + '=' + ','.join(sorted(v)))
        l.sort()
        s = unicode(';'.join(l)) or None
        return 'ecm:security', s

    def _map_prop_to_localroles(self, value, state):
        u = {}
        g = {}
        if value:
            try:
                for i in str(value).split(';'):
                    k, v = i.split('=')
                    if k.startswith('user:'):
                        u[k[5:]] = v.split(',')
                    elif k.startswith('group:'):
                        g[k[6:]] = v.split(',')
                    else:
                        raise ValueError(k)
            except ValueError:
                #logger.debug("Illegal string %r for ecm:localroles", value)
                raise ValueError("Illegal string %r for ecm:localroles"
                                 % value)
        if u:
            state['__ac_local_roles__'] = u
        else:
            state.pop('__ac_local_roles__', None)
        if g:
            state['__ac_local_group_roles__'] = g
        else:
            state.pop('__ac_local_group_roles__', None)

    def _map_prop_to_security(self, value, state):
        # Purge old state
        for k, v in state.items():
            if k.startswith('_') and k.endswith('_Permission'):
                del state[k]
        # Set new state
        if not value:
            return
        try:
            for i in str(value).split(';'):
                k, v = i.split('=')
                if k[-1] == '+':
                    k = k[:-1]
                    r = v.split(',')
                else:
                    r = tuple(v.split(','))
                state['_'+k+'_Permission'] = r
        except ValueError:
            raise ValueError("Illegal string %r for ecm:security" % value)

    __setattr_special_attributes__ = {
        '__ac_local_roles__': _map_localroles_to_prop,
        '__ac_local_group_roles__': _map_localroles_to_prop,
        }

    __setattr_special_properties__ = {
        'ecm:localroles': _map_prop_to_localroles,
        'ecm:security': _map_prop_to_security,
        }

    def __setattr__(self, name, value):
        """Transform special value into properties.
        """
        func = self.__setattr_special_attributes__.get(name)
        if (func is None
            and name.startswith('_')
            and name.endswith('_Permission')):
            func = self._map_security_to_prop.im_func
        if func is not None:
            self.__dict__[name] = value
            k, v = func(self)
            self._p_jar.setProperty(self, k, v)
        else:
            if name == '_v__object_deleted__':
                return
            if (not name.startswith('_p_')
                and self._p_jar is not None
                ):
                print "XXX illegal direct setattr(%s, %r, %r)" % (
                    self.__class__.__name__, name, value)
            super(ObjectBase, self).__setattr__(name, value)

    def __delattr__(self, name):
        func = self.__setattr_special_attributes__.get(name)
        if (func is None
            and name.startswith('_')
            and name.endswith('_Permission')):
            func = self._map_security_to_prop.im_func
        if func is not None:
            del self.__dict__[name]
            k, v = func(self)
            self._p_jar.setProperty(self, k, v)
        else:
            super(ObjectBase, self).__delattr__(name)


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
        child = self._children[name]
        self._p_jar.deleteNode(child)
        del self._children[name]
        if self._order is not None:
            self._order.remove(name)

    def clear(self):
        """See `nuxeo.capsule.interfaces.IContainerBase`
        """
        for child in self._children.itervalues():
            self._p_jar.deleteNode(child)
        self._children.clear()
        if self._order is not None:
            self._order[:] = []

    def reorder(self, names):
        """See `nuxeo.capsule.interfaces.IContainerBase`
        """
        if self._order is None:
            raise TypeError("Unordered container")
        self._p_jar.reorderChildren(self, self._order, names)
        self._order[:] = names


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
        if self.__parent__ is None:
            return (self.__name__,)
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

    def keys(self):
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

    _children = NoChildrenYet(None)

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

    def checkout(self):
        """See `nuxeo.capsule.interfaces.IDocument`
        """
        self._p_jar.checkout(self)

    def checkin(self):
        """See `nuxeo.capsule.interfaces.IDocument`
        """
        self._p_jar.checkin(self)

    def isCheckedOut(self):
        """See `nuxeo.capsule.interfaces.IDocument`
        """
        return bool(self.getProperty('jcr:isCheckedOut', True))


class Workspace(Document, CapsuleWorkspace):
    """JCR Workspace
    """


class ListProperty(ContainerBase, ObjectBase, CapsuleListProperty):
    """JCR-specific list property.
    """

    def addValue(self):
        """Create one item for the list.
        """
        item = self._p_jar.newValue(self)
        name = item.getName()
        self._children[name] = item
        if self._order is not None:
            self._order.append(name)
        return item


class ObjectProperty(ObjectBase, CapsuleObjectProperty):
    """JCR-specific object property.

    Deals with property addition.
    """
