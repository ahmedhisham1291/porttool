"""Build docs/PortTool_Engineer_Guide.pdf.

Run from anywhere:  python docs/guide/build_guide.py [output.pdf]
Needs reportlab (not a runtime dependency of PortTool).
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO))

import content_a  # noqa: E402
import content_b  # noqa: E402
import content_c  # noqa: E402
from guide_lib import GuideDoc  # noqa: E402


def main() -> None:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "docs" / "PortTool_Engineer_Guide.pdf"
    story = [
        *content_a.cover(),
        *content_a.chapter_1(),
        *content_a.chapter_2(),
        *content_a.chapter_3(),
        *content_a.chapter_4(),
        *content_a.chapter_5(),
        *content_b.chapter_6(),
        *content_b.chapter_7(),
        *content_b.chapter_8(),
        *content_b.chapter_9(),
        *content_c.chapter_10(),
        *content_c.chapter_11(),
        *content_c.appendix_a(),
        *content_c.appendix_b(),
    ]
    GuideDoc(str(target)).multiBuild(story)
    print(f"wrote {target} ({target.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
