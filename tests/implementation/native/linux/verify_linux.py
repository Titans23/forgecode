"""Explicit native entry; never pytest-skips a missing Ubuntu runner into a pass."""
from forge.sandbox.doctor import main


if __name__ == '__main__':
    raise SystemExit(main())
