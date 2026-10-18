package resourceadaptive.tpcc;

import java.io.IOException;
import java.lang.reflect.InvocationHandler;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.channels.FileLock;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.security.MessageDigest;
import java.sql.Connection;
import java.sql.Driver;
import java.sql.DriverManager;
import java.sql.DriverPropertyInfo;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Properties;
import java.util.UUID;
import java.util.logging.Logger;

public final class SessionReadbackDriver implements Driver {
    private static final String PREFIX = "jdbc:ra-postgresql:";
    private static final Object AUDIT_LOCK = new Object();
    private static final String[] PARAMETERS = {
        "seq_page_cost",
        "random_page_cost",
        "cpu_tuple_cost",
        "cpu_index_tuple_cost",
        "cpu_operator_cost",
        "parallel_setup_cost",
        "parallel_tuple_cost"
    };

    static {
        try {
            DriverManager.registerDriver(new SessionReadbackDriver());
        } catch (SQLException error) {
            throw new ExceptionInInitializerError(error);
        }
    }

    @Override
    public Connection connect(String url, Properties info) throws SQLException {
        if (!acceptsURL(url)) {
            return null;
        }
        Path readbackPath = Path.of(requiredEnvironment("RA_SESSION_READBACK_PATH"));
        String clientKind = requiredEnvironment("RA_SESSION_CLIENT_KIND");
        String calibrationId = requiredEnvironment("RA_SESSION_CALIBRATION_ID");
        Map<String, String> params = parametersFromEnvironment();
        String delegateUrl = "jdbc:postgresql:" + url.substring(PREFIX.length());
        Connection delegate = DriverManager.getConnection(delegateUrl, info);
        try {
            return wrapConnection(delegate, readbackPath, clientKind, calibrationId, params);
        } catch (RuntimeException error) {
            try {
                delegate.close();
            } catch (SQLException ignored) {
                // Preserve the fail-closed wrapper error.
            }
            throw new SQLException("transaction-local JDBC wrapper initialization failed", error);
        }
    }

    public static Connection wrapConnection(
        Connection delegate,
        Path auditPath,
        String clientKind,
        String calibrationId,
        Map<String, String> params
    ) {
        validateParameters(params);
        TransactionConnectionHandler handler = new TransactionConnectionHandler(
            delegate,
            auditPath,
            clientKind,
            calibrationId,
            new LinkedHashMap<>(params)
        );
        Connection proxy = (Connection) Proxy.newProxyInstance(
            SessionReadbackDriver.class.getClassLoader(),
            new Class<?>[] {Connection.class},
            handler
        );
        handler.setProxy(proxy);
        return proxy;
    }

    private static String requiredEnvironment(String name) throws SQLException {
        String value = System.getenv(name);
        if (value == null || value.isBlank()) {
            throw new SQLException("required environment variable is missing: " + name);
        }
        return value;
    }

    private static Map<String, String> parametersFromEnvironment() throws SQLException {
        Map<String, String> params = new LinkedHashMap<>();
        for (String parameter : PARAMETERS) {
            String environmentName = "RA_TPCC_PARAM_" + parameter.toUpperCase();
            params.put(parameter, requiredEnvironment(environmentName));
        }
        return params;
    }

    private static void validateParameters(Map<String, String> params) {
        if (params.size() != PARAMETERS.length) {
            throw new IllegalArgumentException("exactly seven transaction-local parameters are required");
        }
        for (String parameter : PARAMETERS) {
            String value = params.get(parameter);
            if (value == null || !value.matches("(?:0|[1-9][0-9]*)(?:\\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")) {
                throw new IllegalArgumentException("invalid numeric value for " + parameter);
            }
        }
    }

    private static final class TransactionConnectionHandler implements InvocationHandler {
        private final Connection delegate;
        private final Path auditPath;
        private final String clientKind;
        private final String calibrationId;
        private final Map<String, String> params;
        private final String connectionId = UUID.randomUUID().toString();
        private boolean transactionPrepared;
        private String transactionId;
        private long transactionOrdinal;
        private Connection proxy;

        private TransactionConnectionHandler(
            Connection delegate,
            Path auditPath,
            String clientKind,
            String calibrationId,
            Map<String, String> params
        ) {
            this.delegate = delegate;
            this.auditPath = auditPath;
            this.clientKind = clientKind;
            this.calibrationId = calibrationId;
            this.params = params;
        }

        private void setProxy(Connection proxy) {
            this.proxy = proxy;
        }

        @Override
        public synchronized Object invoke(Object proxy, Method method, Object[] args) throws Throwable {
            String name = method.getName();
            if (name.equals("equals")) {
                return proxy == args[0];
            }
            if (name.equals("hashCode")) {
                return System.identityHashCode(proxy);
            }
            if (name.equals("toString")) {
                return "TransactionLocalConnection[" + connectionId + "]";
            }
            if (name.equals("unwrap") && args != null && args.length == 1) {
                Class<?> type = (Class<?>) args[0];
                if (type.isInstance(proxy)) {
                    return proxy;
                }
                throw new SQLException("unwrapping the transaction-local connection is forbidden");
            }
            if (name.equals("isWrapperFor") && args != null && args.length == 1) {
                return ((Class<?>) args[0]).isInstance(proxy);
            }
            if (name.equals("createStatement")) {
                Statement statement = (Statement) invokeDelegate(method, args);
                return wrapStatement(statement, null);
            }
            if (name.equals("prepareStatement")) {
                PreparedStatement statement = (PreparedStatement) invokeDelegate(method, args);
                return wrapStatement(statement, String.valueOf(args[0]));
            }
            if (name.equals("prepareCall")) {
                throw new SQLException("CallableStatement is unsupported by the transaction-local wrapper");
            }
            if (name.equals("commit")) {
                Object result = invokeDelegate(method, args);
                resetTransaction();
                return result;
            }
            if (name.equals("rollback") && (args == null || args.length == 0)) {
                Object result = invokeDelegate(method, args);
                resetTransaction();
                return result;
            }
            if (name.equals("setAutoCommit")) {
                boolean enabled = (Boolean) args[0];
                Object result = invokeDelegate(method, args);
                if (enabled) {
                    resetTransaction();
                }
                return result;
            }
            if (name.equals("close")) {
                resetTransaction();
            }
            return invokeDelegate(method, args);
        }

        private Object wrapStatement(Statement statement, String preparedSql) {
            Class<?> api = preparedSql == null ? Statement.class : PreparedStatement.class;
            InvocationHandler handler = (proxy, method, args) -> {
                String name = method.getName();
                if (name.equals("equals")) {
                    return proxy == args[0];
                }
                if (name.equals("hashCode")) {
                    return System.identityHashCode(proxy);
                }
                if (name.equals("toString")) {
                    return "TransactionLocal" + api.getSimpleName();
                }
                if (name.equals("getConnection")) {
                    return this.proxy;
                }
                if (name.equals("unwrap")) {
                    throw new SQLException("unwrapping a transaction-local statement is forbidden");
                }
                if (name.equals("isWrapperFor")) {
                    return false;
                }
                if (isBusinessExecution(name)) {
                    String sql = preparedSql;
                    if (sql == null && args != null && args.length > 0 && args[0] instanceof String) {
                        sql = (String) args[0];
                    }
                    beforeBusinessSql(sql == null ? "<prepared-batch>" : sql);
                }
                try {
                    return method.invoke(statement, args);
                } catch (InvocationTargetException error) {
                    throw error.getCause();
                }
            };
            return Proxy.newProxyInstance(
                SessionReadbackDriver.class.getClassLoader(),
                new Class<?>[] {api},
                handler
            );
        }

        private static boolean isBusinessExecution(String methodName) {
            return methodName.equals("execute")
                || methodName.equals("executeQuery")
                || methodName.equals("executeUpdate")
                || methodName.equals("executeLargeUpdate")
                || methodName.equals("executeBatch")
                || methodName.equals("executeLargeBatch");
        }

        private synchronized void beforeBusinessSql(String sql) throws SQLException {
            if (delegate.getAutoCommit()) {
                throw new SQLException(
                    "transaction-local candidate SQL is forbidden while autoCommit=true"
                );
            }
            boolean firstBusinessSql = false;
            if (!transactionPrepared) {
                transactionOrdinal++;
                transactionId = connectionId + ":" + transactionOrdinal;
                applySetLocalAndReadback();
                transactionPrepared = true;
                firstBusinessSql = true;
            }
            if (firstBusinessSql) {
                try {
                    appendAudit(
                        auditPath,
                        auditRecord(
                            "tpcc_transaction_business_sql_dispatch",
                            2,
                            ",\"sql_sha256\":\"" + sha256(sql) + "\""
                        )
                    );
                } catch (IOException error) {
                    throw new SQLException("failed to persist first-business audit", error);
                }
            }
        }

        private void applySetLocalAndReadback() throws SQLException {
            try (Statement statement = delegate.createStatement()) {
                for (String parameter : PARAMETERS) {
                    statement.execute("SET LOCAL " + parameter + " = " + params.get(parameter));
                }
                StringBuilder sql = new StringBuilder(
                    "SELECT current_setting('application_name')"
                );
                for (String parameter : PARAMETERS) {
                    sql.append(", current_setting('").append(parameter).append("')");
                }
                try (ResultSet result = statement.executeQuery(sql.toString())) {
                    if (!result.next()) {
                        throw new SQLException("current_setting readback returned no row");
                    }
                    String applicationName = result.getString(1);
                    Map<String, String> settings = new LinkedHashMap<>();
                    for (int index = 0; index < PARAMETERS.length; index++) {
                        settings.put(PARAMETERS[index], result.getString(index + 2));
                    }
                    if (!numericallyEqual(settings, params)) {
                        throw new SQLException(
                            "transaction-local current_setting readback mismatch: " + settings
                        );
                    }
                    try {
                        appendAudit(
                            auditPath,
                            auditRecord(
                                "tpcc_transaction_parameter_readback",
                                1,
                                ",\"readback_phase\":\"after_set_local_before_business_sql\""
                                    + ",\"application_name\":\"" + escape(applicationName) + "\""
                                    + ",\"settings\":" + settingsJson(settings)
                            )
                        );
                    } catch (IOException error) {
                        throw new SQLException("failed to persist transaction readback", error);
                    }
                }
            }
        }

        private String auditRecord(String event, int sequence, String detail) {
            return "{\"schema_version\":1"
                + ",\"event\":\"" + event + "\""
                + ",\"sequence\":" + sequence
                + ",\"connection_id\":\"" + escape(connectionId) + "\""
                + ",\"transaction_id\":\"" + escape(transactionId) + "\""
                + ",\"transaction_ordinal\":" + transactionOrdinal
                + ",\"client_kind\":\"" + escape(clientKind) + "\""
                + ",\"calibration_id\":\"" + escape(calibrationId) + "\""
                + ",\"captured_at\":\"" + escape(Instant.now().toString()) + "\""
                + detail
                + "}\n";
        }

        private void resetTransaction() {
            transactionPrepared = false;
            transactionId = null;
        }

        private Object invokeDelegate(Method method, Object[] args) throws Throwable {
            try {
                return method.invoke(delegate, args);
            } catch (InvocationTargetException error) {
                throw error.getCause();
            }
        }
    }

    private static String settingsJson(Map<String, String> settings) {
        StringBuilder json = new StringBuilder("{");
        boolean first = true;
        for (String parameter : PARAMETERS) {
            if (!first) {
                json.append(',');
            }
            first = false;
            json.append('"').append(parameter).append("\":\"");
            json.append(escape(settings.get(parameter))).append('"');
        }
        return json.append('}').toString();
    }

    private static boolean numericallyEqual(
        Map<String, String> actual,
        Map<String, String> expected
    ) {
        for (String parameter : PARAMETERS) {
            try {
                double actualValue = Double.parseDouble(actual.get(parameter));
                double expectedValue = Double.parseDouble(expected.get(parameter));
                if (!Double.isFinite(actualValue)
                    || !Double.isFinite(expectedValue)
                    || Double.compare(actualValue, expectedValue) != 0) {
                    return false;
                }
            } catch (NullPointerException | NumberFormatException error) {
                return false;
            }
        }
        return true;
    }

    private static void appendAudit(Path path, String record) throws IOException {
        byte[] bytes = record.getBytes(StandardCharsets.UTF_8);
        Path parent = path.toAbsolutePath().getParent();
        if (parent == null || !java.nio.file.Files.isDirectory(parent)) {
            throw new IOException("audit parent directory does not exist: " + parent);
        }
        synchronized (AUDIT_LOCK) {
            try (FileChannel channel = FileChannel.open(
                     path,
                     StandardOpenOption.CREATE,
                     StandardOpenOption.WRITE,
                     StandardOpenOption.APPEND
                 );
                 FileLock ignored = channel.lock()) {
                ByteBuffer buffer = ByteBuffer.wrap(bytes);
                while (buffer.hasRemaining()) {
                    channel.write(buffer);
                }
                channel.force(true);
            }
        }
    }

    private static String sha256(String value) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(
                value.getBytes(StandardCharsets.UTF_8)
            );
            StringBuilder result = new StringBuilder();
            for (byte item : digest) {
                result.append(String.format("%02x", item));
            }
            return result.toString();
        } catch (java.security.NoSuchAlgorithmException error) {
            throw new IllegalStateException(error);
        }
    }

    private static String escape(String value) {
        return value
            .replace("\\", "\\\\")
            .replace("\"", "\\\"")
            .replace("\n", "\\n")
            .replace("\r", "\\r");
    }

    @Override
    public boolean acceptsURL(String url) {
        return url != null && url.startsWith(PREFIX);
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
        return Logger.getLogger("resourceadaptive.tpcc");
    }
}
