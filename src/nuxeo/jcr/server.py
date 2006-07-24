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

DEBUG = 1

import os
import sys
from types import ListType

import java.io
import java.util
import java.net
import java.nio
import java.nio.charset
import java.nio.channels
from java.nio.channels import SelectionKey
from java.lang import IllegalArgumentException

import javax.jcr
from javax.jcr import RepositoryException # toplevel exception
from javax.jcr import ItemNotFoundException
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
     CompactNodeTypeDefReader


False, True = 0, 1

MARKER = []

NODETYPEDEFS = None

CREDENTIALS = javax.jcr.SimpleCredentials('username', 'password')

CHARSET = java.nio.charset.Charset.forName('ISO-8859-1')
latinDecoder = CHARSET.newDecoder()
latinEncoder = CHARSET.newEncoder()



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
            print "XXX %s: event %-16s path %s" % (
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
        self.continuations = []

    def write(self, s):
        self.io.write(s)

    def writeln(self, s):
        self.io.write(s+'\n')


    def cmdHelp(self, line=None):
        self.writeln("Available commands:")
        keys = self._ops.keys()
        keys.sort()
        for cmd in keys:
            func, desc = self._ops[cmd]
            self.writeln("  %s: %s" % (cmd, desc))


    def cmdLogin(self, workspaceName):
        if self.session is not None:
            return self.writeln("!Already logged in.")
        try:
            self.login(workspaceName)
        except javax.jcr.NoSuchWorkspaceException:
            return self.writeln("!No such workspace '%s'." % workspaceName)
        self.root = self.session.getRootNode()
        self.createValue = self.session.getValueFactory().createValue
        self.writeln('^'+self.root.getUUID())

    def login(self, workspaceName):
        session = self.repository.login(CREDENTIALS, workspaceName)
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
        # XXX and if it triggers, it calls rollback() without ending
        # the association so the next assocation will fail!
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
        if self.session is not None:
            self._trapXAException(self.rollback)
            self.session.logout()

    def cmdStop(self, line=None):
        raise SystemExit

    def cmdDump(self, uuid):
        if self.root is None:
            return self.writeln("!Not logged in.")
        if not uuid:
            node = self.root
        else:
            try:
                node = self.session.getNodeByUUID(uuid)
            except (ItemNotFoundException, IllegalArgumentException):
                return self.writeln("!No such uuid '%s'" % uuid)
        dumpNode(node, '', self.write)
        self.writeln('.')

    def cmdGetNodeTypeDefs(self, line):
        self.write(NODETYPEDEFS)
        self.writeln('\n.')

    def cmdGetNodeChildren(self, uuid):
        try:
            node = self.session.getNodeByUUID(uuid)
        except (ItemNotFoundException, IllegalArgumentException):
            return self.writeln("!No such uuid '%s'" % uuid)
        for subnode in node.getNodes():
            try:
                subuuid = subnode.getUUID()
            except javax.jcr.UnsupportedRepositoryOperationException:
                print "XXX Node %s is not referenceable" % subnode.getPath()
                continue
            self.writeln('%s %s' % (subnode.getUUID(), subnode.getName()))
        self.writeln('.')

    def cmdGetNodeType(self, uuid):
        try:
            node = self.session.getNodeByUUID(uuid)
        except (ItemNotFoundException, IllegalArgumentException):
            return self.writeln("!No uuid '%s'" % uuid)
        nodeType = node.getProperty('jcr:primaryType').getString()
        self.writeln('T%s' % nodeType)

    def cmdGetNodeStates(self, line):
        uuids = line.split(' ')
        # Check all UUIDs exist
        for node_uuid in uuids:
            try:
                node = self.session.getNodeByUUID(node_uuid)
            except (ItemNotFoundException, IllegalArgumentException):
                return self.writeln("!No uuid '%s'" % node_uuid)
        # Fetch all info
        for node_uuid in uuids:
            node = self.session.getNodeByUUID(node_uuid)
            # Node UUID and name
            self.writeln('U%s %s' % (node.getUUID(), node.getName()))
            # Parent
            try:
                parent_uuid = node.getParent().getUUID()
            except (ItemNotFoundException,
                    javax.jcr.UnsupportedRepositoryOperationException):
                # Parent may not exist
                # Parent may not be referenceable (rep:versionStorage)
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
                    self.writeln('M%s' % name)
                    for value in values:
                        self.dumpValue(value)
                    self.writeln('M')
                else:
                    self.writeln('P%s' % name)
                    self.dumpValue(prop.getValue())
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
        self.writeln('b%s' % value.getString())
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

    def cmdMultiple(self, line=None):
        self.commands = [] # parsed Multiple commands
        self.command = None # current command being parsed
        self.prop_name = None # current prop being parsed
        self.prop_value = None # its value
        self.prop_values = None # its values when multiple
        self.continuations.append(self.expectMultipleOne)

    def expectMultipleOne(self, line):
        # Store previous command
        if self.command is not None:
            self.commands.append(self.command)
            self.command = None
        if line == '.':
            # end multiple commands
            commands = self.commands
            self.commands = None
            return self.processCommands(commands)
        else:
            self.continuations.append(self.expectMultipleOne)

        op, rest = line[0], line[1:]
        if op == '+': # add
            puuid, node_type, token, name = rest.split(' ', 3)
            self.command = {
                'op': 'add',
                'puuid': puuid,
                'node_type': node_type,
                'token': token,
                'name': unicode(name, 'utf-8'),
                'props': {},
                }
            self.prop_name = None
            self.continuations.append(self.expectProps)
        elif op == '/': # modify
            self.command = {
                'op': 'modify',
                'uuid': rest,
                'props': {},
                }
            self.prop_name = None
            self.continuations.append(self.expectProps)
        elif op == '-': # remove
            self.command = {
                'op': 'remove',
                'uuid': rest,
                }
        elif op == '%': # reorder
            self.command = {
                'op': 'reorder',
                'uuid': rest,
                'inserts': [],
                }
            self.continuations.append(self.expectInserts)
        else:
            self.continuations = []
            return self.writeln("!Unknown multiple op '%s'" % op)

    def expectProps(self, line):
        # Store previous value
        if self.prop_name is not None:
            if self.prop_values is not None:
                v = self.prop_values
            else:
                v = self.prop_value
            self.command['props'][self.prop_name] = v
        # End props?
        if line == ',':
            return
        else:
            self.continuations.append(self.expectProps)

        op, name = line[0], line[1:]
        self.prop_name = unicode(name, 'utf-8')
        if op == 'P':
            self.prop_values = None # fill prop_value, not prop_values
            self.continuations.append(self.expectProp)
        elif op == 'M':
            self.prop_value = MARKER # nothing yet
            self.prop_values = [] # list to fill
            self.continuations.append(self.expectPropMulti)
        elif op == 'D':
            self.prop_value = None
        else:
            raise ValueError("Unknown props op '%s'" % op)

    def expectProp(self, line):
        op, rest = line[0], line[1:]
        if op == 's':
            self.io.setString(int(rest))
            self.continuations.append(self.expectString)
            return
        elif op == 'x':
            self.io.setBinary(int(rest))
            self.continuations.append(self.expectBinary)
            return
        elif op == 'b':
            value = self.createValue(rest, javax.jcr.PropertyType.BOOLEAN)
        elif op == 'l':
            value = self.createValue(rest, javax.jcr.PropertyType.LONG)
        elif op == 'f':
            value = self.createValue(rest, javax.jcr.PropertyType.DOUBLE)
        elif op == 'd':
            try:
                value = self.createValue(rest, javax.jcr.PropertyType.DATE)
            except javax.jcr.ValueFormatException:
                print 'XXX ValueFormatException on date %s' % repr(rest)
                raise
        elif op == 'r':
            value = self.createValue(rest, javax.jcr.PropertyType.REFERENCE)
        else:
            raise ValueError("Unknown op '%s'" % op)
        self._storeValue(value)

    def expectString(self, data):
        s = unicode(data, 'utf-8')
        value = self.createValue(s)
        self._storeValue(value)

    def expectBinary(self, bin):
        input = java.io.ByteArrayInputStream(bin.array(), 0, bin.limit())
        value = self.createValue(input)
        self._storeValue(value)

    def _storeValue(self, value):
        # Keep value a single or multiple
        if self.prop_values is not None:
            self.prop_values.append(value)
        else:
            self.prop_value = value

    def expectPropMulti(self, line):
        # Store previous list value
        if self.prop_value is not MARKER:
            self.prop_values.append(self.prop_value)
        # End multiprops?
        if line == 'M':
            return
        else:
            self.continuations.append(self.expectPropMulti)

        self.expectProp(line)

    def expectInserts(self, line):
        if line == '%':
            return
        else:
            self.continuations.append(self.expectInserts)
        name, before = unicode(line, 'utf-8').split('/')
        self.command['inserts'].append((name, before))

    def processCommands(self, commands):
        map = {}
        for command in commands:
            op = command['op']
            if op == 'add':
                puuid = command['puuid']
                if map.has_key(puuid):
                    puuid = map[puuid]
                try:
                    parent = self.session.getNodeByUUID(puuid)
                except (ItemNotFoundException, IllegalArgumentException):
                    return self.writeln("!No such uuid '%s'" % puuid)
                try:
                    node = parent.addNode(command['name'],
                                          command['node_type'])
                except RepositoryException, e:
                    print "XXX Cannot add '%s': %s" % (command['name'], e)
                    return self.writeln("!Cannot add '%s': %s" % (command['name'], e))
                for key, value in command['props'].items():
                    try:
                        node.setProperty(key, value)
                    except RepositoryException, e:
                        # XXX happens when a date is set to ''
                        print " XXX Ignoring setProperty '%s' to %s: %s" % (
                            key, value, e)
                map[command['token']] = node.getUUID()
            elif op == 'modify':
                uuid = command['uuid']
                if map.has_key(uuid):
                    uuid = map[uuid]
                try:
                    node = self.session.getNodeByUUID(uuid)
                except (ItemNotFoundException, IllegalArgumentException):
                    return self.writeln("!No such uuid '%s'" % uuid)
                for key, value in command['props'].items():
                    try:
                        node.setProperty(key, value)
                    except RepositoryException, e:
                        # XXX happens when a date is set to ''
                        print " XXX Ignoring setProperty '%s' to %s: %s" % (
                            key, value, e)
            elif op == 'remove':
                uuid = command['uuid']
                if map.has_key(uuid):
                    uuid = map[uuid]
                try:
                    node = self.session.getNodeByUUID(uuid)
                except (ItemNotFoundException, IllegalArgumentException):
                    return self.writeln("!No such uuid '%s'" % uuid)
                try:
                    node.remove()
                except RepositoryException, e:
                    return self.writeln("!Cannot remove node '%s': %s"
                                        % (uuid, e))
            elif op == 'reorder':
                uuid = command['uuid']
                if map.has_key(uuid):
                    uuid = map[uuid]
                try:
                    node = self.session.getNodeByUUID(uuid)
                except (ItemNotFoundException, IllegalArgumentException):
                    return self.writeln("!No such uuid '%s'" % uuid)
                for name, before in command['inserts']:
                    try:
                        node.orderBefore(name, before)
                    except RepositoryException, e:
                        return self.writeln("!Cannot reorder '%s', "
                                            "'%s' before '%s': %s" %
                                            (uuid, name, before, e))
        try:
            self.root.save()
        except RepositoryException, e:
            return self.writeln("!Cannot save: %s" % e)

        # Write token map
        for token, uuid in map.items():
            self.writeln('%s %s' % (token, uuid))

        # Done!
        self.writeln('.')

    def cmdCheckin(self, uuid):
        try:
            node = self.session.getNodeByUUID(uuid)
        except (ItemNotFoundException, IllegalArgumentException):
            return self.writeln("!No such uuid '%s'" % uuid)
        try:
            node.checkin()
        except RepositoryException, e:
            return self.writeln("!Cannot checkin: %s" % e)
        self.writeln('.')

    def cmdCheckout(self, uuid):
        try:
            node = self.session.getNodeByUUID(uuid)
        except (ItemNotFoundException, IllegalArgumentException):
            return self.writeln("!No such uuid '%s'" % uuid)
        try:
            node.checkout()
        except RepositoryException, e:
            return self.writeln("!Cannot checkout: %s" % e)
        self.writeln('.')

    _ops = {
        '?': (cmdHelp, "This help."),
        'q': (cmdQuit, "Quit this connection."),
        'Q': (cmdStop, "Stop the server and all connections."),
        'd': (cmdDump, "Dump the repository."),
        'L': (cmdLogin, "Login to the given workspace."),
        'p': (cmdPrepare, "Prepare the transaction."),
        'c': (cmdCommit, "Commit the prepared transaction."),
        'r': (cmdRollback, "Rollback the transaction."),
        'i': (cmdCheckin, "Checkin."),
        'o': (cmdCheckout, "Checkout."),
        'T': (cmdGetNodeType, "Get the primary type of a given uuid."),
        'S': (cmdGetNodeStates, "Get the state of the given uuids."),
        'P': (cmdGetNodeProperties, "Get some properties of a given uuid."),
        'D': (cmdGetNodeTypeDefs, "Get the CND node type definitions."),
        'M': (cmdMultiple, "Send multiple commands (+/=/-/%)."),
        }


    def cmdCommand(self, line):
        print "XXX processing command", repr(line)
        if not line: # XXX
            return
        if line.lower() == 'help': # XXX
            line = '?'
        cmd, rest = line[0], line[1:]
        info = self._ops.get(cmd)
        if info is not None:
            func = info[0]
            func(self, rest)
        else:
            self.writeln("!Unknown command '%s'" % cmd)

    def process(self, line):
        if self.continuations:
            continuation = self.continuations.pop()
        else:
            continuation = self.cmdCommand
        continuation(line)

class IO:
    """I/O manager, reads lines and passes them to a processor.
    """

    def __init__(self, key, repository):
        self.processor = Processor(self, repository)
        self.key = key
        self.channel = key.channel()
        self.rbbuf = java.nio.ByteBuffer.allocate(8192)
        self.bin = None # A byte buffer for binary data
        self.strlen = 0 # Length of string to return instead of readline
        self.unprocessed = [] # Unprocessed data already read
        self.towrite = [] # Pending data to write

    def close(self):
        self.processor.logout()
        self.key.cancel() # remove channel from selector
        self.channel.close() # close socket

    def setBinary(self, l):
        """Next process expects l bytes of data (followed by '\n').
        """
        self.bin = java.nio.ByteBuffer.allocate(l+1)

    def setString(self, l):
        self.strlen = l+1

    def doRead(self):
        """Called by server when it's possible to read something.
        """
        # Read binary data
        if self.bin is not None:
            n = self.channel.read(self.bin)
            if DEBUG:
                print '< (%d bytes)' % n
            if n == -1:
                # EOF
                self.close()
                return
            if not self.bin.hasRemaining():
                # Just finished a binary
                self.processBinary()
            return

        # Else read non-binary data
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
        if DEBUG:
            print '< %s' % repr(s)

        # Pass all full lines/binaries to the processor
        # XXX Not completely memory efficient
        todo = ''.join(self.unprocessed)
        while todo:
            if self.bin is None:
                if self.strlen:
                    # Want a string of given length
                    if len(todo) < self.strlen:
                        # Not enough data yet
                        break
                    pos = self.strlen - 1
                    self.strlen = 0
                else:
                    pos = todo.find('\n')
                    if pos == -1:
                        break
                line, todo = todo[:pos], todo[pos+1:] # skip '\n'
                self.processor.process(line)
            else:
                remaining = self.bin.remaining()
                data, todo = todo[:remaining], todo[remaining:]
                # Put unprocessed data into the bin byte buffer
                latinEncoder.encode(java.nio.CharBuffer.wrap(data), self.bin,
                                    True)
                if self.bin.hasRemaining():
                    break
                # Just finished a binary
                self.processBinary()

        if todo:
            self.unprocessed = [todo]
        else:
            self.unprocessed = []

    def processBinary(self):
        bin = self.bin
        self.bin = None
        limit = bin.limit()
        char = bin.get(limit-1)
        if char != 0x0a:
            raise ValueError("Bad terminator: %d" % char)
        bin.limit(limit-1)
        bin.position(0)
        self.processor.process(bin)

    def doWrite(self):
        """Called by server when it's possible to write.
        """
        # XXX Not memory efficient
        data = ''.join(self.towrite)
        l = len(data)
        try:
            bbuf = latinEncoder.encode(java.nio.CharBuffer.wrap(data))
        except:
            print 'XXX failing data %s' % repr(data)
            raise
        n = self.channel.write(bbuf)
        if DEBUG:
            print '- %s' % repr(data[:n])
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

        print "Listening."

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
                    io = key.attachment()
                    try:
                        io.doRead()
                    except (ValueError, KeyError, RepositoryException), e:
                        io.towrite = []
                        io.write("!Error: %s\n" % e)
                        try:
                            io.doWrite()
                            io.close()
                        except:
                            pass
                        print "XXX Trapped exception: %s" % e
                        #raise
                elif key.isWritable():
                    key.attachment().doWrite()

    def closeIO(self):
        iter = self.selector.keys().iterator()
        while iter.hasNext():
            key = iter.next()
            io = key.attachment()
            if io is not None:
                io.close()


def checkRepositoryInit(root):
    """Check that things are ok in the repository, after creation.
    """
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


def setupNodeTypes(repository, cndpaths):
    session = repository.login(CREDENTIALS, 'default')
    workspace = session.getWorkspace()
    ntm = workspace.getNodeTypeManager()
    nsr = workspace.getNamespaceRegistry()

    # read all cnd files into one string
    global NODETYPEDEFS
    NODETYPEDEFS = ''
    ns = []
    rest = []
    for cndpath in cndpaths:
        defs = open(cndpath).read()
        for line in defs.split('\n'):
            if line and line[0] == '<' and line[-1] == '>':
                ns.append(line)
            else:
                rest.append(line)
    NODETYPEDEFS = '\n'.join(ns) + '\n' + '\n'.join(rest)

    # parse cnd
    reader = java.io.StringReader(NODETYPEDEFS)
    try:
        cndReader = CompactNodeTypeDefReader(reader, 'ALL-CND')
    except:
        print '--'
        i = 0
        for line in NODETYPEDEFS.split('\n'):
            i += 1
            print '%4d %s' % (i, line)
        print '--'
        raise

    # register namespaces read
    nsm = cndReader.getNamespaceMapping()
    for entry in nsm.getPrefixToURIMapping().entrySet():
        prefix = entry.getKey()
        uri = entry.getValue()
        try:
            nsr.registerNamespace(prefix, uri)
        except javax.jcr.NamespaceException:
            # already registered
            pass
    # register node types
    ntr = ntm.getNodeTypeRegistry();
    ntr.registerNodeTypes(cndReader.getNodeTypeDefs())

    checkRepositoryInit(session.getRootNode())
    session.save()
    session.logout()


def run_server(repoconf, repopath, cndpath, port):
    try:
        # Remove previous nodetypes, we'll reimport them
        os.remove(repopath+'/repository/nodetypes/custom_nodetypes.xml')
    except OSError:
        pass
    repository = TransientRepository(repoconf, repopath)
    setupNodeTypes(repository, cndpath)
    try:
        server = Server(repository)
        server.acceptConnections(port)
    finally:
        server.closeIO()


if __name__ == '__main__':
    if len(sys.argv) < 4:
        print >>sys.stderr, "Usage: server.py <repopath> <port> <cndpath> <cndpath...>"
        sys.exit(1)

    repopath = sys.argv[1]
    repoconf = repopath+'.xml'
    port = int(sys.argv[2])
    cndpaths = sys.argv[3:]
    run_server(repoconf, repopath, cndpaths, port)


