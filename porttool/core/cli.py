"""Commands of porttool.core: ``porttool config check`` and ``porttool config discover``."""

from __future__ import annotations

from pathlib import Path

import click

from porttool.cli import echo_findings
from porttool.core.config_loader import (
    CONFIG_FILE_NAME,
    DiscoveredPaths,
    discover_paths,
    load_config,
    save_config,
)
from porttool.core.errors import ConfigError

_REPO = click.option(
    "--repo",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("."),
    show_default=True,
    help="Root folder of the ECU repository.",
)


@click.group("config")
def config_group() -> None:
    """Check or create the project configuration (porttool.toml)."""


@config_group.command("check")
@_REPO
def check(repo: Path) -> None:
    """Load porttool.toml, owners.csv and locked.txt and report every problem."""
    loaded = load_config(repo)
    code = echo_findings(loaded.findings)
    config = loaded.config
    click.echo(
        f"{loaded.config_file}: {len(config.arxml_roots)} ARXML folder(s), "
        f"{len(config.owners)} SWC(s) in owners.csv, {len(config.locked_swcs)} locked SWC(s)."
    )
    raise click.exceptions.Exit(code)


def _echo_candidates(found: DiscoveredPaths) -> None:
    rows = (
        ("project file (.dpa)", found.dpa_files),
        ("workspace (.dcf)", found.dcf_files),
        ("ARXML folders", found.arxml_roots),
        ("ECUC file", found.ecuc_files),
        ("flat extract", found.flat_extracts),
        ("flat map", found.flat_maps),
        ("generated code", found.gen_data_dirs),
    )
    for title, paths in rows:
        click.echo(f"{title}:")
        if not paths:
            click.echo("  (none found)")
        for index, path in enumerate(paths):
            click.echo(f"  {'*' if index == 0 or title == 'ARXML folders' else ' '} {path}")


@config_group.command("discover")
@_REPO
@click.option(
    "--dpa",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="The DaVinci Configurator project file, if the repo has several.",
)
@click.option("--write", is_flag=True, help=f"Write the proposal (*) to {CONFIG_FILE_NAME}.")
@click.option(
    "--force", is_flag=True, help=f"With --write: replace an existing {CONFIG_FILE_NAME}."
)
def discover(repo: Path, dpa: Path | None, write: bool, force: bool) -> None:
    """Propose the paths for porttool.toml from the .dpa file and a scan of the repo."""
    found = discover_paths(repo, dpa)
    _echo_candidates(found)
    code = echo_findings(found.findings)
    if write:
        target = found.repo_root / CONFIG_FILE_NAME
        if target.exists() and not force:
            raise ConfigError(f"{target} exists already. Use --force to replace it.")
        if not found.arxml_roots:
            raise ConfigError("No ARXML folder was found, so no configuration can be written.")
        click.echo(f"written: {save_config(found.propose())}")
    raise click.exceptions.Exit(code)


def register_cli(group: click.Group) -> None:
    """Add the ``config`` command group to the root command line."""
    group.add_command(config_group)
