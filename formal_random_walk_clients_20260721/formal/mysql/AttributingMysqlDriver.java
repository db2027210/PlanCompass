package formal.mysql;

import java.io.IOException;
import java.lang.reflect.InvocationHandler;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.sql.CallableStatement;
import java.sql.Connection;
import java.sql.Driver;
import java.sql.DriverManager;
import java.sql.DriverPropertyInfo;
import java.sql.PreparedStatement;
import java.sql.SQLException;
import java.sql.SQLTimeoutException;
import java.sql.Statement;
import java.util.Properties;
import java.util.concurrent.atomic.AtomicLong;
import java.util.logging.Logger;

/**
 * Observation-only Connector/J delegate for frozen HyBench XP.
 *
 * The frozen TPClient performs business-rule rollbacks that cannot be inferred
 * from its latency histograms.  This delegate leaves SQL and transaction
 * behavior unchanged and records the successful commit/rollback call, plus
 * statement/transaction SQL exceptions, while execAT1..execAT6 is on-stack.
 */
public final class AttributingMysqlDriver implements Driver {
    private static final String FORMAL_PREFIX = "jdbc:formal-mysql:";
    private static final Object LOG_LOCK = new Object();
    private static final AtomicLong LOG_ROWS = new AtomicLong();
    private static FileChannel logChannel;

    static {
        try {
            DriverManager.registerDriver(new AttributingMysqlDriver());
            Runtime.getRuntime().addShutdownHook(new Thread(() -> closeLog(), "formal-at-log-close"));
        } catch (SQLException error) {
            throw new ExceptionInInitializerError(error);
        }
    }

    private static String eventPath() throws SQLException {
        String value = System.getenv("FORMAL_MYSQL_AT_EVENT_LOG");
        if (value == null || value.isBlank()) {
            throw new SQLException("FORMAL_MYSQL_AT_EVENT_LOG is required");
        }
        return value;
    }

    private static FileChannel channel() throws SQLException {
        synchronized (LOG_LOCK) {
            if (logChannel == null || !logChannel.isOpen()) {
                try {
                    logChannel = FileChannel.open(
                            Path.of(eventPath()),
                            StandardOpenOption.CREATE,
                            StandardOpenOption.WRITE,
                            StandardOpenOption.APPEND);
                } catch (IOException error) {
                    throw new SQLException("cannot open formal AT event log", error);
                }
            }
            return logChannel;
        }
    }

    private static void closeLog() {
        synchronized (LOG_LOCK) {
            if (logChannel == null) return;
            try {
                logChannel.force(true);
                logChannel.close();
            } catch (IOException ignored) {
                // The parent runner fsyncs and validates the final artifact set.
            }
        }
    }

    private static String escape(String value) {
        if (value == null) return "";
        return value.replace("\\", "\\\\").replace("\"", "\\\"")
                .replace("\n", "\\n").replace("\r", "\\r");
    }

    private static String atType() {
        for (StackTraceElement frame : Thread.currentThread().getStackTrace()) {
            if (!"com.hybench.workload.TPClient".equals(frame.getClassName())) continue;
            String method = frame.getMethodName();
            if (method.matches("execAT[1-6]")) return method.substring(4);
        }
        return null;
    }

    private static boolean timeout(SQLException error) {
        String state = error.getSQLState();
        String message = error.getMessage();
        return error instanceof SQLTimeoutException
                || (state != null && state.startsWith("HYT"))
                || error.getErrorCode() == 1205
                || (message != null && message.toLowerCase().contains("timeout"));
    }

    private static void event(String atType, String outcome, SQLException error) throws SQLException {
        if (atType == null) return;
        String line = "{\"epoch_ms\":" + System.currentTimeMillis()
                + ",\"at_type\":\"" + atType + "\""
                + ",\"outcome\":\"" + outcome + "\""
                + (error == null ? "" : ",\"sql_state\":\"" + escape(error.getSQLState()) + "\""
                        + ",\"error_code\":" + error.getErrorCode())
                + "}\n";
        ByteBuffer bytes = StandardCharsets.UTF_8.encode(line);
        synchronized (LOG_LOCK) {
            try {
                FileChannel output = channel();
                while (bytes.hasRemaining()) output.write(bytes);
                if (LOG_ROWS.incrementAndGet() % 1000 == 0) output.force(false);
            } catch (IOException io) {
                throw new SQLException("cannot write formal AT event", io);
            }
        }
    }

    private static Object invokeDelegate(Object delegate, Method method, Object[] args) throws Throwable {
        try {
            return method.invoke(delegate, args);
        } catch (InvocationTargetException error) {
            throw error.getCause();
        }
    }

    private static Object statementProxy(Object statement, Class<?> jdbcInterface) {
        InvocationHandler handler = (proxy, method, args) -> {
            String at = atType();
            try {
                return invokeDelegate(statement, method, args);
            } catch (SQLException error) {
                if (method.getName().startsWith("execute")) {
                    event(at, timeout(error) ? "timeouts" : "errors", error);
                }
                throw error;
            }
        };
        return Proxy.newProxyInstance(
                AttributingMysqlDriver.class.getClassLoader(),
                new Class<?>[]{jdbcInterface},
                handler);
    }

    private static Connection connectionProxy(Connection connection) {
        InvocationHandler handler = (proxy, method, args) -> {
            String name = method.getName();
            String at = atType();
            try {
                Object result = invokeDelegate(connection, method, args);
                if ("commit".equals(name)) event(at, "commits", null);
                if ("rollback".equals(name)) event(at, "rollbacks", null);
                if (result instanceof CallableStatement) {
                    return statementProxy(result, CallableStatement.class);
                }
                if (result instanceof PreparedStatement) {
                    return statementProxy(result, PreparedStatement.class);
                }
                if (result instanceof Statement) {
                    return statementProxy(result, Statement.class);
                }
                return result;
            } catch (SQLException error) {
                if ("commit".equals(name) || "rollback".equals(name)) {
                    event(at, timeout(error) ? "timeouts" : "errors", error);
                }
                throw error;
            }
        };
        return (Connection) Proxy.newProxyInstance(
                AttributingMysqlDriver.class.getClassLoader(),
                new Class<?>[]{Connection.class},
                handler);
    }

    @Override
    public Connection connect(String url, Properties info) throws SQLException {
        if (!acceptsURL(url)) return null;
        String delegateUrl = "jdbc:mysql:" + url.substring(FORMAL_PREFIX.length());
        Connection connection = new com.mysql.cj.jdbc.Driver().connect(delegateUrl, info);
        if (connection == null) throw new SQLException("Connector/J declined a jdbc:mysql URL");
        return connectionProxy(connection);
    }

    @Override
    public boolean acceptsURL(String url) {
        return url != null && url.startsWith(FORMAL_PREFIX);
    }

    @Override
    public DriverPropertyInfo[] getPropertyInfo(String url, Properties info) throws SQLException {
        String delegateUrl = "jdbc:mysql:" + url.substring(FORMAL_PREFIX.length());
        return new com.mysql.cj.jdbc.Driver().getPropertyInfo(delegateUrl, info);
    }

    @Override public int getMajorVersion() { return 8; }
    @Override public int getMinorVersion() { return 0; }
    @Override public boolean jdbcCompliant() { return true; }
    @Override public Logger getParentLogger() { return Logger.getLogger("formal.mysql"); }
}
