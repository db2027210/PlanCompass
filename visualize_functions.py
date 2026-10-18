"""Module."""


import numpy as np
import matplotlib.pyplot as plt
from matplotlib import font_manager
from resource_utility import ResourceUtilityCalculator
import os


RESULT_DIR = 'result'
os.makedirs(RESULT_DIR, exist_ok=True)


plt.rcParams['font.sans-serif'] = ['SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False


def plot_piecewise_functions():
    """plot piecewise functions."""
    u = np.linspace(0, 2, 1000)

    fig, axes = plt.subplots(2, 2, figsize=(15, 12))
    fig.suptitle('', fontsize=16, fontweight='bold')


    configs = [
        ('log', 'power', 'Log + Power penalty'),
        ('linear', 'linear', 'Linear + Linear penalty'),
        ('sigmoid', 'exponential', 'Sigmoid + Exponential penalty'),
        ('exponential', 'sigmoid', 'Exponential + Sigmoid penalty'),
    ]

    for idx, (reward, penalty, title) in enumerate(configs):
        ax = axes[idx // 2, idx % 2]
        calculator = ResourceUtilityCalculator(reward_type=reward, penalty_type=penalty)

        scores = [calculator.calculate_resource_score(ui) for ui in u]


        mask_reward = u <= 1.0
        mask_penalty = u > 1.0

        ax.plot(u[mask_reward], np.array(scores)[mask_reward],
                'g-', linewidth=2.5, label=' (u ≤ 1)')
        ax.plot(u[mask_penalty], np.array(scores)[mask_penalty],
                'r-', linewidth=2.5, label=' (u > 1)')

        ax.axvline(x=1.0, color='blue', linestyle='--', linewidth=1.5,
                   label=' (u=1)', alpha=0.7)
        ax.axhline(y=0, color='black', linestyle='-', linewidth=0.5, alpha=0.3)
        ax.grid(True, alpha=0.3)
        ax.set_xlabel(' u', fontsize=11)
        ax.set_ylabel('', fontsize=11)
        ax.set_title(title, fontsize=12, fontweight='bold')
        ax.legend(loc='best')


        score_at_1 = calculator.calculate_resource_score(1.0)
        ax.plot(1.0, score_at_1, 'bo', markersize=8, zorder=5)
        ax.annotate(f'u=1: {score_at_1:.3f}',
                    xy=(1.0, score_at_1),
                    xytext=(1.2, score_at_1),
                    fontsize=9,
                    bbox=dict(boxstyle='round,pad=0.5', facecolor='yellow', alpha=0.7))

    plt.tight_layout()
    output_path = os.path.join(RESULT_DIR, 'piecewise_functions.png')
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    print(f": {output_path}")
    plt.show()


def plot_continuous_functions():
    """plot continuous functions."""
    u = np.linspace(0, 2, 1000)

    fig, axes = plt.subplots(1, 3, figsize=(18, 5))
    fig.suptitle('', fontsize=16, fontweight='bold')

    continuous_types = ['gaussian', 'inverse', 'tanh']
    titles = [
        'Gaussian',
        'Inverse',
        'TanhHyperbolic tangent'
    ]

    for idx, (cont_type, title) in enumerate(zip(continuous_types, titles)):
        ax = axes[idx]
        calculator = ResourceUtilityCalculator(use_continuous=True, continuous_type=cont_type)

        scores = [calculator.calculate_resource_score(ui) for ui in u]

        ax.plot(u, scores, 'b-', linewidth=2.5, label=cont_type)
        ax.axvline(x=1.0, color='red', linestyle='--', linewidth=1.5,
                   label=' (u=1)', alpha=0.7)
        ax.axhline(y=0, color='black', linestyle='-', linewidth=0.5, alpha=0.3)
        ax.grid(True, alpha=0.3)
        ax.set_xlabel(' u', fontsize=11)
        ax.set_ylabel('', fontsize=11)
        ax.set_title(title, fontsize=12, fontweight='bold')
        ax.legend(loc='best')


        score_at_1 = calculator.calculate_resource_score(1.0)
        ax.plot(1.0, score_at_1, 'ro', markersize=8, zorder=5)
        ax.annotate(f': {score_at_1:.3f}',
                    xy=(1.0, score_at_1),
                    xytext=(1.2, score_at_1 * 0.9),
                    fontsize=9,
                    bbox=dict(boxstyle='round,pad=0.5', facecolor='yellow', alpha=0.7))

    plt.tight_layout()
    output_path = os.path.join(RESULT_DIR, 'continuous_functions.png')
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    print(f": {output_path}")
    plt.show()


def plot_comparison():
    """plot comparison."""
    u = np.linspace(0, 2, 1000)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 6))
    fig.suptitle('', fontsize=16, fontweight='bold')


    reward_types = ['linear', 'log', 'sigmoid', 'exponential']
    colors_reward = ['blue', 'green', 'red', 'purple']

    for reward_type, color in zip(reward_types, colors_reward):
        calculator = ResourceUtilityCalculator(reward_type=reward_type, penalty_type='power')
        scores = [calculator.calculate_resource_score(ui) for ui in u if ui <= 1.0]
        u_reward = u[u <= 1.0]
        ax1.plot(u_reward, scores, color=color, linewidth=2.5, label=reward_type)

    ax1.axvline(x=1.0, color='black', linestyle='--', linewidth=1.5, alpha=0.5)
    ax1.grid(True, alpha=0.3)
    ax1.set_xlabel(' u', fontsize=11)
    ax1.set_ylabel('', fontsize=11)
    ax1.set_title(' (u ≤ 1)', fontsize=12, fontweight='bold')
    ax1.legend(loc='best')


    penalty_types = ['linear', 'power', 'log', 'sigmoid', 'exponential']
    colors_penalty = ['blue', 'green', 'orange', 'red', 'purple']

    for penalty_type, color in zip(penalty_types, colors_penalty):
        calculator = ResourceUtilityCalculator(reward_type='log', penalty_type=penalty_type)
        scores = [calculator.calculate_resource_score(ui) for ui in u]
        ax2.plot(u, scores, color=color, linewidth=2.5, label=penalty_type)

    ax2.axvline(x=1.0, color='black', linestyle='--', linewidth=1.5, alpha=0.5)
    ax2.axhline(y=0, color='black', linestyle='-', linewidth=0.5, alpha=0.3)
    ax2.grid(True, alpha=0.3)
    ax2.set_xlabel(' u', fontsize=11)
    ax2.set_ylabel('', fontsize=11)
    ax2.set_title(' ()', fontsize=12, fontweight='bold')
    ax2.legend(loc='best')

    plt.tight_layout()
    output_path = os.path.join(RESULT_DIR, 'function_comparison.png')
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    print(f": {output_path}")
    plt.show()


def plot_multi_resource_example():
    """plot multi resource example."""
    capability = {'cpu': 4.0, 'memory': 8.0, 'disk': 100.0}


    scenarios = {
        'Low utilization': {'cpu': 1.0, 'memory': 2.0, 'disk': 20.0},
        'Balanced utilization': {'cpu': 3.0, 'memory': 6.0, 'disk': 70.0},
        'High utilization': {'cpu': 3.8, 'memory': 7.5, 'disk': 95.0},
        'CPU overload': {'cpu': 5.0, 'memory': 6.0, 'disk': 70.0},
        'All resources overloaded': {'cpu': 5.0, 'memory': 10.0, 'disk': 120.0},
    }

    calculator = ResourceUtilityCalculator(reward_type='log', penalty_type='power')

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 6))
    fig.suptitle('Log + Power penalty', fontsize=16, fontweight='bold')


    scenario_names = list(scenarios.keys())
    total_scores = []

    for name in scenario_names:
        score = calculator.calculate_utility_score(scenarios[name], capability)
        total_scores.append(score)

    colors = ['green', 'yellowgreen', 'gold', 'orange', 'red']
    bars = ax1.bar(range(len(scenario_names)), total_scores, color=colors, alpha=0.7)
    ax1.set_xticks(range(len(scenario_names)))
    ax1.set_xticklabels(scenario_names, rotation=15, ha='right')
    ax1.set_ylabel('', fontsize=11)
    ax1.set_title('', fontsize=12, fontweight='bold')
    ax1.axhline(y=0, color='black', linestyle='-', linewidth=0.5)
    ax1.grid(True, alpha=0.3, axis='y')


    for bar, score in zip(bars, total_scores):
        height = bar.get_height()
        ax1.text(bar.get_x() + bar.get_width()/2., height,
                f'{score:.2f}',
                ha='center', va='bottom' if height > 0 else 'top', fontsize=9)


    resource_names = ['cpu', 'memory', 'disk']
    utilization_matrix = []

    for name in scenario_names:
        utils = [scenarios[name][r] / capability[r] for r in resource_names]
        utilization_matrix.append(utils)

    utilization_matrix = np.array(utilization_matrix).T

    im = ax2.imshow(utilization_matrix, cmap='RdYlGn_r', aspect='auto', vmin=0, vmax=1.5)
    ax2.set_xticks(range(len(scenario_names)))
    ax2.set_xticklabels(scenario_names, rotation=15, ha='right')
    ax2.set_yticks(range(len(resource_names)))
    ax2.set_yticklabels(resource_names)
    ax2.set_title('', fontsize=12, fontweight='bold')


    for i in range(len(resource_names)):
        for j in range(len(scenario_names)):
            text = ax2.text(j, i, f'{utilization_matrix[i, j]:.2f}',
                           ha="center", va="center", color="black", fontsize=9)

    plt.colorbar(im, ax=ax2, label='u')

    plt.tight_layout()
    output_path = os.path.join(RESULT_DIR, 'multi_resource_example.png')
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    print(f": {output_path}")
    plt.show()


if __name__ == "__main__":
    print("...")
    print("-" * 60)


    plot_piecewise_functions()
    plot_continuous_functions()
    plot_comparison()
    plot_multi_resource_example()

    print("-" * 60)
    print("!")
    print(f"\n '{RESULT_DIR}' :")
    print(f"  1. {os.path.join(RESULT_DIR, 'piecewise_functions.png')} - ")
    print(f"  2. {os.path.join(RESULT_DIR, 'continuous_functions.png')} - ")
    print(f"  3. {os.path.join(RESULT_DIR, 'function_comparison.png')} - /")
    print(f"  4. {os.path.join(RESULT_DIR, 'multi_resource_example.png')} - ")
