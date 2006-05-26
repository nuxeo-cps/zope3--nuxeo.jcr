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


def test_suite():
    import nuxeo.jcr.tests
    import os.path
    testdir = os.path.dirname(nuxeo.jcr.tests.__file__)
    return unittest.TestSuite((
        DocFileTest('../cnd.py'),
        DocFileTest('test_cndlexer.txt'),
        DocFileTest('test_cndparser.txt', globs=dict(testdir=testdir)),
        ))


if __name__ == '__main__':
    unittest.TextTestRunner().run(test_suite())
