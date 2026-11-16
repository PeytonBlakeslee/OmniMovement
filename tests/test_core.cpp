#include "omni_native_core.hpp"
#include "../native/velocity_write_transaction.hpp"
// Run actual assertions even in GitHub's Release configuration.
#ifdef NDEBUG
#undef NDEBUG
#endif
#include <cassert>
#include <cmath>
#include <cstdint>
#include <iostream>
#include <limits>
#include <unordered_map>
using omni::PrecisionFrame;
struct FakeIO final : omni::SpeedAccess {
    std::unordered_map<std::uintptr_t, double> pct{{7, 1.0}, {8, 2.0}};
    int reads = 0, writes = 0, fail_reads = 0, fail_writes = 0;
    bool read_sprinting_pct(std::uintptr_t owner, double& out) override {
        ++reads; auto it = pct.find(owner);
        if (fail_reads > 0) { --fail_reads; return false; }
        if (it == pct.end()) return false;
        out = it->second; return true;
    }
    bool write_sprinting_pct(std::uintptr_t owner, double value) override {
        ++writes; auto it = pct.find(owner);
        if (fail_writes > 0) { --fail_writes; return false; }
        if (it == pct.end()) return false;
        it->second = value; return true;
    }
};
PrecisionFrame live(std::uintptr_t owner = 7, double x = 0.5, double y = 0.0) {
    PrecisionFrame f;
    f.owner = owner;
    f.enabled = f.has_pc = f.has_pi = f.gamepad = true;
    f.pc_sprinting = f.pawn_sprinting = f.walking_physics = true;
    f.raw_joy_right = x; f.raw_joy_up = y; return f;
}
bool close(double a, double b, double tol = 1e-11) {
    return std::abs(a - b) <= tol;
}
int main() {
    // The same native write boundary used for live Velocity covers both
    // sequential reflected fields. Faults may occur before or after a write.
    for (int fail_at = 0; fail_at <= 4; ++fail_at) {
        float x = 75.0f, y = -20.0f;
        int calls = 0;
        const auto outcome = omni::commit_horizontal_velocity(
            [&](bool first, float next) {
                ++calls;
                if (fail_at == 1 && calls == 1) throw std::runtime_error("before X");
                if (fail_at == 3 && calls == 2) throw std::runtime_error("before Y");
                (first ? x : y) = next;
                if (fail_at == 2 && calls == 1) throw std::runtime_error("after X");
                if (fail_at == 4 && calls == 2) throw std::runtime_error("after Y");
            }, -120.0f, 30.0f);
        assert((outcome == omni::HorizontalWriteResult::committed) == (fail_at == 0));
        if (fail_at == 0) assert(x == -120.0f && y == 30.0f);
        if (fail_at == 1) assert(x == 75.0f && y == -20.0f);
        if (fail_at == 2) assert(x == -120.0f && y == -20.0f);
        if (fail_at == 3) assert(x == -120.0f && y == -20.0f);
        if (fail_at == 4) assert(x == -120.0f && y == 30.0f);
    }
    for (double x : {0.0, 0.004, 0.005, 0.05, 0.20, 0.50, 0.80, 0.99}) {
        FakeIO io; omni::PrecisionSprint p;
        const auto frame = live(7, x);
        const auto sampled = omni::precision_radius(frame);
        assert(sampled.has_value());
        p.update(frame, io, sampled);
        const double r = x < 0.005 ? 0.0 : x;
        const double want = 1.0 * (1.0 - 0.70 * (1.0 - (0.35 + 0.65 * std::pow(r, 1.5))));
        assert(close(io.pct.at(7), want)); p.restore(io);
        assert(close(io.pct.at(7), 1.0));
    }
    {
        FakeIO io; omni::PrecisionSprint p;
        assert(!omni::precision_radius(live(7, 0.8, 0.6)));
        assert(!omni::precision_radius(live(7, 0.998)));
        assert(omni::precision_radius(live(7, std::nextafter(0.998, 0.0))));
        assert(!omni::precision_radius(live(7, std::numeric_limits<double>::infinity())));
        auto zero_strength = live();
        zero_strength.strength = 0.0;
        assert(!omni::precision_radius(zero_strength));
        p.update(live(7, 0.5), io); p.update(live(7, 1.0), io);
        assert(close(io.pct.at(7), 1.0) && p.active_owner() == 0);
    }
    {
        FakeIO io; omni::PrecisionSprint p;
        p.update(live(), io);
        const auto edited = io.pct.at(7);
        const auto full_stick = live(7, 1.0);
        io.fail_writes = 1;
        p.update(full_stick, io);
        assert(p.active_owner() == 7 && close(io.pct.at(7), edited));
        p.update(full_stick, io);
        assert(p.active_owner() == 0 && close(io.pct.at(7), 1.0));
        const auto reads = io.reads;
        const auto writes = io.writes;
        p.update(full_stick, io);
        assert(io.reads == reads && io.writes == writes);
        p.update(live(), io);
        assert(p.active_owner() == 7 && close(io.pct.at(7), edited));
        io.pct[7] = 1.5;
        p.update(full_stick, io);
        assert(p.active_owner() == 0 && close(io.pct.at(7), 1.5));
    }
    {
        FakeIO io; omni::PrecisionSprint p;
        for (int i=0; i<2000; ++i) p.update(live(), io);
        assert(io.reads == 1 && io.writes == 1);
    }
    {
        FakeIO io; omni::PrecisionSprint p;
        auto frame = live();
        p.update(frame, io);
        const auto initial = io.pct.at(7);
        frame.strength = 0.4;
        p.update(frame, io);
        assert(io.pct.at(7) > initial);
        const auto weaker = io.pct.at(7);
        frame.raw_joy_up = 0.3;
        p.update(frame, io);
        assert(io.pct.at(7) > weaker);
        p.restore(io);
        io.pct[7] = 1.25;
        p.update(frame, io);
        assert(io.pct.at(7) < 1.25);
        p.restore(io);
        assert(close(io.pct.at(7), 1.25));
    }
    {
        FakeIO io; omni::PrecisionSprint p; p.update(live(), io);
        io.pct[7] = 1.75; p.update(live(7,0.70),io);
        assert(close(io.pct[7],1.75));
        assert(p.blocked_owner() == 7 && p.ownership_conflicts()==1);
        p.update(live(), io); assert(close(io.pct[7],1.75));
        p.reset(io); assert(p.blocked_owner()==0);
    }
    {
        FakeIO io; omni::PrecisionSprint p; p.update(live(),io);
        auto f=live(); f.gamepad=false; p.update(f,io);
        assert(close(io.pct[7],1.0));
        p.update(live(),io); p.update(live(8),io);
        assert(close(io.pct[7],1.0) && io.pct[8]<2.0);
        io.pct.erase(8); p.restore(io);
    }
    {
        FakeIO io; omni::PrecisionSprint p; p.update(live(),io);
        auto f=live(); f.raw_joy_right=std::numeric_limits<double>::quiet_NaN();
        p.update(f,io); assert(close(io.pct[7],1.0));
        f=live(); f.walking_physics=false; p.update(f,io);
        assert(close(io.pct[7],1.0));
    }
    {
        FakeIO io; omni::PrecisionSprint p; p.update(live(7,0.5),io);
        int writes=io.writes; p.update(live(7,0.5001),io);
        assert(io.writes==writes);
    }
    {
        FakeIO io; omni::PrecisionSprint p; p.update(live(),io);
        const double written=io.pct.at(7);
        io.fail_writes=1; p.restore(io);
        assert(p.active_owner()==7 && close(io.pct.at(7),written));
        p.restore(io);
        assert(p.active_owner()==0 && close(io.pct.at(7),1.0));
    }
    {
        FakeIO io; omni::PrecisionSprint p; p.update(live(),io);
        io.fail_reads=1; p.restore(io); assert(p.active_owner()==7);
        p.restore(io); assert(p.active_owner()==0 && close(io.pct.at(7),1.0));
    }
    {
        FakeIO io; omni::PrecisionSprint p; p.update(live(),io);
        io.pct[7]=1.5; p.restore(io);
        assert(p.active_owner()==0 && close(io.pct.at(7),1.5));
    }
    {
        FakeIO io; omni::PrecisionSprint p; p.update(live(),io);
        io.fail_writes=1; p.update(live(8),io);
        assert(p.active_owner()==7 && close(io.pct.at(8),2.0));
        p.update(live(8),io);
        assert(p.active_owner()==8 && close(io.pct.at(7),1.0));
        p.restore(io); assert(close(io.pct.at(8),2.0));
    }
    {
        omni::SprintGate g;
        assert(!g.allows(0,0,1,0.10));
        assert(g.allows(0.5,0,1,0.10));
        assert(g.allows(0,0,1.05,0.10));
        assert(g.allows(0,0,1.099,0.10));
        assert(!g.allows(0,0,1.101,0.10));
        assert(!g.allows(0,0,2,0));
        assert(!g.has_recent_movement());
        assert(!g.allows_moving(false, 0.0, 0.10));
        assert(g.allows_moving(true, 3.0, 0.10));
        assert(g.has_recent_movement());
        assert(g.allows_moving(false, 3.05, 0.10));
        assert(!g.allows_moving(false, 3.11, 0.10));
        assert(!g.has_recent_movement());
    }
    {
        omni::ReversalObserver r;
        auto first = r.observe(1.0, 0.0, 1.0, false);
        assert(!first.needs_kinematics && !first.candidate);
        auto same = r.observe(0.8, 0.0, 0.64, false);
        assert(!same.needs_kinematics && !same.candidate);
        auto opposite = r.observe(-0.8, 0.0, 0.64, false);
        assert(opposite.needs_kinematics && opposite.candidate);
        auto correction = r.observe(-0.7, 0.0, 0.49, true);
        assert(correction.needs_kinematics && !correction.candidate);
        auto released = r.observe(-0.1, 0.0, 0.01, true);
        assert(released.released && !released.needs_kinematics);
        r.reset();
        auto fresh = r.observe(-1.0, 0.0, 1.0, false);
        assert(!fresh.needs_kinematics && !fresh.candidate);
    }
    {
        omni::ReversalCorrection r;
        auto p = r.apply(-1.0,0.0,100.0,0.0,200.0,0.030,false,true);
        assert(p.has_velocity && close(p.vx,50.0) && close(p.vy,0.0));
        assert(r.active());
        p = r.apply(-1.0,0.0,100.0,0.0,200.0,0.030,false,false);
        assert(p.has_velocity && close(p.vx,0.0));
        p = r.apply(-1.0,0.0,100.0,0.0,200.0,0.030,false,false);
        assert(p.has_velocity && close(p.vx,-50.0));
        p = r.apply(-1.0,0.0,100.0,0.0,200.0,0.030,false,false);
        assert(p.has_velocity && close(p.vx,-100.0) && !r.active());
        // Near-instant completes immediately.
        p = r.apply(-1.0,0.0,100.0,0.0,200.0,0.016,true,true);
        assert(p.has_velocity && close(p.vx,-100.0) && !r.active());
        // Overspeed prevents correction, as does a direction change.
        p = r.apply(-1.0,0.0,300.0,0.0,200.0,0.016,false,true);
        assert(!p.has_velocity && !r.active());
        p = r.apply(-1.0,0.0,100.0,0.0,200.0,0.03,false,true);
        assert(p.has_velocity && r.active());
        p = r.apply(1.0,0.0,100.0,0.0,200.0,0.03,false,false);
        assert(!p.has_velocity && !r.active());
        p = r.apply(-1.0,0.0,100.0,0.0,200.0,0.12,false,true);
        assert(!p.has_velocity);
        p = r.apply(-1.0,0.0,std::numeric_limits<double>::quiet_NaN(),
                    0.0,200.0,0.016,false,true);
        assert(!p.has_velocity && !r.active());
    }
    {
        // Stage11: event-driven center path, tested separately from dormant
        // input-observer correction so C++ actually covers live gameplay.
        const auto reverse = omni::center_begin(-1.0, 0.0, 100.0, 0.0, 200.0);
        assert(reverse.accepted && close(reverse.speed, 100.0));
        assert(close(reverse.instant_vx, -100.0) && close(reverse.instant_vy, 0.0));
        assert(!omni::center_begin(1.0,0.0,100.0,0.0,200.0).accepted);
        assert(!omni::center_begin(-1.0,0.0,0.5,0.0,200.0).accepted);
        assert(!omni::center_begin(-1.0,0.0,300.0,0.0,200.0).accepted);
        assert(!omni::center_begin(-1.0,0.0,100.0,0.0,0.0).accepted);
        assert(!omni::center_begin(-1.0,0.0,
                 std::numeric_limits<double>::quiet_NaN(),0.0,200.0).accepted);
        auto a = omni::center_blend(100.0,0.0,-1.0,0.0,100.0,200.0,0.030);
        assert(a.accepted && close(a.vx,50.0) && !a.finished);
        a = omni::center_blend(100.0,0.0,-1.0,0.0,100.0,200.0,0.060);
        assert(a.accepted && close(a.vx,0.0) && !a.finished);
        a = omni::center_blend(100.0,0.0,-1.0,0.0,100.0,200.0,0.120);
        assert(a.accepted && close(a.vx,-100.0) && a.finished);
        a = omni::center_blend(100.0,0.0,-1.0,0.0,100.0,50.0,0.12);
        assert(a.accepted && close(a.vx,-50.0));
        assert(!omni::center_blend(100.0,0.0,-1.0,0.0,100.0,0.0,0.03).accepted);
        assert(!omni::center_blend(100.0,0.0,-1.0,0.0,100.0,200.0,
               std::numeric_limits<double>::quiet_NaN()).accepted);
    }
    {
        omni::CenterInputObserver o;
        auto x = o.observe(1.0,0.0,1.0,0.100,true);
        assert(!x.entered && !x.reversed);
        x = o.observe(0.0,0.0,1.02,0.100,true);
        assert(x.entered && !x.reversed);
        x = o.observe(-1.0,0.0,1.07,0.100,true);
        assert(!x.entered && x.reversed);
        x = o.observe(-1.0,0.0,1.08,0.100,true);
        assert(!x.reversed);
        x = o.observe(0.0,0.0,1.09,0.100,true);
        assert(x.entered);
        x = o.observe(-1.0,0.0,1.10,0.100,true);
        assert(!x.reversed);
        o.reset();
        o.observe(1.0,0.0,2.0,0.100,true);
        o.observe(0.0,0.0,2.01,0.100,true);
        x = o.observe(-1.0,0.0,2.20,0.100,true);
        assert(!x.reversed); // expired
        o.observe(1.0,0.0,3.0,0.100,true);
        o.observe(0.0,0.0,3.01,0.100,true);
        x = o.observe(-1.0,0.0,3.02,0.100,false);
        assert(!x.reversed); // disabled on Vanilla
    }
    {
        omni::AssistRecoveryReadiness assist;
        assert(!assist.retry(true));
        assert(assist.retry(true));
        assert(assist.retry(true));
        assert(!assist.retry(false));
        assert(!assist.retry(true));
        assist.reset_retry();
        assert(!assist.retry(true));

        assert(!assist.summon(true, 10.0));
        assert(!assist.summon(true, 10.049));
        assert(assist.summon(true, 10.050));
        assert(!assist.summon(false, 10.100));
        assert(!assist.summon(true, 11.0));
        assert(!assist.summon(true, std::numeric_limits<double>::quiet_NaN()));
        assist.reset_summon();
        assert(!assist.summon(true, 12.0));

        assert(!assist.travel(true));
        assert(!assist.travel(true));
        assert(assist.travel(true));
        assert(assist.travel(true));
        assert(!assist.travel(false));
        assert(!assist.travel(true));
        assist.reset();
        assert(!assist.retry(true));
        assert(!assist.travel(true));
        assert(!assist.summon(true, 15.0));
    }
    {
        omni::CombatFrame frame;
        omni::AssistRecoveryReadiness readiness;
        frame.moving = true;
        assert(omni::combat_retry_observe(frame, false, readiness) == 3);
        assert(omni::combat_retry_observe(frame, false, readiness) == 4);
        for (auto member : {&omni::CombatFrame::action_blocked,
                            &omni::CombatFrame::pending_zoom,
                            &omni::CombatFrame::weapon_put_down,
                            &omni::CombatFrame::weapon_zoomed}) {
            frame.*member = true;
            assert(omni::combat_retry_observe(frame, false, readiness) == 2);
            frame.*member = false;
            assert(omni::combat_retry_observe(frame, false, readiness) == 3);
            assert(omni::combat_retry_observe(frame, false, readiness) == 4);
        }
        assert(omni::combat_retry_observe(frame, true, readiness) == 2);
        assert(omni::combat_retry_observe(frame, false, readiness) == 3);
        // These observations cannot replace the engine's firing/reload guard.
        frame.firing_hint = frame.reloading = true;
        assert(omni::combat_retry_observe(frame, false, readiness) == 4);
        frame.moving = false;
        assert(omni::combat_retry_observe(frame, true, readiness) == 1);
        frame.sprinting = true;
        assert(omni::combat_retry_observe(frame, true, readiness) == 0);
        frame.sprinting = false;
        frame.moving = true;
        assert(omni::combat_retry_observe(frame, false, readiness) == 3);
    }
    {
        omni::SprintIntent intent;
        omni::IntentContext key{10, 20, 30, 1};
        omni::IntentFrame frame{true, false, true, true, true, false, false};
        auto result = intent.observe(key, true, frame);
        assert(result.phase == omni::IntentPhase::Debounce && !result.try_value);
        result = intent.observe(key, true, frame);
        assert(result.phase == omni::IntentPhase::RetryPending && result.write_required);
        frame.pending_try = frame.pending_wants = true;
        result = intent.observe(key, true, frame);
        assert(result.phase == omni::IntentPhase::RetryPending && !result.write_required);
        assert(!intent.last_frame().sprinting); // Pending is not confirmation.
        frame.pending_try = false; // Engine consumed it without BeginSprint.
        assert(intent.observe(key, true, frame).write_required);
        frame.permitted = false; // Engine CanSprint denied this frame.
        result = intent.observe(key, true, frame);
        assert(result.phase == omni::IntentPhase::Blocked && !result.try_value);
        frame.permitted = true;
        assert(intent.observe(key, true, frame).phase == omni::IntentPhase::Debounce);
        frame.sprinting = true;
        result = intent.observe(key, true, frame);
        assert(result.phase == omni::IntentPhase::Sprinting && !result.try_value);
    }
    {
        omni::SprintIntent intent;
        omni::IntentContext key{10, 20, 30, 1};
        omni::IntentFrame frame{true, false, true, false, false, false, true};
        auto result = intent.observe(key, true, frame);
        assert(result.phase == omni::IntentPhase::AirbornePending && result.airborne_hold);
        frame.moving = false; // Zero input in mid-air keeps remembered intent.
        assert(intent.observe(key, true, frame).airborne_hold);
        frame.grounded = true;
        result = intent.observe(key, true, frame);
        assert(result.phase == omni::IntentPhase::IdlePending && !result.airborne_hold);
        frame.moving = frame.permitted = true;
        assert(intent.observe(key, true, frame).phase == omni::IntentPhase::Debounce);
        frame.context_ready = false; // Menu, vehicle, injury, death, or cinematic.
        result = intent.observe(key, true, frame);
        assert(result.phase == omni::IntentPhase::Suspended && !result.try_value && result.armed);
        frame.context_ready = true;
        assert(intent.observe(key, true, frame).phase == omni::IntentPhase::Debounce);
        assert(intent.observe(key, false, frame).phase == omni::IntentPhase::Walk);
        assert(!intent.armed());
    }
    {
        omni::SprintIntent intent;
        omni::IntentContext key{10, 20, 30, 1};
        assert(intent.press(key, true) && !intent.armed());
        assert(!intent.press(key, false) && !intent.armed());
        intent.release(key, false);
        assert(intent.press(key, false) && intent.armed());
        omni::IntentFrame frame{true, false, true, true, true, false, false};
        assert(intent.observe(key, true, frame).phase == omni::IntentPhase::Debounce);
        assert(intent.observe(key, true, frame).phase == omni::IntentPhase::RetryPending);
        for (int part = 0; part < 4; ++part) {
            if (part == 0) ++key.controller;
            if (part == 1) ++key.input;
            if (part == 2) ++key.pawn;
            if (part == 3) ++key.epoch;
            assert(intent.observe(key, true, frame).phase == omni::IntentPhase::Debounce);
            assert(intent.observe(key, true, frame).phase == omni::IntentPhase::RetryPending);
        }
        intent.reset(false); // Backend handoff carries the selected Walk mode.
        assert(intent.observe(key, false, frame).phase == omni::IntentPhase::Walk);
        intent.reset(true);
        assert(intent.observe(key, true, frame).phase == omni::IntentPhase::Debounce);
        intent.reset_retry();
        assert(intent.observe(key, true, frame).phase == omni::IntentPhase::Debounce);
        key.pawn = 0;
        assert(intent.observe(key, true, frame).phase == omni::IntentPhase::Suspended);
    }
    {
        omni::MotionGeneration generation;
        omni::MotionContext key{{10, 20, 30, 1}, 1, false};
        assert(generation.bind(key));
        assert(!generation.bind(key) && generation.matches(key));
        for (int part = 0; part < 6; ++part) {
            auto next = key;
            if (part == 0) ++next.objects.controller;
            if (part == 1) ++next.objects.input;
            if (part == 2) ++next.objects.pawn;
            if (part == 3) ++next.objects.epoch;
            if (part == 4) next.physics = 2;
            if (part == 5) next.gamepad = true;
            assert(!generation.matches(next));
            assert(generation.bind(next) && !generation.matches(key));
            assert(generation.bind(key));
        }
        generation.reset();
        assert(!generation.matches(key));
        key.objects.pawn = 0;
        assert(generation.bind(key) && !generation.matches(key));
    }
    {
        assert(omni::valid_horizontal_write(0, 0, 440));
        assert(omni::valid_horizontal_write(-440, 0, 440));
        assert(omni::valid_horizontal_write(0, 444.4, 440));
        assert(!omni::valid_horizontal_write(445, 0, 440));
        assert(!omni::valid_horizontal_write(1, 0, 0));
        assert(!omni::valid_horizontal_write(INFINITY, 0, 440));
        assert(!omni::valid_horizontal_write(0, NAN, 440));
        assert(!omni::valid_horizontal_write(1, 0, INFINITY));
        assert(!omni::valid_horizontal_write(1e100, 0, 1e101));
    }
    std::cout << "ALL 19 native core test groups passed\n";
}
