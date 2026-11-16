# OmniMovement: Compatibility

Version **1.1.17**. Exact-commit Windows Player CI and package inspection passed, and the maintainer reported a limited BL2 gameplay smoke test with the six mods installed. A separate clean-install test was not performed. These observations do not establish multiplayer or cross-platform support.

**Contents:** [Platform status](#platform-status) · [Feature limits and recovery](#feature-limits-and-recovery) · [Additional validation](#additional-validation) · [Reporting issues](#reporting-issues)

## Platform status

| Environment | Status |
| --- | --- |
| Windows Borderlands 2 | Primary Player target; limited gameplay smoke acceptance reported on an existing installation |
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

## Known SDK hook-ownership limitation

The pinned UnrealSDK hook API can return `false` after a successful insertion and does not expose callback identity. An adversarial exact-identifier race therefore cannot be proven safe solely from `has_hook`. OmniMovement uses unique per-instance 128-bit identifiers and per-attempt sequencing to minimize accidental collision risk, but that does not remove this API ambiguity. No in-game collision has been observed. The maintainer accepted the residual low-probability risk for the Windows BL2 single-player target; the adversarial case remains an explicit expected failure in CI, not a fixed bug. A future SDK callback-identity or atomic insertion-status mechanism would be needed for a complete fix.

## Additional validation

These are potential follow-up checks, **not claimed passing results**:

1. Clean-install the exact reviewed Player Win32 ZIP on a separate installation, and verify movement, direction transitions, sprint/crouch, reticle, Coyote Time and firing.
2. Repeat with travel, character skills, vehicles, controller reconnect, mixed input, reload and melee. Check disable/re-enable and native script restoration warnings.
3. Test a separate co-op host and client for movement authority, weapon poses, coyote behavior and any interactions with other installed mods.
4. Test Windows BL2 under Proton/Wine separately, including any matching native extension. Do not treat successful startup as complete support.

TPS is outside this release target.

## Reporting issues

[Open an issue](https://github.com/PeytonBlakeslee/OmniMovement/issues) with game/SDK/mod versions, relevant options, operating system and Proton version if applicable, solo/host/client role, steps to reproduce, and short redacted log excerpts. Do not include private account paths, credentials or save data.
