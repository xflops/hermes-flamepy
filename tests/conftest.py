from hermes_flamepy import common

# Tests do not depend on the host's network interfaces, except test_common, which
# is about them and restores the real function. Patched at import, because test
# modules build ids from it at import time.
REAL_MACHINE_ID = common.machine_id
common.machine_id = lambda: "0123456789ab"
