# Releasing

Three things are released from this repository, each on its own tag namespace
and its own semantic version. The tag is the authority: every workflow refuses
a tag that disagrees with the version the code carries.

| What | Version lives in | Tag | How | Publishes |
|---|---|---|---|---|
| Engine and CLI | `engraphy/__init__.py` (`pyproject.toml` reads it) | `vX.Y.Z` | `git tag vX.Y.Z && git push origin vX.Y.Z` | GitHub release with sdist and wheel; `ghcr.io/engraphylabs/engraphy` and `ghcr.io/engraphylabs/engraphy-admin` as `X.Y.Z`, `X.Y`, `latest` and the `-micro` variant; the MCP registry record `io.github.engraphylabs/engraphy`. `.github/workflows/release.yml` |
| VS Code extension | `ide/vscode-engraphy/package.json` | `ide-vX.Y.Z` | Actions > Release extension > Run workflow, from `main` | the VS Code Marketplace, publisher `engraphy`. [`ide/vscode-engraphy/RELEASING.md`](ide/vscode-engraphy/RELEASING.md) |
| Desktop app | `desktop/package.json` | `desktop-vX.Y.Z` | Actions > Release the desktop app > Run workflow, from `main` | the Windows installer, attached to the engine's **latest** GitHub release (one release stream; `desktop-publish.yml` says why) |

Before an engine tag: set `__version__`, turn `## Unreleased` in
`CHANGELOG.md` into `## X.Y.Z`, and set `"version"` in `server.json` to match.
After any of the three: regenerate `version.json` in the marketing site
repository (`build-version.py`), or the shipped clients keep being told the
previous version is the latest. After an engine release the new latest
release carries no desktop installer until `desktop-publish` is re-run with
the current `desktop-v*` tag; regenerate the manifest after that, not before.

Two namespace rules, since the move to the `EngraphyLabs` organisation:

- The old paths do not redirect. `ghcr.io/devon-clarkk/*` holds `0.3.0` and
  earlier and receives nothing further; the registry record
  `io.github.devon-clarkk/engraphy` stays at `0.3.0`.
- GitHub reports the owner as `EngraphyLabs`; container and registry names are
  lowercase. The workflows lowercase for themselves; anything typed by hand
  must be `ghcr.io/engraphylabs/...` and `io.github.engraphylabs/...`.

The estate-wide document, covering the hosted services and the marketing site
as well as this repository, with verification and rollback for each, is
`RELEASING.md` in the (private) `engraphy-control-plane` repository.
