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


class ProtocolError(ValueError):
    pass


class IJCRController(Interface):
    """Commands between Zope and the JCR bridge.

    All commands are synchronous.

    The commands may also return JCR events, if some have been sent.
    They are accumulated and can be read by ``getPendingEvents()``.
    """

    def connect():
        """Connect the controller to the server.
        """

    def login(workspaceName):
        """Login to a given workspace.

        This is the first command sent.
        Returns the root node UUID.
        """

    def getNodeTypeDefs():
        """Get the schemas of the node type definitions.

        Returns a string containing a set of CND declarations.
        System types may be omitted.
        """

    def getNodeType(uuid):
        """Get the type of a node.
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
        - 'add', parent_uuid, name, node_type, props_mapping, token
        - 'modify', uuid, props_mapping
        - 'remove', uuid XXX
        - 'order' XXX

        Returns a mapping of token -> uuid, which gives the new UUIDs
        for created nodes.
        """

    def getPendingEvents():
        """Get pending events.

        The pending events are sent asynchronously by the server and
        accumulated until read by this method.
        """
