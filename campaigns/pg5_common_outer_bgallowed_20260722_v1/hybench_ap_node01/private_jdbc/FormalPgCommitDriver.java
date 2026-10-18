package formal.pg;

import java.io.BufferedWriter;
import java.io.IOException;
import java.lang.reflect.InvocationHandler;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.sql.Connection;
import java.sql.Driver;
import java.sql.DriverManager;
import java.sql.DriverPropertyInfo;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.ResultSetMetaData;
import java.sql.SQLException;
import java.sql.Types;
import java.time.Instant;
import java.util.Properties;
import java.util.logging.Logger;

/**
 * Lane-private correction for the formal HyBench AP commit driver.
 *
 * <p>The shared driver eagerly drained and closed every executeQuery result
 * before returning it. HyBench AP2 then called next() on that closed result.
 * This version lazily hashes executeQuery rows while the client consumes them,
 * drains any unread rows on close, and commits only after the result is fully
 * consumed. The execute() path remains eager because HyBench does not consume
 * the result set returned by its main analytical statements.</p>
 */
public final class FormalPgCommitDriver implements Driver {
    private static final String PREFIX = "jdbc:formalpg:";
    private static final Object LOG_LOCK = new Object();

    static {
        try {
            DriverManager.registerDriver(new FormalPgCommitDriver());
        } catch (SQLException exc) {
            throw new ExceptionInInitializerError(exc);
        }
    }

    @Override
    public Connection connect(String url, Properties info) throws SQLException {
        if (!acceptsURL(url)) {
            return null;
        }
        String delegateUrl = "jdbc:" + url.substring(PREFIX.length());
        Connection connection = new org.postgresql.Driver().connect(delegateUrl, info);
        if (connection == null) {
            throw new SQLException("PostgreSQL 42.7.4 driver rejected delegated URL");
        }
        connection.setAutoCommit(false);
        connection.setTransactionIsolation(Connection.TRANSACTION_READ_COMMITTED);
        return proxyConnection(connection);
    }

    @Override
    public boolean acceptsURL(String url) {
        return url != null && url.startsWith(PREFIX);
    }

    @Override
    public DriverPropertyInfo[] getPropertyInfo(String url, Properties info) throws SQLException {
        String delegateUrl = acceptsURL(url) ? "jdbc:" + url.substring(PREFIX.length()) : url;
        return new org.postgresql.Driver().getPropertyInfo(delegateUrl, info);
    }

    @Override public int getMajorVersion() { return 42; }
    @Override public int getMinorVersion() { return 7; }
    @Override public boolean jdbcCompliant() { return true; }
    @Override public Logger getParentLogger() { return Logger.getLogger("formal.pg"); }

    private static Connection proxyConnection(Connection delegate) {
        InvocationHandler handler = new ConnectionHandler(delegate);
        return (Connection) Proxy.newProxyInstance(
                FormalPgCommitDriver.class.getClassLoader(),
                new Class<?>[]{Connection.class},
                handler
        );
    }

    private static final class ConnectionHandler implements InvocationHandler {
        private final Connection delegate;

        private ConnectionHandler(Connection delegate) {
            this.delegate = delegate;
        }

        @Override
        public Object invoke(Object proxy, Method method, Object[] args) throws Throwable {
            String name = method.getName();
            if ("unwrap".equals(name) && args != null && args.length == 1
                    && ((Class<?>) args[0]).isInstance(proxy)) {
                return proxy;
            }
            if ("isWrapperFor".equals(name) && args != null && args.length == 1
                    && ((Class<?>) args[0]).isInstance(proxy)) {
                return true;
            }
            try {
                Object value = method.invoke(delegate, args);
                if (value instanceof PreparedStatement && name.startsWith("prepareStatement")) {
                    String sql = args != null && args.length > 0 && args[0] instanceof String
                            ? (String) args[0] : "";
                    PreparedStatement statement = (PreparedStatement) value;
                    statement.setFetchSize(1000);
                    return proxyStatement(statement, delegate, sql);
                }
                return value;
            } catch (InvocationTargetException exc) {
                throw exc.getCause();
            }
        }
    }

    private static PreparedStatement proxyStatement(
            PreparedStatement delegate,
            Connection connection,
            String sql
    ) {
        InvocationHandler handler = new StatementHandler(delegate, connection, sql);
        return (PreparedStatement) Proxy.newProxyInstance(
                FormalPgCommitDriver.class.getClassLoader(),
                new Class<?>[]{PreparedStatement.class},
                handler
        );
    }

    private static final class StatementHandler implements InvocationHandler {
        private final PreparedStatement delegate;
        private final Connection connection;
        private final String sql;
        private LazyResultSetHandler activeResult;

        private StatementHandler(PreparedStatement delegate, Connection connection, String sql) {
            this.delegate = delegate;
            this.connection = connection;
            this.sql = sql;
        }

        @Override
        public Object invoke(Object proxy, Method method, Object[] args) throws Throwable {
            String name = method.getName();
            if ("close".equals(name)) {
                if (activeResult != null) {
                    activeResult.closeAndFinalize();
                    activeResult = null;
                }
                return invokeDelegate(method, args);
            }
            if (!isExecute(name)) {
                return invokeDelegate(method, args);
            }

            if (activeResult != null) {
                activeResult.closeAndFinalize();
                activeResult = null;
            }
            long started = System.nanoTime();
            long rowCount = 0L;
            String resultSha256 = null;
            try {
                Object result = method.invoke(delegate, args);
                if ("executeQuery".equals(name)) {
                    ResultSet rawResult = (ResultSet) result;
                    activeResult = new LazyResultSetHandler(
                            rawResult, connection, sql, started
                    );
                    return activeResult.proxy();
                }

                ResultSet resultSet = null;
                if ("execute".equals(name) && Boolean.TRUE.equals(result)) {
                    resultSet = delegate.getResultSet();
                }
                if (resultSet != null) {
                    DigestResult digest = digestResultSet(resultSet);
                    rowCount = digest.rowCount;
                    resultSha256 = digest.sha256;
                    resultSet.close();
                }
                connection.commit();
                appendEvidence(sql, rowCount, resultSha256, started, true, null);
                return result;
            } catch (InvocationTargetException exc) {
                Throwable cause = exc.getCause();
                rollbackQuietly(connection);
                appendEvidence(sql, rowCount, resultSha256, started, false, cause.toString());
                throw cause;
            } catch (Throwable exc) {
                rollbackQuietly(connection);
                appendEvidence(sql, rowCount, resultSha256, started, false, exc.toString());
                throw exc;
            }
        }

        private Object invokeDelegate(Method method, Object[] args) throws Throwable {
            try {
                return method.invoke(delegate, args);
            } catch (InvocationTargetException exc) {
                throw exc.getCause();
            }
        }

        private static boolean isExecute(String name) {
            return "execute".equals(name) || "executeQuery".equals(name)
                    || "executeUpdate".equals(name) || "executeLargeUpdate".equals(name);
        }
    }

    private static final class LazyResultSetHandler implements InvocationHandler {
        private final ResultSet delegate;
        private final Connection connection;
        private final String sql;
        private final long startedNanos;
        private final MessageDigest digest;
        private final ResultSetMetaData metadata;
        private final int columns;
        private long rowCount;
        private boolean finalized;
        private boolean failurePublished;

        private LazyResultSetHandler(
                ResultSet delegate,
                Connection connection,
                String sql,
                long startedNanos
        ) throws SQLException, NoSuchAlgorithmException {
            this.delegate = delegate;
            this.connection = connection;
            this.sql = sql;
            this.startedNanos = startedNanos;
            this.digest = MessageDigest.getInstance("SHA-256");
            this.metadata = delegate.getMetaData();
            this.columns = metadata.getColumnCount();
            updateInt(digest, columns);
            for (int column = 1; column <= columns; column++) {
                updateString(digest, metadata.getColumnLabel(column));
                updateInt(digest, metadata.getColumnType(column));
            }
        }

        private ResultSet proxy() {
            return (ResultSet) Proxy.newProxyInstance(
                    FormalPgCommitDriver.class.getClassLoader(),
                    new Class<?>[]{ResultSet.class},
                    this
            );
        }

        @Override
        public Object invoke(Object proxy, Method method, Object[] args) throws Throwable {
            String name = method.getName();
            if ("unwrap".equals(name) && args != null && args.length == 1
                    && ((Class<?>) args[0]).isInstance(proxy)) {
                return proxy;
            }
            if ("isWrapperFor".equals(name) && args != null && args.length == 1
                    && ((Class<?>) args[0]).isInstance(proxy)) {
                return true;
            }
            if ("close".equals(name)) {
                closeAndFinalize();
                return null;
            }
            if ("next".equals(name)) {
                try {
                    boolean hasRow = delegate.next();
                    if (hasRow) {
                        digestCurrentRow();
                    } else {
                        finalizeSuccess();
                    }
                    return hasRow;
                } catch (Throwable exc) {
                    finalizeFailure(exc);
                    throw exc;
                }
            }
            try {
                return method.invoke(delegate, args);
            } catch (InvocationTargetException exc) {
                Throwable cause = exc.getCause();
                finalizeFailure(cause);
                throw cause;
            }
        }

        private void digestCurrentRow() throws SQLException {
            rowCount++;
            updateLong(digest, rowCount);
            for (int column = 1; column <= columns; column++) {
                updateString(
                        digest,
                        normalizedValue(delegate, metadata.getColumnType(column), column)
                );
            }
        }

        private void closeAndFinalize() throws Throwable {
            if (finalized) {
                delegate.close();
                return;
            }
            try {
                while (delegate.next()) {
                    digestCurrentRow();
                }
                finalizeSuccess();
            } catch (Throwable exc) {
                finalizeFailure(exc);
                throw exc;
            }
        }

        private void finalizeSuccess() throws SQLException {
            if (finalized) {
                return;
            }
            delegate.close();
            connection.commit();
            appendEvidence(sql, rowCount, hex(digest.digest()), startedNanos, true, null);
            finalized = true;
        }

        private void finalizeFailure(Throwable error) {
            if (finalized || failurePublished) {
                return;
            }
            rollbackQuietly(connection);
            appendEvidence(sql, rowCount, null, startedNanos, false, error.toString());
            failurePublished = true;
            finalized = true;
            try {
                delegate.close();
            } catch (SQLException ignored) {
                // The first failure remains authoritative.
            }
        }
    }

    private static DigestResult digestResultSet(ResultSet resultSet)
            throws SQLException, NoSuchAlgorithmException {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        ResultSetMetaData metadata = resultSet.getMetaData();
        int columns = metadata.getColumnCount();
        updateInt(digest, columns);
        for (int column = 1; column <= columns; column++) {
            updateString(digest, metadata.getColumnLabel(column));
            updateInt(digest, metadata.getColumnType(column));
        }
        long rows = 0L;
        while (resultSet.next()) {
            rows++;
            updateLong(digest, rows);
            for (int column = 1; column <= columns; column++) {
                updateString(digest, normalizedValue(resultSet, metadata.getColumnType(column), column));
            }
        }
        return new DigestResult(rows, hex(digest.digest()));
    }

    private static String normalizedValue(ResultSet resultSet, int jdbcType, int column)
            throws SQLException {
        String value = resultSet.getString(column);
        if (value == null) {
            return null;
        }
        switch (jdbcType) {
            case Types.CHAR:
            case Types.NCHAR:
                return value.stripTrailing();
            default:
                return value;
        }
    }

    private static void updateString(MessageDigest digest, String value) {
        if (value == null) {
            updateInt(digest, -1);
            return;
        }
        byte[] bytes = value.getBytes(StandardCharsets.UTF_8);
        updateInt(digest, bytes.length);
        digest.update(bytes);
    }

    private static void updateInt(MessageDigest digest, int value) {
        digest.update((byte) (value >>> 24));
        digest.update((byte) (value >>> 16));
        digest.update((byte) (value >>> 8));
        digest.update((byte) value);
    }

    private static void updateLong(MessageDigest digest, long value) {
        for (int shift = 56; shift >= 0; shift -= 8) {
            digest.update((byte) (value >>> shift));
        }
    }

    private static String hex(byte[] bytes) {
        StringBuilder value = new StringBuilder(bytes.length * 2);
        for (byte item : bytes) {
            value.append(String.format("%02x", item & 0xff));
        }
        return value.toString();
    }

    private static void rollbackQuietly(Connection connection) {
        try {
            connection.rollback();
        } catch (SQLException ignored) {
            // The original SQL failure is authoritative.
        }
    }

    private static void appendEvidence(
            String sql,
            long rowCount,
            String resultSha256,
            long startedNanos,
            boolean success,
            String error
    ) {
        String pathValue = System.getenv("FORMAL_PG_RESULT_LOG");
        if (pathValue == null || pathValue.isBlank()) {
            return;
        }
        String record = "{"
                + "\"event\":\"query_complete\","
                + "\"timestamp_utc\":\"" + escape(Instant.now().toString()) + "\","
                + "\"sql_sha256\":\"" + sha256(sql) + "\","
                + "\"row_count\":" + rowCount + ","
                + "\"result_sha256\":" + nullableJson(resultSha256) + ","
                + "\"latency_ms\":" + ((System.nanoTime() - startedNanos) / 1_000_000.0) + ","
                + "\"success\":" + success + ","
                + "\"commit_after_query\":" + success + ","
                + "\"fetch_size\":1000,"
                + "\"error\":" + nullableJson(error)
                + "}";
        Path path = Path.of(pathValue);
        synchronized (LOG_LOCK) {
            try {
                Path parent = path.toAbsolutePath().getParent();
                if (parent != null) {
                    Files.createDirectories(parent);
                }
                try (BufferedWriter writer = Files.newBufferedWriter(
                        path,
                        StandardCharsets.UTF_8,
                        StandardOpenOption.CREATE,
                        StandardOpenOption.WRITE,
                        StandardOpenOption.APPEND
                )) {
                    writer.write(record);
                    writer.newLine();
                    writer.flush();
                }
            } catch (IOException exc) {
                throw new IllegalStateException("cannot append formal JDBC evidence", exc);
            }
        }
    }

    private static String sha256(String value) {
        try {
            return hex(MessageDigest.getInstance("SHA-256").digest(
                    value.getBytes(StandardCharsets.UTF_8)
            ));
        } catch (NoSuchAlgorithmException exc) {
            throw new IllegalStateException(exc);
        }
    }

    private static String nullableJson(String value) {
        return value == null ? "null" : "\"" + escape(value) + "\"";
    }

    private static String escape(String value) {
        return value.replace("\\", "\\\\")
                .replace("\"", "\\\"")
                .replace("\n", "\\n")
                .replace("\r", "\\r")
                .replace("\t", "\\t");
    }

    private static final class DigestResult {
        private final long rowCount;
        private final String sha256;

        private DigestResult(long rowCount, String sha256) {
            this.rowCount = rowCount;
            this.sha256 = sha256;
        }
    }
}
