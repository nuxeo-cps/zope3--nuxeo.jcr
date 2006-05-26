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
"""Capsule JCR interfaces.
"""

from zope.interface import Interface


class INonPersistent(Interface):
    """Marker interface for objects deriving from Persistent but
    that are not actually written to persistent storage.

    These objects (like ListProperty) are there to have regular
    high-level APIs and adapt them to the low-level storage APIs.
    """


class ProtocolError(ValueError):
    pass


class IJCRController(Interface):
    """Commands between Zope and the JCR bridge.

    All commands are synchronous.

    The commands may also return JCR events, if some have been sent.
    They are accumulated and can be read by ``getPendingEvents()``.
    """

    def login(workspaceName):
        """Login to a given workspace.

        This is the first command sent.
        It returns the root node UUID.
        """

    def getNodeTypeDefs():
        """Get the schemas of the node type definitions.

        A string container a set of CND declarations is returned. System
        types may be omitted.
        """

    def getNodeType(uuid):
        """Get the type of a node.
        """

    def getNodeTypeAndPath(uuid):
        """Get the type of a node and its JCR path.
        """

    def getNodeStates(uuids):
        """Get the state of several nodes.

        Additional node states may be returned, to improve network
        transfers.

        Returns a mapping of UUID to a tuple (`name`, `parent_uuid`,
        `children`, `properties`, `deferred`).

        - `name` is the name of the node,

        - `parent_uuid` is the UUID of the node's parent, or None if
          it's the root,

        - `children` is a sequence of tuples representing children
          nodes, usually (`name`, `uuid`, `type`), but for a child with
          same-name siblings, (`name`, [`uuid`s], `type`),

        - `properties` is a sequence of (`name`, `value`),

        - `deferred` is a sequence of `name` of the remaining deferred
          properties.

        An error is returned if there's no such UUID.
        """

    def getNodeProperties(uuid, names):
        """Get the value of selected properties.

        Returns a mapping of property name to value.

        An error is returned if the UUID doesn't exist or if one of the
        names doesn't exist as a property.
        """

    def sendCommands(commands):
        """Send a sequence of modification commands to the JCR.

        `commands` is an iterable returning tuples of the form:
        - ADD, token, path, node_type, props_mapping
        - MODIFY, uuid, props_mapping
        - REMOVE, uuid

        Returns a mapping of token -> uuid, which gives the new UUIDs
        for created nodes.
        """

    def setNodeState(uuid, properties):
        """Set the state of a node.

        - `properties` is a sequence of (`name`, `value`)
        """

    def addNode(uuid, name, node_type):
        """Add a node.

        A node `name` of type `node_type` is added in the node of the
        given UUID.

        Returns the uuid of the new node.
        """

    def removeNode(uuid, name):
        """Remove nodes.

        Remove all children of the given UUID called `name` (there may
        be multiple ones in case of same-name siblings).
        """

    def getPendingEvents():
        """Get pending events.

        The pending events are sent asynchronously by the server and
        accumulated until read by this method.
        """
