#pragma once

// Pure, SDK-free boundary for sequential horizontal velocity writes.
// UnrealSDK WrappedStruct::set writes live struct storage field by field.
// An exception after the first field therefore cannot authorize a second
// Python write in the same frame.
namespace omni {

enum class HorizontalWriteResult {
    committed,
    uncertain,
};

template <typename WriteField>
[[nodiscard]] HorizontalWriteResult commit_horizontal_velocity(
    WriteField&& write_field, float vx, float vy) noexcept {
    try {
        write_field(true, vx);
        write_field(false, vy);
        return HorizontalWriteResult::committed;
    } catch (...) {
        // Either field may already have changed. Never blind-rollback or
        // signal that a Python fallback can apply the same correction.
        return HorizontalWriteResult::uncertain;
    }
}

}  // namespace omni
