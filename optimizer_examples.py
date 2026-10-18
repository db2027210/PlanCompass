"""Module."""


import numpy as np
import matplotlib.pyplot as plt
from parameter_optimizer import (
    ParameterOptimizer,
    ParameterRange,
    example_resource_estimation
)
import os


RESULT_DIR = 'result'
os.makedirs(RESULT_DIR, exist_ok=True)


def advanced_resource_estimation(sql_set, params):
    """advanced resource estimation."""


    base_cpu = 3.0
    base_memory = 6.0
    base_io = 60.0


    rpc = params.get('random_page_cost', 4.0)
    work_mem = params.get('work_mem', 4096)
    shared_buf = params.get('shared_buffers', 1024)
    eff_cache = params.get('effective_cache_size', 4096)


    if rpc < 2.0:

        io = base_io + (2.0 - rpc) * 20
        cpu = base_cpu + (2.0 - rpc) * 0.5
    elif rpc > 6.0:

        io = base_io - (rpc - 4.0) * 5
        cpu = base_cpu + (rpc - 6.0) * 1.0
    else:

        io = base_io - (rpc - 2.0) * 3
        cpu = base_cpu


    optimal_work_mem = 8192
    memory = base_memory + work_mem / 1024
    if work_mem < optimal_work_mem:

        io += (optimal_work_mem - work_mem) / 1024 * 5
        cpu += (optimal_work_mem - work_mem) / 2048 * 0.3


    optimal_shared_buf = 2048
    memory += shared_buf / 1024
    if shared_buf < optimal_shared_buf:
        io += (optimal_shared_buf - shared_buf) / 512 * 8


    if eff_cache < 6144:

        cpu += (6144 - eff_cache) / 2048 * 0.4


    cpu += np.random.normal(0, 0.1)
    memory += np.random.normal(0, 0.2)
    io += np.random.normal(0, 2.0)

    return {
        'cpu': max(0.5, cpu),
        'memory': max(1.0, memory),
        'io': max(5.0, io)
    }


def visualize_optimization_history(result, output_file=None):
    """visualize optimization history."""

    fig, axes = plt.subplots(2, 2, figsize=(16, 12))
    fig.suptitle('', fontsize=16, fontweight='bold')

    iterations = [h['iteration'] for h in result.history]
    scores = [h['score'] for h in result.history]


    ax1 = axes[0, 0]
    ax1.plot(iterations, scores, 'b-o', linewidth=2, markersize=8)
    ax1.axhline(y=result.initial_score, color='r', linestyle='--',
                label=f': {result.initial_score:.4f}')
    ax1.set_xlabel('', fontsize=12)
    ax1.set_ylabel('', fontsize=12)
    ax1.set_title('', fontsize=14, fontweight='bold')
    ax1.grid(True, alpha=0.3)
    ax1.legend()


    ax1.annotate(f': {result.final_score:.4f}',
                 xy=(iterations[-1], scores[-1]),
                 xytext=(iterations[-1] - 0.5, scores[-1] + 0.1),
                 fontsize=10,
                 bbox=dict(boxstyle='round,pad=0.5', facecolor='yellow', alpha=0.7),
                 arrowprops=dict(arrowstyle='->', color='black'))


    ax2 = axes[0, 1]
    param_names = list(result.initial_params.keys())

    for param_name in param_names:
        values = []
        for h in result.history:

            initial_val = result.initial_params[param_name]
            current_val = h['params'][param_name]
            normalized = current_val / initial_val if initial_val != 0 else current_val
            values.append(normalized)

        ax2.plot(iterations, values, '-o', label=param_name, linewidth=2, markersize=6)

    ax2.axhline(y=1.0, color='gray', linestyle='--', alpha=0.5)
    ax2.set_xlabel('', fontsize=12)
    ax2.set_ylabel('', fontsize=12)
    ax2.set_title('', fontsize=14, fontweight='bold')
    ax2.grid(True, alpha=0.3)
    ax2.legend(loc='best')


    ax3 = axes[1, 0]

    resource_names = list(result.final_resource.keys())
    capability = {
        'cpu': 8.0,
        'memory': 16.0,
        'io': 100.0
    }

    for res_name in resource_names:
        utilizations = []
        for h in result.history:
            util = h['resource'][res_name] / capability[res_name]
            utilizations.append(util)

        ax3.plot(iterations, utilizations, '-o', label=res_name, linewidth=2, markersize=6)

    ax3.axhline(y=1.0, color='red', linestyle='--', label='', linewidth=2)
    ax3.axhline(y=0.8, color='orange', linestyle=':', label='(80%)', linewidth=1.5)
    ax3.set_xlabel('', fontsize=12)
    ax3.set_ylabel('', fontsize=12)
    ax3.set_title('', fontsize=14, fontweight='bold')
    ax3.grid(True, alpha=0.3)
    ax3.legend(loc='best')
    ax3.set_ylim([0, max(1.2, max([max(h['resource'][r] / capability[r] for r in resource_names) for h in result.history]) * 1.1)])


    ax4 = axes[1, 1]


    bottleneck_counts = {res: 0 for res in resource_names}
    overload_counts = {res: 0 for res in resource_names}

    for h in result.history:
        for res in h['bottleneck']['bottleneck_resources']:
            bottleneck_counts[res] += 1
        for res in h['bottleneck']['overload_resources']:
            overload_counts[res] += 1

    x = np.arange(len(resource_names))
    width = 0.35

    bars1 = ax4.bar(x - width/2, [bottleneck_counts[r] for r in resource_names],
                    width, label='', color='orange', alpha=0.7)
    bars2 = ax4.bar(x + width/2, [overload_counts[r] for r in resource_names],
                    width, label='', color='red', alpha=0.7)

    ax4.set_xlabel('', fontsize=12)
    ax4.set_ylabel('', fontsize=12)
    ax4.set_title('', fontsize=14, fontweight='bold')
    ax4.set_xticks(x)
    ax4.set_xticklabels(resource_names)
    ax4.legend()
    ax4.grid(True, alpha=0.3, axis='y')


    for bars in [bars1, bars2]:
        for bar in bars:
            height = bar.get_height()
            if height > 0:
                ax4.text(bar.get_x() + bar.get_width()/2., height,
                        f'{int(height)}',
                        ha='center', va='bottom', fontsize=9)

    plt.tight_layout()

    if output_file:
        output_path = os.path.join(RESULT_DIR, output_file)
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        print(f"\n: {output_path}")

    plt.show()


def compare_different_strategies():
    """compare different strategies."""

    print("\n" + "=" * 80)
    print("".center(80))
    print("=" * 80)


    capability = {
        'cpu': 8.0,
        'memory': 16.0,
        'io': 100.0
    }

    p_real = {
        'random_page_cost': 4.0,
        'work_mem': 4096,
        'shared_buffers': 1024,
        'effective_cache_size': 4096
    }

    p_candidate = {
        'random_page_cost': 2.0,
        'work_mem': 6144,
        'shared_buffers': 1536,
        'effective_cache_size': 6144
    }

    param_ranges = {
        'random_page_cost': ParameterRange(1.0, 8.0, discrete_count=15, priority=1),
        'work_mem': ParameterRange(2048, 16384, discrete_count=12, priority=2),
        'shared_buffers': ParameterRange(512, 4096, discrete_count=10, priority=3),
        'effective_cache_size': ParameterRange(2048, 12288, discrete_count=10, priority=4)
    }

    sql_set = None


    strategies = [
        ('Log + Power ()', 'log', 'power'),
        ('Linear + Linear', 'linear', 'linear'),
        ('Sigmoid + Exponential', 'sigmoid', 'exponential'),
    ]

    results = {}

    for name, reward, penalty in strategies:
        print(f"\n{'='*80}")
        print(f": {name}".center(80))
        print(f"{'='*80}")


        param_ranges_copy = {
            'random_page_cost': ParameterRange(1.0, 8.0, discrete_count=15, priority=1),
            'work_mem': ParameterRange(2048, 16384, discrete_count=12, priority=2),
            'shared_buffers': ParameterRange(512, 4096, discrete_count=10, priority=3),
            'effective_cache_size': ParameterRange(2048, 12288, discrete_count=10, priority=4)
        }

        optimizer = ParameterOptimizer(
            resource_estimation_func=advanced_resource_estimation,
            capability=capability,
            reward_type=reward,
            penalty_type=penalty,
            verbose=False
        )

        result = optimizer.optimize(
            p_real=p_real,
            p_candidate=p_candidate,
            param_ranges=param_ranges_copy,
            sql_set=sql_set,
            max_iter=5,
            shrink_factor=0.5,
            early_stop_threshold=2
        )

        results[name] = result

        print(f": {result.final_score:.4f}")
        print(f": {result.final_score - result.initial_score:.4f}")
        print(f": {result.iterations}")


    print("\n" + "=" * 80)
    print("".center(80))
    print("=" * 80)

    print(f"\n{'':<30} {'':>12} {'':>12} {'':>12} {'':>8}")
    print("-" * 80)

    for name, result in results.items():
        improvement = result.final_score - result.initial_score
        print(f"{name:<30} {result.initial_score:>12.4f} {result.final_score:>12.4f} "
              f"{improvement:>12.4f} {result.iterations:>8}")

    return results


def run_detailed_example():
    """run detailed example."""

    print("\n" + "█" * 80)
    print("█" + " " * 78 + "█")
    print("█" + "".center(76) + "  █")
    print("█" + " " * 78 + "█")
    print("█" * 80)


    capability = {
        'cpu': 8.0,
        'memory': 16.0,
        'io': 100.0      # 100MB/s IO
    }


    p_real = {
        'random_page_cost': 4.0,
        'work_mem': 4096,
        'shared_buffers': 1024,
        'effective_cache_size': 4096
    }


    p_candidate = {
        'random_page_cost': 3.0,
        'work_mem': 6144,
        'shared_buffers': 1536,
        'effective_cache_size': 6144
    }


    param_ranges = {
        'random_page_cost': ParameterRange(
            min_value=1.0,
            max_value=8.0,
            discrete_count=15,
            priority=1
        ),
        'work_mem': ParameterRange(
            min_value=2048,
            max_value=16384,
            discrete_count=12,
            priority=2
        ),
        'shared_buffers': ParameterRange(
            min_value=512,
            max_value=4096,
            discrete_count=10,
            priority=3
        ),
        'effective_cache_size': ParameterRange(
            min_value=2048,
            max_value=12288,
            discrete_count=10,
            priority=4
        )
    }


    optimizer = ParameterOptimizer(
        resource_estimation_func=advanced_resource_estimation,
        capability=capability,
        reward_type='log',
        penalty_type='power',
        verbose=True
    )


    result = optimizer.optimize(
        p_real=p_real,
        p_candidate=p_candidate,
        param_ranges=param_ranges,
        sql_set=None,
        max_iter=10,
        shrink_factor=0.5,
        early_stop_threshold=3
    )


    visualize_optimization_history(result, 'optimization_history.png')

    return result


if __name__ == "__main__":

    result = run_detailed_example()


    print("\n\n")
    strategy_results = compare_different_strategies()

    print("\n" + "=" * 80)
    print("".center(80))
    print("=" * 80)
    print(f"\n: {RESULT_DIR}/")
