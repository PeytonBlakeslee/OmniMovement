#pragma once
#include <cstdint>
#include <cmath>
#include <limits>
#include <optional>
namespace omni {
// Pure state snapshots: a future UnrealSDK adapter must populate these from
// LOCAL game-thread objects. An owner ID is not a safe Unreal pointer.
struct PrecisionFrame {
    std::uintptr_t owner = 0;
    bool enabled = false, has_pc = false, has_pi = false, gamepad = false;
    bool pc_sprinting = false, pawn_sprinting = false, walking_physics = false;
    bool crouched = false, cinematic = false, ignore_move = false;
    double raw_joy_right = 0.0, raw_joy_up = 0.0, strength = 0.70;
};
struct SpeedAccess {
    virtual ~SpeedAccess() = default;
    virtual bool read_sprinting_pct(std::uintptr_t owner, double& out) = 0;
    virtual bool write_sprinting_pct(std::uintptr_t owner, double value) = 0;
};
// A missing radius cannot produce a speed edit.
std::optional<double> precision_radius(const PrecisionFrame& frame) noexcept;

class PrecisionSprint {
public:
    void update(const PrecisionFrame& frame, SpeedAccess& io,
                std::optional<double> sampled_radius = std::nullopt) noexcept;
    void restore(SpeedAccess& io) noexcept;
    void abandon() noexcept;  // Only after the weak pawn becomes invalid.
    void reset(SpeedAccess& io) noexcept;
    std::uintptr_t blocked_owner() const noexcept { return blocked_owner_; }
    std::uintptr_t active_owner() const noexcept { return owner_; }
    std::optional<double> last_write() const noexcept { return last_write_; }
    std::uint64_t ownership_conflicts() const noexcept { return ownership_conflicts_; }
private:
    std::uintptr_t owner_ = 0, blocked_owner_ = 0;
    std::optional<double> original_, last_write_;
    double written_right_ = 0.0, written_up_ = 0.0, written_strength_ = 0.0;
    std::uint64_t ownership_conflicts_ = 0;
};
// Stage 9: native, event-driven reversal observer. It detects opposing
// input and strength drops without touching Unreal objects or velocity.
// The proven Python guard and correction path remain authoritative.
struct ReversalObservation {
    bool needs_kinematics = false;
    bool candidate = false;
    bool released = false;
};
class ReversalObserver {
public:
    ReversalObservation observe(double forward, double side, double magnitude_sq,
                                bool correction_active) noexcept;
    void reset() noexcept {
        has_previous_ = false;
        previous_forward_ = previous_side_ = previous_strength_ = 0.0;
    }
private:
    bool has_previous_ = false;
    double previous_forward_ = 0.0;
    double previous_side_ = 0.0;
    double previous_strength_ = 0.0;
};
// Stage10: stateful reversal velocity blending. Call ONLY after the proven
// Python guard has validated local pawn, movement mode and action context.
// No Unreal pointers or mutable engine objects are held here.
struct ReversalCorrectionResult {
    bool has_velocity = false;
    double vx = 0.0, vy = 0.0;
};
class ReversalCorrection {
public:
    ReversalCorrectionResult apply(double dx, double dy, double vx, double vy,
                                   double cap, double dt, bool instant,
                                   bool candidate) noexcept;
    bool active() const noexcept { return active_; }
    void reset() noexcept {
        active_ = false;
        origin_x_ = origin_y_ = target_x_ = target_y_ = 0.0;
        speed_ = elapsed_ = 0.0;
    }
private:
    bool active_ = false;
    double origin_x_ = 0.0, origin_y_ = 0.0;
    double target_x_ = 0.0, target_y_ = 0.0;
    double speed_ = 0.0, elapsed_ = 0.0;
};
// Stage11: the actually used event-driven center-crossing math.
// These are stateless and operate only on validated numeric snapshots.
// Python keeps grace timing, interruption checks, active transition state,
// Unreal object ownership and the final pawn.Velocity write.
struct CenterBeginResult {
    bool accepted = false;
    double speed = 0.0;
    double instant_vx = 0.0, instant_vy = 0.0;
};
CenterBeginResult center_begin(double dx, double dy, double ox, double oy,
                               double cap) noexcept;
struct CenterBlendResult {
    bool accepted = false;
    double vx = 0.0, vy = 0.0;
    bool finished = false;
};
CenterBlendResult center_blend(double ox, double oy, double tx, double ty,
                               double speed, double cap, double elapsed) noexcept;
// Stage12: center-crossing observation inside the existing sprint gate.
struct CenterInputEvent { bool entered = false, reversed = false; };
class CenterInputObserver {
public:
    CenterInputEvent observe(double fwd, double side, double now,
                             double grace, bool enabled) noexcept;
    void reset() noexcept {
        seen_direction_ = centered_ = false;
        previous_fwd_ = previous_side_ = entered_at_ = 0.0;
    }
private:
    bool seen_direction_ = false, centered_ = false;
    double previous_fwd_ = 0.0, previous_side_ = 0.0, entered_at_ = 0.0;
};
// Stage14: pure retry/return readiness windows. Python validates all gameplay
// predicates, owns sprint state mutations, and keeps the fallback. No native
// player ticks, Unreal objects, or saved pointers are introduced.
class AssistRecoveryReadiness {
public:
    bool retry(bool eligible) noexcept {
        if (!eligible) { retry_frames_ = 0; return false; }
        if (retry_frames_ < 2) ++retry_frames_;
        return retry_frames_ == 2;
    }
    bool summon(bool eligible, double now) noexcept {
        if (!eligible || !std::isfinite(now)) {
            summon_ready_at_ = 0.0;
            return false;
        }
        if (summon_ready_at_ <= 0.0) {
            summon_ready_at_ = now + 0.050;
            return false;
        }
        return now >= summon_ready_at_;
    }
    bool travel(bool eligible) noexcept {
        if (!eligible) { travel_frames_ = 0; return false; }
        if (travel_frames_ < 3) ++travel_frames_;
        return travel_frames_ == 3;
    }
    void reset_retry() noexcept { retry_frames_ = 0; }
    void reset_summon() noexcept { summon_ready_at_ = 0.0; }
    void reset_travel() noexcept { travel_frames_ = 0; }
    void reset() noexcept {
        reset_retry(); reset_summon(); reset_travel();
    }
private:
    unsigned retry_frames_ = 0;
    unsigned travel_frames_ = 0;
    double summon_ready_at_ = 0.0;
};
// Stage20: combat observations are evidence, never permission to sprint.
// Firing/reload are reported separately: CanSprint owns their exact semantics.
struct CombatFrame {
    bool sprinting = false;
    bool action_blocked = false;
    bool pending_zoom = false;
    bool moving = false;
    bool weapon_put_down = false;
    bool weapon_zoomed = false;
    bool firing_hint = false;
    bool reloading = false;
    bool retry_blocked(bool transitioning) const noexcept {
        return action_blocked || pending_zoom || weapon_put_down ||
               weapon_zoomed || transitioning;
    }
};
inline int combat_retry_observe(const CombatFrame& frame, bool transitioning,
                               AssistRecoveryReadiness& readiness) noexcept {
    if (frame.sprinting) { readiness.reset_retry(); return 0; }
    if (!frame.moving) { readiness.reset_retry(); return 1; }
    if (frame.retry_blocked(transitioning)) {
        readiness.reset_retry(); return 2;
    }
    return readiness.retry(true) ? 4 : 3;
}
// Stage21: remembered mode is distinct from the engine's two pending flags
// and actual sprint. Context IDs are opaque generation keys, never pointers
// to dereference. Python supplies current engine/context permission on each
// frame and performs the guarded writes on the game thread.
struct IntentContext {
    std::uintptr_t controller = 0, input = 0, pawn = 0;
    std::uint64_t epoch = 0;
    bool operator==(const IntentContext&) const = default;
    bool valid() const noexcept { return controller && input && pawn; }
};
// Stage22: motion history belongs to a gameplay/input generation, independently
// of SprintingPct ownership. IDs are comparisons only, never retained pointers.
struct MotionContext {
    IntentContext objects{};
    int physics = 0;
    bool gamepad = false;
    bool operator==(const MotionContext&) const = default;
    bool valid() const noexcept { return objects.valid(); }
};
class MotionGeneration {
public:
    bool bind(const MotionContext& next) noexcept {
        const bool changed = !bound_ || context_ != next;
        context_ = next; bound_ = next.valid();
        return changed;
    }
    bool matches(const MotionContext& next) const noexcept {
        return bound_ && next.valid() && context_ == next;
    }
    void reset() noexcept { context_ = {}; bound_ = false; }
private:
    MotionContext context_{};
    bool bound_ = false;
};
inline bool valid_horizontal_write(double vx, double vy, double cap) noexcept {
    // Unreal stores float components; reject values that cannot survive that
    // conversion and impulses outside the existing correction speed envelope.
    return std::isfinite(vx) && std::isfinite(vy) && std::isfinite(cap) &&
           cap > 0.0 && std::abs(vx) <= std::numeric_limits<float>::max() &&
           std::abs(vy) <= std::numeric_limits<float>::max() && std::hypot(vx, vy) <= cap * 1.01;
}
enum class IntentPhase : int {
    Walk = 0, Suspended = 1, Sprinting = 2, IdlePending = 3,
    Blocked = 4, Debounce = 5, RetryPending = 6, AirbornePending = 7
};
struct IntentFrame {
    bool context_ready = false, sprinting = false, moving = false;
    bool grounded = false, permitted = false;
    bool pending_try = false, pending_wants = false;
};
struct IntentDecision {
    IntentPhase phase = IntentPhase::Suspended;
    bool armed = true, try_value = false, write_required = false;
    bool airborne_hold = false;
};
class SprintIntent {
public:
    void reset(bool armed = true) noexcept {
        context_ = {}; armed_ = armed; pressed_ = false; ready_frames_ = 0;
        last_frame_ = {}; last_ = {};
    }
    void reset_retry() noexcept { ready_frames_ = 0; }
    // Return whether this is a new physical press. Replayed press callbacks
    // before Release never toggle again or dispatch another native action.
    bool press(const IntentContext& context, bool mirror) noexcept {
        bind(context, mirror);
        if (!context.valid() || pressed_) return false;
        pressed_ = true; armed_ = !armed_; ready_frames_ = 0;
        return true;
    }
    void release(const IntentContext& context, bool mirror) noexcept {
        bind(context, mirror); pressed_ = false;
    }
    IntentDecision observe(const IntentContext& context, bool mirror,
                           const IntentFrame& frame) noexcept {
        bind(context, mirror);
        last_frame_ = frame;
        auto phase = IntentPhase::Suspended;
        bool request = false;
        if (!armed_) phase = IntentPhase::Walk;
        else if (!context.valid() || !frame.context_ready) phase = IntentPhase::Suspended;
        else if (frame.sprinting) phase = IntentPhase::Sprinting;
        else if (!frame.grounded) { phase = IntentPhase::AirbornePending; request = true; }
        else if (!frame.moving) { phase = IntentPhase::IdlePending; request = true; }
        else if (!frame.permitted) phase = IntentPhase::Blocked;
        else {
            if (ready_frames_ < 2) ++ready_frames_;
            phase = ready_frames_ == 2 ? IntentPhase::RetryPending : IntentPhase::Debounce;
            request = ready_frames_ == 2;
        }
        if (phase != IntentPhase::RetryPending && phase != IntentPhase::Debounce)
            ready_frames_ = 0;
        last_ = {phase, armed_, request, frame.pending_try != request,
                 phase == IntentPhase::AirbornePending};
        return last_;
    }
    bool armed() const noexcept { return armed_; }
    const IntentDecision& last() const noexcept { return last_; }
    const IntentFrame& last_frame() const noexcept { return last_frame_; }
private:
    void bind(const IntentContext& context, bool mirror) noexcept {
        if (context_ != context || armed_ != mirror) {
            const bool same_generation = context_ == context;
            context_ = context; armed_ = mirror; ready_frames_ = 0;
            if (!same_generation) pressed_ = false;
        }
    }
    IntentContext context_{};
    bool armed_ = true, pressed_ = false;
    unsigned ready_frames_ = 0;
    IntentDecision last_{};
    IntentFrame last_frame_{};
};
// Guarded engine velocity writes remain Python supervised.
class SprintGate {
public:
    bool allows(double forward, double side, double now_seconds, double grace_seconds) noexcept;
    bool allows_moving(bool moving, double now_seconds, double grace_seconds) noexcept;
    bool has_recent_movement() const noexcept { return last_moving_at_.has_value(); }
    void reset() noexcept { last_moving_at_.reset(); }
private:
    std::optional<double> last_moving_at_;
};
}  // namespace omni
