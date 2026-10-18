"""
Available utility functions
"""

# ============================================================================

# ============================================================================

REWARD_FUNCTIONS = {
    'linear': {
        'name': 'Linear',
        'formula': 'f(u) = u',
        'description': 'Linear growth',
        'characteristics': 'Uniform reward with a simple shape',
        'use_case': 'Simple cases requiring an interpretable score',
        'desmos': 'https://www.desmos.com/calculator'
    },
    'log': {
        'name': 'Logarithmic',
        'formula': 'f(u) = ln(1 + u)',
        'description': 'Logarithmic growth with diminishing returns',
        'characteristics': 'Reward growth slows as utilization rises',
        'use_case': 'Recommended for balanced resource use',
        'desmos': 'https://www.desmos.com/calculator'
    },
    'sigmoid': {
        'name': 'Sigmoid',
        'formula': 'f(u) = 2 / (1 + e^(-5u))',
        'description': 'S-shaped curve with rapid saturation',
        'characteristics': 'Slow early growth, rapid middle growth, then saturation',
        'use_case': 'Useful when low utilization should have little effect',
        'desmos': 'https://www.desmos.com/calculator'
    },
    'exponential': {
        'name': 'Exponential',
        'formula': 'f(u) = e^u - 1',
        'description': 'Exponential growth',
        'characteristics': 'Reward growth accelerates with utilization',
        'use_case': 'Strongly rewards high utilization',
        'desmos': 'https://www.desmos.com/calculator'
    }
}

PENALTY_FUNCTIONS = {
    'linear': {
        'name': 'Linear penalty',
        'formula': 'f(u) = max_reward - (u - 1)',
        'description': 'Linear decline',
        'severity': 'Mild',
        'characteristics': 'Mild linear penalty after overload',
        'use_case': 'Allows some overload',
        'desmos': 'https://www.desmos.com/calculator'
    },
    'power': {
        'name': 'Power penalty',
        'formula': 'f(u) = max_reward - (u - 1)^2',
        'description': 'Quadratic decline',
        'severity': 'Moderate',
        'characteristics': 'Penalty rises with overload',
        'use_case': 'Recommended balanced overload penalty',
        'desmos': 'https://www.desmos.com/calculator'
    },
    'log': {
        'name': 'Logarithmic penalty',
        'formula': 'f(u) = max_reward - ln(1 + (u - 1))',
        'description': 'Logarithmic decline',
        'severity': 'Mild',
        'characteristics': 'Tolerates mild overload with slowly growing penalty',
        'use_case': 'Allows mild overload in elastic systems',
        'desmos': 'https://www.desmos.com/calculator'
    },
    'sigmoid': {
        'name': 'Sigmoid penalty',
        'formula': 'f(u) = max_reward * 2 / (1 + e^(5(u-1)))',
        'description': 'S-shaped decline',
        'severity': 'Threshold-based',
        'characteristics': 'Declines rapidly beyond the threshold',
        'use_case': 'Tolerates mild overload but penalizes severe overload',
        'desmos': 'https://www.desmos.com/calculator'
    },
    'exponential': {
        'name': 'Exponential penalty',
        'formula': 'f(u) = max_reward * e^(-(u-1))',
        'description': 'Exponential decline',
        'severity': 'Strict',
        'characteristics': 'Score decays rapidly after overload',
        'use_case': 'Avoids overload for strict SLA workloads',
        'desmos': 'https://www.desmos.com/calculator'
    }
}

# ============================================================================

# ============================================================================

CONTINUOUS_FUNCTIONS = {
    'gaussian': {
        'name': 'Gaussian function',
        'formula': 'f(u) = e^(-(u-1)^2)',
        'description': 'Gaussian shape peaking at u=1',
        'characteristics': 'Symmetric penalty for low and high utilization',
        'use_case': 'Targets utilization near 100% for capacity planning',
        'desmos': 'https://www.desmos.com/calculator'
    },
    'inverse': {
        'name': 'Inverse function',
        'formula': 'f(u) = 1 / (1 + |u - 1|)',
        'description': 'Mild symmetric inverse shape',
        'characteristics': 'Smooth near u=1 and slowly declining away from it',
        'use_case': 'Allows deviation while favoring the target',
        'desmos': None
    },
    'tanh': {
        'name': 'Hyperbolic tangent',
        'formula': 'f(u) = 1 - tanh(|u - 1|)',
        'description': 'Smooth symmetric hyperbolic tangent',
        'characteristics': 'Smooth symmetric decline',
        'use_case': 'Useful for a smooth symmetric penalty',
        'desmos': None
    }
}

# ============================================================================

# ============================================================================

RECOMMENDED_CONFIGS = {
    'general': {
        'name': 'General case',
        'reward': 'log',
        'penalty': 'power',
        'description': 'Suitable for general resource optimization',
        'reason': 'Balances resource use and overload penalties'
    },
    'cloud_cost': {
        'name': 'Cloud cost optimization',
        'reward': 'log',
        'penalty': 'linear',
        'description': 'Reduces cloud resource cost',
        'reason': 'Encourages utilization and tolerates mild overload'
    },
    'realtime': {
        'name': 'Real-time system',
        'reward': 'sigmoid',
        'penalty': 'exponential',
        'description': 'Low-latency real-time system',
        'reason': 'Prioritizes quick response and avoids overload'
    },
    'batch': {
        'name': 'Batch-processing system',
        'reward': 'exponential',
        'penalty': 'power',
        'description': 'Batch and offline computation',
        'reason': 'Rewards high utilization with moderate overload penalty'
    },
    'strict_sla': {
        'name': 'Strict SLA',
        'reward': 'log',
        'penalty': 'exponential',
        'description': 'Production environment with a strict SLA',
        'reason': 'Avoids all overload'
    },
    'capacity_planning': {
        'name': 'Capacity planning',
        'continuous': 'gaussian',
        'description': 'Precise capacity planning',
        'reason': 'Matches capacity closely to demand'
    }
}

# ============================================================================

# ============================================================================

STATISTICS = {
    'total_reward_functions': len(REWARD_FUNCTIONS),
    'total_penalty_functions': len(PENALTY_FUNCTIONS),
    'total_continuous_functions': len(CONTINUOUS_FUNCTIONS),
    'total_piecewise_combinations': len(REWARD_FUNCTIONS) * len(PENALTY_FUNCTIONS),
    'total_configurations': len(REWARD_FUNCTIONS) * len(PENALTY_FUNCTIONS) + len(CONTINUOUS_FUNCTIONS)
}


# ============================================================================

# ============================================================================

def print_all_functions():
    """Print the available utility functions."""

    print("=" * 100)
    print("Resource utility function catalog".center(100))
    print("=" * 100)


    print("\n u ≤ 1 {}".format(STATISTICS['total_reward_functions']))
    print("-" * 100)
    for key, func in REWARD_FUNCTIONS.items():
        print(f"\n{key.upper():<15} - {func['name']}")
        print(f"  Formula: {func['formula']}")
        print(f"  Description: {func['description']}")
        print(f"  Properties: {func['characteristics']}")
        print(f"  Use case: {func['use_case']}")
        if func['desmos']:
            print(f"  Graph: {func['desmos']}")


    print("\n\n u > 1 {}".format(STATISTICS['total_penalty_functions']))
    print("-" * 100)
    for key, func in PENALTY_FUNCTIONS.items():
        print(f"\n{key.upper():<15} - {func['name']}")
        print(f"  Formula: {func['formula']}")
        print(f"  Description: {func['description']}")
        print(f"  Severity: {func['severity']}")
        print(f"  Properties: {func['characteristics']}")
        print(f"  Use case: {func['use_case']}")
        if func['desmos']:
            print(f"  Graph: {func['desmos']}")


    print("\n\n u=1{}".format(STATISTICS['total_continuous_functions']))
    print("-" * 100)
    for key, func in CONTINUOUS_FUNCTIONS.items():
        print(f"\n{key.upper():<15} - {func['name']}")
        print(f"  Formula: {func['formula']}")
        print(f"  Description: {func['description']}")
        print(f"  Properties: {func['characteristics']}")
        print(f"  Use case: {func['use_case']}")
        if func['desmos']:
            print(f"  Graph: {func['desmos']}")


    print("\n\nConfiguration")
    print("-" * 100)
    for key, config in RECOMMENDED_CONFIGS.items():
        print(f"\n{config['name']}")
        if 'continuous' in config:
            print(f"  Configuration: continuous='{config['continuous']}'")
        else:
            print(f"  Configuration: reward='{config['reward']}', penalty='{config['penalty']}'")
        print(f"  Description: {config['description']}")
        print(f"  Reason: {config['reason']}")


    print("\n\n")
    print("-" * 100)
    print(f"  Reward functions: {STATISTICS['total_reward_functions']}")
    print(f"  Penalty functions: {STATISTICS['total_penalty_functions']}")
    print(f"  Continuous functions: {STATISTICS['total_continuous_functions']}")
    print(f"  Piecewise combinations: {STATISTICS['total_piecewise_combinations']}")
    print(f"  Available configurations: {STATISTICS['total_configurations']}")

    print("\n" + "=" * 100)


if __name__ == "__main__":
    print_all_functions()

    print("\n\n:")
    print("-" * 100)
    print("""
from resource_utility import ResourceUtilityCalculator

# Configuration
calc = ResourceUtilityCalculator(reward_type='log', penalty_type='power')

# Configuration
from function_catalog import REWARD_FUNCTIONS, PENALTY_FUNCTIONS, RECOMMENDED_CONFIGS

#
for name, info in REWARD_FUNCTIONS.items():
    print(f"{name}: {info['description']}")
    """)
