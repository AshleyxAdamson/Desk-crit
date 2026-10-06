# COPIED by tools/sync-from-lumr.sh from lumr-studio/server/lumr_studio/errors.py. Do not edit: change it in Lumr Studio and run the script.
"""The one error type the tools raise on purpose."""


class StudioError(Exception):
    """A failure the caller can fix.

    The message must say what was wrong and how to fix it, in one or two plain
    sentences. The MCP layer passes it to the model as a tool error, so it must
    never contain a traceback.
    """
