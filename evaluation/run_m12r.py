"""CLI entry point for the capacity-adjusted M12R experiment."""

from evaluation.m12r_governed_semantic_generation import prepare, run

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "run"))
    args = parser.parse_args()
    (prepare if args.command == "prepare" else run)()
