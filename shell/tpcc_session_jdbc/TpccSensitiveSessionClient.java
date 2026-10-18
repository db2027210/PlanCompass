package resourceadaptive.tpcc;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Properties;
import java.util.SplittableRandom;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;

public final class TpccSensitiveSessionClient {
    private TpccSensitiveSessionClient() {
    }

    private record Options(
        String url,
        String user,
        int clients,
        int durationSeconds,
        int warehouses,
        Path output
    ) {
    }

    private record ClientResult(long transactions, long latencyNanos) {
    }

    public static void main(String[] args) throws Exception {
        Options options = parseArgs(args);
        String password = System.getenv("TPCC_DB_PASSWORD");
        if (password == null) {
            throw new IllegalArgumentException("TPCC_DB_PASSWORD is required");
        }
        Class.forName(SessionReadbackDriver.class.getName());
        Instant started = Instant.now();
        long deadlineNanos = System.nanoTime() + Duration.ofSeconds(options.durationSeconds()).toNanos();
        ExecutorService executor = Executors.newFixedThreadPool(options.clients());
        List<Future<ClientResult>> futures = new ArrayList<>();
        for (int client = 0; client < options.clients(); client++) {
            final int clientIndex = client;
            futures.add(executor.submit(runClient(options, password, deadlineNanos, clientIndex)));
        }
        executor.shutdown();
        long transactions = 0;
        long latencyNanos = 0;
        for (Future<ClientResult> future : futures) {
            ClientResult result = future.get();
            transactions += result.transactions();
            latencyNanos += result.latencyNanos();
        }
        double elapsedSeconds = Duration.between(started, Instant.now()).toNanos() / 1_000_000_000.0;
        double tps = elapsedSeconds > 0 ? transactions / elapsedSeconds : 0.0;
        double averageLatencyMs = transactions > 0
            ? latencyNanos / (double) transactions / 1_000_000.0
            : 0.0;
        String json = "{"
            + "\"schema_version\":1,"
            + "\"artifact_type\":\"tpcc_sensitive_session_metrics_v1\","
            + "\"clients\":" + options.clients() + ","
            + "\"transactions\":" + transactions + ","
            + "\"elapsed_seconds\":" + elapsedSeconds + ","
            + "\"tps\":" + tps + ","
            + "\"average_latency_ms\":" + averageLatencyMs
            + "}\n";
        writeNew(options.output(), json);
        System.out.print("TPCC_SENSITIVE_METRICS_JSON=" + json);
    }

    private static Callable<ClientResult> runClient(
        Options options,
        String password,
        long deadlineNanos,
        int clientIndex
    ) {
        return () -> {
            Properties properties = new Properties();
            properties.setProperty("user", options.user());
            properties.setProperty("password", password);
            SplittableRandom random = new SplittableRandom(20260727L + clientIndex);
            long transactions = 0;
            long latencyNanos = 0;
            String sql = """
                SELECT count(DISTINCT s.s_i_id)
                FROM order_line AS ol
                JOIN stock AS s
                  ON s.s_w_id = ol.ol_w_id
                 AND s.s_i_id = ol.ol_i_id
                WHERE ol.ol_w_id = ?
                  AND ol.ol_d_id BETWEEN 1 AND 10
                  AND ol.ol_o_id BETWEEN 2501 AND 3000
                  AND s.s_quantity < 50
                """;
            try (Connection connection = DriverManager.getConnection(options.url(), properties);
                 PreparedStatement statement = connection.prepareStatement(sql)) {
                connection.setAutoCommit(false);
                while (System.nanoTime() < deadlineNanos) {
                    int warehouse = random.nextInt(1, options.warehouses() + 1);
                    long started = System.nanoTime();
                    statement.setInt(1, warehouse);
                    try (ResultSet result = statement.executeQuery()) {
                        if (!result.next()) {
                            throw new IllegalStateException("StockLevel query returned no row");
                        }
                        result.getLong(1);
                    }
                    connection.commit();
                    latencyNanos += System.nanoTime() - started;
                    transactions++;
                }
            }
            return new ClientResult(transactions, latencyNanos);
        };
    }

    private static Options parseArgs(String[] args) {
        String url = null;
        String user = null;
        Path output = null;
        int clients = 0;
        int durationSeconds = 0;
        int warehouses = 0;
        for (int index = 0; index < args.length; index += 2) {
            if (index + 1 >= args.length) {
                throw new IllegalArgumentException("arguments must be --name value pairs");
            }
            String value = args[index + 1];
            switch (args[index]) {
                case "--url" -> url = value;
                case "--user" -> user = value;
                case "--clients" -> clients = Integer.parseInt(value);
                case "--duration-seconds" -> durationSeconds = Integer.parseInt(value);
                case "--warehouses" -> warehouses = Integer.parseInt(value);
                case "--output" -> output = Path.of(value);
                default -> throw new IllegalArgumentException("unknown argument: " + args[index]);
            }
        }
        if (url == null || user == null || output == null
            || clients <= 0 || durationSeconds <= 0 || warehouses <= 0) {
            throw new IllegalArgumentException("all arguments are required and counts must be positive");
        }
        return new Options(url, user, clients, durationSeconds, warehouses, output);
    }

    private static void writeNew(Path path, String value) throws IOException {
        Path parent = path.toAbsolutePath().getParent();
        if (parent == null || !Files.isDirectory(parent)) {
            throw new IOException("output parent does not exist: " + parent);
        }
        Files.writeString(
            path,
            value,
            StandardCharsets.UTF_8,
            java.nio.file.StandardOpenOption.CREATE_NEW
        );
    }
}
