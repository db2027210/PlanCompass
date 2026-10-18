#!/usr/bin/env python
"""Module."""


import sys
import os


def print_header(text):
    """print header."""
    print("\n" + "=" * 80)
    print(text.center(80))
    print("=" * 80 + "\n")


def run_script(script_name, description):
    """run script."""
    print_header(description)
    try:
        with open(script_name, 'r', encoding='utf-8') as f:
            code = f.read()
        exec(code)
        return True
    except Exception as e:
        print(f"❌ : {e}")
        return False


def main():
    """main."""
    print("\n" + "█" * 80)
    print("█" + " " * 78 + "█")
    print("█" + " - ".center(76) + "  █")
    print("█" + " " * 78 + "█")
    print("█" * 80)


    print_header("")
    try:
        import numpy
        import matplotlib
        print("✓ NumPy :", numpy.__version__)
        print("✓ Matplotlib :", matplotlib.__version__)
    except ImportError as e:
        print(f"❌ : {e}")
        print("\n: pip install -r requirements.txt")
        return


    os.makedirs('result', exist_ok=True)

    scripts = [
        ('function_catalog.py', '1. '),
        ('examples.py', '2. '),
        ('generate_reports.py', '3. '),
        ('shell/test_experience_table_gradient_optimizer.py', '4. '),
        ('run_all.py', '5. '),
    ]

    # Launch monitor for the 5 benchmarks
    subprocess.Popen(["python", "shell/benchmark_monitor.py"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    success_count = 0
    for script, desc in scripts:
        if run_script(script, desc):
            success_count += 1
            print(f"\n✓ {desc} ")
        else:
            print(f"\n✗ {desc} ")


    print_header("4. ")
    print(": Graph")
    print(": python visualize_functions.py")


    print_header("")
    print(f": {success_count}/{len(scripts)}")
    print(f"\n 'result/' ")
    print("\n:")
    print("  python examples.py              - ")
    print("  python generate_reports.py      - ")
    print("  python visualize_functions.py   - ")
    print("  python function_catalog.py      - ")

    print("\n" + "=" * 80)
    print("".center(80))
    print("=" * 80 + "\n")


if __name__ == "__main__":
    main()
