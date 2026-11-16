"""Repack the CI-built and PE-audited Win32 SDK mod into an install-only ZIP.

This never builds or substitutes a native backend. The branch's existing
package_release.py builds and verifies the matching .pyd first.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "tools/release-config.json").read_text(encoding="utf-8"))
FOLDER = CONFIG["folder"]
MODULE = CONFIG["module"]
DEVELOPER_MODULES = {
    "_fpstester_bridge.py", "_fpstester_native_upgrade.py",
    "profiler.py", "profiling.py", "native_capture.py",
}
ARCHIVE_DATE = (2020, 1, 1, 0, 0, 0)
# Exact paths admitted in an installable Player archive.
PLAYER_INSTALL_FILES = frozenset({
    '__init__.py',
    '_settings_migration.py',
    'coyote.py',
    'fire_pose.py',
    'interruptions.py',
    'native_hipfire.py',
    'precision.py',
    'precision_native_adapter.py',
    'pyproject.toml',
    'recovery_budget.py',
    'reversal.py',
    'travel_input.py',
    'README.md',
    'LICENSE-NOTICE.txt',
    'LICENSE',
    'THIRD_PARTY_NOTICES.md',
    'omni_precision_backend.pyd',
    'licenses/GPL-3.0.txt',
    'licenses/UnrealSDK-LGPL-3.0.txt',
    'licenses/pybind11-BSD.txt',
    'licenses/pyunrealsdk-LGPL-3.0.txt',
})


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--flavor", choices=("player", "developer"), required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--private-candidate", action="store_true")
    args = parser.parse_args()

    branch = "main" if args.flavor == "player" else "testing"
    actual_branch = os.environ.get("GITHUB_REF_NAME") or subprocess.check_output(
        ["git", "branch", "--show-current"], cwd=ROOT, text=True
    ).strip()
    if args.private_candidate:
        if args.flavor != "player" or actual_branch != "release-candidate":
            raise ValueError("Private candidate is limited to the release-candidate Player branch")
        if subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip():
            raise ValueError("Private candidate requires a clean source checkout")
        branch = actual_branch
    elif actual_branch != branch:
        raise ValueError(f"{args.flavor} ZIP must be built on {branch}; got {actual_branch!r}")
    commit = os.environ.get("GITHUB_SHA") or subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Missing full source commit SHA")

    version = tomllib.loads(
        (ROOT / "mod" / FOLDER / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]["version"]
    if not re.fullmatch(r"[0-9][0-9A-Za-z._+-]*", version):
        raise ValueError(f"Invalid mod version: {version!r}")
    if any(word in version.lower() for word in ("debug", "profiler", "testing")):
        raise ValueError("Installer filename must not use a developer-only suffix")

    base = args.output / f"{CONFIG['product']}-v{version}-Win32.zip"
    if not base.is_file():
        raise FileNotFoundError(f"First build the audited native release ZIP: {base}")

    files: dict[str, bytes] = {}
    with zipfile.ZipFile(base) as zf:
        names = zf.namelist()
        if zf.testzip() is not None or len(names) != len(set(names)):
            raise ValueError("Native package ZIP failed integrity or duplicate-entry check")
        # The native base package contains regular files only. Validate names
        # using ZIP's POSIX separator on every host; Windows also interprets
        # backslashes and drive prefixes, which Path on Linux can overlook.
        for info in zf.infolist():
            name = info.filename
            segments = name.split("/")
            if (not name or name.endswith("/") or name.startswith("/")
                    or "\\" in name or ":" in name or "\x00" in name
                    or len(segments) < 2 or segments[0] != FOLDER
                    or any(part in ("", ".", "..") for part in segments)
                    or ((info.external_attr >> 16) & 0o170000) == 0o120000):
                raise ValueError(f"Unsafe native source ZIP entry: {name!r}")
            path = Path(name)
            if "validation" in path.parts or path.name == "VALIDATION.md":
                raise ValueError(f'Validation evidence in install ZIP: {name}')
            if path.name.endswith((".pyc", ".pyo")) or "__pycache__" in path.parts:
                raise ValueError(f"Compiled Python cache in install package: {name}")
            files[name] = zf.read(name)

    def require(name: str) -> bytes:
        full = f"{FOLDER}/{name}"
        if full not in files:
            raise ValueError(f"Required runtime file missing: {full}")
        return files[full]

    require("__init__.py")
    require("pyproject.toml")
    require("README.md")
    require("LICENSE")
    require("THIRD_PARTY_NOTICES.md")
    pyd = require(MODULE + ".pyd")
    # Build identity lives outside the install directory and is checked here.
    provenance = json.loads((args.output / "BUILD_INFO.json").read_text(encoding="utf-8"))
    if provenance.get("binary_sha256") != digest(pyd):
        raise ValueError("The packaged .pyd does not match the native build hash")
    if provenance.get("native_backend") != CONFIG["backend_version"]:
        raise ValueError("Native backend version does not match its branch")
    if provenance.get("source_commit") != commit:
        raise ValueError("Binary/source provenance does not match this commit")
    if provenance.get("architecture") != "PE i386" or provenance.get("python") != "3.14.3 win32":
        raise ValueError("Unexpected native architecture or Python ABI")

    bridge = f"{FOLDER}/_fpstester_bridge.py"
    imports_bridge = b"_fpstester_bridge" in require("__init__.py")
    if args.flavor == "player":
        expected_files = {f"{FOLDER}/{relative}" for relative in PLAYER_INSTALL_FILES}
        if set(files) != expected_files:
            missing = sorted(expected_files - set(files))
            unexpected = sorted(set(files) - expected_files)
            raise ValueError(f"Player file catalog mismatch: missing={missing}, unexpected={unexpected}")
        prohibited = [name for name in files if Path(name).name in DEVELOPER_MODULES
                      or name.startswith(f"{FOLDER}/validation/")]
        forbidden = (
            b"_fpstester", b"HookProfiler", b"NativeCapture(",
            b"_pause_trace", b"_trace_crosshair",
            b"def start_profiler_session", b"def snapshot_profiler",
            b'identifier="debug"', b'identifier="auto_capture"',
            b"_debug(", b"_sprint_fire_entry_counts", b"_pose_context_notice",
            b"native Precision stats at reset", b".stats()",
        )
        leaked = [(name, token.decode("ascii")) for name, data in files.items()
                  if name.endswith(".py") for token in forbidden if token in data]
        if prohibited or leaked:
            raise ValueError(
                f"Player ZIP contains developer instrumentation: files={prohibited}, markers={leaked}")
    elif bridge in files:
        # Developer profiling is optional; a bundled bridge must be wired.
        if not imports_bridge:
            raise ValueError("Developer FPS Tester bridge is not wired into the mod")
        docs = ROOT / "docs" / "DEVELOPER_TESTING.md"
        if not docs.is_file():
            raise ValueError("Developer testing instructions missing")
        files[f"{FOLDER}/DEVELOPER_TESTING.md"] = docs.read_bytes()
    elif imports_bridge:
        raise ValueError("Developer runtime references a missing FPS Tester bridge")

    metadata = {
        "mod": FOLDER,
        "repository": os.environ.get("GITHUB_REPOSITORY", ""),
        "workflow_file": ".github/workflows/build-win32.yml",
        "workflow_run": provenance.get("workflow_run"),
        "version": version,
        "flavor": args.flavor,
        "branch": branch,
        "source_commit": commit,
        "supported_games": ["BL2"],
        "coop_scope": "Unknown; co-op movement interactions unverified",
        "dependencies": {"required": ["PythonSDK", "Willow2 Mod Manager"],
                         "optional": ["Win32 native input/movement backend"]},
        "native_fallback": "Python movement processing is available for recoverable native failures",
        "native_build": {"pyunrealsdk_commit": provenance.get("pyunrealsdk_commit"),
                         "unrealsdk_commit": provenance.get("unrealsdk_commit"),
                         "workflow_run": provenance.get("workflow_run"),
                         "toolchain_target": "Visual Studio 2022 MSVC Win32 Release"},
        "native_imports": provenance.get("pe_audit", {}).get("imports", []),
        "native_module": MODULE + ".pyd",
        "native_sha256": digest(pyd),
        "native_backend": CONFIG["backend_version"],
        "developer_tool_files": (
            sorted(name for name in files
                   if Path(name).name in DEVELOPER_MODULES or "/_devtools/" in name)
            if args.flavor == "developer" else []
        ),
        "architecture": "Windows i386",
        "python_abi": "CPython 3.14 (win32)",
        "install_to": f"Borderlands 2/sdk_mods/{FOLDER}/",
        "game_acceptance": CONFIG["game_acceptance"],
        "published_release": False,
    }
    # Only developer packages retain inline diagnostic metadata.
    # Player provenance is in the .manifest.json and .sha256 beside the ZIP.
    if args.flavor == "developer":
        files[f"{FOLDER}/PACKAGE-INFO.json"] = (
            json.dumps(metadata, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")

    label = "Player" if args.flavor == "player" else "Developer"
    stem = f"{FOLDER}-v{version}-{label}-Win32"
    archive_path = args.output / (stem + ".zip")
    if archive_path.exists():
        raise FileExistsError(f"Refusing to overwrite package: {archive_path}")
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, ARCHIVE_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            zf.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    with zipfile.ZipFile(archive_path) as zf:
        if zf.testzip() is not None or sorted(zf.namelist()) != sorted(files):
            raise ValueError("Final install ZIP validation failed")
        if zf.read(f"{FOLDER}/{MODULE}.pyd") != pyd:
            raise ValueError("Native binary changed while packaging")

    archive_hash = digest(archive_path.read_bytes())
    manifest = {
        **metadata,
        "archive": archive_path.name,
        "archive_sha256": archive_hash,
        "files": [{"path": name, "sha256": digest(data), "bytes": len(data)}
                  for name, data in sorted(files.items())],
    }
    (args.output / (stem + ".manifest.json")).write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    (args.output / (stem + ".sha256")).write_text(
        f"{archive_hash}  {archive_path.name}\n", encoding="utf-8"
    )
    print(json.dumps({"result": "PASS", "package": archive_path.name,
                      "flavor": args.flavor, "native_sha256": digest(pyd),
                      "source_commit": commit, "file_count": len(files)}, indent=2))


if __name__ == "__main__":
    main()
