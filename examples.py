"""Module."""


from resource_utility import ResourceUtilityCalculator, is_improved


def example_1_basic_usage():
    """example 1 basic usage."""
    print("\n" + "=" * 60)
    print("1:  - Configuration")
    print("=" * 60)


    capability = {
        'cpu': 8.0,
        'memory': 16.0,
        'bandwidth': 1000.0
    }


    current_usage = {
        'cpu': 4.0,
        'memory': 8.0,
        'bandwidth': 500.0
    }


    planned_usage = {
        'cpu': 6.0,
        'memory': 12.0,
        'bandwidth': 800.0
    }


    calculator = ResourceUtilityCalculator(reward_type='log', penalty_type='power')


    current_score = calculator.calculate_utility_score(current_usage, capability)
    planned_score = calculator.calculate_utility_score(planned_usage, capability)

    print(f"Configuration: {current_score:.4f}")
    print(f"Configuration: {planned_score:.4f}")


    improved = calculator.is_improved(current_usage, planned_usage, capability)
    print(f"Configuration: {' ✓' if improved else ' ✗'}")


    improved_v2 = is_improved(current_usage, planned_usage, capability)
    print(f": {' ✓' if improved_v2 else ' ✗'}")


def example_2_detailed_analysis():
    """example 2 detailed analysis."""
    print("\n" + "=" * 60)
    print("2:  - ")
    print("=" * 60)

    capability = {'cpu': 4.0, 'memory': 8.0, 'disk': 100.0}
    resource = {'cpu': 3.8, 'memory': 9.0, 'disk': 70.0}

    calculator = ResourceUtilityCalculator(reward_type='log', penalty_type='power')


    details = calculator.get_detailed_scores(resource, capability)

    print(f"\n: {details['total_score']:.4f}\n")
    print(f"{'':<10} {'':>8} {'':>8} {'':>10} {'':>10} {'':<10}")
    print("-" * 65)

    for res_name in ['cpu', 'memory', 'disk']:
        info = details[res_name]
        status_symbol = '✓' if info['status'] == 'normal' else '⚠'
        print(f"{res_name:<10} {info['usage']:>8.1f} {info['capability']:>8.1f} "
              f"{info['utilization']:>9.1%} {info['score']:>10.4f} "
              f"{status_symbol} {info['status']}")


def example_3_compare_functions():
    """example 3 compare functions."""
    print("\n" + "=" * 60)
    print("3: ")
    print("=" * 60)

    capability = {'cpu': 4.0, 'memory': 8.0}


    scenarios = [
        ('', {'cpu': 3.0, 'memory': 6.0}),
        ('', {'cpu': 3.9, 'memory': 7.8}),
        ('', {'cpu': 4.5, 'memory': 8.5}),
    ]


    configs = [
        ('linear', 'linear', 'Linear-Linear'),
        ('log', 'power', 'Log-'),
        ('sigmoid', 'exponential', 'Sigmoid-Exponential'),
    ]

    print(f"\n{'':<12}", end='')
    for _, _, name in configs:
        print(f"{name:>20}", end='')
    print("\n" + "-" * 75)

    for scenario_name, resource in scenarios:
        print(f"{scenario_name:<12}", end='')

        for reward, penalty, _ in configs:
            calc = ResourceUtilityCalculator(reward_type=reward, penalty_type=penalty)
            score = calc.calculate_utility_score(resource, capability)
            print(f"{score:>20.4f}", end='')

        print()


def example_4_continuous_functions():
    """example 4 continuous functions."""
    print("\n" + "=" * 60)
    print("4: ")
    print("=" * 60)

    capability = {'cpu': 4.0, 'memory': 8.0}

    test_cases = [
        ('', {'cpu': 2.0, 'memory': 4.0}),
        ('', {'cpu': 4.0, 'memory': 8.0}),
        ('', {'cpu': 5.0, 'memory': 10.0}),
    ]

    continuous_types = ['gaussian', 'inverse', 'tanh']

    print(f"\n{'':<12}", end='')
    for ct in continuous_types:
        print(f"{ct:>15}", end='')
    print("\n" + "-" * 57)

    for case_name, resource in test_cases:
        print(f"{case_name:<12}", end='')

        for cont_type in continuous_types:
            calc = ResourceUtilityCalculator(use_continuous=True, continuous_type=cont_type)
            score = calc.calculate_utility_score(resource, capability)
            print(f"{score:>15.4f}", end='')

        print()

    print("\n:  u=1 () ")


def example_5_parameter_tuning():
    """example 5 parameter tuning."""
    print("\n" + "=" * 60)
    print("5: ")
    print("=" * 60)

    capability = {
        'cpu': 4.0,
        'memory': 8.0,
        'network': 1000.0
    }


    params_v1 = {
        'cpu': 2.0,
        'memory': 4.0,
        'network': 500.0
    }

    calculator = ResourceUtilityCalculator(reward_type='log', penalty_type='power')

    print(":")
    print(f"  {params_v1}")
    initial_score = calculator.calculate_utility_score(params_v1, capability)
    print(f"  : {initial_score:.4f}\n")


    iterations = [
        {'cpu': 3.0, 'memory': 6.0, 'network': 700.0},
        {'cpu': 3.5, 'memory': 7.0, 'network': 850.0},
        {'cpu': 3.8, 'memory': 7.5, 'network': 900.0},
        {'cpu': 4.5, 'memory': 8.0, 'network': 950.0},  # CPU overload
        {'cpu': 3.9, 'memory': 7.8, 'network': 920.0},
    ]

    current_params = params_v1
    current_score = initial_score

    for i, new_params in enumerate(iterations, 1):
        new_score = calculator.calculate_utility_score(new_params, capability)
        is_better = new_score > current_score

        print(f" {i}: {new_params}")
        print(f"  : {new_score:.4f} ({'↑ ' if is_better else '↓ '} "
              f"{abs(new_score - current_score):.4f})")

        if is_better:
            print(f"  ✓ ")
            current_params = new_params
            current_score = new_score
        else:
            print(f"  ✗ Configuration")

        print()

    print(f": {current_params}")
    print(f": {current_score:.4f}")
    print(f": {current_score - initial_score:.4f} "
          f"({(current_score - initial_score) / initial_score * 100:.1f}%)")


def example_6_multi_objective():
    """example 6 multi objective."""
    print("\n" + "=" * 60)
    print("6:  - ")
    print("=" * 60)


    capability = {'cpu': 8.0, 'memory': 16.0, 'storage': 500.0}


    candidates = [
        ('', {'cpu': 2.0, 'memory': 4.0, 'storage': 100.0}),
        ('', {'cpu': 6.0, 'memory': 12.0, 'storage': 300.0}),
        ('', {'cpu': 7.5, 'memory': 15.0, 'storage': 450.0}),
        ('', {'cpu': 8.5, 'memory': 16.5, 'storage': 480.0}),
    ]


    cost_per_unit = {'cpu': 10, 'memory': 5, 'storage': 0.1}

    calculator = ResourceUtilityCalculator(reward_type='log', penalty_type='power')

    print(f"{'Configuration':<10} {'':>12} {'':>10} {'':>12}")
    print("-" * 50)

    for name, config in candidates:

        utility_score = calculator.calculate_utility_score(config, capability)


        total_cost = sum(config[r] * cost_per_unit[r] for r in config)


        cost_effectiveness = utility_score / total_cost if total_cost > 0 else 0

        print(f"{name:<10} {utility_score:>12.4f} {total_cost:>10.1f} "
              f"{cost_effectiveness:>12.6f}")


def example_7_adaptive_threshold():
    """example 7 adaptive threshold."""
    print("\n" + "=" * 60)
    print("7:  - ")
    print("=" * 60)

    base_capability = {'cpu': 4.0, 'memory': 8.0}


    load_scenarios = [
        ('', 0.3, {'cpu': 1.2, 'memory': 2.4}),
        ('', 0.6, {'cpu': 2.4, 'memory': 4.8}),
        ('', 0.9, {'cpu': 3.6, 'memory': 7.2}),
    ]

    calculator = ResourceUtilityCalculator(reward_type='log', penalty_type='power')

    print(f"{'':<10} {'':>8} {'(CPU)':>15} {'(MEM)':>15} {'':>10}")
    print("-" * 70)

    for scenario, load_factor, usage in load_scenarios:

        adjusted_capability = {
            r: base_capability[r] * (0.5 + 0.5 * load_factor)
            for r in base_capability
        }

        score = calculator.calculate_utility_score(usage, adjusted_capability)

        print(f"{scenario:<10} {load_factor:>7.1%} {adjusted_capability['cpu']:>15.2f} "
              f"{adjusted_capability['memory']:>15.2f} {score:>10.4f}")

    print("\n: ")


if __name__ == "__main__":
    print("\n" + "█" * 60)
    print("█" + " " * 58 + "█")
    print("█" + "   - ".center(56) + "  █")
    print("█" + " " * 58 + "█")
    print("█" * 60)


    example_1_basic_usage()
    example_2_detailed_analysis()
    example_3_compare_functions()
    example_4_continuous_functions()
    example_5_parameter_tuning()
    example_6_multi_objective()
    example_7_adaptive_threshold()

    print("\n" + "=" * 60)
    print("")
    print("=" * 60)
    print("\n")
    print("  1. : is_improved(old, new, cap)")
    print("  2. : calculator.get_detailed_scores(res, cap)")
    print("  3. Configuration: reward='log', penalty='power'")
    print("  4. : use_continuous=True, continuous_type='gaussian'")
    print("\n:  visualize_functions.py ")
