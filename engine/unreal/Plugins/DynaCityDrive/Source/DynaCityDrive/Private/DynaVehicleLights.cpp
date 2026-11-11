#include "DynaVehicleLights.h"
#include "Components/InstancedStaticMeshComponent.h"
#include "Engine/StaticMesh.h"
#include "Materials/MaterialInterface.h"
#include "GameFramework/Actor.h"

void UDynaVehicleLights::Configure(const TArray<UMaterialInterface*>& Materials)
{
    if(Materials.Num()!=4 || Materials.Contains(nullptr))return;
    if(Lamps.IsEmpty())for(int32 I=0;I<4;++I) {
        auto* Mesh=NewObject<UInstancedStaticMeshComponent>(GetOwner());
        Mesh->SetupAttachment(GetOwner()->GetRootComponent());
        Mesh->SetMobility(EComponentMobility::Movable);
        Mesh->SetCollisionEnabled(ECollisionEnabled::NoCollision);
        Mesh->SetCanEverAffectNavigation(false);Mesh->SetCastShadow(false);
        Mesh->SetStaticMesh(LoadObject<UStaticMesh>(nullptr,TEXT("/Engine/BasicShapes/Cube.Cube")));
        GetOwner()->AddInstanceComponent(Mesh);Mesh->RegisterComponent();Lamps.Add(Mesh);
    }
    for(int32 I=0;I<4;++I)Lamps[I]->SetMaterial(0,Materials[I]);
    Reset();Flush();
}

void UDynaVehicleLights::Reset() { Pending.SetNum(4);for(auto& Items:Pending)Items.Reset(); }

void UDynaVehicleLights::AddVehicle(const FTransform& Transform,const FBox& Bounds,int32 Signals,double Time,bool Night)
{
    if(Lamps.Num()!=4 || !Bounds.IsValid)return;
    const FVector Centre=Bounds.GetCenter(),Extent=Bounds.GetExtent();
    const double Y=Extent.Y*.78,Z=Bounds.Min.Z+Extent.Z*.8;
    auto Add=[&](int32 Group,double X,double Side,double Width) {
        const FVector Location=Transform.TransformPosition(FVector(X,Centre.Y+Side*Y,Z));
        Pending[Group].Emplace(Transform.GetRotation(),Location,FVector(.045,Width/100.,.075));
    };
    for(int32 Side:{-1,1}) {
        // UE X forward, Y right; bounds preserve each prepared fleet mesh's dimensions.
        if(Signals>=0 && (Signals&8))Add(1,Bounds.Min.X-2,Side,16);
        else if(Night)Add(0,Bounds.Min.X-2,Side,16);
        if(Night || (Signals>=0 && (Signals&16)))Add(2,Bounds.Max.X+2,Side,16);
        const bool Indicate=Signals>=0 && ((Signals&4) || (Signals&(Side<0?2:1)));
        if(Indicate && FMath::Fmod(FMath::Max(Time,0.0),.8)<.4) {
            Add(3,Bounds.Min.X-5,Side,9);Add(3,Bounds.Max.X+5,Side,9);
        }
    }
}

void UDynaVehicleLights::Flush()
{
    for(int32 I=0;I<Lamps.Num();++I) {
        UInstancedStaticMeshComponent* Mesh=Lamps[I];const auto& Items=Pending[I];
        while(Mesh->GetInstanceCount()<Items.Num())Mesh->AddInstance(FTransform::Identity);
        while(Mesh->GetInstanceCount()>Items.Num())Mesh->RemoveInstance(Mesh->GetInstanceCount()-1);
        if(!Items.IsEmpty())Mesh->BatchUpdateInstancesTransforms(0,Items,true,true,true);
    }
}

void UDynaVehicleLights::OnUnregister()
{
    for(UInstancedStaticMeshComponent* Mesh:Lamps)if(Mesh)Mesh->DestroyComponent();
    Lamps.Reset();Super::OnUnregister();
}
