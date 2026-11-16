# OmniMovement 1.1.17

Sprint in any direction in Borderlands 2, remember Walk/Sprint selection, and customize transitions, hip-fire behavior, reticle, accuracy and optional controller precision.

**Contents:** [Installation](#installation) · [Basic controls](#basic-controls) · [Settings and defaults](#settings-and-defaults) · [Gameplay notes](#gameplay-notes) · [Recovery and troubleshooting](#recovery-and-troubleshooting) · [Compatibility](#compatibility)

## Installation

1. Close Borderlands 2 before installing or replacing the native extension.
2. Back up `sdk_mods/OmniMovement/` and `sdk_mods/settings/OmniMovement.json`.
3. Extract the complete, matching **Player Win32 ZIP's** `OmniMovement/` folder into `Borderlands 2/sdk_mods/`, replacing the old folder. Remove duplicate installations; do not replace shared SDK DLLs.
4. Confirm `sdk_mods/OmniMovement/__init__.py`, start the game and enable OmniMovement in the SDK Mods menu.

Existing settings are preserved when updating. Saved `Vanilla (Native)` direction choices migrate to `Vanilla`; the saved `native_precision` option appears in the menu as **Movement Backend**.

To **roll back**, exit the game and restore the backed-up folder and settings. To **uninstall**, disable the mod, exit the game and remove its folder. OmniMovement does not modify game packages on disk.

## Basic controls

- **Sprint Assist** makes the usual Sprint action toggle remembered Walk/Sprint selection. **Starting Mode** determines the initial selection.
- **Sprint While Hip-Firing** lets an existing sprint continue through hip-fire. **Begin Sprint While Firing** also allows starting sprint while firing; disable and re-enable the mod after changing it. Both settings must be On.
- **Coyote Time** allows one normal Jump action briefly after a supported ledge departure. It does not add a second jump or queue a jump for landing.

The game still controls sprint, crouch, ADS, skill, weapon, vehicle and airborne restrictions. See the [sprint-firing guide](https://github.com/PeytonBlakeslee/OmniMovement/blob/main/docs/SPRINT_FIRING.md) for a focused check.

## Settings and defaults

| Option | Default | Purpose |
| --- | --- | --- |
| Omnidirectional Sprint | On | Sprint in any horizontal direction. |
| Sprint Assist | On | Remember the selected Walk/Sprint mode. |
| Starting Mode | Sprint | Initial selection on enable or a new pawn. |
| Direction Changes | Vanilla | Vanilla, Responsive or Near-instant direction transitions. |
| Center Gap Window (ms) | 100 ms | Keep sprint during brief movement-input center crossings. |
| Coyote Time | On | One normal jump just after a supported ledge departure. |
| Coyote Time Window (ms) | 200 ms | Grace window from 0 to 1,000 ms; 0 disables it. |
| Sprint While Hip-Firing | On | Continue sprint when hip-fire begins. |
| Begin Sprint While Firing | On | Also begin sprint while firing; re-enable mod to apply. |
| Crosshair While Sprinting | On | Show the native weapon reticle. |
| Walking Accuracy While Sprinting | On | Use walking accuracy behavior during sprint. |
| Precision Sprint | On | Experimental partial-stick speed control. |
| Precision Strength (%) | 70% | Partial-stick slowdown with Precision Sprint On. |
| Movement Backend | Python | Try the optional experimental C++ backend for potentially better performance; Python is the default. |

## Settings guide

### Sprint & Movement

- **Omnidirectional Sprint:** Allows native sprint in any horizontal direction. Turning it off restores the game's ordinary direction restrictions.
- **Sprint Assist:** Uses the normal Sprint action to toggle a remembered Sprint or Walk mode. **Starting Mode** chooses the initial mode when the mod is enabled or a new pawn is created.
- **Direction Changes:** Vanilla uses the game's normal momentum. Responsive applies a short transition on eligible opposing sprint inputs; Near-instant redirects more quickly on eligible reversals. Action, airborne, knockback and other safety restrictions can prevent a correction.
- **Center Gap Window:** How long sprint can remain active while movement input is neutral, such as a stick crossing center or the gap between keyboard presses. A larger value can help brief direction changes but can also keep sprint active longer after input stops. It does not guarantee instant acceleration.

### Jumping

- **Coyote Time:** Permits one normal jump shortly after walking off a supported ledge. **Coyote Time Window** sets that grace period; 0 ms disables the window. It does not add a second jump.

### Combat

- **Sprint While Hip-Firing:** Keeps an existing sprint going when hip-fire begins. **Begin Sprint While Firing** also allows starting sprint while already firing. Both must be on for the latter; disable and re-enable the mod after changing it.
- **Walking Accuracy While Sprinting:** Uses the weapon's walking accuracy behavior during sprint.
- **Crosshair While Sprinting:** Shows the normal weapon crosshair during sprint. ADS and other native reticle restrictions still apply.

### Controller & Backend

- **Precision Sprint:** Scales the sprint speed setting for partial controller-stick input. Full-stick speed is unchanged. **Precision Strength** controls the amount of slowdown; it does not change your deadzone or guarantee a particular acceleration or stopping distance. Keyboard input does not receive the same partial-stick scaling.
- **Movement Backend:** Python is the default. Try C++ for potentially better performance; its benefit depends on the setup and is not guaranteed. C++ uses the included native extension for supported movement and Sprint Assist processing. C++ does not require Precision Sprint. Recoverable loading or backend failures can fall back to Python, but native process failures can occur before fallback. Keep the extension matched to the mod and its supported SDK/runtime.

Defaults apply to new or missing options; your existing saved choices are preserved when updating.

## Gameplay notes

- **Character skills:** Sprint Assist respects native action restrictions for Zer0, Maya, Salvador, Krieg, Axton and Gaige. Zer0's Decepti0n/Execute recovery does not override dash or strike timing; a short post-Execute pause was also observed with OmniMovement disabled.
- **Rapid crouch:** Crouch takes priority, with a 220 ms auto-resprint hold after the final crouch edge. Sideways/backward crouch-spam movement was observed working during limited Windows BL2 testing.
- **Salvador:** Sprint-firing retains the reviewed normal and Gunzerking first-person weapon-pose paths.
- **Coyote Time:** Only normal on-foot, locally authorized ledge departures qualify. Menus, downing, vehicles, scripted launches and unsupported skill contexts invalidate the window; clients in co-op retain vanilla jumping.
- **Travel:** Recovery after loading has a bounded budget of 24 eligible gameplay seconds, capped at 120 overall seconds. Repeated arrival events do not reset it.

The maintainer subsequently reported a limited Windows BL2 gameplay smoke test of version 1.1.17 with the six mods installed, and the corresponding Player ZIP passed CI and package inspection. This does not verify every setting, a clean install, co-op, Proton/Wine or other configurations.

## Recovery and troubleshooting

- **Unexpected movement or a stuck weapon pose:** Stop firing, disable the mod and allow recovery to finish if possible. For a native script restoration warning, fully exit BL2 before restarting, updating or reinstalling.
- **C++ backend unavailable or malfunctioning:** Select **Python** under **Movement Backend** and inspect `Binaries/Win32/Plugins/unrealsdk.log`. Recoverable backend failures may fall back to Python; native process failures may crash before fallback.
- **Options or behavior changed after updating:** Check the active mode and restore your saved settings backup if needed. Keep the native `.pyd` matched to the Player ZIP's Python files.

## Compatibility

**Windows x86 Borderlands 2** is the primary package target; limited gameplay smoke acceptance was reported on the maintainer's existing installation. A separate clean-install test has not been performed. The Pre-Sequel is unsupported. Co-op host/client, controller reconnect and Proton/Wine remain unverified; native Linux BL2 is unsupported by this Win32 package.

Other input/movement mods have received source-level interaction review, not certification of every combination. OmniMovement does not alter global Steam Input, sensitivity or deadzone preferences.

[Compatibility and outstanding checks](https://github.com/PeytonBlakeslee/OmniMovement/blob/main/docs/COMPATIBILITY.md) · [Report an issue](https://github.com/PeytonBlakeslee/OmniMovement/issues) with game, SDK and mod versions, enabled settings, solo/host/client role, reproduction steps and a relevant redacted SDK log.
