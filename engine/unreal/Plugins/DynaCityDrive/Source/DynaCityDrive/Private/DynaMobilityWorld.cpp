#include "DynaMobilityWorld.h"
#include "DynaMinimapComponent.h"
#include "DynaVehicleLights.h"
#include "DynaDrivePawn.h"
#include "Components/InstancedStaticMeshComponent.h"
#include "GameFramework/HUD.h"
#include "GameFramework/Pawn.h"
#include "Kismet/GameplayStatics.h"
#include "Engine/Engine.h"
#include "Engine/World.h"
#include "Engine/StaticMesh.h"
#include "Dom/JsonObject.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "Misc/Crc.h"

namespace {
bool Read(const FString& Text, TSharedPtr<FJsonObject>& Out)
{ return FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text), Out) && Out.IsValid(); }
FVector Point(const TArray<TSharedPtr<FJsonValue>>& P)
{ return FVector(P[0]->AsNumber(),P[1]->AsNumber(),P[2]->AsNumber()); }

}

ADynaMobilityWorld::ADynaMobilityWorld()
{
    PrimaryActorTick.bCanEverTick=true;
    PrimaryActorTick.TickInterval=1.0f/30;
    Minimap=CreateDefaultSubobject<UDynaMinimapComponent>(TEXT("GeographicMinimap"));
    Vehicles=CreateDefaultSubobject<UInstancedStaticMeshComponent>(TEXT("SUMOVehicles"));
    SetRootComponent(Vehicles);
    Vehicles->SetMobility(EComponentMobility::Movable);
    Vehicles->SetCollisionEnabled(ECollisionEnabled::NoCollision);
    Vehicles->SetCanEverAffectNavigation(false);
    Vehicles->SetCastShadow(false);
    Vehicles->NumCustomDataFloats=4;
    VehicleLights=CreateDefaultSubobject<UDynaVehicleLights>(TEXT("VehicleLights"));
}

void ADynaMobilityWorld::ConfigureLights(const TArray<UMaterialInterface*>& Materials)
{ VehicleLights->Configure(Materials); }

void ADynaMobilityWorld::Configure(const FString& MapJson,FVector Origin,FVector East,FVector North,FVector Up,UStaticMesh* VehicleMesh)
{
    BasisOrigin=Origin;BasisEast=East;BasisNorth=North;BasisUp=Up;
    Minimap->ConfigureGeography(MapJson);
    Segments.Reset();RoadGrid.Reset();RoadNames.Reset();RoadCount=0;LastStreetRoad=INDEX_NONE;
    Vehicles->SetStaticMesh(VehicleMesh);
    ConfigureTrafficFleet({});
    TSharedPtr<FJsonObject> Data;
    if(!Read(MapJson,Data))return;
    const TArray<TSharedPtr<FJsonValue>>* Roads;
    if(!Data->TryGetArrayField(TEXT("roads"),Roads))return;
    RoadCount=Roads->Num();
    for(const auto& Value:*Roads) {
        const auto Object=Value->AsObject();
        const auto& Path=Object->GetArrayField(TEXT("path"));
        FString Name;Object->TryGetStringField(TEXT("name"),Name);
        const int32 RoadIndex=RoadNames.Add(Name);
        for(int I=1;I<Path.Num();++I) {
            const auto& A=Path[I-1]->AsArray();const auto& B=Path[I]->AsArray();
            FDynaMapSegment S{FVector2D(A[0]->AsNumber(),A[1]->AsNumber()),FVector2D(B[0]->AsNumber(),B[1]->AsNumber()),RoadIndex};
            int Index=Segments.Add(S);
            for(int X=FMath::FloorToInt(FMath::Min(S.A.X,S.B.X)/200);X<=FMath::FloorToInt(FMath::Max(S.A.X,S.B.X)/200);++X)
                for(int Y=FMath::FloorToInt(FMath::Min(S.A.Y,S.B.Y)/200);Y<=FMath::FloorToInt(FMath::Max(S.A.Y,S.B.Y)/200);++Y)
                    RoadGrid.FindOrAdd(FIntPoint(X,Y)).Add(Index);
        }
    }
}

void ADynaMobilityWorld::ConfigureTrafficFleet(const TArray<UStaticMesh*>& AdditionalMeshes)
{
    // Tear down only components owned by this renderer; the root remains the
    // default mesh so an empty list preserves the current single-car behavior.
    Vehicles->ClearInstances();
    for(UInstancedStaticMeshComponent* Component:AdditionalVehicles)
        if(Component)Component->DestroyComponent();
    AdditionalVehicles.Reset();
    TrafficTypeGroups.Reset();
    for(UStaticMesh* Mesh:AdditionalMeshes) {
        if(!Mesh || AdditionalVehicles.Num()>=15)continue;
        auto* Component=NewObject<UInstancedStaticMeshComponent>(this);
        Component->SetupAttachment(Vehicles);
        Component->SetMobility(EComponentMobility::Movable);
        Component->SetCollisionEnabled(ECollisionEnabled::NoCollision);
        Component->SetCanEverAffectNavigation(false);
        Component->SetCastShadow(false);
        Component->NumCustomDataFloats=4;
        Component->SetStaticMesh(Mesh);
        AddInstanceComponent(Component);
        Component->RegisterComponent();
        AdditionalVehicles.Add(Component);
    }
}

void ADynaMobilityWorld::ConfigureTrafficTypes(const TMap<FString, int32>& TypeGroups)
{
    TrafficTypeGroups.Reset();
    for(const auto& Pair:TypeGroups)
        if(!Pair.Key.IsEmpty() && Pair.Value>=0 && Pair.Value<=AdditionalVehicles.Num())
            TrafficTypeGroups.Add(Pair.Key,Pair.Value);
}

FVector2D ADynaMobilityWorld::Local(const FVector& Position) const
{
    FVector D=Position-BasisOrigin;
    return FVector2D(FVector::DotProduct(D,BasisEast)/FMath::Max(BasisEast.SizeSquared(),1.0),
        FVector::DotProduct(D,BasisNorth)/FMath::Max(BasisNorth.SizeSquared(),1.0));
}

FString ADynaMobilityWorld::StreetAt(const FVector& Position,double MaxDistanceM)
{
    const FVector2D P=Local(Position);
    const int32 CX=FMath::FloorToInt(P.X/200),CY=FMath::FloorToInt(P.Y/200);
    TMap<int32,double> Nearest; // road index -> closest distance in metres
    for(int X=CX-1;X<=CX+1;++X)
        for(int Y=CY-1;Y<=CY+1;++Y) {
            const auto* Cell=RoadGrid.Find(FIntPoint(X,Y));
            if(!Cell)continue;
            for(int Index:*Cell) {
                const FDynaMapSegment& S=Segments[Index];
                const FVector2D D=S.B-S.A;
                const double Length2=D.SizeSquared();
                const double T=Length2>1e-9?FMath::Clamp(FVector2D::DotProduct(P-S.A,D)/Length2,0.0,1.0):0.0;
                const double Distance=FVector2D::Distance(P,S.A+D*T);
                double& Best=Nearest.FindOrAdd(S.Road,TNumericLimits<double>::Max());
                Best=FMath::Min(Best,Distance);
            }
        }
    int32 BestRoad=INDEX_NONE;double BestDistance=MaxDistanceM;
    for(const auto& Pair:Nearest)
        if(Pair.Value<BestDistance){BestDistance=Pair.Value;BestRoad=Pair.Key;}
    // Hold the current street until another road is clearly closer.
    if(const double* Last=Nearest.Find(LastStreetRoad))
        if(*Last<=MaxDistanceM&&BestRoad!=LastStreetRoad&&*Last<=BestDistance+6.0)BestRoad=LastStreetRoad;
    LastStreetRoad=BestRoad;
    return RoadNames.IsValidIndex(BestRoad)?RoadNames[BestRoad]:FString();
}

bool ADynaMobilityWorld::AppendFrames(const FString& Json)
{
    TSharedPtr<FJsonObject> Data;const TArray<TSharedPtr<FJsonValue>>* Input;
    if(!Read(Json,Data)||!Data->TryGetArrayField(TEXT("frames"),Input))return false;
    TArray<FDynaTrafficFrame> Batch;
    double Last=Frames.IsEmpty()?-1:Frames.Last().Time;
    for(const auto& Value:*Input) {
        auto Row=Value->AsObject();FDynaTrafficFrame F;F.Time=Row->GetNumberField(TEXT("time_s"));
        if(!FMath::IsFinite(F.Time)||F.Time<=Last)return false;
        Last=F.Time;
        for(const auto& V:Row->GetArrayField(TEXT("vehicles"))) {
            auto Car=V->AsObject();const auto& P=Car->GetArrayField(TEXT("position"));
            if(P.Num()!=3)return false;
            FDynaTrafficVehicle Vehicle;Vehicle.Id=Car->GetStringField(TEXT("id"));
            Car->TryGetStringField(TEXT("type_id"),Vehicle.TypeId);
            Vehicle.Position=Point(P);Vehicle.Heading=Car->GetNumberField(TEXT("heading_deg"));
            if(Car->HasField(TEXT("light_bits"))) {
                double Bits;
                if(!Car->TryGetNumberField(TEXT("light_bits"),Bits) || !FMath::IsFinite(Bits) || Bits<0 || Bits>16383 || Bits!=FMath::FloorToDouble(Bits))return false;
                Vehicle.LightBits=static_cast<int32>(Bits);
            }
            if(Vehicle.Position.ContainsNaN()||!FMath::IsFinite(Vehicle.Heading))return false;
            const TArray<TSharedPtr<FJsonValue>>* Paint;
            if(Car->TryGetArrayField(TEXT("paint_rgb"),Paint)) {
                if(Paint->Num()!=3)return false;
                float Channels[3];
                for(int32 C=0;C<3;++C) {
                    double Channel;
                    if(!(*Paint)[C]->TryGetNumber(Channel)||!FMath::IsFinite(Channel)||Channel<0||Channel>1)return false;
                    Channels[C]=Channel;
                }
                Vehicle.Paint=FLinearColor(Channels[0],Channels[1],Channels[2],1);
            }
            F.Vehicles.Add(MoveTemp(Vehicle));
        }
        Batch.Add(MoveTemp(F));
    }
    Frames.Append(MoveTemp(Batch));
    if(Clock<0&&Frames.Num()>1)Clock=Frames[0].Time;
    return true;
}

void ADynaMobilityWorld::ClearTraffic()
{ Frames.Reset();Clock=-1;VisibleCars=0;Vehicles->ClearInstances();VehicleLights->Reset();VehicleLights->Flush();
  for(UInstancedStaticMeshComponent* Component:AdditionalVehicles)if(Component)Component->ClearInstances(); }

void ADynaMobilityWorld::Tick(float DeltaSeconds)
{
    Super::Tick(DeltaSeconds);
    if(Frames.Num()<2)return;
    // Freeze at the last authoritative sample on network starvation; never extrapolate.
    Clock=FMath::Min(Clock+DeltaSeconds,Frames.Last().Time);
    while(Frames.Num()>2&&Frames[1].Time<=Clock)Frames.RemoveAt(0);
    const auto& A=Frames[0];const auto& B=Frames[1];
    double T=FMath::Clamp((Clock-A.Time)/(B.Time-A.Time),0.0,1.0);
    TMap<FString,const FDynaTrafficVehicle*> After;
    for(const auto& V:B.Vehicles)After.Add(V.Id,&V);
    APawn* Player=UGameplayStatics::GetPlayerPawn(this,0);
    const auto* Driver=Cast<ADynaDrivePawn>(Player);
    const bool Night=Driver && Driver->NightMode;
    VehicleLights->Reset();
    FVector PlayerPosition=Player?Player->GetActorLocation():FVector::ZeroVector;
    const int32 Limit=FMath::Clamp(MaxVisibleVehicles,1,750);
    const double RadiusCm=FMath::Clamp(VisibilityRadiusM,50.f,750.f)*100;
    TArray<UInstancedStaticMeshComponent*> Groups;Groups.Add(Vehicles);
    for(UInstancedStaticMeshComponent* Component:AdditionalVehicles)if(Component)Groups.Add(Component);
    TArray<TArray<FTransform>> Transforms;Transforms.SetNum(Groups.Num());
    TArray<TArray<FLinearColor>> Paints;Paints.SetNum(Groups.Num());
    int32 Count=0;
    for(const auto& V:A.Vehicles) {
        const auto Found=After.Find(V.Id);const auto* Next=Found?*Found:&V;
        const FVector P=FMath::Lerp(V.Position,Next->Position,T);
        const double Heading=FMath::DegreesToRadians(V.Heading+FMath::FindDeltaAngleDegrees(V.Heading,Next->Heading)*T);
        const FVector Direction=(BasisEast*FMath::Sin(Heading)+BasisNorth*FMath::Cos(Heading)).GetSafeNormal();
        int32 Group=FCrc::StrCrc32(*V.Id)%Groups.Num();
        if(!TrafficTypeGroups.IsEmpty()) {
            const int32* Mapped=TrafficTypeGroups.Find(V.TypeId);
            if(!Mapped || !Groups.IsValidIndex(*Mapped))continue;
            Group=*Mapped;
        }
        const UStaticMesh* Mesh=Groups[Group]->GetStaticMesh();
        const double HalfLength=Mesh?Mesh->GetBounds().BoxExtent.X:220;
        FVector Where=BasisOrigin+BasisEast*P.X+BasisNorth*P.Y+BasisUp*(P.Z+.03)-Direction*HalfLength;
        if(FVector::DistSquared(Where,PlayerPosition)>FMath::Square(RadiusCm))continue;
        Transforms[Group].Emplace(Direction.Rotation(),Where);
        // Discrete states belong to the last source sample, not a blended future intent.
        const auto& State=T>=1.0?*Next:V;
        const double FlashTime=Clock+(FCrc::StrCrc32(*V.Id)%800)*.001;
        if(Mesh)VehicleLights->AddVehicle(Transforms[Group].Last(),Mesh->GetBoundingBox(),State.LightBits,FlashTime,Night);
        Paints[Group].Add(V.Paint);
        if(++Count==Limit)break;
    }
    for(int32 Group=0;Group<Groups.Num();++Group) {
        auto* Component=Groups[Group];const auto& Items=Transforms[Group];
        while(Component->GetInstanceCount()<Items.Num())Component->AddInstance(FTransform::Identity);
        while(Component->GetInstanceCount()>Items.Num())Component->RemoveInstance(Component->GetInstanceCount()-1);
        for(int32 I=0;I<Paints[Group].Num();++I) {
            const auto& Colour=Paints[Group][I];
            Component->SetCustomDataValue(I,0,Colour.R,false);
            Component->SetCustomDataValue(I,1,Colour.G,false);
            Component->SetCustomDataValue(I,2,Colour.B,false);
            Component->SetCustomDataValue(I,3,Colour.A,false);
        }
        // One render update carries both transforms and instance paint data.
        if(!Items.IsEmpty())Component->BatchUpdateInstancesTransforms(0,Items,false,true,true);
    }
    VisibleCars=Count;
    VehicleLights->Flush();
}

FString ADynaMobilityWorld::Status() const
{
    auto Data=MakeShared<FJsonObject>();
    Data->SetNumberField(TEXT("roads"),RoadCount);
    Data->SetNumberField(TEXT("visible_vehicles"),VisibleCars);
    Data->SetNumberField(TEXT("time_s"),Clock);
    Data->SetNumberField(TEXT("buffer_s"),Frames.IsEmpty()?0:FMath::Max(0.0,Frames.Last().Time-Clock));
    if(Minimap)Data->SetObjectField(TEXT("minimap"),Minimap->StatusObject());
    FString Json;
    FJsonSerializer::Serialize(Data,TJsonWriterFactory<>::Create(&Json));
    return Json;
}

void ADynaMobilityWorld::DrawMinimap(AHUD* HUD,const APawn* Player,float X,float Y,float Size,UCanvas* Canvas)
{
    Minimap->Draw(HUD,Canvas,Player,X,Y,Size,Segments,RoadGrid);
}
