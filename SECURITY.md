# Security

## Report a vulnerability

Use GitHub's private vulnerability reporting:

https://github.com/AshleyxAdamson/Desk-crit/security/advisories/new

Please don't open a public issue for a security problem. A public issue tells everyone before there's a fix.

A useful report says what you did, what you expected and what happened. Include the plugin version (`version` in `.claude-plugin/plugin.json`), your macOS and Claude Code versions, and the smallest steps that show the problem. Don't attach a recording you don't want to share. A sample that reproduces it works better.

Reports are read and answered on the advisory. A fix ships as a new version of the plugin, and the advisory says which version.

## Which versions

The latest release. Older ones don't get fixes.

## What's in scope

Anything in this repo that runs on your Mac:

- The local MCP server, and the files it reads and writes.
- The model download: the pinned host, the size and sha256 checks, and where files land.
- The session-start hook, the environment build script and the server launcher script.
- The locked Python packages in `server/uv.lock`. A report about a package that the lock pins is welcome. The fix is a new lock.

## Not in scope

- Claude itself, Claude Code, or Anthropic's services. Report those to Anthropic.
- The model and packages the plugin downloads, when the problem is in the upstream project. Report those upstream, and open an issue here so the lock can move.
- A problem that needs someone who already has full control of your Mac and your account.

## Where to ask for help

Ordinary bugs and questions go to [GitHub Issues](https://github.com/AshleyxAdamson/Desk-crit/issues). What the plugin collects and sends is on the [privacy page](https://github.com/AshleyxAdamson/Desk-crit/blob/main/PRIVACY.md).
