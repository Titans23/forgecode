"""Explicit built-in bundle compositions; optional surfaces import only at launch."""
import argparse
import importlib
import json
import sys


PROFILES = {
    'terminal': {'bundles': ['core', 'terminal'], 'entry': 'forge.cli:app'},
    'evaluation': {'bundles': ['core', 'evaluation'], 'entry': 'benchmark.cli:main'},
}


def main(argv=None):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--profile', choices=tuple(PROFILES), default='terminal')
    parser.add_argument('--show-profile', action='store_true')
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args, unknown = parser.parse_known_args(sys.argv[1:] if argv is None else argv)
    remainder = unknown + args.arguments
    profile = PROFILES[args.profile]
    if args.show_profile:
        print(json.dumps({'schema_version': 'forge.launch.profile.v1', 'name': args.profile,
            **profile, 'sandbox_selected_by_profile': False}))
        return 0
    module, name = profile['entry'].split(':')
    entry = getattr(importlib.import_module(module), name)
    if args.profile == 'terminal':
        entry(args=remainder)
        return 0
    return int(entry(remainder) or 0)


if __name__ == '__main__':
    raise SystemExit(main())
