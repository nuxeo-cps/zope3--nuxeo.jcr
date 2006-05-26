package org.nuxeo.jcrconnector;

import javax.jcr.Repository;
import org.apache.jackrabbit.core.TransientRepository;
import Ice.Application;

public class Server extends Application {

    public int run(String[] args) {
        if (args.length < 2) {
            System.err.println("Usage: Server <repopath> <cndpath> --Ice.Config=/path/to/config");
            communicator().shutdown();
            System.exit(1);
        }

        Ice.ObjectAdapter adapter = communicator().createObjectAdapter("JCR");

        String repoPath = args[0];
        String repoConf = repoPath + ".xml";
        String cndFileName = args[1];

        Repository repository = null;
        try {
            repository = new TransientRepository(repoConf, repoPath);
        } catch (Exception e) {
            e.printStackTrace();
            communicator().shutdown();
            System.exit(1);
        }

        adapter.add(new JCRControllerImp(repository, cndFileName), Ice.Util.stringToIdentity("jcrcontroller"));
        adapter.activate();
        communicator().waitForShutdown();
        return 0;
    }

    /**
     * @param args
     */
    public static void main(String[] args) {
        Server app = new Server();
        int status = app.main("Server", args);
        System.exit(status);
    }

}
