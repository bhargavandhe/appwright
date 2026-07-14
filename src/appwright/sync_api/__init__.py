"""Synchronous Appwright API."""

from appwright.api.generated.sync_api import (
    Android as Android,
)
from appwright.api.generated.sync_api import (
    App as App,
)
from appwright.api.generated.sync_api import (
    Appwright as Appwright,
)
from appwright.api.generated.sync_api import (
    AppwrightContextManager as AppwrightContextManager,
)
from appwright.api.generated.sync_api import (
    Device as Device,
)
from appwright.api.generated.sync_api import (
    Keyboard as Keyboard,
)
from appwright.api.generated.sync_api import (
    Locator as Locator,
)
from appwright.api.generated.sync_api import (
    LocatorAssertions as LocatorAssertions,
)
from appwright.api.generated.sync_api import (
    MobileApp as MobileApp,
)
from appwright.api.generated.sync_api import Screen as GeneratedDeviceSurface
from appwright.api.generated.sync_api import (
    Touchscreen as Touchscreen,
)
from appwright.api.generated.sync_api import (
    expect as expect,
)
from appwright.api.generated.sync_api import (
    sync_appwright as sync_appwright,
)
from appwright.screens.elements import Button as Button
from appwright.screens.elements import Checkbox as Checkbox
from appwright.screens.elements import Choice as Choice
from appwright.screens.elements import Element as Element
from appwright.screens.elements import Scrollable as Scrollable
from appwright.screens.elements import TextField as TextField
from appwright.screens.elements import button as button
from appwright.screens.elements import by_accessibility_id as by_accessibility_id
from appwright.screens.elements import by_id as by_id
from appwright.screens.elements import by_role as by_role
from appwright.screens.elements import by_text as by_text
from appwright.screens.elements import checkbox as checkbox
from appwright.screens.elements import choice as choice
from appwright.screens.elements import element as element
from appwright.screens.elements import scrollable as scrollable
from appwright.screens.elements import text_contains as text_contains
from appwright.screens.elements import text_field as text_field
from appwright.screens.errors import LifecycleTimeoutError as LifecycleTimeoutError
from appwright.screens.errors import TransitionFailureError as TransitionFailureError
from appwright.screens.errors import TransitionTimeoutError as TransitionTimeoutError
from appwright.screens.interruptions import InterruptionError as InterruptionError
from appwright.screens.model import AppScope as AppScope
from appwright.screens.model import DeviceScope as DeviceScope
from appwright.screens.model import DeviceScreen as DeviceScreen
from appwright.screens.model import Interruption as Interruption
from appwright.screens.model import Readiness as Readiness
from appwright.screens.model import Screen as Screen
from appwright.screens.model import all_of as all_of
from appwright.screens.model import any_of as any_of
from appwright.screens.model import visible as visible
from appwright.screens.recovery import BackRecovery as BackRecovery
from appwright.screens.recovery import RecoveryError as RecoveryError
from appwright.screens.recovery import back_until as back_until
from appwright.screens.targets import ScreenChoice as ScreenChoice
from appwright.screens.targets import ScreenTarget as ScreenTarget
from appwright.screens.targets import one_of as one_of
from appwright.screens.transitions import ScreenTimeoutError as ScreenTimeoutError

DeviceSurface = GeneratedDeviceSurface
