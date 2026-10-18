package com.formal;

import com.oltpbenchmark.FormalBindingTrace;
import java.lang.reflect.Proxy;
import java.sql.PreparedStatement;

/** No-database harness proving that trace-off is inert and trace-on emits actual setter values. */
public final class FormalBindingTraceFakeWorkload {
  private FormalBindingTraceFakeWorkload() {}

  public static void main(String[] args) throws Exception {
    PreparedStatement target =
        (PreparedStatement)
            Proxy.newProxyInstance(
                FormalBindingTraceFakeWorkload.class.getClassLoader(),
                new Class<?>[] {PreparedStatement.class},
                (proxy, method, values) -> {
                  Class<?> result = method.getReturnType();
                  if (result == boolean.class) return false;
                  if (result == int.class) return 0;
                  if (result == long.class) return 0L;
                  if (result == float.class) return 0.0f;
                  if (result == double.class) return 0.0d;
                  return null;
                });
    FormalBindingTrace.beginAttempt(2, "Q6", FormalBindingTrace.nextSequence(2), 1, 17L, 991);
    PreparedStatement traced =
        FormalBindingTrace.wrap(
            target, "Q6", "select * from lineitem where l_quantity < ? and l_discount = ?");
    traced.setInt(1, 24);
    traced.setString(2, "0.06");
    traced.executeQuery();
    FormalBindingTrace.finishAttempt("success");
    System.out.println("FAKE_WORKLOAD_DONE");
  }
}
