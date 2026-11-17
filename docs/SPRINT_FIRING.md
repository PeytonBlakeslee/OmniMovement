# Sprint-firing: Player Guide

Applies to **OmniMovement v1.1.17** on Windows Borderlands 2. Get the matching Player Win32 ZIP from the [Releases page](https://github.com/PeytonBlakeslee/OmniMovement/releases) when available; GitHub-generated source archives are not installable Player packages.

**Contents:** [Setup](#setup) · [Sprint-firing options](#sprint-firing-options) · [Quick in-game check](#quick-in-game-check) · [Recovery and rollback](#recovery-and-rollback)

## Setup

Install a complete matching Player Win32 ZIP as described in the [player guide](../mod/OmniMovement/README.md#installation). Do not mix Python files and a native extension from different builds. Back up the mod folder and saved settings before updating.

## Sprint-firing options

**Sprint While Hip-Firing** is On by default. **Begin Sprint While Firing** is On by default and requires the first option.

| Begin Sprint While Firing | Expected behavior |
| --- | --- |
| Off | Continue an existing sprint when firing begins; new sprint initiation during firing stays normally restricted. |
| On | Also begin sprint during hip-fire where native movement, action and weapon restrictions permit it. |

Changing **Begin Sprint While Firing** saves the preference, but the runtime native patch applies only after disabling and re-enabling OmniMovement (or restarting the game). Avoid changing it while firing. This feature adjusts live game-script memory, not game INI files or assets.

## Quick in-game check

1. **Sprint, then fire:** Existing sprint continues while the weapon fires.
2. **Fire, then sprint:** With the option Off, a new sprint stays normally blocked; with it On, sprint can begin while firing under eligible conditions.
3. **Fire, sprint, release and repress Fire:** No stuck pose or lost fire input.
4. **Jump, land, reload, ADS and switch weapons:** Normal restrictions and animation recovery remain in effect.

Try an automatic weapon and a semi-automatic weapon or shotgun with the same character and settings.

## Recovery and rollback

If sprinting, firing or first-person animations become stuck, stop firing and disable the feature. If a native-script restoration warning appears, fully exit BL2 before restarting or reinstalling. Restore the matching backed-up folder and settings if needed.

Co-op and Proton/Wine behavior remains unverified. See [compatibility](COMPATIBILITY.md) and [player troubleshooting](../mod/OmniMovement/README.md#recovery-and-troubleshooting).
