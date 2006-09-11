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

Can be run through following script:

jython=$HOME/Java/jython-2.1
jars=$HOME/Java/lib
cp=$jars/commons-collections-3.1.jar
cp=$cp:$jars/concurrent-1.3.4.jar
cp=$cp:$jars/derby-10.1.1.0.jar
cp=$cp:$jars/geronimo-spec-jta-1.0-M1.jar
cp=$cp:$jars/jackrabbit-core-1.1-SNAPSHOT.jar
cp=$cp:$jars/jackrabbit-jcr-commons-1.1-SNAPSHOT.jar
cp=$cp:$jars/jcr-1.0.jar
cp=$cp:$jars/junit-3.8.1.jar
cp=$cp:$jars/log4j-1.2.8.jar
cp=$cp:$jars/lucene-1.4.3.jar
cp=$cp:$jars/slf4j-log4j12-1.0.jar
cp=$cp:$jars/xercesImpl-2.6.2.jar
cp=$cp:$jars/xmlParserAPIs-2.0.2.jar
java -Dpython.home=$jython \
    -classpath $jython/jython.jar:$cp:$CLASSPATH \
    org.python.util.jython "$@"

Pass as an argument the repository path.
"""

import sys

from javax.jcr import SimpleCredentials
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


class Dummy:
    # Class needed because the bug is memory-layout dependent.
    def foo(self): pass


def doit(session):
    root = session.getRootNode()

    # Transaction setup
    workspace = session.getWorkspace()
    xaresource = session.getXAResource()

    ################################################## T1

    print 'start 1'
    xid1 = DummyXid()
    xaresource.start(xid1, XAResource.TMNOFLAGS)

    # Create node, subnode, set props
    node = root.addNode('blob', 'nt:unstructured')
    node.addMixin('mix:versionable')
    print 'node', node.getUUID()
    node.addNode('sub', 'nt:unstructured')
    root.save()
    node.setProperty('youpi', 'yo')
    root.save()

    print 'commit 1'
    xaresource.end(xid1, XAResource.TMSUCCESS)
    xaresource.commit(xid1, True)

    ################################################## T2

    print 'start 2'
    xid2 = DummyXid()
    xaresource.start(xid2, XAResource.TMNOFLAGS)

    # checkin + checkout
    node.checkin()
    node.checkout()
    root.save()

    print 'commit 2'
    xaresource.end(xid2, XAResource.TMSUCCESS)
    xaresource.commit(xid2, True)

    ################################################## T3

    print 'start 3'
    xid3 = DummyXid()
    xaresource.start(xid3, XAResource.TMNOFLAGS)

    # restore
    node.restore(node.getBaseVersion(), True)
    node.checkout()
    # modify subnode
    node.getNode('sub').setProperty('foo', '3') # needed for crash
    root.save()

    print 'commit 3'
    xaresource.end(xid3, XAResource.TMSUCCESS)
    xaresource.commit(xid3, True)

    ################################################## T4

    print 'start 4'
    xid4 = DummyXid()
    xaresource.start(xid4, XAResource.TMNOFLAGS)

    # checkin + checkout
    node.checkin()
    node.checkout()
    # modify node, subnode
    node.setProperty('youpi', 'ho') # needed for crash
    node.getNode('sub').setProperty('foo', '4') # needed for crash
    root.save()

    print 'commit 4'
    xaresource.end(xid4, XAResource.TMSUCCESS)
    xaresource.commit(xid4, True)

    print 'done'


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print >>sys.stderr, "Usage: debug1.py <repopath>"
        sys.exit(1)
    repopath = sys.argv[1]
    repoconf = repopath+'.xml'

    repository = TransientRepository(repoconf, repopath)
    credentials = SimpleCredentials('username', 'password')
    session = repository.login(credentials, 'default')
    try:
        doit(session)
    finally:
        session.logout()

    sys.stdout.flush()
