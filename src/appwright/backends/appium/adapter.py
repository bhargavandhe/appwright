"""Official Appium Python client adapter."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any, TypeVar, cast

from appium import webdriver
from appium.options.android import UiAutomator2Options
from appium.webdriver.client_config import AppiumClientConfig
from appium.webdriver.common.appiumby import AppiumBy
from selenium.common.exceptions import (
    InvalidSessionIdException,
    NoSuchElementException,
    StaleElementReferenceException,
    WebDriverException,
)

from appwright.backends.appium.models import (
    AppCommandArguments,
    DragGestureArguments,
    ElementGestureArguments,
    InstallAppArguments,
    PointGestureArguments,
    RemoveAppArguments,
    ScrollGestureArguments,
    SwipeGestureArguments,
)
from appwright.backends.appium.service import ManagedAppiumService
from appwright.backends.appium.worker import SessionWorker
from appwright.backends.base import (
    BackendError,
    BackendFailure,
    BackendFailureKind,
    RecoverableBackendError,
)
from appwright.models.config import (
    AdditionalCapability,
    AndroidSessionOptions,
    AppiumServer,
    CapabilityValue,
    SessionCapabilities,
)
from appwright.models.data import (
    ActionRequest,
    ActionResult,
    ElementSnapshot,
    HierarchySource,
    InstallApplicationRequest,
    OperationResult,
    Point,
    QueryResult,
    Rect,
    Screenshot,
    ServerLogRecord,
    UninstallApplicationRequest,
)
from appwright.models.enums import (
    ActionKind,
    CapabilityValueKind,
    Direction,
    Key,
    LocatorStrategy,
    MobileCommand,
    ServerMode,
)
from appwright.selectors.compiler import LocatorPlan

Result = TypeVar("Result")


def action_mobile_command(kind: ActionKind) -> MobileCommand | None:
    if kind is ActionKind.DOUBLE_TAP:
        return MobileCommand.DOUBLE_CLICK_GESTURE
    if kind is ActionKind.LONG_PRESS:
        return MobileCommand.LONG_CLICK_GESTURE
    if kind is ActionKind.SWIPE:
        return MobileCommand.SWIPE_GESTURE
    if kind is ActionKind.SCROLL:
        return MobileCommand.SCROLL_GESTURE
    return None


def capability_value_to_python(value: CapabilityValue) -> object:
    if value.kind is CapabilityValueKind.STRING:
        return value.string_value
    if value.kind is CapabilityValueKind.INTEGER:
        return value.integer_value
    if value.kind is CapabilityValueKind.NUMBER:
        return value.number_value
    if value.kind is CapabilityValueKind.BOOLEAN:
        return value.boolean_value
    if value.kind is CapabilityValueKind.NULL:
        return None
    if value.kind is CapabilityValueKind.ARRAY:
        return [capability_value_to_python(item) for item in value.items]
    return {entry.name: capability_value_to_python(entry.value) for entry in value.entries}


def capability_value_from_python(value: object) -> CapabilityValue:
    if value is None:
        return CapabilityValue(kind=CapabilityValueKind.NULL)
    if isinstance(value, bool):
        return CapabilityValue(kind=CapabilityValueKind.BOOLEAN, boolean_value=value)
    if isinstance(value, str):
        return CapabilityValue(kind=CapabilityValueKind.STRING, string_value=value)
    if isinstance(value, int):
        return CapabilityValue(kind=CapabilityValueKind.INTEGER, integer_value=value)
    if isinstance(value, float):
        return CapabilityValue(kind=CapabilityValueKind.NUMBER, number_value=value)
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        entries: list[AdditionalCapability] = []
        for name, entry_value in mapping.items():
            if not isinstance(name, str):
                raise TypeError("Appium capability names must be strings")
            entries.append(
                AdditionalCapability(
                    name=name,
                    value=capability_value_from_python(entry_value),
                )
            )
        return CapabilityValue(kind=CapabilityValueKind.OBJECT, entries=tuple(entries))
    if isinstance(value, Sequence):
        sequence = cast(Sequence[object], value)
        return CapabilityValue(
            kind=CapabilityValueKind.ARRAY,
            items=tuple(capability_value_from_python(item) for item in sequence),
        )
    raise TypeError(f"unsupported Appium capability value: {type(value).__name__}")


def validate_session_capabilities(value: object) -> SessionCapabilities:
    if not isinstance(value, Mapping):
        raise TypeError("Appium returned non-object session capabilities")
    mapping = cast(Mapping[object, object], value)
    converted = capability_value_from_python(mapping)
    return SessionCapabilities(entries=converted.entries)


def apply_capability(options: UiAutomator2Options, capability: AdditionalCapability) -> None:
    options.set_capability(capability.name, capability_value_to_python(capability.value))


def create_options(options: AndroidSessionOptions) -> UiAutomator2Options:
    appium_options = UiAutomator2Options()
    selector = options.device
    if selector.serial is not None:
        appium_options.udid = selector.serial
    if selector.platform_version is not None:
        appium_options.platform_version = selector.platform_version
    if selector.emulator_name is not None:
        appium_options.avd = selector.emulator_name
    appium_options.no_reset = True
    for capability in options.capabilities:
        apply_capability(appium_options, capability)
    return appium_options


def appium_by(strategy: LocatorStrategy) -> str:
    if strategy is LocatorStrategy.ID:
        return AppiumBy.ID
    if strategy is LocatorStrategy.ACCESSIBILITY_ID:
        return AppiumBy.ACCESSIBILITY_ID
    if strategy is LocatorStrategy.CLASS_NAME:
        return AppiumBy.CLASS_NAME
    return AppiumBy.XPATH


def boolean_attribute(element: Any, name: str) -> bool:
    value = element.get_attribute(name)
    if isinstance(value, bool):
        return value
    return str(value).lower() == "true"


def string_attribute(element: Any, name: str) -> str:
    value = element.get_attribute(name)
    return "" if value is None else str(value)


def snapshot_element(element: Any) -> ElementSnapshot:
    class_name = string_attribute(element, "className")
    return ElementSnapshot(
        identity=str(element.id),
        text=element.text or "",
        accessible_name=string_attribute(element, "contentDescription"),
        resource_id=string_attribute(element, "resourceId"),
        class_name=class_name,
        package_name=string_attribute(element, "package"),
        displayed=bool(element.is_displayed()),
        enabled=bool(element.is_enabled()),
        selected=bool(element.is_selected()),
        checked=boolean_attribute(element, "checked"),
        checkable=boolean_attribute(element, "checkable"),
        focusable=boolean_attribute(element, "focusable"),
        focused=boolean_attribute(element, "focused"),
        editable=class_name.endswith("EditText"),
        bounds=Rect.model_validate(element.rect),
        window_id=string_attribute(element, "window-id"),
    )


def find_elements(driver: Any, plan: LocatorPlan) -> tuple[Any, ...]:
    elements = tuple(driver.find_elements(by=appium_by(plan.strategy), value=plan.value))
    if plan.package is None:
        return elements
    return tuple(
        element for element in elements if string_attribute(element, "package") == plan.package
    )


def execute_query(driver: Any, plan: LocatorPlan) -> QueryResult:
    try:
        elements = find_elements(driver, plan)
        return QueryResult(elements=tuple(snapshot_element(element) for element in elements))
    except StaleElementReferenceException as error:
        raise RecoverableBackendError(
            BackendFailure(kind=BackendFailureKind.RECOVERABLE, message=str(error))
        ) from error
    except InvalidSessionIdException as error:
        raise BackendError(
            BackendFailure(kind=BackendFailureKind.NOT_STARTED, message=str(error))
        ) from error
    except WebDriverException as error:
        raise BackendError(
            BackendFailure(kind=BackendFailureKind.UNKNOWN, message=str(error))
        ) from error


def execute_action(driver: Any, plan: LocatorPlan, request: ActionRequest) -> ActionResult:
    try:
        elements = find_elements(driver, plan)
        if len(elements) != 1:
            raise BackendError(
                BackendFailure(
                    kind=BackendFailureKind.MATCH_COUNT,
                    message=f"locator resolved to {len(elements)} elements",
                    match_count=len(elements),
                )
            )
        element = elements[0]
        if not request.trial:
            if request.kind is ActionKind.TAP:
                element.click()
            elif request.kind is ActionKind.DOUBLE_TAP:
                double_tap_arguments = ElementGestureArguments(element_id=str(element.id))
                driver.execute_script(
                    MobileCommand.DOUBLE_CLICK_GESTURE.value,
                    double_tap_arguments.model_dump(by_alias=True, exclude_none=True),
                )
            elif request.kind is ActionKind.LONG_PRESS:
                long_press_arguments = ElementGestureArguments(element_id=str(element.id))
                driver.execute_script(
                    MobileCommand.LONG_CLICK_GESTURE.value,
                    long_press_arguments.model_dump(by_alias=True, exclude_none=True),
                )
            elif request.kind is ActionKind.FILL:
                element.clear()
                element.send_keys(request.text or "")
            elif request.kind is ActionKind.CLEAR:
                element.clear()
            elif request.kind is ActionKind.PRESS:
                element.send_keys(request.key or "")
            elif request.kind in {ActionKind.CHECK, ActionKind.UNCHECK}:
                checked = boolean_attribute(element, "checked")
                if (request.kind is ActionKind.CHECK and not checked) or (
                    request.kind is ActionKind.UNCHECK and checked
                ):
                    element.click()
            elif request.kind is ActionKind.SWIPE:
                direction = request.direction
                if direction is None:
                    raise ValueError("swipe requires a direction")
                swipe_arguments = SwipeGestureArguments(
                    element_id=str(element.id),
                    direction=direction,
                    percent=request.percent or 0.75,
                )
                driver.execute_script(
                    MobileCommand.SWIPE_GESTURE.value,
                    swipe_arguments.model_dump(mode="json", by_alias=True, exclude_none=True),
                )
            elif request.kind is ActionKind.SCROLL:
                direction = request.direction
                if direction is None:
                    raise ValueError("scroll requires a direction")
                scroll_arguments = ScrollGestureArguments(
                    element_id=str(element.id),
                    direction=direction,
                    percent=request.percent or 0.75,
                )
                driver.execute_script(
                    MobileCommand.SCROLL_GESTURE.value,
                    scroll_arguments.model_dump(mode="json", by_alias=True, exclude_none=True),
                )
            elif request.kind in {ActionKind.DRAG_TO, ActionKind.SCREENSHOT}:
                raise BackendError(
                    BackendFailure(
                        kind=BackendFailureKind.UNKNOWN,
                        message=f"{request.kind.value} uses a dedicated backend command",
                    )
                )
            else:
                raise BackendError(
                    BackendFailure(
                        kind=BackendFailureKind.UNKNOWN,
                        message=f"action is not implemented: {request.kind.value}",
                    )
                )
        return ActionResult(element=snapshot_element(element))
    except (StaleElementReferenceException, NoSuchElementException) as error:
        raise RecoverableBackendError(
            BackendFailure(kind=BackendFailureKind.RECOVERABLE, message=str(error))
        ) from error


def strict_driver_element(driver: Any, plan: LocatorPlan) -> Any:
    elements = find_elements(driver, plan)
    if len(elements) != 1:
        raise BackendError(
            BackendFailure(
                kind=BackendFailureKind.MATCH_COUNT,
                message=f"locator resolved to {len(elements)} elements",
                match_count=len(elements),
            )
        )
    return elements[0]


def execute_drag(driver: Any, source: LocatorPlan, destination: LocatorPlan) -> ActionResult:
    source_element = strict_driver_element(driver, source)
    destination_element = strict_driver_element(driver, destination)
    destination_snapshot = snapshot_element(destination_element)
    end_x = round(destination_snapshot.bounds.x + destination_snapshot.bounds.width / 2)
    end_y = round(destination_snapshot.bounds.y + destination_snapshot.bounds.height / 2)
    arguments = DragGestureArguments(
        element_id=str(source_element.id),
        end_x=end_x,
        end_y=end_y,
    )
    driver.execute_script(
        MobileCommand.DRAG_GESTURE.value,
        arguments.model_dump(mode="json", by_alias=True, exclude_none=True),
    )
    return ActionResult(element=snapshot_element(source_element))


def execute_scroll_into_view(driver: Any, plan: LocatorPlan) -> OperationResult:
    try:
        targets = find_elements(driver, plan)
        if any(bool(target.is_displayed()) for target in targets):
            return OperationResult(succeeded=True)
        ancestor_containers: list[Any] = []
        for target in targets:
            ancestor_containers.extend(
                target.find_elements(
                    by=AppiumBy.XPATH,
                    value="ancestor::*[@scrollable='true'][1]",
                )
            )
        if ancestor_containers:
            containers = tuple(ancestor_containers)
        else:
            discovered = tuple(
                driver.find_elements(by=AppiumBy.XPATH, value="//*[@scrollable='true']")
            )
            containers = (
                discovered
                if plan.package is None
                else tuple(
                    container
                    for container in discovered
                    if string_attribute(container, "package") == plan.package
                )
            )
        if not containers:
            return OperationResult(succeeded=False)
        arguments = ScrollGestureArguments(
            element_id=str(containers[0].id),
            direction=Direction.DOWN,
        )
        response = driver.execute_script(
            MobileCommand.SCROLL_GESTURE.value,
            arguments.model_dump(mode="json", by_alias=True, exclude_none=True),
        )
        return OperationResult(succeeded=response is not False)
    except (StaleElementReferenceException, NoSuchElementException) as error:
        raise RecoverableBackendError(
            BackendFailure(kind=BackendFailureKind.RECOVERABLE, message=str(error))
        ) from error


def capture_element_screenshot(driver: Any, plan: LocatorPlan, path: Path | None) -> bytes:
    element = strict_driver_element(driver, plan)
    content = bytes(element.screenshot_as_png)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return content


class AppiumBackend:
    """Backend implementation backed by Appium 3."""

    def __init__(self, server: AppiumServer) -> None:
        self.server = server
        self.service: ManagedAppiumService | None = None
        self.server_url: str | None = None
        self.worker: SessionWorker | None = None
        self.session_options: AndroidSessionOptions | None = None
        self.session_capabilities: SessionCapabilities | None = None
        self.server_logs: list[ServerLogRecord] = []

    def sanitize_message(self, message: str) -> str:
        access_key = self.server.security.access_key
        if access_key is None:
            return message
        secret = access_key.get_secret_value()
        return message.replace(secret, "[REDACTED]")

    def sanitize_server_logs(
        self,
        records: tuple[ServerLogRecord, ...] | list[ServerLogRecord],
    ) -> tuple[ServerLogRecord, ...]:
        return tuple(
            ServerLogRecord(
                timestamp=record.timestamp,
                stream=record.stream,
                message=self.sanitize_message(record.message),
            )
            for record in records
        )

    async def invoke(
        self,
        operation: Callable[[Any], Result],
        timeout: timedelta,
        command: MobileCommand | None = None,
    ) -> Result:
        worker = self.require_worker()
        try:
            return await worker.invoke(operation, timeout)
        except RecoverableBackendError as error:
            failure = error.failure
            raise RecoverableBackendError(
                BackendFailure(
                    kind=failure.kind,
                    message=self.sanitize_message(failure.message),
                    match_count=failure.match_count,
                    appium_command=failure.appium_command or command,
                )
            ) from error
        except BackendError as error:
            failure = error.failure
            raise BackendError(
                BackendFailure(
                    kind=failure.kind,
                    message=self.sanitize_message(failure.message),
                    match_count=failure.match_count,
                    appium_command=failure.appium_command or command,
                )
            ) from error
        except WebDriverException as error:
            raise BackendError(
                BackendFailure(
                    kind=BackendFailureKind.UNKNOWN,
                    message=self.sanitize_message(str(error)),
                    appium_command=command,
                )
            ) from error
        except Exception as error:
            raise BackendError(
                BackendFailure(
                    kind=BackendFailureKind.UNKNOWN,
                    message=self.sanitize_message(str(error)),
                    appium_command=command,
                )
            ) from error

    async def start(self, timeout: timedelta) -> None:
        if self.server.mode is ServerMode.REMOTE:
            self.server_url = str(self.server.url)
            return
        self.service = ManagedAppiumService(
            host=self.server.host,
            port=self.server.port,
            executable=self.server.executable,
            timeout=timeout,
        )
        try:
            self.server_url = await self.service.start()
        except Exception as error:
            service = self.service
            self.service = None
            await service.stop()
            self.server_logs.extend(self.sanitize_server_logs(service.server_logs))
            raise BackendError(
                BackendFailure(
                    kind=BackendFailureKind.UNAVAILABLE,
                    message=self.sanitize_message(str(error)),
                )
            ) from error

    def create_driver(
        self,
        options: AndroidSessionOptions,
        timeout: timedelta | None = None,
    ) -> Any:
        if self.server_url is None:
            raise BackendError(
                BackendFailure(
                    kind=BackendFailureKind.NOT_STARTED,
                    message="Appium backend has not been started",
                )
            )
        selected_timeout = options.timeouts.transport if timeout is None else timeout
        client_config = AppiumClientConfig(
            remote_server_addr=self.server_url,
            direct_connection=False,
            ignore_certificates=not self.server.security.verify_tls,
            timeout=min(
                options.timeouts.transport.total_seconds(),
                selected_timeout.total_seconds(),
            ),
            username=self.server.security.username,
            password=(
                None
                if self.server.security.access_key is None
                else self.server.security.access_key.get_secret_value()
            ),
        )
        driver = webdriver.Remote(
            self.server_url,
            options=create_options(options),
            client_config=client_config,
        )
        try:
            raw_capabilities = cast(
                object,
                driver.capabilities,  # pyright: ignore[reportUnknownMemberType]
            )
            self.session_capabilities = validate_session_capabilities(raw_capabilities)
        except (TypeError, ValueError) as error:
            driver.quit()
            raise BackendError(
                BackendFailure(
                    kind=BackendFailureKind.UNKNOWN,
                    message=f"invalid Appium session capabilities: {error}",
                )
            ) from error
        return driver

    async def create_session(self, options: AndroidSessionOptions) -> None:
        await self.close_session()
        self.session_options = options
        try:
            self.worker = await SessionWorker.create(
                lambda: self.create_driver(options, options.timeouts.server_start),
                timeout=options.timeouts.server_start,
            )
        except Exception as error:
            raise BackendError(
                BackendFailure(
                    kind=BackendFailureKind.UNAVAILABLE,
                    message=self.sanitize_message(str(error)),
                )
            ) from error

    def require_worker(self) -> SessionWorker:
        if self.worker is None:
            raise BackendError(
                BackendFailure(
                    kind=BackendFailureKind.NOT_STARTED,
                    message="no Appium device session is active",
                )
            )
        return self.worker

    async def query(self, plan: LocatorPlan, timeout: timedelta) -> QueryResult:
        return await self.invoke(lambda driver: execute_query(driver, plan), timeout)

    async def perform(
        self,
        plan: LocatorPlan,
        request: ActionRequest,
        timeout: timedelta,
    ) -> ActionResult:
        return await self.invoke(
            lambda driver: execute_action(driver, plan, request),
            timeout,
            action_mobile_command(request.kind),
        )

    async def screenshot(self, path: Path | None, timeout: timedelta) -> Screenshot:
        content = await self.invoke(lambda driver: capture_screenshot(driver, path), timeout)
        return Screenshot(content=content, path=path)

    async def element_screenshot(
        self,
        plan: LocatorPlan,
        path: Path | None,
        timeout: timedelta,
    ) -> Screenshot:
        content = await self.invoke(
            lambda driver: capture_element_screenshot(driver, plan, path),
            timeout,
        )
        return Screenshot(content=content, path=path)

    async def drag(
        self,
        source: LocatorPlan,
        destination: LocatorPlan,
        timeout: timedelta,
    ) -> ActionResult:
        return await self.invoke(
            lambda driver: execute_drag(driver, source, destination),
            timeout,
            MobileCommand.DRAG_GESTURE,
        )

    async def scroll_into_view(
        self,
        plan: LocatorPlan,
        timeout: timedelta,
    ) -> OperationResult:
        return await self.invoke(
            lambda driver: execute_scroll_into_view(driver, plan),
            timeout,
            MobileCommand.SCROLL_GESTURE,
        )

    async def hierarchy(self, timeout: timedelta) -> HierarchySource:
        content = await self.invoke(lambda driver: str(driver.page_source), timeout)
        return HierarchySource(content=content)

    async def read_server_logs(self) -> tuple[ServerLogRecord, ...]:
        service = self.service
        if service is None:
            return self.sanitize_server_logs(self.server_logs)
        return self.sanitize_server_logs(service.snapshot_logs())

    async def install_app(
        self,
        request: InstallApplicationRequest,
        timeout: timedelta,
    ) -> OperationResult:
        arguments = InstallAppArguments(
            app_path=str(request.path),
            replace=request.replace,
            grant_permissions=request.grant_permissions,
        )
        response = await self.invoke(
            lambda driver: driver.execute_script(
                MobileCommand.INSTALL_APP.value,
                arguments.model_dump(mode="json", by_alias=True),
            ),
            timeout,
            MobileCommand.INSTALL_APP,
        )
        return OperationResult(succeeded=response is not False)

    async def uninstall_app(
        self,
        request: UninstallApplicationRequest,
        timeout: timedelta,
    ) -> OperationResult:
        arguments = RemoveAppArguments(app_id=request.package, keep_data=request.keep_data)
        response = await self.invoke(
            lambda driver: driver.execute_script(
                MobileCommand.REMOVE_APP.value,
                arguments.model_dump(mode="json", by_alias=True),
            ),
            timeout,
            MobileCommand.REMOVE_APP,
        )
        return OperationResult(succeeded=response is not False)

    async def execute_app_command(
        self,
        command: MobileCommand,
        package: str,
        timeout: timedelta,
    ) -> None:
        arguments = AppCommandArguments(app_id=package)
        await self.invoke(
            lambda driver: driver.execute_script(
                command.value,
                arguments.model_dump(by_alias=True),
            ),
            timeout,
            command,
        )

    async def activate_app(self, package: str, timeout: timedelta) -> None:
        await self.execute_app_command(MobileCommand.ACTIVATE_APP, package, timeout)

    async def terminate_app(self, package: str, timeout: timedelta) -> None:
        await self.execute_app_command(MobileCommand.TERMINATE_APP, package, timeout)

    async def clear_app(self, package: str, timeout: timedelta) -> None:
        await self.execute_app_command(MobileCommand.CLEAR_APP, package, timeout)

    async def press_key(self, key: Key, timeout: timedelta) -> None:
        key_code = android_key_code(key)
        await self.invoke(lambda driver: driver.press_keycode(key_code), timeout)

    async def tap_point(self, point: Point, timeout: timedelta) -> None:
        arguments = PointGestureArguments(x=round(point.x), y=round(point.y))
        await self.invoke(
            lambda driver: driver.execute_script(
                MobileCommand.CLICK_GESTURE.value,
                arguments.model_dump(by_alias=True),
            ),
            timeout,
            MobileCommand.CLICK_GESTURE,
        )

    async def close_session(self) -> None:
        worker = self.worker
        self.worker = None
        if worker is not None:
            cleanup_timeout = (
                self.session_options.timeouts.transport
                if self.session_options is not None
                else timedelta(seconds=10)
            )
            await worker.close(timeout=min(cleanup_timeout, timedelta(seconds=10)))

    async def close(self) -> None:
        session_error: BaseException | None = None
        try:
            await self.close_session()
        except BaseException as error:
            session_error = error
        service = self.service
        self.service = None
        if service is not None:
            try:
                await service.stop()
            finally:
                self.server_logs.extend(self.sanitize_server_logs(service.server_logs))
        if session_error is not None:
            raise session_error


def android_key_code(key: Key) -> int:
    if key is Key.HOME:
        return 3
    if key is Key.BACK:
        return 4
    if key is Key.VOLUME_UP:
        return 24
    if key is Key.VOLUME_DOWN:
        return 25
    if key is Key.POWER:
        return 26
    if key is Key.MENU:
        return 82
    return 66


def capture_screenshot(driver: Any, path: Path | None) -> bytes:
    content = bytes(driver.get_screenshot_as_png())
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return content
