package com.resourceadaptive.hyxp;

import java.io.IOException;
import java.lang.reflect.InvocationHandler;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.channels.FileLock;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.sql.CallableStatement;
import java.sql.Connection;
import java.sql.Driver;
import java.sql.DriverManager;
import java.sql.DriverPropertyInfo;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Properties;
import java.util.concurrent.atomic.AtomicLong;
import java.util.logging.Logger;

/** PostgreSQL JDBC proxy enforcing seven transaction-local planner settings. */
public final class SetLocalPostgresDriver implements Driver {
    private static final String PREFIX = "jdbc:hyxp-setlocal:";
    private static final String[] NAMES = {
        "seq_page_cost",
        "random_page_cost",
        "cpu_tuple_cost",
        "cpu_index_tuple_cost",
        "cpu_operator_cost",
        "parallel_setup_cost",
        "parallel_tuple_cost"
    };
    private static final AtomicLong CONNECTIONS = new AtomicLong();
    private static final Object AUDIT_WRITE_LOCK = new Object();

    static {
        try {
            DriverManager.registerDriver(new SetLocalPostgresDriver());
        } catch (SQLException error) {
            throw new ExceptionInInitializerError(error);
        }
    }

    @Override
    public boolean acceptsURL(String url) {
        return url != null && url.startsWith(PREFIX);
    }

    @Override
    public Connection connect(String url, Properties info) throws SQLException {
        if (!acceptsURL(url)) return null;
        String remainder = url.substring(PREFIX.length());
        int delimiter = remainder.indexOf(':');
        if (delimiter <= 0) throw new SQLException("setlocal URL lacks connection role");
        String role = remainder.substring(0, delimiter);
        if (!role.equals("tp") && !role.equals("ap")) {
            throw new SQLException("setlocal connection role must be tp or ap");
        }
        String delegateUrl = remainder.substring(delimiter + 1);
        if (!delegateUrl.startsWith("jdbc:postgresql:")) {
            throw new SQLException("setlocal delegate must be PostgreSQL JDBC");
        }
        Config config = Config.fromEnvironment();
        Connection target = DriverManager.getConnection(delegateUrl, info);
        String connectionId = role + "-C" + CONNECTIONS.incrementAndGet();
        return (Connection) Proxy.newProxyInstance(
                target.getClass().getClassLoader(),
                new Class<?>[] {Connection.class},
                new ConnectionHandler(target, role, connectionId, config));
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
        return Logger.getLogger("com.resourceadaptive.hyxp");
    }

    private static final class Config {
        final LinkedHashMap<String, Double> params;
        final Path auditPath;
        final String runId;
        final String phase;

        Config(
                LinkedHashMap<String, Double> params,
                Path auditPath,
                String runId,
                String phase) {
            this.params = params;
            this.auditPath = auditPath;
            this.runId = runId;
            this.phase = phase;
        }

        static Config fromEnvironment() throws SQLException {
            String raw = required("HYXP_SETLOCAL_PARAMETERS");
            LinkedHashMap<String, Double> params = new LinkedHashMap<>();
            String[] fields = raw.split(";", -1);
            if (fields.length != NAMES.length) {
                throw new SQLException("setlocal parameter count must be seven");
            }
            for (int index = 0; index < NAMES.length; index++) {
                String prefix = NAMES[index] + "=";
                if (!fields[index].startsWith(prefix)) {
                    throw new SQLException("setlocal parameter order/name mismatch");
                }
                double value;
                try {
                    value = Double.parseDouble(fields[index].substring(prefix.length()));
                } catch (NumberFormatException error) {
                    throw new SQLException("invalid setlocal parameter value", error);
                }
                if (!Double.isFinite(value) || value <= 0.0) {
                    throw new SQLException("setlocal parameter must be finite and positive");
                }
                params.put(NAMES[index], value);
            }
            Path audit = Path.of(required("HYXP_SETLOCAL_AUDIT_PATH"))
                    .toAbsolutePath().normalize();
            return new Config(
                    params,
                    audit,
                    required("HYXP_SETLOCAL_RUN_ID"),
                    required("HYXP_SETLOCAL_PHASE"));
        }

        private static String required(String name) throws SQLException {
            String value = System.getenv(name);
            if (value == null || value.isBlank()) {
                throw new SQLException("missing required environment: " + name);
            }
            return value;
        }
    }

    private static final class ConnectionHandler implements InvocationHandler {
        private final Connection target;
        private final String role;
        private final String connectionId;
        private final Config config;
        private long transactionSequence;
        private boolean applied;

        ConnectionHandler(
                Connection target,
                String role,
                String connectionId,
                Config config) {
            this.target = target;
            this.role = role;
            this.connectionId = connectionId;
            this.config = config;
        }

        @Override
        public Object invoke(Object proxy, Method method, Object[] args) throws Throwable {
            String name = method.getName();
            try {
                if (name.equals("setAutoCommit") && Boolean.TRUE.equals(args[0])) {
                    throw new SQLException("setlocal HyBench connections forbid autocommit");
                }
                if (name.equals("commit") || name.equals("rollback")) {
                    Object result = method.invoke(target, args);
                    applied = false;
                    return result;
                }
                Object result = method.invoke(target, args);
                if (result instanceof CallableStatement) {
                    return wrapStatement((Statement) result, CallableStatement.class);
                }
                if (result instanceof PreparedStatement) {
                    return wrapStatement((Statement) result, PreparedStatement.class);
                }
                if (result instanceof Statement) {
                    return wrapStatement((Statement) result, Statement.class);
                }
                return result;
            } catch (InvocationTargetException error) {
                throw error.getCause();
            }
        }

        private Object wrapStatement(Statement statement, Class<?> jdbcInterface) {
            return Proxy.newProxyInstance(
                    statement.getClass().getClassLoader(),
                    new Class<?>[] {jdbcInterface},
                    new StatementHandler(statement, this));
        }

        synchronized void ensureApplied() throws SQLException {
            if (target.getAutoCommit()) {
                throw new SQLException("setlocal workload statement attempted in autocommit");
            }
            if (applied) return;
            LinkedHashMap<String, Double> readback = new LinkedHashMap<>();
            try (Statement statement = target.createStatement()) {
                for (Map.Entry<String, Double> item : config.params.entrySet()) {
                    statement.execute(
                            "SET LOCAL " + item.getKey() + " = "
                                    + Double.toString(item.getValue()));
                }
                StringBuilder sql = new StringBuilder();
                for (int index = 0; index < NAMES.length; index++) {
                    if (index > 0) sql.append(" UNION ALL ");
                    sql.append("SELECT '").append(NAMES[index])
                            .append("', current_setting('").append(NAMES[index])
                            .append("')::double precision");
                }
                try (ResultSet rows = statement.executeQuery(sql.toString())) {
                    while (rows.next()) readback.put(rows.getString(1), rows.getDouble(2));
                }
            }
            if (!readback.keySet().equals(config.params.keySet())) {
                throw new SQLException("setlocal immediate readback set mismatch");
            }
            for (String name : NAMES) {
                double expected = config.params.get(name);
                double actual = readback.get(name);
                double tolerance = Math.max(1.0e-12, Math.abs(expected) * 1.0e-12);
                if (Math.abs(actual - expected) > tolerance) {
                    throw new SQLException("setlocal immediate readback mismatch: " + name);
                }
            }
            transactionSequence++;
            appendAudit(readback);
            applied = true;
        }

        private void appendAudit(Map<String, Double> readback) throws SQLException {
            StringBuilder params = new StringBuilder("{");
            int index = 0;
            for (String name : NAMES) {
                if (index++ > 0) params.append(',');
                params.append(json(name)).append(':').append(readback.get(name));
            }
            params.append('}');
            String line = "{"
                    + "\"connection_id\":" + json(connectionId)
                    + ",\"event\":\"transaction_local_parameters_applied\""
                    + ",\"global_writes_used\":false"
                    + ",\"immediate_readback_passed\":true"
                    + ",\"parameters\":" + params
                    + ",\"phase\":" + json(config.phase)
                    + ",\"role\":" + json(role)
                    + ",\"run_id\":" + json(config.runId)
                    + ",\"transaction_sequence\":" + transactionSequence
                    + "}\n";
            try {
                Path parent = config.auditPath.getParent();
                if (parent == null) throw new IOException("audit path has no parent");
                Files.createDirectories(parent);
                synchronized (AUDIT_WRITE_LOCK) {
                    try (FileChannel channel = FileChannel.open(
                            config.auditPath,
                            StandardOpenOption.CREATE,
                            StandardOpenOption.WRITE,
                            StandardOpenOption.APPEND);
                         FileLock ignored = channel.lock()) {
                        ByteBuffer encoded = StandardCharsets.UTF_8.encode(line);
                        while (encoded.hasRemaining()) channel.write(encoded);
                        channel.force(true);
                    }
                }
            } catch (IOException error) {
                throw new SQLException("cannot append setlocal audit", error);
            }
        }
    }

    private static final class StatementHandler implements InvocationHandler {
        private final Statement target;
        private final ConnectionHandler connection;

        StatementHandler(Statement target, ConnectionHandler connection) {
            this.target = target;
            this.connection = connection;
        }

        @Override
        public Object invoke(Object proxy, Method method, Object[] args) throws Throwable {
            String name = method.getName();
            if (name.equals("execute")
                    || name.equals("executeQuery")
                    || name.equals("executeUpdate")
                    || name.equals("executeLargeUpdate")
                    || name.equals("executeBatch")
                    || name.equals("executeLargeBatch")) {
                connection.ensureApplied();
            }
            try {
                return method.invoke(target, args);
            } catch (InvocationTargetException error) {
                throw error.getCause();
            }
        }
    }

    private static String json(String value) {
        return "\"" + value.replace("\\", "\\\\").replace("\"", "\\\"") + "\"";
    }
}
