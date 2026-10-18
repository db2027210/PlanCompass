package com.hybench;

import com.hybench.stats.Histogram;
import com.hybench.stats.Result;
import com.hybench.workload.SqlReader;
import com.hybench.workload.Client;
import java.lang.reflect.Field;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;

/**
 * Observation-only launcher around the frozen HyBench classes.
 *
 * It does not replace or modify any frozen class.  It invokes the same public
 * workload methods as HyBench.main and emits counters already retained by the
 * frozen Result/Histogram objects, including per-AT successful commit counts.
 */
public final class HyBenchAdaptiveMain {
    private static int parseStopRequest(
            String text, int minimumMeasurementSeconds, int maximumMeasurementSeconds) {
        String stripped = text.trim();
        if (!stripped.matches("[0-9]+")) {
            throw new IllegalArgumentException("stop request must contain one decimal integer");
        }
        int requested = Integer.parseInt(stripped);
        if (requested < minimumMeasurementSeconds
                || requested > maximumMeasurementSeconds || requested % 30 != 0) {
            throw new IllegalArgumentException(
                    "stop request must be one of " + minimumMeasurementSeconds
                    + "," + (minimumMeasurementSeconds + 30) + ",...,"
                    + maximumMeasurementSeconds);
        }
        return requested;
    }

    private static Integer readStopRequest(
            Path stopFile, int minimumMeasurementSeconds, int maximumMeasurementSeconds)
            throws Exception {
        if (!Files.exists(stopFile, LinkOption.NOFOLLOW_LINKS)) return null;
        if (Files.isSymbolicLink(stopFile)
                || !Files.isRegularFile(stopFile, LinkOption.NOFOLLOW_LINKS)) {
            throw new IllegalArgumentException(
                    "formal_stop_file must be an atomically published regular file");
        }
        return parseStopRequest(Files.readString(stopFile, StandardCharsets.UTF_8),
                minimumMeasurementSeconds, maximumMeasurementSeconds);
    }

    private static Field countField(String name) throws Exception {
        Field field = Client.class.getDeclaredField(name);
        field.setAccessible(true);
        return field;
    }

    private static boolean xpUsesConfiguredApQueries() {
        return !ConfigLoader.prop.getProperty("ap_query_sequence", "").trim().isEmpty()
                || !ConfigLoader.prop.getProperty("ap_query_ids", "").trim().isEmpty();
    }

    private static Thread progress(
            HyBench hybench, Result result, AtomicBoolean running,
            AtomicReference<Throwable> progressError,
            String workload, long sessionStartedNanos) throws Exception {
        Field apTotal = countField("apTotalCount");
        Field atTotal = countField("atTotalCount");
        Field iqTotal = countField("iqTotalCount");
        Field tpTotal = countField("tpTotalCount");
        int warmupSeconds = Integer.parseInt(ConfigLoader.prop.getProperty("formal_warmup_seconds", "0"));
        boolean configuredXpAp = "xp".equals(workload) && xpUsesConfiguredApQueries();
        Thread thread = new Thread(() -> {
            boolean reset = false;
            while (running.get()) {
                try {
                    Histogram hist = result.getHist();
                    if (!reset && System.nanoTime() - sessionStartedNanos
                            >= warmupSeconds * 1_000_000_000L) {
                        if ("xp".equals(workload)) {
                            try {
                                hybench.beginXpFormalMeasurement();
                            } catch (IllegalStateException notActiveYet) {
                                Thread.sleep(100L);
                                continue;
                            }
                        }
                        apTotal.setLong(null, 0L);
                        atTotal.setLong(null, 0L);
                        iqTotal.setLong(null, 0L);
                        tpTotal.setLong(null, 0L);
                        for (int i = 0; i < 13; i++) hist.getAPItem(i).clear();
                        for (int i = 0; i < 6; i++) {
                            hist.getXPATItem(i).clear();
                            hist.getXPIQItem(i).clear();
                        }
                        for (int i = 0; i < 18; i++) hist.getTPItem(i).clear();
                        reset = true;
                        System.out.println("HYBENCH_FORMAL_WARMUP_RESET={\"epoch_ms\":"
                                + System.currentTimeMillis() + ",\"warmup_seconds\":" + warmupSeconds + "}");
                    }
                    StringBuilder out = new StringBuilder();
                    long xapOrApTotal = ("xp".equals(workload) && !configuredXpAp)
                            ? readLong(iqTotal) : readLong(apTotal);
                    out.append("HYBENCH_FORMAL_SAMPLE_JSON={\"epoch_ms\":")
                            .append(System.currentTimeMillis())
                            .append(",\"xap_or_ap_total\":").append(xapOrApTotal)
                            .append(",\"xtp_total\":").append(readLong(atTotal))
                            .append(",\"measurement_phase\":").append(reset)
                            .append(",\"at_commit_counts\":{");
                    for (int i = 0; i < 6; i++) {
                        if (i > 0) out.append(',');
                        out.append("\"AT").append(i + 1).append("\":")
                                .append(hist.getXPATItem(i).getN());
                    }
                    out.append("}}");
                    System.out.println(out.toString());
                    Thread.sleep(1000L);
                } catch (InterruptedException stopped) {
                    return;
                } catch (Exception error) {
                    progressError.compareAndSet(null, error);
                    System.err.println("HYBENCH_FORMAL_SAMPLE_ERROR=" + error.getClass().getSimpleName());
                    return;
                }
            }
        }, "formal-progress");
        thread.setDaemon(true);
        thread.start();
        return thread;
    }

    private static long readLong(Field field) throws IllegalAccessException {
        return field.getLong(null);
    }

    private static String jsonNumber(double value) {
        if (Double.isNaN(value) || Double.isInfinite(value)) return "null";
        return Double.toString(value);
    }

    private static double percentileMax(Histogram hist, boolean xp, double percentile) {
        double value = 0.0;
        if (xp) {
            for (int i = 0; i < 6; i++) {
                if (hist.getXPATItem(i).getN() > 0) {
                    value = Math.max(value, hist.getXPATItem(i).getPercentile(percentile));
                }
                if (!xpUsesConfiguredApQueries() && hist.getXPIQItem(i).getN() > 0) {
                    value = Math.max(value, hist.getXPIQItem(i).getPercentile(percentile));
                }
            }
            if (xpUsesConfiguredApQueries() && hist.getAPItem(9).getN() > 0) {
                value = Math.max(value, hist.getAPItem(9).getPercentile(percentile));
            }
        } else {
            if (hist.getAPItem(9).getN() > 0) {
                value = hist.getAPItem(9).getPercentile(percentile);
            }
        }
        return value;
    }

    private static long primaryTotal(Result result, String workload) {
        if ("xp".equals(workload)) {
            return result.getIqTotal();
        }
        return result.getApTotal();
    }

    private static long livePrimaryTotal(String workload) throws Exception {
        if ("xp".equals(workload)) {
            return xpUsesConfiguredApQueries()
                    ? readLong(countField("apTotalCount"))
                    : readLong(countField("iqTotalCount"));
        }
        return readLong(countField("apTotalCount"));
    }

    private static long liveSecondaryTotal() throws Exception {
        return readLong(countField("atTotalCount"));
    }

    private static void emit(
            Result result, String workload, int warmupSeconds,
            int maximumMeasurementSeconds, int measurementSeconds, boolean stopRequested,
            long stopRequestObservedEpochMs, long primaryAtBoundary, long secondaryAtBoundary,
            Throwable workloadError, Throwable progressError, boolean forcedBoundaryStop) {
        Histogram hist = result.getHist();
        boolean xp = "xp".equals(workload);
        StringBuilder out = new StringBuilder();
        out.append("HYBENCH_FORMAL_METRICS_JSON={");
        out.append("\"workload\":\"").append(workload).append("\",");
        out.append("\"qps\":").append(xp ? "null" : jsonNumber(
                primaryAtBoundary / (double) measurementSeconds)).append(",");
        out.append("\"xap_qps\":").append(xp ? jsonNumber(
                primaryAtBoundary / (double) measurementSeconds) : "null").append(",");
        out.append("\"xtp_tps\":").append(xp ? jsonNumber(
                secondaryAtBoundary / (double) measurementSeconds) : "null").append(",");
        out.append("\"ap_total\":").append(result.getApTotal()).append(",");
        out.append("\"xap_total\":").append(xp ? primaryAtBoundary : result.getIqTotal()).append(",");
        out.append("\"xtp_total\":").append(xp ? secondaryAtBoundary : result.getAtTotal()).append(",");
        out.append("\"latency_p95_ms\":").append(jsonNumber(percentileMax(hist, xp, 95.0))).append(",");
        out.append("\"latency_p99_ms\":").append(jsonNumber(percentileMax(hist, xp, 99.0))).append(",");
        out.append("\"warmup_seconds\":").append(warmupSeconds).append(',');
        out.append("\"measurement_seconds\":").append(measurementSeconds).append(',');
        out.append("\"maximum_measurement_seconds\":")
                .append(maximumMeasurementSeconds).append(',');
        out.append("\"stop_requested\":").append(stopRequested).append(',');
        out.append("\"stop_request_observed_epoch_ms\":")
                .append(stopRequested ? Long.toString(stopRequestObservedEpochMs) : "null").append(',');
        out.append("\"primary_right_censored_count\":")
                .append(Math.max(0L, primaryTotal(result, workload) - primaryAtBoundary)).append(',');
        out.append("\"xtp_right_censored_count\":")
                .append(Math.max(0L, result.getAtTotal() - secondaryAtBoundary)).append(',');
        out.append("\"forced_boundary_stop\":").append(forcedBoundaryStop).append(',');
        out.append("\"workload_error\":").append(workloadError == null ? "null" : "\""
                + workloadError.getClass().getSimpleName() + "\"").append(',');
        out.append("\"progress_error\":").append(progressError == null ? "null" : "\""
                + progressError.getClass().getSimpleName() + "\"").append(',');
        out.append("\"at_commit_counts\":{");
        for (int i = 0; i < 6; i++) {
            if (i > 0) out.append(',');
            out.append("\"AT").append(i + 1).append("\":").append(hist.getXPATItem(i).getN());
        }
        out.append("}}");
        System.out.println(out.toString());
    }

    static boolean quiesceAndJoinWorkload(
            HyBench hybench, Thread workloadThread, String workload,
            AtomicReference<Throwable> workloadError, long timeoutMillis)
            throws InterruptedException {
        if (!workloadThread.isAlive()) return false;
        if ("xp".equals(workload)) {
            try {
                hybench.requestXpGracefulQuiescence(timeoutMillis);
            } catch (Throwable error) {
                workloadError.compareAndSet(null, error);
            }
        }
        /*
         * Never interrupt the workload thread.  XP admission has already
         * stopped, and admitted AP queries/AT transactions must return through
         * their normal trace-finalization paths before runXP seals.
         */
        workloadThread.join(timeoutMillis);
        return workloadThread.isAlive();
    }

    static int terminalExitCode(
            boolean forcedBoundaryStop, Throwable workloadError, Throwable progressError) {
        return forcedBoundaryStop || workloadError != null || progressError != null ? 2 : 0;
    }

    public static void main(String[] args) throws Exception {
        if (args.length == 4 && "validate-stop-request".equals(args[0])) {
            int minimum = Integer.parseInt(args[2]);
            int maximum = Integer.parseInt(args[3]);
            int requested = parseStopRequest(args[1], minimum, maximum);
            System.out.println("HYBENCH_FORMAL_STOP_REQUEST_JSON={\"measurement_seconds\":"
                    + requested + ",\"minimum_measurement_seconds\":" + minimum
                    + ",\"maximum_measurement_seconds\":" + maximum + "}");
            System.out.flush();
            return;
        }
        if (args.length == 5 && "await-stop-request".equals(args[0])) {
            Path stopFile = Path.of(args[1]);
            int minimum = Integer.parseInt(args[2]);
            int maximum = Integer.parseInt(args[3]);
            long timeoutMs = Long.parseLong(args[4]);
            if (Files.exists(stopFile, LinkOption.NOFOLLOW_LINKS)) {
                throw new IllegalArgumentException("stale formal_stop_file exists before await");
            }
            System.out.println("HYBENCH_FORMAL_STOP_AWAIT_READY");
            System.out.flush();
            long timeoutNanos = System.nanoTime() + TimeUnit.MILLISECONDS.toNanos(timeoutMs);
            Integer requested = null;
            while (requested == null && System.nanoTime() < timeoutNanos) {
                requested = readStopRequest(stopFile, minimum, maximum);
                if (requested == null) TimeUnit.MILLISECONDS.sleep(10L);
            }
            if (requested == null) throw new IllegalStateException("stop request await timed out");
            System.out.println("HYBENCH_FORMAL_STOP_REQUEST_JSON={\"measurement_seconds\":"
                    + requested + ",\"minimum_measurement_seconds\":" + minimum
                    + ",\"maximum_measurement_seconds\":" + maximum + "}");
            System.out.flush();
            return;
        }
        if (args.length != 3 || !("ap".equals(args[0]) || "xp".equals(args[0]))) {
            throw new IllegalArgumentException("usage: HyBenchAdaptiveMain ap|xp CONFIG STATEMENTS");
        }
        String workload = args[0];
        ConfigLoader.confFile = args[1];
        ConfigLoader loader = new ConfigLoader();
        loader.loadConfig();
        int warmupSeconds = Integer.parseInt(
                ConfigLoader.prop.getProperty("formal_warmup_seconds", "0"));
        int maximumMeasurementSeconds = Integer.parseInt(
                ConfigLoader.prop.getProperty("formal_maximum_measurement_seconds", "900"));
        int minimumMeasurementSeconds = Integer.parseInt(
                ConfigLoader.prop.getProperty("formal_minimum_measurement_seconds", "300"));
        int gracefulQuiescenceSeconds = Integer.parseInt(
                ConfigLoader.prop.getProperty("formal_graceful_quiescence_seconds", "0"));
        String rawStopFile = ConfigLoader.prop.getProperty("formal_stop_file", "").trim();
        if (warmupSeconds != 0 || minimumMeasurementSeconds != 300
                || maximumMeasurementSeconds != 900
                || gracefulQuiescenceSeconds < 30 || gracefulQuiescenceSeconds > 120
                || rawStopFile.isEmpty()) {
            throw new IllegalArgumentException(
                    "controlled formal HyBench requires warmup=0, minimum measurement=300, "
                    + "maximum measurement=900, "
                    + "graceful quiescence in [30,120] seconds and stop-file");
        }
        Path stopFile = Path.of(rawStopFile);
        if (!stopFile.isAbsolute()) throw new IllegalArgumentException("formal_stop_file must be absolute");
        if (Files.exists(stopFile, LinkOption.NOFOLLOW_LINKS)) {
            throw new IllegalArgumentException("stale formal_stop_file exists before workload start");
        }
        int configuredSeconds = Integer.parseInt(ConfigLoader.prop.getProperty(
                "xp".equals(workload) ? "xpRunSecs" : "apRunSecs", "0"));
        if (configuredSeconds != warmupSeconds + maximumMeasurementSeconds) {
            throw new IllegalArgumentException(
                    "HyBench configured duration must equal warmup+maximum measurement seconds");
        }
        HyBench hybench = new HyBench();
        hybench.setSqls(new SqlReader(args[2]).loader());
        AtomicBoolean running = new AtomicBoolean(true);
        AtomicReference<Throwable> progressError = new AtomicReference<>();
        AtomicReference<Throwable> workloadError = new AtomicReference<>();
        long sessionStartedNanos = System.nanoTime();
        long sessionStartedEpochMs = System.currentTimeMillis();
        System.out.println("HYBENCH_FORMAL_SESSION_JSON={\"started_epoch_ms\":"
                + sessionStartedEpochMs + ",\"measurement_start_epoch_ms\":"
                + (sessionStartedEpochMs + warmupSeconds * 1_000L)
                + ",\"warmup_seconds\":" + warmupSeconds
                + ",\"minimum_measurement_seconds\":" + minimumMeasurementSeconds
                + ",\"maximum_measurement_seconds\":" + maximumMeasurementSeconds + "}");
        System.out.flush();
        Thread progress = progress(
                hybench, hybench.getRes(), running, progressError,
                workload, sessionStartedNanos);
        Thread workloadThread = new Thread(() -> {
            try {
                if ("ap".equals(workload)) hybench.runAP();
                else hybench.runXP(0);
            } catch (Throwable error) {
                workloadError.compareAndSet(null, error);
            }
        }, "formal-hybench-workload");
        workloadThread.setDaemon(true);
        workloadThread.start();
        int selectedMeasurementSeconds = maximumMeasurementSeconds;
        boolean stopRequested = false;
        long stopRequestObservedEpochMs = 0L;
        long selectedDeadlineNanos = sessionStartedNanos
                + TimeUnit.SECONDS.toNanos(warmupSeconds + maximumMeasurementSeconds);
        while (workloadThread.isAlive() && System.nanoTime() < selectedDeadlineNanos) {
            if (!stopRequested) {
                Integer request = readStopRequest(
                        stopFile, minimumMeasurementSeconds, maximumMeasurementSeconds);
                if (request != null) {
                    selectedMeasurementSeconds = request;
                    selectedDeadlineNanos = sessionStartedNanos
                            + TimeUnit.SECONDS.toNanos(warmupSeconds + request);
                    stopRequested = true;
                    stopRequestObservedEpochMs = System.currentTimeMillis();
                }
            }
            long remaining = selectedDeadlineNanos - System.nanoTime();
            if (remaining > 0) {
                TimeUnit.NANOSECONDS.sleep(Math.min(
                        remaining, TimeUnit.MILLISECONDS.toNanos(100L)));
            }
        }
        Result result = hybench.getRes();
        long primaryAtBoundary = livePrimaryTotal(workload);
        long secondaryAtBoundary = liveSecondaryTotal();
        boolean forcedBoundaryStop = false;
        try {
            forcedBoundaryStop = quiesceAndJoinWorkload(
                    hybench, workloadThread, workload, workloadError,
                    TimeUnit.SECONDS.toMillis(gracefulQuiescenceSeconds));
        } finally {
            running.set(false);
            progress.interrupt();
            progress.join();
        }
        result.printResult("ap".equals(workload) ? 7 : 0);
        emit(result, workload, warmupSeconds, maximumMeasurementSeconds,
                selectedMeasurementSeconds, stopRequested,
                stopRequestObservedEpochMs, primaryAtBoundary, secondaryAtBoundary,
                workloadError.get(), progressError.get(), forcedBoundaryStop);
        System.out.flush();
        int terminalExitCode = terminalExitCode(
                forcedBoundaryStop, workloadError.get(), progressError.get());
        if (terminalExitCode != 0) {
            if (workloadError.get() != null) {
                workloadError.get().printStackTrace(System.err);
            }
            if (progressError.get() != null) {
                progressError.get().printStackTrace(System.err);
            }
            System.err.flush();
            /*
             * A failed/forced drain can leave non-daemon JDBC workers blocked
             * inside the frozen client.  Throwing from main does not terminate
             * that JVM.  The final marker is already durable, so terminate
             * fail-closed with a non-zero status and never publish success.
             */
            Runtime.getRuntime().halt(terminalExitCode);
        }
    }
}
