#include "DriveDynamics.h"
#include <cassert>
#include <iostream>
#include <limits>

int main()
{
    using namespace DynaDrive;
    double speed = 0;
    for (int i = 0; i < 3600; ++i) speed = SpeedStep(speed, 1, false, 1.0 / 60, 0);
    assert(std::abs(speed * .036 - 50) < .001);
    for (int i = 0; i < 3600; ++i) speed = SpeedStep(speed, -1, false, 1.0 / 60, 0);
    assert(std::abs(speed * .036 + 20) < .001);

    // Braking must not overshoot through zero, including reverse.
    assert(SpeedStep(3, 0, true, 1.0 / 60, 0) == 0);
    assert(SpeedStep(-3, 0, true, 1.0 / 60, 0) == 0);
    assert(SpeedStep(3, -1, false, 1.0 / 60, 0) == 0);
    assert(SpeedStep(100, 0, true, -1, 0) == 100);
    assert(YawStepDegrees(0, 1, 1.0 / 60) == 0);
    assert(YawStepDegrees(100, 1, 1.0 / 60) > 0);
    assert(YawStepDegrees(-100, 1, 1.0 / 60) < 0);
    assert(SpeedStep(100, std::numeric_limits<double>::quiet_NaN(), false, .01, 0) == 0);
    assert(YawStepDegrees(100, std::numeric_limits<double>::infinity(), .01) == 0);

    auto simulate = [](int fps)
    {
        double value = 0;
        for (int frame = 0; frame < fps * 4; ++frame)
        {
            const int steps = static_cast<int>(std::ceil(60.0 / fps));
            for (int step = 0; step < steps; ++step)
                value = SpeedStep(value, 1, false, 1.0 / (fps * steps), 0);
        }
        return value;
    };
    assert(std::abs(simulate(30) - simulate(60)) < 1e-6);
    assert(std::abs(simulate(144) - simulate(60)) < 1e-6);
    assert(SpeedStep(100, 1, false, 1.0 / 60, .1) < SpeedStep(100, 1, false, 1.0 / 60, 0));
    std::cout << "Drive dynamics: speed limits, braking, reverse, steering, slope and timestep checks passed\n";
}
