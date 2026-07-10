# ADR 0003: Typed records and descriptive state names

Status: accepted

All framework-owned records are strict, frozen Pydantic models. Enums identify framework states,
commands, actions, and strategies. Temporary mappings exist only where a third-party protocol
requires them and are converted at the adapter boundary.

Project-authored Python state names cannot begin with an underscore. Supported API is determined
by documented exports, not language-level privacy. The AST policy check covers source, tests,
scripts, generated code, comprehensions, exception targets, and structural pattern targets.
