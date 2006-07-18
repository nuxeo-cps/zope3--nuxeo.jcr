=================
Nuxeo Capsule JCR
=================

The JCR storage is an indirection to a real JCR server, based on the
Capsule framework.

Capsule JCR database
====================

To make the Capsule JCR database visible by Zope, add the following in
your etc/zope.conf::

  %import Products.lib
  %import nuxeo.jcr

  <capsule-jcr-db foodefault>
    jcr-server foo.example.com:12345
    jcr-workspace-name default
    mount-point /path/in/zope:/;/other/path:/
  </capsule-jcr-db>

The first line ``%import Products.lib`` is needed if ``nuxeo.jcr`` is
not in the python path when Zope starts (the zope.conf ``path``
directive is executed after these imports and is therefore useless
here).
