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
import java.util
import java.net
import java.nio
import java.nio.charset
import java.nio.channels
from java.nio.channels import SelectionKey

import javax.jcr
import javax.jcr.observation.EventListener
from javax.jcr.observation.Event import NODE_ADDED
from javax.jcr.observation.Event import NODE_REMOVED
from javax.jcr.observation.Event import PROPERTY_ADDED
from javax.jcr.observation.Event import PROPERTY_REMOVED
from javax.jcr.observation.Event import PROPERTY_CHANGED

from javax.transaction.xa import XAResource
from javax.transaction.xa import XAException
from javax.transaction.xa import Xid

from org.apache.jackrabbit.core import TransientRepository
from org.apache.jackrabbit.core.nodetype.compact import \
     CompactNodeTypeDefWriter
from org.apache.jackrabbit.core import XASession


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




charset = java.nio.charset.Charset.forName('iso-8859-1')
latinDecoder = charset.newDecoder()
latinEncoder = charset.newEncoder()

credentials = javax.jcr.SimpleCredentials('username', 'password')



def dumpNode(node, spaces, output):
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
        dumpNode(n, spaces, output)


class Listener(javax.jcr.observation.EventListener):

    strings = {
        NODE_ADDED: 'NODE_ADDED',
        NODE_REMOVED: 'NODE_REMOVED',
        PROPERTY_ADDED: 'PROPERTY_ADDED',
        PROPERTY_REMOVED: 'PROPERTY_REMOVED',
        PROPERTY_CHANGED: 'PROPERTY_CHANGED',
        }

    def __init__(self, processor, name):
        self.processor = processor
        self.name = name

    def _eventString(self, type):
        return self.strings.get(type, str(type))

    def onEvent(self, events):
        while events.hasNext():
            event = events.nextEvent()
            print '%s: event %-16s path %s' % (
                self.name,
                self._eventString(event.getType()),
                event.getPath())


class DummyXid(Xid):
    def getBranchQualifier(self):
        return []
    def getFormatId(self):
        return 0
    def getGlobalTransactionId(self):
        return []


class Processor:
    """Command line processor, tied to a JCR Session.
    """

    session = None
    root = None
    prepared = False

    def __init__(self, io, repository):
        self.io = io
        self.repository = repository
        self.state = 0

    def write(self, s):
        self.io.write(s)

    def writeln(self, s):
        self.io.write(s+'\n')


    def cmdHelp(self, line=None):
        self.writeln("Available commands:")
        keys = self.commands.keys()
        keys.sort()
        for cmd in keys:
            func, desc = self.commands[cmd]
            self.writeln("  %s: %s" % (cmd, desc))


    def cmdLogin(self, workspaceName):
        if self.session is not None:
            return self.writeln("!Already logged in.")
        try:
            self.login(workspaceName)
        except javax.jcr.NoSuchWorkspaceException:
            return self.writeln("!No such workspace '%s'." % workspaceName)
        self.root = self.session.getRootNode()
        self.checkRepositoryInit()
        self.writeln('^'+self.root.getUUID())

    def login(self, workspaceName):
        session = self.repository.login(credentials, workspaceName)
        xaresource = session.getXAResource()
        xid = DummyXid()
        self.session = session
        self.xaresource = xaresource
        self.xid = xid

        self.new()

        om = self.session.getWorkspace().getObservationManager()
        listener = Listener(self, workspaceName)
        eventTypes = (NODE_ADDED | NODE_REMOVED |
                      PROPERTY_ADDED | PROPERTY_REMOVED | PROPERTY_CHANGED)
        isDeep = True
        noLocal = False
        om.addEventListener(listener, eventTypes, '/',
                            isDeep, None, None, noLocal)


    def new(self, end=None):
        self.xaresource.start(self.xid, XAResource.TMNOFLAGS)
        self.prepared = False

    def _trapXAException(self, func, *args):
        try:
            func(*args)
        except XAException, e:
            msg = e.getMessage() or 'XAException %s' % e.errorCode
            e = e.getCause()
            while e is not None:
                m = e.getMessage()
                if m is not None:
                    msg = m
                    if msg.endswith(' has been modified externally'):
                        break
                e = e.getCause()
            return "!"+msg
        return None

    def cmdPrepare(self, line=None):
        # Note that there is a default timeout of 5s after prepare
        if self.prepared:
            return self.writeln("!Already prepared.")
        msg = self._trapXAException(self.xaresource.prepare, self.xid)
        if msg is not None:
            self.xaresource.end(self.xid, XAResource.TMFAIL)
            self.rollback()
            self.new()
        else:
            msg = '.'
            self.prepared = True
        self.writeln(msg)

    def cmdCommit(self, line=None):
        if not self.prepared:
            return self.writeln("!Not prepared.")
        # End association before commit
        self.xaresource.end(self.xid, XAResource.TMSUCCESS)
        msg = self._trapXAException(self.xaresource.commit, self.xid, False)
        if msg is not None:
            self.rollback()
        else:
            msg = '.'
        self.new()
        self.writeln(msg)

    def cmdRollback(self, line=None):
        # End association before rollback
        self.xaresource.end(self.xid, XAResource.TMFAIL)
        msg = self.rollback()
        if msg is None:
            msg = '.'
        self.new()
        self.writeln(msg)

    def rollback(self):
        return self._trapXAException(self.xaresource.rollback, self.xid)

    def cmdQuit(self, line=None):
        self.logout()
        self.io.close()

    def logout(self):
        self._trapXAException(self.rollback)
        if self.session is not None:
            self.session.logout()

    def cmdStop(self, line=None):
        raise SystemExit

    def cmdDump(self, line=None):
        if self.root is None:
            return self.writeln("!Not logged in.")
        dumpNode(self.root, '', self.write)
        self.writeln('.')

    def checkRepositoryInit(self):
        """Check that things are ok in the repository, after creation.

        """
        self.checkNodeTypeDefs()
        root = self.root
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

    def checkNodeTypeDefs(self):
        workspace = self.session.getWorkspace()
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

    def dumpNodeTypes(self):
        workspace = self.session.getWorkspace()
        ntr = workspace.getNodeTypeManager().getNodeTypeRegistry()
        nsr = workspace.getNamespaceRegistry()
        l = java.util.ArrayList()
        for name in ntr.getRegisteredNodeTypes():
            l.add(ntr.getNodeTypeDef(name))
        sw = java.io.StringWriter()
        CompactNodeTypeDefWriter.write(l, nsr, sw)
        return sw.toString()

    def cmdGetNodeTypeDefs(self, line):
        if self.session is None:
            return self.writeln("!Not logged in.")
        schema = self.dumpNodeTypes()
        self.write(schema)
        self.writeln('\n.')

    def cmdGetNodeChildren(self, uuid):
        try:
            node = self.session.getNodeByUUID(uuid)
        except javax.jcr.ItemNotFoundException:
            return self.writeln("!No such uuid '%s'" % uuid)
        for subnode in node.getNodes():
            try:
                subuuid = subnode.getUUID()
            except javax.jcr.UnsupportedRepositoryOperationException:
                print "Node %s is not referenceable" % subnode.getPath()
                continue
            self.writeln('%s %s' % (subnode.getUUID(), subnode.getName()))
        self.writeln('.')

    def cmdGetNodeType(self, uuid):
        try:
            node = self.session.getNodeByUUID(uuid)
        except javax.jcr.ItemNotFoundException:
            return self.writeln("!No uuid '%s'" % uuid)
        nodeType = node.getProperty('jcr:primaryType').getString()
        self.writeln('T%s' % nodeType)

    def cmdGetNodeStates(self, line):
        uuids = line.split(' ')
        # Check all UUIDs exist
        for node_uuid in uuids:
            try:
                node = self.session.getNodeByUUID(node_uuid)
            except javax.jcr.ItemNotFoundException:
                return self.writeln("!No uuid '%s'" % node_uuid)
        # Fetch all info
        for node_uuid in uuids:
            node = self.session.getNodeByUUID(node_uuid)
            # Node UUID and name
            self.writeln('U%s %s' % (node.getUUID(), node.getName()))
            # Parent
            try:
                parent_uuid = node.getParent().getUUID()
            except javax.jcr.ItemNotFoundException:
                pass
            else:
                self.writeln('^%s' % parent_uuid)
            # Children
            for subnode in node.getNodes():
                try:
                    subuuid = subnode.getUUID()
                except javax.jcr.UnsupportedRepositoryOperationException:
                    print "XXX %s is not referenceable" % subnode.getPath()
                    continue
                nodeType = subnode.getProperty('jcr:primaryType').getString()
                nodeName = subnode.getName()
                self.writeln('N%s %s %s' % (subuuid, nodeType, nodeName))
            # Properties
            for prop in node.getProperties():
                name = prop.getName()
                definition = prop.getDefinition()
                if definition.isMultiple():
                    values = prop.getValues()
                    self.writeln('M%d %s' % (len(values), name))
                    for value in values:
                        self.dumpValue(value)
                else:
                    self.writeln('P%s' % name)
                    self.dumpValue(prop.getValue())
        self.writeln('.')

    def cmdSave(self, line=None):
        if self.prepared:
            return self.writeln("!Can only commit or rollback while prepared.")
        self.session.save() # XXX exceptions?
        self.writeln('.')

    def cmdGetNodeProperties(self, line):
        self.writeln('!XXX not implemented')

    def dumpString(self, value):
        s = value.getString().encode('utf-8')
        self.writeln('s%d' % len(s))
        self.writeln(s)
    def dumpBinary(self, value):
        s = value.getString()
        self.writeln('x%d' % len(s))
        self.writeln(s)
    def dumpLong(self, value):
        self.writeln('l%s' % value.getString())
    def dumpDouble(self, value):
        self.writeln('f%s' % value.getString())
    def dumpDate(self, value):
        self.writeln('d%s' % value.getString()) # 2006-04-07T18:00:42.754+02:00
    def dumpBoolean(self, value):
        self.writeln('b%d' % int(value.getBoolean()))
    def dumpName(self, value):
        self.writeln('n%s' % value.getString())
    def dumpPath(self, value):
        self.writeln('p%s' % value.getString())
    def dumpReference(self, value):
        self.writeln('r%s' % value.getString())

    valueDumpers = {
        javax.jcr.PropertyType.STRING: dumpString,
        javax.jcr.PropertyType.BINARY: dumpBinary,
        javax.jcr.PropertyType.LONG: dumpLong,
        javax.jcr.PropertyType.DOUBLE: dumpDouble,
        javax.jcr.PropertyType.DATE: dumpDate,
        javax.jcr.PropertyType.BOOLEAN: dumpBoolean,
        javax.jcr.PropertyType.NAME: dumpName,
        javax.jcr.PropertyType.PATH: dumpPath,
        javax.jcr.PropertyType.REFERENCE: dumpReference,
        }

    def dumpValue(self, value):
        self.valueDumpers[value.getType()](self, value)

    commands = {
        '?': (cmdHelp, "This help."),
        'q': (cmdQuit, "Quit this connection."),
        'Q': (cmdStop, "Stop the server and all connections."),
        'd': (cmdDump, "Dump the repository."),
        'L': (cmdLogin, "Login to the given workspace."),
        's': (cmdSave, "Save the transient work."),
        'p': (cmdPrepare, "Prepare the transaction."),
        'c': (cmdCommit, "Commit the prepared transaction."),
        'r': (cmdRollback, "Rollback the transaction."),
        'T': (cmdGetNodeType, "Get the primary type of a given uuid."),
        'S': (cmdGetNodeStates, "Get the state of the given uuids."),
        'P': (cmdGetNodeProperties, "Get some properties of a given uuid."),
        'D': (cmdGetNodeTypeDefs, "Get the CND node type definitions."),
        }

    def process(self, line):
        print 'processing', repr(line)
        if not line: # XXX
            return
        if line.lower() == 'help': # XXX
            line = '?'
        cmd, rest = line[0], line[1:]
        info = self.commands.get(cmd)
        if info is not None:
            func = info[0]
            func(self, rest)
        else:
            self.writeln("!Unknown command '%s'" % cmd)

class IO:
    """I/O manager, reads lines and passes them to a processor.
    """

    def __init__(self, key, repository):
        self.processor = Processor(self, repository)
        self.key = key
        self.channel = key.channel()
        self.rbbuf = java.nio.ByteBuffer.allocate(16384)
        self.unprocessed = [] # Unprocessed data already read
        self.towrite = [] # Pending data to write

    def close(self):
        self.processor.logout()
        self.key.cancel() # remove channel from selector
        self.channel.close() # close socket

    def doRead(self):
        """Called by server when it's possible to read.
        """
        n = self.channel.read(self.rbbuf)
        if n == -1:
            # EOF
            self.close()
            return

        # Convert buffer to string
        self.rbbuf.flip()
        s = latinDecoder.decode(self.rbbuf).toString()
        self.rbbuf.clear()
        self.unprocessed.append(s)

        # Pass all full lines to the processor
        # XXX Not memory efficient
        data = ''.join(self.unprocessed)
        while True:
            pos = data.find('\n')
            if pos == -1:
                break
            line = data[:pos]
            data = data[pos+1:]
            self.processor.process(line)
        if data:
            self.unprocessed = [data]
        else:
            self.unprocessed = []

    def doWrite(self):
        """Called by server when it's possible to write.
        """
        # XXX Not memory efficient
        data = ''.join(self.towrite)
        l = len(data)
        bbuf = latinEncoder.encode(java.nio.CharBuffer.wrap(data))
        n = self.channel.write(bbuf)
        if n != l:
            #print 'short write! %d written out of %d' % (n, l)
            self.towrite = [data[n:]]
        else:
            self.towrite = []
            # Set key not interested in writes
            self.key.interestOps(SelectionKey.OP_READ)

    def write(self, s):
        """Append data to be written.
        """
        if not s:
            return
        was_empty = not len(self.towrite)
        self.towrite.append(s)
        if was_empty:
            # Set key interested in writes
            self.key.interestOps(SelectionKey.OP_READ | SelectionKey.OP_WRITE)


class Server:
    def __init__(self, repository):
        self.repository = repository

    def acceptConnections(self, port):
        selector = java.nio.channels.Selector.open()
        self.selector = selector

        listenChannel = java.nio.channels.ServerSocketChannel.open()
        listenChannel.configureBlocking(False)
        isa = java.net.InetSocketAddress(port) # on localhost
        listenChannel.socket().bind(isa)
        listenChannel.register(selector, SelectionKey.OP_ACCEPT)

        print 'Listening...'

        while selector.select() > 0:
            iter = selector.selectedKeys().iterator()
            while iter.hasNext():
                key = iter.next()
                iter.remove()
                if key.isAcceptable():
                    channel = key.channel().accept()
                    channel.configureBlocking(False)
                    newkey = channel.register(selector, SelectionKey.OP_READ)
                    io = IO(newkey, self.repository)
                    newkey.attach(io)
                    io.write("Welcome.\n")
                elif key.isReadable():
                    key.attachment().doRead()
                elif key.isWritable():
                    key.attachment().doWrite()

    def closeIO(self):
        iter = self.selector.keys().iterator()
        while iter.hasNext():
            key = iter.next()
            io = key.attachment()
            if io is not None:
                io.close()


def run_server(repoconf, repopath, port):
    repository = TransientRepository(repoconf, repopath)
    try:
        server = Server(repository)
        server.acceptConnections(port)
    finally:
        server.closeIO()


if __name__ == '__main__':
    if len(sys.argv) != 3:
        print >>sys.stderr, "Usage: server.py <repopath> <port>"
        sys.exit(1)

    repopath = sys.argv[1]
    repoconf = repopath+'.xml'
    port = int(sys.argv[2])
    run_server(repoconf, repopath, port)
