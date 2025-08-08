# uv-outdated

Show outdated packages in your [uv](https://github.com/astral-sh/uv) projects with
better formatting and dependency context.

While `uv pip list --outdated` shows which packages have newer versions available,
`uv-outdated` adds useful context like dependency relationships, constraint information,
and organizes output by dependency groups.

## Usage

Run in any directory with a `uv.lock` file:

```bash
uvx uv-outdated [OPTIONS]
```

### Options

- `--show-headers/--no-headers`: Include table headers (default: no headers)
- `--why/--no-why`: Show constraint and dependents columns (default: show)
- `--direct/--all`: Only show direct dependencies (default: all)
- `--transitive/--all`: Only show transitive dependencies (default: all)
- `--group-by-ancestor`: Group transitive dependencies under their direct ancestors

### Example Output

```
Package         Current  Latest   Constraint  Dependents      Description
requests        2.28.0   2.31.0   >=2.25.0    mypy, pytest   HTTP library for Python
urllib3         1.26.0   2.0.7    <2.0.0      requests        HTTP library with thread-safe connection pooling

[group:dev]
pytest          7.0.0    8.0.0    ^7.0.0      pytest-cov     Simple and scalable testing
pytest-cov      4.0.0    5.0.0                               Coverage plugin for pytest
```

## What it shows you

**Dependency groups**: Packages are organized by their groups from `pyproject.toml` (main dependencies, optional dependencies, development groups).

**Why packages can't upgrade**: 
- Yellow versions indicate the package is constrained by version specifiers
- Red versions show packages that can be freely upgraded
- The Constraint column shows the limiting specifier

**Dependency relationships**: The Dependents column shows which other packages require each outdated package.

**Hierarchical view**: With `--group-by-ancestor`, transitive dependencies are grouped under their direct dependency parents.

## How it works

The tool combines information from:
- `uv.lock` for exact versions and dependency relationships
- `pyproject.toml` for dependency groups and constraints  
- `uv pip list --outdated` for available updates
- Site-packages metadata for descriptions (when available)

It works even when your virtual environment isn't activated or available.

## Requirements

- Python 3.12+
- A project with `uv.lock`
- Optional: `pyproject.toml` for dependency group information

## Why this exists

`uv pip list --outdated` gives you the basics, but when you're managing a project with many dependencies across different groups, it helps to see:
- Which outdated packages are your direct dependencies vs. transitive
- Why certain packages can't be upgraded (version constraints)
- How dependencies relate to each other
- Packages organized by their purpose (dev, test, optional features, etc.)

This is particularly useful for larger projects where understanding the dependency tree matters for upgrade decisions.
