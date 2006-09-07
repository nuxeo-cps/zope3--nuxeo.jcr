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
"""Testcase to debug JCR bug.

Can be run through:
  bin/jcrctl jython debug1.py <repopath>
"""

import sys

import java.io
import javax.jcr

from javax.transaction.xa import XAResource
from javax.transaction.xa import XAException
from javax.transaction.xa import Xid

from org.apache.jackrabbit.core import TransientRepository

try:
    True
except NameError:
    True = 1
    False = 0


class DummyXid(Xid):
    def getBranchQualifier(self):
        return []
    def getFormatId(self):
        return 0
    def getGlobalTransactionId(self):
        return []


class Main:

    def __init__(self, repository):
        self.repository = repository
        self.session = None

    def main(self):
        try:
            self.doit()
        finally:
            if self.session is not None:
                self.session.logout()

    def doit(self):
        # session
        credentials = javax.jcr.SimpleCredentials('username', 'password')
        self.session = session = self.repository.login(credentials, 'default')

        root = session.getRootNode()

        # Transaction setup
        workspace = session.getWorkspace()
        xaresource = session.getXAResource()
        xid = DummyXid()

        ################################################## T1

        print 'start 1'
        xaresource.start(xid, XAResource.TMNOFLAGS)

        # Create node, subnode, set props
        node = root.addNode('blob', 'nt:unstructured')
        node.addMixin('mix:versionable')
        print 'node', node.getUUID()
        node.addNode('sub', 'nt:unstructured')
        root.save()
        node.setProperty('youpi', 'yo')
        root.save()

        print 'commit 1'
        xaresource.end(xid, XAResource.TMSUCCESS)
    	xaresource.commit(xid, True)

        ################################################## T2

        print 'start 2'
        xaresource.start(xid, XAResource.TMNOFLAGS)

        # checkin + checkout
        node.checkin()
        node.checkout()
        root.save()

        print 'commit 2'
        xaresource.end(xid, XAResource.TMSUCCESS)
        xaresource.commit(xid, True)

        ################################################## T3

        print 'start 3'
        xaresource.start(xid, XAResource.TMNOFLAGS)

        # restore
        node.restore(node.getBaseVersion(), True)
        node.checkout()
        # modify subnode
        node.getNode('sub').setProperty('foo', '3') # needed for crash
        root.save()

        print 'commit 3'
        xaresource.end(xid, XAResource.TMSUCCESS)
        xaresource.commit(xid, True)

        ################################################## T4

        print 'start 4'
        xaresource.start(xid, XAResource.TMNOFLAGS)

        # checkin + checkout
        node.checkin()
        node.checkout()
        # modify node, subnode
        node.setProperty('youpi', 'ho') # needed for crash
        node.getNode('sub').setProperty('foo', '4') # needed for crash
        root.save()

        print 'commit 4'
        xaresource.end(xid, XAResource.TMSUCCESS)
        xaresource.commit(xid, True)

        print 'done'


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print >>sys.stderr, "Usage: debug1.py <repopath>"
        sys.exit(1)

    repopath = sys.argv[1]
    repoconf = repopath+'.xml'
    repository = TransientRepository(repoconf, repopath)

    Main(repository).main()
    sys.stdout.flush()
