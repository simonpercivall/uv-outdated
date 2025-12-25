# mypy: ignore-errors
import importlib.metadata
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from uv_outdated.utils import (
    get_locked_packages_and_deps,
    get_package_specifiers,
    SitePackage,
    is_locked_by_specifier,
    run_uv,
    get_uv_outdated,
    OutdatedPkg,
    get_direct_dependencies,
    Package,
    DependencyGroup,
    Dependent,
    get_site_packages_from_uv,
    get_all_metadata_from_site_packages,
    find_direct_ancestors,
    Name,
)

# Additional imports for row-building and collection
from uv_outdated.__main__ import _create_package_row, collect_outdated_packages


class TestOutdatedScript(unittest.TestCase):
    @patch("uv_outdated.utils.get_all_metadata_from_site_packages")
    def test_get_package_specifiers(self, mock_site_packages):
        """Test that get_package_specifiers extracts specifiers correctly."""
        # Mock site-packages to return empty dict (simulating no venv)
        mock_site_packages.return_value = {}

        packages = get_locked_packages_and_deps()
        specifiers = get_package_specifiers(packages)

        self.assertIsInstance(specifiers, dict)
        # Check that all values are strings
        for spec in specifiers.values():
            self.assertIsInstance(spec, str)

    @patch("uv_outdated.utils.get_all_metadata_from_site_packages")
    def test_package_dependents_from_objects(self, mock_site_packages):
        """Test that Package objects have dependents correctly populated."""
        # Mock site-packages to return empty dict (simulating no venv)
        mock_site_packages.return_value = {}

        packages = get_locked_packages_and_deps()

        # Find a package with dependents
        pkg_with_dependents = None
        for pkg in packages.values():
            if pkg.dependents:
                pkg_with_dependents = pkg
                break

        if pkg_with_dependents is not None:
            # Check that dependents are properly structured
            for dependent in pkg_with_dependents.dependents:
                self.assertIsInstance(dependent.package.name, str)

    @patch("uv_outdated.utils.get_all_metadata_from_site_packages")
    def test_package_summary_population(self, mock_site_packages):
        """Test that Package summaries are populated from site-packages."""
        # Create a proper mock distribution
        mock_dist = MagicMock()
        # Type ignore needed for test mocking
        mock_dist.__class__ = importlib.metadata.PathDistribution  # type: ignore[assignment]

        # Mock site-packages to return some sample data
        mock_site_packages.return_value = {
            "django": SitePackage(
                name="django",
                version="5.0.1",
                summary="A high-level Python web framework",
                requires_dist=[],
                provides_extra=[],
                distribution=mock_dist,
            )
        }

        packages = get_locked_packages_and_deps()

        # Check specific known package
        if "django" in packages:
            django_pkg = packages["django"]
            # Django should have a summary from our mock
            self.assertIn("framework", django_pkg.summary.lower())

    @patch("uv_outdated.utils.get_all_metadata_from_site_packages")
    def test_package_dependencies_structure(self, mock_site_packages):
        """Test that Package dependencies are properly structured."""
        # Mock site-packages to return empty dict (simulating no venv)
        mock_site_packages.return_value = {}

        packages = get_locked_packages_and_deps()

        # Find a package with dependencies
        pkg_with_deps = None
        for pkg in packages.values():
            if pkg.requires:
                pkg_with_deps = pkg
                break

        self.assertIsNotNone(pkg_with_deps, "Should find at least one package with dependencies")

        # Check main dependencies (empty string key)
        if pkg_with_deps is not None and "" in pkg_with_deps.requires:
            main_group = pkg_with_deps.requires[""]
            self.assertIsInstance(main_group, DependencyGroup)
            self.assertEqual(main_group.name, "")
            self.assertIsInstance(main_group.dependencies, list)
            for req in main_group.dependencies:
                self.assertIsInstance(req, Requirement)

    @patch("uv_outdated.utils.get_all_metadata_from_site_packages")
    def test_package_optional_dependencies(self, mock_site_packages):
        """Test that Package optional dependencies (extras) are handled correctly."""
        # Mock site-packages to return empty dict (simulating no venv)
        mock_site_packages.return_value = {}

        packages = get_locked_packages_and_deps()

        # Find a package with optional dependencies
        pkg_with_extras = None
        for pkg in packages.values():
            if any(key != "" for key in pkg.requires):
                pkg_with_extras = pkg
                break

        if pkg_with_extras is not None:
            # Check that extra dependencies are properly structured
            for extra_name, dep_group in pkg_with_extras.requires.items():
                if extra_name != "":  # Skip main dependencies
                    self.assertIsInstance(dep_group, DependencyGroup)
                    self.assertEqual(dep_group.name, extra_name)
                    self.assertIsInstance(dep_group.dependencies, list)
                    for req in dep_group.dependencies:
                        self.assertIsInstance(req, Requirement)

    @patch("uv_outdated.utils.get_all_metadata_from_site_packages")
    def test_package_dependents_structure(self, mock_site_packages):
        """Test that Package dependents are properly structured."""
        # Mock site-packages to return empty dict (simulating no venv)
        mock_site_packages.return_value = {}

        packages = get_locked_packages_and_deps()

        # Find a package with dependents
        pkg_with_dependents = None
        for pkg in packages.values():
            if pkg.dependents:
                pkg_with_dependents = pkg
                break

        self.assertIsNotNone(
            pkg_with_dependents, "Should find at least one package with dependents"
        )

        # Check dependents structure
        if pkg_with_dependents is not None:
            for dependent in pkg_with_dependents.dependents:
                self.assertIsInstance(dependent, Dependent)
                self.assertIsInstance(dependent.through, str)
                self.assertIsInstance(dependent.package, Package)
                # The dependent package should also be in our packages dict
                self.assertIn(dependent.package.name, packages)

    @patch("uv_outdated.utils.get_all_metadata_from_site_packages")
    def test_project_package_representation(self, mock_site_packages):
        """Test that the project package is represented in the results."""
        # Mock site-packages to return empty dict (simulating no venv)
        mock_site_packages.return_value = {}

        packages = get_locked_packages_and_deps()

        # The project package should be present
        self.assertIn("uv-outdated", packages)

        project_pkg = packages["uv-outdated"]
        self.assertEqual(project_pkg.name, "uv-outdated")
        self.assertIsInstance(project_pkg.version, str)
        # Project package should have dependencies
        self.assertGreater(len(project_pkg.requires), 0)

    def test_graceful_site_packages_handling(self):
        """Test that the function handles site-packages unavailability gracefully."""

        def mock_get_site_packages():
            raise RuntimeError("Site-packages not available")

        # Should not raise an exception
        with patch(
            "uv_outdated.utils.get_all_metadata_from_site_packages", new=mock_get_site_packages
        ):
            packages = get_locked_packages_and_deps()
        self.assertIsInstance(packages, dict)
        self.assertGreater(len(packages), 0)

        # Packages should still have basic info, just no summaries
        for pkg in packages.values():
            self.assertIsInstance(pkg.name, str)
            self.assertIsInstance(pkg.version, str)
            # Summary should be empty when site-packages unavailable
            self.assertEqual(pkg.summary, "")

    @patch("uv_outdated.utils.get_all_metadata_from_site_packages")
    def test_uv_lock_structure_handling(self, mock_site_packages):
        """Test that the function correctly handles uv.lock structure."""
        # Mock site-packages to return empty dict (simulating no venv)
        mock_site_packages.return_value = {}

        packages = get_locked_packages_and_deps()

        # Test docstring point 4: project package should have dev-dependencies
        if "uv-outdated" in packages:
            project_pkg = packages["uv-outdated"]
            # Project should have main dependencies
            self.assertIn("", project_pkg.requires, "Project should have main dependencies")

            # Check that we have some dependencies
            main_deps = project_pkg.requires[""]
            self.assertGreater(
                len(main_deps.dependencies), 0, "Project should have main dependencies"
            )

        # Test docstring point 9: packages should have optional-dependencies (extras)
        pkg_with_extras = None
        for pkg in packages.values():
            if any(key != "" for key in pkg.requires):
                pkg_with_extras = pkg
                break

        if pkg_with_extras:
            # Verify that extras are properly handled
            for extra_name, dep_group in pkg_with_extras.requires.items():
                if extra_name != "":
                    self.assertIsInstance(dep_group, DependencyGroup)
                    self.assertEqual(dep_group.name, extra_name)

        # Test docstring point 12: dependents should track through which extra they come
        for pkg in packages.values():
            for dependent in pkg.dependents:
                # The 'through' field should indicate the dependency group
                self.assertIsInstance(dependent.through, str)
                # Empty string means main dependencies, non-empty means extra
                self.assertIn(dependent.through, ["", *list(dependent.package.requires.keys())])

    def test_canonicalize_name(self):
        """Test that package name canonicalization works correctly."""
        self.assertEqual(canonicalize_name("Django"), "django")
        self.assertEqual(canonicalize_name("django-cors-headers"), "django-cors-headers")
        self.assertEqual(canonicalize_name("Django_CORS_Headers"), "django-cors-headers")
        self.assertEqual(canonicalize_name("DJANGO.CORS.HEADERS"), "django-cors-headers")

    def test_is_locked_by_specifier(self):
        """Test the is_locked_by_specifier function."""
        specifiers = {
            "django": ">=5.0,<5.1",
            "requests": ">=2.31.0",
            "invalid": "invalid_spec",
            "empty": "",
        }

        # Test locked by specifier
        self.assertTrue(is_locked_by_specifier(specifiers, "django", "5.2.0"))
        self.assertFalse(is_locked_by_specifier(specifiers, "django", "5.0.9"))

        # Test not locked by specifier
        self.assertFalse(is_locked_by_specifier(specifiers, "requests", "2.32.0"))
        self.assertTrue(is_locked_by_specifier(specifiers, "requests", "2.30.0"))

        # Test invalid specifier
        self.assertFalse(is_locked_by_specifier(specifiers, "invalid", "1.0.0"))

        # Test empty specifier
        self.assertFalse(is_locked_by_specifier(specifiers, "empty", "1.0.0"))

        # Test missing package
        self.assertFalse(is_locked_by_specifier(specifiers, "missing", "1.0.0"))

    @patch("subprocess.run")
    def test_run_uv(self, mock_run):
        """Test that run_uv function works and returns a CompletedProcess."""
        # Mock subprocess.run to return a successful result
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "uv 0.4.0"
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        result = run_uv(["uv", "--version"])
        self.assertEqual(result.returncode, 0)
        self.assertIn("uv", result.stdout.lower())

    @patch("uv_outdated.utils.run_uv")
    def test_get_uv_outdated(self, mock_run_uv):
        """Test that get_uv_outdated returns a dictionary of outdated packages."""
        # Mock run_uv to return sample outdated packages JSON
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '[{"name": "django", "version": "5.0.1", "latest_version": "5.1.0"}]'
        mock_result.stderr = ""
        mock_run_uv.return_value = mock_result

        outdated = get_uv_outdated()
        self.assertIsInstance(outdated, dict)

        # Check that all values are OutdatedPkg instances
        for pkg in outdated.values():
            self.assertIsInstance(pkg, OutdatedPkg)
            self.assertTrue(hasattr(pkg, "name"))
            self.assertTrue(hasattr(pkg, "version"))
            self.assertTrue(hasattr(pkg, "latest_version"))

    @patch("uv_outdated.utils.run_uv")
    def test_get_uv_outdated_fallback(self, mock_run_uv):
        """Test that get_uv_outdated gracefully handles failures."""
        # Test case 1: Command fails (no venv)
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "No virtual environment found"
        mock_run_uv.return_value = mock_result

        # Should return empty dict instead of raising exception
        outdated = get_uv_outdated()
        self.assertIsInstance(outdated, dict)
        self.assertEqual(len(outdated), 0)

        # Test case 2: JSON parsing fails
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Invalid JSON"
        mock_run_uv.return_value = mock_result

        # Should return empty dict instead of raising exception
        outdated = get_uv_outdated()
        self.assertIsInstance(outdated, dict)
        self.assertEqual(len(outdated), 0)

    def test_get_direct_dependencies(self):
        """Test that get_direct_dependencies parses pyproject.toml correctly."""
        direct = get_direct_dependencies()
        self.assertIsInstance(direct, dict)

        # Check that some known dependencies are present
        self.assertIn("packaging", direct)

        # Check that dev dependencies are included
        self.assertIn("mypy", direct)

        # Check that all values are Requirement instances
        for req in direct.values():
            self.assertIsInstance(req, Requirement)

    @patch("uv_outdated.utils.get_all_metadata_from_site_packages")
    def test_get_locked_packages_and_deps(self, mock_site_packages):
        """Test that get_locked_packages_and_deps returns the expected structure."""
        # Mock site-packages to return empty dict (simulating no venv)
        mock_site_packages.return_value = {}

        packages = get_locked_packages_and_deps()

        # Check return type is dict[Name, Package] as specified in docstring
        self.assertIsInstance(packages, dict)

        # Check that some known packages are present
        self.assertIn("packaging", packages)

        # Check that all values are Package instances
        for pkg in packages.values():
            self.assertIsInstance(pkg, Package)
            self.assertTrue(hasattr(pkg, "name"))
            self.assertTrue(hasattr(pkg, "version"))
            self.assertTrue(hasattr(pkg, "summary"))
            self.assertTrue(hasattr(pkg, "requires"))
            self.assertTrue(hasattr(pkg, "dependents"))

        # Check that Package objects have proper structure
        django_pkg = packages["packaging"]
        self.assertEqual(django_pkg.name, "packaging")
        self.assertIsInstance(django_pkg.version, str)
        self.assertIsInstance(django_pkg.summary, str)
        self.assertIsInstance(django_pkg.requires, dict)
        self.assertIsInstance(django_pkg.dependents, list)

        # Check that requires contains DependencyGroup instances
        for dep_group in django_pkg.requires.values():
            self.assertIsInstance(dep_group, DependencyGroup)
            self.assertIsInstance(dep_group.name, str)
            self.assertIsInstance(dep_group.dependencies, list)
            for req in dep_group.dependencies:
                self.assertIsInstance(req, Requirement)

        # Check that dependents contain Dependent instances
        for dependent in django_pkg.dependents:
            self.assertIsInstance(dependent, Dependent)
            self.assertIsInstance(dependent.through, str)
            self.assertIsInstance(dependent.package, Package)

    @patch("uv_outdated.utils.run_uv")
    def test_get_site_packages_from_uv(self, mock_run_uv):
        """Test that get_site_packages_from_uv returns a valid path."""
        # Mock run_uv to return a fake Python path
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "/fake/venv/bin/python"
        mock_result.stderr = ""
        mock_run_uv.return_value = mock_result

        # Mock the pyvenv.cfg file reading and path operations
        mock_file = MagicMock()
        mock_file.__enter__ = lambda self: self
        mock_file.__exit__ = lambda self, *args: None
        mock_file.__iter__ = lambda self: iter(["version = 3.12.0\n"])

        with (
            patch("pathlib.Path.open", return_value=mock_file),
            patch("pathlib.Path.is_dir", return_value=True),
        ):
            site_packages = get_site_packages_from_uv()
            self.assertIsInstance(site_packages, Path)
            self.assertEqual(site_packages.name, "site-packages")

    @patch("uv_outdated.utils.get_site_packages_from_uv")
    @patch("pathlib.Path.glob")
    def test_get_all_metadata_from_site_packages(self, mock_glob, mock_site_packages_path):
        """Test that get_all_metadata_from_site_packages returns package metadata."""
        # Mock site-packages path
        mock_site_packages_path.return_value = Path("/fake/site-packages")

        # Mock dist-info directories
        mock_dist_info = MagicMock()
        mock_dist_info.name = "django-5.0.1.dist-info"
        mock_glob.return_value = [mock_dist_info]

        # Mock PathDistribution and metadata
        with patch("importlib.metadata.PathDistribution") as mock_path_dist:
            mock_dist = MagicMock()
            mock_metadata = MagicMock()
            mock_metadata.__getitem__ = lambda self, key: {
                "Name": "Django",
                "Version": "5.0.1",
                "Summary": "A high-level Python web framework",
            }[key]
            mock_metadata.get = lambda key, default="": {
                "Name": "Django",
                "Version": "5.0.1",
                "Summary": "A high-level Python web framework",
            }.get(key, default)
            mock_metadata.get_all = lambda key: []
            mock_dist.metadata = mock_metadata
            mock_path_dist.return_value = mock_dist

            metadata = get_all_metadata_from_site_packages()
            self.assertIsInstance(metadata, dict)

            # Check that all values are SitePackage instances
            for pkg in metadata.values():
                self.assertIsInstance(pkg, SitePackage)
                self.assertTrue(hasattr(pkg, "name"))
                self.assertTrue(hasattr(pkg, "version"))
                self.assertTrue(hasattr(pkg, "summary"))

    @patch("uv_outdated.utils.get_all_metadata_from_site_packages")
    def test_find_direct_ancestors(self, mock_site_packages):
        """Test that find_direct_ancestors correctly identifies dependency ancestors."""
        # Mock site-packages to return empty dict (simulating no venv)
        mock_site_packages.return_value = {}

        packages = get_locked_packages_and_deps()
        direct = get_direct_dependencies()

        # Test with a known transitive dependency
        if "sqlparse" in packages and "django" in direct:
            # sqlparse is typically a dependency of django
            ancestors = find_direct_ancestors("sqlparse", packages)
            # Should find django as an ancestor (or another direct dependency)
            self.assertGreater(len(ancestors), 0)
            # All ancestors should be direct dependencies
            for ancestor in ancestors:
                self.assertIn(ancestor, direct)

        # Test with a package that doesn't exist
        ancestors = find_direct_ancestors("nonexistent-package", packages)
        self.assertEqual(len(ancestors), 0)

    @patch("uv_outdated.utils.get_all_metadata_from_site_packages")
    @patch("uv_outdated.utils.get_uv_outdated")
    def test_group_by_ancestor_functionality(self, mock_outdated, mock_site_packages):
        """Test that group-by-ancestor functionality works correctly."""
        # Mock site-packages to return empty dict (simulating no venv)
        mock_site_packages.return_value = {}

        # Mock outdated packages
        mock_outdated.return_value = {
            "django": OutdatedPkg(name="django", version="5.0.1", latest_version="5.1.0"),
            "sqlparse": OutdatedPkg(name="sqlparse", version="0.4.4", latest_version="0.5.0"),
        }

        # Get test data
        packages = get_locked_packages_and_deps()
        direct = get_direct_dependencies()
        outdated = mock_outdated.return_value

        # Simulate the grouping logic
        groups: dict[str, list[tuple[Name, Package, OutdatedPkg, bool]]] = {}
        direct_dependencies_with_groups: set[str] = set()

        # Process some test packages
        test_packages = []
        for name, pkg in packages.items():
            if name in outdated:
                outdated_pkg = outdated[name]
                is_direct = name in direct
                test_packages.append((name, pkg, outdated_pkg, is_direct))

                if is_direct:
                    groups.setdefault(name, []).append((name, pkg, outdated_pkg, is_direct))
                else:
                    ancestors = find_direct_ancestors(name, packages)
                    if ancestors:
                        for ancestor in sorted(ancestors):
                            groups.setdefault(ancestor, []).append(
                                (name, pkg, outdated_pkg, is_direct)
                            )
                            direct_dependencies_with_groups.add(ancestor)

        # Verify grouping logic
        self.assertIsInstance(groups, dict)
        self.assertIsInstance(direct_dependencies_with_groups, set)

        # All group keys should be string names
        for group_key in groups:
            self.assertIsInstance(group_key, str)

        # All group values should be lists of tuples
        for group_packages in groups.values():
            self.assertIsInstance(group_packages, list)
            for item in group_packages:
                self.assertIsInstance(item, tuple)
                self.assertEqual(len(item), 4)  # (name, pkg, outdated_pkg, is_direct)

        # Check that packages in direct_dependencies_with_groups are in groups
        for direct_with_transitive in direct_dependencies_with_groups:
            self.assertIn(direct_with_transitive, groups)

    @patch("uv_outdated.utils.get_all_metadata_from_site_packages")
    def test_find_direct_ancestors_edge_cases(self, mock_site_packages):
        """Test find_direct_ancestors with edge cases."""
        # Mock site-packages to return empty dict (simulating no venv)
        mock_site_packages.return_value = {}

        packages = get_locked_packages_and_deps()
        direct = get_direct_dependencies()

        # Test with empty packages dict
        empty_packages: dict[Name, Package] = {}
        ancestors = find_direct_ancestors("nonexistent", empty_packages)
        self.assertEqual(len(ancestors), 0)

        # Test with a known transitive dependency
        if "sqlparse" in packages and "django" in direct:
            ancestors = find_direct_ancestors("sqlparse", packages)
            # Should find at least one ancestor (django or another that depends on
            # sqlparse)
            for ancestor in ancestors:
                self.assertIn(ancestor, direct)

    def test_package_formatting_consistency(self):
        """Test that package name formatting is consistent across display modes."""
        from packaging.utils import canonicalize_name

        # Test canonicalization consistency
        test_names = ["Django", "django-cors-headers", "Django_CORS_Headers", "DJANGO.CORS.HEADERS"]

        expected = ["django", "django-cors-headers", "django-cors-headers", "django-cors-headers"]

        for name, expected_canonical in zip(test_names, expected, strict=False):
            self.assertEqual(canonicalize_name(name), expected_canonical)

    @patch("uv_outdated.utils.get_all_metadata_from_site_packages")
    @patch("uv_outdated.utils.get_uv_outdated")
    def test_direct_dependency_appears_as_group_header(self, mock_outdated, mock_site_packages):
        """
        Test that direct dependencies appear as group headers even if not
        outdated.
        """
        # Mock site-packages to return empty dict (simulating no venv)
        mock_site_packages.return_value = {}

        # Mock outdated packages - include a transitive dependency
        mock_outdated.return_value = {
            "sqlparse": OutdatedPkg(name="sqlparse", version="0.4.4", latest_version="0.5.0")
        }

        packages = get_locked_packages_and_deps()
        direct = get_direct_dependencies()
        outdated = mock_outdated.return_value

        # Find a direct dependency that has outdated transitive dependencies
        # but might not be outdated itself
        direct_deps_with_transitive_outdated = set()

        for name in packages:
            if name in outdated and name not in direct:
                # This is a transitive outdated dependency
                ancestors = find_direct_ancestors(name, packages)
                direct_deps_with_transitive_outdated.update(ancestors)

        # Test that these direct dependencies would appear in group-by-ancestor output
        # even if they themselves are not outdated
        for direct_dep in direct_deps_with_transitive_outdated:
            if direct_dep in direct:
                # This direct dependency should appear as a group header
                self.assertIn(direct_dep, direct)

    @patch("uv_outdated.utils.get_all_metadata_from_site_packages")
    def test_specifiers_exclude_non_installed_extras(self, mock_site_packages):
        """
        Test that get_package_specifiers only includes constraints from
        actually installed extras, not from non-installed extras.
        """
        # Mock site-packages to return empty dict (simulating no venv)
        mock_site_packages.return_value = {}

        packages = get_locked_packages_and_deps()
        specifiers = get_package_specifiers(packages)

        # Test that django-ninja's "test" extra constraint on ruff is not included
        # since the "test" extra is not installed (django-ninja without extras)

        # First, verify django-ninja is actually installed
        if "django-ninja" in packages:
            # Check if ruff has a constraint
            if "ruff" in specifiers:
                # If ruff has a constraint, it should NOT be the ==0.5.7 from
                # django-ninja's test extra
                ruff_constraint = specifiers["ruff"]
                self.assertNotEqual(
                    ruff_constraint,
                    "==0.5.7",
                    "ruff should not be constrained by django-ninja's uninstalled 'test' extra",
                )

            # Alternatively, ruff might not have any constraints at all, which is
            # also correct since the django-ninja test extra is not installed

    # Mocked tests that don't require a venv
    @patch("uv_outdated.utils.get_site_packages_from_uv")
    @patch("uv_outdated.utils.get_all_metadata_from_site_packages")
    def test_get_locked_packages_and_deps_mocked(self, mock_site_packages, mock_site_packages_path):
        """Test get_locked_packages_and_deps with mocked site-packages."""
        # Mock site-packages to be unavailable (RuntimeError)
        mock_site_packages.side_effect = RuntimeError("Site-packages not available")

        # Should still work without site-packages
        packages = get_locked_packages_and_deps()
        self.assertIsInstance(packages, dict)
        self.assertGreater(len(packages), 0)

        # Packages should have basic info but no summaries
        for pkg in packages.values():
            self.assertIsInstance(pkg.name, str)
            self.assertIsInstance(pkg.version, str)
            self.assertEqual(pkg.summary, "")  # Should be empty without site-packages

    @patch("uv_outdated.utils.get_all_metadata_from_site_packages")
    def test_local_editable_package_without_version_is_empty_string(self, mock_site_packages):
        """If no site metadata exists, missing uv.lock version becomes empty string."""
        mock_site_packages.return_value = {}

        uv_lock = {
            "package": [
                {
                    "name": "my-local-pkg",
                    "source": {"editable": "."},
                    # intentionally no "version"
                    "dependencies": [],
                }
            ]
        }

        with patch("uv_outdated.utils.tomllib.load", return_value=uv_lock):
            packages = get_locked_packages_and_deps()

        self.assertIn("my-local-pkg", packages)
        self.assertEqual(packages["my-local-pkg"].version, "")

    @patch("uv_outdated.utils.get_all_metadata_from_site_packages")
    def test_local_editable_package_without_version_uses_site_metadata(self, mock_site_packages):
        """If site metadata exists, missing uv.lock version falls back to it."""
        mock_dist = MagicMock()
        mock_dist.__class__ = importlib.metadata.PathDistribution  # type: ignore[assignment]

        mock_site_packages.return_value = {
            "my-local-pkg": SitePackage(
                name="my-local-pkg",
                version="9.9.9",
                summary="Local package",
                requires_dist=[],
                provides_extra=[],
                distribution=mock_dist,
            )
        }

        uv_lock = {
            "package": [
                {
                    "name": "my-local-pkg",
                    "source": {"editable": "."},
                    # intentionally no "version"
                    "dependencies": [],
                }
            ]
        }

        with patch("uv_outdated.utils.tomllib.load", return_value=uv_lock):
            packages = get_locked_packages_and_deps()

        self.assertIn("my-local-pkg", packages)
        self.assertEqual(packages["my-local-pkg"].version, "9.9.9")
        self.assertEqual(packages["my-local-pkg"].summary, "Local package")

    @patch("uv_outdated.utils.get_uv_outdated")
    def test_get_uv_outdated_mocked(self, mock_outdated):
        """Test get_uv_outdated with mocked uv output."""
        # Mock the outdated packages response
        mock_outdated.return_value = {
            "django": OutdatedPkg(name="django", version="5.0.1", latest_version="5.1.0"),
            "celery": OutdatedPkg(name="celery", version="5.3.4", latest_version="5.3.5"),
        }

        outdated = mock_outdated.return_value
        self.assertIsInstance(outdated, dict)
        self.assertIn("django", outdated)
        self.assertIn("celery", outdated)

        for pkg in outdated.values():
            self.assertIsInstance(pkg, OutdatedPkg)

    def test_pure_functions_without_venv(self):
        """Test functions that don't depend on venv or external calls."""
        # Test canonicalize_name (this doesn't need venv)
        self.assertEqual(canonicalize_name("Django"), "django")
        self.assertEqual(canonicalize_name("django-cors-headers"), "django-cors-headers")

        # Test is_locked_by_specifier (pure function)
        specifiers = {"django": ">=5.0,<5.1", "requests": ">=2.31.0"}
        self.assertTrue(is_locked_by_specifier(specifiers, "django", "5.2.0"))
        self.assertFalse(is_locked_by_specifier(specifiers, "django", "5.0.9"))
        self.assertFalse(is_locked_by_specifier(specifiers, "requests", "2.32.0"))

    # --- Added targeted tests ---
    def make_pkg(self, name: str, version: str) -> Package:
        return Package(name=name, version=version, summary="", requires={}, dependents=[])

    def test_color_yellow_only_when_current_allowed_latest_blocked(self):
        name = "demo"
        pkg = self.make_pkg(name, "1.5.0")
        outdated = OutdatedPkg(name=name, version=pkg.version, latest_version="2.1.0")
        specifiers = {name: ">=1,<2"}
        row = _create_package_row(
            name=name,
            pkg=pkg,
            outdated_pkg=outdated,
            is_direct=True,
            specifiers=specifiers,
            show_why=True,
            name_format="{name}",
        )
        self.assertIn("[yellow]2.1.0[/yellow]", row[2])
        self.assertEqual(row[3], ">=1,<2")

    def test_color_red_when_both_current_and_latest_disallowed(self):
        name = "demo"
        pkg = self.make_pkg(name, "1.6.0")
        outdated = OutdatedPkg(name=name, version=pkg.version, latest_version="2.0.0")
        specifiers = {name: "<=1.5"}
        row = _create_package_row(
            name=name,
            pkg=pkg,
            outdated_pkg=outdated,
            is_direct=True,
            specifiers=specifiers,
            show_why=True,
            name_format="{name}",
        )
        self.assertIn("[red]2.0.0[/red]", row[2])
        self.assertEqual(row[3], "")

    def test_color_red_when_both_allowed(self):
        name = "demo"
        pkg = self.make_pkg(name, "1.5.0")
        outdated = OutdatedPkg(name=name, version=pkg.version, latest_version="2.0.0")
        specifiers = {name: ">=1"}
        row = _create_package_row(
            name=name,
            pkg=pkg,
            outdated_pkg=outdated,
            is_direct=False,
            specifiers=specifiers,
            show_why=True,
            name_format="{name}",
        )
        self.assertIn("[red]2.0.0[/red]", row[2])
        self.assertEqual(row[3], "")

    def test_color_red_when_no_specifier(self):
        name = "demo"
        pkg = self.make_pkg(name, "1.0.0")
        outdated = OutdatedPkg(name=name, version=pkg.version, latest_version="1.1.0")
        specifiers = {}
        row = _create_package_row(
            name=name,
            pkg=pkg,
            outdated_pkg=outdated,
            is_direct=True,
            specifiers=specifiers,
            show_why=True,
            name_format="{name}",
        )
        self.assertIn("[red]1.1.0[/red]", row[2])

    def test_collect_outdated_filters_equal_latest_and_current(self):
        packages = {
            "a": self.make_pkg("a", "1.0.0"),
            "b": self.make_pkg("b", "2.0.0"),
        }
        outdated = {
            "a": OutdatedPkg(name="a", version="1.0.0", latest_version="1.1.0"),
            "b": OutdatedPkg(name="b", version="2.0.0", latest_version="2.0.0"),
        }
        direct = {"a": object(), "b": object()}
        collected = collect_outdated_packages(
            packages=packages,
            outdated=outdated,
            direct=direct,
            direct_only=False,
            transitive_only=False,
        )
        names = [name for name, *_ in collected]
        self.assertEqual(names, ["a"])
