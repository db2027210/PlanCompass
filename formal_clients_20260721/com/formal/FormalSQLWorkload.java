package com.formal;

import java.io.BufferedWriter;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.security.MessageDigest;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.ResultSetMetaData;
import java.sql.SQLException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Properties;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;

/** Prepared-statement client for frozen single-SQL correctness and JOB runs. */
public final class FormalSQLWorkload {
    private final Properties config;
    private final String sql;
    private final Path eventPath;
    private final Object eventLock = new Object();
    private final List<Completion> completions = Collections.synchronizedList(new ArrayList<>());
    private final List<Long> failureEndNanos = Collections.synchronizedList(new ArrayList<>());
    private final AtomicLong failures = new AtomicLong();
    private final AtomicInteger inFlight = new AtomicInteger();
    private volatile long deadlineNanos;

    private static final class Completion {
        final long startedNanos;
        final long endedNanos;

        Completion(long startedNanos, long endedNanos) {
            this.startedNanos = startedNanos;
            this.endedNanos = endedNanos;
        }

        double latencyMs() {
            return (endedNanos - startedNanos) / 1_000_000.0;
        }
    }

    private FormalSQLWorkload(Properties config) throws IOException {
        this.config = config;
        this.sql = Files.readString(Path.of(required("sql_file")), StandardCharsets.UTF_8)
                .trim().replaceFirst(";\\s*$", "");
        this.eventPath = Path.of(required("event_path"));
        Files.createDirectories(eventPath.getParent());
    }

    private String required(String key) {
        String value = config.getProperty(key);
        if (value == null || value.isBlank()) throw new IllegalArgumentException("missing " + key);
        return value;
    }

    private Connection connect() throws SQLException {
        Properties props = new Properties();
        props.setProperty("user", required("user"));
        props.setProperty("password", required("password"));
        Connection connection = DriverManager.getConnection(required("url"), props);
        connection.setAutoCommit(false);
        connection.setTransactionIsolation(Connection.TRANSACTION_READ_COMMITTED);
        return connection;
    }

    private PreparedStatement prepare(Connection connection) throws SQLException {
        PreparedStatement statement = connection.prepareStatement(
                sql, ResultSet.TYPE_FORWARD_ONLY, ResultSet.CONCUR_READ_ONLY);
        statement.setFetchSize(1000);
        String parameter = config.getProperty("parameter_int", "").trim();
        if (!parameter.isEmpty()) statement.setInt(1, Integer.parseInt(parameter));
        return statement;
    }

    private static String escape(String value) {
        return value.replace("\\", "\\\\").replace("\"", "\\\"")
                .replace("\n", "\\n").replace("\r", "\\r");
    }

    private void event(String status, int worker, long startedNs, long endedNs, long rows, String error)
            throws IOException {
        String line = "{\"epoch_ms\":" + System.currentTimeMillis()
                + ",\"worker\":" + worker
                + ",\"status\":\"" + status + "\""
                + ",\"started_nanos\":" + startedNs
                + ",\"ended_nanos\":" + endedNs
                + ",\"latency_ms\":" + ((endedNs - startedNs) / 1_000_000.0)
                + ",\"rows\":" + rows
                + ",\"counted_before_deadline\":" + (endedNs <= deadlineNanos)
                + (error == null ? "" : ",\"error\":\"" + escape(error) + "\"")
                + "}\n";
        synchronized (eventLock) {
            Files.writeString(
                    eventPath, line, StandardCharsets.UTF_8,
                    StandardOpenOption.CREATE, StandardOpenOption.APPEND);
        }
    }

    private void worker(int worker, CountDownLatch start) {
        try (Connection connection = connect(); PreparedStatement statement = prepare(connection)) {
            start.await();
            while (System.nanoTime() < deadlineNanos) {
                long begun = System.nanoTime();
                inFlight.incrementAndGet();
                try (ResultSet result = statement.executeQuery()) {
                    long rows = 0;
                    while (result.next()) rows++;
                    connection.commit();
                    long ended = System.nanoTime();
                    completions.add(new Completion(begun, ended));
                    event("complete", worker, begun, ended, rows, null);
                } catch (SQLException error) {
                    try { connection.rollback(); } catch (SQLException ignored) { }
                    long ended = System.nanoTime();
                    failures.incrementAndGet();
                    failureEndNanos.add(ended);
                    event("error", worker, begun, ended, 0, error.getClass().getSimpleName() + ":" + error.getMessage());
                    break;
                } finally {
                    inFlight.decrementAndGet();
                }
            }
        } catch (Exception error) {
            failures.incrementAndGet();
            try {
                long now = System.nanoTime();
                failureEndNanos.add(now);
                event("worker_error", worker, now, now, 0, error.getClass().getSimpleName() + ":" + error.getMessage());
            } catch (IOException ignored) { }
        }
    }

    private static int parseStopRequest(String text, int maximumMeasurementSeconds) {
        String stripped = text.trim();
        if (!stripped.matches("[0-9]+")) {
            throw new IllegalArgumentException("stop request must contain one decimal integer");
        }
        int requested = Integer.parseInt(stripped);
        if (requested < 180 || requested > maximumMeasurementSeconds || requested % 30 != 0) {
            throw new IllegalArgumentException(
                    "stop request must be one of 180,210,...," + maximumMeasurementSeconds);
        }
        return requested;
    }

    private static Integer readStopRequest(Path stopFile, int maximumMeasurementSeconds)
            throws IOException {
        if (!Files.exists(stopFile, LinkOption.NOFOLLOW_LINKS)) return null;
        if (Files.isSymbolicLink(stopFile) || !Files.isRegularFile(stopFile, LinkOption.NOFOLLOW_LINKS)) {
            throw new IOException("stop_file must be an atomically published regular file");
        }
        return parseStopRequest(Files.readString(stopFile, StandardCharsets.UTF_8),
                maximumMeasurementSeconds);
    }

    private static double percentile(List<Double> source, double quantile) {
        if (source.isEmpty()) return 0.0;
        List<Double> values = new ArrayList<>(source);
        Collections.sort(values);
        double position = (values.size() - 1) * quantile;
        int low = (int) Math.floor(position);
        int high = (int) Math.ceil(position);
        if (low == high) return values.get(low);
        return values.get(low) * (high - position) + values.get(high) * (position - low);
    }

    private void measure() throws Exception {
        int duration = Integer.parseInt(required("duration_seconds"));
        int concurrency = Integer.parseInt(required("concurrency"));
        if (duration <= 0 || concurrency <= 0) throw new IllegalArgumentException("invalid duration/concurrency");
        String rawStopFile = config.getProperty("stop_file", "").trim();
        boolean controlled = !rawStopFile.isEmpty();
        int warmupSeconds = Integer.parseInt(config.getProperty("warmup_seconds", "0"));
        int maximumMeasurementSeconds = Integer.parseInt(
                config.getProperty("maximum_measurement_seconds", Integer.toString(duration - warmupSeconds)));
        Path stopFile = controlled ? Path.of(rawStopFile) : null;
        if (warmupSeconds < 0 || maximumMeasurementSeconds <= 0
                || duration != warmupSeconds + maximumMeasurementSeconds) {
            throw new IllegalArgumentException(
                    "duration_seconds must equal warmup_seconds + maximum_measurement_seconds");
        }
        if (controlled) {
            if (!stopFile.isAbsolute()) throw new IllegalArgumentException("stop_file must be absolute");
            if (warmupSeconds != 60 || maximumMeasurementSeconds != 600) {
                throw new IllegalArgumentException("controlled formal JOB timing must be 60+600 seconds");
            }
            if (Files.exists(stopFile, LinkOption.NOFOLLOW_LINKS)) {
                throw new IllegalArgumentException("stale stop_file exists before workload start");
            }
            Path parent = stopFile.getParent();
            if (parent == null || !Files.isDirectory(parent, LinkOption.NOFOLLOW_LINKS)) {
                throw new IllegalArgumentException("stop_file parent directory is absent");
            }
        }
        ExecutorService pool = Executors.newFixedThreadPool(concurrency);
        CountDownLatch start = new CountDownLatch(1);
        long started = System.nanoTime();
        long startedEpochMs = System.currentTimeMillis();
        long measurementStartNanos = started + TimeUnit.SECONDS.toNanos(warmupSeconds);
        long maximumDeadlineNanos = measurementStartNanos
                + TimeUnit.SECONDS.toNanos(maximumMeasurementSeconds);
        deadlineNanos = maximumDeadlineNanos;
        int selectedMeasurementSeconds = maximumMeasurementSeconds;
        boolean stopRequested = false;
        long stopRequestObservedEpochMs = 0L;
        System.out.println("FORMAL_SQL_SESSION_JSON={"
                + "\"started_epoch_ms\":" + startedEpochMs + ","
                + "\"measurement_start_epoch_ms\":"
                + (startedEpochMs + warmupSeconds * 1000L) + ","
                + "\"warmup_seconds\":" + warmupSeconds + ","
                + "\"maximum_measurement_seconds\":" + maximumMeasurementSeconds + "}");
        System.out.flush();
        for (int worker = 0; worker < concurrency; worker++) {
            final int id = worker;
            pool.submit(() -> worker(id, start));
        }
        start.countDown();
        while (System.nanoTime() < deadlineNanos) {
            if (controlled && !stopRequested) {
                Integer request = readStopRequest(stopFile, maximumMeasurementSeconds);
                if (request != null) {
                    selectedMeasurementSeconds = request;
                    deadlineNanos = measurementStartNanos
                            + TimeUnit.SECONDS.toNanos(selectedMeasurementSeconds);
                    stopRequested = true;
                    stopRequestObservedEpochMs = System.currentTimeMillis();
                }
            }
            long remaining = deadlineNanos - System.nanoTime();
            if (remaining > 0) {
                TimeUnit.NANOSECONDS.sleep(Math.min(remaining, TimeUnit.MILLISECONDS.toNanos(100)));
            }
        }
        int inflightAtEnd = inFlight.get();
        pool.shutdown();
        if (!pool.awaitTermination(610, TimeUnit.SECONDS)) pool.shutdownNow();
        long finalDeadlineNanos = measurementStartNanos
                + TimeUnit.SECONDS.toNanos(selectedMeasurementSeconds);
        List<Double> selectedLatenciesMs = new ArrayList<>();
        long rightCensored = 0L;
        long startedAfterDeadline = 0L;
        synchronized (completions) {
            for (Completion completion : completions) {
                if (completion.endedNanos > measurementStartNanos
                        && completion.endedNanos <= finalDeadlineNanos) {
                    selectedLatenciesMs.add(completion.latencyMs());
                } else if (completion.startedNanos <= finalDeadlineNanos
                        && completion.endedNanos > finalDeadlineNanos) {
                    rightCensored++;
                } else if (completion.startedNanos > finalDeadlineNanos) {
                    startedAfterDeadline++;
                }
            }
        }
        long selectedFailures;
        synchronized (failureEndNanos) {
            selectedFailures = failureEndNanos.stream()
                    .filter(ended -> ended > measurementStartNanos && ended <= finalDeadlineNanos)
                    .count();
        }
        long completed = selectedLatenciesMs.size();
        long measurementStartEpochMs = startedEpochMs + warmupSeconds * 1000L;
        System.out.println("FORMAL_SQL_METRICS_JSON={"
                + "\"completed\":" + completed + ","
                + "\"failures\":" + selectedFailures + ","
                + "\"all_session_failures\":" + failures.get() + ","
                + "\"inflight_at_end\":" + inflightAtEnd + ","
                + "\"qps\":" + (completed / (double) selectedMeasurementSeconds) + ","
                + "\"latency_p95_ms\":" + percentile(selectedLatenciesMs, 0.95) + ","
                + "\"latency_p99_ms\":" + percentile(selectedLatenciesMs, 0.99) + ","
                + "\"warmup_seconds\":" + warmupSeconds + ","
                + "\"measurement_seconds\":" + selectedMeasurementSeconds + ","
                + "\"maximum_measurement_seconds\":" + maximumMeasurementSeconds + ","
                + "\"measurement_start_epoch_ms\":" + measurementStartEpochMs + ","
                + "\"measurement_end_epoch_ms\":"
                + (measurementStartEpochMs + selectedMeasurementSeconds * 1000L) + ","
                + "\"stop_requested\":" + stopRequested + ","
                + "\"stop_request_observed_epoch_ms\":"
                + (stopRequested ? Long.toString(stopRequestObservedEpochMs) : "null") + ","
                + "\"right_censored_count\":" + rightCensored + ","
                + "\"started_after_deadline_count\":" + startedAfterDeadline + "}");
        System.out.flush();
    }

    private static String hex(byte[] bytes) {
        StringBuilder result = new StringBuilder(bytes.length * 2);
        for (byte value : bytes) result.append(String.format("%02x", value));
        return result.toString();
    }

    private void correctness() throws Exception {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        long rows = 0;
        try (Connection connection = connect(); PreparedStatement statement = prepare(connection);
             ResultSet result = statement.executeQuery()) {
            ResultSetMetaData metadata = result.getMetaData();
            int columns = metadata.getColumnCount();
            digest.update(("columns=" + columns + "\n").getBytes(StandardCharsets.UTF_8));
            while (result.next()) {
                rows++;
                for (int column = 1; column <= columns; column++) {
                    String value = result.getString(column);
                    if (value == null) {
                        digest.update("N\n".getBytes(StandardCharsets.UTF_8));
                    } else {
                        byte[] bytes = value.getBytes(StandardCharsets.UTF_8);
                        digest.update(("V" + bytes.length + ":").getBytes(StandardCharsets.UTF_8));
                        digest.update(bytes);
                        digest.update((byte) '\n');
                    }
                }
            }
            connection.commit();
        }
        System.out.println("FORMAL_SQL_CORRECTNESS_JSON={\"row_count\":" + rows
                + ",\"sha256\":\"" + hex(digest.digest()) + "\"}");
    }

    public static void main(String[] args) throws Exception {
        if (args.length == 3 && "validate-stop-request".equals(args[0])) {
            int maximum = Integer.parseInt(args[2]);
            int requested = parseStopRequest(args[1], maximum);
            System.out.println("FORMAL_SQL_STOP_REQUEST_JSON={\"measurement_seconds\":"
                    + requested + ",\"maximum_measurement_seconds\":" + maximum + "}");
            System.out.flush();
            return;
        }
        if (args.length == 4 && "await-stop-request".equals(args[0])) {
            Path stopFile = Path.of(args[1]);
            int maximum = Integer.parseInt(args[2]);
            long timeoutMs = Long.parseLong(args[3]);
            if (Files.exists(stopFile, LinkOption.NOFOLLOW_LINKS)) {
                throw new IllegalArgumentException("stale stop_file exists before await");
            }
            long timeoutNanos = System.nanoTime() + TimeUnit.MILLISECONDS.toNanos(timeoutMs);
            Integer requested = null;
            while (requested == null && System.nanoTime() < timeoutNanos) {
                requested = readStopRequest(stopFile, maximum);
                if (requested == null) TimeUnit.MILLISECONDS.sleep(10L);
            }
            if (requested == null) throw new IllegalStateException("stop request await timed out");
            System.out.println("FORMAL_SQL_STOP_REQUEST_JSON={\"measurement_seconds\":"
                    + requested + ",\"maximum_measurement_seconds\":" + maximum + "}");
            System.out.flush();
            return;
        }
        if (args.length != 2) throw new IllegalArgumentException("usage: FormalSQLWorkload measure|correctness CONFIG");
        Properties config = new Properties();
        try (var reader = Files.newBufferedReader(Path.of(args[1]), StandardCharsets.UTF_8)) {
            config.load(reader);
        }
        FormalSQLWorkload workload = new FormalSQLWorkload(config);
        if ("measure".equals(args[0])) workload.measure();
        else if ("correctness".equals(args[0])) workload.correctness();
        else throw new IllegalArgumentException("unknown mode " + args[0]);
    }
}
