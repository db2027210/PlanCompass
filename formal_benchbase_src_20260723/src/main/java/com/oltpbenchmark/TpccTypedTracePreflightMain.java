package com.oltpbenchmark;

/** Performs the fail-closed typed trace environment check without contacting a database. */
public final class TpccTypedTracePreflightMain {
  private TpccTypedTracePreflightMain() {}

  public static void main(String[] arguments) {
    if (arguments.length != 0) {
      throw new IllegalArgumentException("TPC-C typed trace preflight accepts no arguments");
    }
    if (!TpccTypedTrace.enabled()) {
      throw new IllegalStateException("TPC-C typed trace is disabled");
    }
    System.out.println("TPCC_TYPED_TRACE_PREFLIGHT_PASS");
  }
}
