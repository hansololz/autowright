"""OS notifications (§6): dispatch to the §2 platform notifier.

`post` stays the one patchable seam (the §15 autouse no-op fixture patches
this module attribute); the osascript implementation lives in
platform/darwin.py, and unsupported platforms compose a silent no-op.
The platform import is at module top on purpose: the §6.1 executor replaces
sys.modules["autowright"] with the step-SDK shim, so a real module must
finish its cross-module imports before that swap.
"""
from __future__ import annotations

import uuid

from . import platform


def post(title: str, body: str) -> None:
    platform.current().notifier.post(title, body)


def post_targeted(title: str, body: str, *, kind: str, target: dict) -> None:
    """§6 carrier rule: hand the notification to the Electron shell when a
    §19 `role=shell` subscriber is connected (one `notification.posted`
    event carrying the click target), else post natively. Never both; the
    choice is made from the hub's roles at this instant. The native leg goes
    through the module-level `post` so the §15 test fixture still catches it.
    """
    from .events import hub
    if hub.has_role("shell"):
        hub.publish("notification.posted", id=str(uuid.uuid4()), kind=kind,
                    title=title, body=body, target=target)
        return
    post(title, body)
