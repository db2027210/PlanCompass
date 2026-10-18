package com.oltpbenchmark;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.sql.Connection;

/** Executes one capture-only SensitiveStockLevel transaction through the typed tracer. */
public final class TpccSensitiveTypedMain {
  private TpccSensitiveTypedMain() {}

  private static String required(String name) {
    String value = System.getenv(name);
    if (value == null || value.isBlank()) {
      throw new IllegalStateException("missing TPC-C sensitive helper environment: " + name);
    }
    return value;
  }

  public static void main(String[] arguments) throws Exception {
    if (arguments.length != 0) {
      throw new IllegalArgumentException("TPC-C sensitive helper accepts no command arguments");
    }
    if (!"JOINER".equals(System.getenv("MYSQL_TPCC_TYPED_TRACE_ROLE"))) {
      throw new IllegalStateException("TPC-C sensitive helper must join an owner trace");
    }
    Path sqlPath =
        Path.of(required("MYSQL_TPCC_TYPED_SENSITIVE_SQL_PATH")).toAbsolutePath().normalize();
    if (Files.isSymbolicLink(sqlPath) || !Files.isRegularFile(sqlPath, LinkOption.NOFOLLOW_LINKS)) {
      throw new IllegalStateException("TPC-C sensitive SQL is absent or symlinked");
    }
    String sql = Files.readString(sqlPath, StandardCharsets.UTF_8);
    try (Connection target =
        TpccTypedTrace.connectionFromSealedDefaults(required("MYSQL_TPCC_TYPED_TARGET_JDBC_URL"))) {
      TpccTypedTrace.captureSensitiveStockLevel(target, sql);
    }
  }
}
