package com.hybench.util;

import java.io.IOException;
import java.io.BufferedReader;
import java.io.InputStream;
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
import java.nio.file.StandardCopyOption;
import java.nio.file.StandardOpenOption;
import java.security.MessageDigest;
import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.Statement;
import java.time.Instant;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashSet;
import java.util.HashMap;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Execution-site SQL, binding, and transaction trace for formal HyBench AP/XP runs. */
public final class FormalBindingTrace {
    private static final String SCHEMA = "formal_mysql_binding_trace_event_v1";
    private static final String RAW_PATH = System.getenv("FORMAL_MYSQL_BINDING_TRACE_PATH");
    private static final String RUN_ID = System.getenv("FORMAL_MYSQL_BINDING_TRACE_RUN_ID");
    private static final String ENV_SCHEMA = System.getenv("FORMAL_MYSQL_BINDING_TRACE_SCHEMA");
    private static final boolean ENABLED = RAW_PATH != null && !RAW_PATH.isBlank();
    private static final Path PATH = ENABLED ? Path.of(RAW_PATH).toAbsolutePath().normalize() : null;
    private static final Path LOCK = ENABLED ? Path.of(RAW_PATH + ".lock").toAbsolutePath().normalize() : null;
    private static final DateTimeFormatter TIMESTAMP = DateTimeFormatter
            .ofPattern("uuuu-MM-dd'T'HH:mm:ss.SSSSSS'Z'").withZone(ZoneOffset.UTC);
    private static final ConcurrentHashMap<String, AtomicLong> SEQUENCES = new ConcurrentHashMap<>();
    private static final ConcurrentHashMap<String, AtomicLong> TRANSACTION_SEQUENCES = new ConcurrentHashMap<>();
    private static final Object APPEND_MUTEX = new Object();
    private static final ThreadLocal<Worker> WORKER = new ThreadLocal<>();
    private static final ThreadLocal<Context> CONTEXT = new ThreadLocal<>();
    private static final AtomicLong ACTIVE_CONTEXTS = new AtomicLong();
    private static final AtomicLong CONNECTION_SEQUENCE = new AtomicLong();

    private FormalBindingTrace() {}

    public static boolean enabled() {
        if (ENABLED && (!SCHEMA.equals(ENV_SCHEMA) || RUN_ID == null || RUN_ID.isBlank())) {
            throw new IllegalStateException("invalid formal binding trace environment");
        }
        return ENABLED;
    }

    public static void bindWorker(String workerId, long baseSeed, long derivedWorkerSeed) {
        if (!enabled()) return;
        if (workerId == null || !workerId.matches("(?:T|XAP-|XTP-)[1-9][0-9]*")) {
            throw new IllegalArgumentException("invalid formal worker identity");
        }
        WORKER.set(new Worker(workerId, baseSeed, derivedWorkerSeed));
    }

    public static void unbindWorker() {
        if (!enabled()) return;
        if (CONTEXT.get() != null) throw new IllegalStateException("unfinished AP trace query");
        WORKER.remove();
    }

    public static void beginQuery(int queryId, long rngDrawCountBefore) {
        if (!enabled()) return;
        if (queryId < 1 || queryId > 13) throw new IllegalArgumentException("invalid AP query id");
        if (CONTEXT.get() != null) throw new IllegalStateException("nested AP trace query");
        Worker worker = requireWorker();
        CONTEXT.set(Context.ap(worker, queryId, rngDrawCountBefore));
        ACTIVE_CONTEXTS.incrementAndGet();
    }

    public static void endQuery() {
        if (!enabled()) return;
        Context context = CONTEXT.get();
        try {
            if (context == null) throw new IllegalStateException("missing AP trace query context");
            int expected = context.queryId == 2 ? 2 : 1;
            if (context.emitted != expected) {
                throw new IllegalStateException("incomplete AP-" + context.queryId
                        + " trace: expected " + expected + " SQL events, saw " + context.emitted);
            }
        } finally {
            CONTEXT.remove();
            ACTIVE_CONTEXTS.decrementAndGet();
        }
    }

    public static void beginTransaction(String transactionType, long rngDrawCountBefore) {
        if (!enabled()) return;
        if (transactionType == null || !transactionType.matches("AT[1-6]")) {
            throw new IllegalArgumentException("invalid XP transaction type");
        }
        if (CONTEXT.get() != null) throw new IllegalStateException("nested formal trace context");
        Worker worker = requireWorker();
        if (!worker.workerId.matches("XTP-[1-9][0-9]*")) {
            throw new IllegalStateException("AT transaction is not owned by an XTP worker");
        }
        long ordinal = TRANSACTION_SEQUENCES.computeIfAbsent(worker.workerId,
                ignored -> new AtomicLong()).incrementAndGet();
        Context context = Context.at(worker, transactionType, ordinal, rngDrawCountBefore);
        CONTEXT.set(context);
        ACTIVE_CONTEXTS.incrementAndGet();
        appendLifecycle(context, "transaction_begin", "success", null);
    }

    public static void recordRetry(String reason) {
        if (!enabled()) return;
        Context context = requireAtContext();
        context.retry++;
        context.attempt++;
        appendLifecycle(context, "retry", "retry", reason);
    }

    public static void recordBranch(String branch) {
        if (!enabled()) return;
        Context context = requireAtContext();
        appendLifecycle(context, "branch", "observed", branch);
    }

    public static void recordLoop(String loop, int iteration) {
        if (!enabled()) return;
        if (iteration < 1) throw new IllegalArgumentException("loop iteration must start at one");
        Context context = requireAtContext();
        appendLifecycle(context, "loop", "observed", loop + "#" + iteration);
    }

    public static void recordTechnicalFailure(String detail) {
        if (!enabled()) return;
        Context context = requireAtContext();
        appendLifecycle(context, "technical_failure", "failure", detail);
        context.terminalEvents++;
        context.technicalFailures++;
    }

    public static void endTransaction() {
        endTransaction(true);
    }

    public static void endTransaction(boolean clientReportedSuccess) {
        if (!enabled()) return;
        Context context = CONTEXT.get();
        try {
            if (context == null || !context.transaction) {
                throw new IllegalStateException("missing XP transaction context");
            }
            if ((!clientReportedSuccess || context.sqlFailures > 0) && context.technicalFailures == 0) {
                appendLifecycle(context, "technical_failure", "failure",
                        clientReportedSuccess ? "observed_statement_sql_failure" : "client_result_failed");
                context.terminalEvents++;
                context.technicalFailures++;
            }
            if (context.terminalEvents == 0) {
                throw new IllegalStateException("transaction has no commit, rollback, or technical failure");
            }
        } finally {
            CONTEXT.remove();
            ACTIVE_CONTEXTS.decrementAndGet();
        }
    }

    public static void captureDatabaseRandomValue(int value) {
        if (!enabled()) return;
        Context context = requireContext();
        if (context.queryId != 2 || context.statementOrdinal != 1 || context.emitted != 0
                || context.pendingSelector == null) {
            throw new IllegalStateException("AP-2 database RAND value was not observed after selector execution");
        }
        context.databaseRandomValue = value;
        Pending selector = context.pendingSelector;
        context.pendingSelector = null;
        append(context, selector.connectionId, selector.queryId, selector.templateSql, selector.executableSql,
                selector.bindings, "success", value);
    }

    public static void rejectDatabaseRandomSelection() {
        if (!enabled()) return;
        Context context = requireContext();
        if (context.queryId != 2 || context.pendingSelector == null) return;
        Pending selector = context.pendingSelector;
        context.pendingSelector = null;
        append(context, selector.connectionId, selector.queryId, selector.templateSql, selector.executableSql,
                selector.bindings, "aborted", null);
    }

    public static Connection wrapConnection(Connection target) {
        if (!enabled()) return target;
        if (target == null) throw new IllegalStateException("AP database connection is null");
        String connectionId = "C" + CONNECTION_SEQUENCE.incrementAndGet();
        return (Connection) Proxy.newProxyInstance(target.getClass().getClassLoader(),
                new Class<?>[] {Connection.class}, new ConnectionHandler(target, connectionId));
    }

    private static Worker requireWorker() {
        Worker worker = WORKER.get();
        if (worker == null) throw new IllegalStateException("binding trace lacks AP worker identity");
        return worker;
    }

    private static Context requireContext() {
        Context context = CONTEXT.get();
        if (context == null) throw new IllegalStateException("binding trace lacks AP query context");
        return context;
    }

    private static Context requireAtContext() {
        Context context = requireContext();
        if (!context.transaction) throw new IllegalStateException("operation requires XP transaction context");
        return context;
    }

    private static String eventQueryId(Context context) {
        if (context.transaction) return context.transactionType + "-S" + (++context.statementOrdinal);
        int ordinal = ++context.statementOrdinal;
        if (context.queryId == 2) {
            if (ordinal == 1) return "AP-2-RAND";
            if (ordinal == 2) return "AP-2";
            throw new IllegalStateException("AP-2 prepared more than two SQL statements");
        }
        if (ordinal != 1) throw new IllegalStateException("AP-" + context.queryId
                + " prepared more than one SQL statement");
        return "AP-" + context.queryId;
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
                if (binding == null) throw new IllegalStateException("missing JDBC binding position "
                        + (position - 1));
                output.append(sqlLiteral(binding.value));
            } else {
                output.append(value);
            }
        }
        if (bindings.size() != position - 1) throw new IllegalStateException("unused JDBC binding values");
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
                case '\\': out.append("\\\\"); break;
                case '"': out.append("\\\""); break;
                case '\n': out.append("\\n"); break;
                case '\r': out.append("\\r"); break;
                case '\t': out.append("\\t"); break;
                default:
                    if (c < 0x20) out.append(String.format("\\u%04x", (int)c));
                    else out.append(c);
            }
        }
        return out.append('"').toString();
    }

    private static String canonicalSql(String sql) {
        return sql.trim().replaceAll("\\s+", " ");
    }

    private static String sha256(String text) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256")
                    .digest(text.getBytes(StandardCharsets.UTF_8));
            StringBuilder out = new StringBuilder(64);
            for (byte value : digest) out.append(String.format("%02x", value & 0xff));
            return out.toString();
        } catch (Exception error) {
            throw new IllegalStateException(error);
        }
    }

    private static String sha256(Path path) throws IOException {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            try (InputStream input = Files.newInputStream(path)) {
                byte[] buffer = new byte[65536];
                for (int count; (count = input.read(buffer)) >= 0;) {
                    if (count > 0) digest.update(buffer, 0, count);
                }
            }
            StringBuilder out = new StringBuilder(64);
            for (byte value : digest.digest()) out.append(String.format("%02x", value & 0xff));
            return out.toString();
        } catch (java.security.NoSuchAlgorithmException error) {
            throw new IllegalStateException(error);
        }
    }

    private static void append(Context context, String connectionId, String queryId, String templateSql,
                               String executableSql, ArrayList<Binding> values, String outcome,
                               Integer databaseRandValue) {
        long sequence = SEQUENCES.computeIfAbsent(context.worker.workerId, ignored -> new AtomicLong())
                .incrementAndGet();
        String identity = RUN_ID + "\0" + context.worker.workerId + "\0" + sequence
                + "\0" + context.attempt + "\0" + queryId;
        String eventId = sha256(identity);
        values.sort(Comparator.comparingInt(value -> value.position));
        StringBuilder bindings = new StringBuilder("[");
        for (int index = 0; index < values.size(); index++) {
            Binding binding = values.get(index);
            if (binding.position != index + 1) throw new IllegalStateException("non-contiguous bindings");
            if (index > 0) bindings.append(',');
            bindings.append("{\"jdbc_type\":").append(json(binding.jdbcType))
                    .append(",\"position\":").append(binding.position)
                    .append(",\"value\":").append(json(binding.value)).append('}');
        }
        bindings.append(']');
        boolean databaseRandom = databaseRandValue != null;
        String line = "{\"actual_bindings\":" + bindings
                + ",\"attempt\":" + context.attempt + ",\"benchmark\":"
                + json(context.transaction ? "hybench_xp" : "hybench_ap")
                + ",\"canonical_executable_sql_sha256\":" + json(sha256(canonicalSql(executableSql)))
                + ",\"connection_id\":" + json(connectionId)
                + ",\"event_id\":" + json(eventId)
                + ",\"event_type\":\"statement\""
                + ",\"outcome\":" + json(outcome)
                + ",\"query_id\":" + json(queryId)
                + ",\"retry\":" + context.retry + ",\"run_id\":" + json(RUN_ID)
                + ",\"schema_version\":" + json(SCHEMA)
                + ",\"seed_derivation\":{\"base_random_seed\":" + context.worker.baseSeed
                + ",\"database_rand_observed\":" + databaseRandom
                + ",\"database_rand_value\":" + json(databaseRandValue)
                + ",\"derived_worker_seed\":" + context.worker.derivedWorkerSeed
                + ",\"source\":\"Client.deriveSeed(worker,ordinal)\"}"
                + ",\"seed_state\":{\"rng_draw_count_before\":" + context.rngDrawCountBefore
                + ",\"rng_draw_count_at_execute\":" + RandomGenerator.getThreadDrawCount() + "}"
                + ",\"sequence\":" + sequence
                + ",\"statement_ordinal\":" + context.statementOrdinal
                + ",\"template_sql\":" + json(canonicalSql(templateSql))
                + ",\"template_sql_sha256\":" + json(sha256(canonicalSql(templateSql)))
                + ",\"timestamp_utc\":" + json(TIMESTAMP.format(Instant.now()))
                + ",\"transaction_id\":" + json(context.transactionId)
                + ",\"transaction_type\":" + json(context.transactionType)
                + ",\"canonical_executable_sql\":" + json(canonicalSql(executableSql))
                + ",\"worker_or_terminal\":" + json(context.worker.workerId) + "}\n";
        appendLine(line, eventId);
        context.emitted++;
    }

    private static void appendLifecycle(Context context, String eventType, String outcome, String detail) {
        long sequence = SEQUENCES.computeIfAbsent(context.worker.workerId, ignored -> new AtomicLong())
                .incrementAndGet();
        String identity = RUN_ID + "\0" + context.worker.workerId + "\0" + sequence
                + "\0" + context.attempt + "\0" + eventType;
        String eventId = sha256(identity);
        String line = "{\"actual_bindings\":[],\"attempt\":" + context.attempt
                + ",\"benchmark\":\"hybench_xp\",\"canonical_executable_sql_sha256\":null"
                + ",\"connection_id\":" + json(context.connectionId)
                + ",\"detail\":" + json(detail) + ",\"event_id\":" + json(eventId)
                + ",\"event_type\":" + json(eventType) + ",\"outcome\":" + json(outcome)
                + ",\"query_id\":null,\"retry\":" + context.retry
                + ",\"run_id\":" + json(RUN_ID) + ",\"schema_version\":" + json(SCHEMA)
                + ",\"seed_derivation\":{\"base_random_seed\":" + context.worker.baseSeed
                + ",\"derived_worker_seed\":" + context.worker.derivedWorkerSeed
                + ",\"source\":\"Client.deriveSeed(worker,ordinal)\"}"
                + ",\"seed_state\":{\"rng_draw_count_before\":" + context.rngDrawCountBefore
                + ",\"rng_draw_count_at_event\":" + RandomGenerator.getThreadDrawCount() + "}"
                + ",\"sequence\":" + sequence + ",\"statement_ordinal\":" + context.statementOrdinal
                + ",\"template_sql_sha256\":null,\"timestamp_utc\":" + json(TIMESTAMP.format(Instant.now()))
                + ",\"transaction_id\":" + json(context.transactionId)
                + ",\"transaction_type\":" + json(context.transactionType)
                + ",\"worker_or_terminal\":" + json(context.worker.workerId) + "}\n";
        appendLine(line, eventId);
    }

    private static void appendLine(String line, String eventId) {
        byte[] payload = line.getBytes(StandardCharsets.UTF_8);
        try {
            Files.createDirectories(PATH.getParent());
            if ((Files.exists(PATH, LinkOption.NOFOLLOW_LINKS) && Files.isSymbolicLink(PATH))
                    || Files.exists(Path.of(RAW_PATH + ".manifest.json"), LinkOption.NOFOLLOW_LINKS)) {
                throw new IOException("trace is symlinked or already sealed");
            }
            synchronized (APPEND_MUTEX) {
                try (FileChannel lockChannel = FileChannel.open(LOCK,
                        StandardOpenOption.CREATE, StandardOpenOption.WRITE);
                     FileLock ignored = lockChannel.lock()) {
                    try (FileChannel events = FileChannel.open(PATH, StandardOpenOption.CREATE,
                            StandardOpenOption.WRITE, StandardOpenOption.APPEND)) {
                        ByteBuffer buffer = ByteBuffer.wrap(payload);
                        while (buffer.hasRemaining()) events.write(buffer);
                        events.force(true);
                    }
                    try (FileChannel directory = FileChannel.open(PATH.getParent(), StandardOpenOption.READ)) {
                        directory.force(true);
                    }
                }
            }
        } catch (IOException error) {
            throw new IllegalStateException("cannot append formal binding trace", error);
        }
    }

    private static String field(String line, String name) {
        Matcher matcher = Pattern.compile("\\\"" + Pattern.quote(name)
                + "\\\":(?:null|\\\"([^\\\"]*)\\\")").matcher(line);
        if (!matcher.find()) throw new IllegalStateException("trace event lacks " + name);
        return matcher.group(1);
    }

    /** Seal a complete XP stream. Missing joint AP/AT coverage fails closed and publishes no seal. */
    public static void sealXp() {
        if (!enabled()) return;
        if (ACTIVE_CONTEXTS.get() != 0) throw new IllegalStateException("cannot seal active trace contexts");
        Path manifest = Path.of(RAW_PATH + ".manifest.json");
        Path sidecar = Path.of(RAW_PATH + ".manifest.json.sha256");
        synchronized (APPEND_MUTEX) {
            try (FileChannel lockChannel = FileChannel.open(LOCK,
                    StandardOpenOption.CREATE, StandardOpenOption.WRITE);
                 FileLock ignored = lockChannel.lock()) {
                if (!Files.isRegularFile(PATH, LinkOption.NOFOLLOW_LINKS)
                        || Files.exists(manifest, LinkOption.NOFOLLOW_LINKS)) {
                    throw new IOException("trace is missing, unsafe, or already sealed");
                }
                Set<String> eventIds = new HashSet<>();
                Set<String> ap = new HashSet<>();
                Set<String> begun = new HashSet<>();
                Set<String> terminal = new HashSet<>();
                Set<String> committedAt = new HashSet<>();
                Set<String> workers = new HashSet<>();
                long eventCount = 0;
                try (BufferedReader reader = Files.newBufferedReader(PATH, StandardCharsets.UTF_8)) {
                  for (String line; (line = reader.readLine()) != null;) {
                    eventCount++;
                    if (!line.contains("\"schema_version\":\"" + SCHEMA + "\"")
                            || !line.contains("\"run_id\":" + json(RUN_ID))) {
                        throw new IOException("foreign or malformed trace event");
                    }
                    String eventId = field(line, "event_id");
                    if (!eventIds.add(eventId)) throw new IOException("duplicate trace event id");
                    String worker = field(line, "worker_or_terminal");
                    workers.add(worker);
                    String eventType = field(line, "event_type");
                    String queryId = field(line, "query_id");
                    String transactionId = field(line, "transaction_id");
                    String transactionType = field(line, "transaction_type");
                    String outcome = field(line, "outcome");
                    if ("statement".equals(eventType) && queryId != null && queryId.matches("AP-(?:[1-9]|1[0-3])")) {
                        ap.add(queryId);
                    }
                    if ("transaction_begin".equals(eventType)) begun.add(transactionId);
                    if ("commit".equals(eventType) || "rollback".equals(eventType)
                            || "technical_failure".equals(eventType)) terminal.add(transactionId);
                    if ("commit".equals(eventType) && "success".equals(outcome)
                            && transactionType != null && transactionType.matches("AT[1-6]")) {
                        committedAt.add(transactionType);
                    }
                  }
                }
                Set<String> requiredAp = new HashSet<>();
                Set<String> requiredAt = new HashSet<>();
                Set<String> requiredWorkers = new HashSet<>();
                for (int index = 1; index <= 13; index++) requiredAp.add("AP-" + index);
                for (int index = 1; index <= 6; index++) requiredAt.add("AT" + index);
                for (int index = 1; index <= 8; index++) {
                    requiredWorkers.add("XAP-" + index);
                    requiredWorkers.add("XTP-" + index);
                }
                if (!ap.containsAll(requiredAp) || !committedAt.containsAll(requiredAt)
                        || !terminal.containsAll(begun) || !workers.containsAll(requiredWorkers)) {
                    throw new IOException("incomplete AP/AT/worker/terminal coverage; retain real replay");
                }
                String traceSha = sha256(PATH);
                String payload = "{\"ap_query_coverage\":13,\"at_committed_coverage\":6"
                        + ",\"event_count\":" + eventCount + ",\"run_id\":" + json(RUN_ID)
                        + ",\"schema_version\":\"formal_mysql_binding_trace_manifest_v1\""
                        + ",\"status\":\"SEALED\",\"trace_sha256\":" + json(traceSha)
                        + ",\"worker_coverage\":16}\n";
                String manifestSha = sha256(payload);
                Path temporary = manifest.resolveSibling(manifest.getFileName() + ".partial");
                Path temporarySidecar = sidecar.resolveSibling(sidecar.getFileName() + ".partial");
                try (FileChannel channel = FileChannel.open(temporary, StandardOpenOption.CREATE_NEW,
                        StandardOpenOption.WRITE)) {
                    channel.write(ByteBuffer.wrap(payload.getBytes(StandardCharsets.UTF_8)));
                    channel.force(true);
                }
                String sidecarPayload = manifestSha + "  " + manifest.getFileName() + "\n";
                try (FileChannel channel = FileChannel.open(temporarySidecar, StandardOpenOption.CREATE_NEW,
                        StandardOpenOption.WRITE)) {
                    channel.write(ByteBuffer.wrap(sidecarPayload.getBytes(StandardCharsets.US_ASCII)));
                    channel.force(true);
                }
                Files.move(temporarySidecar, sidecar, StandardCopyOption.ATOMIC_MOVE);
                Files.move(temporary, manifest, StandardCopyOption.ATOMIC_MOVE);
                try (FileChannel directory = FileChannel.open(PATH.getParent(), StandardOpenOption.READ)) {
                    directory.force(true);
                }
            } catch (IOException error) {
                throw new IllegalStateException("cannot seal complete formal XP binding trace", error);
            }
        }
    }

    private static final class ConnectionHandler implements InvocationHandler {
        private final Connection target;
        private final String connectionId;

        ConnectionHandler(Connection target, String connectionId) {
            this.target = target;
            this.connectionId = connectionId;
        }

        @Override
        public Object invoke(Object proxy, Method method, Object[] arguments) throws Throwable {
            String name = method.getName();
            Context active = CONTEXT.get();
            if (active != null && active.transaction && ("commit".equals(name) || "rollback".equals(name))) {
                active.connectionId = connectionId;
                try {
                    Object result = method.invoke(target, arguments);
                    appendLifecycle(active, name, "success", null);
                    active.terminalEvents++;
                    return result;
                } catch (InvocationTargetException error) {
                    appendLifecycle(active, "technical_failure", "sql_error",
                            error.getCause().getClass().getName());
                    active.terminalEvents++;
                    active.technicalFailures++;
                    throw error.getCause();
                }
            }
            try {
                Object result = method.invoke(target, arguments);
                if ("prepareStatement".equals(name) && arguments != null
                        && arguments.length > 0 && arguments[0] instanceof String
                        && result instanceof PreparedStatement && CONTEXT.get() != null) {
                    Context context = requireContext();
                    context.connectionId = connectionId;
                    String eventQueryId = context.transaction ? null : eventQueryId(context);
                    return Proxy.newProxyInstance(result.getClass().getClassLoader(),
                            new Class<?>[] {PreparedStatement.class},
                            new PreparedHandler((PreparedStatement)result, (String)arguments[0], eventQueryId,
                                    connectionId));
                }
                if ("createStatement".equals(name) && result instanceof Statement
                        && CONTEXT.get() != null && CONTEXT.get().transaction) {
                    CONTEXT.get().connectionId = connectionId;
                    return Proxy.newProxyInstance(result.getClass().getClassLoader(),
                            new Class<?>[] {Statement.class},
                            new StatementHandler((Statement)result, connectionId));
                }
                return result;
            } catch (InvocationTargetException error) {
                throw error.getCause();
            }
        }
    }

    private static final class PreparedHandler implements InvocationHandler {
        private final PreparedStatement target;
        private final String templateSql;
        private final String eventQueryId;
        private final String connectionId;
        private final Map<Integer, Binding> bindings = new HashMap<>();

        PreparedHandler(PreparedStatement target, String templateSql, String eventQueryId,
                        String connectionId) {
            this.target = target;
            this.templateSql = templateSql;
            this.eventQueryId = eventQueryId;
            this.connectionId = connectionId;
        }

        @Override
        public Object invoke(Object proxy, Method method, Object[] arguments) throws Throwable {
            String name = method.getName();
            if ("setNull".equals(name) && arguments != null && arguments.length >= 1) {
                String nullType = arguments.length >= 2 ? "NULL_JDBC_TYPE_" + arguments[1] : "NULL";
                bindings.put((Integer)arguments[0], new Binding((Integer)arguments[0], nullType, null));
            } else if (name.startsWith("set") && arguments != null && arguments.length >= 2
                    && arguments[0] instanceof Integer) {
                bindings.put((Integer)arguments[0], new Binding((Integer)arguments[0],
                        name.substring(3).toUpperCase(), arguments[1]));
            } else if ("clearParameters".equals(name)) {
                bindings.clear();
            }
            boolean execute = ("execute".equals(name) || "executeQuery".equals(name)
                    || "executeUpdate".equals(name)) && (arguments == null || arguments.length == 0);
            if (!execute) {
                try {
                    return method.invoke(target, arguments);
                } catch (InvocationTargetException error) {
                    throw error.getCause();
                }
            }
            Context context = requireContext();
            String currentQueryId = eventQueryId == null ? eventQueryId(context) : eventQueryId;
            if ("AP-2".equals(currentQueryId)) {
                Binding chosen = bindings.get(1);
                if (context.databaseRandomValue == null || chosen == null
                        || !(chosen.value instanceof Number)
                        || ((Number)chosen.value).intValue() != context.databaseRandomValue) {
                    throw new IllegalStateException("AP-2 main SQL lacks authoritative database RAND binding");
                }
            }
            String executableSql = render(templateSql, bindings);
            ArrayList<Binding> snapshot = new ArrayList<>(bindings.values());
            try {
                Object result = method.invoke(target, arguments);
                if ("AP-2-RAND".equals(currentQueryId)) {
                    if (!(result instanceof java.sql.ResultSet)) {
                        throw new IllegalStateException("AP-2 RAND selector returned no ResultSet");
                    }
                    context.pendingSelector = new Pending(currentQueryId, connectionId, templateSql,
                            executableSql, snapshot);
                } else {
                    append(context, connectionId, currentQueryId, templateSql, executableSql, snapshot,
                            "success", "AP-2".equals(currentQueryId) ? context.databaseRandomValue : null);
                }
                return result;
            } catch (InvocationTargetException error) {
                Throwable cause = error.getCause();
                context.sqlFailures++;
                append(context, connectionId, currentQueryId, templateSql, executableSql, snapshot,
                        cause instanceof java.sql.SQLException ? "sql_error" : "aborted", null);
                throw cause;
            }
        }
    }

    private static final class StatementHandler implements InvocationHandler {
        private final Statement target;
        private final String connectionId;

        StatementHandler(Statement target, String connectionId) {
            this.target = target;
            this.connectionId = connectionId;
        }

        @Override
        public Object invoke(Object proxy, Method method, Object[] arguments) throws Throwable {
            String name = method.getName();
            boolean execute = ("execute".equals(name) || "executeQuery".equals(name)
                    || "executeUpdate".equals(name)) && arguments != null && arguments.length > 0
                    && arguments[0] instanceof String;
            if (!execute) {
                try { return method.invoke(target, arguments); }
                catch (InvocationTargetException error) { throw error.getCause(); }
            }
            Context context = requireAtContext();
            String sql = (String)arguments[0];
            String queryId = eventQueryId(context);
            try {
                Object result = method.invoke(target, arguments);
                append(context, connectionId, queryId, sql, sql, new ArrayList<>(), "success", null);
                return result;
            } catch (InvocationTargetException error) {
                Throwable cause = error.getCause();
                context.sqlFailures++;
                append(context, connectionId, queryId, sql, sql, new ArrayList<>(),
                        cause instanceof java.sql.SQLException ? "sql_error" : "aborted", null);
                throw cause;
            }
        }
    }

    private static final class Worker {
        final String workerId;
        final long baseSeed;
        final long derivedWorkerSeed;

        Worker(String workerId, long baseSeed, long derivedWorkerSeed) {
            this.workerId = workerId;
            this.baseSeed = baseSeed;
            this.derivedWorkerSeed = derivedWorkerSeed;
        }
    }

    private static final class Context {
        final Worker worker;
        final int queryId;
        final boolean transaction;
        final String transactionType;
        final String transactionId;
        final long rngDrawCountBefore;
        int attempt = 1;
        int retry;
        int statementOrdinal;
        int emitted;
        int terminalEvents;
        int sqlFailures;
        int technicalFailures;
        String connectionId;
        Integer databaseRandomValue;
        Pending pendingSelector;

        private Context(Worker worker, int queryId, boolean transaction, String transactionType,
                        String transactionId, long rngDrawCountBefore) {
            this.worker = worker;
            this.queryId = queryId;
            this.transaction = transaction;
            this.transactionType = transactionType;
            this.transactionId = transactionId;
            this.rngDrawCountBefore = rngDrawCountBefore;
        }

        static Context ap(Worker worker, int queryId, long rngDrawCountBefore) {
            return new Context(worker, queryId, false, "AP-" + queryId,
                    sha256(RUN_ID + "\0" + worker.workerId + "\0AP-" + queryId + "\0"
                            + rngDrawCountBefore), rngDrawCountBefore);
        }

        static Context at(Worker worker, String type, long ordinal, long rngDrawCountBefore) {
            return new Context(worker, 0, true, type,
                    sha256(RUN_ID + "\0" + worker.workerId + "\0" + type + "\0" + ordinal),
                    rngDrawCountBefore);
        }
    }

    private static final class Pending {
        final String queryId;
        final String connectionId;
        final String templateSql;
        final String executableSql;
        final ArrayList<Binding> bindings;

        Pending(String queryId, String connectionId, String templateSql, String executableSql,
                ArrayList<Binding> bindings) {
            this.queryId = queryId;
            this.connectionId = connectionId;
            this.templateSql = templateSql;
            this.executableSql = executableSql;
            this.bindings = bindings;
        }
    }

    private static final class Binding {
        final int position;
        final String jdbcType;
        final Object value;

        Binding(int position, String jdbcType, Object value) {
            this.position = position;
            this.jdbcType = jdbcType;
            this.value = value;
        }
    }
}
