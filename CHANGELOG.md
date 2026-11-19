# Changelog

## 1.2.0

- Fixed an issue where swapping or dropping a weapon interrupted sprint. Sprint Assist can also start while swaps are in progress.
- Fixed an issue where swapping during Axton’s turret deployment, Zer0’s Decepti0n, Maya’s Phaselock, or the Doppelganger’s active summon could interrupt sprint.
- Fixed an issue where activating Gunzerk could stall movement, especially on backward diagonals with a controller or after holding forward through spawn.
- Fixed an issue where releasing left-hand fire during Gunzerk could end sprint despite continued movement input.
- Fixed an issue where the right gun’s reload could force walking while firing the left gun, and where either gun’s reload could leave the firing gun in its sprint pose.
- Fixed an issue where firing through a swap could leave the incoming weapon in its sprint pose. During Gunzerk, a ready gun can now use its firing pose while the other gun finishes equipping.
- Fixed an issue where holding fire through a weapon swap did not resume firing the main-hand weapon, including when Firing Fix is disabled.
- Fixed an issue where holding fire through a melee attack could leave the weapon in its sprint animation while shooting.
- Fixed an issue where the left gun could fire by itself after a swap or ignore a held trigger until it was pressed again. Applies when hosting locally in Offline, LAN, or online mode.
- Fixed an issue where swapping mid-jump, or firing the left gun in a Gunzerk jump, could reset sprint FOV.
- Fixed an issue where sprint reticles could disappear or show the wrong weapon after a menu, Gunzerk activation, or weapon swap.
