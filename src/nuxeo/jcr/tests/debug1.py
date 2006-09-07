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
import javax.jcr.observation.EventListener
from javax.jcr.PropertyType import BOOLEAN
from javax.jcr.observation.Event import NODE_ADDED
from javax.jcr.observation.Event import NODE_REMOVED
from javax.jcr.observation.Event import PROPERTY_ADDED
from javax.jcr.observation.Event import PROPERTY_REMOVED
from javax.jcr.observation.Event import PROPERTY_CHANGED

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



credentials = javax.jcr.SimpleCredentials('username', 'password')


class Listener(javax.jcr.observation.EventListener):
    strings = {
        NODE_ADDED: 'NODE_ADDED',
        NODE_REMOVED: 'NODE_REMOVED',
        PROPERTY_ADDED: 'PROPERTY_ADDED',
        PROPERTY_REMOVED: 'PROPERTY_REMOVED',
        PROPERTY_CHANGED: 'PROPERTY_CHANGED',
        }
    def __init__(self, name):
        self.name = name

    def eventString(self, type):
        return self.strings.get(type, str(type))

    def onEvent(self, events):
        while events.hasNext():
            event = events.nextEvent()
            type = event.getType()
            if type in (NODE_ADDED, NODE_REMOVED):
                childid = event.getChildId().toString()
            else:
                childid = ''
            print '%s: event %-16s path %s (%s)' % (
                self.name,
                self.eventString(event.getType()),
                event.getPath(),
                childid)

class Main:

    def __init__(self, repository):
        self.repository = repository
        self.session1 = None
        self.session2 = None

    def main(self):
        try:
            self.doit_drafts()
        finally:
            if self.session1 is not None:
                self.session1.logout()
            if self.session2 is not None:
                self.session2.logout()

    def doit_drafts(self):
        # session 1
        session = repository.login(credentials, 'default')
        self.session1 = session # for cleanup if exception

        root = session.getRootNode()

        if not root.isNodeType('mix:referenceable'):
            root.addMixin('mix:referenceable')
        if not root.hasNode('toto'):
            node = root.addNode('toto', 'nt:unstructured')
            node.addMixin('mix:versionable')
            root.save()
            node.checkin()
            node.checkout()
            node.setProperty('foo', 'hello bob')
            root.save()
            node.checkin()
        node = root.getNode('toto')
        if not node.hasProperty('bool'):
            node.checkout()
            node.setProperty('bool', 'true', BOOLEAN)
            root.save()
            node.checkin()

        # Transaction /events setup
        workspace = session.getWorkspace()
        xaresource = session.getXAResource()
        xid = DummyXid()
        om = workspace.getObservationManager()
        listener = Listener('listener')
        eventTypes = (NODE_ADDED | NODE_REMOVED |
                      PROPERTY_ADDED | PROPERTY_REMOVED | PROPERTY_CHANGED)
        isDeep = True
        noLocal = False
        om.addEventListener(listener, eventTypes, '/', isDeep, None, None, noLocal)

        ################################################## T1

        print 'start 1'
        xaresource.start(xid, XAResource.TMNOFLAGS)

        while root.hasNode('blob'):
            node = root.getNode('blob')
            node.remove()

        # Create node, set prop
        node = root.addNode('blob', 'nt:unstructured')
        node.addMixin('mix:versionable')
        node.addNode('sub', 'nt:unstructured')
        root.save()
        node.setProperty('youpi', 'oui')
        node.getNode('sub').setProperty('foo', '1')
        root.save()

        # prepare/commit
        print 'end 1'
        xaresource.end(xid, XAResource.TMSUCCESS)
        print 'prepare 1'
    	xaresource.prepare(xid)
        print 'commit 1'
    	xaresource.commit(xid, False)

        ################################################## T2

        print 'start 2'
        xaresource.start(xid, XAResource.TMNOFLAGS)

        # checkin + checkout
        node.checkin()
        node.checkout()
        root.save()
        node.setProperty('youpi', 'non')
        node.getNode('sub').setProperty('foo', '2')
        root.save()

        # prepare/commit
        print 'end 2'
        xaresource.end(xid, XAResource.TMSUCCESS)
        print 'prepare 2'
        xaresource.prepare(xid)
        print 'commit 2'
        xaresource.commit(xid, False)

        ################################################## T3

        print 'start 3'
        xaresource.start(xid, XAResource.TMNOFLAGS)

        # restore
        node.restore(node.getBaseVersion(), True)
        node.checkout()
        # modify doc (secu) et wf status
        node.setProperty('youpi', 'ptet')
        node.getNode('sub').setProperty('foo', '3') # needed for crash
        root.save()

        # prepare/commit
        print 'end 3'
        xaresource.end(xid, XAResource.TMSUCCESS)
        print 'prepare 3'
        xaresource.prepare(xid)
        print 'commit 3'
        xaresource.commit(xid, False)

        ################################################## T4

        print 'start 4'
        xaresource.start(xid, XAResource.TMNOFLAGS)

        # checkin + checkout
        node.checkin()
        node.checkout()
        # modify doc (secu) et wf status
        node.setProperty('youpi', 'n/a') # needed for crash
        node.getNode('sub').setProperty('foo', '4') # needed for crash
        root.save()

        # prepare/commit
        print 'end 4'
        xaresource.end(xid, XAResource.TMSUCCESS)
        print 'prepare 4'
        xaresource.prepare(xid)
        print 'commit 4'
        xaresource.commit(xid, False)

        print 'done'


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print >>sys.stderr, "Usage: debugjcr.py <repopath>"
        sys.exit(1)

    repopath = sys.argv[1]
    repoconf = repopath+'.xml'
    repository = TransientRepository(repoconf, repopath)

    Main(repository).main()
    sys.stdout.flush()
