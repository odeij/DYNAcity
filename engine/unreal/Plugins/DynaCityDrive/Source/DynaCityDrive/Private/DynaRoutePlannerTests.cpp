#include "DynaRoutePlanner.h"
#include "DynaMobilityWorld.h"
#include "Misc/AutomationTest.h"

#if WITH_DEV_AUTOMATION_TESTS
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FDynaRoutePlannerTest, "DynaCity.Route.Planner",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)
bool FDynaRoutePlannerTest::RunTest(const FString& Parameters)
{
    // 0 Main St east, 1 Side St north, 2 Far Rd east, 3 an island, 4 a lane whose end
    // stops 5 m short of Main St, 5 a 20 m longer bypass west and north of Side St.
    const TArray<FString> Names={TEXT("Main St"),TEXT("Side St"),TEXT("Far Rd"),TEXT("Island"),TEXT("Gap Ln"),TEXT("Bypass")};
    TArray<FDynaMapSegment> Segments;
    auto Path=[&](int32 Road,std::initializer_list<FVector2D> Points)
    {
        const TArray<FVector2D> P(Points);
        for(int32 I=1;I<P.Num();++I)Segments.Add({P[I-1],P[I],Road});
    };
    Path(0,{{0,0},{100,0},{200,0}});
    Path(1,{{100,0},{100,100}});
    Path(2,{{100,100},{300,100}});
    Path(3,{{1000,1000},{1100,1000}});
    Path(4,{{200,5},{200,60}});
    Path(5,{{0,0},{0,110},{100,110},{100,100}});
    TArray<TArray<float>> Speeds; Speeds.SetNum(Names.Num());
    for(auto& Row:Speeds)Row.Init(36.f,24);           // 10 m/s
    Speeds[1][8]=3.6f;                                // Side St crawls at 08:00

    FDynaRoutePlanner Planner; Planner.Build(Segments,Speeds);
    TestTrue(TEXT("Gap lane stitched to Main St"),Planner.JunctionLinks()>=1);

    FDynaRoute Route; FString Error;
    TestTrue(TEXT("Plans at 14:00"),Planner.Plan(FVector2D(0,-3),FVector2D(300,103),14,Names,Route,Error));
    TestEqual(TEXT("Fastest at 14:00 is the 400 m Side St path"),Route.Length,400.0,.5);
    TestEqual(TEXT("Travel time at 10 m/s"),Route.Seconds,40.0,.1);
    if(TestTrue(TEXT("Two turns and arrival"),Route.Maneuvers.Num()==3))
    {
        TestEqual(TEXT("Left onto Side St"),Route.Maneuvers[0].Turn,-1);
        TestEqual(TEXT("Side St named"),Route.Maneuvers[0].Road,FString(TEXT("Side St")));
        TestEqual(TEXT("Right onto Far Rd"),Route.Maneuvers[1].Turn,1);
        TestTrue(TEXT("Ends in arrival"),Route.Maneuvers[2].Arrive);
        TestEqual(TEXT("Destination road"),Route.DestinationRoad,FString(TEXT("Far Rd")));
    }
    int32 Hint=0; double Off=0;
    TestEqual(TEXT("Progress along Main St"),Route.Project(FVector2D(50,10),Hint,Off),50.0,.5);
    TestEqual(TEXT("Off-route distance"),Off,10.0,.5);

    TestTrue(TEXT("Plans at 08:00"),Planner.Plan(FVector2D(0,-3),FVector2D(300,103),8,Names,Route,Error));
    TestEqual(TEXT("Congested Side St avoided via the longer bypass"),Route.Length,420.0,.5);
    TestEqual(TEXT("Slowest stretch is not on the bypass route"),Route.SlowKmh,36.0,.01);

    TestTrue(TEXT("Routes across the stitched gap"),Planner.Plan(FVector2D(0,0),FVector2D(200,60),14,Names,Route,Error));
    TestEqual(TEXT("Gap route length"),Route.Length,260.0,1.0);

    TestFalse(TEXT("Island is unreachable"),Planner.Plan(FVector2D(0,0),FVector2D(1050,1000),14,Names,Route,Error));
    TestFalse(TEXT("Error explains why"),Error.IsEmpty());
    TestFalse(TEXT("No road near the destination"),Planner.Plan(FVector2D(0,0),FVector2D(5000,5000),14,Names,Route,Error));
    return true;
}
#endif
