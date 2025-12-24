from __future__ import annotations

import typing
from typing import Annotated, Mapping

import typer
from rich.console import Console
from rich.table import Table, box
from packaging.specifiers import SpecifierSet, InvalidSpecifier

from uv_outdated.utils import (
    Package,
    get_uv_outdated,
    get_locked_packages_and_deps,
    get_direct_dependencies,
    get_package_specifiers,
    find_direct_ancestors,
    group_packages_by_dependency_groups,
    OutdatedPkg,
    SpecifierStr,
    Name,
)

if typing.TYPE_CHECKING:
    from collections.abc import Generator

app = typer.Typer()


@app.command()
def cli(
    show_headers: Annotated[
        bool,
        typer.Option("--show-headers/--no-headers", help="Show table headers."),
    ] = False,
    show_why: Annotated[
        bool,
        typer.Option(
            "--why/--no-why",
            help="Show dependent information (Dependents column).",
        ),
    ] = True,
    direct_only: Annotated[
        bool,
        typer.Option("--direct/--all", help="Show only direct dependencies (default: all)."),
    ] = False,
    transitive_only: Annotated[
        bool,
        typer.Option(
            "--transitive/--all",
            help="Show only transitive dependencies (default: all).",
        ),
    ] = False,
    group_by_ancestor: Annotated[
        bool,
        typer.Option(
            "--group-by-ancestor",
            help="Group outdated dependencies by their direct dependency ancestor.",
        ),
    ] = False,
) -> None:
    """
    Show outdated packages in the current project, or run tests.
    """
    console = Console()

    try:
        outdated = get_uv_outdated()
        packages = get_locked_packages_and_deps()
        direct = get_direct_dependencies()
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise

    # Extract helper data from packages
    specifiers = get_package_specifiers(packages)

    # Collect all outdated packages that match our filters
    outdated_packages = collect_outdated_packages(
        packages=packages,
        outdated=outdated,
        direct=direct,
        direct_only=direct_only,
        transitive_only=transitive_only,
    )

    # Handle case where no outdated packages are found
    if not outdated_packages:
        if not outdated:
            console.print("[yellow]No outdated packages found.[/yellow]")
            console.print(
                "[dim]Note: Could not check for outdated packages (no virtual environment).[/dim]"
            )
            console.print(f"Total packages in uv.lock: {len(packages)}")
        else:
            console.print("[yellow]No outdated packages found.[/yellow]")
            console.print(f"Total packages: {len(packages)}")
            console.print(f"Checked {len(outdated)} packages for updates")
        return

    # Group packages by dependency groups
    dependency_groups = group_packages_by_dependency_groups(outdated_packages)

    # Create table with sections
    table = Table(
        show_header=show_headers,
        show_lines=False,
        box=box.SIMPLE,
        expand=True,
        pad_edge=False,
    )
    table.add_column("Package", style="cyan", min_width=10, ratio=2, no_wrap=True)
    table.add_column("Current", style="bold", min_width=8, no_wrap=True)
    table.add_column("Latest", min_width=8, no_wrap=True)
    if show_why:
        table.add_column("Constraint", min_width=10, ratio=1, no_wrap=True)
        table.add_column("Dependents", min_width=10, max_width=30, no_wrap=True)
    table.add_column("Description", min_width=20, ratio=3, no_wrap=True)

    # Add sections in order: main dependencies first, then extras alphabetically
    section_order = ["", *sorted(group for group in dependency_groups if group != "")]

    for group_name in section_order:
        if group_name not in dependency_groups:
            continue

        group_packages = dependency_groups[group_name]
        if not group_packages:
            continue

        # Add section header with title
        section_title = rf"\[group:{group_name}]" if group_name else ""
        table.add_section()
        # Add a row with the section title
        empty_cols = [""] * (5 if show_why else 3)
        if section_title:
            table.add_row(f"[bold blue]{section_title}[/bold blue]", *empty_cols)

        # Generate and add rows for this section
        if group_by_ancestor:
            for row in generate_grouped_rows(group_packages, packages, specifiers, show_why):
                table.add_row(*row)
        else:
            for row in generate_normal_rows(group_packages, specifiers, show_why):
                table.add_row(*row)

    console.print(table)


def generate_normal_rows(
    outdated_packages: list[tuple[Name, Package, OutdatedPkg, bool]],
    specifiers: dict[Name, SpecifierStr],
    show_why: bool,
) -> Generator[list[str], None, None]:
    """Generate table rows for outdated packages in a flat list."""
    for name, pkg, outdated_pkg, is_direct in outdated_packages:
        yield _create_package_row(
            name,
            pkg,
            outdated_pkg,
            is_direct,
            specifiers,
            show_why,
            "[cyan]{name}[/cyan]",
        )


def generate_grouped_rows(
    outdated_packages: list[tuple[Name, Package, OutdatedPkg, bool]],
    packages: dict[Name, Package],
    specifiers: dict[Name, SpecifierStr],
    show_why: bool,
) -> Generator[list[str], None, None]:
    """Generate table rows for outdated packages grouped by direct ancestor."""
    # Group packages by their direct dependency ancestors
    groups: dict[str, list[tuple[Name, Package, OutdatedPkg, bool]]] = {}
    direct_dependencies_with_groups: set[str] = set()

    for name, pkg, outdated_pkg, is_direct in outdated_packages:
        if is_direct:
            # All direct dependencies get their own group
            groups.setdefault(name, []).append((name, pkg, outdated_pkg, is_direct))
        else:
            # Find ancestors for transitive dependencies
            ancestors = find_direct_ancestors(name, packages)
            if ancestors:
                for ancestor in sorted(ancestors):
                    groups.setdefault(ancestor, []).append((name, pkg, outdated_pkg, is_direct))
                    # Track that this direct dependency has transitive deps
                    direct_dependencies_with_groups.add(ancestor)
            else:
                # Orphaned packages (shouldn't happen but just in case)
                groups.setdefault("_unknown", []).append((name, pkg, outdated_pkg, is_direct))

    # Yield rows
    for group_key in sorted(groups.keys()):
        group_packages = groups[group_key]

        if group_key == "_unknown":
            # Yield header for unknown ancestors
            empty_cols = [""] * (5 if show_why else 3)
            yield ["[dim]Unknown ancestor[/dim]", *empty_cols]

            # Yield the orphaned packages
            for name, pkg, outdated_pkg, is_direct in group_packages:
                yield _create_package_row(
                    name,
                    pkg,
                    outdated_pkg,
                    is_direct,
                    specifiers,
                    show_why,
                    "  [cyan]{name}[/cyan]",
                )
        elif group_key not in direct_dependencies_with_groups:
            # Direct dependency with no transitive deps - show directly without header
            name, pkg, outdated_pkg, is_direct = group_packages[0]  # Should only be one
            yield _create_package_row(
                name,
                pkg,
                outdated_pkg,
                is_direct,
                specifiers,
                show_why,
                "[cyan]{name}[/cyan]",
            )
        else:
            # Direct dependency with transitive deps - show header with direct dep info,
            # then transitive deps
            direct_dep = next(
                (item for item in group_packages if item[3]), None
            )  # Find the direct dependency
            transitive_deps = [item for item in group_packages if not item[3]]

            if direct_dep:
                name, pkg, outdated_pkg, is_direct = direct_dep
                yield _create_package_row(
                    name,
                    pkg,
                    outdated_pkg,
                    is_direct,
                    specifiers,
                    show_why,
                    "[cyan]{name}[/cyan]",
                )
            else:
                # No direct dep found, just show group header
                empty_cols = [""] * (5 if show_why else 3)
                yield [f"[cyan]{group_key}[/cyan]", *empty_cols]

            # Yield transitive dependencies
            for name, pkg, outdated_pkg, is_direct in transitive_deps:
                yield _create_package_row(
                    name,
                    pkg,
                    outdated_pkg,
                    is_direct,
                    specifiers,
                    show_why,
                    "  [italic cyan]{name}[/italic cyan]",
                )


def collect_outdated_packages(
    *,
    packages: dict[Name, Package],
    outdated: dict[Name, OutdatedPkg],
    direct: Mapping[str, object],
    direct_only: bool,
    transitive_only: bool,
) -> list[tuple[Name, Package, OutdatedPkg, bool]]:
    """Collect outdated packages with filtering and hide entries where latest == current."""
    collected: list[tuple[Name, Package, OutdatedPkg, bool]] = []
    for name, pkg in packages.items():
        # Some local/workspace/editable packages (including the root project) may not
        # have a statically known version when using dynamic versioning.
        # See PEP 621 (Project metadata in pyproject.toml) which allows
        # `dynamic = ["version"]`.
        if not pkg.version:
            continue

        if name not in outdated:
            continue
        outdated_pkg = outdated[name]
        # Skip if latest equals current; nothing to show
        if outdated_pkg.latest_version == pkg.version:
            continue
        is_direct = name in direct
        if direct_only and not is_direct:
            continue
        if transitive_only and is_direct:
            continue
        collected.append((name, pkg, outdated_pkg, is_direct))
    return collected


def _create_package_row(
    name: Name,
    pkg: Package,
    outdated_pkg: OutdatedPkg,
    is_direct: bool,
    specifiers: dict[Name, SpecifierStr],
    show_why: bool,
    name_format: str,
) -> list[str]:
    """Create a package row with consistent formatting."""
    latest = outdated_pkg.latest_version
    constraint = ""
    if latest != pkg.version:
        # Consider a package as "constrained" (yellow) only when the current version
        # is allowed by the specifier but the latest version is NOT allowed.
        spec = specifiers.get(name)
        if spec:
            try:
                spec_set = SpecifierSet(spec)
                current_allowed = spec_set.contains(pkg.version)
                latest_allowed = spec_set.contains(latest)
                if current_allowed and not latest_allowed:
                    latest_colored = f"[yellow]{latest}[/yellow]"
                    constraint = spec
                else:
                    latest_colored = f"[red]{latest}[/red]"
            except InvalidSpecifier:
                latest_colored = f"[red]{latest}[/red]"
        else:
            latest_colored = f"[red]{latest}[/red]"
    else:
        # If latest equals current, this entry should normally not appear (uv wouldn't
        # report it as outdated). Keep a neutral/yellow display to avoid implying an
        # upgrade path.
        latest_colored = f"[yellow]{latest}[/yellow]"

    parents = {dep.package.name for dep in pkg.dependents}
    dependents_str = "" if is_direct or not parents else ", ".join(sorted(parents))
    desc = pkg.summary
    name_cyan = name_format.format(name=name)
    version_bold = f"[bold]{pkg.version}[/bold]"

    row_items = [name_cyan, version_bold, latest_colored]
    if show_why:
        row_items.extend([constraint, dependents_str])
    row_items.append(desc)
    return row_items


if __name__ == "__main__":
    app()
