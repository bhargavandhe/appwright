"""Framework-owned enums."""

from enum import StrEnum


class ServerMode(StrEnum):
    LOCAL = "local"
    REMOTE = "remote"


class SessionState(StrEnum):
    STARTING = "starting"
    ACTIVE = "active"
    TAINTED = "tainted"
    CLOSING = "closing"
    CLOSED = "closed"


class DeviceState(StrEnum):
    ONLINE = "online"
    OFFLINE = "offline"
    UNAUTHORIZED = "unauthorized"
    UNKNOWN = "unknown"


class Role(StrEnum):
    BUTTON = "button"
    CHECKBOX = "checkbox"
    DIALOG = "dialog"
    HEADING = "heading"
    IMAGE = "image"
    LINK = "link"
    LIST = "list"
    LIST_ITEM = "list_item"
    MENU = "menu"
    MENU_ITEM = "menu_item"
    PROGRESS_BAR = "progress_bar"
    RADIO = "radio"
    SLIDER = "slider"
    SWITCH = "switch"
    TAB = "tab"
    TEXT = "text"
    TEXTBOX = "textbox"


class Direction(StrEnum):
    UP = "up"
    DOWN = "down"
    LEFT = "left"
    RIGHT = "right"


class Orientation(StrEnum):
    PORTRAIT = "PORTRAIT"
    LANDSCAPE = "LANDSCAPE"


class Key(StrEnum):
    BACK = "BACK"
    ENTER = "ENTER"
    HOME = "HOME"
    MENU = "MENU"
    POWER = "POWER"
    VOLUME_DOWN = "VOLUME_DOWN"
    VOLUME_UP = "VOLUME_UP"


class WaitState(StrEnum):
    ATTACHED = "attached"
    DETACHED = "detached"
    VISIBLE = "visible"
    HIDDEN = "hidden"


class ActionKind(StrEnum):
    TAP = "tap"
    DOUBLE_TAP = "double_tap"
    LONG_PRESS = "long_press"
    FILL = "fill"
    CLEAR = "clear"
    PRESS = "press"
    CHECK = "check"
    UNCHECK = "uncheck"
    SWIPE = "swipe"
    SCROLL = "scroll"
    DRAG_TO = "drag_to"
    SCREENSHOT = "screenshot"


class ErrorCode(StrEnum):
    APPIUM_UNAVAILABLE = "appium_unavailable"
    APPIUM_INCOMPATIBLE = "appium_incompatible"
    DEVICE_NOT_FOUND = "device_not_found"
    DEVICE_DISCONNECTED = "device_disconnected"
    EXPECTATION_FAILED = "expectation_failed"
    INVALID_SELECTOR = "invalid_selector"
    INDETERMINATE_ACTION = "indeterminate_action"
    PROTOCOL_ERROR = "protocol_error"
    SESSION_TAINTED = "session_tainted"
    STRICT_MODE = "strict_mode"
    TARGET_CLOSED = "target_closed"
    TIMEOUT = "timeout"
    UNSUPPORTED_OPERATION = "unsupported_operation"


class TraceEventKind(StrEnum):
    ACTION = "action"
    ASSERTION = "assertion"
    ERROR = "error"
    QUERY = "query"
    SERVER_LOG = "server_log"
    SESSION = "session"


class TraceArtifactKind(StrEnum):
    CAPABILITIES = "capabilities"
    HIERARCHY = "hierarchy"
    SCREENSHOT = "screenshot"
    SERVER_LOG = "server_log"


class TraceMode(StrEnum):
    ALWAYS = "always"
    OFF = "off"
    RETAIN_ON_FAILURE = "retain-on-failure"


class ScreenshotMode(StrEnum):
    OFF = "off"
    ONLY_ON_FAILURE = "only-on-failure"


class LogStream(StrEnum):
    STANDARD_ERROR = "stderr"
    STANDARD_OUTPUT = "stdout"


class LocatorStrategy(StrEnum):
    ID = "id"
    ACCESSIBILITY_ID = "accessibility_id"
    CLASS_NAME = "class_name"
    XPATH = "xpath"


class SelectorKind(StrEnum):
    RESOURCE_ID = "resource_id"
    CONTENT_DESCRIPTION = "content_description"
    CLASS_NAME = "class_name"
    TEXT = "text"
    LABEL = "label"
    PLACEHOLDER = "placeholder"
    TEST_ID = "test_id"
    ROLE = "role"
    AND = "and"
    OR = "or"
    DESCENDANT = "descendant"
    HAS = "has"
    HAS_NOT = "has_not"
    HAS_TEXT = "has_text"
    HAS_NOT_TEXT = "has_not_text"
    NTH = "nth"


class MatchMode(StrEnum):
    EXACT = "exact"
    CONTAINS = "contains"
    REGEX = "regex"


class CapabilityValueKind(StrEnum):
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    NULL = "null"
    ARRAY = "array"
    OBJECT = "object"


class FrameworkCapability(StrEnum):
    PLATFORM_NAME = "platformName"
    AUTOMATION_NAME = "appium:automationName"
    DEVICE_SERIAL = "appium:udid"
    PLATFORM_VERSION = "appium:platformVersion"
    EMULATOR_NAME = "appium:avd"
    NO_RESET = "appium:noReset"
    APPLICATION_PATH = "appium:app"
    APPLICATION_PACKAGE = "appium:appPackage"
    APPLICATION_ACTIVITY = "appium:appActivity"


class MobileCommand(StrEnum):
    ACTIVATE_APP = "mobile: activateApp"
    CLEAR_APP = "mobile: clearApp"
    CLICK_GESTURE = "mobile: clickGesture"
    DOUBLE_CLICK_GESTURE = "mobile: doubleClickGesture"
    DRAG_GESTURE = "mobile: dragGesture"
    INSTALL_APP = "mobile: installApp"
    LONG_CLICK_GESTURE = "mobile: longClickGesture"
    SCROLL_GESTURE = "mobile: scrollGesture"
    SWIPE_GESTURE = "mobile: swipeGesture"
    REMOVE_APP = "mobile: removeApp"
    TERMINATE_APP = "mobile: terminateApp"
