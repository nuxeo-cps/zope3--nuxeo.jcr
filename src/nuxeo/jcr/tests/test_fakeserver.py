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
"""Tests for fake server.
"""

import unittest
from zope.interface.verify import verifyClass

from copy import deepcopy
from nuxeo.jcr.tests.fakeserver import FakeJCR
from nuxeo.jcr.tests.fakeserver import Merger
from nuxeo.jcr.interfaces import ConflictError


class InterfaceTests(unittest.TestCase):

    def test_FakeJCRController(self):
        from nuxeo.jcr.interfaces import IJCRController
        from nuxeo.jcr.tests.fakeserver import FakeJCRController
        verifyClass(IJCRController, FakeJCRController)


class MergerTests(unittest.TestCase):

    def makeProps(self, ini_props, cur_change, new_change):
        self.ini = FakeJCR()
        uuid = self.ini.root_uuid
        self.ini.modifyProperties(uuid, ini_props)
        self.cur = deepcopy(self.ini)
        self.new = deepcopy(self.ini)
        self.cur.modifyProperties(uuid, cur_change)
        self.new.modifyProperties(uuid, new_change)
        self.props = self.new.data[uuid].properties
        return Merger(self.ini, self.cur, self.new).merge

    def test_props_no_changes(self):
        merge = self.makeProps({'foo': 1, 'bar': 2}, {}, {})
        merge()
        self.assertEquals(self.props['foo'], 1)
        self.assertEquals(self.props['bar'], 2)

    def test_props_disjoint_changes(self):
        merge = self.makeProps({'foo': 1, 'bar': 2}, {'foo': 2}, {'bar': 3})
        merge()
        self.assertEquals(self.props['foo'], 2)
        self.assertEquals(self.props['bar'], 3)

    def test_props_conflicting_changes(self):
        merge = self.makeProps({'foo': 1}, {'foo': 2}, {'foo': 3})
        self.assertRaises(ConflictError, merge)

    def test_props_remove_same(self):
        merge = self.makeProps({'foo': 1}, {'foo': None}, {'foo': None})
        merge()
        self.failIf('foo' in self.props)

    def test_props_change_remove_1(self):
        merge = self.makeProps({'foo': 1}, {'foo': None}, {'foo': 2})
        self.assertRaises(ConflictError, merge)

    def test_props_change_remove_2(self):
        merge = self.makeProps({'foo': 1}, {'foo': 2}, {'foo': None})
        self.assertRaises(ConflictError, merge)

    def test_props_added_1(self):
        merge = self.makeProps({}, {'foo': 1}, {})
        merge()
        self.assertEquals(self.props['foo'], 1)

    def test_props_added_2(self):
        merge = self.makeProps({}, {}, {'foo': 1})
        merge()
        self.assertEquals(self.props['foo'], 1)

    def test_props_added_twice(self):
        merge = self.makeProps({}, {'foo': 1}, {'foo': 2})
        self.assertRaises(ConflictError, merge)


    def makeChildren(self, ini_children, cur_children, new_children,
                     cur_remove=(), new_remove=()):
        self.ini = FakeJCR()
        uuid = self.ini.root_uuid
        for name, child_uuid in ini_children:
            self.ini.addChild(uuid, child_uuid, name, 'type', [], {})

        self.cur = deepcopy(self.ini)
        for name, child_uuid in cur_children:
            self.cur.addChild(uuid, child_uuid, name, 'type', [], {})
        for child_uuid in cur_remove:
            self.cur.removeNode(child_uuid)

        self.new = deepcopy(self.ini)
        for name, child_uuid in new_children:
            self.new.addChild(uuid, child_uuid, name, 'type', [], {})
        for child_uuid in new_remove:
            self.new.removeNode(child_uuid)

        self.children = self.new.data[uuid].children
        return Merger(self.ini, self.cur, self.new).merge

    def test_children_no_changes(self):
        merge = self.makeChildren([], [], [])
        merge()
        self.assertEquals(self.children, [])

    def test_children_disjoint_adds(self):
        merge = self.makeChildren([('x', '0')], [('a', '1')], [('b', '2')])
        merge()
        self.assertEquals(self.children, [('x', '0'), ('b', '2'), ('a', '1')])

    def test_children_add_same_name(self):
        merge = self.makeChildren([('x', '0')], [('a', '1')], [('a', '2')])
        self.assertRaises(ConflictError, merge)

    def test_children_keep_cur(self):
        merge = self.makeChildren([('x', '0')], [('a', '1')], [],
                                  cur_remove=['0'])
        merge()
        self.assertEquals(self.children, [('a', '1')])

    def test_children_keep_new(self):
        merge = self.makeChildren([('x', '0')], [], [('a', '1')],
                                  new_remove=['0'])
        merge()
        self.assertEquals(self.children, [('a', '1')])

    def test_children_add_remove(self):
        # Add in one, remove in another. Could we resolve this?
        merge = self.makeChildren([('x', '0')], [('a', '1')], [],
                                  new_remove=['0'])
        self.assertRaises(ConflictError, merge)

    def test_children_reorder_cur(self):
        merge = self.makeChildren([('x', '0'), ('a', '1')], [], [])
        self.cur.data[self.cur.root_uuid].children[:] = [
            ('a', '1'), ('x', '0')]
        merge()
        self.assertEquals(self.children, [('a', '1'), ('x', '0')])

    def test_children_reorder_new(self):
        merge = self.makeChildren([('x', '0'), ('a', '1')], [], [])
        self.new.data[self.new.root_uuid].children[:] = [
            ('a', '1'), ('x', '0')]
        merge()
        self.assertEquals(self.children, [('a', '1'), ('x', '0')])



def test_suite():
    return unittest.TestSuite((
        unittest.makeSuite(InterfaceTests),
        unittest.makeSuite(MergerTests),
        ))

if __name__ == '__main__':
    unittest.TextTestRunner().run(test_suite())
