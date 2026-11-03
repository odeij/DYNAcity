#include "DynaMobilityWorld.h"
#include "Components/InstancedStaticMeshComponent.h"
#include "Engine/StaticMesh.h"
#include "Engine/World.h"
#include "Misc/AutomationTest.h"

#if WITH_DEV_AUTOMATION_TESTS
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FDynaFleetTest, "DynaCity.Traffic.FleetLifecycle",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)
bool FDynaFleetTest::RunTest(const FString& Parameters)
{
    UWorld* World=UWorld::CreateWorld(EWorldType::Game,false);
    if(!TestNotNull(TEXT("World"),World))return false;
    auto* Actor=World->SpawnActor<ADynaMobilityWorld>();
    UStaticMesh* Mesh=LoadObject<UStaticMesh>(nullptr,TEXT("/Engine/BasicShapes/Cube.Cube"));
    if(!TestNotNull(TEXT("Actor"),Actor)||!TestNotNull(TEXT("Mesh"),Mesh)) { World->DestroyWorld(false);return false; }
    Actor->Configure(TEXT("{\"roads\":[]}"),FVector::ZeroVector,FVector(100,0,0),FVector(0,100,0),FVector(0,0,100),Mesh);
    Actor->ConfigureTrafficFleet({Mesh});
    TestEqual(TEXT("Additional group created"),Actor->AdditionalVehicles.Num(),1);
    FString Cars;
    for(int32 I=0;I<32;++I) {
        if(I)Cars+=TEXT(",");
        Cars+=FString::Printf(TEXT("{\"id\":\"car-%d\",\"position\":[0,0,0],\"heading_deg\":0}"),I);
    }
    FString Frames=FString::Printf(TEXT("{\"frames\":[{\"time_s\":1,\"vehicles\":[%s]},{\"time_s\":2,\"vehicles\":[%s]}]}"),*Cars,*Cars);
    TestTrue(TEXT("Frames accepted"),Actor->AppendFrames(Frames));
    Actor->Tick(0.1f);
    int32 First=Actor->Vehicles->GetInstanceCount(),Second=Actor->AdditionalVehicles[0]->GetInstanceCount();
    TestTrue(TEXT("Both groups receive cars"),First>0 && Second>0);
    TestEqual(TEXT("All cars rendered"),First+Second,32);
    Actor->Tick(0.1f);
    TestEqual(TEXT("Stable first group"),Actor->Vehicles->GetInstanceCount(),First);
    TestEqual(TEXT("Stable second group"),Actor->AdditionalVehicles[0]->GetInstanceCount(),Second);
    Actor->ClearTraffic();
    TestEqual(TEXT("Default group cleared"),Actor->Vehicles->GetInstanceCount(),0);
    TestEqual(TEXT("Additional group cleared"),Actor->AdditionalVehicles[0]->GetInstanceCount(),0);
    Actor->ConfigureTrafficTypes({{TEXT("car"),0},{TEXT("bus"),1}});
    const FString Typed=TEXT("{\"frames\":[{\"time_s\":1,\"vehicles\":[{\"id\":\"a\",\"type_id\":\"car\",\"position\":[0,0,0],\"heading_deg\":0},{\"id\":\"b\",\"type_id\":\"bus\",\"position\":[0,0,0],\"heading_deg\":0},{\"id\":\"c\",\"type_id\":\"unknown\",\"position\":[0,0,0],\"heading_deg\":0}]},{\"time_s\":2,\"vehicles\":[]}]}");
    TestTrue(TEXT("Typed frames accepted"),Actor->AppendFrames(Typed));
    Actor->Tick(0.1f);
    TestEqual(TEXT("Car uses default mesh"),Actor->Vehicles->GetInstanceCount(),1);
    TestEqual(TEXT("Bus uses mapped mesh"),Actor->AdditionalVehicles[0]->GetInstanceCount(),1);
    Actor->ClearTraffic();
    const FString Painted=TEXT("{\"frames\":[{\"time_s\":1,\"vehicles\":[{\"id\":\"painted\",\"type_id\":\"car\",\"position\":[0,0,0],\"heading_deg\":0,\"paint_rgb\":[0.2,0.4,0.6]}]},{\"time_s\":2,\"vehicles\":[]}]}");
    TestTrue(TEXT("Painted frame accepted"),Actor->AppendFrames(Painted));
    Actor->Tick(0.1f);
    TestEqual(TEXT("Paint RGB and mask allocated"),Actor->Vehicles->NumCustomDataFloats,4);
    TestEqual(TEXT("Paint red uploaded"),Actor->Vehicles->PerInstanceSMCustomData[0],0.2f);
    TestEqual(TEXT("Paint override enabled"),Actor->Vehicles->PerInstanceSMCustomData[3],1.f);
    Actor->Tick(0.1f);
    TestEqual(TEXT("Paint remains stable"),Actor->Vehicles->PerInstanceSMCustomData[2],0.6f);
    Actor->ClearTraffic();
    TestFalse(TEXT("Out-of-range paint rejected"),Actor->AppendFrames(Painted.Replace(TEXT("0.2,0.4,0.6"),TEXT("1.2,0.4,0.6"))));
    Actor->ConfigureTrafficFleet({});
    TestEqual(TEXT("Single mesh restored"),Actor->AdditionalVehicles.Num(),0);
    World->DestroyWorld(false);
    return true;
}
#endif
