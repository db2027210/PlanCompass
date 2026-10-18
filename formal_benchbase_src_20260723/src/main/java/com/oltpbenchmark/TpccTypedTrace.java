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
import java.nio.file.attribute.PosixFilePermission;
import java.security.MessageDigest;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.sql.Types;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.WeakHashMap;
import java.util.concurrent.atomic.AtomicLong;

/**
 * Capture-only TPC-C JDBC typed bindings and statement-level Performance Schema metrics.
 *
 * <p>This class is inert unless MYSQL_TPCC_TYPED_TRACE_PATH is set. It never emits QPS and never
 * authorizes an outer replay. Failed and rolled-back transactions are discarded; only transactions
 * whose database commit completed are written to the trace.
 */
public final class TpccTypedTrace {
  private static final String SCHEMA = "mysql_tpcc_jdbc_typed_trace_v1";
  private static final String RAW_PATH = System.getenv("MYSQL_TPCC_TYPED_TRACE_PATH");
  private static final String MANIFEST_PATH = System.getenv("MYSQL_TPCC_TYPED_MANIFEST_PATH");
  private static final String MANIFEST_SHA = System.getenv("MYSQL_TPCC_TYPED_MANIFEST_SHA256");
  private static final String CAPTURE_TOKEN = System.getenv("MYSQL_TPCC_TYPED_CAPTURE_TOKEN");
  private static final String METRICS_URL = System.getenv("MYSQL_TPCC_TYPED_METRICS_JDBC_URL");
  private static final String DEFAULTS_FILE = System.getenv("MYSQL_TPCC_TYPED_DEFAULTS_FILE");
  private static final String JOIN_AUTHORITY_PATH =
      System.getenv("MYSQL_TPCC_TYPED_JOIN_AUTHORITY_PATH");
  private static final String JOIN_AUTHORITY_SHA =
      System.getenv("MYSQL_TPCC_TYPED_JOIN_AUTHORITY_SHA256");
  private static final Path SEALED_DEFAULTS_FILE =
      Path.of("/opt/mysql-tpcc-config/mysql.cnf").toAbsolutePath().normalize();
  private static final String TRACE_ROLE = System.getenv("MYSQL_TPCC_TYPED_TRACE_ROLE");
  private static final boolean ENABLED = RAW_PATH != null && !RAW_PATH.isBlank();
  private static final Path PATH = ENABLED ? Path.of(RAW_PATH).toAbsolutePath().normalize() : null;
  private static final Path LOCK =
      ENABLED ? Path.of(RAW_PATH + ".lock").toAbsolutePath().normalize() : null;
  private static final ThreadLocal<TransactionContext> CONTEXT = new ThreadLocal<>();
  private static final ThreadLocal<Connection> METRICS_CONNECTION = new ThreadLocal<>();
  private static final Map<Connection, Long> TARGET_CONNECTION_IDS = new WeakHashMap<>();
  private static final Map<Long, Long> TARGET_THREAD_IDS = new HashMap<>();
  private static final Map<String, Authority> AUTHORITIES = new LinkedHashMap<>();
  private static final AtomicLong TRANSACTION_SEQUENCE = new AtomicLong();
  private static volatile boolean environmentValidated;
  private static volatile JdbcCredentials defaultsCredentials;

  static {
    authority(
        1,
        "NewOrder",
        "stmtGetCustSQL",
        3,
        "3792fcbcc0fd9dce4cd8d1ba1b123f82bb4861a77c75a52d211a52183e6170f5");
    authority(
        2,
        "NewOrder",
        "stmtGetWhseSQL",
        1,
        "0691e3ff293051026196e34d534b8a5333bb77c11b82c977a304ea17874d1d8e");
    authority(
        3,
        "NewOrder",
        "stmtGetDistSQL",
        2,
        "d6f6cc89a20e79c1061253958b0521d817f03e1681b2c37779f0e30d12833f87");
    authority(
        4,
        "NewOrder",
        "stmtInsertNewOrderSQL",
        3,
        "7929b33b9c50e8d0bfc8a4d3a772edbcc82d4d54f629802e6311dfe246c534b3");
    authority(
        5,
        "NewOrder",
        "stmtUpdateDistSQL",
        2,
        "9d405e094da765b19aa875fda1be9427383df9ae13c0a22d316c9d88b6a93695");
    authority(
        6,
        "NewOrder",
        "stmtInsertOOrderSQL",
        7,
        "7c2850ca2539f1291a7a89fd21b33d1057ed1b8aaefc92a1014d0ba28ad76ebd");
    authority(
        7,
        "NewOrder",
        "stmtGetItemSQL",
        1,
        "0274b8d6a6ffd9c1228eeb841fc166d606b3b6d8697d56f6b81457341c35a418");
    authority(
        8,
        "NewOrder",
        "stmtGetStockSQL",
        2,
        "423381cfa59aad3332b30a16046e94edcc3df1aea5e36a83d76fa2cdc916002d");
    authority(
        9,
        "NewOrder",
        "stmtUpdateStockSQL",
        5,
        "b3dd5596adb6506e545ada0582326ea32dbbf47e0d191d02f6cd59be09f68081");
    authority(
        10,
        "NewOrder",
        "stmtInsertOrderLineSQL",
        9,
        "36e468f6a4754df23fdb7943109212ade853dc8852e479499d63f6268ac79691");
    authority(
        11,
        "Payment",
        "payUpdateWhseSQL",
        2,
        "90d08ab8872fdfc6be0af5e2279ff141d5127349d1fb10ae6a601bfc12ade34b");
    authority(
        12,
        "Payment",
        "payGetWhseSQL",
        1,
        "d5b6c4753bee6d190489c73d9ff10931b2dd7fe35465163a84c536f871392b3f");
    authority(
        13,
        "Payment",
        "payUpdateDistSQL",
        3,
        "5279819ccd560ac9aac5e9eee2672a06ba1f16a3cf600c18535c3180ddcd9b9b");
    authority(
        14,
        "Payment",
        "payGetDistSQL",
        2,
        "8b1c671e31891b93d42b10d936dcd24a4c9a6f125b37c4a0d9926d175c5b4dc4");
    authority(
        15,
        "Payment",
        "payGetCustSQL",
        3,
        "292209c5348c5714f02108845fd93b4f17cbc6cfd52c37c20ca80ce60cee7ab6");
    authority(
        16,
        "Payment",
        "payGetCustCdataSQL",
        3,
        "4f0d9ab3b7b54cc74910c8bcdfb5b7b2e52755b2108060f0d163a42b089a33a4");
    authority(
        17,
        "Payment",
        "payUpdateCustBalCdataSQL",
        7,
        "7bebb00ee35db40cf40eb124c8b5e9a41cd65c5b71f2a21f3f6cf7be82fcdbb4");
    authority(
        18,
        "Payment",
        "payUpdateCustBalSQL",
        6,
        "642052c85c14a5a6839cc7dea171e9a9a7b37f31d3855369d9daa2b3927b5c1c");
    authority(
        19,
        "Payment",
        "payInsertHistSQL",
        8,
        "9a932980676c7aa927f05ff244d3cea531956e3af5770dc0ea7e8c964607764c");
    authority(
        20,
        "Payment",
        "customerByNameSQL",
        3,
        "178abd93e6588eec7ea59fe17684e479166f9f23f490293b072356b44e0cc44f");
    authority(
        21,
        "OrderStatus",
        "ordStatGetNewestOrdSQL",
        3,
        "7c65dae4d8aad99fea5db9556437fa7c9af117860e12af76f984bbf96766c95b");
    authority(
        22,
        "OrderStatus",
        "ordStatGetOrderLinesSQL",
        3,
        "9b1318bc23bcb657bb5d624ade3c157f2c735308d2f28ed0db39d6c701fe0999");
    authority(
        23,
        "OrderStatus",
        "payGetCustSQL",
        3,
        "292209c5348c5714f02108845fd93b4f17cbc6cfd52c37c20ca80ce60cee7ab6");
    authority(
        24,
        "OrderStatus",
        "customerByNameSQL",
        3,
        "178abd93e6588eec7ea59fe17684e479166f9f23f490293b072356b44e0cc44f");
    authority(
        25,
        "Delivery",
        "delivGetOrderIdSQL",
        2,
        "9afa66f3f27a620ebcd48f9cab27240c7f49bbee244533dc0a4c98d9513a7051");
    authority(
        26,
        "Delivery",
        "delivDeleteNewOrderSQL",
        3,
        "38ecfdd1f1590627c5e50c7afeab67208bcba419f1a403fec0fefeafb5c5b807");
    authority(
        27,
        "Delivery",
        "delivGetCustIdSQL",
        3,
        "8a467b6dca3ff54618a35fe9b8f7c0aeddccf0197d7bb483e3a3bed114a061d5");
    authority(
        28,
        "Delivery",
        "delivUpdateCarrierIdSQL",
        4,
        "93143c53e36fafda7929bbfc939142626f429d7ead7ccea8f451f5e2809107c7");
    authority(
        29,
        "Delivery",
        "delivUpdateDeliveryDateSQL",
        4,
        "2671294a6214ff027c541f7c29ef2ab55e5f784ea1360137b17c7d83717161ac");
    authority(
        30,
        "Delivery",
        "delivSumOrderAmountSQL",
        3,
        "76751a2ae18042754d289fc822e715c6dfd58057e3d8716b4abf7a1dcbce6925");
    authority(
        31,
        "Delivery",
        "delivUpdateCustBalDelivCntSQL",
        4,
        "7c23f803aa778bf20703f216a8572bb8170c1488a9028c7c5d58fe0bd91d6b57");
    authority(
        32,
        "StockLevel",
        "stockGetDistOrderIdSQL",
        2,
        "794b750f9d4b4eee6f35246d8346faa0080928843b68a06a630caf201f6f7781");
    authority(
        33,
        "StockLevel",
        "stockGetCountStockSQL",
        6,
        "490a60850248cacfe11aac16b3f696084619ea9db4d478da33d9097db5b1765a");
    specialAuthority(
        34,
        "StockLevel",
        "SensitiveStockLevel",
        "SensitiveStockLevel/item-in-orderline-w1",
        0,
        "d727716a2be8da74ec08cf8cf87cbcc342fa3518ef40281fcc86ca9233c60c6c");
  }

  private TpccTypedTrace() {}

  private static void authority(
      int order, String transaction, String statement, int parameters, String hash) {
    Authority value = new Authority(order, transaction, transaction, statement, parameters, hash);
    if (AUTHORITIES.put(transaction + "\0" + statement, value) != null) {
      throw new ExceptionInInitializerError("duplicate TPC-C typed authority");
    }
  }

  private static void specialAuthority(
      int order,
      String normalizedTransaction,
      String bindingTransaction,
      String statement,
      int parameters,
      String hash) {
    Authority value =
        new Authority(
            order, normalizedTransaction, bindingTransaction, statement, parameters, hash);
    if (AUTHORITIES.put(normalizedTransaction + "\0" + statement, value) != null) {
      throw new ExceptionInInitializerError("duplicate TPC-C typed authority");
    }
  }

  public static boolean enabled() {
    if (!ENABLED) return false;
    validateEnvironment();
    return true;
  }

  private static synchronized void validateEnvironment() {
    if (environmentValidated) return;
    if (CAPTURE_TOKEN == null
        || CAPTURE_TOKEN.isBlank()
        || MANIFEST_PATH == null
        || MANIFEST_PATH.isBlank()
        || MANIFEST_SHA == null
        || !MANIFEST_SHA.matches("[0-9a-f]{64}")
        || METRICS_URL == null
        || METRICS_URL.isBlank()
        || DEFAULTS_FILE == null
        || DEFAULTS_FILE.isBlank()
        || (!"OWNER".equals(TRACE_ROLE) && !"JOINER".equals(TRACE_ROLE))
        || ("JOINER".equals(TRACE_ROLE)
            && (JOIN_AUTHORITY_PATH == null
                || JOIN_AUTHORITY_PATH.isBlank()
                || JOIN_AUTHORITY_SHA == null
                || !JOIN_AUTHORITY_SHA.matches("[0-9a-f]{64}")))
        || !"1".equals(System.getenv("MYSQL_TPCC_TYPED_CORRECTNESS_VERIFIED"))
        || !"1".equals(System.getenv("MYSQL_TPCC_TYPED_CAPTURE_ONLY"))
        || !"0".equals(System.getenv("MYSQL_TPCC_TYPED_OUTER_AUTHORIZED"))) {
      throw new IllegalStateException("incomplete fail-closed TPC-C typed trace environment");
    }
    Path manifest = Path.of(MANIFEST_PATH).toAbsolutePath().normalize();
    try {
      if (Files.isSymbolicLink(manifest)
          || !Files.isRegularFile(manifest, LinkOption.NOFOLLOW_LINKS)
          || !MANIFEST_SHA.equals(sha256(Files.readAllBytes(manifest)))) {
        throw new IllegalStateException("TPC-C typed manifest identity mismatch");
      }
      if ("OWNER".equals(TRACE_ROLE)) {
        if (Files.exists(PATH, LinkOption.NOFOLLOW_LINKS)
            || Files.isSymbolicLink(PATH)
            || Files.exists(LOCK, LinkOption.NOFOLLOW_LINKS)
            || Files.isSymbolicLink(LOCK)) {
          throw new IllegalStateException("TPC-C typed trace output is not fresh");
        }
      } else {
        Path joinAuthority = Path.of(JOIN_AUTHORITY_PATH).toAbsolutePath().normalize();
        Path expectedJoinAuthority =
            PATH.resolveSibling(PATH.getFileName() + ".sensitive_join_authority.json");
        if (Files.isSymbolicLink(PATH)
            || !Files.isRegularFile(PATH, LinkOption.NOFOLLOW_LINKS)
            || Files.isSymbolicLink(LOCK)
            || !Files.isRegularFile(LOCK, LinkOption.NOFOLLOW_LINKS)
            || !expectedJoinAuthority.equals(joinAuthority)
            || Files.isSymbolicLink(joinAuthority)
            || !Files.isRegularFile(joinAuthority, LinkOption.NOFOLLOW_LINKS)) {
          throw new IllegalStateException("TPC-C typed trace join authority is absent");
        }
        byte[] joinAuthorityBytes = Files.readAllBytes(joinAuthority);
        String joinAuthorityJson = new String(joinAuthorityBytes, StandardCharsets.UTF_8);
        if (!JOIN_AUTHORITY_SHA.equals(sha256(joinAuthorityBytes))
            || !joinAuthorityJson.contains(
                "\"schema_version\":\"mysql_tpcc_sensitive_stock_level_real_join_authority_v1\"")
            || !joinAuthorityJson.contains("\"status\":\"PASS_REAL_OWNER_TRACE_JOIN\"")
            || !joinAuthorityJson.contains(
                "\"binding_id\":\"34:SensitiveStockLevel:"
                    + "SensitiveStockLevel/item-in-orderline-w1\"")
            || !joinAuthorityJson.contains("\"capture_token\":" + json(CAPTURE_TOKEN))
            || !joinAuthorityJson.contains("\"manifest_sha256\":" + json(MANIFEST_SHA))
            || !joinAuthorityJson.contains("\"qps_resource_input_prohibited\":true")
            || !joinAuthorityJson.contains("\"synthetic_or_fabricated_join_prohibited\":true")
            || !joinAuthorityJson.contains("\"outer_execution_authorized\":false")
            || !joinAuthorityJson.contains("\"accepted_round_authorized\":false")) {
          throw new IllegalStateException("TPC-C typed trace join authority differs");
        }
        String expectedPrefix =
            "{\"record_type\":\"header\",\"schema_version\":"
                + json(SCHEMA)
                + ",\"manifest_sha256\":"
                + json(MANIFEST_SHA)
                + ",\"capture_token\":"
                + json(CAPTURE_TOKEN)
                + ",";
        try (var reader = Files.newBufferedReader(PATH, StandardCharsets.UTF_8)) {
          String header = reader.readLine();
          if (header == null
              || !header.startsWith(expectedPrefix)
              || !header.contains("\"capture_only\":true")
              || !header.contains("\"outer_execution_authorized\":false")
              || !header.contains("\"correctness_verified\":true")
              || !header.contains("\"qps_included\":false")) {
            throw new IllegalStateException("TPC-C typed trace join header differs");
          }
        }
      }
    } catch (IOException error) {
      throw new IllegalStateException("cannot validate TPC-C typed trace environment", error);
    }
    if (AUTHORITIES.size() != 34) {
      throw new IllegalStateException("TPC-C typed authority must contain 34 statements");
    }
    environmentValidated = true;
  }

  public static void beginTransaction(int workerId, String transactionType, Connection target)
      throws SQLException {
    if (!enabled()) return;
    if (!Set.of("Delivery", "NewOrder", "OrderStatus", "Payment", "StockLevel")
        .contains(transactionType)) {
      throw new SQLException("unsupported TPC-C typed transaction: " + transactionType);
    }
    if (CONTEXT.get() != null) throw new SQLException("nested TPC-C typed transaction");
    Connection metrics = metricsConnection();
    long connectionId = targetConnectionId(target);
    long threadId = targetThreadId(metrics, connectionId);
    long sequence = TRANSACTION_SEQUENCE.incrementAndGet();
    String identity =
        CAPTURE_TOKEN
            + "\0"
            + ProcessHandle.current().pid()
            + "\0"
            + workerId
            + "\0"
            + sequence
            + "\0"
            + transactionType;
    CONTEXT.set(
        new TransactionContext(
            sha256(identity.getBytes(StandardCharsets.UTF_8)),
            transactionType,
            workerId,
            System.nanoTime(),
            threadId,
            metrics));
  }

  public static void commitTransaction() {
    if (!enabled()) return;
    TransactionContext context = requireContext();
    try {
      if (context.statements.isEmpty()) {
        throw new IllegalStateException("committed TPC-C typed transaction has no statements");
      }
      context.endedMonotonicNs = System.nanoTime();
      appendCommitted(context);
    } finally {
      CONTEXT.remove();
    }
  }

  public static void abortTransaction() {
    if (ENABLED) CONTEXT.remove();
  }

  public static void captureSensitiveStockLevel(Connection target, String templateSql)
      throws SQLException {
    beginTransaction(-1, "StockLevel", target);
    boolean originalAutoCommit = target.getAutoCommit();
    try {
      target.setAutoCommit(false);
      try (PreparedStatement statement =
              wrap(
                  target.prepareStatement(templateSql),
                  "StockLevel",
                  "SensitiveStockLevel/item-in-orderline-w1",
                  templateSql);
          ResultSet rows = statement.executeQuery()) {
        while (rows.next()) {
          // Consume the complete result so timing and Performance Schema attribution
          // cover the actual production query, not only server-side dispatch.
        }
      }
      target.commit();
      commitTransaction();
    } catch (SQLException | RuntimeException error) {
      abortTransaction();
      try {
        target.rollback();
      } catch (SQLException rollbackError) {
        error.addSuppressed(rollbackError);
      }
      throw error;
    } finally {
      target.setAutoCommit(originalAutoCommit);
    }
  }

  public static PreparedStatement wrap(
      PreparedStatement target, String transactionType, String statementId, String templateSql) {
    if (!enabled()) return target;
    Authority authority = AUTHORITIES.get(transactionType + "\0" + statementId);
    if (authority == null) {
      throw new IllegalStateException(
          "statement is absent from TPC-C typed authority: " + transactionType + "." + statementId);
    }
    String observed = sha256(canonicalSql(templateSql).getBytes(StandardCharsets.UTF_8));
    if (!authority.templateHash.equals(observed)) {
      throw new IllegalStateException(
          "TPC-C SQL template hash mismatch: " + transactionType + "." + statementId);
    }
    InvocationHandler handler = new PreparedHandler(target, authority);
    return (PreparedStatement)
        Proxy.newProxyInstance(
            target.getClass().getClassLoader(), new Class<?>[] {PreparedStatement.class}, handler);
  }

  private static TransactionContext requireContext() {
    TransactionContext context = CONTEXT.get();
    if (context == null)
      throw new IllegalStateException("TPC-C statement lacks transaction context");
    return context;
  }

  private static Connection metricsConnection() throws SQLException {
    Connection connection = METRICS_CONNECTION.get();
    if (connection == null || connection.isClosed()) {
      connection = connectionFromSealedDefaults(METRICS_URL);
      connection.setReadOnly(true);
      connection.setAutoCommit(true);
      validatePerformanceSchema(connection);
      METRICS_CONNECTION.set(connection);
    }
    return connection;
  }

  /**
   * Opens the typed metrics/sensitive-query JDBC connection using only the client-private defaults
   * file mounted at the sealed config path. Credentials are never accepted from environment
   * variables, command arguments, trace records, or manifests.
   */
  public static Connection connectionFromSealedDefaults(String jdbcUrl) throws SQLException {
    if (jdbcUrl == null || jdbcUrl.isBlank()) {
      throw new SQLException("TPC-C typed JDBC URL is absent");
    }
    JdbcCredentials credentials = defaultsCredentials();
    return DriverManager.getConnection(jdbcUrl, credentials.user, credentials.password);
  }

  private static JdbcCredentials defaultsCredentials() {
    JdbcCredentials cached = defaultsCredentials;
    if (cached != null) return cached;
    synchronized (TpccTypedTrace.class) {
      cached = defaultsCredentials;
      if (cached == null) {
        cached = readSealedDefaults();
        defaultsCredentials = cached;
      }
      return cached;
    }
  }

  private static JdbcCredentials readSealedDefaults() {
    Path configured;
    try {
      configured = Path.of(DEFAULTS_FILE).toAbsolutePath().normalize();
    } catch (RuntimeException error) {
      throw new IllegalStateException("TPC-C typed defaults path is invalid", error);
    }
    if (!SEALED_DEFAULTS_FILE.equals(configured)
        || Files.isSymbolicLink(configured)
        || !Files.isRegularFile(configured, LinkOption.NOFOLLOW_LINKS)) {
      throw new IllegalStateException("TPC-C typed defaults authority differs");
    }
    try {
      for (PosixFilePermission permission : Files.getPosixFilePermissions(configured)) {
        if (permission.name().startsWith("GROUP_") || permission.name().startsWith("OTHERS_")) {
          throw new IllegalStateException("TPC-C typed defaults permissions differ");
        }
      }
      String user = null;
      String password = null;
      boolean inClientSection = false;
      for (String raw : Files.readAllLines(configured, StandardCharsets.UTF_8)) {
        if (raw.indexOf('\u0000') >= 0 || raw.length() > 4096) {
          throw new IllegalStateException("TPC-C typed defaults syntax differs");
        }
        String line = raw.trim();
        if (line.isEmpty() || line.startsWith("#") || line.startsWith(";")) continue;
        if (line.startsWith("[") && line.endsWith("]")) {
          inClientSection = "[client]".equalsIgnoreCase(line);
          continue;
        }
        if (!inClientSection) continue;
        int equals = line.indexOf('=');
        if (equals <= 0 || line.indexOf('=', equals + 1) >= 0) {
          throw new IllegalStateException("TPC-C typed defaults syntax differs");
        }
        String key = line.substring(0, equals).trim().toLowerCase(java.util.Locale.ROOT);
        String value = line.substring(equals + 1).trim();
        if (value.isEmpty() || value.indexOf('\u0000') >= 0) {
          throw new IllegalStateException("TPC-C typed defaults value differs");
        }
        if ("user".equals(key)) {
          if (user != null || !value.matches("[A-Za-z0-9_.-]{1,128}")) {
            throw new IllegalStateException("TPC-C typed defaults user differs");
          }
          user = value;
        } else if ("password".equals(key)) {
          if (password != null) {
            throw new IllegalStateException("TPC-C typed defaults password differs");
          }
          password = value;
        } else {
          throw new IllegalStateException("TPC-C typed defaults client option differs");
        }
      }
      if (user == null || password == null) {
        throw new IllegalStateException("TPC-C typed defaults credentials are absent");
      }
      return new JdbcCredentials(user, password);
    } catch (IOException error) {
      throw new IllegalStateException("cannot read TPC-C typed defaults", error);
    }
  }

  private static void validatePerformanceSchema(Connection connection) throws SQLException {
    String sql =
        "SELECT "
            + "(SELECT ENABLED FROM performance_schema.setup_consumers "
            + "WHERE NAME='events_statements_history_long'),"
            + "(SELECT ENABLED FROM performance_schema.setup_consumers "
            + "WHERE NAME='events_waits_history_long')";
    try (Statement statement = connection.createStatement();
        ResultSet rows = statement.executeQuery(sql)) {
      if (!rows.next() || !"YES".equals(rows.getString(1)) || !"YES".equals(rows.getString(2))) {
        throw new SQLException("required Performance Schema history_long consumers are disabled");
      }
    }
    try (Statement statement = connection.createStatement();
        ResultSet ignored =
            statement.executeQuery(
                "SELECT EVENT_ID,CPU_TIME,LOCK_TIME FROM "
                    + "performance_schema.events_statements_history_long LIMIT 0")) {
      if (ignored.getMetaData().getColumnCount() != 3) {
        throw new SQLException("Performance Schema statement metric columns differ");
      }
    }
  }

  private static long targetConnectionId(Connection target) throws SQLException {
    synchronized (TARGET_CONNECTION_IDS) {
      Long cached = TARGET_CONNECTION_IDS.get(target);
      if (cached != null) return cached;
      try (Statement statement = target.createStatement();
          ResultSet rows = statement.executeQuery("SELECT CONNECTION_ID()")) {
        if (!rows.next()) throw new SQLException("target CONNECTION_ID() is absent");
        long result = rows.getLong(1);
        TARGET_CONNECTION_IDS.put(target, result);
        return result;
      }
    }
  }

  private static long targetThreadId(Connection metrics, long connectionId) throws SQLException {
    synchronized (TARGET_THREAD_IDS) {
      Long cached = TARGET_THREAD_IDS.get(connectionId);
      if (cached != null) return cached;
      try (PreparedStatement statement =
          metrics.prepareStatement(
              "SELECT THREAD_ID FROM performance_schema.threads WHERE PROCESSLIST_ID=?")) {
        statement.setLong(1, connectionId);
        try (ResultSet rows = statement.executeQuery()) {
          if (!rows.next()) throw new SQLException("target Performance Schema thread is absent");
          long result = rows.getLong(1);
          TARGET_THREAD_IDS.put(connectionId, result);
          return result;
        }
      }
    }
  }

  private static long latestStatementEvent(Connection metrics, long threadId) throws SQLException {
    try (PreparedStatement statement =
        metrics.prepareStatement(
            "SELECT COALESCE(MAX(EVENT_ID),0) FROM "
                + "performance_schema.events_statements_history_long WHERE THREAD_ID=?")) {
      statement.setLong(1, threadId);
      try (ResultSet rows = statement.executeQuery()) {
        if (!rows.next()) throw new SQLException("statement event cursor is absent");
        return rows.getLong(1);
      }
    }
  }

  private static Metrics statementMetrics(Connection metrics, long threadId, long previousEventId)
      throws SQLException {
    Long eventId = null;
    double cpuMs = 0.0;
    double lockMs = 0.0;
    try (PreparedStatement statement =
        metrics.prepareStatement(
            "SELECT EVENT_ID,COALESCE(CPU_TIME,0)/1000000000.0,"
                + "COALESCE(LOCK_TIME,0)/1000000000.0 "
                + "FROM performance_schema.events_statements_history_long "
                + "WHERE THREAD_ID=? AND EVENT_ID>? ORDER BY EVENT_ID DESC LIMIT 1")) {
      statement.setLong(1, threadId);
      statement.setLong(2, previousEventId);
      // Performance Schema publishes history_long rows asynchronously with
      // respect to the client receiving its statement result.  A separate
      // metrics connection can therefore observe a short visibility lag.
      // Poll the exact (thread,event-cursor) identity for at most two seconds;
      // never substitute a different statement or fabricate zero metrics.
      for (int attempt = 0; attempt < 2000 && eventId == null; attempt++) {
        try (ResultSet rows = statement.executeQuery()) {
          if (rows.next()) {
            eventId = rows.getLong(1);
            cpuMs = rows.getDouble(2);
            lockMs = rows.getDouble(3);
          }
        }
        if (eventId == null) {
          try {
            Thread.sleep(1L);
          } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
            throw new SQLException(
                "interrupted while waiting for executed statement history", interrupted);
          }
        }
      }
    }
    if (eventId == null) {
      throw new SQLException("executed statement is absent from history_long after 2000ms");
    }
    long readBytes;
    long writeBytes;
    double ioWaitMs;
    try (PreparedStatement statement =
        metrics.prepareStatement(
            "SELECT "
                + "COALESCE(SUM(CASE WHEN LOWER(OPERATION) LIKE 'read%' "
                + "THEN COALESCE(NUMBER_OF_BYTES,0) ELSE 0 END),0),"
                + "COALESCE(SUM(CASE WHEN LOWER(OPERATION) LIKE 'write%' "
                + "THEN COALESCE(NUMBER_OF_BYTES,0) ELSE 0 END),0),"
                + "COALESCE(SUM(COALESCE(TIMER_WAIT,0)),0)/1000000000.0 "
                + "FROM performance_schema.events_waits_history_long "
                + "WHERE THREAD_ID=? AND NESTING_EVENT_TYPE='STATEMENT' "
                + "AND NESTING_EVENT_ID=? AND EVENT_NAME LIKE 'wait/io/%'")) {
      statement.setLong(1, threadId);
      statement.setLong(2, eventId.longValue());
      try (ResultSet rows = statement.executeQuery()) {
        if (!rows.next()) throw new SQLException("statement IO attribution row is absent");
        readBytes = rows.getLong(1);
        writeBytes = rows.getLong(2);
        ioWaitMs = rows.getDouble(3);
      }
    }
    return new Metrics(cpuMs, lockMs, readBytes, writeBytes, ioWaitMs);
  }

  // FileChannel.lock() coordinates separate JVMs, but Java deliberately throws
  // OverlappingFileLockException instead of blocking when two threads in the
  // same JVM overlap. Serialize the whole append critical section in-process;
  // the file lock below remains the cross-process OWNER/JOINER authority.
  private static synchronized void appendCommitted(TransactionContext context) {
    StringBuilder payload = new StringBuilder();
    payload
        .append("{\"record_type\":\"transaction_begin\",\"transaction_id\":")
        .append(json(context.transactionId))
        .append(",\"transaction_type\":")
        .append(json(context.transactionType))
        .append(",\"worker_id\":")
        .append(context.workerId)
        .append(",\"started_monotonic_ns\":")
        .append(context.startedMonotonicNs)
        .append("}\n");
    for (StatementEvent event : context.statements)
      payload.append(event.json(context)).append('\n');
    payload
        .append("{\"record_type\":\"transaction_end\",\"transaction_id\":")
        .append(json(context.transactionId))
        .append(",\"transaction_type\":")
        .append(json(context.transactionType))
        .append(",\"ended_monotonic_ns\":")
        .append(context.endedMonotonicNs)
        .append(",\"outcome\":\"COMMIT\"}\n");
    byte[] bytes = payload.toString().getBytes(StandardCharsets.UTF_8);
    try {
      Files.createDirectories(PATH.getParent());
      try (FileChannel lockChannel =
          FileChannel.open(LOCK, StandardOpenOption.CREATE_NEW, StandardOpenOption.WRITE)) {
        // CREATE_NEW establishes a non-symlink lock authority for the first writer.
        lockChannel.force(true);
      } catch (java.nio.file.FileAlreadyExistsException expected) {
        if (Files.isSymbolicLink(LOCK) || !Files.isRegularFile(LOCK, LinkOption.NOFOLLOW_LINKS)) {
          throw new IOException("typed trace lock is not a regular file");
        }
      }
      try (FileChannel lockChannel = FileChannel.open(LOCK, StandardOpenOption.WRITE);
          FileLock fileLock = lockChannel.lock()) {
        if (!fileLock.isValid()) throw new IOException("typed trace file lock is invalid");
        boolean needsHeader = !Files.exists(PATH, LinkOption.NOFOLLOW_LINKS);
        if (!needsHeader && Files.isSymbolicLink(PATH)) {
          throw new IOException("typed trace path is a symlink");
        }
        try (FileChannel output =
            FileChannel.open(
                PATH,
                StandardOpenOption.CREATE,
                StandardOpenOption.WRITE,
                StandardOpenOption.APPEND)) {
          if (needsHeader) {
            String header =
                "{\"record_type\":\"header\",\"schema_version\":"
                    + json(SCHEMA)
                    + ",\"manifest_sha256\":"
                    + json(MANIFEST_SHA)
                    + ",\"capture_token\":"
                    + json(CAPTURE_TOKEN)
                    + ",\"capture_only\":true,\"outer_execution_authorized\":false,"
                    + "\"correctness_verified\":true,\"qps_included\":false}\n";
            writeFully(output, header.getBytes(StandardCharsets.UTF_8));
          }
          writeFully(output, bytes);
          output.force(true);
        }
      }
      try (FileChannel directory = FileChannel.open(PATH.getParent(), StandardOpenOption.READ)) {
        directory.force(true);
      }
    } catch (IOException error) {
      throw new IllegalStateException("cannot append TPC-C typed trace", error);
    }
  }

  private static void writeFully(FileChannel channel, byte[] bytes) throws IOException {
    ByteBuffer buffer = ByteBuffer.wrap(bytes);
    while (buffer.hasRemaining()) channel.write(buffer);
  }

  private static String canonicalSql(String sql) {
    return sql.trim().replaceAll("\\s+", " ");
  }

  private static String sha256(byte[] bytes) {
    try {
      byte[] digest = MessageDigest.getInstance("SHA-256").digest(bytes);
      StringBuilder output = new StringBuilder(64);
      for (byte value : digest) output.append(String.format("%02x", value & 0xff));
      return output.toString();
    } catch (Exception error) {
      throw new IllegalStateException(error);
    }
  }

  private static String json(Object value) {
    if (value == null) return "null";
    if (value instanceof Number || value instanceof Boolean) return value.toString();
    String text = value.toString();
    StringBuilder output = new StringBuilder("\"");
    for (int index = 0; index < text.length(); index++) {
      char c = text.charAt(index);
      switch (c) {
        case '\\' -> output.append("\\\\");
        case '"' -> output.append("\\\"");
        case '\n' -> output.append("\\n");
        case '\r' -> output.append("\\r");
        case '\t' -> output.append("\\t");
        default -> {
          if (c < 0x20) output.append(String.format("\\u%04x", (int) c));
          else output.append(c);
        }
      }
    }
    return output.append('"').toString();
  }

  private static String jdbcType(String setter, Object[] arguments) {
    return switch (setter) {
      case "setInt" -> "INTEGER";
      case "setLong" -> "BIGINT";
      case "setShort" -> "SMALLINT";
      case "setByte" -> "TINYINT";
      case "setBoolean" -> "BOOLEAN";
      case "setString" -> "VARCHAR";
      case "setBigDecimal" -> "DECIMAL";
      case "setDouble" -> "DOUBLE";
      case "setFloat" -> "FLOAT";
      case "setBytes" -> "VARBINARY";
      case "setDate" -> "DATE";
      case "setTime" -> "TIME";
      case "setTimestamp" -> "TIMESTAMP";
      case "setNull" -> sqlTypeName((Integer) arguments[1]);
      default -> throw new IllegalStateException("unsupported typed JDBC setter: " + setter);
    };
  }

  private static String sqlTypeName(int type) {
    return switch (type) {
      case Types.INTEGER -> "INTEGER";
      case Types.BIGINT -> "BIGINT";
      case Types.SMALLINT -> "SMALLINT";
      case Types.TINYINT -> "TINYINT";
      case Types.BOOLEAN -> "BOOLEAN";
      case Types.BIT -> "BIT";
      case Types.CHAR -> "CHAR";
      case Types.VARCHAR -> "VARCHAR";
      case Types.LONGVARCHAR -> "LONGVARCHAR";
      case Types.DECIMAL -> "DECIMAL";
      case Types.NUMERIC -> "NUMERIC";
      case Types.DOUBLE -> "DOUBLE";
      case Types.REAL -> "REAL";
      case Types.FLOAT -> "FLOAT";
      case Types.BINARY -> "BINARY";
      case Types.VARBINARY -> "VARBINARY";
      case Types.LONGVARBINARY -> "LONGVARBINARY";
      case Types.DATE -> "DATE";
      case Types.TIME -> "TIME";
      case Types.TIMESTAMP -> "TIMESTAMP";
      default -> throw new IllegalStateException("unsupported setNull JDBC type: " + type);
    };
  }

  private static final class PreparedHandler implements InvocationHandler {
    private final PreparedStatement target;
    private final Authority authority;
    private final Map<Integer, Binding> bindings = new HashMap<>();
    private final List<List<Binding>> batches = new ArrayList<>();

    PreparedHandler(PreparedStatement target, Authority authority) {
      this.target = target;
      this.authority = authority;
    }

    @Override
    public Object invoke(Object proxy, Method method, Object[] arguments) throws Throwable {
      String name = method.getName();
      if (name.startsWith("set")
          && arguments != null
          && arguments.length >= 2
          && arguments[0] instanceof Integer position) {
        String type = jdbcType(name, arguments);
        Object raw = "setNull".equals(name) ? null : arguments[1];
        String digest =
            sha256((type + "\0" + String.valueOf(raw)).getBytes(StandardCharsets.UTF_8));
        bindings.put(position, new Binding(position, type, name, raw == null, digest));
      } else if ("clearParameters".equals(name)) {
        bindings.clear();
      } else if ("addBatch".equals(name) && (arguments == null || arguments.length == 0)) {
        batches.add(snapshotBindings());
      } else if ("clearBatch".equals(name)) {
        batches.clear();
      }
      boolean execution =
          ("execute".equals(name)
                  || "executeQuery".equals(name)
                  || "executeUpdate".equals(name)
                  || "executeLargeUpdate".equals(name)
                  || "executeBatch".equals(name)
                  || "executeLargeBatch".equals(name))
              && (arguments == null || arguments.length == 0);
      if (!execution) return invokeTarget(method, arguments);
      TransactionContext context = requireContext();
      if (!authority.transactionType.equals(context.transactionType)) {
        throw new IllegalStateException("statement transaction does not match typed context");
      }
      List<Binding> representative =
          name.endsWith("Batch") && !batches.isEmpty()
              ? batches.get(batches.size() - 1)
              : snapshotBindings();
      validateBindings(representative);
      long started = System.nanoTime();
      long previous = latestStatementEvent(context.metrics, context.targetThreadId);
      Object result = invokeTarget(method, arguments);
      long ended = System.nanoTime();
      Metrics metrics = statementMetrics(context.metrics, context.targetThreadId, previous);
      context.statements.add(
          new StatementEvent(
              authority,
              representative,
              metrics,
              started,
              ended,
              name.endsWith("Batch") ? batches.size() : 1));
      return result;
    }

    private Object invokeTarget(Method method, Object[] arguments) throws Throwable {
      try {
        return method.invoke(target, arguments);
      } catch (InvocationTargetException error) {
        throw error.getCause();
      }
    }

    private List<Binding> snapshotBindings() {
      ArrayList<Binding> result = new ArrayList<>(bindings.values());
      result.sort(Comparator.comparingInt(value -> value.position));
      return result;
    }

    private void validateBindings(List<Binding> values) {
      if (values.size() != authority.parameterCount) {
        throw new IllegalStateException("typed JDBC binding count differs from manifest");
      }
      for (int index = 0; index < values.size(); index++) {
        if (values.get(index).position != index + 1) {
          throw new IllegalStateException("typed JDBC binding positions are not contiguous");
        }
      }
    }
  }

  private record Authority(
      int order,
      String transactionType,
      String bindingTransactionType,
      String statementId,
      int parameterCount,
      String templateHash) {
    String bindingId() {
      return String.format("%02d:%s:%s", order, bindingTransactionType, statementId);
    }
  }

  private record Binding(
      int position, String jdbcType, String setter, boolean isNull, String valueSha256) {}

  private record Metrics(
      double cpuMs, double lockMs, long readBytes, long writeBytes, double ioWaitMs) {}

  private record JdbcCredentials(String user, String password) {}

  private static final class StatementEvent {
    final Authority authority;
    final List<Binding> bindings;
    final Metrics metrics;
    final long started;
    final long ended;
    final int batchSize;

    StatementEvent(
        Authority authority,
        List<Binding> bindings,
        Metrics metrics,
        long started,
        long ended,
        int batchSize) {
      this.authority = authority;
      this.bindings = List.copyOf(bindings);
      this.metrics = metrics;
      this.started = started;
      this.ended = ended;
      this.batchSize = batchSize;
    }

    String json(TransactionContext context) {
      StringBuilder typed = new StringBuilder("[");
      for (int index = 0; index < bindings.size(); index++) {
        Binding binding = bindings.get(index);
        if (index > 0) typed.append(',');
        typed
            .append("{\"position\":")
            .append(binding.position)
            .append(",\"jdbc_type\":")
            .append(TpccTypedTrace.json(binding.jdbcType))
            .append(",\"setter\":")
            .append(TpccTypedTrace.json(binding.setter))
            .append(",\"is_null\":")
            .append(binding.isNull)
            .append(",\"value_sha256\":")
            .append(TpccTypedTrace.json(binding.valueSha256))
            .append('}');
      }
      typed.append(']');
      return "{\"record_type\":\"statement\",\"transaction_id\":"
          + TpccTypedTrace.json(context.transactionId)
          + ",\"transaction_type\":"
          + TpccTypedTrace.json(context.transactionType)
          + ",\"binding_id\":"
          + TpccTypedTrace.json(authority.bindingId())
          + ",\"statement_id\":"
          + TpccTypedTrace.json(authority.statementId)
          + ",\"statement_order\":"
          + authority.order
          + ",\"template_sql_sha256\":"
          + TpccTypedTrace.json(authority.templateHash)
          + ",\"typed_bindings\":"
          + typed
          + ",\"batch_size\":"
          + batchSize
          + ",\"cpu_time_ms\":"
          + metrics.cpuMs
          + ",\"lock_wait_ms\":"
          + metrics.lockMs
          + ",\"io_read_bytes\":"
          + metrics.readBytes
          + ",\"io_write_bytes\":"
          + metrics.writeBytes
          + ",\"io_wait_ms\":"
          + metrics.ioWaitMs
          + ",\"started_monotonic_ns\":"
          + started
          + ",\"ended_monotonic_ns\":"
          + ended
          + "}";
    }
  }

  private static final class TransactionContext {
    final String transactionId;
    final String transactionType;
    final int workerId;
    final long startedMonotonicNs;
    final long targetThreadId;
    final Connection metrics;
    final List<StatementEvent> statements = new ArrayList<>();
    long endedMonotonicNs;

    TransactionContext(
        String transactionId,
        String transactionType,
        int workerId,
        long startedMonotonicNs,
        long targetThreadId,
        Connection metrics) {
      this.transactionId = transactionId;
      this.transactionType = transactionType;
      this.workerId = workerId;
      this.startedMonotonicNs = startedMonotonicNs;
      this.targetThreadId = targetThreadId;
      this.metrics = metrics;
    }
  }
}
