from __future__ import annotations

import importlib.metadata
import json
import os
import subprocess
import tomllib

from dataclasses import dataclass, field
from itertools import chain
from pathlib import Path
from typing import Any

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet, InvalidSpecifier
from packaging.utils import canonicalize_name
from packaging.version import Version
from pydantic import BaseModel

type Name = str
type SpecifierStr = str
type VersionStr = str


class SitePackage(BaseModel):
    model_config = {"arbitrary_types_allowed": True}

    name: str
    version: str
    summary: str = ""
    requires_dist: list[str] = []
    provides_extra: list[str] = []
    distribution: Any  # Changed from PathDistribution to Any for testing flexibility


@dataclass
class DependencyGroup:
    name: str  # The main requirements will be named ""
    dependencies: list[Requirement]


@dataclass
class Package:
    name: Name
    version: VersionStr
    dynamic_version: bool = False
    summary: str = ""
    requires: dict[str, DependencyGroup] = field(default_factory=dict)
    dependents: list[Dependent] = field(default_factory=list)


@dataclass
class Dependent:
    through: str  # The name of the dependency group that creates this dependency.
    package: Package


def run_uv(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a uv subprocess with consistent environment and capture_output."""
    return subprocess.run(
        ["uv", *args],
        capture_output=True,
        text=True,
        env={**os.environ, "VIRTUAL_ENV": ""},
        check=False,
    )


class OutdatedPkg(BaseModel):
    name: Name
    version: VersionStr
    latest_version: VersionStr


def get_uv_outdated() -> dict[Name, OutdatedPkg]:
    """Get outdated packages from uv pip list command, with fallback to empty dict."""
    result = run_uv(["pip", "list", "--outdated", "--format=json"])

    if result.returncode != 0:
        # If uv pip list fails (e.g., no venv), return empty dict to show no outdated
        # packages. This allows the script to still show package info from uv.lock
        return {}

    try:
        packages_data = json.loads(result.stdout)
    except json.JSONDecodeError:
        # If JSON parsing fails, return empty dict rather than crashing
        return {}

    return {
        canonicalize_name(pkg["name"]): OutdatedPkg.model_validate(pkg) for pkg in packages_data
    }


def get_site_packages_from_uv() -> Path:
    """Get the site-packages path from the current uv venv."""
    result = run_uv(["python", "find"])
    if result.returncode != 0:
        raise RuntimeError("Could not find Python executable with uv")
    python_path = Path(result.stdout.strip())
    venv_dir = python_path.parent.parent
    pyvenv_cfg = venv_dir / "pyvenv.cfg"
    py_version = None
    with pyvenv_cfg.open("r") as f:
        for line in f:
            if line.startswith("version"):
                py_version = line.strip().split("=")[1].strip()
                break
    if not py_version:
        raise RuntimeError("Could not find Python version in pyvenv.cfg")
    py_version = ".".join(py_version.split(".")[:2])
    site_packages = venv_dir / "lib" / f"python{py_version}" / "site-packages"
    if site_packages.is_dir():
        return site_packages
    raise RuntimeError(f"Could not find site-packages at {site_packages}")


def get_all_metadata_from_site_packages() -> dict[Name, SitePackage]:
    """
    Parse all .dist-info/METADATA files in site-packages for metadata.
    """
    site_packages = get_site_packages_from_uv()
    package_meta_by_package_name: dict[str, SitePackage] = {}
    for dist_info_dir in site_packages.glob("*.dist-info"):
        dist = importlib.metadata.PathDistribution(dist_info_dir)
        metadata = dist.metadata
        name = canonicalize_name(metadata["Name"])
        package_meta_by_package_name[name] = SitePackage(
            name=name,
            version=metadata["Version"],
            summary=metadata.get("Summary", ""),
            requires_dist=metadata.get_all("Requires-Dist") or [],
            provides_extra=metadata.get_all("Provides-Extra") or [],
            distribution=dist,
        )
    return package_meta_by_package_name


def _parse_dependency_name(dep: dict[str, Any] | str) -> str:
    """Extract dependency name from various formats."""
    if isinstance(dep, dict):
        return canonicalize_name(dep.get("name", ""))
    else:
        return canonicalize_name(dep.split()[0].split("=")[0])


def _create_requirement_from_dep(
    dep_name: str, parent_name: str, site_metadata_by_name: dict[str, SitePackage]
) -> Requirement:
    """Create a Requirement object from dependency name and site metadata."""
    req_str = dep_name
    if parent_name in site_metadata_by_name:
        # Find the actual requirement string from site-packages
        for req_str_full in site_metadata_by_name[parent_name].requires_dist:
            try:
                req = Requirement(req_str_full)
                if canonicalize_name(req.name) == dep_name:
                    req_str = req_str_full
                    break
            except (ValueError, TypeError):
                continue

    try:
        return Requirement(req_str)
    except (ValueError, TypeError):
        # Fallback to basic requirement if parsing fails
        return Requirement(dep_name)


def _populate_package_dependencies(
    packages: dict[Name, Package],
    uv_lock: dict[str, Any],
    site_metadata_by_name: dict[str, SitePackage],
) -> None:
    """Populate dependencies and optional-dependencies for each package."""
    for pkg_data in uv_lock.get("package", []):
        name = canonicalize_name(pkg_data["name"])
        package = packages[name]

        # Process main dependencies
        main_deps = []
        for dep in pkg_data.get("dependencies", []):
            dep_name = _parse_dependency_name(dep)
            if dep_name:
                main_deps.append(
                    _create_requirement_from_dep(dep_name, name, site_metadata_by_name)
                )

        if main_deps:
            package.requires[""] = DependencyGroup(name="", dependencies=main_deps)

        # Process optional dependencies (extras)
        for extra_name, extra_deps in pkg_data.get("optional-dependencies", {}).items():
            extra_requirements = []
            for dep in extra_deps:
                dep_name = _parse_dependency_name(dep)
                if dep_name:
                    extra_requirements.append(
                        _create_requirement_from_dep(dep_name, name, site_metadata_by_name)
                    )

            if extra_requirements:
                package.requires[extra_name] = DependencyGroup(
                    name=extra_name, dependencies=extra_requirements
                )


def _populate_package_dependents(packages: dict[Name, Package], uv_lock: dict[str, Any]) -> None:
    """Populate dependents for each package."""
    for pkg_data in uv_lock.get("package", []):
        parent_name = canonicalize_name(pkg_data["name"])
        parent_package = packages[parent_name]

        # Process main dependencies
        for dep in pkg_data.get("dependencies", []):
            dep_name = _parse_dependency_name(dep)
            if dep_name and dep_name in packages:
                dependent = Dependent(through="", package=parent_package)
                packages[dep_name].dependents.append(dependent)

        # Process optional dependencies
        for extra_name, extra_deps in pkg_data.get("optional-dependencies", {}).items():
            for dep in extra_deps:
                dep_name = _parse_dependency_name(dep)
                if dep_name and dep_name in packages:
                    dependent = Dependent(through=extra_name, package=parent_package)
                    packages[dep_name].dependents.append(dependent)


def get_locked_packages_and_deps() -> dict[Name, Package]:
    """
    Parse package information from uv.lock and site-packages to create Package objects.

    This function combines data from multiple sources to build a comprehensive view
    of all packages in the project:

    1. uv.lock provides the exact versions of all packages and their dependency
       relationships, serving as the source of truth for what's actually installed.

    2. site-packages provides additional metadata like package summaries and detailed
       requirement specifications with version constraints.

    3. The function gracefully handles cases where site-packages is not available
       (e.g., when the project isn't installed in the current environment).

    Returns:
        A dictionary mapping canonicalized package names to Package objects.
        Each Package contains:
        - name: The canonicalized package name
        - version: The exact version from uv.lock
        - summary: Package description from site-packages (if available)
        - requires: Dict of dependency groups (main deps use empty string key)
        - dependents: List of packages that depend on this package

    The dependency tracking includes both main dependencies and optional dependencies
    (extras), with proper tracking of which dependency group each relationship
    comes from.
    """
    try:
        with Path("uv.lock").open("rb") as f:
            uv_lock = tomllib.load(f)
    except FileNotFoundError:
        raise RuntimeError("uv.lock not found") from None
    except tomllib.TOMLDecodeError as e:
        raise RuntimeError(f"Failed to parse uv.lock: {e}") from e

    packages: dict[Name, Package] = {}

    # Step 1: Get site-packages metadata if available
    # (gracefully handle if not available)
    try:
        site_metadata_by_name = get_all_metadata_from_site_packages()
    except RuntimeError:
        # Site-packages may not be available if project isn't installed
        site_metadata_by_name = {}

    # Determine whether the project version is declared as dynamic.
    # This is allowed by PEP 621 via `project.dynamic = ["version"]`.
    # We don't currently change behavior based on this, but keeping the flag
    # makes it easy to add future logging/debug output.
    project_version_is_dynamic = False
    try:
        with Path("pyproject.toml").open("rb") as f:
            pyproject = tomllib.load(f)
        project_dynamic = pyproject.get("project", {}).get("dynamic", [])
        project_version_is_dynamic = "version" in project_dynamic
    except (FileNotFoundError, tomllib.TOMLDecodeError):
        project_version_is_dynamic = False

    # Step 2: Process each package in uv.lock
    for pkg_data in uv_lock.get("package", []):
        name = canonicalize_name(pkg_data["name"])

        # Get summary from site-packages if available, otherwise empty
        summary = ""
        if name in site_metadata_by_name:
            summary = site_metadata_by_name[name].summary

        # Determine version.
        #
        # For most resolved packages, uv.lock contains an exact "version".
        # However, local/workspace/editable packages (including the root project
        # when using dynamic versioning; see PEP 621 which allows
        # `dynamic = ["version"]`) may omit "version". In that case we fall
        # back to site-packages metadata if available, otherwise we treat the
        # version as unknown (empty string).
        version = pkg_data.get("version")
        if version is None:
            site_meta = site_metadata_by_name.get(name)
            version = site_meta.version if site_meta else ""

        # Create Package object
        package = Package(
            name=name,
            version=version,
            # If the project uses a dynamic version (PEP 621), the version may not
            # be available. This is a no-op addition for potential future logging.
            dynamic_version=project_version_is_dynamic,
            summary=summary,
        )
        packages[name] = package

    # Step 3: Populate dependencies and optional-dependencies for each package
    _populate_package_dependencies(packages, uv_lock, site_metadata_by_name)

    # Step 4: Populate dependents for each package
    _populate_package_dependents(packages, uv_lock)

    return packages


def get_direct_dependencies() -> dict[str, Requirement]:
    """Parse direct dependencies from pyproject.toml."""
    try:
        with Path("pyproject.toml").open("rb") as f:
            pyproject = tomllib.load(f)
    except FileNotFoundError:
        raise RuntimeError("pyproject.toml not found") from None
    except tomllib.TOMLDecodeError as e:
        raise RuntimeError(f"Failed to parse pyproject.toml: {e}") from e

    dependencies = []

    if "project" in pyproject:
        project = pyproject["project"]
        dependencies.extend(project.get("dependencies", []))
        dependencies.extend(chain.from_iterable(project.get("optional-dependencies", {}).values()))

    if "dependency-groups" in pyproject:
        dependencies.extend(chain.from_iterable(pyproject["dependency-groups"].values()))

    direct_dependencies_by_name: dict[str, Requirement] = {}
    for dep in dependencies:
        req = Requirement(dep)
        direct_dependencies_by_name[str(canonicalize_name(req.name))] = req

    return direct_dependencies_by_name


def get_package_specifiers(packages: dict[Name, Package]) -> dict[Name, SpecifierStr]:
    """
    Extract specifiers from packages for constraint checking.

    Only includes constraints from actually installed extras, not from
    non-installed extras that would incorrectly restrict upgrades.
    """
    specifiers: dict[Name, SpecifierStr] = {}

    # Get information about which extras are actually installed
    installed_extras: dict[Name, set[str]] = {}

    try:
        with Path("uv.lock").open("rb") as f:
            uv_lock = tomllib.load(f)

        # First pass: collect which extras are actually installed
        for pkg_data in uv_lock.get("package", []):
            pkg_name = str(canonicalize_name(pkg_data["name"]))
            extras = set(pkg_data.get("extra", []))
            installed_extras[pkg_name] = extras

        # Get specifiers from project package metadata (if available)
        for pkg in uv_lock.get("package", []):
            meta = pkg.get("metadata", {})
            if "requires-dist" not in meta:
                continue

            for req in meta["requires-dist"]:
                if isinstance(req, dict):
                    dep_name = str(canonicalize_name(req.get("name", "")))
                    spec = req.get("specifier")
                    if dep_name and spec:
                        specifiers[dep_name] = spec
    except (FileNotFoundError, KeyError, ValueError):
        # Handle cases where uv.lock is missing or malformed
        pass

    # Also get specifiers from site-packages, but only from installed extras
    try:
        site_metadata = get_all_metadata_from_site_packages()
        for pkg_name, meta in site_metadata.items():
            # Get the set of actually installed extras for this package
            pkg_installed_extras = installed_extras.get(pkg_name, set())

            for req_str in meta.requires_dist:
                try:
                    req = Requirement(req_str)
                    if not req.specifier:
                        continue

                    # Check if this requirement has extra conditions
                    if req.marker:
                        # Parse the marker to see if it's conditional on an extra
                        marker_str = str(req.marker)
                        if "extra ==" in marker_str:
                            # Extract the extra name from the marker
                            # e.g., "extra == 'test'" -> "test"
                            extra_parts = marker_str.split("extra ==")
                            if len(extra_parts) > 1:
                                extra_name = extra_parts[1].strip().strip("'\"").strip()
                                # Only include constraint if this extra is installed
                                if extra_name not in pkg_installed_extras:
                                    continue

                    # Include this constraint
                    dep_name = str(canonicalize_name(req.name))
                    specifiers[dep_name] = str(req.specifier)
                except (ValueError, TypeError):
                    continue
    except RuntimeError:
        pass

    return specifiers


def is_locked_by_specifier(
    specifiers: dict[Name, SpecifierStr], name: Name, latest_version: VersionStr
) -> bool:
    """Returns True if the package is locked to a lower version by its specifier."""
    spec = specifiers.get(name)
    if not spec:
        return False
    try:
        spec_set = SpecifierSet(spec)
        latest = Version(latest_version)
        return not spec_set.contains(latest)
    except InvalidSpecifier:
        return False


def find_direct_ancestors(package_name: Name, packages: dict[Name, Package]) -> set[Name]:
    """
    Find all direct dependency ancestors for a given package.

    This function performs a breadth-first search to find all direct dependencies
    that eventually lead to the given package through the dependency chain.

    Args:
        package_name: The package to find ancestors for
        packages: All packages with their dependency information

    Returns:
        Set of direct dependency names that are ancestors of the given package
    """
    # Get direct dependencies
    try:
        direct_deps = get_direct_dependencies()
    except RuntimeError:
        return set()

    ancestors = set()
    visited = set()
    queue = [package_name]

    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)

        if current not in packages:
            continue

        # Check all packages that depend on the current package
        for dependent in packages[current].dependents:
            dependent_name = dependent.package.name

            if dependent_name in direct_deps:
                # Found a direct dependency ancestor
                ancestors.add(dependent_name)
            else:
                # Continue searching up the dependency tree
                queue.append(dependent_name)

    return ancestors


def group_packages_by_dependency_groups(
    outdated_packages: list[tuple[Name, Package, OutdatedPkg, bool]],
) -> dict[str, list[tuple[Name, Package, OutdatedPkg, bool]]]:
    """
    Group outdated packages by their dependency groups.

    Returns a dict where keys are dependency group names ("" for main, extra names
    for extras) and values are lists of outdated packages that belong to those groups.
    """
    # First, get the dependency groups from pyproject.toml
    try:
        with Path("pyproject.toml").open("rb") as f:
            pyproject = tomllib.load(f)
    except (FileNotFoundError, tomllib.TOMLDecodeError):
        # Fallback to simple grouping if pyproject.toml is not available
        return {"": outdated_packages}

    # Parse dependency groups
    dependency_groups_from_toml: dict[str, set[Name]] = {}

    # PEP 621 style dependencies (main dependencies)
    if "project" in pyproject:
        project = pyproject["project"]
        main_deps: set[Name] = set()
        for dep_str in project.get("dependencies", []):
            req = Requirement(dep_str)
            main_deps.add(canonicalize_name(req.name))
        if main_deps:
            dependency_groups_from_toml[""] = main_deps

        # PEP 621 optional dependencies (extras)
        for extra_name, extra_deps in project.get("optional-dependencies", {}).items():
            extra_set: set[Name] = set()
            for dep_str in extra_deps:
                req = Requirement(dep_str)
                extra_set.add(canonicalize_name(req.name))
            if extra_set:
                dependency_groups_from_toml[extra_name] = extra_set

    # PEP 735/uv-style: top-level [dependency-groups]
    if "dependency-groups" in pyproject:
        for group_name, group_deps in pyproject["dependency-groups"].items():
            group_set: set[Name] = set()
            for dep_str in group_deps:
                req = Requirement(dep_str)
                group_set.add(canonicalize_name(req.name))
            if group_set:
                dependency_groups_from_toml[group_name] = group_set

    # Now group the outdated packages
    groups: dict[str, list[tuple[Name, Package, OutdatedPkg, bool]]] = {}

    for name, pkg, outdated_pkg, is_direct in outdated_packages:
        if is_direct:
            # For direct dependencies, find which groups they belong to
            found_groups = set()
            for group_name, group_deps in dependency_groups_from_toml.items():
                if name in group_deps:
                    found_groups.add(group_name)

            # If not found in any specific group, put in main
            if not found_groups:
                found_groups.add("")

            # Add to all groups it belongs to
            for group_name in found_groups:
                groups.setdefault(group_name, []).append((name, pkg, outdated_pkg, is_direct))
        else:
            # For transitive dependencies, determine which groups they come from
            # by examining their dependents
            found_groups = set()

            for dependent in pkg.dependents:
                # Find which group the dependent belongs to
                dependent_name = dependent.package.name
                for group_name, group_deps in dependency_groups_from_toml.items():
                    if dependent_name in group_deps:
                        found_groups.add(group_name)

            # If no specific group found, put in main
            if not found_groups:
                found_groups.add("")

            # Add to all groups it comes from
            for group_name in found_groups:
                groups.setdefault(group_name, []).append((name, pkg, outdated_pkg, is_direct))

    return groups
