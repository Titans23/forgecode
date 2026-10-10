"""Run the real Windows canary entry. Missing native hosts/setup are blocked, never skipped."""
from forge.sandbox.doctor import main

if __name__ == '__main__':
    raise SystemExit(main())
