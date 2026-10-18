package com.hybench.util;

import java.lang.reflect.Proxy;
import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.util.Random;

/** No-database smoke probe for HyBench AP execution-site tracing. */
public final class FormalBindingTraceNoDbProbe {
    private FormalBindingTraceNoDbProbe() {}

    public static void main(String[] args) throws Exception {
        Random expectedRandom = new Random(1234567L);
        RandomGenerator randomGenerator = new RandomGenerator();
        RandomGenerator.setThreadSeed(1234567L);
        for (int index = 0; index < 100; index++) {
            if (randomGenerator.getRandomint(1000000) != expectedRandom.nextInt(1000000)) {
                throw new AssertionError("counting RNG changed the Java Random stream");
            }
        }
        if (RandomGenerator.getThreadDrawCount() < 100L) {
            throw new AssertionError("counting RNG did not record state transitions");
        }
        RandomGenerator.clearThreadSeed();

        ResultSet resultSet = (ResultSet)Proxy.newProxyInstance(
                FormalBindingTraceNoDbProbe.class.getClassLoader(),
                new Class<?>[] {ResultSet.class},
                (proxy, method, arguments) -> defaultValue(method.getReturnType()));
        PreparedStatement statement = (PreparedStatement)Proxy.newProxyInstance(
                FormalBindingTraceNoDbProbe.class.getClassLoader(),
                new Class<?>[] {PreparedStatement.class},
                (proxy, method, arguments) -> "executeQuery".equals(method.getName())
                        ? resultSet : defaultValue(method.getReturnType()));
        Connection target = (Connection)Proxy.newProxyInstance(
                FormalBindingTraceNoDbProbe.class.getClassLoader(),
                new Class<?>[] {Connection.class},
                (proxy, method, arguments) -> "prepareStatement".equals(method.getName())
                        ? statement : defaultValue(method.getReturnType()));
        Connection traced = FormalBindingTrace.wrapConnection(target);
        if (!FormalBindingTrace.enabled()) {
            if (traced != target) throw new AssertionError("disabled trace wrapped connection");
            System.out.println("FORMAL_HYBENCH_AP_TRACE_DISABLED_OK");
            return;
        }

        String workerId = args.length > 0 && args[0].matches("T[1-9][0-9]*") ? args[0] : "T1";
        boolean mismatch = args.length > 0 && "mismatch".equals(args[args.length - 1]);
        long workerSeed = 991L + Integer.parseInt(workerId.substring(1));
        RandomGenerator.setThreadSeed(workerSeed);
        FormalBindingTrace.bindWorker(workerId, 4508333L, workerSeed);
        FormalBindingTrace.beginQuery(2, RandomGenerator.getThreadDrawCount());
        PreparedStatement selector = traced.prepareStatement(
                "SELECT applicantid FROM loanapps WHERE applicantid < ? ORDER BY RAND() LIMIT 1");
        selector.setInt(1, 600000);
        selector.executeQuery();
        FormalBindingTrace.captureDatabaseRandomValue(271828);
        PreparedStatement main = traced.prepareStatement("SELECT * FROM customer WHERE custid = ?");
        main.setInt(1, mismatch ? 7 : 271828);
        if (mismatch) {
            try {
                main.execute();
                throw new AssertionError("AP-2 accepted a non-authoritative database RAND binding");
            } catch (IllegalStateException expected) {
                if (!expected.getMessage().contains("authoritative database RAND binding")) throw expected;
            }
            try {
                FormalBindingTrace.endQuery();
                throw new AssertionError("incomplete AP-2 trace was accepted");
            } catch (IllegalStateException expected) {
                if (!expected.getMessage().contains("incomplete AP-2 trace")) throw expected;
            }
            FormalBindingTrace.unbindWorker();
            RandomGenerator.clearThreadSeed();
            System.out.println("FORMAL_HYBENCH_AP_RAND_FAIL_CLOSED_OK");
            return;
        }
        main.execute();
        FormalBindingTrace.endQuery();

        int[] bindingCounts = {0, 4, 0, 2, 2, 1, 0, 4, 0, 0, 0, 0, 1, 1};
        for (int queryId = 1; queryId <= 13; queryId++) {
            if (queryId == 2) continue;
            int bindingCount = bindingCounts[queryId];
            StringBuilder sql = new StringBuilder("SELECT ");
            if (bindingCount == 0) sql.append('1');
            for (int position = 1; position <= bindingCount; position++) {
                if (position > 1) sql.append(',');
                sql.append('?');
            }
            FormalBindingTrace.beginQuery(queryId, RandomGenerator.getThreadDrawCount());
            PreparedStatement query = traced.prepareStatement(sql.toString());
            for (int position = 1; position <= bindingCount; position++) {
                if (queryId == 12) query.setString(position, "software_IT");
                else query.setInt(position, queryId * 100 + position);
            }
            query.execute();
            FormalBindingTrace.endQuery();
        }
        FormalBindingTrace.unbindWorker();
        RandomGenerator.clearThreadSeed();
        System.out.println("FORMAL_HYBENCH_AP_TRACE_ENABLED_OK");
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
