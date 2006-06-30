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
"""JCR Jython server

Can be run through following script:

jython=$HOME/Java/jython-2.1
jars=$HOME/Java/lib
cp=$jars/commons-collections-3.1.jar
cp=$cp:$jars/concurrent-1.3.4.jar
cp=$cp:$jars/derby-10.1.1.0.jar
cp=$cp:$jars/geronimo-spec-jta-1.0-M1.jar
cp=$cp:$jars/jackrabbit-core-1.0.1.jar
cp=$cp:$jars/jackrabbit-jcr-commons-1.0.1.jar
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

"""

import sys

import java.io
import javax.jcr
import javax.jcr.observation.EventListener
from javax.jcr.observation.Event import NODE_ADDED
from javax.jcr.observation.Event import NODE_REMOVED
from javax.jcr.observation.Event import PROPERTY_ADDED
from javax.jcr.observation.Event import PROPERTY_REMOVED
from javax.jcr.observation.Event import PROPERTY_CHANGED

from org.apache.jackrabbit.core import TransientRepository


NAMESPACES = [
    ('cpsnt', 'http://nuxeo.org/jcr/1.0/cpsnt/'),
    ('cpss', 'http://nuxeo.org/jcr/1.0/cpss/'),
    ('cpst', 'http://nuxeo.org/jcr/1.0/cpst/'),
    ('cpsd', 'http://nuxeo.org/jcr/1.0/cpsd/'),
    ('cps', 'http://nuxeo.org/jcr/1.0/cps/'),
    ]

NODETYPEDEFS = """
<cpsnt='http://nuxeo.org/jcr/1.0/cpsnt/'>
<cpss='http://nuxeo.org/jcr/1.0/cpss/'>
<cpst='http://nuxeo.org/jcr/1.0/cpst/'>
<cpsd='http://nuxeo.org/jcr/1.0/cpsd/'>
<cps='http://nuxeo.org/jcr/1.0/cps/'>

// complex type base
[cpsnt:type]

// schema base
[cpsnt:schema]

// document
[cpsnt:document]

// non-orderable  folder
[cpsnt:folder] > cpsnt:document
  + * (cpsnt:document)

// dublin core
[cpss:dublincore] > cpsnt:schema
  - cps:title
  - cps:description (String)

////////// example

// a complex type for firstname+lastname
[cpst:name] > cpsnt:type
  - cps:firstname (String)
  - cps:lastname (String)

// the schema for the tripreport part
[cpss:tripreport] > cpsnt:schema
  - cps:duedate (Date)
  - cps:cities (String) multiple
  + cps:username (cpst:name)
  + cps:childrennames (cpst:name) multiple

// a full document type
[cpsd:tripreport] > cpsnt:document, cpss:tripreport, cpss:dublincore

"""


try:
    True
except NameError:
    True = 1
    False = 0



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
    checkNodeTypeDefs(session)
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
        node.setProperty('bool', 'true', javax.jcr.PropertyType.BOOLEAN)
        root.save()
        node.checkin()

def checkNodeTypeDefs(session):
    workspace = session.getWorkspace()
    ntm = workspace.getNodeTypeManager()
    try:
        ntm.getNodeType('cpsnt:document')
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
            print '%s: event %-16s path %s' % (
                self.name,
                self.eventString(event.getType()),
                event.getPath())

class Main:

    def __init__(self, repository, workspaceName):
        self.repository = repository
        self.workspaceName = workspaceName
        self.session = None
        self.session2 = None

    def main(self):
        try:
            self.doit()
        finally:
            if self.session is not None:
                self.session.logout()
            if self.session2 is not None:
                self.session2.logout()

    def doit(self):
        # first session
        session = repository.login(credentials, workspaceName)
        self.session = session
        checkRepositoryInit(self.session)
        workspace = session.getWorkspace()

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
        if root.hasNode('blob'):
            node = root.getNode('blob')
            node.remove()
            #root.save()
        print 'adding'
        blob = root.addNode('blob', 'nt:unstructured')
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
        print 'setting prop'
        blob.setProperty('youpi', 'true', javax.jcr.PropertyType.BOOLEAN)
        print 'setting subnode'
        node = blob.addNode('under', 'nt:unstructured')
        print 'saving'
        root.save()


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print >>sys.stderr, "Usage: debugjcr.py <repopath>"
        sys.exit(1)

    repopath = sys.argv[1]
    repoconf = repopath+'.xml'
    workspaceName = 'default'
    repository = TransientRepository(repoconf, repopath)

    Main(repository, workspaceName).main()



if 0:
    node = root.addNode('toto', 'nt:unstructured')
    node.addMixin('mix:versionable')
    root.save()
    node.checkin()

if 0:
    node = root.getNode('toto')
    node.checkout()
    node.setProperty('foo', 'hello bob')
    root.save()
    node.checkin()

