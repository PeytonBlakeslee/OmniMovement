# Build OmniMovement

Use Windows x86 Borderlands 2, Visual Studio 2022 C++ tools and Python 3.14.
The workflow pins pyunrealsdk `c72c5558473bfa1f1cbc9c9113efb2a02251e656`,
UnrealSDK `b1852aa440277afcce8a13566b5487aeae5a7ae9`, and hash-verified Python
3.14.3 Win32 headers. Runtime Python 3.14.6 uses that patch-line ABI.

Use the exact fetch/configure commands in `.github/workflows/build-win32.yml`;
the SDK is fetched into `native/pyunrealsdk/` and is not committed. Configure
`native/` into `build/native/`, then compile target `omni_precision_backend`. No other mod
repository is required and no shared SDK DLLs are installed by this workflow.

Frontend command: `python -B -m unittest discover -s tests -p test_*bridge.py -v`.
Native test configuration: `cmake -S . -B build/core -A Win32` with the VS 2022 generator;
build Release and run CTest with `-C Release --output-on-failure`. Assertions stay
enabled. Tests do not replace a game acceptance run.

From a clean committed checkout, run `python -B tools/package_release.py --native <path-to-omni_precision_backend.pyd>`.
This produces an audited native/source intermediate archive and `dist/BUILD_INFO.json`
outside the install folder. Then run `python -B tools/package_native_install.py --flavor player`
on `main`, or use `--flavor player --private-candidate` on a clean, reviewed
`release-candidate` branch. The packager requires a clean source tree;
GitHub branch protection is separate and is not currently enabled. The final Player
ZIP contains only
allowlisted runtime files, required notices/licenses and the matching native extension;
`.manifest.json` and `.sha256` are saved beside the final ZIP. PE machine, initializer, runtime imports and native API
markers are checked. `package_release.py` accepts `--output`, `--repository`,
`--run-id`, and `--source-commit`; `package_native_install.py` accepts
`--output`. Previous output is never recursively deleted or overwritten.

Historical release evidence, including earlier commit and build IDs, is retained in the private development repository and is not included in this production snapshot. It does not represent new gameplay results. Original project source is MIT-licensed.
