# Selectors, actions, and assertions

Appwright locators are lazy descriptions of elements. They do not retain Selenium elements and
do not query the device when created. Every action and query resolves the locator against the
current UI.

## Locator priority

Prefer stable, native-friendly selectors in this order:

1. test ID or resource ID;
2. content description or label;
3. semantic role and accessible name;
4. visible text or placeholder;
5. composed hierarchy selectors.

```python
from appwright.models import Role


app.get_by_test_id("sign-in")
app.get_by_resource_id("com.example.app:id/sign_in")
app.get_by_content_description("Sign in")
app.get_by_label("Email")
app.get_by_role(Role.BUTTON, name="Sign in")
app.get_by_text("Welcome")
app.get_by_placeholder("name@example.com")
```

Exact resource IDs, content descriptions, and class names can compile to native Appium strategies.
Complex text, role, hierarchy, composition, and positional queries may use internally generated
XPath. Appwright never accepts raw XPath from public users.

Selector values compiled to XPath may contain Unicode, emoji, newlines, tabs, and either kind of
quote. They cannot contain characters forbidden by XML 1.0, such as NUL, most C0 controls, or
unpaired Unicode surrogates. Appwright validates these values before sending a command and raises
`SelectorCompilationError` with the invalid code point:

```python
from appwright.selectors import SelectorCompilationError


try:
    app.get_by_text("invalid\x00text").tap()
except SelectorCompilationError as error:
    print(error)
```

`get_by_test_id()` currently maps to Android's resource-ID semantics. Prefer fully qualified IDs
when applications or build variants can create ambiguity.

## Text matching

Text and role-name locators accept strings. Matching is exact by default; pass `exact=False` for
a substring match:

```python
from appwright.models import Role


app.get_by_text("Welcome", exact=True)
app.get_by_text("Welcome", exact=False)  # contains "Welcome"
app.get_by_role(Role.BUTTON, name="Sign in", exact=True)
app.get_by_role(Role.BUTTON, name="Sign", exact=False)
```

Python regular expressions are intentionally unsupported. Android's Appium hierarchy uses XPath
1.0, which cannot reproduce Python regular-expression syntax and flags reliably. Appwright raises
a selector-compilation error instead of silently changing the meaning of a pattern.

Case-insensitive matching exists in the typed selector model, but the convenience factories expose
the stable string contract above. Prefer stable accessibility labels and resource IDs over
case-folded visible text.

## Android accessibility semantics

Selector names describe Android accessibility-tree data, not relationships inferred from the
visual layout:

- `get_by_label("Email")` matches an exact Android `content-desc` value. It does not search for a
  nearby `TextView` or infer a `labelFor` relationship.
- `get_by_content_description("Email")` addresses the same underlying Android attribute directly
  and can use Appium's native accessibility-ID strategy.
- `get_by_placeholder("name@example.com")` matches the exact Android accessibility `hint`
  attribute when the application exposes it.
- `get_by_text()` checks both element text and content description so accessible icon-only controls
  can participate in text lookup.

Semantic roles are conservative Android class or accessibility-attribute mappings. `button`,
`checkbox`, `dialog`, `heading`, `image`, `link`, `list`, `list_item`, `progress_bar`, `radio`,
`slider`, `switch`, `text`, and `textbox` are currently supported. `menu`, `menu_item`, and `tab`
are rejected because Android does not expose a sufficiently reliable backend-neutral mapping for
them. Role behavior can differ between Views, Compose, and cross-platform renderers; use a resource
ID or content description when the accessibility tree does not expose the expected semantics.

## Composition

Scope one locator beneath another with `locator()` and a typed `Selector`:

```python
from appwright.selectors import Selector, TextMatcher


dialog = app.get_by_role(Role.DIALOG)
confirm = dialog.locator(Selector.text(TextMatcher(value="Confirm")))
```

Because `Locator` deliberately has a narrower surface than `App` and `Screen`, use
`locator(Selector...)`, `filter()`, boolean composition, or an app-root locator:

```python
from appwright.selectors import Selector, TextMatcher


premium_card = app.get_by_role(Role.LIST_ITEM).filter(has_text="Premium")
buy_text = Selector.text(TextMatcher(value="Buy"))
buy_button = premium_card.locator(buy_text)

primary = app.get_by_test_id("primary-action")
fallback = app.get_by_text("Continue")
action = primary.or_(fallback)

enabled_submit = app.get_by_role(Role.BUTTON, name="Submit").and_(
    app.get_by_text("Submit")
)
```

Filters support `has`, `has_not`, `has_text`, and `has_not_text`:

```python
row = app.get_by_role(Role.LIST_ITEM).filter(
    has=app.get_by_text("Ada Lovelace"),
    has_not_text="Disabled",
)
```

`has` and `has_not` evaluate descendant elements. `has_text` and `has_not_text` evaluate accessible
text on the selected element *and* all of its descendants; accessible text includes Android text
and content-description values. This makes container filtering work when a nested child renders
the visible label:

```python
premium_card = app.get_by_test_id("plan-card").filter(has_text="Premium")
available_card = premium_card.filter(has_not_text="Unavailable")
```

Both text filters perform contains matching.

Use positional selection only when position is part of the UI contract:

```python
first_result = app.get_by_role(Role.LIST_ITEM).first
last_result = app.get_by_role(Role.LIST_ITEM).last
third_result = app.get_by_role(Role.LIST_ITEM).nth(2)
```

## Strictness

Actions and single-element queries require exactly one match. Zero matches are retried until the
deadline. More than one final match raises `StrictModeViolationError` rather than selecting an
arbitrary element.

```python
from appwright.errors import StrictModeViolationError


try:
    app.get_by_text("Delete").tap()
except StrictModeViolationError as error:
    print(error.details.locator)
```

`count()`, `all()`, and `element_infos()` are multi-element operations:

```python
items = app.get_by_role(Role.LIST_ITEM)

assert items.count() >= 1
lazy_items = items.all()
snapshots = items.element_infos()
```

`all()` returns locators tied to the matches' current positions. `element_infos()` returns
immutable Pydantic snapshots suitable for inspection and logging.

## Auto-waiting and actionability

Every action repeatedly resolves its locator and checks the conditions relevant to that action.
For example:

| Action | Required conditions |
| --- | --- |
| `tap()` | present, visible, enabled, stable |
| `fill()` | present, visible, enabled, editable, stable |
| `check()` | present, visible, enabled, checkable, stable |
| `screenshot()` | visible, stable |
| `drag_to()` | visible and stable source and target |

Stability compares UI snapshots across the configured quiet window. Recoverable stale-element
failures trigger re-resolution. Off-screen actions can attempt an Android scroll gesture before
retrying.

Override the action deadline with `datetime.timedelta`:

```python
from datetime import timedelta


app.get_by_text("Continue").tap(timeout=timedelta(seconds=10))
```

`trial=True` runs resolution and actionability without sending input. `force=True` skips optional
actionability checks, but never skips existence or strictness:

```python
button = app.get_by_text("Continue")
button.tap(trial=True)
button.tap(force=True)
```

## Actions

The initial action surface includes:

```python
from appwright.models import Direction


locator.tap()
locator.double_tap()
locator.long_press()
locator.fill("value")
locator.clear()
locator.press("ENTER")
locator.check()
locator.uncheck()
locator.swipe(Direction.UP, percent=0.75)
locator.scroll(Direction.DOWN)
locator.drag_to(target)
locator.screenshot()
```

System-level input is available from the device:

```python
from appwright.models import Key, Point


device.keyboard.press(Key.BACK)
device.touchscreen.tap(Point(x=120, y=640))
```

## Queries and waits

Queries inspect one strict match:

```python
locator.is_visible()
locator.is_enabled()
locator.is_checked()
locator.text_content()
locator.accessible_name()
locator.bounds()
locator.element_info()
```

Boolean state queries return `False` for no match and still reject duplicate matches. For test
synchronization, prefer retrying assertions over immediate query assertions.

```python
from appwright.models import WaitState


locator.wait_for(WaitState.VISIBLE)
locator.wait_for(WaitState.HIDDEN)
```

## Retrying assertions

Assertions poll until their expectation timeout and include the last observed state in failures:

```python
from appwright.sync_api import expect


expect(locator).to_be_visible()
expect(locator).to_be_hidden()
expect(locator).to_be_enabled()
expect(locator).to_be_disabled()
expect(locator).to_be_editable()
expect(locator).to_be_checked()
expect(locator).to_be_unchecked()
expect(locator).to_be_focused()
expect(locator).to_be_selected()
expect(locator).to_have_text("Ready")
expect(locator).to_contain_text("Read")
expect(locator).to_have_accessible_name("Continue")
expect(locator).to_have_resource_id("com.example.app:id/continue")
expect(locator).to_have_count(3)
```

Negate any assertion through the fluent `not_` property:

```python
expect(locator).not_.to_be_visible()
expect(locator).not_.to_have_text("Loading")
```

In the async API, await both actions and assertions:

```python
await locator.tap()
await expect(locator).to_be_visible()
```
