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
"""Capsule JCR dynamic classes.

Dynamic classes are generated for JCR objects when no class has been
specified in ZCML for a specific node type.

The generated class will appear as nuxeo.jcr.dynamic.Foo.

As classes are global to the python interpreter and node type are local
to a JCR repository, identical node types can conceivably refer to
different classes if several repositories are used.

"""

from nuxeo.capsule.item import Node as _Node

class _Base(_Node):
    """Dynamic class for a JCR object.
    """

def makeDynamicClass():
    pass
