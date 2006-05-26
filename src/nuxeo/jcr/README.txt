=================
Nuxeo Capsule JCR
=================

The JCR storage is an indirection to a real JCR server, based on the
Capsule framework.

Capsule JCR mount point
=======================

To make the Capsule JCR mount point visible by Zope, add the following
in your etc/zope.conf::

  %import nuxeo.jcr

  <capsule-jcr-db JCR>
    jcr-server foo.example.com:12345
    jcr-workspace-name default
    mount-point /path/in/zope:/
    jcr-controller-class-name nuxeo.jcr.protocol.JCRController
    #jcr-controller-class-name nuxeo.jcr.protocol.JCRIceController
    #jcr-slice-file /path/to/jcr.ice
    #jcr-ice-config /path/to/config
  </capsule-jcr-db>
