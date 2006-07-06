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
"""Connection tests.
"""
import unittest
from nuxeo.jcr.connection import findInserts

class FindInsertTests(unittest.TestCase):

    def test_findInserts_0(self):
        old = list('abcdef')
        new = list('abcdef')
        self.assertEquals(findInserts(old, new), [])

    def test_findInserts_1(self):
        old = list('abcd')
        new = list('cdab')
        self.assertEquals(findInserts(old, new),
                          [('c', 'a'), ('d', 'a')])

    def test_findInserts_2(self):
        old = list('abcd')
        new = list('dcba')
        self.assertEquals(findInserts(old, new),
                          [('d', 'a'), ('c', 'a'), ('b', 'a')])

    def test_findInserts_3(self):
        old = list('abcd')
        new = list('adcb')
        self.assertEquals(findInserts(old, new),
                          [('d', 'b'), ('c', 'b')])

def test_suite():
    return unittest.TestSuite((
        unittest.makeSuite(FindInsertTests),
        ))

if __name__ == '__main__':
    unittest.TextTestRunner().run(test_suite())
