"""Structured errors raised by composed typed-screen operations."""

from __future__ import annotations

from appwright.operations import ActionReceipt, OperationDeadline
from appwright.screens.transitions import ScreenTimeoutError, TransitionHistory


class LifecycleTimeoutError(TimeoutError):
    """A typed mobile operation could not enter its lifecycle before expiry."""

    def __init__(self, operation: str, deadline: OperationDeadline) -> None:
        self.operation = operation
        self.deadline = deadline
        super().__init__(f"{operation} could not enter the mobile lifecycle before its deadline")


class TransitionTimeoutError(TimeoutError):
    """A dispatched action did not reach its declared screen destination."""

    def __init__(
        self,
        receipt: ActionReceipt,
        screen_timeout: ScreenTimeoutError,
    ) -> None:
        self.receipt = receipt
        self.screen_timeout = screen_timeout
        super().__init__(
            f"{receipt.action.value} was {receipt.dispatch_state.value}, but its screen "
            f"transition failed: {screen_timeout}"
        )

    @property
    def transition_history(self) -> TransitionHistory:
        """Return the exact destination history from the wrapped screen timeout."""

        return self.screen_timeout.transition_history


class TransitionFailureError(RuntimeError):
    """A dispatched action was followed by a non-timeout transition failure."""

    def __init__(
        self,
        receipt: ActionReceipt,
        transition_error: Exception,
    ) -> None:
        self.receipt = receipt
        self.transition_error = transition_error
        super().__init__(
            f"{receipt.action.value} was {receipt.dispatch_state.value}, but its screen "
            f"transition failed: {transition_error}"
        )
