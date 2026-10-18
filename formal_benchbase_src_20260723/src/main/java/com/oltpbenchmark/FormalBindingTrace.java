package com.oltpbenchmark;

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
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.security.MessageDigest;
import java.sql.PreparedStatement;
import java.time.Instant;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;

/** Execution-site binding trace for formal TPC-H. Disabled means no proxy and no file I/O. */
public final class FormalBindingTrace {
  private static final String SCHEMA = "formal_mysql_binding_trace_event_v1";
  private static final String RAW_PATH = System.getenv("FORMAL_MYSQL_BINDING_TRACE_PATH");
  private static final String RUN_ID = System.getenv("FORMAL_MYSQL_BINDING_TRACE_RUN_ID");
  private static final String ENV_SCHEMA = System.getenv("FORMAL_MYSQL_BINDING_TRACE_SCHEMA");
  private static final String BENCHMARK =
      environmentOrDefault("FORMAL_MYSQL_BINDING_TRACE_BENCHMARK", "tpch");
  private static final String SEED_SOURCE =
      environmentOrDefault("FORMAL_MYSQL_BINDING_TRACE_SEED_SOURCE", "TPCHWorker.rng.nextInt");
  private static final boolean ENABLED = RAW_PATH != null && !RAW_PATH.isBlank();
  private static final Path PATH = ENABLED ? Path.of(RAW_PATH).toAbsolutePath().normalize() : null;
  private static final Path LOCK =
      ENABLED ? Path.of(RAW_PATH + ".lock").toAbsolutePath().normalize() : null;
  private static final DateTimeFormatter TIMESTAMP =
      DateTimeFormatter.ofPattern("uuuu-MM-dd'T'HH:mm:ss.SSSSSS'Z'").withZone(ZoneOffset.UTC);
  private static final ConcurrentHashMap<Integer, AtomicLong> SEQUENCES = new ConcurrentHashMap<>();
  private static final ThreadLocal<Context> CONTEXT = new ThreadLocal<>();
  private static final Object APPEND_LOCK = new Object();

  private FormalBindingTrace() {}

  private static String environmentOrDefault(String name, String fallback) {
    String value = System.getenv(name);
    return value == null || value.isBlank() ? fallback : value;
  }

  public static boolean enabled() {
    if (ENABLED && (!SCHEMA.equals(ENV_SCHEMA) || RUN_ID == null || RUN_ID.isBlank())) {
      throw new IllegalStateException("invalid formal binding trace environment");
    }
    if (ENABLED && !("tpch".equals(BENCHMARK) || "tpcc".equals(BENCHMARK))) {
      throw new IllegalStateException("unsupported formal binding trace benchmark");
    }
    return ENABLED;
  }

  public static long nextSequence(int workerId) {
    if (!enabled()) return 0L;
    return SEQUENCES.computeIfAbsent(workerId, ignored -> new AtomicLong()).incrementAndGet();
  }

  public static void beginAttempt(
      int workerId,
      String queryId,
      long sequence,
      int attempt,
      long rngDrawCount,
      int derivedWorkerSeed) {
    if (!enabled()) return;
    if (CONTEXT.get() != null) throw new IllegalStateException("nested binding trace attempt");
    CONTEXT.set(new Context(workerId, queryId, sequence, attempt, rngDrawCount, derivedWorkerSeed));
  }

  public static void finishAttempt(String outcome) {
    if (!enabled()) return;
    Context context = CONTEXT.get();
    try {
      if (context != null && context.pending != null) append(context, context.pending, outcome);
    } finally {
      CONTEXT.remove();
    }
  }

  public static void abandonAttempt() {
    if (enabled()) CONTEXT.remove();
  }

  public static PreparedStatement wrap(
      PreparedStatement target, String queryId, String templateSql) {
    if (!enabled() || "Q15".equals(queryId)) return target;
    InvocationHandler handler = new PreparedHandler(target, templateSql);
    return (PreparedStatement)
        Proxy.newProxyInstance(
            target.getClass().getClassLoader(), new Class<?>[] {PreparedStatement.class}, handler);
  }

  /** Q15 substitutes its date into direct Statement SQL and is not visible to PreparedStatement. */
  public static void captureDirect(String templateSql, String executableSql, Object... bindings) {
    if (!enabled()) return;
    Context context = requireContext();
    ArrayList<Binding> values = new ArrayList<>();
    for (int index = 0; index < bindings.length; index++) {
      values.add(new Binding(index + 1, "DIRECT_SQL_LITERAL", bindings[index]));
    }
    context.pending = new Pending(templateSql, executableSql, values);
  }

  private static Context requireContext() {
    Context context = CONTEXT.get();
    if (context == null)
      throw new IllegalStateException("binding trace lacks worker attempt context");
    return context;
  }

  private static String render(String template, Map<Integer, Binding> bindings) {
    StringBuilder output = new StringBuilder();
    boolean single = false;
    int position = 1;
    for (int index = 0; index < template.length(); index++) {
      char value = template.charAt(index);
      if (value == '\'' && (index == 0 || template.charAt(index - 1) != '\\')) single = !single;
      if (value == '?' && !single) {
        Binding binding = bindings.get(position++);
        if (binding == null)
          throw new IllegalStateException("missing JDBC binding position " + (position - 1));
        output.append(sqlLiteral(binding.value));
      } else {
        output.append(value);
      }
    }
    if (bindings.size() != position - 1)
      throw new IllegalStateException("unused JDBC binding values");
    return output.toString();
  }

  private static String sqlLiteral(Object value) {
    if (value == null) return "NULL";
    if (value instanceof Number || value instanceof Boolean) return value.toString();
    return "'" + value.toString().replace("'", "''") + "'";
  }

  private static String json(Object value) {
    if (value == null) return "null";
    if (value instanceof Number || value instanceof Boolean) return value.toString();
    String text = value.toString();
    StringBuilder out = new StringBuilder("\"");
    for (int index = 0; index < text.length(); index++) {
      char c = text.charAt(index);
      switch (c) {
        case '\\' -> out.append("\\\\");
        case '"' -> out.append("\\\"");
        case '\n' -> out.append("\\n");
        case '\r' -> out.append("\\r");
        case '\t' -> out.append("\\t");
        default -> {
          if (c < 0x20) out.append(String.format("\\u%04x", (int) c));
          else out.append(c);
        }
      }
    }
    return out.append('"').toString();
  }

  private static String canonicalSql(String sql) {
    return sql.trim().replaceAll("\\s+", " ");
  }

  private static String sha256(String text) {
    try {
      byte[] digest =
          MessageDigest.getInstance("SHA-256").digest(text.getBytes(StandardCharsets.UTF_8));
      StringBuilder out = new StringBuilder(64);
      for (byte value : digest) out.append(String.format("%02x", value & 0xff));
      return out.toString();
    } catch (Exception error) {
      throw new IllegalStateException(error);
    }
  }

  private static void append(Context context, Pending pending, String outcome) {
    if (!("success".equals(outcome) || "sql_error".equals(outcome) || "aborted".equals(outcome))) {
      throw new IllegalArgumentException("invalid binding trace outcome");
    }
    String identity =
        RUN_ID
            + "\0terminal-"
            + context.workerId
            + "\0"
            + context.sequence
            + "\0"
            + context.attempt
            + "\0"
            + context.queryId;
    String eventId = sha256(identity);
    pending.bindings.sort(Comparator.comparingInt(value -> value.position));
    StringBuilder bindings = new StringBuilder("[");
    for (int index = 0; index < pending.bindings.size(); index++) {
      Binding binding = pending.bindings.get(index);
      if (binding.position != index + 1) throw new IllegalStateException("non-contiguous bindings");
      if (index > 0) bindings.append(',');
      bindings
          .append("{\"jdbc_type\":")
          .append(json(binding.jdbcType))
          .append(",\"position\":")
          .append(binding.position)
          .append(",\"value\":")
          .append(json(binding.value))
          .append('}');
    }
    bindings.append(']');
    String line =
        "{\"actual_bindings\":"
            + bindings
            + ",\"attempt\":"
            + context.attempt
            + ",\"benchmark\":"
            + json(BENCHMARK)
            + ",\"canonical_executable_sql_sha256\":"
            + json(sha256(canonicalSql(pending.executableSql)))
            + ",\"event_id\":"
            + json(eventId)
            + ",\"outcome\":"
            + json(outcome)
            + ",\"query_id\":"
            + json(context.queryId)
            + ",\"retry\":"
            + (context.attempt - 1)
            + ",\"run_id\":"
            + json(RUN_ID)
            + ",\"schema_version\":"
            + json(SCHEMA)
            + ",\"seed_derivation\":{\"derived_worker_seed\":"
            + context.derivedWorkerSeed
            + ",\"source\":"
            + json(SEED_SOURCE)
            + "}"
            + ",\"seed_state\":{\"rng_draw_count_before\":"
            + context.rngDrawCount
            + "}"
            + ",\"sequence\":"
            + context.sequence
            + ",\"template_sql_sha256\":"
            + json(sha256(canonicalSql(pending.templateSql)))
            + ",\"timestamp_utc\":"
            + json(TIMESTAMP.format(Instant.now()))
            + ",\"worker_or_terminal\":"
            + json("terminal-" + context.workerId)
            + "}\n";
    byte[] payload = line.getBytes(StandardCharsets.UTF_8);
    try {
      Files.createDirectories(PATH.getParent());
      if ((Files.exists(PATH, LinkOption.NOFOLLOW_LINKS) && Files.isSymbolicLink(PATH))
          || Files.exists(Path.of(RAW_PATH + ".manifest.json"), LinkOption.NOFOLLOW_LINKS)) {
        throw new IOException("trace is symlinked or already sealed");
      }
      synchronized (APPEND_LOCK) {
        try (FileChannel lockChannel =
            FileChannel.open(LOCK, StandardOpenOption.CREATE, StandardOpenOption.WRITE)) {
          FileLock fileLock = lockChannel.lock();
          try {
            if (Files.exists(PATH)) {
              String previous = Files.readString(PATH, StandardCharsets.UTF_8);
              if (previous.contains("\"event_id\":\"" + eventId + "\"")) {
                throw new IOException("duplicate binding trace event");
              }
            }
            try (FileChannel events =
                FileChannel.open(
                    PATH,
                    StandardOpenOption.CREATE,
                    StandardOpenOption.WRITE,
                    StandardOpenOption.APPEND)) {
              ByteBuffer buffer = ByteBuffer.wrap(payload);
              while (buffer.hasRemaining()) events.write(buffer);
              events.force(true);
            }
            try (FileChannel directory =
                FileChannel.open(PATH.getParent(), StandardOpenOption.READ)) {
              directory.force(true);
            }
          } finally {
            fileLock.release();
          }
        }
      }
    } catch (IOException error) {
      throw new IllegalStateException("cannot append formal binding trace", error);
    }
  }

  private static final class PreparedHandler implements InvocationHandler {
    private final PreparedStatement target;
    private final String templateSql;
    private final Map<Integer, Binding> bindings = new HashMap<>();

    PreparedHandler(PreparedStatement target, String templateSql) {
      this.target = target;
      this.templateSql = templateSql;
    }

    @Override
    public Object invoke(Object proxy, Method method, Object[] arguments) throws Throwable {
      String name = method.getName();
      if ("setNull".equals(name) && arguments != null && arguments.length >= 1) {
        bindings.put((Integer) arguments[0], new Binding((Integer) arguments[0], "NULL", null));
      } else if (name.startsWith("set")
          && arguments != null
          && arguments.length >= 2
          && arguments[0] instanceof Integer) {
        bindings.put(
            (Integer) arguments[0],
            new Binding((Integer) arguments[0], name.substring(3).toUpperCase(), arguments[1]));
      } else if ("clearParameters".equals(name)) {
        bindings.clear();
      }
      if (("execute".equals(name) || "executeQuery".equals(name) || "executeUpdate".equals(name))
          && (arguments == null || arguments.length == 0)) {
        Context context = requireContext();
        ArrayList<Binding> snapshot = new ArrayList<>(bindings.values());
        context.pending = new Pending(templateSql, render(templateSql, bindings), snapshot);
      }
      try {
        return method.invoke(target, arguments);
      } catch (InvocationTargetException error) {
        throw error.getCause();
      }
    }
  }

  private static final class Context {
    final int workerId;
    final String queryId;
    final long sequence;
    final int attempt;
    final long rngDrawCount;
    final int derivedWorkerSeed;
    Pending pending;

    Context(
        int workerId,
        String queryId,
        long sequence,
        int attempt,
        long rngDrawCount,
        int derivedWorkerSeed) {
      this.workerId = workerId;
      this.queryId = queryId;
      this.sequence = sequence;
      this.attempt = attempt;
      this.rngDrawCount = rngDrawCount;
      this.derivedWorkerSeed = derivedWorkerSeed;
    }
  }

  private record Binding(int position, String jdbcType, Object value) {}

  private record Pending(String templateSql, String executableSql, ArrayList<Binding> bindings) {}
}
