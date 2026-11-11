#pragma once
#include <algorithm>
#include <cmath>

// Centimetres and seconds, matching Unreal. This is an exploration controller,
// not a calibrated tyre, drivetrain or traffic behaviour model.
namespace DynaDrive
{
    inline double MoveToward(double value, double target, double amount)
    {
        return value < target ? std::min(value + amount, target) : std::max(value - amount, target);
    }

    inline double SpeedStep(double speed, double throttle, bool brake, double dt, double uphill)
    {
        if (!std::isfinite(speed) || !std::isfinite(throttle) || !std::isfinite(dt) || !std::isfinite(uphill)) return 0;
        throttle = std::clamp(throttle, -1.0, 1.0);
        dt = std::clamp(dt, 0.0, 1.0 / 60.0);
        if (brake) return MoveToward(speed, 0.0, 900.0 * dt);
        if (std::abs(throttle) < 0.01) return MoveToward(speed, 0.0, 100.0 * dt);
        // Opposite throttle brakes before reversing, rather than changing sign instantly.
        if (speed * throttle < 0.0) return MoveToward(speed, 0.0, 650.0 * dt);
        const double acceleration = throttle * 250.0 - 981.0 * uphill;
        // Free exploration must not impose a citywide 50 km/h governor.
        // SUMO vehicles retain their authoritative road/time-specific speeds.
        // Keep the reverse manoeuvring bound; forward speed follows input/slope.
        return std::max(speed + acceleration * dt, -555.5556);
    }

    inline double YawStepDegrees(double speed, double steering, double dt)
    {
        if (!std::isfinite(speed) || !std::isfinite(steering) || !std::isfinite(dt)) return 0;
        // Bicycle model with 2.71 m wheelbase and 28 degrees maximum steering.
        return speed / 271.0 * std::tan(std::clamp(steering, -1.0, 1.0) * 0.48869219)
            * std::clamp(dt, 0.0, 1.0 / 60.0) * 57.295779513;
    }
}
