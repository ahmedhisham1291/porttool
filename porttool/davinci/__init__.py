"""Adapters for the Vector command-line tools. Public API: see HANDOFF.md.

Nothing about the real tools is hard-coded: command lines are templates and output
patterns are settings in the ``[davinci]`` table of porttool.toml. The built-in defaults
are guesses, labelled UNVERIFIED wherever they are shown.
"""

from porttool.davinci.gui import davinci_gui_findings
from porttool.davinci.runner import DaVinciToolRunner
from porttool.davinci.settings import DaVinciSettings, load_settings

__all__ = ["DaVinciSettings", "DaVinciToolRunner", "davinci_gui_findings", "load_settings"]
