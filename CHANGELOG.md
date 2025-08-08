# Changelog

## [1.0.1] - 2025-08-15

### Added

- Better PyPI metadata
- MIT license

## [1.0.0] - 2025-08-08

### Added

- Initial release of `uv-outdated`.
- Scans `uv.lock` and `pyproject.toml` to find outdated dependencies.
- Displays outdated packages grouped by dependency groups (e.g., `main`, `dev`).
- Shows why a package cannot be upgraded by displaying version constraints from `pyproject.toml`.
- Displays which other packages depend on an outdated package.
- Provides a hierarchical view to see transitive dependencies grouped under their top-level ancestors (`--group-by-ancestor`).
- CLI options to filter for direct or transitive dependencies (`--direct`, `--transitive`).
- CLI options to customize output format (`--show-headers`, `--why`).
- Works without needing an activated virtual environment by reading lockfiles and config directly.
