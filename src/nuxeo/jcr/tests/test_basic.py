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
"""Basic tests.
"""

import unittest
from zope.testing.doctest import DocFileTest
from zope.interface.verify import verifyClass

class InterfaceTests(unittest.TestCase):

    def test_NoChildrenYet(self):
        from nuxeo.capsule.interfaces import IChildren
        from nuxeo.jcr.impl import NoChildrenYet
        verifyClass(IChildren, NoChildrenYet)

    def test_JCRController(self):
        from nuxeo.jcr.interfaces import IJCRController
        from nuxeo.jcr.controller import JCRController
        verifyClass(IJCRController, JCRController)

    def test_JCRIceController(self):
        from nuxeo.jcr.interfaces import IJCRController
        from nuxeo.jcr.controller import JCRIceController
        verifyClass(IJCRController, JCRIceController)

    def test_FakeJCRController(self):
        from nuxeo.jcr.interfaces import IJCRController
        from nuxeo.jcr.tests.fakeserver import FakeJCRController
        verifyClass(IJCRController, FakeJCRController)


def test_suite():
    import nuxeo.jcr.tests
    import os.path
    testdir = os.path.dirname(nuxeo.jcr.tests.__file__)
    return unittest.TestSuite((
        unittest.makeSuite(InterfaceTests),
        DocFileTest('test_basic.txt', globs=dict(testdir=testdir)),
        ))

if __name__ == '__main__':
    unittest.TextTestRunner().run(test_suite())
