import argparse
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('path', type=Path)
parser.add_argument('--write', action='store_true')
args = parser.parse_args()
if args.write:
    args.path.write_text('synthetic fixture mutation', encoding='utf-8')
else:
    print(args.path.read_text(encoding='utf-8'))
