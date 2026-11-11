"""Parameters of the synthetic repo generator and its two presets."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class FileLayout(StrEnum):
    #: All port interfaces in one file, all implementation data types in one file.
    GROUPED = "grouped"
    #: One file per port interface and one per implementation data type (with its
    #: compu method and constraint). PENDING-DISCOVERY: whether the real DaVinci
    #: Developer workspace is laid out like this is not known yet.
    PER_OBJECT = "per-object"


@dataclass(frozen=True, slots=True)
class GeneratorSpec:
    """What to generate. The same spec (including ``seed``) always gives the same bytes."""

    swc_count: int = 3
    #: Average number of ports per SWC. Ignored when ``port_count`` is given.
    ports_per_swc: int = 4
    connector_count: int = 5
    layout: FileLayout = FileLayout.GROUPED
    #: Share of SWCs that are model-owned (Simulink / TargetLink), rounded to whole SWCs.
    model_owned_share: float = 1 / 3
    seed: int = 1
    #: Exact total number of ports, spread evenly over the SWCs.
    port_count: int | None = None
    #: Number of port interfaces. Default: half the number of provider ports.
    interface_count: int | None = None
    #: Number of implementation data types. Default: one per 20 ports, at least 4.
    data_type_count: int | None = None
    #: Number of SWCs listed in locked.txt (never model-owned ones).
    locked_count: int = 0
    #: Share of the port interfaces that are client-server (the rest is sender-receiver).
    client_server_share: float = 0.15
    project_name: str = "Synthetic"
    #: Package of the ECUC module definitions. PENDING-DISCOVERY: "/MICROSAR" is what
    #: Vector projects are expected to use; the AUTOSAR standard path is "/AUTOSAR/EcucDefs".
    ecuc_definition_root: str = "/MICROSAR"
    #: Write a UUID attribute on identifiable elements. PENDING-DISCOVERY: which elements
    #: carry a UUID in real DaVinci files, and in which letter case, is not known yet.
    with_uuids: bool = True

    @property
    def total_ports(self) -> int:
        if self.port_count is not None:
            return self.port_count
        return self.swc_count * self.ports_per_swc

    @property
    def model_owned_count(self) -> int:
        return min(self.swc_count, round(self.swc_count * self.model_owned_share))

    def validate(self) -> None:
        """Raise ``ValueError`` with a clear message when the spec cannot be generated."""
        if self.swc_count < 1:
            raise ValueError("swc_count must be at least 1")
        if self.total_ports < self.swc_count:
            raise ValueError("every SWC needs at least one port: raise ports_per_swc or port_count")
        if self.connector_count < 0:
            raise ValueError("connector_count must not be negative")
        if self.connector_count > 0 and self.swc_count < 2:
            raise ValueError("connectors need at least two SWCs")
        if self.connector_count > 0 and self.connector_count >= self.total_ports:
            raise ValueError(
                "connector_count must be smaller than the number of ports: every connector "
                "needs its own requester port, and at least one provider port must exist"
            )
        if not 0.0 <= self.model_owned_share <= 1.0:
            raise ValueError("model_owned_share must be between 0 and 1")
        if not 0.0 <= self.client_server_share <= 1.0:
            raise ValueError("client_server_share must be between 0 and 1")
        if self.locked_count < 0 or self.locked_count + self.model_owned_count > self.swc_count:
            raise ValueError("locked_count plus the model-owned SWCs exceed swc_count")
        if self.interface_count is not None and self.interface_count < 1:
            raise ValueError("interface_count must be at least 1")
        if self.data_type_count is not None and self.data_type_count < 1:
            raise ValueError("data_type_count must be at least 1")
        if not self.project_name.isidentifier():
            raise ValueError("project_name must be a plain identifier")


#: 3 SWCs (one model-owned), 12 ports, 5 connectors, 4 data types, grouped files.
SMALL = GeneratorSpec()

#: 300 SWCs, 20,000 ports, 15,000 connectors, about 2,000 files, one file per object.
LARGE = GeneratorSpec(
    swc_count=300,
    ports_per_swc=67,
    port_count=20_000,
    connector_count=15_000,
    layout=FileLayout.PER_OBJECT,
    model_owned_share=0.2,
    seed=2026,
    interface_count=1_400,
    data_type_count=290,
    locked_count=5,
)

PRESETS: dict[str, GeneratorSpec] = {"small": SMALL, "large": LARGE}
