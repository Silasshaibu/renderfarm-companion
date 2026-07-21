"""
Driver script executed by hython to headlessly render a specific ROP node
from a .hip file. Invoked by renderfarm_worker.py — not meant to be run
directly except for debugging.

Usage:
    hython.exe houdini_render_rop.py <hip_path> <rop_path> <start> <end>
"""

import sys

import hou  # noqa: available only inside hython's bundled Python


def main():
    if len(sys.argv) < 5:
        print("Usage: houdini_render_rop.py <hip_path> <rop_path> <start> <end>")
        sys.exit(2)

    hip_path, rop_path, start, end = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])

    hou.hipFile.load(hip_path)

    rop = hou.node(rop_path)
    if rop is None:
        print(f"ERROR: ROP node not found: {rop_path}")
        sys.exit(1)

    print(f"Rendering {rop_path} frames {start}-{end}...")
    rop.render(frame_range=(start, end, 1), verbose=True)
    print("Render complete.")


if __name__ == "__main__":
    main()
