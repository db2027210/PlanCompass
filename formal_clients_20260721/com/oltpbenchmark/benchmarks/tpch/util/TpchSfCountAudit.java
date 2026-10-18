package com.oltpbenchmark.benchmarks.tpch.util;

import com.oltpbenchmark.util.RowRandomBoundedInt;

/**
 * Derive TPC-H source cardinalities from the exact BenchBase generator classes.
 *
 * <p>This is an observation-only audit helper. It does not generate or load rows.
 * In particular, LINEITEM cardinality is obtained from the same deterministic
 * per-order line-count RNG used by BenchBase's OrderGenerator/LineItemGenerator.
 */
public final class TpchSfCountAudit {
    private TpchSfCountAudit() {}

    private static long count(Iterable<?> values) {
        long result = 0;
        for (Object ignored : values) {
            result++;
        }
        return result;
    }

    public static void main(String[] args) {
        if (args.length != 1) {
            throw new IllegalArgumentException("usage: TpchSfCountAudit <integral-scale-factor>");
        }
        int scaleFactor = Integer.parseInt(args[0]);
        if (scaleFactor <= 0) {
            throw new IllegalArgumentException("scale factor must be positive");
        }

        long suppliers = Math.multiplyExact((long) SupplierGenerator.SCALE_BASE, scaleFactor);
        long customers = Math.multiplyExact((long) CustomerGenerator.SCALE_BASE, scaleFactor);
        long parts = Math.multiplyExact((long) PartGenerator.SCALE_BASE, scaleFactor);
        long orders = Math.multiplyExact((long) OrderGenerator.SCALE_BASE, scaleFactor);
        long partSuppliers = Math.multiplyExact(parts, 4L); // TPC-H standard suppliers-per-part.

        RowRandomBoundedInt lineCount = OrderGenerator.createLineCountRandom();
        long lineItems = 0;
        for (long order = 0; order < orders; order++) {
            lineItems = Math.addExact(lineItems, lineCount.nextValue());
            lineCount.rowFinished();
        }

        System.out.println("region\t" + count(new RegionGenerator()));
        System.out.println("nation\t" + count(new NationGenerator()));
        System.out.println("supplier\t" + suppliers);
        System.out.println("customer\t" + customers);
        System.out.println("part\t" + parts);
        System.out.println("partsupp\t" + partSuppliers);
        System.out.println("orders\t" + orders);
        System.out.println("lineitem\t" + lineItems);
    }
}
