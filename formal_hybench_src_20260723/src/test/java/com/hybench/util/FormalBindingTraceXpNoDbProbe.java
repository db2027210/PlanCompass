package com.hybench.util;

import java.lang.reflect.Proxy;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.Statement;

/** Complete AP13 + AT1-AT6, sixteen-worker, no-database trace/seal probe. */
public final class FormalBindingTraceXpNoDbProbe {
    private FormalBindingTraceXpNoDbProbe() {}

    public static void main(String[] args) throws Exception {
        if (!FormalBindingTrace.enabled()) throw new AssertionError("trace must be enabled");
        ResultSet rows = (ResultSet)Proxy.newProxyInstance(
                FormalBindingTraceXpNoDbProbe.class.getClassLoader(), new Class<?>[] {ResultSet.class},
                (proxy, method, values) -> defaultValue(method.getReturnType()));
        PreparedStatement prepared = (PreparedStatement)Proxy.newProxyInstance(
                FormalBindingTraceXpNoDbProbe.class.getClassLoader(),
                new Class<?>[] {PreparedStatement.class},
                (proxy, method, values) -> "executeQuery".equals(method.getName())
                        ? rows : defaultValue(method.getReturnType()));
        Statement statement = (Statement)Proxy.newProxyInstance(
                FormalBindingTraceXpNoDbProbe.class.getClassLoader(), new Class<?>[] {Statement.class},
                (proxy, method, values) -> "executeQuery".equals(method.getName())
                        ? rows : defaultValue(method.getReturnType()));
        Connection target = (Connection)Proxy.newProxyInstance(
                FormalBindingTraceXpNoDbProbe.class.getClassLoader(), new Class<?>[] {Connection.class},
                (proxy, method, values) -> {
                    if ("prepareStatement".equals(method.getName())) return prepared;
                    if ("createStatement".equals(method.getName())) return statement;
                    return defaultValue(method.getReturnType());
                });
        Connection traced = FormalBindingTrace.wrapConnection(target);

        for (int worker = 1; worker <= 8; worker++) {
            FormalBindingTrace.bindWorker("XAP-" + worker, 4508333L, 9000L + worker);
            for (int query = 1; query <= 13; query++) {
                FormalBindingTrace.beginQuery(query, query);
                if (query == 2) {
                    PreparedStatement selector = traced.prepareStatement("SELECT id FROM t ORDER BY RAND() LIMIT 1");
                    selector.executeQuery();
                    FormalBindingTrace.captureDatabaseRandomValue(42);
                    PreparedStatement main = traced.prepareStatement("SELECT * FROM t WHERE id=?");
                    main.setInt(1, 42);
                    main.execute();
                } else {
                    traced.prepareStatement("SELECT 1").execute();
                }
                FormalBindingTrace.endQuery();
            }
            FormalBindingTrace.unbindWorker();
        }

        for (int worker = 1; worker <= 8; worker++) {
            FormalBindingTrace.bindWorker("XTP-" + worker, 4508333L, 19000L + worker);
            for (int at = 1; at <= 6; at++) {
                FormalBindingTrace.beginTransaction("AT" + at, at);
                traced.createStatement().executeQuery("SELECT id FROM selector");
                FormalBindingTrace.recordBranch("AT" + at + ".probe=selected");
                FormalBindingTrace.recordLoop("AT" + at + ".probe", 1);
                PreparedStatement update = traced.prepareStatement("UPDATE t SET v=? WHERE id=?");
                update.setString(1, "v" + at);
                update.setInt(2, worker);
                update.executeUpdate();
                traced.commit();
                FormalBindingTrace.endTransaction(true);
            }
            FormalBindingTrace.unbindWorker();
        }
        FormalBindingTrace.bindWorker("XTP-1", 4508333L, 19001L);
        FormalBindingTrace.beginTransaction("AT1", 100L);
        FormalBindingTrace.recordRetry("offline_deadlock_probe");
        traced.createStatement().executeQuery("SELECT 1");
        traced.rollback();
        FormalBindingTrace.endTransaction(true);
        FormalBindingTrace.beginTransaction("AT2", 101L);
        FormalBindingTrace.recordTechnicalFailure("offline_failure_probe");
        FormalBindingTrace.endTransaction(false);
        FormalBindingTrace.unbindWorker();
        FormalBindingTrace.sealXp();
        Path trace = Path.of(System.getenv("FORMAL_MYSQL_BINDING_TRACE_PATH"));
        if (!Files.isRegularFile(Path.of(trace + ".manifest.json"))
                || !Files.isRegularFile(Path.of(trace + ".manifest.json.sha256"))) {
            throw new AssertionError("complete XP trace was not sealed");
        }
        System.out.println("FORMAL_HYBENCH_XP_TRACE_SEALED_OK");
    }

    private static Object defaultValue(Class<?> type) {
        if (!type.isPrimitive() || type == void.class) return null;
        if (type == boolean.class) return false;
        if (type == char.class) return '\0';
        if (type == byte.class) return (byte)0;
        if (type == short.class) return (short)0;
        if (type == int.class) return 0;
        if (type == long.class) return 0L;
        if (type == float.class) return 0.0F;
        return 0.0D;
    }
}
