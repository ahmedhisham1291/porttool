"""Render a plan into files and write the synthetic repo."""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path

from tests.fixtures.generator import davinci_seam as seam
from tests.fixtures.generator import plan as p
from tests.fixtures.generator.arxml import Renderer, document
from tests.fixtures.generator.spec import SMALL, FileLayout, GeneratorSpec
from tests.fixtures.generator.truth import GroundTruth, build_truth


def render(spec: GeneratorSpec = SMALL) -> tuple[dict[str, bytes], GroundTruth]:
    """Return every file of the synthetic repo (repo-relative path -> bytes) and the truth.

    Pure and deterministic: the same spec gives the same bytes. Nothing is written.
    """
    plan = p.build_plan(spec)
    renderer = Renderer(uuids=spec.with_uuids)
    root = seam.WORKSPACE
    texts: dict[str, str] = {}

    for swc in plan.swcs:
        swc.file = f"{root}/ComponentTypes/{swc.name}.arxml"
        texts[swc.file] = document([(p.CT, renderer.swc(swc))])
        if swc.export_folder:
            # PENDING-DISCOVERY: a model-owned SWC also exists as the model tool's export.
            texts[f"{swc.export_folder}/{swc.name}.arxml"] = texts[swc.file]
    texts[f"{root}/ComponentTypes/{p.COMPOSITION_NAME}.arxml"] = document(
        [(p.CT, renderer.composition(plan))]
    )

    per_object = spec.layout is FileLayout.PER_OBJECT
    grouped_interfaces: list[tuple[str, str]] = []
    for interface in plan.interfaces:
        content = (p.IF, renderer.interface(interface))
        if per_object:
            interface.file = f"{root}/PortInterfaces/{interface.name}.arxml"
            texts[interface.file] = document([content])
        else:
            interface.file = f"{root}/PortInterfaces/PortInterfaces.arxml"
            grouped_interfaces.append(content)
    if grouped_interfaces:
        texts[f"{root}/PortInterfaces/PortInterfaces.arxml"] = document(grouped_interfaces)

    texts[f"{root}/DataTypes/BaseTypes.arxml"] = document(
        (p.BASE_TYPES, renderer.base_type(base)) for base in plan.base_types
    )
    texts[f"{root}/DataTypes/Units.arxml"] = document(
        (p.UNITS, renderer.unit(unit)) for unit in plan.units
    )
    grouped_types: list[tuple[str, str]] = []
    for data_type in plan.data_types:
        parts = [(p.DT, renderer.data_type(data_type))]
        if data_type.is_scalar:
            parts.append((p.COMPU_METHODS, renderer.compu_method(data_type)))
            parts.append((p.DATA_CONSTRS, renderer.constraint(data_type)))
        if per_object:
            data_type.file = f"{root}/DataTypes/{data_type.name}.arxml"
            texts[data_type.file] = document(parts)
        else:
            data_type.file = f"{root}/DataTypes/DataTypes.arxml"
            grouped_types.extend(parts)
    if grouped_types:
        texts[f"{root}/DataTypes/DataTypes.arxml"] = document(grouped_types)

    workspace_files = sorted(
        texts_name for texts_name in texts if texts_name.startswith(root + "/")
    )
    ecuc_file = seam.ecuc_path(plan)
    dcf_file = seam.dcf_path(plan)
    dpa_file = seam.dpa_path(plan)
    texts[ecuc_file] = seam.render_ecuc(plan)
    texts[dcf_file] = seam.render_dcf(plan, workspace_files)
    texts[dpa_file] = seam.render_dpa(plan)
    texts["owners.csv"] = seam.render_owners(plan)
    locked = sorted(swc.name for swc in plan.swcs if swc.owner.value == "locked")
    texts["locked.txt"] = "# SWCs PortTool must never edit, one name per line.\n" + "".join(
        f"{name}\n" for name in locked
    )
    texts["porttool.toml"] = seam.render_config(plan)
    texts[f"{seam.GEN_DATA}/Rte_Type.h"] = (
        "/* Synthetic placeholder for generated code. PortTool never edits this folder. */\n"
    )

    files = {name: text.encode("utf-8") for name, text in texts.items()}
    return files, build_truth(plan, files, root, ecuc_file, dcf_file, dpa_file)


def generate(target: str | os.PathLike[str], spec: GeneratorSpec = SMALL) -> GroundTruth:
    """Write the synthetic repo into ``target`` (created if missing, must be empty).

    Returns the :class:`GroundTruth` with ``repo_root`` set. Raises ``ValueError`` for a
    spec that cannot be generated and ``FileExistsError`` for a non-empty target.
    """
    root = Path(os.path.abspath(target))
    if root.exists() and any(root.iterdir()):
        raise FileExistsError(f"{root} is not empty")
    files, truth = render(spec)
    made: set[Path] = set()
    for name, data in files.items():
        path = root / name
        if path.parent not in made:
            path.parent.mkdir(parents=True, exist_ok=True)
            made.add(path.parent)
        with open(path, "wb") as stream:
            stream.write(data)
    return dataclasses.replace(truth, repo_root=root)
