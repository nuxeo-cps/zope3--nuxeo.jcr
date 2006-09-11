
import javax.jcr.Repository;
import javax.jcr.Session;
import javax.jcr.SimpleCredentials;
import javax.jcr.Node;
import org.apache.jackrabbit.core.TransientRepository;

public class debug2 {
    public static void main(String[] args) throws Exception {
        Repository repository = new TransientRepository();
        Session session = repository.login(
                new SimpleCredentials("username", "password".toCharArray()));
        try {
            Node root = session.getRootNode();

            Node foo = root.addNode("foo");
            foo.addMixin("mix:versionable");

            Node bar = foo.addNode("bar");
            bar.addMixin("mix:referenceable");
            System.out.println("bar:            " + bar.getUUID());

            session.save();
            foo.checkin();

            Node frozenbar = foo.getBaseVersion().getNode("jcr:frozenNode").getNode("bar");
            System.out.println("frozenbar UUID: " + frozenbar.getUUID());
            System.out.println("jcr:uuid:       " + frozenbar.getProperty("jcr:uuid").getValue().getString());
            System.out.println("jcr:frozenUuid: " + frozenbar.getProperty("jcr:frozenUuid").getValue().getString());

        } finally {
            session.logout();
        }
    }
}
