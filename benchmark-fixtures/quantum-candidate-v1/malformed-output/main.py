import argparse
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--input")
parser.add_argument("--output", type=Path)
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
(args.output / "candidate-summary.json").write_text("{not-json", encoding="utf-8")
