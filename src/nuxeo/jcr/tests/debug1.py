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


NAMESPACES = [
    ('ecm', 'http://nuxeo.org/ecm/jcr/names'),
    ('ecmnt', 'http://nuxeo.org/ecm/jcr/types'),
    ('ecmst', 'http://nuxeo.org/ecm/jcr/schemas'),
    ('ecmdt', 'http://nuxeo.org/ecm/jcr/docs'),
    ('dc', 'http://purl.org/dc/elements/1.1/'),
    ]

NODETYPEDEFS = """
<ecm='http://nuxeo.org/ecm/jcr/names'>
<ecmnt='http://nuxeo.org/ecm/jcr/types'>
<ecmst='http://nuxeo.org/ecm/jcr/schemas'>
<ecmdt='http://nuxeo.org/ecm/jcr/docs'>
<dc='http://purl.org/dc/elements/1.1/'>

// schema base
[ecmnt:schema]

// document
[ecmnt:document]

// non-orderable  folder
[ecmnt:folder] > ecmnt:document
  + * (ecmnt:document)

// dublin core
[ecmst:dublincore] > ecmnt:schema
  - dc:title
  - dc:description (String)

////////// example

// a complex type for firstname+lastname
[ecmst:name] > ecmnt:schema
  - firstname (String)
  - lastname (String)

// the schema for the tripreport part
[ecmst:tripreport] > ecmnt:schema
  - duedate (Date)
  - cities (String) multiple
  + username (ecmst:name)
  + childrennames (ecmst:name) multiple

// a full document type
[ecmdt:tripreport] > ecmnt:document, ecmst:tripreport, ecmst:dublincore

"""


class DummyXid(Xid):
    def getBranchQualifier(self):
        return []
    def getFormatId(self):
        return 0
    def getGlobalTransactionId(self):
        return []



credentials = javax.jcr.SimpleCredentials('username', 'password')

def output(s):
    print s,


def dumpNode(node, spaces):
    name = node.getName()
    if name == 'jcr:nodeTypes':
        return
    path = node.getPath()
    t = node.getProperty('jcr:primaryType').getString()
    output(spaces + "%s (%s)\n" % (path, t))
    spaces += "  "
    for p in node.getProperties():
        name = p.getName()
        if name == 'jcr:primaryType':
           continue
        try:
            s = p.getString()
        except javax.jcr.ValueFormatException:
            s = str([v.getString() for v in p.getValues()])
        output(spaces + "prop %s %s\n" % (name, s))
    for n in node.getNodes():
        dumpNode(n, spaces)


def checkRepositoryInit(session):
    """Check that things are ok in the repository, after creation.

    """
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

def checkNodeTypeDefs(session):
    return
    workspace = session.getWorkspace()
    ntm = workspace.getNodeTypeManager()
    try:
        ntm.getNodeType('ecmnt:document')
        return
    except:
        # NoSuchNodeTypeException, UnknownPrefixException
        pass
    # Create node types from CND data
    nsr = workspace.getNamespaceRegistry()
    for prefix, uri in NAMESPACES:
        try:
            nsr.registerNamespace(prefix, uri)
        except javax.jcr.NamespaceException:
            # already registered
            pass
    reader = java.io.ByteArrayInputStream(NODETYPEDEFS)
    #reader = java.io.StringReader(NODETYPEDEFS)
    ntm.registerNodeTypes(reader, 'text/x-jcr-cnd')


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

    def __init__(self, repository, workspaceName):
        self.repository = repository
        self.workspaceName = workspaceName
        self.session1 = None
        self.session2 = None

    def main(self):
        try:
            #self.doit_dump()
            self.doit_listeners()
            self.doit_transactions()
            self.doit_drafts()
        finally:
            if self.session1 is not None:
                self.session1.logout()
            if self.session2 is not None:
                self.session2.logout()

    def doit_dump(self):
        session = repository.login(credentials, workspaceName)
        session.exportSystemView('/', sys.stdout, False, False)

    def doit_listeners(self):
        # first session
        session = repository.login(credentials, workspaceName)
        self.session1 = session
        checkRepositoryInit(session)
        workspace = session.getWorkspace()
        xaresource = session.getXAResource()
        xid = DummyXid()
        xaresource.start(xid, XAResource.TMNOFLAGS)

        om = workspace.getObservationManager()
        listener = Listener('1')
        eventTypes = (NODE_ADDED | NODE_REMOVED |
                      PROPERTY_ADDED | PROPERTY_REMOVED | PROPERTY_CHANGED)
        isDeep = True
        noLocal = True
        om.addEventListener(listener, eventTypes, '/',
                            isDeep, None, None, noLocal)


        # session 2
        session2 = repository.login(credentials, workspaceName)
        self.session2 = session2
        workspace2 = session2.getWorkspace()
        om2 = workspace2.getObservationManager()
        listener2 = Listener('2')
        isDeep = True
        noLocal = False
        om2.addEventListener(listener2, eventTypes, '/',
                             isDeep, None, None, noLocal)

        # session 1
        print 'deleting'
        root = session.getRootNode()
        while root.hasNode('blob'):
            node = root.getNode('blob')
            node.remove()
            #root.save()
        print 'adding'
        blob1 = root.addNode('blob', 'nt:unstructured')
        #print 'added', blob1.getUUID()
        blob2 = root.addNode('blob', 'nt:unstructured')
        #print 'added', blob2.getUUID()
        root.save()
        print 'setting prop'
        blob1.setProperty('youpi', 'true', BOOLEAN)
        blob2.setProperty('hoho', 'false', BOOLEAN)
        root.orderBefore('blob[2]', 'blob[1]')
        root.save()
        if 0:
            print 'add mixin'
            blob.addMixin('mix:versionable')
            print 'save'
            root.save()
        if 0:
            print 'checkin'
            blob.checkin()
            print 'checkout'
            blob.checkout()
        print 'setting subnode'
        node = blob1.addNode('under', 'nt:unstructured')
        #print 'added', node.getUUID()
        print 'saving'
        root.save()

    def doit_transactions(self):
        # session 1
        session1 = repository.login(credentials, workspaceName)
        self.session1 = session1
        checkRepositoryInit(session1)
        workspace1 = session1.getWorkspace()
        xaresource1 = session1.getXAResource()
        xid1 = DummyXid()

        om = workspace1.getObservationManager()
        listener1 = Listener('1')
        eventTypes = (NODE_ADDED | NODE_REMOVED |
                      PROPERTY_ADDED | PROPERTY_REMOVED | PROPERTY_CHANGED)
        isDeep = True
        noLocal = False
        om.addEventListener(listener1, eventTypes, '/',
                            isDeep, None, None, noLocal)


        # session 2
        session2 = repository.login(credentials, workspaceName)
        self.session2 = session2
        workspace2 = session2.getWorkspace()
        xaresource2 = session2.getXAResource()
        xid2 = DummyXid()

        om2 = workspace2.getObservationManager()
        listener2 = Listener('2')
        isDeep = True
        noLocal = False
        om2.addEventListener(listener2, eventTypes, '/',
                             isDeep, None, None, noLocal)

        # outside session
        root = session1.getRootNode()
        while root.hasNode('blob'):
            node = root.getNode('blob')
            node.remove()
            #root.save()
        blob = root.addNode('blob', 'nt:unstructured')
        root.save()

        # start transactions
        xaresource1.start(xid1, XAResource.TMNOFLAGS)
        xaresource2.start(xid2, XAResource.TMNOFLAGS)

        root1 = session1.getRootNode()
        root2 = session2.getRootNode()
        blob1 = root1.getNode('blob')
        blob2 = root2.getNode('blob')
        blob1.setProperty('youpi', 'true', BOOLEAN)
        blob2.setProperty('youpi', 'false', BOOLEAN)

        # commit
        print 'now save'
        root1.save()
        root2.save()

        xaresource1.end(xid1, XAResource.TMSUCCESS)
        xaresource2.end(xid2, XAResource.TMSUCCESS)
        print 'prepare 1'
    	xaresource1.prepare(xid1)
        print 'commit 1'
    	xaresource1.commit(xid1, False)
        print 'prepare 2'
        try:
            xaresource2.prepare(xid2)
        except XAException, e:
            msgs = []
            while e is not None:
                msg = e.getMessage()
                if msg is not None:
                    if msg.endswith('.'):
                        msg = msg[:-1]
                    msgs.append(msg)
                e = e.getCause()
            print "cannot prepare 2,", ': '.join(msgs)
            print 'rollback 2'
            xaresource2.rollback(xid2)
        else:
            print 'commit 2'
            xaresource2.commit(xid2, False)

    ##################################################

    def doit_drafts(self):
        # session 1
        session = repository.login(credentials, workspaceName)
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
    workspaceName = 'default'
    repository = TransientRepository(repoconf, repopath)

    Main(repository, workspaceName).main()
    sys.stdout.flush()
