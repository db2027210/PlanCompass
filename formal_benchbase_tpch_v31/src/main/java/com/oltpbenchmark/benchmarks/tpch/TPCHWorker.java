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

package com.oltpbenchmark.benchmarks.tpch;

import com.oltpbenchmark.api.Procedure.UserAbortException;
import com.oltpbenchmark.api.TransactionType;
import com.oltpbenchmark.api.Worker;
import com.oltpbenchmark.benchmarks.tpch.procedures.GenericQuery;
import com.oltpbenchmark.types.TransactionStatus;
import com.oltpbenchmark.util.RandomGenerator;
import java.sql.Connection;
import java.sql.SQLException;

public final class TPCHWorker extends Worker<TPCHBenchmark> {

  private final CountingRandomGenerator rand;
  private final int derivedWorkerSeed;

  public TPCHWorker(TPCHBenchmark benchmarkModule, int id) {
    super(benchmarkModule, id);
    this.rng().setSeed(15721);
    derivedWorkerSeed = this.rng().nextInt();
    rand = new CountingRandomGenerator(derivedWorkerSeed);
  }

  @Override
  protected TransactionStatus executeWork(Connection conn, TransactionType nextTransaction)
      throws UserAbortException, SQLException {
    try {
      GenericQuery proc = (GenericQuery) this.getProcedure(nextTransaction.getProcedureClass());
      proc.run(conn, rand, this.configuration.getScaleFactor());
    } catch (ClassCastException e) {
      throw new RuntimeException(e);
    }

    return (TransactionStatus.SUCCESS);
  }

  public long formalRngDrawCount() {
    return rand.drawCount;
  }

  public int formalDerivedWorkerSeed() {
    return derivedWorkerSeed;
  }

  private static final class CountingRandomGenerator extends RandomGenerator {
    private static final long serialVersionUID = 1L;
    private long drawCount;

    CountingRandomGenerator(int seed) {
      super(seed);
    }

    @Override
    protected int next(int bits) {
      int value = super.next(bits);
      drawCount++;
      return value;
    }
  }
}
