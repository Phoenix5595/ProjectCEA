import sys
from unittest.mock import MagicMock

# Mock shared module before any app imports
# This ensures that 'from shared.logging import get_logger' works in all tests
if "shared" not in sys.modules:
    sys.modules["shared"] = MagicMock()
    sys.modules["shared.logging"] = MagicMock()
    sys.modules["shared.logging"].get_logger = MagicMock(return_value=MagicMock())
