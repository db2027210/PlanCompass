"""Module."""


from resource_utility import ResourceUtilityCalculator
import os


RESULT_DIR = 'result'
os.makedirs(RESULT_DIR, exist_ok=True)


def generate_comparison_report():
    """generate comparison report."""

    capability = {'cpu': 4.0}


    test_cases = [
        ('(50%)', {'cpu': 2.0}),   # u=0.5
        ('(75%)', {'cpu': 3.0}),   # u=0.75
        ('(100%)', {'cpu': 4.0}),    # u=1.0
        ('(112%)', {'cpu': 4.5}), # u=1.125
        ('(125%)', {'cpu': 5.0}),    # u=1.25
        ('(150%)', {'cpu': 6.0}), # u=1.5
    ]

    rewards = ['linear', 'log', 'sigmoid', 'exponential']
    penalties = ['linear', 'power', 'log', 'sigmoid', 'exponential']
    continuous = ['gaussian', 'inverse', 'tanh']

    output_file = os.path.join(RESULT_DIR, 'function_comparison_report.txt')

    with open(output_file, 'w', encoding='utf-8') as f:
        f.write("=" * 100 + "\n")
        f.write("\n")
        f.write("=" * 100 + "\n\n")


        f.write(" + \n")
        f.write("=" * 100 + "\n\n")

        for reward in rewards:
            f.write(f"\n: {reward.upper()}\n")
            f.write("-" * 100 + "\n")

            header = f"{'':<15}"
            for case_name, _ in test_cases:
                header += f"{case_name:>15}"
            f.write(header + "\n")
            f.write("-" * 100 + "\n")

            for penalty in penalties:
                calc = ResourceUtilityCalculator(reward_type=reward, penalty_type=penalty)
                line = f"{penalty:<15}"

                for _, resource in test_cases:
                    score = calc.calculate_utility_score(resource, capability)
                    line += f"{score:>15.4f}"

                f.write(line + "\n")

            f.write("\n")


        f.write("\n" + "=" * 100 + "\n")
        f.write("\n")
        f.write("=" * 100 + "\n\n")

        header = f"{'':<15}"
        for case_name, _ in test_cases:
            header += f"{case_name:>15}"
        f.write(header + "\n")
        f.write("-" * 100 + "\n")

        for cont_type in continuous:
            calc = ResourceUtilityCalculator(use_continuous=True, continuous_type=cont_type)
            line = f"{cont_type:<15}"

            for _, resource in test_cases:
                score = calc.calculate_utility_score(resource, capability)
                line += f"{score:>15.4f}"

            f.write(line + "\n")


        f.write("\n\n" + "=" * 100 + "\n")
        f.write("Configuration\n")
        f.write("=" * 100 + "\n\n")

        recommended = [
            ('Log + Power ()', 'log', 'power', False, None),
            ('Linear + Linear ()', 'linear', 'linear', False, None),
            ('Sigmoid + Exponential ()', 'sigmoid', 'exponential', False, None),
            ('Log + Exponential ()', 'log', 'exponential', False, None),
            ('Gaussian ()', None, None, True, 'gaussian'),
        ]

        header = f"{'Configuration':<30}"
        for case_name, _ in test_cases:
            header += f"{case_name:>15}"
        f.write(header + "\n")
        f.write("-" * 100 + "\n")

        for name, r, p, use_cont, cont_type in recommended:
            if use_cont:
                calc = ResourceUtilityCalculator(use_continuous=True, continuous_type=cont_type)
            else:
                calc = ResourceUtilityCalculator(reward_type=r, penalty_type=p)

            line = f"{name:<30}"

            for _, resource in test_cases:
                score = calc.calculate_utility_score(resource, capability)
                line += f"{score:>15.4f}"

            f.write(line + "\n")


        f.write("\n\n" + "=" * 100 + "\n")
        f.write("\n")
        f.write("=" * 100 + "\n\n")

        f.write(f":\n")
        f.write(f"  - : {len(rewards)} × {len(penalties)} = {len(rewards) * len(penalties)} \n")
        f.write(f"  - : {len(continuous)} \n")
        f.write(f"  - : {len(rewards) * len(penalties) + len(continuous)} Configuration\n\n")

        f.write(f": {', '.join(rewards)}\n")
        f.write(f": {', '.join(penalties)}\n")
        f.write(f": {', '.join(continuous)}\n\n")

        f.write("ConfigurationDescription:\n")
        f.write("  1. Log + Power: \n")
        f.write("  2. Linear + Linear: Linear\n")
        f.write("  3. Sigmoid + Exponential: Strict\n")
        f.write("  4. Log + Exponential: Avoids overload for strict SLA workloads\n")
        f.write("  5. Gaussian: \n")

        f.write("\n" + "=" * 100 + "\n")
        f.write("\n")
        f.write("=" * 100 + "\n")

    print(f": {output_file}")
    return output_file


def generate_quick_reference():
    """generate quick reference."""

    output_file = os.path.join(RESULT_DIR, 'quick_reference.txt')

    with open(output_file, 'w', encoding='utf-8') as f:
        f.write("╔" + "═" * 78 + "╗\n")
        f.write("║" + " - ".center(76) + "  ║\n")
        f.write("╚" + "═" * 78 + "╝\n\n")

        f.write(" u ≤ 1 \n")
        f.write("─" * 80 + "\n")
        f.write("  linear      : Linear growth - \n")
        f.write("  log         : Logarithmic -  []\n")
        f.write("  sigmoid     : S - \n")
        f.write("  exponential : Exponential growth - \n\n")

        f.write(" u > 1 \n")
        f.write("─" * 80 + "\n")
        f.write("  linear      : Linear decline - Mild\n")
        f.write("  power       :  - Moderate []\n")
        f.write("  log         : Logarithmic decline - Mild\n")
        f.write("  sigmoid     : S-shaped decline - \n")
        f.write("  exponential : Exponential decline - Strict\n\n")

        f.write(" u = 1\n")
        f.write("─" * 80 + "\n")
        f.write("  gaussian    :  - \n")
        f.write("  inverse     :  - Mild\n")
        f.write("  tanh        : Hyperbolic tangent - \n\n")

        f.write("\n")
        f.write("─" * 80 + "\n")
        f.write("from resource_utility import ResourceUtilityCalculator\n\n")
        f.write("# Configuration\n")
        f.write("calc = ResourceUtilityCalculator(reward_type='log', penalty_type='power')\n\n")
        f.write("# \n")
        f.write("score = calc.calculate_utility_score(resource, capability)\n\n")
        f.write("# \n")
        f.write("improved = calc.is_improved(old, new, capability)\n\n")

        f.write("\n")
        f.write("─" * 80 + "\n")
        f.write("      : log + power\n")
        f.write("  Cloud cost optimization  : log + linear\n")
        f.write("  Real-time system    : sigmoid + exponential\n")
        f.write("  Batch-processing system  : exponential + power\n")
        f.write("  SLA     : log + exponential\n")
        f.write("  Capacity planning    : gaussian ()\n\n")

        f.write("─" * 80 + "\n")
        f.write(f"Configuration: 23  (20 + 3)\n")
        f.write("─" * 80 + "\n")

    print(f": {output_file}")
    return output_file


if __name__ == "__main__":
    print("\n" + "=" * 80)
    print("...")
    print("=" * 80 + "\n")

    report_file = generate_comparison_report()
    reference_file = generate_quick_reference()

    print("\n" + "=" * 80)
    print("")
    print("=" * 80)
    print(f"\n:")
    print(f"  1. {report_file}")
    print(f"  2. {reference_file}")
    print(f"\n: ")
