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
"""Zope/JCR protocol
"""

import errno
import socket
import sys
import traceback
import os.path
import zope.interface
from datetime import datetime

from nuxeo.jcr.interfaces import IJCRController
from nuxeo.jcr.interfaces import ProtocolError


class JCRController(object):
    """JCR Controller.

    Does synchronous communication with a JCR server using
    our hand-made protocol.

    """
    zope.interface.implements(IJCRController)

    _sock = None
    _state = 'disconnected'

    def __init__(self, db):
        # db.server is a ZConfig.datatypes.SocketConnectionAddress
        self._server = db.server
        self._unprocessed = []

    def connect(self):
        """Connect the controller to the server.
        """
        server = self._server
        sock = socket.socket(server.family, socket.SOCK_STREAM)
        while True:
            try:
                sock.connect(server.address)
            except socket.error, e:
                err = e[0]
                if err in (errno.EINTR, errno.EALREADY):
                    continue
                if err in (errno.ECONNREFUSED, errno.ECONNRESET):
                    raise ValueError("Connection refused to JCR server "
                                     "%s:%s" % server.address)
                raise
            break
        self._sock = sock
        self._state = 'connected'
        self._readline() # XXX Welcome message

    # Note: we don't bother using select and multiplexing reads with
    # writes, as the server side will be sufficiently intelligent to
    # buffer in both directions and will therefore prevent deadlocks.

    def _write(self, data):
        self._sock.sendall(data)

    def _writeline(self, data):
        self._write(data+'\n')

    # _read, _readline and _extract_line attempt to minimize copies

    # XXX could record the chunkno / pos from one call to another
    # to avoid scanning for '\n' each time

    def _read(self, n):
        if not n:
            return ''
        todo = n
        i = -1
        for i, chunk in enumerate(self._unprocessed):
            length = len(chunk)
            if length >= todo:
                return self._extract(i, todo)
            todo -= length
        while True:
            chunk = self._sock.recv(8192)
            self._unprocessed.append(chunk)
            i += 1
            length = len(chunk)
            if length >= todo:
                return self._extract(i, todo)
            todo -= length

    def _readline(self):
        i = -1
        for i, chunk in enumerate(self._unprocessed):
            pos = chunk.find('\n')
            if pos >= 0:
                return self._extract(i, pos, 1)
        while True:
            chunk = self._sock.recv(8192)
            self._unprocessed.append(chunk)
            i += 1
            pos = chunk.find('\n')
            if pos >= 0:
                return self._extract(i, pos, 1)

    def _extract(self, i, pos, toskip=0):
        # i is chunk number, pos is position in chunk
        chunks = self._unprocessed[:i]
        del self._unprocessed[:i]
        last = self._unprocessed[0]
        if pos:
            chunks.append(last[:pos])
        # fixup last
        if pos+toskip < len(last):
            self._unprocessed[0] = last[pos+toskip:]
        else:
            del self._unprocessed[0]
        return ''.join(chunks)

    def _pushback(self, line):
        self._unprocessed.insert(0, line+'\n')

    # API

    def login(self, workspaceName):
        """See IJCRController.
        """
        self._writeline('L'+workspaceName)
        line = self._readline()
        if not line.startswith('^'):
            raise ProtocolError(line)
        root_uuid = line[1:]
        return root_uuid

    def getNodeTypeDefs(self):
        """See IJCRController.
        """
        self._writeline('D')
        lines = []
        while True:
            line = self._readline()
            if line == '.':
                break
            lines.append(line)
        return '\n'.join(lines)

    def getNodeType(self, uuid):
        """See IJCRController.
        """
        self._writeline('T'+uuid)
        line = self._readline()
        if not line.startswith('T'):
            raise ProtocolError(line)
        node_type = line[1:]
        return node_type

    def getNodeStates(self, uuids):
        """See IJCRController.
        """
        self._writeline('S' + ' '.join(uuids))
        line = self._readline()
        if line.startswith('!'):
            raise ProtocolError(line)
        self._pushback(line)
        infos = {}
        while True:
            parent_uuid = None
            children = []
            properties = []
            deferred = []
            line = self._readline()
            if not line.startswith('U'):
                raise ProtocolError(line)
            node_uuid, node_name = line[1:].split(' ', 1)
            while True:
                line = self._readline()
                tag = line[:1]
                if tag == '.':
                    break
                elif tag == 'U':
                    self._pushback(line)
                    break
                elif tag == '^':
                    parent_uuid = line[1:]
                elif tag == 'N':
                    uuid, nodetype, name = line[1:].split(' ', 2)
                    children.append((name, uuid, nodetype))
                elif tag == 'P':
                    name = line[1:]
                    properties.append((name, self._getOneValue()))
                elif tag == 'M':
                    number, name = line[1:].split(' ', 1)
                    values = []
                    for i in xrange(int(number)):
                        values.append(self._getOneValue())
                    properties.append((name, values))
                elif tag == 'D':
                    name = line[1:]
                    deferred.append(name)
                else:
                    raise ProtocolError(line)
            infos[node_uuid] = (node_name, parent_uuid,
                                children, properties, deferred)
            if tag == '.':
                break
        return infos

    def _readString(self, line, decode=True):
        length = int(line)
        data = self._read(length)
        last = self._read(1)
        if last != '\n':
            raise ProtocolError("Bad terminator %r" % last)
        if decode:
            return data.decode('utf-8')
        else:
            return data

    def _readBinary(self, line):
        return self._readString(line, decode=False)

    def _readLong(self, line):
        return int(line)

    def _readFloat(self, line):
        return float(line)

    def _readDate(self, line):
        # D2006-04-07T18:00:42.754Z
        # D2006-04-07T18:00:42.754+02:00
        return datetime(2006, 01, 01) # XXX

    def _readBoolean(self, line):
        if line == '0':
            return False
        elif line == '1':
            return True
        else:
            raise ProtocolError(line)

    def _readName(self, line):
        return line

    def _readPath(self, line):
        return line

    def _readReference(self, line):
        return line

    _valueReaders = {
        's': _readString,
        'x': _readBinary,
        'l': _readLong,
        'f': _readFloat,
        'd': _readDate,
        'b': _readBoolean,
        'n': _readName,
        'p': _readPath,
        'r': _readReference,
        }

    def _getOneValue(self):
        """Read one value.
        """
        line = self._readline()
        if not line:
            raise ProtocolError(line)
        reader = self._valueReaders.get(line[0])
        if reader is None:
            raise ProtocolError(line)
        return reader(self, line[1:])

    def sendCommands(self, commands):
        """See IJCRController.
        """
        raise NotImplementedError

    def getNodeProperties(self, uuid, names):
        """See IJCRController.
        """
        raise NotImplementedError('Unused')

    def getPendingEvents(self):
        """See IJCRController.
        """
        raise NotImplementedError('Unused')

    def commit(self):
        """See IJCRController.
        """
        raise NotImplementedError

    def abort(self):
        """See IJCRController.
        """
        raise NotImplementedError


class JCRIceController(object):
    """JCR Controller.

    Does synchronous communication with a JCR server using Ice protocol.
    """
    zope.interface.implements(IJCRController)

    def __init__(self, db):
        import Ice
        self.ice_config = db.ice_config
        slice_file = db.slice_file
        Ice.loadSlice(slice_file)
        # Here we suggest that file name without extension is the name
        # of module.
        mname = os.path.splitext(os.path.split(slice_file)[1])[0]
        globals()[mname] = __import__(mname, globals(), locals())

    def connect(self):
        """Connect the controller to the server.
        """
        import Ice
        try:
            props = Ice.createProperties()
            props.load(self.ice_config)
            communicator = Ice.initializeWithProperties(sys.argv, props)
            properties = communicator.getProperties()
            refprop = 'JCR.JCRController'
            string_proxy = properties.getProperty(refprop)
            proxy = communicator.stringToProxy(string_proxy)
            self.server = jcr.JCRControllerPrx.checkedCast(proxy)
        except:
            traceback.print_exc()

    # API

    def login(self, workspaceName):
        """See IJCRController.
        """
        return self.server.login(workspaceName)

    def getNodeTypeDefs(self):
        """See IJCRController.
        """
        return self.server.getNodeTypeDefs()

    def getNodeType(self, uuid):
        """See IJCRController.
        """
        return self.server.getNodeType(uuid)

    def getNodeStates(self, uuids):
        """See IJCRController.
        """
        info = {}
        states = self.server.getNodeStates(uuids)

        for uuid, nstate in states.items():
            properties = []
            for prop in nstate.properties:
                ptype = prop.type
                if prop.multiple:
                    values = [self._getOneValue(v, ptype) for v in prop.value]
                    properties.append((prop.name, values))
                else:
                    value = self._getOneValue(prop.value[0], ptype)
                    properties.append((prop.name, value))

            parent_uuid = nstate.parentuuid
            if parent_uuid == '':
                parent_uuid = None
            info[uuid] = (nstate.nodename, parent_uuid, nstate.children,
                           properties, nstate.deferred)

        return info

    # Utility methods

    def _toString(self, value, decode=True):
        if decode:
            return value.decode('utf-8')
        else:
            return value

    def _toBinary(self, value):
        #return self._toString(value, decode=False)
        return value

    def _toLong(self, value):
        return int(value)

    def _toFloat(self, value):
        return float(value)

    def _toDate(self, value):
        # D2006-04-07T18:00:42.754Z
        # D2006-04-07T18:00:42.754+02:00
        return datetime(2006, 01, 01) # XXX

    def _toBoolean(self, value):
        if value == 'false':
            return False
        elif value == 'true':
            return True

    def _toName(self, value):
        return value

    def _toPath(self, value):
        return value

    def _toReference(self, value):
        return value

    _valueConverters = {
        'string': _toString,
        'binary': _toBinary,
        'long': _toLong,
        'float': _toFloat,
        'date': _toDate,
        'boolean': _toBoolean,
        'name': _toName,
        'path': _toPath,
        'reference': _toReference,
        }

    def _getOneValue(self, value, type):
        """Read one value.
        """
        converter = self._valueConverters.get(type)
        return converter(self, value)

    def sendCommands(self, commands):
        """See IJCRController.
        """
        raise NotImplementedError

    def getNodeProperties(self, uuid, names):
        """See IJCRController.
        """
        raise NotImplementedError('Unused')

    def getPendingEvents(self):
        """See IJCRController.
        """
        raise NotImplementedError('Unused')

    def commit(self):
        """See IJCRController.
        """
        raise NotImplementedError

    def abort(self):
        """See IJCRController.
        """
        raise NotImplementedError
