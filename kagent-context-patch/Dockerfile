FROM ghcr.io/kagent-dev/kagent/app:0.7.8

# Copy patched source files over the originals.
# The packages are installed as editable (via .pth files), so replacing
# the source files here is sufficient — no pip install needed.
COPY patches/types.py       /.kagent/packages/kagent-adk/src/kagent/adk/types.py
COPY patches/_a2a.py        /.kagent/packages/kagent-adk/src/kagent/adk/_a2a.py
COPY patches/cli.py         /.kagent/packages/kagent-adk/src/kagent/adk/cli.py

# Patch ADK's compaction.py to add INFO-level logging at every decision point.
# This replaces the site-packages copy (not an editable install, so we must
# overwrite it directly).
COPY patches/compaction.py  /.kagent/.venv/lib/python3.13/site-packages/google/adk/apps/compaction.py
