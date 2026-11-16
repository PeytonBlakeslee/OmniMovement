// Native Precision Sprint backend for BL2 / UnrealSDK 3.2.
//
// Invoked from existing LOCAL Python movement and recovery hooks. Python
// validates its pc/pi wrappers and passes short-lived live addresses on the
// game thread. Never call update() from a worker thread or with saved pointers.
//
// All persistent pawn ownership is held via UnrealSDK WeakPointer, never
// a naked game UObject*. No extra native hook is installed.
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cmath>
#include <mutex>
#include <exception>
#include <stdexcept>
#include <string>
#include <utility>
#include <pybind11/pybind11.h>
#include "omni_native_core.hpp"
#include "velocity_write_transaction.hpp"
#include "unrealsdk/unreal/classes/uobject_funcs.h"
#include "unrealsdk/unreal/classes/uclass.h"
#include "unrealsdk/unreal/properties/copyable_property.h"
#include "unrealsdk/unreal/properties/attribute_property.h"
#include "unrealsdk/unreal/properties/zboolproperty.h"
#include "unrealsdk/unreal/properties/zbyteproperty.h"
#include "unrealsdk/unreal/properties/zobjectproperty.h"
#include "unrealsdk/unreal/properties/zstructproperty.h"
#include "unrealsdk/unreal/wrappers/weak_pointer.h"
#include "unrealsdk/unreal/structs/fname.h"

namespace py = pybind11;
namespace u = unrealsdk::unreal;

namespace {
struct MotionContextError : std::runtime_error { using std::runtime_error::runtime_error; };
// Cache only type-validated property metadata, never live gameplay objects.
// Revalidate on concrete class changes (including travel/respawn).
template <typename T>
struct CachedProperty {
    const u::UStruct* owner_class = nullptr;
    const T* property = nullptr;
    const T* find(u::UObject* obj, const u::FName& name) {
        const auto* cls = obj->Class();
        if (cls != owner_class || property == nullptr) {
            const T* checked = cls->find_prop_and_validate<T>(name);
            owner_class = cls;
            property = checked;
        }
        return property;
    }
    void clear() noexcept { owner_class = nullptr; property = nullptr; }
};

class GameSpeedIO final : public omni::SpeedAccess {
public:
    u::WeakPointer pawn{};
    std::uintptr_t pawn_address = 0;
    std::uint64_t read_failures = 0;
    std::uint64_t write_failures = 0;
    CachedProperty<u::ZFloatProperty> speed_property{};

    void capture(u::UObject* ptr) {
        pawn = u::WeakPointer{ptr};
        pawn_address = reinterpret_cast<std::uintptr_t>(ptr);
    }
    void clear() {
        pawn = u::WeakPointer{};
        pawn_address = 0;
    }
    u::UObject* resolve(std::uintptr_t addr) {
        if (addr == 0 || addr != pawn_address) return nullptr;
        auto* obj = *pawn;
        if (reinterpret_cast<std::uintptr_t>(obj) != addr) return nullptr;
        return obj;
    }
    bool read_sprinting_pct(std::uintptr_t addr, double& out) override {
        try {
            auto* obj = resolve(addr);
            if (obj == nullptr) { ++read_failures; return false; }
            static const u::FName PROP{L"SprintingPct"};
            const double read = obj->get<u::ZFloatProperty>(speed_property.find(obj, PROP));
            if (!std::isfinite(read)) { ++read_failures; return false; }
            out = read;
            return true;
        } catch (...) {
            ++read_failures;
            return false;
        }
    }
    bool write_sprinting_pct(std::uintptr_t addr, double value) override {
        try {
            auto* obj = resolve(addr);
            if (obj == nullptr || !std::isfinite(value)) { ++write_failures; return false; }
            static const u::FName PROP{L"SprintingPct"};
            obj->set<u::ZFloatProperty>(speed_property.find(obj, PROP), static_cast<float>(value));
            return true;
        } catch (...) {
            ++write_failures;
            return false;
        }
    }
};

struct NativePrecision {
    std::mutex mutex;
    omni::PrecisionSprint state{};
    GameSpeedIO io{};
    CachedProperty<u::ZBoolProperty> c_gamepad{};
    CachedProperty<u::ZBoolProperty> c_pc_sprinting{};
    CachedProperty<u::ZBoolProperty> c_retry_melee{};
    CachedProperty<u::ZBoolProperty> c_retry_shared{};
    CachedProperty<u::ZBoolProperty> c_retry_grenade{};
    CachedProperty<u::ZBoolProperty> c_retry_pending_zoom{};
    CachedProperty<u::ZObjectProperty> c_combat_input{}, c_combat_controller{};
    CachedProperty<u::ZObjectProperty> c_combat_weapon{}, c_combat_offhand{};
    CachedProperty<u::ZBoolProperty> c_combat_putdown{}, c_combat_firing{};
    CachedProperty<u::ZIntProperty> c_combat_reload{};
    CachedProperty<u::ZByteProperty> c_combat_zoom{};
    std::uint64_t travel_epoch = 0;
    int travel_valid_frames = 0;
    int travel_close_frames = 0;
    double travel_last_retry_at = -1.0;
    void travel_reset_locked() noexcept {
        travel_epoch = 0;
        travel_valid_frames = 0;
        travel_close_frames = 0;
        travel_last_retry_at = -1.0;
    }
    // Native Sprint Assist observer without engine-property write authority.
    // Controller reflection and debounce share one Python/native bridge.
    // Status: 0 already sprinting; 1 idle; 2 known blocker;
    // 3 wait for second ready frame; 4 candidate (Python must approve);
    // -1 reflection/unavailable -> old Python path.
    CachedProperty<u::ZObjectProperty> c_pawn{};
    CachedProperty<u::ZIntAttributeProperty> c_pawn_sprinting{};
    CachedProperty<u::ZByteProperty> c_physics{};
    CachedProperty<u::ZBoolProperty> c_crouched{};
    CachedProperty<u::ZBoolProperty> c_cinematic{};
    CachedProperty<u::ZByteProperty> c_ignore_move{};
    CachedProperty<u::ZFloatProperty> c_raw_right{};
    CachedProperty<u::ZFloatProperty> c_raw_up{};
    CachedProperty<u::ZFloatProperty> c_gate_forward{};
    CachedProperty<u::ZFloatProperty> c_gate_side{};
    CachedProperty<u::ZStructProperty> c_velocity{};
    CachedProperty<u::ZBoolProperty> c_wants_crouch{}, c_jump{};
    // Engine.PlayerController.bDuck is an input byte, unlike bPressedJump.
    CachedProperty<u::ZByteProperty> c_duck{};
    CachedProperty<u::ZBoolProperty> c_force_rm{}, c_force_regular{}, c_rm_stop{};
    CachedProperty<u::ZFloatAttributeProperty> c_ground_speed{};
    CachedProperty<u::ZFloatProperty> c_max_desired{};
    omni::MotionGeneration motion_generation{};
    u::WeakPointer motion_pc{}, motion_pi{}, motion_pawn{};
    struct MotionSnapshot {
        // Borrowed for one callback only. Never stored in NativePrecision.
        u::UObject* pc = nullptr;
        u::UObject* pi = nullptr;
        u::UObject* pawn = nullptr;
        omni::MotionContext context{};
        bool sprinting = false;
    };
    omni::SprintGate sprint_gate{};
    omni::CenterInputObserver center_input{};
    omni::AssistRecoveryReadiness assist_recovery{};
    omni::SprintIntent sprint_intent{};
    omni::ReversalObserver reversal_observer{};
    omni::ReversalCorrection reversal_correction{};
    void invalidate_cache() noexcept {
        c_gamepad.clear(); c_pc_sprinting.clear(); c_pawn.clear();
        c_retry_melee.clear(); c_retry_shared.clear();
        c_retry_grenade.clear(); c_retry_pending_zoom.clear();
        c_combat_input.clear(); c_combat_controller.clear();
        c_combat_weapon.clear(); c_combat_offhand.clear();
        c_combat_putdown.clear(); c_combat_firing.clear();
        c_combat_reload.clear(); c_combat_zoom.clear();
        c_pawn_sprinting.clear(); c_physics.clear(); c_crouched.clear();
        c_cinematic.clear(); c_ignore_move.clear();
        c_raw_right.clear(); c_raw_up.clear();
        c_gate_forward.clear(); c_gate_side.clear(); c_velocity.clear();
        c_wants_crouch.clear(); c_duck.clear(); c_jump.clear();
        c_force_rm.clear(); c_force_regular.clear(); c_rm_stop.clear();
        c_ground_speed.clear(); c_max_desired.clear();
        io.speed_property.clear();
    }
    bool quarantined = false;


    void release_speed_handle_if_safe_locked() noexcept {
        if (state.active_owner() != 0) {
            try {
                if (io.resolve(state.active_owner()) == nullptr) state.abandon();
            } catch (...) { return; }
        }
        if (state.active_owner() == 0) io.clear();
    }
    void restore_locked() noexcept {
        state.restore(io);
        release_speed_handle_if_safe_locked();
    }
    void reset_locked() noexcept {
        state.reset(io);
        release_speed_handle_if_safe_locked();
        quarantined = state.active_owner() != 0;
        sprint_gate.reset();
        center_input.reset();
        assist_recovery.reset();
        sprint_intent.reset();
        travel_reset_locked();
        reversal_observer.reset();
        reversal_correction.reset();
        motion_generation.reset();
        motion_pc = motion_pi = motion_pawn = u::WeakPointer{};
        invalidate_cache();
    }

    MotionSnapshot read_motion(std::uintptr_t pc_address, std::uintptr_t pi_address,
                               std::uint64_t epoch) {
        if (!pc_address || !pi_address)
            throw MotionContextError("missing motion controller/input");
        MotionSnapshot snapshot{};
        snapshot.pc = reinterpret_cast<u::UObject*>(pc_address);
        snapshot.pi = reinterpret_cast<u::UObject*>(pi_address);
        static const u::FName INPUT{L"PlayerInput"}, PAWN{L"Pawn"}, CONTROLLER{L"Controller"};
        if (snapshot.pc->get<u::ZObjectProperty>(c_combat_input.find(snapshot.pc, INPUT)) != snapshot.pi)
            throw MotionContextError("motion input generation mismatch");
        snapshot.pawn = snapshot.pc->get<u::ZObjectProperty>(c_pawn.find(snapshot.pc, PAWN));
        if (!snapshot.pawn || snapshot.pawn->get<u::ZObjectProperty>(
                c_combat_controller.find(snapshot.pawn, CONTROLLER)) != snapshot.pc)
            throw MotionContextError("motion pawn generation mismatch");
        static const u::FName PHYSICS{L"Physics"}, GAMEPAD{L"bUsingGamepad"}, SPRINT{L"bInSprintState"};
        snapshot.context = {{pc_address, pi_address,
                            reinterpret_cast<std::uintptr_t>(snapshot.pawn), epoch},
            static_cast<int>(snapshot.pawn->get<u::ZByteProperty>(c_physics.find(snapshot.pawn, PHYSICS))),
            snapshot.pi->get<u::ZBoolProperty>(c_gamepad.find(snapshot.pi, GAMEPAD))};
        snapshot.sprinting = snapshot.pc->get<u::ZBoolProperty>(c_pc_sprinting.find(snapshot.pc, SPRINT));
        return snapshot;
    }
    void reset_motion_locked() noexcept {
        sprint_gate.reset(); center_input.reset();
        reversal_observer.reset(); reversal_correction.reset();
        motion_generation.reset();
        motion_pc = motion_pi = motion_pawn = u::WeakPointer{};
    }
    void motion_reset() {
        std::scoped_lock lock{mutex};
        reset_motion_locked();
    }

    // Evaluate sprint input while retaining the production Python PRE hook.
    // This is intentionally separate from speed scaling: if input reflection
    // fails, the Python gate can take over without changing speed ownership.
    // The callback supplies only a short-lived, verified LOCAL PlayerInput
    // address; no gameplay UObject* is stored between invocations.
    py::tuple gate_update(std::uintptr_t pi_address, double center_grace,
                          bool center_enabled) {
        std::scoped_lock lock{mutex};
        try {
            if (pi_address == 0 || !std::isfinite(center_grace)) {
                throw std::invalid_argument("invalid local PlayerInput/grace");
            }
            auto* pi = reinterpret_cast<u::UObject*>(pi_address);
            static const u::FName FWD{L"aBaseY"};
            static const u::FName SIDE{L"aStrafe"};
            const double fwd = pi->get<u::ZFloatProperty>(c_gate_forward.find(pi, FWD));
            const double side = pi->get<u::ZFloatProperty>(c_gate_side.find(pi, SIDE));
            const bool moving = (fwd * fwd + side * side) > 0.0001;
            using Clock = std::chrono::steady_clock;
            const double now = std::chrono::duration<double>(
                Clock::now().time_since_epoch()).count();
            const bool allowed = sprint_gate.allows(
                fwd, side, now, std::max(0.0, center_grace));
            const auto crossing = center_input.observe(
                fwd, side, now, std::max(0.0, center_grace), center_enabled);
            return py::make_tuple(fwd, side, moving, allowed,
                                  crossing.entered, crossing.reversed);
        } catch (const std::exception&) {
            sprint_gate.reset();
            center_input.reset();
            throw;
        } catch (...) {
            sprint_gate.reset();
            throw;
        }
    }
    void gate_reset() {
        std::scoped_lock lock{mutex};
        sprint_gate.reset();
        center_input.reset();
    }

    // Travel-generation latch and bounded input retry schedule.
    // All inputs are validated by the local Python hook first. The epoch is a
    // monotonically increasing INTEGER, not an Unreal object pointer.
    // This method cannot grant sprint or write any engine property.
    py::tuple travel_step(std::uint64_t epoch, bool ready, bool modal_open,
                          bool close_pending, bool sprinting, bool armed,
                          double now) {
        std::scoped_lock lock{mutex};
        if (epoch == 0 || !std::isfinite(now)) {
            throw std::invalid_argument("invalid travel epoch or monotonic time");
        }
        if (epoch != travel_epoch) {
            travel_reset_locked();
            travel_epoch = epoch;
        }
        if (!ready || modal_open) {
            travel_valid_frames = 0;
            travel_close_frames = 0;
            return py::make_tuple(false, false, false);
        }
        if (travel_valid_frames < 3) ++travel_valid_frames;
        if (travel_valid_frames < 3) return py::make_tuple(false, false, false);
        bool close_settled = false;
        if (close_pending) {
            if (travel_close_frames < 6) ++travel_close_frames;
            if (travel_close_frames < 6)
                return py::make_tuple(false, false, false);
            if (travel_close_frames == 6) {
                // The Python callback drops close_pending immediately once
                // this response is processed. Reset retry timing only once.
                travel_last_retry_at = -1.0;
                travel_close_frames = 7;
            }
            close_settled = true;
        } else {
            travel_close_frames = 0;
        }
        if (!armed || sprinting) return py::make_tuple(true, false, close_settled);
        const bool due = travel_last_retry_at < 0.0 ||
                         now - travel_last_retry_at >= 0.25;
        if (due) {
            travel_last_retry_at = now;
        }
        return py::make_tuple(true, due, close_settled);
    }
    void travel_reset() {
        std::scoped_lock lock{mutex};
        travel_reset_locked();
    }

    // Optional early rejection of definite native sprint blockers.
    // Caller supplies an already-verified LOCAL controller address on the game
    // thread, never stores it; Python still owns CanSprint() and ALL permits.
    // Return 1 for blocked, 0 for inconclusive, -1 on reflection failure.
    int retry_preflight(std::uintptr_t pc_address) noexcept {
        std::scoped_lock lock{mutex};
        if (!pc_address || quarantined) return -1;
        try {
            auto* pc = reinterpret_cast<u::UObject*>(pc_address);
            static const u::FName MELEE{L"bPerformingMeleeAttack"};
            static const u::FName SHARED{L"bPerformingSharedWeaponAction"};
            static const u::FName GRENADE{L"bThrowingGrenade"};
            static const u::FName ZOOM{L"bPendingZoom"};
            const bool blocked =
                pc->get<u::ZBoolProperty>(c_retry_melee.find(pc, MELEE)) ||
                pc->get<u::ZBoolProperty>(c_retry_shared.find(pc, SHARED)) ||
                pc->get<u::ZBoolProperty>(c_retry_grenade.find(pc, GRENADE)) ||
                pc->get<u::ZBoolProperty>(c_retry_pending_zoom.find(pc, ZOOM));
            return blocked ? 1 : 0;
        } catch (...) {
            return -1;  // Never make a gameplay decision from failed reflection.
        }
    }

    // Take one type-checked snapshot of the native fields used
    // by Python Sprint Assist. NEVER grant sprint from this snapshot: weapon,
    // context, skill, animation and pawn.CanSprint stay authoritative in Python.
    // Controller and input pointers have LOCAL, callback-only lifetimes.
    // Tuple = (sprinting, action_blocked, pending_zoom, raw_moving).
    // None means Python must use its unchanged ordinary path.
    py::object assist_snapshot(std::uintptr_t pc_address,
                               std::uintptr_t pi_address) {
        std::scoped_lock lock{mutex};
        if (!pc_address || !pi_address || quarantined) return py::none();
        try {
            auto* pc = reinterpret_cast<u::UObject*>(pc_address);
            auto* pi = reinterpret_cast<u::UObject*>(pi_address);
            static const u::FName SPRINT{L"bInSprintState"};
            static const u::FName MELEE{L"bPerformingMeleeAttack"};
            static const u::FName SHARED{L"bPerformingSharedWeaponAction"};
            static const u::FName GRENADE{L"bThrowingGrenade"};
            static const u::FName ZOOM{L"bPendingZoom"};
            static const u::FName FORWARD{L"aBaseY"};
            static const u::FName SIDE{L"aStrafe"};
            const bool sprinting = pc->get<u::ZBoolProperty>(c_pc_sprinting.find(pc, SPRINT));
            const bool melee = pc->get<u::ZBoolProperty>(c_retry_melee.find(pc, MELEE));
            const bool shared = pc->get<u::ZBoolProperty>(c_retry_shared.find(pc, SHARED));
            const bool grenade = pc->get<u::ZBoolProperty>(c_retry_grenade.find(pc, GRENADE));
            const bool zoom = pc->get<u::ZBoolProperty>(c_retry_pending_zoom.find(pc, ZOOM));
            const double forward = pi->get<u::ZFloatProperty>(c_gate_forward.find(pi, FORWARD));
            const double side = pi->get<u::ZFloatProperty>(c_gate_side.find(pi, SIDE));
            const bool blocked = melee || shared || grenade;
            const bool moving = (forward * forward + side * side) > 0.0001;
            return py::make_tuple(sprinting, blocked, zoom, moving);
        } catch (...) {
            return py::none();
        }
    }

    // No ProcessEvent or Python callbacks under the native mutex. Read only
    // type-checked fields on the current verified controller/input/pawn trio.
    // Weapon functions (including mod overrides) remain authoritative in Python.
    omni::CombatFrame read_combat(std::uintptr_t pc_address,
                                 std::uintptr_t pi_address,
                                 bool recovery_only = false) {
        if (!pc_address || !pi_address || quarantined)
            throw std::runtime_error("unavailable combat context");
        auto* pc = reinterpret_cast<u::UObject*>(pc_address);
        auto* pi = reinterpret_cast<u::UObject*>(pi_address);
        static const u::FName INPUT{L"PlayerInput"}, PAWN{L"Pawn"};
        static const u::FName CONTROLLER{L"Controller"};
        if (pc->get<u::ZObjectProperty>(c_combat_input.find(pc, INPUT)) != pi)
            throw std::runtime_error("combat input generation mismatch");
        auto* pawn = pc->get<u::ZObjectProperty>(c_pawn.find(pc, PAWN));
        if (!pawn || pawn->get<u::ZObjectProperty>(
                c_combat_controller.find(pawn, CONTROLLER)) != pc)
            throw std::runtime_error("combat pawn generation mismatch");
        static const u::FName SPRINT{L"bInSprintState"}, MELEE{L"bPerformingMeleeAttack"};
        static const u::FName SHARED{L"bPerformingSharedWeaponAction"}, GRENADE{L"bThrowingGrenade"};
        static const u::FName ZOOM{L"bPendingZoom"}, FORWARD{L"aBaseY"}, SIDE{L"aStrafe"};
        static const u::FName WEAPON{L"Weapon"}, OFFHAND{L"OffHandWeapon"};
        static const u::FName PUTDOWN{L"bWeaponPutDown"}, WZOOM{L"ZoomState"};
        static const u::FName FIRING{L"bIsFiringWeapon"}, RELOAD{L"bReloading"};
        omni::CombatFrame frame{};
        frame.sprinting = pc->get<u::ZBoolProperty>(c_pc_sprinting.find(pc, SPRINT));
        frame.action_blocked =
            pc->get<u::ZBoolProperty>(c_retry_melee.find(pc, MELEE)) ||
            pc->get<u::ZBoolProperty>(c_retry_shared.find(pc, SHARED)) ||
            pc->get<u::ZBoolProperty>(c_retry_grenade.find(pc, GRENADE));
        frame.pending_zoom = pc->get<u::ZBoolProperty>(c_retry_pending_zoom.find(pc, ZOOM));
        const double fwd = pi->get<u::ZFloatProperty>(c_gate_forward.find(pi, FORWARD));
        const double side = pi->get<u::ZFloatProperty>(c_gate_side.find(pi, SIDE));
        if (!std::isfinite(fwd) || !std::isfinite(side))
            throw std::runtime_error("non-finite combat input");
        frame.moving = fwd * fwd + side * side > 0.0001;
        // Only a moving, unblocked, non-sprinting recovery candidate can
        // reach Python's weapon checks. Recovery does not consume firing hints.
        if (recovery_only && (frame.sprinting || !frame.moving
                              || frame.action_blocked || frame.pending_zoom))
            return frame;
        auto* weapon = pawn->get<u::ZObjectProperty>(c_combat_weapon.find(pawn, WEAPON));
        if (weapon) {
            frame.weapon_put_down = weapon->get<u::ZBoolProperty>(c_combat_putdown.find(weapon, PUTDOWN));
            frame.weapon_zoomed = weapon->get<u::ZByteProperty>(c_combat_zoom.find(weapon, WZOOM)) != 0;
        }
        if (recovery_only) return frame;
        frame.reloading = pawn->get<u::ZIntProperty>(c_combat_reload.find(pawn, RELOAD)) != 0;
        auto* offhand = pawn->get<u::ZObjectProperty>(c_combat_offhand.find(pawn, OFFHAND));
        if (weapon) frame.firing_hint = weapon->get<u::ZBoolProperty>(
            c_combat_firing.find(weapon, FIRING));
        if (offhand) frame.firing_hint = offhand->get<u::ZBoolProperty>(
            c_combat_firing.find(offhand, FIRING)) || frame.firing_hint;
        return frame;
    }
    static py::tuple combat_tuple(const omni::CombatFrame& frame) {
        return py::make_tuple(frame.sprinting, frame.action_blocked,
            frame.pending_zoom, frame.moving, frame.weapon_put_down,
            frame.weapon_zoomed, frame.firing_hint, frame.reloading);
    }
    py::object combat_snapshot(std::uintptr_t pc, std::uintptr_t pi,
                               bool recovery_only = false) {
        std::scoped_lock lock{mutex};
        try {
            const auto frame = read_combat(pc, pi, recovery_only);
            if (recovery_only)
                return py::make_tuple(frame.sprinting, frame.action_blocked,
                    frame.pending_zoom, frame.moving, frame.weapon_put_down, frame.weapon_zoomed);
            return combat_tuple(frame);
        }
        catch (const std::exception&) {
            return py::none();
        } catch (...) {
            return py::none();
        }
    }
    py::object combat_retry_observe(std::uintptr_t pc, std::uintptr_t pi,
                                   bool transitioning) {
        std::scoped_lock lock{mutex};
        try {
            const auto frame = read_combat(pc, pi);
            const int result = omni::combat_retry_observe(frame, transitioning, assist_recovery);
            return py::make_tuple(result, combat_tuple(frame));
        } catch (const std::exception&) {
            assist_recovery.reset_retry();
            return py::none();
        } catch (...) {
            assist_recovery.reset_retry();
            return py::none();
        }
    }

    int assist_retry_observe(std::uintptr_t pc_address,
                             std::uintptr_t pi_address) noexcept {
        std::scoped_lock lock{mutex};
        if (!pc_address || !pi_address || quarantined) {
            return -1;
        }
        try {
            auto* pc = reinterpret_cast<u::UObject*>(pc_address);
            auto* pi = reinterpret_cast<u::UObject*>(pi_address);
            static const u::FName SPRINT{L"bInSprintState"};
            static const u::FName MELEE{L"bPerformingMeleeAttack"};
            static const u::FName SHARED{L"bPerformingSharedWeaponAction"};
            static const u::FName GRENADE{L"bThrowingGrenade"};
            static const u::FName ZOOM{L"bPendingZoom"};
            static const u::FName FORWARD{L"aBaseY"};
            static const u::FName SIDE{L"aStrafe"};
            const bool sprinting = pc->get<u::ZBoolProperty>(
                c_pc_sprinting.find(pc, SPRINT));
            if (sprinting) {
                assist_recovery.reset_retry();
                return 0;
            }
            const double fwd = pi->get<u::ZFloatProperty>(
                c_gate_forward.find(pi, FORWARD));
            const double side = pi->get<u::ZFloatProperty>(
                c_gate_side.find(pi, SIDE));
            if (!std::isfinite(fwd) || !std::isfinite(side))
                throw std::runtime_error("non-finite controller axis");
            if (fwd * fwd + side * side <= 0.0001) {
                assist_recovery.reset_retry();
                return 1;
            }
            const bool blocked =
                pc->get<u::ZBoolProperty>(c_retry_melee.find(pc, MELEE)) ||
                pc->get<u::ZBoolProperty>(c_retry_shared.find(pc, SHARED)) ||
                pc->get<u::ZBoolProperty>(c_retry_grenade.find(pc, GRENADE)) ||
                pc->get<u::ZBoolProperty>(c_retry_pending_zoom.find(pc, ZOOM));
            if (blocked) {
                assist_recovery.reset_retry();
                return 2;
            }
            if (!assist_recovery.retry(true)) return 3;
            return 4;
        } catch (...) {
            assist_recovery.reset_retry();
            return -1;
        }
    }
    int assist_summon_observe(std::uintptr_t pc_address,
                              double now) noexcept {
        std::scoped_lock lock{mutex};
        if (!pc_address || quarantined || !std::isfinite(now)) {
            return -1;
        }
        try {
            auto* pc = reinterpret_cast<u::UObject*>(pc_address);
            static const u::FName SPRINT{L"bInSprintState"};
            static const u::FName MELEE{L"bPerformingMeleeAttack"};
            static const u::FName SHARED{L"bPerformingSharedWeaponAction"};
            static const u::FName GRENADE{L"bThrowingGrenade"};
            static const u::FName ZOOM{L"bPendingZoom"};
            if (pc->get<u::ZBoolProperty>(c_pc_sprinting.find(pc, SPRINT))) {
                assist_recovery.reset_summon();
                return 0;
            }
            const bool blocked =
                pc->get<u::ZBoolProperty>(c_retry_melee.find(pc, MELEE)) ||
                pc->get<u::ZBoolProperty>(c_retry_shared.find(pc, SHARED)) ||
                pc->get<u::ZBoolProperty>(c_retry_grenade.find(pc, GRENADE)) ||
                pc->get<u::ZBoolProperty>(c_retry_pending_zoom.find(pc, ZOOM));
            if (blocked) {
                assist_recovery.reset_summon();
                return 2;
            }
            if (!assist_recovery.summon(true, now)) return 3;
            return 4;
        } catch (...) {
            assist_recovery.reset_summon();
            return -1;
        }
    }

    // Validated eligibility booleans are supplied by Python's existing game
    // guards. This pure state machine never reads or writes an Unreal object.
    bool assist_retry_ready(bool eligible) noexcept {
        std::scoped_lock lock{mutex};
        const bool ready = assist_recovery.retry(eligible);
        return ready;
    }
    bool assist_summon_ready(bool eligible, double now) noexcept {
        std::scoped_lock lock{mutex};
        const bool ready = assist_recovery.summon(eligible, now);
        return ready;
    }
    bool assist_travel_ready(bool eligible) noexcept {
        std::scoped_lock lock{mutex};
        const bool ready = assist_recovery.travel(eligible);
        return ready;
    }
    void assist_reset(int which) noexcept {
        std::scoped_lock lock{mutex};
        if (which == 1) { assist_recovery.reset_retry(); sprint_intent.reset_retry(); }
        else if (which == 2) assist_recovery.reset_summon();
        else if (which == 3) assist_recovery.reset_travel();
        else { assist_recovery.reset(); sprint_intent.reset_retry(); }
    }

    // Reversal detection only; corrected velocity and all action/weapon guards
    // are still handled by the previously validated Python implementation.
    py::tuple reversal_observe(double fwd, double side, double mag_sq,
                               bool correction_active) {
        std::scoped_lock lock{mutex};
        const auto scan = reversal_observer.observe(
            fwd, side, mag_sq, correction_active || reversal_correction.active());
        if (scan.released || !(mag_sq > 0.0) || !std::isfinite(mag_sq))
            reversal_correction.reset();
        return py::make_tuple(scan.needs_kinematics,
                              scan.candidate, scan.released);
    }
    py::object reversal_apply(double dx, double dy, double vx, double vy,
                              double cap, double dt, bool instant,
                              bool candidate) {
        std::scoped_lock lock{mutex};
        const auto result = reversal_correction.apply(
            dx, dy, vx, vy, cap, dt, instant, candidate);
        if (!result.has_velocity) return py::none();
        return py::make_tuple(result.vx, result.vy);
    }
    py::object center_begin(double dx, double dy, double ox, double oy,
                            double cap) {
        std::scoped_lock lock{mutex};
        const auto result = omni::center_begin(dx, dy, ox, oy, cap);
        if (!result.accepted) return py::none();
        return py::make_tuple(result.speed, result.instant_vx, result.instant_vy);
    }
    py::object center_blend(double ox, double oy, double tx, double ty,
                            double speed, double cap, double elapsed) {
        std::scoped_lock lock{mutex};
        const auto result = omni::center_blend(
            ox, oy, tx, ty, speed, cap, elapsed);
        if (!result.accepted) return py::none();
        return py::make_tuple(result.vx, result.vy, result.finished);
    }
    // Movement writes have their own callback-scoped ownership. Precision may
    // be disabled, keyboard input may be active, or its speed owner restored.
    py::object velocity_write(std::uintptr_t pc_address, std::uintptr_t pi_address,
                              std::uintptr_t pawn_address, std::uint64_t epoch,
                              double vx, double vy) {
        std::scoped_lock lock{mutex};
        bool write_phase_entered = false;
        try {
            const auto snapshot = read_motion(pc_address, pi_address, epoch);
            auto* pawn = snapshot.pawn;
            // A changed generation is an ordinary rejection, never permission
            // for the Python fallback to write an old correction instead.
            const auto reject = []() -> py::object { return py::bool_(false); };
            if (reinterpret_cast<std::uintptr_t>(pawn) != pawn_address ||
                !motion_generation.matches(snapshot.context) ||
                *motion_pc != snapshot.pc || *motion_pi != snapshot.pi || *motion_pawn != pawn ||
                snapshot.context.physics != 1 || !snapshot.sprinting) return reject();
            u::WeakPointer live_pawn{pawn};
            if (*live_pawn != pawn) return reject();
            static const u::FName CROUCHED{L"bIsCrouched"}, WANTS_CROUCH{L"bWantsToCrouch"};
            static const u::FName CINEMATIC{L"bCinematicMode"}, IGNORE_MOVE{L"bIgnoreMoveInput"};
            static const u::FName DUCK{L"bDuck"}, JUMP{L"bPressedJump"}, ZOOM{L"bPendingZoom"};
            static const u::FName RELOAD{L"bReloading"}, FORCE_RM{L"bForceRMVelocity"};
            static const u::FName FORCE_REGULAR{L"bForceRegularVelocity"}, RM_STOP{L"bRMForceStop"};
            if (pawn->get<u::ZBoolProperty>(c_crouched.find(pawn, CROUCHED)) ||
                pawn->get<u::ZBoolProperty>(c_wants_crouch.find(pawn, WANTS_CROUCH)) ||
                snapshot.pc->get<u::ZBoolProperty>(c_cinematic.find(snapshot.pc, CINEMATIC)) ||
                snapshot.pc->get<u::ZByteProperty>(c_ignore_move.find(snapshot.pc, IGNORE_MOVE)) ||
                snapshot.pc->get<u::ZByteProperty>(c_duck.find(snapshot.pc, DUCK)) ||
                snapshot.pc->get<u::ZBoolProperty>(c_jump.find(snapshot.pc, JUMP)) ||
                snapshot.pc->get<u::ZBoolProperty>(c_retry_pending_zoom.find(snapshot.pc, ZOOM)) ||
                pawn->get<u::ZIntProperty>(c_combat_reload.find(pawn, RELOAD)) ||
                pawn->get<u::ZBoolProperty>(c_force_rm.find(pawn, FORCE_RM)) ||
                pawn->get<u::ZBoolProperty>(c_force_regular.find(pawn, FORCE_REGULAR)) ||
                pawn->get<u::ZBoolProperty>(c_rm_stop.find(pawn, RM_STOP))) return reject();
            static const u::FName GROUND_SPEED{L"GroundSpeed"}, MAX_DESIRED{L"MaxDesiredSpeed"};
            const double cap = pawn->get<u::ZFloatAttributeProperty>(c_ground_speed.find(pawn, GROUND_SPEED)) *
                std::min(1.0, static_cast<double>(pawn->get<u::ZFloatProperty>(
                    c_max_desired.find(pawn, MAX_DESIRED))));
            if (!omni::valid_horizontal_write(vx, vy, cap)) return reject();
            static const u::FName VELOCITY{L"Velocity"};
            static const u::FName X{L"X"};
            static const u::FName Y{L"Y"};
            auto value = pawn->get<u::ZStructProperty>(c_velocity.find(pawn, VELOCITY));
            // SDK WrappedStruct points at the LIVE Velocity storage. Resolve
            // and validate BOTH field descriptors before touching either value.
            // A missing/mismatched Y property must fail before X is mutated.
            const auto* x_field = value.type->find_prop_and_validate<u::ZFloatProperty>(X);
            const auto* y_field = value.type->find_prop_and_validate<u::ZFloatProperty>(Y);
            write_phase_entered = true;
            const auto status = omni::commit_horizontal_velocity(
                [&](bool x, float component) {
                    value.set<u::ZFloatProperty>(x ? x_field : y_field, component);
                }, static_cast<float>(vx), static_cast<float>(vy));
            if (status == omni::HorizontalWriteResult::uncertain) {
                // -1 is a strict sentinel, NOT a bool. Python must disable this
                // native writer and decline same-frame fallback. In particular,
                // never claim the earlier component was unchanged.
                return py::int_(-1);
            }
            return py::bool_(true);
        } catch (const MotionContextError&) {
            return py::bool_(false);
        } catch (const std::exception&) {
            // The first write has NOT run when phase=false. Only that case
            // can explicitly authorize the established Python fallback.
            return write_phase_entered ? py::object(py::int_(-1))
                                       : py::object(py::none());
        } catch (...) {
            return write_phase_entered ? py::object(py::int_(-1))
                                       : py::object(py::none());
        }
    }
    void reversal_reset() {
        std::scoped_lock lock{mutex};
        reversal_observer.reset();
        reversal_correction.reset();
    }

    // One bridge/lock/input pair for gate, center and reversal observation;
    // optional Precision reuses this callback snapshot instead of rereading it.
    py::object motion_update(std::uintptr_t pc_address, std::uintptr_t pi_address,
                             std::uint64_t epoch, bool precision_enabled,
                             double strength, double grace, bool center_enabled,
                             bool reversal_enabled, bool correction_active,
                             bool compact_result = false) {
        double fwd = 0.0, side = 0.0;
        bool moving = false, allowed = false, changed = false;
        bool scanned = false;
        omni::CenterInputEvent crossing{};
        omni::ReversalObservation scan{};
        {
            std::scoped_lock lock{mutex};
            try {
                if (!std::isfinite(grace)) throw std::invalid_argument("non-finite motion grace");
                const auto snapshot = read_motion(pc_address, pi_address, epoch);
                // SDK weak handles validate the GObjects slot/outer/class/name
                // under UE3; current relationships and map epoch also matter.
                if (*motion_pc != snapshot.pc || *motion_pi != snapshot.pi || *motion_pawn != snapshot.pawn)
                    motion_generation.reset();
                changed = motion_generation.bind(snapshot.context);
                if (changed) {
                    motion_pc = u::WeakPointer{snapshot.pc};
                    motion_pi = u::WeakPointer{snapshot.pi};
                    motion_pawn = u::WeakPointer{snapshot.pawn};
                    sprint_gate.reset(); center_input.reset();
                    reversal_observer.reset(); reversal_correction.reset();
                }
                static const u::FName FWD{L"aBaseY"}, SIDE{L"aStrafe"};
                fwd = snapshot.pi->get<u::ZFloatProperty>(c_gate_forward.find(snapshot.pi, FWD));
                side = snapshot.pi->get<u::ZFloatProperty>(c_gate_side.find(snapshot.pi, SIDE));
                if (!std::isfinite(fwd) || !std::isfinite(side))
                    throw std::runtime_error("non-finite motion input");
                if (precision_enabled) {
                    // Keep the full update whenever a speed edit or recovery
                    // may be pending. The live movement generation still runs.
                    const bool skip_speed = !quarantined && state.active_owner() == 0
                        && io.pawn_address == 0
                        && (!snapshot.sprinting || !snapshot.context.gamepad);
                    if (!skip_speed && !update_locked(pc_address, pi_address, true, strength, &snapshot))
                        throw std::runtime_error("native Precision update rejected");
                } else if (io.pawn_address) {
                    restore_locked();
                }
                const double gap = std::max(0.0, grace);
                const double magnitude_sq = fwd * fwd + side * side;
                moving = magnitude_sq > 0.0001;
                const bool correcting_context = snapshot.sprinting && snapshot.context.physics == 1;
                const bool observe_center = center_enabled && correcting_context && gap > 0.0;
                double now = 0.0;
                if (observe_center || (gap > 0.0 && (moving || sprint_gate.has_recent_movement()))) {
                    using Clock = std::chrono::steady_clock;
                    now = std::chrono::duration<double>(
                        Clock::now().time_since_epoch()).count();
                }
                allowed = sprint_gate.allows_moving(moving, now, gap);
                if (observe_center)
                    crossing = center_input.observe(fwd, side, now, gap, true);
                else
                    center_input.reset();
                if (reversal_enabled && correcting_context && moving && !crossing.reversed) {
                    scanned = true;
                    scan = reversal_observer.observe(fwd, side, magnitude_sq,
                        (!changed && correction_active) || reversal_correction.active());
                    if (scan.released) reversal_correction.reset();
                }
            } catch (const std::exception&) {
                reset_motion_locked(); restore_locked();
                throw;
            } catch (...) {
                reset_motion_locked(); restore_locked();
                throw;
            }
        }
        // Construct Python objects after releasing the native mutex.
        if (compact_result) {
            // v1: moving, allowed and generation changed occupy bits 0–2.
            const unsigned flags = static_cast<unsigned>(moving)
                | (static_cast<unsigned>(allowed) << 1)
                | (static_cast<unsigned>(changed) << 2);
            return py::int_(flags);
        }
        py::object observed = py::none();
        if (scanned) observed = py::make_tuple(scan.needs_kinematics, scan.candidate, scan.released);
        return py::make_tuple(true,
            py::make_tuple(fwd, side, moving, allowed, crossing.entered, crossing.reversed),
            observed, changed);
    }

    // Returns true if update was handled natively, even if it intentionally
    // skips a write. False means Python may safely take over after restore.
    bool update(std::uintptr_t pc_address,
                std::uintptr_t pi_address,
                bool enabled,
                double strength) noexcept {
        std::scoped_lock lock{mutex};
        return update_locked(pc_address, pi_address, enabled, strength);
    }
    bool update_locked(std::uintptr_t pc_address, std::uintptr_t pi_address,
                       bool enabled, double strength,
                       const MotionSnapshot* sampled = nullptr) noexcept {
        if (quarantined) {
            restore_locked();
            return state.active_owner() != 0;
        }
        try {
            if (!enabled || pc_address == 0 || pi_address == 0) {
                restore_locked();
                return true;
            }
            if (!std::isfinite(strength) || strength < 0.0 || strength > 1.0) {
                restore_locked();
                return true;
            }

            // Addresses provided by the existing verified Python local-pc
            // callback are valid ONLY for this call; never cache either.
            auto* pc = reinterpret_cast<u::UObject*>(pc_address);
            auto* pi = reinterpret_cast<u::UObject*>(pi_address);
            omni::PrecisionFrame frame{};
            frame.enabled = enabled;
            frame.has_pc = true;
            frame.has_pi = true;
            frame.strength = strength;

            static const u::FName GAMEPAD{L"bUsingGamepad"};
            static const u::FName IN_SPRINT{L"bInSprintState"};
            frame.gamepad = sampled ? sampled->context.gamepad :
                pi->get<u::ZBoolProperty>(c_gamepad.find(pi, GAMEPAD));
            frame.pc_sprinting = sampled ? sampled->sprinting :
                pc->get<u::ZBoolProperty>(c_pc_sprinting.find(pc, IN_SPRINT));
            if (!frame.gamepad || !frame.pc_sprinting) {
                state.update(frame, io);
                release_speed_handle_if_safe_locked();
                if (state.active_owner() != 0) quarantined = true;
                return true;
            }

            static const u::FName PAWN{L"Pawn"};
            static const u::FName IS_SPRINTING{L"bIsSprinting"};
            static const u::FName PHYSICS{L"Physics"};
            static const u::FName CROUCHED{L"bIsCrouched"};
            static const u::FName CINEMATIC{L"bCinematicMode"};
            static const u::FName IGNORE_MOVE{L"bIgnoreMoveInput"};
            static const u::FName RAW_RIGHT{L"RawJoyRight"};
            static const u::FName RAW_UP{L"RawJoyUp"};

            auto* pawn = sampled ? sampled->pawn : pc->get<u::ZObjectProperty>(c_pawn.find(pc, PAWN));
            if (pawn == nullptr) {
                state.update(frame, io);
                release_speed_handle_if_safe_locked();
                if (state.active_owner() != 0) quarantined = true;
                return true;
            }

            // Engine.Pawn.bIsSprinting is a Gearbox IntAttributeProperty
            // (verified by in-game native evidence for BL2 Pawn at 0x1d0).
            // It derives from ZIntProperty in the SDK C++ wrapper, but its
            // reflected runtime class is *IntAttributeProperty*, not
            // IntProperty. The generic IntProperty and BoolProperty class
            // validators correctly reject that invalid property type.
            frame.pawn_sprinting =
                pawn->get<u::ZIntAttributeProperty>(c_pawn_sprinting.find(pawn, IS_SPRINTING)) > 0;
            const auto physics = sampled ? sampled->context.physics :
                pawn->get<u::ZByteProperty>(c_physics.find(pawn, PHYSICS));
            frame.walking_physics = physics == 1;
            frame.crouched = pawn->get<u::ZBoolProperty>(c_crouched.find(pawn, CROUCHED));
            frame.cinematic = pc->get<u::ZBoolProperty>(c_cinematic.find(pc, CINEMATIC));
            // Engine.PlayerController.bIgnoreMoveInput is a byte, not a bool.
            // Match the UE3 reflected declaration to avoid a
            // property-class validation failure on the first eligible sprint.
            frame.ignore_move = pc->get<u::ZByteProperty>(c_ignore_move.find(pc, IGNORE_MOVE)) != 0;
            if (!frame.pawn_sprinting || !frame.walking_physics || frame.crouched ||
                frame.cinematic || frame.ignore_move) {
                state.update(frame, io);
                release_speed_handle_if_safe_locked();
                if (state.active_owner() != 0) quarantined = true;
                return true;
            }
            frame.raw_joy_right = pi->get<u::ZFloatProperty>(c_raw_right.find(pi, RAW_RIGHT));
            frame.raw_joy_up = pi->get<u::ZFloatProperty>(c_raw_up.find(pi, RAW_UP));
            frame.owner = reinterpret_cast<std::uintptr_t>(pawn);

            std::optional<double> sampled_radius;
            if (state.active_owner() == 0 && io.pawn_address == 0) {
                // Full stick cannot write a speed value. With no outstanding
                // ownership, avoid capturing and resolving a weak pawn handle.
                if (frame.owner == state.blocked_owner()) return true;
                sampled_radius = omni::precision_radius(frame);
                if (!sampled_radius) return true;
            }

            // Restore the old pawn using its existing weak reference before
            // replacing the only native lifetime handle.
            if (io.pawn_address != frame.owner || io.resolve(frame.owner) != pawn) {
                restore_locked();
                if (state.active_owner() != 0) return true;
                io.capture(pawn);
            }
            const auto failed_before = io.read_failures + io.write_failures;
            state.update(frame, io, sampled_radius);
            if (io.read_failures + io.write_failures != failed_before) {
                restore_locked();
                quarantined = true;
                return state.active_owner() != 0;
            }
            // A full-stick transition can restore speed and retire the owner.
            // Release the now-unused handle so subsequent frames take the fast path.
            if (state.active_owner() == 0) io.clear();
            return true;
        } catch (const std::exception&) {
            restore_locked();
            quarantined = true;
            return state.active_owner() != 0;
        } catch (...) {
            restore_locked();
            quarantined = true;
            return state.active_owner() != 0;
        }
    }
};

NativePrecision native{};

bool update(std::uintptr_t pc_addr, std::uintptr_t pi_addr,
            bool enabled, double strength) noexcept {
    return native.update(pc_addr, pi_addr, enabled, strength);
}
py::tuple gate_update(std::uintptr_t pi_addr, double center_grace, bool center_enabled) {
    return native.gate_update(pi_addr, center_grace, center_enabled);
}
void gate_reset() { native.gate_reset(); }
int assist_retry_observe(std::uintptr_t pc, std::uintptr_t pi) noexcept {
    return native.assist_retry_observe(pc, pi);
}
int assist_summon_observe(std::uintptr_t pc, double now) noexcept {
    return native.assist_summon_observe(pc, now);
}
py::tuple travel_step(std::uint64_t epoch, bool ready, bool modal,
                      bool close_pending, bool sprinting, bool armed,
                      double now) {
    return native.travel_step(epoch, ready, modal, close_pending,
                              sprinting, armed, now);
}
void travel_reset() { native.travel_reset(); }
// Fuses two already-verified native operations into one Python/native bridge.
// Their independent reflection ownership and safe skip behaviors are retained.
py::tuple precision_gate_update(std::uintptr_t pc_addr,
                                std::uintptr_t pi_addr, bool enabled,
                                double strength, double center_grace,
                                bool center_enabled) {
    if (!native.update(pc_addr, pi_addr, enabled, strength)) {
        return py::make_tuple(false, py::none());
    }
    return py::make_tuple(true, native.gate_update(pi_addr, center_grace, center_enabled));
}
// Fuse native movement and reversal input observation in one result
// returned to the Python hook.
// The game-verified Python hook still decides whether a candidate is safe to
// apply, and all movement/velocity writes retain their original authority.
py::tuple precision_gate_motion_update(
        std::uintptr_t pc_addr, std::uintptr_t pi_addr, bool enabled,
        double strength, double center_grace, bool center_enabled,
        bool reversal_enabled, bool correction_active) {
    auto baseline = precision_gate_update(
        pc_addr, pi_addr, enabled, strength, center_grace, center_enabled);
    if (!baseline[0].cast<bool>())
        return py::make_tuple(false, py::none(), py::none());
    auto gate = baseline[1].cast<py::tuple>();
    if (gate.size() != 6)
        throw std::runtime_error("unexpected movement gate tuple size");
    py::object scan = py::none();
    const bool moving = gate[2].cast<bool>();
    const bool crossing = gate[5].cast<bool>();
    if (reversal_enabled && moving && !crossing) {
        const double fwd = gate[0].cast<double>();
        const double side = gate[1].cast<double>();
        const double mag_sq = fwd * fwd + side * side;
        const auto result = native.reversal_observe(
            fwd, side, mag_sq, correction_active);
        scan = result;
        std::scoped_lock lock{native.mutex};
    } else {
        std::scoped_lock lock{native.mutex};
    }
    return py::make_tuple(true, gate, scan);
}
py::object assist_snapshot(std::uintptr_t pc_addr, std::uintptr_t pi_addr) {
    return native.assist_snapshot(pc_addr, pi_addr);
}
int retry_preflight(std::uintptr_t pc_addr) noexcept { return native.retry_preflight(pc_addr); }
bool assist_retry_ready(bool eligible) noexcept { return native.assist_retry_ready(eligible); }
bool assist_summon_ready(bool eligible, double now) noexcept { return native.assist_summon_ready(eligible, now); }
bool assist_travel_ready(bool eligible) noexcept { return native.assist_travel_ready(eligible); }
void assist_reset(int which) noexcept { native.assist_reset(which); }
py::tuple reversal_observe(double fwd, double side, double magnitude_sq,
                           bool correction_active) {
    return native.reversal_observe(fwd, side, magnitude_sq, correction_active);
}
void reversal_reset() { native.reversal_reset(); }
py::object reversal_apply(double dx, double dy, double vx, double vy,
                          double cap, double dt, bool instant, bool candidate) {
    return native.reversal_apply(dx, dy, vx, vy, cap, dt, instant, candidate);
}
py::object center_begin(double dx, double dy, double ox, double oy, double cap) {
    return native.center_begin(dx, dy, ox, oy, cap);
}
py::object center_blend(double ox, double oy, double tx, double ty,
                        double speed, double cap, double elapsed) {
    return native.center_blend(ox, oy, tx, ty, speed, cap, elapsed);
}
py::object velocity_write(std::uintptr_t pc_addr, std::uintptr_t pi_addr,
                          std::uintptr_t pawn_addr, std::uint64_t epoch, double vx, double vy) {
    return native.velocity_write(pc_addr, pi_addr, pawn_addr, epoch, vx, vy);
}
bool restore() noexcept {
    std::scoped_lock lock{native.mutex};
    native.restore_locked();
    return native.state.active_owner() == 0;
}
bool reset() noexcept {
    std::scoped_lock lock{native.mutex};
    native.reset_locked();
    return native.state.active_owner() == 0;
}
}

PYBIND11_MODULE(omni_precision_backend, mod) {
    mod.doc() = "Native UnrealSDK Precision Sprint and movement backend";
    mod.def("intent_press", [](std::uintptr_t pc, std::uintptr_t pi,
                              std::uintptr_t pawn, std::uint64_t epoch, bool mirror) {
        bool changed, armed;
        {
            std::scoped_lock lock{native.mutex};
            changed = native.sprint_intent.press({pc, pi, pawn, epoch}, mirror);
            armed = native.sprint_intent.armed();
        }
        return py::make_tuple(armed, changed);
    });
    mod.def("intent_release", [](std::uintptr_t pc, std::uintptr_t pi,
                                std::uintptr_t pawn, std::uint64_t epoch, bool mirror) {
        std::scoped_lock lock{native.mutex};
        native.sprint_intent.release({pc, pi, pawn, epoch}, mirror);
    });
    mod.def("intent_reset", [](bool mirror) {
        std::scoped_lock lock{native.mutex}; native.sprint_intent.reset(mirror);
    });
    mod.def("intent_observe", [](std::uintptr_t pc, std::uintptr_t pi,
                                std::uintptr_t pawn, std::uint64_t epoch, bool mirror,
                                bool context_ready, bool sprinting, bool moving,
                                bool grounded, bool permitted, bool pending_try,
                                bool pending_wants) {
        omni::IntentDecision decision;
        {
            std::scoped_lock lock{native.mutex};
            decision = native.sprint_intent.observe({pc, pi, pawn, epoch}, mirror,
                {context_ready, sprinting, moving, grounded, permitted,
                 pending_try, pending_wants});
        }
        return py::make_tuple(static_cast<int>(decision.phase), decision.armed,
                             decision.try_value, decision.write_required,
                             decision.airborne_hold);
    });
    mod.def("update", &update, py::arg("pc_address"), py::arg("pi_address"),
            py::arg("enabled"), py::arg("strength"));
    mod.def("gate_update", &gate_update, py::arg("local_pi_address"),
            py::arg("center_grace"), py::arg("center_enabled"));
    mod.def("gate_reset", &gate_reset);
    mod.def("precision_gate_update", &precision_gate_update,
            py::arg("local_pc_address"), py::arg("local_pi_address"),
            py::arg("enabled"), py::arg("strength"),
            py::arg("center_grace"), py::arg("center_enabled"));
    mod.def("precision_gate_motion_update", &precision_gate_motion_update,
            py::arg("local_pc_address"), py::arg("local_pi_address"),
            py::arg("enabled"), py::arg("strength"),
            py::arg("center_grace"), py::arg("center_enabled"),
            py::arg("reversal_enabled"), py::arg("correction_active"));
    mod.def("motion_update", [](std::uintptr_t pc, std::uintptr_t pi,
            std::uint64_t epoch, bool precision, double strength, double grace,
            bool center, bool reversal, bool correcting) {
        return native.motion_update(pc, pi, epoch, precision, strength, grace,
                                    center, reversal, correcting);
    }, py::arg("local_pc_address"), py::arg("local_pi_address"), py::arg("epoch"),
       py::arg("precision_enabled"), py::arg("strength"), py::arg("center_grace"),
       py::arg("center_enabled"), py::arg("reversal_enabled"), py::arg("correction_active"));
    // Vanilla direction has no Python consumer for axes or correction data.
    mod.def("motion_gate_v1", [](std::uintptr_t pc, std::uintptr_t pi,
            std::uint64_t epoch, bool precision, double strength, double grace) {
        return native.motion_update(pc, pi, epoch, precision, strength, grace,
                                    false, false, false, true);
    }, py::arg("local_pc_address"), py::arg("local_pi_address"), py::arg("epoch"),
       py::arg("precision_enabled"), py::arg("strength"), py::arg("center_grace"));
    mod.def("motion_reset", []() { native.motion_reset(); });
    mod.def("travel_step", &travel_step, py::arg("epoch"), py::arg("ready"),
            py::arg("modal_open"), py::arg("close_pending"),
            py::arg("sprinting"), py::arg("armed"), py::arg("now"));
    mod.def("travel_reset", &travel_reset);
    mod.def("assist_retry_observe", &assist_retry_observe,
            py::arg("local_pc_address"), py::arg("local_pi_address"));
    mod.def("combat_snapshot", [](std::uintptr_t pc, std::uintptr_t pi) {
        return native.combat_snapshot(pc, pi);
    }, py::arg("local_pc_address"), py::arg("local_pi_address"));
    mod.def("assist_combat_v1", [](std::uintptr_t pc, std::uintptr_t pi) {
        return native.combat_snapshot(pc, pi, true);
    }, py::arg("local_pc_address"), py::arg("local_pi_address"));
    mod.def("combat_retry_observe", [](std::uintptr_t pc, std::uintptr_t pi, bool transitioning) {
        return native.combat_retry_observe(pc, pi, transitioning);
    }, py::arg("local_pc_address"), py::arg("local_pi_address"),
       py::arg("weapon_transitioning"));
    mod.def("assist_summon_observe", &assist_summon_observe,
            py::arg("local_pc_address"), py::arg("monotonic_time"));
    mod.def("assist_snapshot", &assist_snapshot,
            py::arg("local_pc_address"), py::arg("local_pi_address"));
    mod.def("retry_preflight", &retry_preflight, py::arg("local_pc_address"));
    mod.def("assist_retry_ready", &assist_retry_ready, py::arg("eligible"));
    mod.def("assist_summon_ready", &assist_summon_ready, py::arg("eligible"), py::arg("now"));
    mod.def("assist_travel_ready", &assist_travel_ready, py::arg("eligible"));
    mod.def("assist_reset", &assist_reset, py::arg("which") = 0);
    mod.def("reversal_observe", &reversal_observe,
            py::arg("forward"), py::arg("side"), py::arg("magnitude_sq"),
            py::arg("correction_active"));
    mod.def("reversal_reset", &reversal_reset);
    mod.def("reversal_apply", &reversal_apply,
            py::arg("dx"), py::arg("dy"), py::arg("vx"), py::arg("vy"),
            py::arg("cap"), py::arg("dt"), py::arg("instant"),
            py::arg("candidate"));
    mod.def("center_begin", &center_begin,
            py::arg("dx"), py::arg("dy"), py::arg("ox"), py::arg("oy"),
            py::arg("cap"));
    mod.def("center_blend", &center_blend,
            py::arg("ox"), py::arg("oy"), py::arg("tx"), py::arg("ty"),
            py::arg("speed"), py::arg("cap"), py::arg("elapsed"));
    mod.def("velocity_write", &velocity_write,
            py::arg("local_pc_address"), py::arg("local_pi_address"),
            py::arg("local_pawn_address"), py::arg("epoch"), py::arg("vx"), py::arg("vy"));
    mod.def("restore", &restore);
    mod.def("reset", &reset);
    mod.def("backend_version", [](){ return "precision-native-stage29-velocity-guard-20261111"; });
}
