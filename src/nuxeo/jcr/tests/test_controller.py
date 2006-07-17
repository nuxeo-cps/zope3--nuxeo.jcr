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
from difflib import ndiff
from datetime import datetime

from nuxeo.capsule.base import Blob
from nuxeo.capsule.base import Reference

from nuxeo.jcr.controller import JCRController


class fakedict(object):
    """Fake dict with a iteritems() that returns things in order."""
    def __init__(self, *items):
        self._items = items
    def iteritems(self):
        return self._items


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

            'Pastring\xc3\xa9', 's10', 'caf\xc3\xa9 babe', # len is in bytes
            'Pabin', 'x9', 'caf\xe9 babe',
            'Palong', 'l123123123123',
            'Pafloat', 'f123.456789',
            'Pabool', 'bfalse',
            'Pdate1', 'd2006-04-07T18:00:42.754Z', # XXX
            'Pdate2', 'd2006-04-07T18:00:42.754+02:00', # XXX
            'Paname', 'ndc:title',
            'Papath', 'p/foo/bar:baz',
            'Paref', 'rabc-def-ghijk',

            'Mempty',
            'M',

            'Mmultstr',
            's5', 'abcde',
            's8', '12345678',
            'M',

            'Dsomedeferred',

            # second node

            'Uuuid1 foo',
            # no '^' parent

            'Nsubchild-uuid typemoo moo',

            'Pbool', 'btrue',

            # other unrequested node

            'Uuuid3 baz',
            '^baz-parent-uuid',
            'Ptitle', 's5', 'Title',

            '.\n')))
        states = c.getNodeStates(['uuid', 'uuid1'])
        self.assertEqual(c._sock.sent, 'Suuid uuid1\n')
        self.assertEqual(c._unprocessed, [])
        self.assertEqual(sorted(states.keys()), ['uuid', 'uuid1', 'uuid3'])
        expected1 = [
            (u'astring\xe9', u'caf\xe9 babe'),
            (u'abin', Blob('caf\xe9 babe')),
            (u'along', 123123123123),
            (u'afloat', 123.456789),
            (u'abool', False),
            (u'date1', datetime(2006, 04, 07, 18, 0, 42, 754000)),
            (u'date2', 'XXX'),
            (u'aname', 'dc:title'),
            (u'apath', '/foo/bar:baz'),
            (u'aref', 'abc-def-ghijk'),
            (u'empty', []),
            (u'multstr', ['abcde', '12345678']),
            ]
        name, parent_uuid, children, props, deferred = states['uuid']
        self.assertEqual(name, 'somename')
        self.assertEqual(parent_uuid, 'parent-uuid')
        self.assertEqual(children, [('foo', 'uuid1', 'type1'),
                                    ('bar', 'uuid2', 'type2'),
                                    ('baz', 'uuid3', 'type3')])
        self.assertEqual([t[0] for t in props], [t[0] for t in expected1])
        for i, (key, value) in enumerate(expected1):
            v = props[i][1]
            if isinstance(value, Blob):
                self.assertEqual(type(value), type(v))
                self.assertEqual(value.data, v.data)
                continue
            if key.startswith('date'):
                continue # XXX
            self.assertEqual(value, v, '%s: %r != %r' % (key, value, v))
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


    def test_sendCommands(self):
        commands = [
            ('add', 'puuid1', u'fo\xe9', 'folder', fakedict(
                (u'astring', u'caf\xe9'),
                (u'ablob', Blob('expos\xe9')),
                (u'aint', 123),
                (u'afloat', 3.14),
                (u'adate', datetime(2006, 04, 07, 18, 0, 42, 754000)),
                (u'abool', True),
                (u'aref', Reference('dead-beef')),
                (u'multstr', [u'foo', u'bar']),
                ), 't1'),
            ('modify', 'uuid2', fakedict(
                (u'astring\xe9', u'foo'),
                (u'killme', None),
                )),
            ('remove', 'uuid3'),
            ('reorder', 'uuid4', (
                (u'a', u'b\xe9'),
                (u'c\xe9', u'd'),
                )),
            ]
        expect_sent = '\n'.join((
            'M',

            '+puuid1 folder t1 fo\xc3\xa9',
            'Pastring', 's5', 'caf\xc3\xa9',
            'Pablob', 'x6', 'expos\xe9',
            'Paint', 'l123',
            'Pafloat', 'f3.14',
            'Padate', 'd2006-04-07T18:00:42.754000',
            'Pabool', 'btrue',
            'Paref', 'rdead-beef',
            'Mmultstr',
              's3', 'foo',
              's3', 'bar',
            'M', # end multiple
            ',', # end props

            '/uuid2',
            'Pastring\xc3\xa9', 's3', 'foo',
            'Dkillme',
            ',', # end props

            '-uuid3',

            '%uuid4',
            'a/b\xc3\xa9',
            'c\xc3\xa9/d',
            '%',

            '.\n'))
        c = self.makeOne('\n'.join((
            't1 uuid1',
            '.\n')))
        map = c.sendCommands(commands)
        sent = c._sock.sent
        if sent != expect_sent:
            print "\nDifferences in output:"
            print ''.join(ndiff(expect_sent.splitlines(1),
                                sent.splitlines(1)))
        self.assertEqual(sent, expect_sent)
        self.assertEqual(c._unprocessed, [])
        self.assertEqual(sorted(map.items()), [
            ('t1', 'uuid1'),
            ])


def test_suite():
    return unittest.TestSuite((
        unittest.makeSuite(ProtocolTest),
        ))

if __name__ == '__main__':
    unittest.TextTestRunner().run(test_suite())
