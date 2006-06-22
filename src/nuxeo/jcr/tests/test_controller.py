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
from datetime import datetime

from nuxeo.jcr.controller import JCRController


class FakeSocket(object):
    def __init__(self, toread=''):
        self.toread = toread
        self.sent = ''
    def recv(self, size):
        res = self.toread[:size]
        self.toread = self.toread[size:]
        return res
    def send(self, data):
        self.sent += data
        return len(data)
    def sendall(self, data):
        self.sent += data

class FakeDB(object):
    server = None

class ProtocolTest(unittest.TestCase):

    def makeOne(self, toread=''):
        c = JCRController(FakeDB())
        c._sock = FakeSocket(toread)
        return c

    def test_read_1(self):
        c = self.makeOne('Something more to see')
        self.assertEqual(c._read(0), '')
        self.assertEqual(c._read(4), 'Some')
        self.assertEqual(c._read(6), 'thing ')
        self.assertEqual(c._read(11), 'more to see')
        self.assertEqual(c._unprocessed, [])

    def test_read_2(self):
        c = self.makeOne()
        c._unprocessed = ['abc', 'def', 'ghij', 'klm', 'n', 'op']
        self.assertEqual(c._read(8), 'abcdefgh')
        self.assertEqual(c._read(6), 'ijklmn')
        self.assertEqual(c._read(2), 'op')
        self.assertEqual(c._unprocessed, [])

    def test_read_3(self):
        c = self.makeOne('Something more to')
        c._unprocessed = ['abc', 'def']
        self.assertEqual(c._read(16), 'abcdefSomething ')
        self.assertEqual(c._read(7), 'more to')
        self.assertEqual(c._unprocessed, [])

    def test_readline_1(self):
        c = self.makeOne('Something\nMore\n')
        self.assertEqual(c._readline(), 'Something')
        self.assertEqual(c._readline(), 'More')
        self.assertEqual(c._unprocessed, [])

    def test_readline_2(self):
        c = self.makeOne()
        c._unprocessed = ['abc', 'def', 'gh\nij', 'klm', 'n\n', '\nop\n']
        self.assertEqual(c._readline(), 'abcdefgh')
        self.assertEqual(c._readline(), 'ijklmn')
        self.assertEqual(c._readline(), '')
        self.assertEqual(c._readline(), 'op')
        self.assertEqual(c._unprocessed, [])

    def test_readline_3(self):
        c = self.makeOne('Something\nMore\nto')
        c._unprocessed = ['abc', 'def']
        self.assertEqual(c._readline(), 'abcdefSomething')
        self.assertEqual(c._readline(), 'More')
        self.assertEqual(c._unprocessed, ['to'])

    # API tests

    def test_login(self):
        c = self.makeOne('^some-uuid\n')
        uuid = c.login('foo')
        self.assertEqual(c._sock.sent, 'Lfoo\n')
        self.assertEqual(c._unprocessed, [])
        self.assertEqual(uuid, 'some-uuid')

    def test_getNodeTypeDefs(self):
        c = self.makeOne('\n'.join((
            "[foo] > bar",
            "  - prop (string) = 'blah'",
            "  - * (undefined)",
            "  + child (nt:unstructured) multiple",
            "",
            ".\n")))
        s = c.getNodeTypeDefs()
        self.assertEqual(c._sock.sent, 'D\n')
        self.assertEqual(c._unprocessed, [])
        self.assert_(s.startswith('[foo]'), s)
        self.assert_('multiple' in s, s)

    def test_getNodeType(self):
        c = self.makeOne('Tnt:foo\n')
        type = c.getNodeType('some-uuid')
        self.assertEqual(c._sock.sent, 'Tsome-uuid\n')
        self.assertEqual(c._unprocessed, [])
        self.assertEqual(type, 'nt:foo')

    def test_getNodeStates(self):
        c = self.makeOne('\n'.join((
            # first answer

            'Uuuid somename',
            '^parent-uuid',

            'Nuuid1 type1 foo',
            'Nuuid2 type2 bar',
            'Nuuid3 type3 baz',

            'Pastring',
            's10', 'caf\xc3\xa9 babe',

            'Pabin',
            'x10', 'caf\xc3\xa9 babe',

            'Palong',
            'l123123123123',

            'Pafloat',
            'f123.456789',

            'Pabool',
            'b0',

            'Pdate1',
            'd2006-04-07T18:00:42.754Z',

            'Pdate2',
            'd2006-04-07T18:00:42.754+02:00',

            'Paname',
            'ndc:title',

            'Papath',
            'p/foo/bar:baz',

            'Paref',
            'rabc-def-ghijk',

            'M0 empty',

            'M2 multstr',
            's5', 'abcde',
            's8', '12345678',

            'Dsomedeferred',

            # second node

            'Uuuid1 foo',
            # no '^' parent

            'Nsubchild-uuid typemoo moo',

            'Pbool',
            'b1',

            # other unrequested node

            'Uuuid3 baz',
            '^baz-parent-uuid',
            'Ptitle',
            's5', 'Title',

            '.\n')))
        states = c.getNodeStates(['uuid', 'uuid1'])
        self.assertEqual(c._sock.sent, 'Suuid uuid1\n')
        self.assertEqual(c._unprocessed, [])
        self.assertEqual(sorted(states.keys()), ['uuid', 'uuid1', 'uuid3'])
        expected1 = [
            ('astring', u'caf\xe9 babe'),
            ('abin', 'caf\xc3\xa9 babe'),
            ('along', 123123123123),
            ('afloat', 123.456789),
            ('abool', False),
            ('date1', datetime(2006, 04, 07, 18, 0, 42, 754000)),
            ('date2', 'XXX'),
            ('aname', 'dc:title'),
            ('apath', '/foo/bar:baz'),
            ('aref', 'abc-def-ghijk'),
            ('empty', []),
            ('multstr', ['abcde', '12345678']),
            ]
        name, parent_uuid, children, props, deferred = states['uuid']
        self.assertEqual(name, 'somename')
        self.assertEqual(parent_uuid, 'parent-uuid')
        self.assertEqual(children, [('foo', 'uuid1', 'type1'),
                                    ('bar', 'uuid2', 'type2'),
                                    ('baz', 'uuid3', 'type3')])
        self.assertEqual([t[0] for t in props], [t[0] for t in expected1])
        for i, (key, value) in enumerate(expected1):
            if key.startswith('date'):
                continue # XXX
            self.assertEqual(value, props[i][1],
                             '%s: %r != %r' % (key, value, props[i][1]))
        self.assertEqual(deferred, ['somedeferred'])

        # second node

        name, parent_uuid, children, props, deferred = states['uuid1']
        self.assertEqual(name, 'foo')
        self.assertEqual(parent_uuid, None)
        self.assertEqual(children, [('moo', 'subchild-uuid', 'typemoo')])
        self.assertEqual(props, [('bool', True)])
        self.assertEqual(deferred, [])

        # unrequested node

        name, parent_uuid, children, props, deferred = states['uuid3']
        self.assertEqual(name, 'baz')
        self.assertEqual(parent_uuid, 'baz-parent-uuid')
        self.assertEqual(children, [])
        self.assertEqual(props, [('title', u'Title')])
        self.assertEqual(deferred, [])


def test_suite():
    return unittest.TestSuite((
        unittest.makeSuite(ProtocolTest),
        ))

if __name__ == '__main__':
    unittest.TextTestRunner().run(test_suite())
