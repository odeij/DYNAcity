#include "DynaStreetInstances.h"
#include "Components/HierarchicalInstancedStaticMeshComponent.h"

ADynaStreetInstances::ADynaStreetInstances()
{
    PrimaryActorTick.bCanEverTick = false;
    Instances = CreateDefaultSubobject<UHierarchicalInstancedStaticMeshComponent>(TEXT("MappedInstances"));
    SetRootComponent(Instances);
    Instances->SetMobility(EComponentMobility::Static);
    Instances->SetCollisionEnabled(ECollisionEnabled::NoCollision);
    Instances->SetCanEverAffectNavigation(false);
}

void ADynaStreetInstances::SetStreetMesh(UStaticMesh* Mesh) { Instances->SetStaticMesh(Mesh); }
int32 ADynaStreetInstances::AddStreetInstance(const FTransform& Transform, const FString& SourceId)
{
    if (Transform.ContainsNaN()) return INDEX_NONE;
    const int32 Index = Instances->AddInstance(Transform, true);
    if (Index == SourceIds.Num()) SourceIds.Add(SourceId);
    else if (Index >= 0)
    {
        SourceIds.SetNum(Instances->GetInstanceCount());
        SourceIds[Index] = SourceId;
    }
    return Index;
}
void ADynaStreetInstances::ClearStreetInstances() { Instances->ClearInstances(); SourceIds.Reset(); }
void ADynaStreetInstances::SetStreetCollision(bool Enabled)
{
    Instances->SetCollisionProfileName(TEXT("BlockAll"));
    Instances->SetCollisionEnabled(Enabled ? ECollisionEnabled::QueryAndPhysics : ECollisionEnabled::NoCollision);
}
void ADynaStreetInstances::SetStreetCullDistance(int32 StartCm, int32 EndCm)
{ Instances->SetCullDistances(FMath::Max(0, StartCm), FMath::Max(StartCm, EndCm)); }
int32 ADynaStreetInstances::GetStreetInstanceCount() const { return Instances->GetInstanceCount(); }
