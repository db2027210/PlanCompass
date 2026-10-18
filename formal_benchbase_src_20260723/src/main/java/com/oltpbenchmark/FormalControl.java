package com.oltpbenchmark;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.util.Map;
import java.util.TreeMap;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;

/** Frozen single-session stop and workload-native completion protocol. */
public final class FormalControl {
  private static final AtomicLong COMPLETED = new AtomicLong();
  private static final ConcurrentLinkedQueue<Long> COMPLETION_END_NANOS =
      new ConcurrentLinkedQueue<>();
  private static final AtomicLong RIGHT_CENSORED = new AtomicLong();
  private static final TreeMap<Long, Long> LATENCY_MICROS = new TreeMap<>();
  private static long latencySamples;
  private static final AtomicBoolean STOP_DUE = new AtomicBoolean();
  private static volatile boolean enabled;
  private static volatile boolean stopRequested;
  private static volatile int selectedSeconds = 600;
  private static volatile int maximumSeconds = 600;
  private static volatile long startedNanos;
  private static volatile long startedEpochMs;
  private static volatile long measurementStartedNanos;
  private static volatile long stopObservedEpochMs;
  private static Path stopFile;
  private static Thread sampler;

  private FormalControl() {}

  private static synchronized void recordLatency(long startedNanos, long endedNanos) {
    if (endedNanos < startedNanos) {
      throw new IllegalArgumentException("BenchBase latency clock regressed");
    }
    long micros = Math.max(0L, (endedNanos - startedNanos) / 1000L);
    LATENCY_MICROS.merge(micros, 1L, Long::sum);
    latencySamples++;
  }

  private static synchronized double latencyPercentileMs(double percentile) {
    if (latencySamples == 0L) return 0.0;
    long rank = Math.max(1L, (long) Math.ceil(percentile * latencySamples));
    long observed = 0L;
    for (Map.Entry<Long, Long> row : LATENCY_MICROS.entrySet()) {
      observed += row.getValue();
      if (observed >= rank) return row.getKey() / 1000.0;
    }
    throw new IllegalStateException("BenchBase latency histogram coverage differs");
  }

  public static int parseStopRequest(String text, int maximum) {
    String value = text.trim();
    if (!value.matches("[0-9]+"))
      throw new IllegalArgumentException("stop request must be decimal");
    int requested = Integer.parseInt(value);
    if (requested < 180 || requested > maximum || requested % 30 != 0) {
      throw new IllegalArgumentException("stop request must be one of 180,210,...," + maximum);
    }
    return requested;
  }

  private static Integer readStop() throws Exception {
    if (!Files.exists(stopFile, LinkOption.NOFOLLOW_LINKS)) return null;
    if (Files.isSymbolicLink(stopFile)
        || !Files.isRegularFile(stopFile, LinkOption.NOFOLLOW_LINKS)) {
      throw new IllegalArgumentException("BENCHBASE_FORMAL_STOP_FILE must be a regular file");
    }
    return parseStopRequest(Files.readString(stopFile, StandardCharsets.UTF_8), maximumSeconds);
  }

  public static void initialize() {
    String raw = System.getenv("BENCHBASE_FORMAL_STOP_FILE");
    if (raw == null || raw.isBlank()) return;
    stopFile = Path.of(raw);
    if (!stopFile.isAbsolute())
      throw new IllegalArgumentException("formal stop-file must be absolute");
    if (Files.exists(stopFile, LinkOption.NOFOLLOW_LINKS)) {
      throw new IllegalArgumentException("stale BenchBase formal stop-file exists");
    }
    String maximumRaw = System.getenv("BENCHBASE_FORMAL_MAXIMUM_MEASUREMENT_SECONDS");
    if (!"60".equals(System.getenv("BENCHBASE_FORMAL_WARMUP_SECONDS"))
        || maximumRaw == null
        || !maximumRaw.matches("[0-9]+")) {
      throw new IllegalArgumentException("formal BenchBase timing must be 60+N");
    }
    maximumSeconds = Integer.parseInt(maximumRaw);
    if (maximumSeconds < 180 || maximumSeconds % 30 != 0) {
      throw new IllegalArgumentException(
          "formal BenchBase maximum must be a multiple of 30 and at least 180");
    }
    selectedSeconds = maximumSeconds;
    enabled = true;
    startedNanos = System.nanoTime();
    startedEpochMs = System.currentTimeMillis();
    System.out.println(
        "BENCHBASE_FORMAL_SESSION_JSON={\"started_epoch_ms\":"
            + startedEpochMs
            + ",\"measurement_start_epoch_ms\":"
            + (startedEpochMs + 60000L)
            + ",\"warmup_seconds\":60,\"maximum_measurement_seconds\":"
            + maximumSeconds
            + "}");
    System.out.flush();
    sampler =
        new Thread(
            () -> {
              try {
                while (!Thread.currentThread().isInterrupted()) {
                  Integer request = stopRequested ? null : readStop();
                  if (request != null) {
                    selectedSeconds = request;
                    stopRequested = true;
                    stopObservedEpochMs = System.currentTimeMillis();
                  }
                  long measurementElapsed =
                      measurementStartedNanos == 0L
                          ? -1L
                          : System.nanoTime() - measurementStartedNanos;
                  if (stopRequested
                      && measurementStartedNanos != 0L
                      && measurementElapsed >= TimeUnit.SECONDS.toNanos(selectedSeconds)) {
                    STOP_DUE.set(true);
                  }
                  System.out.println(
                      "BENCHBASE_FORMAL_SAMPLE_JSON={\"epoch_ms\":"
                          + System.currentTimeMillis()
                          + ",\"completed_total\":"
                          + COMPLETED.get()
                          + ",\"latency_p50_ms\":"
                          + latencyPercentileMs(0.50)
                          + ",\"latency_p95_ms\":"
                          + latencyPercentileMs(0.95)
                          + ",\"latency_p99_ms\":"
                          + latencyPercentileMs(0.99)
                          + ",\"measurement_phase\":"
                          + (measurementElapsed >= 0L)
                          + "}");
                  System.out.flush();
                  Thread.sleep(1000L);
                }
              } catch (InterruptedException ignored) {
                Thread.currentThread().interrupt();
              } catch (Throwable error) {
                error.printStackTrace(System.err);
                STOP_DUE.set(true);
              }
            },
            "benchbase-formal-control");
    sampler.setDaemon(true);
    sampler.start();
  }

  public static boolean stopDue() {
    return enabled && STOP_DUE.get();
  }

  public static void measurementStarted() {
    if (!enabled || measurementStartedNanos != 0L) return;
    long now = System.nanoTime();
    long warmupNanos = now - startedNanos;
    if (warmupNanos < TimeUnit.SECONDS.toNanos(59) || warmupNanos > TimeUnit.SECONDS.toNanos(61)) {
      throw new IllegalStateException("actual BenchBase warmup differs from frozen 60 seconds");
    }
    COMPLETED.set(0L);
    COMPLETION_END_NANOS.clear();
    RIGHT_CENSORED.set(0L);
    synchronized (FormalControl.class) {
      LATENCY_MICROS.clear();
      latencySamples = 0L;
    }
    measurementStartedNanos = now;
    System.out.println(
        "BENCHBASE_FORMAL_WARMUP_RESET={\"epoch_ms\":"
            + System.currentTimeMillis()
            + ",\"warmup_seconds\":60,\"completed_total\":0}");
    System.out.flush();
  }

  public static void completion(long startedNanos, long endedNanos) {
    if (enabled) {
      recordLatency(startedNanos, endedNanos);
      COMPLETED.incrementAndGet();
      COMPLETION_END_NANOS.add(endedNanos);
    }
  }

  public static void rightCensored() {
    if (enabled) RIGHT_CENSORED.incrementAndGet();
  }

  public static void finish(Results results) {
    if (!enabled) return;
    sampler.interrupt();
    try {
      sampler.join(5000L);
    } catch (InterruptedException error) {
      Thread.currentThread().interrupt();
    }
    long selectedDeadline = measurementStartedNanos + TimeUnit.SECONDS.toNanos(selectedSeconds);
    long selectedCompleted =
        COMPLETION_END_NANOS.stream().filter(ended -> ended <= selectedDeadline).count();
    long postDeadlineCompleted = COMPLETED.get() - selectedCompleted;
    System.out.println(
        "BENCHBASE_FORMAL_METRICS_JSON={\"measurement_seconds\":"
            + selectedSeconds
            + ",\"stop_requested\":"
            + stopRequested
            + ",\"stop_request_observed_epoch_ms\":"
            + (stopRequested ? Long.toString(stopObservedEpochMs) : "null")
            + ",\"completed_total\":"
            + selectedCompleted
            + ",\"post_deadline_completed_count\":"
            + postDeadlineCompleted
            + ",\"right_censored_count\":"
            + RIGHT_CENSORED.get()
            + ",\"measured_requests\":"
            + results.getMeasuredRequests()
            + ",\"unexpected_errors\":"
            + results.getError().getSampleCount()
            + ",\"aborts\":"
            + results.getAbort().getSampleCount()
            + ",\"retries\":"
            + results.getRetry().getSampleCount()
            + ",\"outputs_written\":true}");
    System.out.flush();
  }

  public static void main(String[] args) {
    if (args.length == 3 && "validate-stop-request".equals(args[0])) {
      int requested = parseStopRequest(args[1], Integer.parseInt(args[2]));
      System.out.println(
          "BENCHBASE_FORMAL_STOP_REQUEST_JSON={\"measurement_seconds\":" + requested + "}");
      return;
    }
    throw new IllegalArgumentException("unsupported FormalControl command");
  }
}
