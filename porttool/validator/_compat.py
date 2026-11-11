"""Interface compatibility of a provider port and a requester port. Internal."""

from __future__ import annotations

from porttool.core.models import InterfaceKind, Operation, PortInterface


def _signature(operation: Operation) -> tuple[tuple[str, str, str | None], ...]:
    return tuple((a.name, a.direction.value, a.type_path) for a in operation.arguments)


def incompatibility(provider: PortInterface, requester: PortInterface) -> str:
    """Why a requester port of ``requester`` cannot be fed by a provider port of ``provider``.

    Empty string when the interfaces are compatible: the same interface, or the same kind
    and everything the requester uses exists on the provider side with the same types.
    """
    if provider.path == requester.path:
        return ""
    if provider.kind is not requester.kind:
        return (
            f"{provider.name} is a {provider.kind.value} interface and {requester.name} is a "
            f"{requester.kind.value} interface"
        )
    if provider.kind is InterfaceKind.CLIENT_SERVER:
        for operation in requester.operations:
            served = provider.operation(operation.name)
            if served is None:
                return (
                    f"operation {operation.name} of {requester.name} is missing in {provider.name}"
                )
            if _signature(served) != _signature(operation):
                return (
                    f"operation {operation.name} has different arguments in {provider.name} "
                    f"and {requester.name}"
                )
        return ""
    if provider.kind is InterfaceKind.MODE_SWITCH:
        if provider.mode_group_path != requester.mode_group_path:
            return f"{provider.name} and {requester.name} use different mode groups"
        return ""
    for element in requester.elements:
        sent = provider.element(element.name)
        if sent is None:
            return f"element {element.name} of {requester.name} is missing in {provider.name}"
        if sent.type_path != element.type_path:
            return (
                f"element {element.name} has data type {sent.type_path} in {provider.name} "
                f"and {element.type_path} in {requester.name}"
            )
        if sent.is_queued != element.is_queued:
            return (
                f"element {element.name} is queued in one of {provider.name} and "
                f"{requester.name} and not in the other"
            )
    return ""
