#include "omni_native_core.hpp"
#include <algorithm>
#include <cmath>
namespace omni {
namespace {
bool nearly_equal(double a, double b, double tolerance) noexcept {
    return std::abs(a - b) <= tolerance;
}
}
void PrecisionSprint::abandon() noexcept {
    owner_ = 0;
    original_.reset();
    last_write_.reset();
}
void PrecisionSprint::restore(SpeedAccess& io) noexcept {
    if (owner_ != 0 && original_.has_value() && last_write_.has_value()) {
        double current = 0.0;
        if (!io.read_sprinting_pct(owner_, current)) return;
        if (nearly_equal(current, *last_write_, 0.0001) &&
            !io.write_sprinting_pct(owner_, *original_)) return;
        // A foreign write takes precedence over our old value.
    }
    abandon();
}
void PrecisionSprint::reset(SpeedAccess& io) noexcept {
    restore(io);
    blocked_owner_ = 0;
    ownership_conflicts_ = 0;
}
std::optional<double> precision_radius(const PrecisionFrame& f) noexcept {
    const double x = f.raw_joy_right, y = f.raw_joy_up;
    if (!std::isfinite(x) || !std::isfinite(y)) return std::nullopt;
    const double radius = std::min(1.0, std::hypot(x, y));
    if (radius >= 0.998 || f.strength <= 0.0) return std::nullopt;
    return radius < 0.005 ? 0.0 : radius;
}

void PrecisionSprint::update(const PrecisionFrame& f, SpeedAccess& io,
                             std::optional<double> sampled_radius) noexcept {
    if (!f.enabled || !f.has_pc || !f.has_pi || !f.gamepad || !f.pc_sprinting ||
        f.owner == 0 || !f.pawn_sprinting || !f.walking_physics || f.crouched ||
        f.cinematic || f.ignore_move) {
        restore(io);
        return;
    }
    if (f.owner == blocked_owner_) return;
    // The current sample would produce exactly the last successful target.
    // As with the write tolerance below, no property read or write is due.
    if (f.owner == owner_ && last_write_ && f.raw_joy_right == written_right_
        && f.raw_joy_up == written_up_ && f.strength == written_strength_) return;
    const auto radius = sampled_radius ? sampled_radius : precision_radius(f);
    if (!radius) {
        restore(io);
        return;
    }
    if (owner_ != f.owner) {
        restore(io);
        if (owner_ != 0) return;  // Do not adopt a new pawn with pending old ownership.
        double original = 0.0;
        if (!io.read_sprinting_pct(f.owner, original)) return;
        if (!std::isfinite(original) || original < 0.05 || original > 5.0) return;
        owner_ = f.owner;
        original_ = original;
        last_write_.reset();
    }
    const double shaped = 0.35 + 0.65 * std::pow(*radius, 1.5);
    const double target = *original_ * (1.0 - f.strength * (1.0 - shaped));
    if (!last_write_.has_value() || std::abs(target - *last_write_) >= 0.005) {
        if (last_write_.has_value()) {
            double current = 0.0;
            if (!io.read_sprinting_pct(f.owner, current)) {
                restore(io);
                return;
            }
            if (!nearly_equal(current, *last_write_, 0.0001)) {
                restore(io);
                blocked_owner_ = f.owner;
                ++ownership_conflicts_;
                return;
            }
        }
        if (io.write_sprinting_pct(f.owner, target)) {
            last_write_ = target;
            written_right_ = f.raw_joy_right;
            written_up_ = f.raw_joy_up;
            written_strength_ = f.strength;
        } else {
            restore(io);
        }
    }
}
ReversalObservation ReversalObserver::observe(
    double fwd, double side, double magnitude_sq, bool correction_active) noexcept {
    // Mirrors reversal.py: no sqrt/division on the observation hot path.
    // A release is a >50% magnitude drop, hence 0.25 in squared space.
    if (!(magnitude_sq > 0.0) || !std::isfinite(magnitude_sq) ||
        !std::isfinite(fwd) || !std::isfinite(side)) {
        reset();
        return {};
    }
    const double previous_strength = previous_strength_;
    const bool release = has_previous_ &&
        magnitude_sq < previous_strength * 0.25;
    bool opposite = false;
    if (has_previous_ && previous_strength > 0.0) {
        const double dot = fwd * previous_forward_ + side * previous_side_;
        opposite = dot < 0.0 &&
            dot * dot > 0.01 * magnitude_sq * previous_strength;
    }
    previous_forward_ = fwd;
    previous_side_ = side;
    previous_strength_ = magnitude_sq;
    has_previous_ = true;

    if (release) return {false, false, true};
    return {correction_active || opposite, opposite, false};
}
CenterBeginResult center_begin(double dx, double dy, double ox,
                               double oy, double cap) noexcept {
    if (!std::isfinite(dx) || !std::isfinite(dy) ||
        !std::isfinite(ox) || !std::isfinite(oy) ||
        !std::isfinite(cap)) return {};
    const double speed = std::hypot(ox, oy);
    if (!(speed > 1.0) || dx * ox + dy * oy >= -speed * 0.1 ||
        !(cap > 0.0) || speed > cap * 1.01) return {};
    const double clamped = std::min(speed, cap);
    return {true, clamped, dx * clamped, dy * clamped};
}

CenterBlendResult center_blend(double ox, double oy, double tx, double ty,
                               double speed, double cap, double elapsed) noexcept {
    if (!std::isfinite(ox) || !std::isfinite(oy) ||
        !std::isfinite(tx) || !std::isfinite(ty) ||
        !std::isfinite(speed) || !std::isfinite(cap) ||
        !std::isfinite(elapsed) || !(cap > 0.0) || speed < 0.0)
        return {};
    const double blend = std::min(1.0, std::max(0.0, elapsed) / 0.120);
    const double corrected_speed = std::min(speed, cap);
    const double inv = 1.0 - blend;
    double vx = inv * ox + blend * tx * corrected_speed;
    double vy = inv * oy + blend * ty * corrected_speed;
    const double magnitude = std::hypot(vx, vy);
    if (!std::isfinite(magnitude)) return {};
    if (magnitude > cap && magnitude > 0.0) {
        const double scale = cap / magnitude;
        vx *= scale;
        vy *= scale;
    }
    return {true, vx, vy, blend >= 1.0};
}

ReversalCorrectionResult ReversalCorrection::apply(
    double dx, double dy, double vx, double vy, double cap, double dt,
    bool instant, bool candidate) noexcept {
    // Mirror Python Reversal.apply exactly, including rejection and clamp.
    if (!std::isfinite(dx) || !std::isfinite(dy) ||
        !std::isfinite(vx) || !std::isfinite(vy) ||
        !std::isfinite(cap) || !std::isfinite(dt) ||
        dt <= 0.0 || dt > 0.1 || cap <= 0.0) {
        reset();
        return {};
    }
    const double velocity_speed = std::hypot(vx, vy);
    if (velocity_speed > cap * 1.01) {
        reset();
        return {};
    }
    if (candidate && velocity_speed > 1.0 &&
        dx * vx + dy * vy < -velocity_speed * 0.1) {
        origin_x_ = vx;
        origin_y_ = vy;
        speed_ = std::min(velocity_speed, cap);
        elapsed_ = 0.0;
        target_x_ = dx;
        target_y_ = dy;
        active_ = true;
    }
    if (!active_) return {};
    if (dx * target_x_ + dy * target_y_ < 0.8) {
        reset();
        return {};
    }
    elapsed_ += dt;
    const double blend = instant ? 1.0 : std::min(1.0, elapsed_ / 0.120);
    const double target_speed = std::min(speed_, cap);
    double rx = (1.0 - blend) * origin_x_ + blend * dx * target_speed;
    double ry = (1.0 - blend) * origin_y_ + blend * dy * target_speed;
    const double magnitude = std::hypot(rx, ry);
    if (magnitude > cap) {
        const double scale = cap / magnitude;
        rx *= scale;
        ry *= scale;
    }
    if (blend == 1.0) reset();
    return {true, rx, ry};
}
CenterInputEvent CenterInputObserver::observe(double fwd, double side,
                              double now, double grace, bool enabled) noexcept {
    if (!enabled || !(grace > 0.0) || !std::isfinite(fwd) ||
        !std::isfinite(side) || !std::isfinite(now) || !std::isfinite(grace)) {
        reset();
        return {};
    }
    const double strength_sq = fwd * fwd + side * side;
    if (!(strength_sq > 0.0001)) {
        if (!centered_ && seen_direction_) {
            centered_ = true;
            entered_at_ = now;
            return {true, false};
        }
        if (centered_ && (now < entered_at_ || now - entered_at_ > grace))
            reset();
        return {};
    }
    bool reversed = false;
    if (centered_ && seen_direction_ && now >= entered_at_ &&
        now - entered_at_ <= grace) {
        const double old_sq = previous_fwd_ * previous_fwd_ +
                              previous_side_ * previous_side_;
        const double dot = fwd * previous_fwd_ + side * previous_side_;
        reversed = dot < 0.0 && dot * dot > 0.01 * strength_sq * old_sq;
    }
    previous_fwd_ = fwd;
    previous_side_ = side;
    seen_direction_ = true;
    centered_ = false;
    return {false, reversed};
}
bool SprintGate::allows(double forward, double side, double now_seconds,
                        double grace_seconds) noexcept {
    return allows_moving(forward * forward + side * side > 0.0001, now_seconds, grace_seconds);
}
bool SprintGate::allows_moving(bool moving, double now_seconds, double grace_seconds) noexcept {
    if (moving) {
        if (grace_seconds > 0.0) last_moving_at_ = now_seconds;
        return true;
    }
    if (grace_seconds <= 0.0 || !last_moving_at_.has_value()) {
        last_moving_at_.reset();
        return false;
    }
    const double elapsed = now_seconds - *last_moving_at_;
    if (elapsed >= 0.0 && elapsed <= grace_seconds) return true;
    last_moving_at_.reset();
    return false;
}
} // namespace omni
