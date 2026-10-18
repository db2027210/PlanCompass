import com.resourceadaptive.hyxp.SetLocalPostgresDriver;

import java.lang.reflect.Proxy;
import java.sql.Connection;
import java.sql.Driver;
import java.sql.DriverManager;
import java.sql.DriverPropertyInfo;
import java.sql.ResultSet;
import java.sql.Statement;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Properties;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.logging.Logger;

/** Offline JDBC probe; no database or network access. */
public final class SetLocalPostgresDriverProbe {
    private static final String[] NAMES = {
        "seq_page_cost",
        "random_page_cost",
        "cpu_tuple_cost",
        "cpu_index_tuple_cost",
        "cpu_operator_cost",
        "parallel_setup_cost",
        "parallel_tuple_cost"
    };
    private static final int CONCURRENT_CONNECTIONS = 32;
    private static final int TRANSACTIONS_PER_CONNECTION = 8;
    private static final AtomicInteger LOCAL_APPLICATIONS = new AtomicInteger();

    public static void main(String[] args) throws Exception {
        DriverManager.registerDriver(new FakePostgresDriver());
        Class.forName(SetLocalPostgresDriver.class.getName());
        try (Connection tp = DriverManager.getConnection(
                    "jdbc:hyxp-setlocal:tp:jdbc:postgresql:fake")) {
            tp.setAutoCommit(false);
            try (Statement statement = tp.createStatement()) {
                statement.execute("SELECT tp_first");
            }
            tp.commit();
            try (Statement statement = tp.createStatement()) {
                statement.execute("SELECT tp_second");
            }
            tp.rollback();
        }
        try (Connection ap = DriverManager.getConnection(
                    "jdbc:hyxp-setlocal:ap:jdbc:postgresql:fake")) {
            ap.setAutoCommit(false);
            try (Statement statement = ap.createStatement()) {
                statement.execute("SELECT ap_first");
            }
            ap.rollback();
        }
        runConcurrentAuditProbe();
        int expectedTransactions = 3
                + CONCURRENT_CONNECTIONS * TRANSACTIONS_PER_CONNECTION;
        int expectedApplications = expectedTransactions * NAMES.length;
        if (LOCAL_APPLICATIONS.get() != expectedApplications) {
            throw new AssertionError(
                    "expected " + expectedApplications + " local applications, got "
                            + LOCAL_APPLICATIONS.get());
        }
        System.out.println("HYXP_SETLOCAL_JDBC_PROBE_OK");
        System.out.println(
                "HYXP_SETLOCAL_CONCURRENT_AUDIT_OK connections="
                        + CONCURRENT_CONNECTIONS
                        + " transactions="
                        + (CONCURRENT_CONNECTIONS * TRANSACTIONS_PER_CONNECTION));
    }

    private static void runConcurrentAuditProbe() throws Exception {
        CountDownLatch ready = new CountDownLatch(CONCURRENT_CONNECTIONS);
        CountDownLatch start = new CountDownLatch(1);
        ConcurrentLinkedQueue<Throwable> failures = new ConcurrentLinkedQueue<>();
        Thread[] threads = new Thread[CONCURRENT_CONNECTIONS];
        for (int index = 0; index < threads.length; index++) {
            final int worker = index;
            threads[index] = new Thread(() -> {
                ready.countDown();
                try {
                    start.await();
                    String role = worker % 2 == 0 ? "tp" : "ap";
                    try (Connection connection = DriverManager.getConnection(
                            "jdbc:hyxp-setlocal:" + role + ":jdbc:postgresql:fake")) {
                        connection.setAutoCommit(false);
                        for (int transaction = 1;
                                transaction <= TRANSACTIONS_PER_CONNECTION;
                                transaction++) {
                            try (Statement statement = connection.createStatement()) {
                                statement.execute(
                                        "SELECT concurrent_" + worker + "_" + transaction);
                            }
                            connection.commit();
                        }
                    }
                } catch (Throwable error) {
                    failures.add(error);
                }
            }, "setlocal-probe-" + index);
            threads[index].start();
        }
        ready.await();
        start.countDown();
        for (Thread thread : threads) thread.join();
        if (!failures.isEmpty()) {
            AssertionError error = new AssertionError(
                    "concurrent setlocal audit failures: " + failures.size());
            for (Throwable failure : failures) error.addSuppressed(failure);
            throw error;
        }
    }

    private static final class FakePostgresDriver implements Driver {
        @Override
        public boolean acceptsURL(String url) {
            return "jdbc:postgresql:fake".equals(url);
        }

        @Override
        public Connection connect(String url, Properties info) {
            if (!acceptsURL(url)) return null;
            State state = new State();
            return (Connection) Proxy.newProxyInstance(
                    getClass().getClassLoader(),
                    new Class<?>[] {Connection.class},
                    (proxy, method, args) -> {
                        switch (method.getName()) {
                            case "setAutoCommit":
                                state.autocommit = (Boolean) args[0];
                                return null;
                            case "getAutoCommit":
                                return state.autocommit;
                            case "createStatement":
                                return fakeStatement(state);
                            case "commit":
                            case "rollback":
                                state.values.clear();
                                return null;
                            case "close":
                                return null;
                            case "isClosed":
                                return false;
                            case "unwrap":
                                return null;
                            case "isWrapperFor":
                                return false;
                            case "toString":
                                return "FakePostgresConnection";
                            default:
                                return defaultValue(method.getReturnType());
                        }
                    });
        }

        @Override
        public DriverPropertyInfo[] getPropertyInfo(String url, Properties info) {
            return new DriverPropertyInfo[0];
        }

        @Override
        public int getMajorVersion() {
            return 1;
        }

        @Override
        public int getMinorVersion() {
            return 0;
        }

        @Override
        public boolean jdbcCompliant() {
            return false;
        }

        @Override
        public Logger getParentLogger() {
            return Logger.getAnonymousLogger();
        }
    }

    private static Statement fakeStatement(State state) {
        return (Statement) Proxy.newProxyInstance(
                SetLocalPostgresDriverProbe.class.getClassLoader(),
                new Class<?>[] {Statement.class},
                (proxy, method, args) -> {
                    String name = method.getName();
                    if (name.equals("execute")) {
                        String sql = (String) args[0];
                        if (sql.startsWith("SET LOCAL ")) {
                            String[] fields = sql.substring("SET LOCAL ".length()).split(" = ", 2);
                            state.values.put(fields[0], Double.parseDouble(fields[1]));
                            LOCAL_APPLICATIONS.incrementAndGet();
                        }
                        return true;
                    }
                    if (name.equals("executeQuery")) return fakeRows(state.values);
                    if (name.equals("close")) return null;
                    if (name.equals("isClosed")) return false;
                    return defaultValue(method.getReturnType());
                });
    }

    private static ResultSet fakeRows(LinkedHashMap<String, Double> values) {
        String[] names = values.keySet().toArray(new String[0]);
        int[] index = {-1};
        return (ResultSet) Proxy.newProxyInstance(
                SetLocalPostgresDriverProbe.class.getClassLoader(),
                new Class<?>[] {ResultSet.class},
                (proxy, method, args) -> {
                    switch (method.getName()) {
                        case "next":
                            index[0]++;
                            return index[0] < names.length;
                        case "getString":
                            return names[index[0]];
                        case "getDouble":
                            return values.get(names[index[0]]);
                        case "close":
                            return null;
                        default:
                            return defaultValue(method.getReturnType());
                    }
                });
    }

    private static Object defaultValue(Class<?> type) {
        if (!type.isPrimitive()) return null;
        if (type == boolean.class) return false;
        if (type == byte.class) return (byte) 0;
        if (type == short.class) return (short) 0;
        if (type == int.class) return 0;
        if (type == long.class) return 0L;
        if (type == float.class) return 0.0f;
        if (type == double.class) return 0.0d;
        if (type == char.class) return '\0';
        return null;
    }

    private static final class State {
        boolean autocommit = true;
        final LinkedHashMap<String, Double> values = new LinkedHashMap<>();
    }
}
