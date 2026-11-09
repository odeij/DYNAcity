#include "DynaVehicleLights.h"
#include "DynaMobilityWorld.h"
#include "DynaDrivePawn.h"
#include "Components/InstancedStaticMeshComponent.h"
#include "Engine/StaticMesh.h"
#include "Engine/World.h"
#include "Materials/MaterialInterface.h"
#include "Misc/AutomationTest.h"
#include <limits>

#if WITH_DEV_AUTOMATION_TESTS
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FDynaVehicleLightsTest,"DynaCity.Traffic.VehicleLights",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)
bool FDynaVehicleLightsTest::RunTest(const FString& Parameters)
{
    UWorld* World=UWorld::CreateWorld(EWorldType::Game,false);
    if(!TestNotNull(TEXT("World"),World))return false;
    auto* Actor=World->SpawnActor<ADynaMobilityWorld>();
    auto* Lights=Actor->VehicleLights.Get();
    auto* Material=LoadObject<UMaterialInterface>(nullptr,TEXT("/Engine/BasicShapes/BasicShapeMaterial.BasicShapeMaterial"));
    Lights->Configure({Material,Material,Material,Material});
    const FBox Bounds(FVector(-220,-90,0),FVector(220,90,150));
    auto Draw=[&](int32 Bits,double Time,bool Night=false) {
        Lights->Reset();Lights->AddVehicle(FTransform::Identity,Bounds,Bits,Time,Night);Lights->Flush();
    };
    Draw(2,0);TestEqual(TEXT("Left front/rear lit"),Lights->Lamps[3]->GetInstanceCount(),2);
    FTransform Left;Lights->Lamps[3]->GetInstanceTransform(0,Left,true);
    TestTrue(TEXT("Left is negative local Y"),Left.GetLocation().Y<0);
    Draw(1,0);FTransform Right;Lights->Lamps[3]->GetInstanceTransform(0,Right,true);
    TestTrue(TEXT("Right is positive local Y"),Right.GetLocation().Y>0);
    Draw(4,0);TestEqual(TEXT("Hazards light all corners"),Lights->Lamps[3]->GetInstanceCount(),4);
    Draw(4,.5);TestEqual(TEXT("Indicator off half cycle"),Lights->Lamps[3]->GetInstanceCount(),0);
    Draw(10,0);TestEqual(TEXT("Brakes and left indicator coexist"),Lights->Lamps[1]->GetInstanceCount(),2);
    TestEqual(TEXT("Indicator remains amber"),Lights->Lamps[3]->GetInstanceCount(),2);
    Draw(0,0);TestEqual(TEXT("Authoritative zero clears brake"),Lights->Lamps[1]->GetInstanceCount(),0);
    Draw(-1,0,true);TestEqual(TEXT("Unknown source does not invent indicators"),Lights->Lamps[3]->GetInstanceCount(),0);
    TestEqual(TEXT("Night rear running lamps"),Lights->Lamps[0]->GetInstanceCount(),2);
    TestEqual(TEXT("Night front lamps"),Lights->Lamps[2]->GetInstanceCount(),2);
    Actor->ClearTraffic();TestEqual(TEXT("Session clear removes lamps"),Lights->Lamps[2]->GetInstanceCount(),0);
    auto* Cube=LoadObject<UStaticMesh>(nullptr,TEXT("/Engine/BasicShapes/Cube.Cube"));
    Actor->Configure(TEXT("{\"roads\":[]}"),FVector::ZeroVector,FVector(100,0,0),FVector(0,100,0),FVector(0,0,100),Cube);
    const FString Frames=TEXT("{\"frames\":[{\"time_s\":0,\"vehicles\":[{\"id\":\"a\",\"position\":[0,0,0],\"heading_deg\":0,\"light_bits\":2}]},{\"time_s\":0.4,\"vehicles\":[{\"id\":\"a\",\"position\":[0,0,0],\"heading_deg\":0,\"light_bits\":8}]}]}");
    TestTrue(TEXT("Source states accepted"),Actor->AppendFrames(Frames));
    Actor->Tick(.1f);TestEqual(TEXT("No premature future brake state"),Lights->Lamps[1]->GetInstanceCount(),0);
    Actor->Tick(.5f);TestEqual(TEXT("Final sample brakes shown"),Lights->Lamps[1]->GetInstanceCount(),2);
    Actor->ClearTraffic();TestEqual(TEXT("Restart clears final brakes"),Lights->Lamps[1]->GetInstanceCount(),0);
    TestFalse(TEXT("Fractional bitmask rejected"),Actor->AppendFrames(Frames.Replace(TEXT("\"light_bits\":2"),TEXT("\"light_bits\":2.5"))));
    auto* Pawn=World->SpawnActor<ADynaDrivePawn>();
    Pawn->ToggleLeftSignal();TestEqual(TEXT("Player left selected"),Pawn->TurnSignal,2);
    Pawn->ToggleLeftSignal();TestEqual(TEXT("Player repeat cancels"),Pawn->TurnSignal,0);
    Pawn->ToggleRightSignal();Pawn->ToggleHazards();TestEqual(TEXT("Hazards replace turn"),Pawn->TurnSignal,4);
    Pawn->SetTimeOfDay(18.2f);TestTrue(TEXT("Sunset enables running lights"),Pawn->NightMode);
    Pawn->SetTimeOfDay(14);TestFalse(TEXT("Afternoon disables running lights"),Pawn->NightMode);
    Pawn->SetTimeOfDay(std::numeric_limits<float>::quiet_NaN());TestEqual(TEXT("Invalid hour ignored"),Pawn->TimeOfDay,14.f);
    World->DestroyWorld(false);return true;
}
#endif
