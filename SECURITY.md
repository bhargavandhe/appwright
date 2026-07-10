# Security policy

## Supported versions

Appwright is pre-1.0. Security fixes are provided for the latest released minor version only.
Older pre-release versions may be asked to upgrade before receiving a fix.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability.

Email **bhargavandhe2310@gmail.com** with:

- the affected version or commit;
- the impact and affected component;
- reproduction steps or a proof of concept;
- relevant logs with secrets removed; and
- any suggested remediation or disclosure constraints.

You should receive an acknowledgement within seven days. No resolution deadline is guaranteed,
but confirmed reports will receive status updates while remediation is active. Please allow time
for a fix and coordinated disclosure before publishing details.

## Scope

Security-sensitive areas include credential redaction, remote Appium endpoints, direct connection
handling, capability validation, trace and pytest artifacts, subprocess lifecycle, and dependency
supply chain. Screenshots and UI hierarchy artifacts may contain application data and must be
handled as sensitive test output even when framework-controlled secrets are redacted.
