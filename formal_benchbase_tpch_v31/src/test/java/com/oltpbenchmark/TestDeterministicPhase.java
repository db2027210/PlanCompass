package com.oltpbenchmark;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotEquals;

import java.util.ArrayList;
import java.util.List;
import org.junit.Test;

public class TestDeterministicPhase {
  private static Phase phase(int seed) {
    return new Phase(
        "tpch",
        1,
        660,
        60,
        0,
        List.of(4.0, 4.0, 4.0, 16.0, 4.0),
        false,
        false,
        false,
        true,
        4,
        Phase.Arrival.REGULAR,
        seed);
  }

  private static List<Integer> sequence(Phase phase) {
    List<Integer> result = new ArrayList<>();
    for (int index = 0; index < 100; index++) {
      result.add(phase.chooseTransaction());
    }
    return result;
  }

  @Test
  public void sameSeedProducesSameGlobalSchedule() {
    assertEquals(sequence(phase(4508333)), sequence(phase(4508333)));
  }

  @Test
  public void differentSeedChangesGlobalSchedule() {
    assertNotEquals(sequence(phase(4508333)), sequence(phase(4508334)));
  }
}
