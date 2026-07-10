"""Public Appwright exception hierarchy."""

from appwright.core.errors import AppiumCompatibilityError as AppiumCompatibilityError
from appwright.core.errors import AppiumUnavailableError as AppiumUnavailableError
from appwright.core.errors import AppwrightError as AppwrightError
from appwright.core.errors import DeviceDisconnectedError as DeviceDisconnectedError
from appwright.core.errors import DeviceNotFoundError as DeviceNotFoundError
from appwright.core.errors import ExpectationError as ExpectationError
from appwright.core.errors import InvalidSelectorError as InvalidSelectorError
from appwright.core.errors import ProtocolError as ProtocolError
from appwright.core.errors import SessionTaintedError as SessionTaintedError
from appwright.core.errors import StrictModeViolationError as StrictModeViolationError
from appwright.core.errors import TargetClosedError as TargetClosedError
from appwright.core.errors import TimeoutError as TimeoutError
from appwright.core.errors import UnsupportedOperationError as UnsupportedOperationError
