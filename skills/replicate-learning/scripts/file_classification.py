#!/usr/bin/env python3
"""Deterministic Project DeepDive file typing and repository-surface classification.

This module is deliberately pure: it performs no filesystem, Git or network I/O and
it never consults the host MIME registry, so two machines classify the same tracked
path set identically.

Precedence is fixed and documented in ``references/coverage-policy.md``:

1. explicit exact-path override;
2. tracked sensitive environment files (``.env`` and friends);
3. strong vendor-directory conventions;
4. strong generated-directory/name conventions;
5. exact filenames (CI, infrastructure, lockfile, build, configuration, environment, tooling);
6. path-component conventions (fixture, migration, test, documentation, script, CLI, MCP,
   database, asset, frontend, backend, API, domain, agent, tooling, configuration);
7. known-extension fallback with no architectural claim;
8. ``UNKNOWN`` with surface ``other``.

The output status vocabulary is intentionally conservative: Phase 2 never emits
``COVERED`` because inventory is not proof that a file was taught, graphed or
source-verified.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import NamedTuple

CLASSIFICATION_POLICY_VERSION = "1.0.0"

#: Published surface vocabulary; must stay identical to the coverage schema enum.
SURFACES: tuple[str, ...] = (
    "backend",
    "frontend",
    "domain",
    "api",
    "agent",
    "database",
    "migration",
    "test",
    "fixture",
    "configuration",
    "environment",
    "infrastructure",
    "build",
    "script",
    "cli",
    "mcp",
    "ci",
    "tooling",
    "documentation",
    "asset",
    "lockfile",
    "generated",
    "vendor",
    "other",
)

CLASSIFICATIONS: tuple[str, ...] = (
    "UNKNOWN",
    "COVERED",
    "CLASSIFIED",
    "GENERATED",
    "VENDOR",
    "IGNORED_WITH_REASON",
)

TEACHING_STATUSES: tuple[str, ...] = (
    "NOT_STARTED",
    "LOCATED",
    "TAUGHT",
    "VERIFIED",
    "NOT_APPLICABLE",
)

#: Phase 2 exact-path overrides may resolve ambiguity to these four classifications.
OVERRIDE_CLASSIFICATIONS: tuple[str, ...] = (
    "CLASSIFIED",
    "GENERATED",
    "VENDOR",
    "IGNORED_WITH_REASON",
)

CONTENT_KINDS: tuple[str, ...] = ("text", "binary", "symlink", "gitlink")

TEACHING_STATUS_BY_CLASSIFICATION: dict[str, str] = {
    "CLASSIFIED": "LOCATED",
    "UNKNOWN": "LOCATED",
    "GENERATED": "NOT_APPLICABLE",
    "VENDOR": "NOT_APPLICABLE",
    "IGNORED_WITH_REASON": "NOT_APPLICABLE",
    "COVERED": "NOT_APPLICABLE",
}

#: Stable built-in rule IDs.  They are a closed vocabulary: never derive one from path text.
RULE_IDS: frozenset[str] = frozenset({
    "override:exact-path",
    "safety:tracked-env",
    "path:vendor",
    "path:generated",
    "name:ci",
    "name:infrastructure",
    "name:build",
    "name:lockfile",
    "name:configuration",
    "name:environment",
    "name:tooling",
    "path:test",
    "path:fixture",
    "path:configuration",
    "path:tooling",
    "path:documentation",
    "path:script",
    "path:cli",
    "path:mcp",
    "path:database",
    "path:migration",
    "path:asset",
    "path:frontend",
    "path:backend",
    "path:api",
    "path:domain",
    "path:agent",
    "extension:known",
    "fallback:unknown",
})

_REASON_TEXT: dict[str, str] = {
    "safety:tracked-env": "Tracked environment file: its contents must never be taught or logged; "
                          "it stays indexed for coverage accounting only.",
    "path:vendor": "Third-party vendored path: external code, not repository-owned source.",
    "path:generated": "Generated build output: reproduced from sources instead of authored by hand.",
    "name:ci": "CI/CD pipeline definition.",
    "name:infrastructure": "Container, deployment or edge infrastructure definition.",
    "name:build": "Build or package manifest.",
    "name:lockfile": "Dependency lockfile: pinned external versions, not authored source.",
    "name:configuration": "Application configuration file.",
    "name:environment": "Non-secret environment example file.",
    "name:tooling": "Developer tooling configuration.",
    "path:test": "Test code or test-directory convention.",
    "path:fixture": "Test fixture data used by tests rather than shipped as product code.",
    "path:configuration": "Configuration directory convention.",
    "path:tooling": "Developer tooling directory convention.",
    "path:documentation": "Documentation directory or repository-document name convention.",
    "path:script": "Automation/script directory convention.",
    "path:cli": "Command-line entry-point convention.",
    "path:mcp": "MCP/tool-server convention.",
    "path:database": "Database schema or seed convention.",
    "path:migration": "Ordered schema migration convention.",
    "path:asset": "Static/public web asset convention.",
    "path:frontend": "Frontend page/component/state convention.",
    "path:backend": "Explicit backend/server path convention.",
    "path:api": "Explicit API/route convention.",
    "path:domain": "Explicit domain/core model convention.",
    "path:agent": "Explicit agent/RAG/tool convention.",
}

_MEDIA_TYPE_REASON = (
    "Recognized {media_type} file; no deterministic convention fixes its architectural role, "
    "so Phase 3 determines it."
)
_UNKNOWN_REASON = (
    "No deterministic built-in rule matched this tracked path; it stays UNKNOWN until an "
    "exact-path override records its role and evidence."
)

# ---------------------------------------------------------------------------
# Deterministic media typing
# ---------------------------------------------------------------------------

EXACT_NAME_MEDIA_TYPES: dict[str, str] = {
    "dockerfile": "text/x-dockerfile",
    "containerfile": "text/x-dockerfile",
    "makefile": "text/x-makefile",
    "gnumakefile": "text/x-makefile",
    "cmakelists.txt": "text/x-cmake",
    "jenkinsfile": "text/x-groovy",
    "procfile": "text/plain",
    "vagrantfile": "text/x-ruby",
}

EXTENSION_MEDIA_TYPES: dict[str, str] = {
    # source
    "py": "text/x-python",
    "pyi": "text/x-python",
    "java": "text/x-java",
    "kt": "text/x-kotlin",
    "kts": "text/x-kotlin",
    "scala": "text/x-scala",
    "groovy": "text/x-groovy",
    "c": "text/x-c",
    "h": "text/x-c",
    "cc": "text/x-c++",
    "cpp": "text/x-c++",
    "cxx": "text/x-c++",
    "hpp": "text/x-c++",
    "cs": "text/x-csharp",
    "go": "text/x-go",
    "rs": "text/x-rust",
    "rb": "text/x-ruby",
    "php": "text/x-php",
    "swift": "text/x-swift",
    "m": "text/x-objective-c",
    "lua": "text/x-lua",
    "pl": "text/x-perl",
    "r": "text/x-r",
    "ex": "text/x-elixir",
    "exs": "text/x-elixir",
    "erl": "text/x-erlang",
    "dart": "text/x-dart",
    "sh": "text/x-shellscript",
    "bash": "text/x-shellscript",
    "zsh": "text/x-shellscript",
    "fish": "text/x-shellscript",
    "ps1": "text/x-powershell",
    "psm1": "text/x-powershell",
    "bat": "text/x-batch",
    "cmd": "text/x-batch",
    # scripted web
    "js": "text/javascript",
    "mjs": "text/javascript",
    "cjs": "text/javascript",
    "jsx": "text/jsx",
    "ts": "text/typescript",
    "mts": "text/typescript",
    "cts": "text/typescript",
    "tsx": "text/tsx",
    "vue": "text/x-vue",
    "svelte": "text/x-svelte",
    "html": "text/html",
    "htm": "text/html",
    "css": "text/css",
    "scss": "text/x-scss",
    "sass": "text/x-sass",
    "less": "text/x-less",
    # data / markup
    "json": "application/json",
    "jsonc": "application/json",
    "json5": "application/json",
    "map": "application/json",
    "yml": "application/x-yaml",
    "yaml": "application/x-yaml",
    "toml": "application/toml",
    "ini": "text/x-ini",
    "cfg": "text/x-ini",
    "conf": "text/plain",
    "properties": "text/x-java-properties",
    "xml": "application/xml",
    "xsd": "application/xml",
    "xsl": "application/xml",
    "plist": "application/xml",
    "csv": "text/csv",
    "tsv": "text/tab-separated-values",
    "sql": "text/x-sql",
    "graphql": "application/graphql",
    "gql": "application/graphql",
    "proto": "text/x-protobuf",
    "md": "text/markdown",
    "markdown": "text/markdown",
    "rst": "text/x-rst",
    "adoc": "text/x-asciidoc",
    "txt": "text/plain",
    "lock": "text/plain",
    "env": "text/plain",
    # media / binary
    "svg": "image/svg+xml",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "bmp": "image/bmp",
    "ico": "image/vnd.microsoft.icon",
    "tiff": "image/tiff",
    "pdf": "application/pdf",
    "zip": "application/zip",
    "gz": "application/gzip",
    "bz2": "application/x-bzip2",
    "xz": "application/x-xz",
    "tar": "application/x-tar",
    "jar": "application/java-archive",
    "war": "application/java-archive",
    "class": "application/java-vm",
    "exe": "application/vnd.microsoft.portable-executable",
    "dll": "application/vnd.microsoft.portable-executable",
    "so": "application/x-sharedlib",
    "dylib": "application/x-sharedlib",
    "wasm": "application/wasm",
    "woff": "font/woff",
    "woff2": "font/woff2",
    "ttf": "font/ttf",
    "otf": "font/otf",
    "eot": "application/vnd.ms-fontobject",
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "ogg": "audio/ogg",
    "mp4": "video/mp4",
    "webm": "video/webm",
    "mov": "video/quicktime",
}

#: ``application/*`` types that are still plain text payloads.
_TEXT_APPLICATION_TYPES: frozenset[str] = frozenset({
    "application/json",
    "application/xml",
    "application/x-yaml",
    "application/toml",
    "application/graphql",
})

#: Documentation-ish extensions that are unambiguous enough to name a surface.
_DOCUMENTATION_EXTENSIONS: frozenset[str] = frozenset({"md", "markdown", "rst", "adoc"})

_EXTENSION_RE = re.compile(r"^[a-z0-9]+$")
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")


@dataclass(frozen=True)
class FileFacts:
    """Normalized facts about one tracked path.  No raw content is carried."""

    path: str
    content_kind: str
    media_type: str
    extension: str


@dataclass(frozen=True)
class CoverageOverride:
    """A human exact-path classification decision with its evidence."""

    surface: str
    classification: str
    reason: str


@dataclass(frozen=True)
class CoverageDecision:
    """The final Phase 2 classification of one tracked path."""

    surface: str
    secondary_surfaces: tuple[str, ...]
    classification: str
    teaching_status: str
    reason: str
    rule_id: str


def normalize_artifact_path(path: object) -> str:
    """Return the normalized analysis-root-relative POSIX path or raise ``ValueError``."""
    if not isinstance(path, str):
        raise ValueError(f"path must be a string, got {type(path).__name__}")
    text = path
    if text.startswith("./"):
        text = text[2:]
    if not text:
        raise ValueError("path must not be empty")
    if text == ".":
        raise ValueError(f"path must name a file below the analysis root: {path!r}")
    if "\\" in text:
        raise ValueError(f"path must use POSIX separators: {path!r}")
    if text.startswith("/") or _WINDOWS_DRIVE_RE.match(text):
        raise ValueError(f"absolute path is not allowed: {path!r}")
    parts = text.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError(f"path must be relative and must not escape the analysis root: {path!r}")
    return "/".join(parts)


def extension_of(path: str) -> str:
    """Return the lowercase alphanumeric final suffix, or ``""`` when there is none."""
    name = str(path).replace("\\", "/").rsplit("/", 1)[-1]
    if "." not in name:
        return ""
    stem, _, suffix = name.rpartition(".")
    if not stem:
        return ""
    suffix = suffix.lower()
    return suffix if _EXTENSION_RE.fullmatch(suffix) else ""


def _basename(path: str) -> str:
    return str(path).replace("\\", "/").rsplit("/", 1)[-1]


def _content_kind_for_media_type(media_type: str) -> str:
    if media_type.startswith("text/") or media_type == "image/svg+xml":
        return "text"
    if media_type in _TEXT_APPLICATION_TYPES:
        return "text"
    return "binary"


def detect_media_type(path: str, content_kind: str, sample: bytes) -> tuple[str, str]:
    """Return ``(media_type, content_kind)`` from the path, the Git mode and a byte sample.

    ``content_kind`` is the Git-derived mode: ``gitlink``/``symlink`` are authoritative,
    anything else is treated as a regular file whose text/binary nature is decided here.
    """
    if content_kind == "gitlink":
        return "application/x-gitlink", "gitlink"
    name = _basename(path).lower()
    extension = extension_of(path)
    if content_kind == "symlink":
        media_type = EXACT_NAME_MEDIA_TYPES.get(name) or EXTENSION_MEDIA_TYPES.get(extension)
        return (media_type or "text/plain"), "symlink"
    media_type = EXACT_NAME_MEDIA_TYPES.get(name) or EXTENSION_MEDIA_TYPES.get(extension)
    if media_type is not None:
        return media_type, _content_kind_for_media_type(media_type)
    if b"\x00" in sample:
        return "application/octet-stream", "binary"
    return "text/plain", "text"


# ---------------------------------------------------------------------------
# Ordered surface rules
# ---------------------------------------------------------------------------

class _Match(NamedTuple):
    surface: str
    rule_id: str
    classification: str
    reason: str
    contributes_secondary: bool = True


_VENDOR_COMPONENTS = frozenset({"vendor", "vendors", "third_party", "thirdparty", "third-party"})
_GENERATED_COMPONENTS = frozenset({
    "dist", "target", "build", "out", "output", "generated", "gen", "coverage",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".next", ".nuxt",
})
_CI_NAMES = frozenset({
    ".gitlab-ci.yml", ".gitlab-ci.yaml", "jenkinsfile", "azure-pipelines.yml",
    "azure-pipelines.yaml", ".travis.yml", ".travis.yaml", "appveyor.yml",
    "bitbucket-pipelines.yml", "buildkite.yml", "buildkite.yaml", "ci.yml", "ci.yaml",
    ".drone.yml", "cloudbuild.yaml",
})
_CI_COMPONENTS = frozenset({".circleci"})
_INFRASTRUCTURE_NAMES = frozenset({
    "containerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml",
    "nginx.conf", "caddy.conf", "procfile", "vagrantfile", "skaffold.yaml",
})
_INFRASTRUCTURE_COMPONENTS = frozenset({"k8s", "kubernetes", "helm", "charts", "terraform", "ansible"})
_LOCKFILE_NAMES = frozenset({
    "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb",
    "poetry.lock", "pipfile.lock", "cargo.lock", "go.sum", "gemfile.lock", "composer.lock",
    "packages.lock.json", "paket.lock", "mix.lock", "flake.lock",
})
_BUILD_NAMES = frozenset({
    "pyproject.toml", "setup.py", "setup.cfg", "pom.xml", "build.gradle", "build.gradle.kts",
    "settings.gradle", "settings.gradle.kts", "build.xml", "makefile", "gnumakefile",
    "cmakelists.txt", "cargo.toml", "go.mod", "mix.exs", "build.sbt", "package.json",
    "meson.build", "build.ninja", "directory.build.props", "conanfile.txt", "conanfile.py",
})
_BUILD_SUFFIXES = (".csproj", ".fsproj", ".vbproj", ".vcxproj", ".sln", ".nuspec")
_CONFIGURATION_NAMES = frozenset({
    "application.yml", "application.yaml", "application.properties", "application.json",
    "appsettings.json", "settings.yml", "settings.yaml", "settings.py", "settings.json",
    "config.yml", "config.yaml", "config.json", "config.toml", "logging.yml", "logging.yaml",
    "logback.xml", "log4j2.xml", "web.config", "app.config", "uwsgi.ini", "gunicorn.conf.py",
})
_CONFIGURATION_EXTENSIONS = frozenset({"yml", "yaml", "properties", "json", "toml"})
_ENVIRONMENT_EXAMPLES = frozenset({"example", "sample", "template", "defaults", "dist", "test"})
_TOOLING_NAMES = frozenset({
    ".pre-commit-config.yaml", ".pre-commit-config.yml", ".editorconfig", ".gitignore",
    ".gitattributes", ".dockerignore", ".npmrc", ".nvmrc", ".node-version", ".python-version",
    ".tool-versions", "tsconfig.json", "jsconfig.json", ".eslintrc", ".eslintrc.js",
    ".eslintrc.json", ".eslintrc.cjs", ".eslintignore", ".prettierrc", ".prettierrc.json",
    ".prettierignore", "ruff.toml", ".ruff.toml", "mypy.ini", ".mypy.ini", "tox.ini",
    "pytest.ini", "noxfile.py", ".babelrc", "babel.config.js", "jest.config.js", "jest.config.ts",
    "vitest.config.ts", "vitest.config.js", "rollup.config.js", "webpack.config.js",
    "karma.conf.js", "tailwind.config.js", "postcss.config.js", "stylelint.config.js",
    ".stylelintrc", "commitlint.config.js", ".flake8", "sonar-project.properties",
    ".golangci.yml", "rustfmt.toml", ".rubocop.yml", ".shellcheckrc", "codecov.yml",
})
_TOOLING_CONFIG_SUFFIXES = (
    ".config.js", ".config.mjs", ".config.cjs", ".config.ts", ".config.mts", ".config.cts",
    ".config.json", ".config.yml", ".config.yaml",
)
_TOOLING_COMPONENTS = frozenset({
    ".mvn", ".vscode", ".idea", ".devcontainer", ".github", ".husky", "tools", "tooling",
})
_FIXTURE_COMPONENTS = frozenset({
    "fixture", "fixtures", "testdata", "test-data", "__snapshots__", "snapshots", "golden",
})
_MIGRATION_COMPONENTS = frozenset({"migration", "migrations"})
_TEST_COMPONENTS = frozenset({"test", "tests", "__tests__", "spec", "specs"})
_DOCUMENTATION_COMPONENTS = frozenset({"doc", "docs", "documentation"})
_DOCUMENTATION_NAME_PREFIXES = (
    "readme", "changelog", "changes", "contributing", "code_of_conduct", "security", "authors",
    "notice", "license", "licence", "copying", "roadmap", "architecture", "governance",
)
_DOCUMENTATION_NAME_EXTENSIONS = frozenset({"", "md", "markdown", "rst", "txt", "adoc"})
_SCRIPT_COMPONENTS = frozenset({"script", "scripts", "bin", "automation", "hack"})
_CLI_COMPONENTS = frozenset({"cli", "cmd", "console"})
_MCP_COMPONENTS = frozenset({"mcp", "mcp-server", "mcp_server", "toolserver", "tool-server"})
_DATABASE_COMPONENTS = frozenset({"db", "database", "schema", "schemas", "sql", "seed", "seeds"})
_ASSET_COMPONENTS = frozenset({
    "assets", "asset", "static", "public", "wwwroot", "images", "img", "fonts", "media",
})
_FRONTEND_COMPONENTS = frozenset({
    "frontend", "ui", "pages", "page", "components", "component", "views", "view",
    "store", "stores", "hooks", "styles", "css", "sass",
})
_BACKEND_COMPONENTS = frozenset({"backend", "server", "service", "services"})
_PRODUCTION_SOURCE_LANGUAGES = frozenset({"java", "kotlin", "scala", "groovy"})
_API_COMPONENTS = frozenset({
    "api", "apis", "routes", "route", "controllers", "controller", "endpoints", "endpoint",
    "handlers", "handler", "rest",
})
_DOMAIN_COMPONENTS = frozenset({
    "domain", "domains", "core", "model", "models", "entities", "entity", "aggregate",
})
_AGENT_COMPONENTS = frozenset({
    "agent", "agents", "rag", "llm", "prompts", "prompt", "chain", "chains", "embeddings",
})
_CONFIGURATION_COMPONENTS = frozenset({"config", "configs", "configuration", "conf", "settings"})

_TEST_NAME_RE = re.compile(
    r"(^test[_-]|^it[_-]|_test\.|_tests\.|_spec\.|\.test\.|\.spec\.|"
    r"test\.(java|kt|scala|cs|py|js|ts|tsx|jsx|go|rb|php|rs)$)"
)
_TEST_JAVA_SUFFIXES = ("Test.java", "Tests.java", "IT.java", "Test.kt", "Tests.kt",
                       "Test.scala", "Tests.scala", "Test.cs", "Tests.cs")
_FLYWAY_MIGRATION_RE = re.compile(r"^[vV][0-9]+__.+\.sql$")


def _components(path: str) -> tuple[str, ...]:
    return tuple(path.split("/")[:-1])


def _has_production_source_root(components: tuple[str, ...]) -> bool:
    if "src" not in components or "main" not in components:
        return False
    if components.index("src") >= components.index("main"):
        return False
    return any(component in _PRODUCTION_SOURCE_LANGUAGES for component in components)


def _is_environment_example(name: str) -> bool:
    if not name.startswith(".env."):
        return False
    return name.removeprefix(".env.") in _ENVIRONMENT_EXAMPLES


def _is_sensitive_environment(name: str) -> bool:
    if not name.startswith(".env"):
        return False
    return not _is_environment_example(name)


def _is_ci(components: tuple[str, ...], name: str) -> bool:
    if name in _CI_NAMES:
        return True
    if name == ".gitlab-ci.yml" or name == ".gitlab-ci.yaml":
        return True
    if len(components) >= 2 and components[0] == ".github" and components[1] == "workflows":
        return True
    return any(component in _CI_COMPONENTS for component in components)


def _is_infrastructure(components: tuple[str, ...], name: str, extension: str) -> bool:
    if name in _INFRASTRUCTURE_NAMES or name == "dockerfile" or name.startswith("dockerfile."):
        return True
    if extension == "dockerfile":
        return True
    return any(component in _INFRASTRUCTURE_COMPONENTS for component in components)


def _is_lockfile(name: str, extension: str) -> bool:
    if name in _LOCKFILE_NAMES or extension == "lock":
        return True
    return name.startswith("requirements") and extension == "txt"


def _is_build(name: str, extension: str) -> bool:
    if name in _BUILD_NAMES:
        return True
    return any(name.endswith(suffix) for suffix in _BUILD_SUFFIXES)


def _is_configuration(name: str, extension: str) -> bool:
    if name in _CONFIGURATION_NAMES:
        return True
    return name.startswith("application-") and extension in _CONFIGURATION_EXTENSIONS


def _is_tooling(name: str) -> bool:
    if name in _TOOLING_NAMES:
        return True
    return any(name.endswith(suffix) for suffix in _TOOLING_CONFIG_SUFFIXES)


def _is_test_name(name: str) -> bool:
    if any(name.endswith(suffix) for suffix in _TEST_JAVA_SUFFIXES):
        return True
    return _TEST_NAME_RE.search(name.lower()) is not None


def _is_documentation_name(stem: str, extension: str) -> bool:
    if extension not in _DOCUMENTATION_NAME_EXTENSIONS:
        return False
    return stem.startswith(_DOCUMENTATION_NAME_PREFIXES)


def _iter_matches(path: str) -> list[_Match]:
    name = _basename(path).lower()
    extension = extension_of(path)
    stem = name[: len(name) - len(extension) - 1] if extension else name
    components = _components(path)

    matches: list[_Match] = []

    def add(surface: str, rule_id: str, classification: str = "CLASSIFIED",
            reason: str | None = None, contributes_secondary: bool = True) -> None:
        matches.append(_Match(
            surface=surface,
            rule_id=rule_id,
            classification=classification,
            reason=reason if reason is not None else _REASON_TEXT[rule_id],
            contributes_secondary=contributes_secondary,
        ))

    # Tier 3 — strong vendor conventions.
    if any(component in _VENDOR_COMPONENTS for component in components):
        add("vendor", "path:vendor", "VENDOR")

    # Tier 4 — strong generated conventions.
    if any(component in _GENERATED_COMPONENTS for component in components):
        add("generated", "path:generated", "GENERATED")

    # Tier 5 — exact filenames.
    if _is_ci(components, name):
        add("ci", "name:ci")
    if _is_infrastructure(components, name, extension):
        add("infrastructure", "name:infrastructure")
    if _is_lockfile(name, extension):
        add("lockfile", "name:lockfile")
    if _is_build(name, extension):
        add("build", "name:build")
    if _is_configuration(name, extension):
        add("configuration", "name:configuration")
    if _is_environment_example(name):
        add("environment", "name:environment")
    if _is_tooling(name):
        add("tooling", "name:tooling")

    # Tier 6 — path-component conventions (most specific first).
    if any(component in _FIXTURE_COMPONENTS for component in components):
        add("fixture", "path:fixture")
    if any(component in _MIGRATION_COMPONENTS for component in components) \
            or _FLYWAY_MIGRATION_RE.match(_basename(path)):
        add("migration", "path:migration")
    if any(component in _TEST_COMPONENTS for component in components) or _is_test_name(_basename(path)):
        add("test", "path:test")
    if any(component in _DOCUMENTATION_COMPONENTS for component in components) \
            or _is_documentation_name(stem, extension):
        add("documentation", "path:documentation")
    if any(component in _SCRIPT_COMPONENTS for component in components):
        add("script", "path:script")
    if any(component in _CLI_COMPONENTS for component in components):
        add("cli", "path:cli")
    if any(component in _MCP_COMPONENTS for component in components):
        add("mcp", "path:mcp")
    if any(component in _DATABASE_COMPONENTS for component in components):
        add("database", "path:database")
    if any(component in _ASSET_COMPONENTS for component in components):
        add("asset", "path:asset")
    if any(component in _FRONTEND_COMPONENTS for component in components):
        add("frontend", "path:frontend")
    if any(component in _BACKEND_COMPONENTS for component in components):
        add("backend", "path:backend")
    elif _has_production_source_root(components):
        add(
            "backend",
            "path:backend",
            reason="Maven/Gradle production source set (src/main/<language>); "
                   "Phase 3 confirms its architectural role.",
        )
    if any(component in _API_COMPONENTS for component in components):
        add("api", "path:api")
    if any(component in _DOMAIN_COMPONENTS for component in components):
        add("domain", "path:domain")
    if any(component in _AGENT_COMPONENTS for component in components):
        add("agent", "path:agent")
    if any(component in _TOOLING_COMPONENTS for component in components):
        add("tooling", "path:tooling")
    if any(component in _CONFIGURATION_COMPONENTS for component in components):
        add("configuration", "path:configuration")

    # Tier 7 — known extension with no architectural claim.
    known_media_type = EXACT_NAME_MEDIA_TYPES.get(name) or EXTENSION_MEDIA_TYPES.get(extension)
    if known_media_type is not None:
        surface = "documentation" if extension in _DOCUMENTATION_EXTENSIONS else "other"
        matches.append(_Match(
            surface=surface,
            rule_id="extension:known",
            classification="CLASSIFIED",
            reason=_MEDIA_TYPE_REASON.format(media_type=known_media_type),
            contributes_secondary=False,
        ))

    return matches


def normalize_override(
    surface: object,
    classification: object,
    reason: object,
) -> CoverageOverride:
    """Validate and normalize one override triple, failing closed on anything unusable."""
    if not isinstance(surface, str) or surface not in SURFACES:
        raise ValueError(f"override surface is not in the published vocabulary: {surface!r}")
    if not isinstance(classification, str) or classification not in OVERRIDE_CLASSIFICATIONS:
        raise ValueError(
            f"override classification {classification!r} is not allowed in Phase 2; "
            f"choose one of {', '.join(OVERRIDE_CLASSIFICATIONS)}"
        )
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("override reason must be a non-empty explanation")
    return CoverageOverride(surface=surface, classification=classification, reason=reason)


def classify_file(facts: FileFacts, override: CoverageOverride | None = None) -> CoverageDecision:
    """Classify one tracked path from pure facts, optionally with a human override."""
    path = normalize_artifact_path(facts.path)
    if facts.content_kind not in CONTENT_KINDS:
        raise ValueError(
            f"content_kind {facts.content_kind!r} is not one of {', '.join(CONTENT_KINDS)}"
        )

    if override is not None:
        checked = normalize_override(override.surface, override.classification, override.reason)
        return CoverageDecision(
            surface=checked.surface,
            secondary_surfaces=(),
            classification=checked.classification,
            teaching_status=TEACHING_STATUS_BY_CLASSIFICATION[checked.classification],
            reason=checked.reason,
            rule_id="override:exact-path",
        )

    name = _basename(path).lower()
    if _is_sensitive_environment(name):
        return CoverageDecision(
            surface="environment",
            secondary_surfaces=(),
            classification="IGNORED_WITH_REASON",
            teaching_status=TEACHING_STATUS_BY_CLASSIFICATION["IGNORED_WITH_REASON"],
            reason=_REASON_TEXT["safety:tracked-env"],
            rule_id="safety:tracked-env",
        )

    matches = _iter_matches(path)
    if not matches:
        return CoverageDecision(
            surface="other",
            secondary_surfaces=(),
            classification="UNKNOWN",
            teaching_status=TEACHING_STATUS_BY_CLASSIFICATION["UNKNOWN"],
            reason=_UNKNOWN_REASON,
            rule_id="fallback:unknown",
        )

    primary = matches[0]
    secondary = tuple(sorted({
        match.surface
        for match in matches[1:]
        if match.contributes_secondary and match.surface != primary.surface
    }))
    return CoverageDecision(
        surface=primary.surface,
        secondary_surfaces=secondary,
        classification=primary.classification,
        teaching_status=TEACHING_STATUS_BY_CLASSIFICATION[primary.classification],
        reason=primary.reason,
        rule_id=primary.rule_id,
    )
