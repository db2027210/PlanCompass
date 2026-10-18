/*
 * Copyright 2020 by OLTPBenchmark Project
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 *
 */

package com.oltpbenchmark.benchmarks.tpch.procedures;

import com.oltpbenchmark.FormalBindingTrace;
import com.oltpbenchmark.api.SQLStmt;
import com.oltpbenchmark.util.RandomGenerator;
import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.SQLException;
import java.sql.Statement;
import java.util.concurrent.locks.ReentrantLock;

public class Q15 extends GenericQuery {

  /*
   * TPC-H Q15's revenue0 view is schema-global in MySQL, while BenchBase
   * terminals use independent connections. Concurrent Q15 executions can
   * therefore drop each other's view and turn an otherwise valid run into an
   * Unknown table failure. Keep the frozen SQL and CREATE/SELECT/DROP
   * sequence unchanged, but serialize the only transaction that owns this
   * schema-global object. Fair ordering avoids terminal starvation.
   */
  private static final ReentrantLock REVENUE_VIEW_LOCK = new ReentrantLock(true);

  public final SQLStmt createview_stmt =
      new SQLStmt(
          """
            CREATE view revenue0 (supplier_no, total_revenue) AS
            SELECT
               l_suppkey,
               SUM(l_extendedprice * (1 - l_discount))
            FROM
               lineitem
            WHERE
               l_shipdate >= DATE ?
               AND l_shipdate < DATE ? + INTERVAL '3' MONTH
            GROUP BY
               l_suppkey
            """);

  public final SQLStmt query_stmt =
      new SQLStmt(
          """
            SELECT
               s_suppkey,
               s_name,
               s_address,
               s_phone,
               total_revenue
            FROM
               supplier,
               revenue0
            WHERE
               s_suppkey = supplier_no
               AND total_revenue = (
                  SELECT
                     MAX(total_revenue)
                  FROM
                     revenue0
               )
            ORDER BY
               s_suppkey
            """);

  public final SQLStmt dropview_stmt =
      new SQLStmt(
          """
            DROP VIEW revenue0
            """);

  @Override
  public void run(Connection conn, RandomGenerator rand, double scaleFactor) throws SQLException {
    REVENUE_VIEW_LOCK.lock();
    try {
      // With this query, we have to set up a view before we execute the
      // query, then drop it once we're done.
      try (Statement stmt = conn.createStatement()) {
        try {
          // DATE is the first day of a randomly selected month between
          // the first month of 1993 and the 10th month of 1997
          int year = rand.number(1993, 1997);
          int month = rand.number(1, year == 1997 ? 10 : 12);
          String date = String.format("%d-%02d-01", year, month);

          String sql = createview_stmt.getSQL();
          sql = sql.replace("?", String.format("'%s'", date));
          FormalBindingTrace.captureDirect(createview_stmt.getSQL(), sql, date);
          stmt.execute(sql);
          super.run(conn, rand, scaleFactor);
        } finally {
          String sql = dropview_stmt.getSQL();
          stmt.execute(sql);
        }
      }
    } finally {
      REVENUE_VIEW_LOCK.unlock();
    }
  }

  @Override
  protected PreparedStatement getStatement(
      Connection conn, RandomGenerator rand, double scaleFactor) throws SQLException {
    return this.getPreparedStatement(conn, query_stmt);
  }
}
