module jcr
{
    exception JCRRepositoryException {
        string reason;
    };

    exception AlreadyLoggedException {
        string reason;
    };

    sequence<string> StringSeq;
    struct PropertyStruct {
        string name;
        StringSeq value;
        string type;
        bool multiple;
    };

    ["java:type:java.util.ArrayList"] sequence<StringSeq> ChildrenSeq;
    ["java:type:java.util.ArrayList"] sequence<PropertyStruct> PropertiesSeq;

    struct NodeStates {
        string nodename;
        string parentuuid;
        ChildrenSeq children;
        PropertiesSeq properties;
        StringSeq deferred;
    };

    ["java:type:java.util.HashMap<String, NodeStates>"]
    dictionary<string, NodeStates> NodeStatesDict;

    interface JCRController
    {
        string login(string workspaceName) throws JCRRepositoryException;
        string getNodeTypeDefs() throws JCRRepositoryException;
        string getNodeType(string uuid) throws JCRRepositoryException;
        NodeStatesDict getNodeStates(StringSeq uuids)
            throws JCRRepositoryException;
    };

};