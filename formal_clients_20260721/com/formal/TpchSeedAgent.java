package com.formal;

import java.lang.instrument.ClassFileTransformer;
import java.lang.instrument.Instrumentation;
import java.security.ProtectionDomain;
import java.util.Arrays;

/** Observation-only runner adapter that replaces BenchBase TPC-H's hardcoded
 * worker seed (15721) with the frozen workload seed (4508333) at class load.
 * The frozen BenchBase JAR itself remains byte-for-byte unchanged.
 */
public final class TpchSeedAgent {
    private static final int FROZEN_SEED = 4508333;
    private static final String TARGET = "com/oltpbenchmark/benchmarks/tpch/TPCHWorker";

    public static void premain(String arguments, Instrumentation instrumentation) {
        instrumentation.addTransformer(new Transformer(), false);
        System.err.println("FORMAL_TPCH_SEED_AGENT=4508333");
    }

    private static int u2(byte[] bytes, int offset) {
        return ((bytes[offset] & 0xff) << 8) | (bytes[offset + 1] & 0xff);
    }

    private static int constantPoolEnd(byte[] bytes) {
        int count = u2(bytes, 8);
        int offset = 10;
        for (int index = 1; index < count; index++) {
            int tag = bytes[offset++] & 0xff;
            switch (tag) {
                case 1:
                    int length = u2(bytes, offset);
                    offset += 2 + length;
                    break;
                case 3:
                case 4:
                case 9:
                case 10:
                case 11:
                case 12:
                case 17:
                case 18:
                    offset += 4;
                    break;
                case 5:
                case 6:
                    offset += 8;
                    index++;
                    break;
                case 7:
                case 8:
                case 16:
                case 19:
                case 20:
                    offset += 2;
                    break;
                case 15:
                    offset += 3;
                    break;
                default:
                    throw new IllegalStateException("unsupported constant-pool tag " + tag);
            }
        }
        return offset;
    }

    private static final class Transformer implements ClassFileTransformer {
        @Override
        public byte[] transform(
                Module module,
                ClassLoader loader,
                String className,
                Class<?> classBeingRedefined,
                ProtectionDomain protectionDomain,
                byte[] classfileBuffer) {
            if (!TARGET.equals(className)) return null;
            int oldCount = u2(classfileBuffer, 8);
            int poolEnd = constantPoolEnd(classfileBuffer);
            byte[] patched = new byte[classfileBuffer.length + 5];
            System.arraycopy(classfileBuffer, 0, patched, 0, poolEnd);
            patched[8] = (byte) ((oldCount + 1) >>> 8);
            patched[9] = (byte) (oldCount + 1);
            patched[poolEnd] = 3; // CONSTANT_Integer
            patched[poolEnd + 1] = (byte) (FROZEN_SEED >>> 24);
            patched[poolEnd + 2] = (byte) (FROZEN_SEED >>> 16);
            patched[poolEnd + 3] = (byte) (FROZEN_SEED >>> 8);
            patched[poolEnd + 4] = (byte) FROZEN_SEED;
            System.arraycopy(
                    classfileBuffer, poolEnd, patched, poolEnd + 5,
                    classfileBuffer.length - poolEnd);

            int replacements = 0;
            for (int offset = poolEnd + 5; offset + 2 < patched.length; offset++) {
                if ((patched[offset] & 0xff) == 0x11
                        && (patched[offset + 1] & 0xff) == 0x3d
                        && (patched[offset + 2] & 0xff) == 0x69) {
                    patched[offset] = 0x13; // ldc_w
                    patched[offset + 1] = (byte) (oldCount >>> 8);
                    patched[offset + 2] = (byte) oldCount;
                    replacements++;
                }
            }
            if (replacements != 1) {
                throw new IllegalStateException(
                        "expected exactly one TPCHWorker hardcoded seed, found " + replacements);
            }
            return patched;
        }
    }
}
