'''Linux service handoff belongs to the Linux CI matrix; Windows tests its Job owner.'''
import sys

# These tests require Linux subreapers and /proc. Both Linux CI jobs collect
# them; do not turn platform inapplicability into a missing required resource.
collect_ignore = ['test_service_handoff.py'] if not sys.platform.startswith('linux') else []
