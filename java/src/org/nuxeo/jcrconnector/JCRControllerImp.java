package org.nuxeo.jcrconnector;

import java.io.FileReader;
import java.io.IOException;
import java.io.StringWriter;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import javax.jcr.ItemNotFoundException;
import javax.jcr.NamespaceException;
import javax.jcr.NamespaceRegistry;
import javax.jcr.Node;
import javax.jcr.NodeIterator;
import javax.jcr.Property;
import javax.jcr.PropertyIterator;
import javax.jcr.PropertyType;
import javax.jcr.Repository;
import javax.jcr.RepositoryException;
import javax.jcr.Session;
import javax.jcr.SimpleCredentials;
import javax.jcr.UnsupportedRepositoryOperationException;
import javax.jcr.Value;
import javax.jcr.Workspace;
import javax.jcr.nodetype.NoSuchNodeTypeException;

import org.apache.jackrabbit.core.SessionImpl;
import org.apache.jackrabbit.core.nodetype.NodeTypeDef;
import org.apache.jackrabbit.core.nodetype.NodeTypeManagerImpl;
import org.apache.jackrabbit.core.nodetype.NodeTypeRegistry;
import org.apache.jackrabbit.core.nodetype.compact.CompactNodeTypeDefReader;
import org.apache.jackrabbit.core.nodetype.compact.CompactNodeTypeDefWriter;
import org.apache.jackrabbit.name.NamespaceResolver;
import org.apache.jackrabbit.name.QName;
import org.apache.jackrabbit.util.name.NamespaceMapping;

import Ice.Current;
import jcr.JCRRepositoryException;
import jcr.NodeStates;
import jcr.PropertyStruct;
import jcr._JCRControllerDisp;

public class JCRControllerImp extends _JCRControllerDisp {

    private Repository repository;
    private Session session;
    private String cndFileName;
    private Node root;

    private final static SimpleCredentials credentials = new SimpleCredentials("username", "password".toCharArray());

    public JCRControllerImp(Repository repository, String cndFileName) {
        this.repository = repository;
        this.cndFileName = cndFileName;
    }

    public String login(String workspaceName, Current current) throws JCRRepositoryException {
        try {
            session = repository.login(credentials, workspaceName);
            root = session.getRootNode();
            checkRepositoryInit();
            return session.getRootNode().getUUID();
        } catch (RepositoryException e) {
            throw new JCRRepositoryException(e.toString());
        } catch (Exception e) {
            throw new JCRRepositoryException(e.toString());
        }
    }

    public String getNodeTypeDefs(Current current) throws JCRRepositoryException {
        try {
            Workspace workspace = session.getWorkspace();
            NodeTypeRegistry ntr = ((NodeTypeManagerImpl)workspace.getNodeTypeManager()).getNodeTypeRegistry();
            NamespaceResolver nsr = ((SessionImpl)session).getNamespaceResolver();

            List<NodeTypeDef> al = new ArrayList<NodeTypeDef>();
            for (QName qname : ntr.getRegisteredNodeTypes())
                al.add(ntr.getNodeTypeDef(qname));

            StringWriter sw = new StringWriter();

            new CompactNodeTypeDefWriter(al, nsr, sw).write();
            return sw.toString();
        } catch (RepositoryException e) {
            throw new JCRRepositoryException(e.toString());
        } catch (IOException e) {
            throw new JCRRepositoryException(e.toString());
        }
    }

    public String getNodeType(String uuid, Current current) throws JCRRepositoryException {
        try {
            return session.getNodeByUUID(uuid).getProperty("jcr:primaryType").getString();
        } catch (RepositoryException e) {
            throw new JCRRepositoryException(e.toString());
        }
    }

    public HashMap<String, NodeStates> getNodeStates(String[] uuids, Current current) throws JCRRepositoryException {
        HashMap<String, NodeStates> states = new HashMap<String, NodeStates>();
        try {
            // Check all uuids exist
            for (String uuid : uuids)
                session.getNodeByUUID(uuid);

            for (String uuid : uuids) {
                Node node = session.getNodeByUUID(uuid);
                String nodeUUID = node.getUUID();
                String nodeName = node.getName();
                String parentUUID = "";
                try {
                    parentUUID = node.getParent().getUUID();
                } catch (ItemNotFoundException e) {
                    // Do nothing
                }

                ArrayList<String[]> children = new ArrayList<String[]>();
                ArrayList<String> al = null;
                NodeIterator niter = node.getNodes();
                while (niter.hasNext()) {
                    Node subnode = niter.nextNode();
                    al = new ArrayList<String>();
                    al.add(subnode.getName());
                    try {
                        al.add(subnode.getUUID());
                    } catch (UnsupportedRepositoryOperationException e) {
                        continue;
                    }
                    al.add(subnode.getProperty("jcr:primaryType").getString());
                    children.add(al.toArray(new String[al.size()]));
                }

                ArrayList<PropertyStruct> properties = new ArrayList<PropertyStruct>();
                PropertyIterator piter = node.getProperties();
                while (piter.hasNext()) {
                    Property prop = piter.nextProperty();
                    String propName = prop.getName();
                    boolean multiple = prop.getDefinition().isMultiple();

                    ArrayList<String> values = new ArrayList<String>();
                    if (multiple) {
                        for (Value value : prop.getValues()) {
                            values.add(value.getString());
                        }
                    } else {
                        values.add(prop.getValue().getString());
                    }
                    String[] value = values.toArray(new String[values.size()]);

                    String type = null;
                    switch (prop.getType()) {
                    case PropertyType.STRING:
                        type = "string";
                        break;
                    case PropertyType.BINARY:
                        type = "binary";
                        break;
                    case PropertyType.LONG:
                        type = "long";
                        break;
                    case PropertyType.DOUBLE:
                        type = "double";
                        break;
                    case PropertyType.BOOLEAN:
                        type = "boolean";
                        break;
                    case PropertyType.DATE:
                        type = "date";
                        break;
                    case PropertyType.NAME:
                        type = "name";
                        break;
                    case PropertyType.PATH:
                        type = "path";
                        break;
                    case PropertyType.REFERENCE:
                        type = "reference";
                        break;
                    default:
                        break;
                    }
                    properties.add(new PropertyStruct(propName, value, type, multiple));
                }
                NodeStates nodeStates = new NodeStates(nodeName, parentUUID, children, properties, new String[] {});
                states.put(nodeUUID, nodeStates);
            }

            return states;
        } catch (RepositoryException e) {
            throw new JCRRepositoryException(e.toString());
        }
    }

    private void checkRepositoryInit() throws Exception {
        checkNodeTypeDefs();
        if (!root.isNodeType("mix:referenceable"))
            root.addMixin("mix:referenceable");
        Node node = null;
        if (!root.hasNode("toto")) {
            node = root.addNode("toto", "nt:unstructured");
            node.addMixin("mix:versionable");
            root.save();
            node.checkin();
            node.checkout();
            node.setProperty("foo", "hello bob");
            root.save();
            node.checkin();
        }
        node = root.getNode("toto");
        if (!node.hasProperty("bool")) {
            node.checkout();
            node.setProperty("bool", "true", PropertyType.BOOLEAN);
            root.save();
            node.checkin();
        }
    }

	@SuppressWarnings("unchecked")
	private void checkNodeTypeDefs() throws Exception {
        Workspace ws = session.getWorkspace();
        NodeTypeManagerImpl ntm = (NodeTypeManagerImpl)ws.getNodeTypeManager();
        try {
            ntm.getNodeType("ecmnt:document");
            return;
        } catch (NoSuchNodeTypeException e) {
        	// fall through
        }
        // read cnd
        FileReader fileReader = new FileReader(cndFileName);
        CompactNodeTypeDefReader cndReader = new CompactNodeTypeDefReader(fileReader, cndFileName);
        // register namespaces
        NamespaceRegistry nsr = ws.getNamespaceRegistry();
        NamespaceMapping nsm = cndReader.getNamespaceMapping();
        Map<String, String> map = (Map<String, String>)nsm.getPrefixToURIMapping();
        for (Map.Entry<String, String> entry : map.entrySet()) {
            String prefix = entry.getKey();
            String uri = entry.getValue();
            try {
                nsr.registerNamespace(prefix, uri);
            } catch (NamespaceException e) {
            	// ignore -- already defined or something like that
            }
        }
        // register node types
        NodeTypeRegistry ntr = ntm.getNodeTypeRegistry();
        ntr.registerNodeTypes(cndReader.getNodeTypeDefs());
    }
}
