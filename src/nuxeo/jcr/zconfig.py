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
"""ZConfig datatypes.
"""

from ZODB.ActivityMonitor import ActivityMonitor
from Zope2.Startup.datatypes import ZopeDatabase
from nuxeo.jcr.db import DB

class JCRDatabaseFactory(ZopeDatabase):
    """JCR Database factory.
    """

    def open(self, database_name, databases):
        db = self.createDB(database_name, databases)
        db.setActivityMonitor(ActivityMonitor())
        return db

    def createDB(self, database_name, databases):
        config = self.config
        #config.database_name = database_name
        config.container_class = 'OFS.Folder.Folder' # XXX
        return DB(
            database_name=database_name,
            databases=databases,
            cache_size=config.cache_size,
            pool_size=config.pool_size,
            server=config.jcr_server,
            workspace_name=config.jcr_workspace_name,
            )
