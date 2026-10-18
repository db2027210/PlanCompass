package com.oltpbenchmark;

import java.lang.reflect.Proxy;
import java.sql.PreparedStatement;

/** No-database smoke probe for the execution-site binding trace. */
public final class FormalBindingTraceNoDbProbe {
  private FormalBindingTraceNoDbProbe() {}

  public static void main(String[] args) throws Exception {
    PreparedStatement target =
        (PreparedStatement)
            Proxy.newProxyInstance(
                FormalBindingTraceNoDbProbe.class.getClassLoader(),
                new Class<?>[] {PreparedStatement.class},
                (proxy, method, arguments) -> {
                  Class<?> resultType = method.getReturnType();
                  if (!resultType.isPrimitive() || resultType == void.class) return null;
                  if (resultType == boolean.class) return false;
                  if (resultType == char.class) return '\0';
                  if (resultType == byte.class) return (byte) 0;
                  if (resultType == short.class) return (short) 0;
                  if (resultType == int.class) return 0;
                  if (resultType == long.class) return 0L;
                  if (resultType == float.class) return 0.0F;
                  return 0.0D;
                });

    if (!FormalBindingTrace.enabled()) {
      if (FormalBindingTrace.nextSequence(7) != 0L) {
        throw new AssertionError("disabled trace allocated a sequence");
      }
      if (FormalBindingTrace.wrap(target, "Q18", "SELECT ?") != target) {
        throw new AssertionError("disabled trace wrapped the statement");
      }
      System.out.println("FORMAL_BINDING_TRACE_DISABLED_OK");
      return;
    }

    long sequence = FormalBindingTrace.nextSequence(7);
    FormalBindingTrace.beginAttempt(7, "Q18", sequence, 1, 19L, 4508340);
    PreparedStatement traced =
        FormalBindingTrace.wrap(target, "Q18", "SELECT ? AS quantity, ? AS comment");
    traced.setInt(1, 314);
    traced.setString(2, "trace-probe");
    traced.executeQuery();
    FormalBindingTrace.finishAttempt("success");
    System.out.println("FORMAL_BINDING_TRACE_ENABLED_OK");
  }
}
