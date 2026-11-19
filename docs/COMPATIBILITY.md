# OmniMovement: Compatibility

Version **1.2.0**. Windows BL2 has development gameplay observations, but the final versioned Player ZIP still needs separate clean-install acceptance. Source checks do not establish multiplayer or cross-platform support.

The gameplay candidate was tested on Steam BL2 build 9218157 (executable version 1.0.257.2863302), UnrealSDK 3.2.0 (b1852aa4), pyunrealsdk 1.10.0 (c72c5558) and Python 3.14.6 on Windows. The build ID is from the local Steam manifest; the SDK and Python versions are from the game log. These describe the tested installation, not an exclusive support range or a guarantee for later updates. The release build pins the SDK source revisions and Python headers documented in [BUILDING.md](../BUILDING.md).

**Contents:** [Platform status](#platform-status) · [Feature limits and recovery](#feature-limits-and-recovery) · [Uncompleted acceptance checks](#uncompleted-acceptance-checks) · [Reporting issues](#reporting-issues)

## Platform status

| Environment | Status |
| --- | --- |
| Windows Borderlands 2 | Primary Player target; final packaged-install gameplay acceptance pending |
| Borderlands: The Pre-Sequel | Unsupported by this release |
| Co-op host/client | Not certified; movement authority, prediction, recovery and script changes require testing |
| Windows BL2 under Proton/Wine | Unverified, including Win32 native backend and script changes |
| Native Linux Borderlands 2 | Unsupported by the current Win32 package |

A local reticle or input setting does not establish that movement and native-script changes are safe in co-op.

## Feature limits and recovery

- **Coyote Time** applies only to observed, eligible on-foot ledge departures with local authority. Co-op clients retain vanilla jumping; server prediction/replication is not implemented.
- **Begin Sprint While Firing** is On by default, requires **Sprint While Hip-Firing**, and changes live native-script state when enabled. Disable/re-enable OmniMovement to apply a setting change. If script restoration warns or weapon input remains stuck, fully exit the game before restarting.
- **Movement Backend** defaults to Python. The experimental C++ backend can fall back after recoverable failures, but a native process failure may prevent fallback.
- Native skill, ADS, airborne, crouch, vehicle and weapon restrictions still apply. Travel recovery is bounded, not indefinite.

## Uncompleted acceptance checks

These are planned checks, **not passing results**:

1. Clean-install the exact reviewed Player Win32 ZIP; verify movement, direction transitions, sprint/crouch, reticle, Coyote Time and firing before release.
2. Repeat with travel, character skills, vehicles, controller reconnect, mixed input, reload and melee. Check disable/re-enable and native script restoration warnings.
3. Test a separate co-op host and client for movement authority, weapon poses, coyote behavior and any interactions with other installed mods.
4. Test Windows BL2 under Proton/Wine separately, including any matching native extension. Do not treat successful startup as complete support.

TPS is outside this release target.

## Reporting issues

[Open an issue](https://github.com/PeytonBlakeslee/OmniMovement/issues) with game/SDK/mod versions, relevant options, operating system and Proton version if applicable, solo/host/client role, steps to reproduce, and short redacted log excerpts. Do not include private account paths, credentials or save data.
