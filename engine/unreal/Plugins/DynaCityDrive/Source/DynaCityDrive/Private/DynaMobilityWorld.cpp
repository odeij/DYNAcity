#include "DynaMobilityWorld.h"
#include "DynaMinimapComponent.h"
#include "DynaVehicleLights.h"
#include "DynaDrivePawn.h"
#include "Components/InstancedStaticMeshComponent.h"
#include "GameFramework/HUD.h"
#include "Engine/Canvas.h"
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
    Segments.Reset();RoadGrid.Reset();RoadNames.Reset();RoadSpeeds.Reset();RoadCount=0;LastStreetRoad=INDEX_NONE;
    Planner=FDynaRoutePlanner();ClearWaypoint();
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
        // Tari'ak hourly median speeds (24 values, km/h) for GPS travel times; optional.
        TArray<float>& Speeds=RoadSpeeds.AddDefaulted_GetRef();
        const TArray<TSharedPtr<FJsonValue>>* Hourly;
        if(Object->TryGetArrayField(TEXT("speed_kmh"),Hourly) && Hourly->Num()==24)
            for(const auto& Speed:*Hourly) { double V=0; Speeds.Add(Speed->TryGetNumber(V)?float(V):0.f); }
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
    UpdateRoute(DeltaSeconds);
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
    Minimap->RoutePoints.Reset();
    Minimap->RouteHasGoal=RouteActive && Route.IsValid();
    if(Minimap->RouteHasGoal)
    {
        Minimap->RouteGoal=Route.Points.Last();
        // Only the part still ahead: from the car's projection onto the route to the end.
        if(ArrivedSeconds<=0)
        {
            const int32 Leg=FMath::Clamp(RouteHint,0,Route.Points.Num()-2);
            const double Span=Route.Along[Leg+1]-Route.Along[Leg];
            const double T=Span>1e-6?FMath::Clamp((RouteAlong-Route.Along[Leg])/Span,0.0,1.0):0.0;
            Minimap->RoutePoints.Add(FMath::Lerp(Route.Points[Leg],Route.Points[Leg+1],T));
            for(int32 I=Leg+1;I<Route.Points.Num();++I)Minimap->RoutePoints.Add(Route.Points[I]);
        }
    }
    Minimap->Draw(HUD,Canvas,Player,X,Y,Size,Segments,RoadGrid);
    if(RouteActive && Canvas && X+Size+14+CardWidth<=Canvas->ClipX)DrawRouteCard(HUD,X+Size+14,Y-28,Size+99);
}

// ---------------------------------------------------------------- GPS route

namespace {
FString Distance(double Metres)
{
    if(Metres<1000)return FString::Printf(TEXT("%d m"),FMath::Max(0,FMath::RoundToInt(Metres/10)*10));
    return FString::Printf(TEXT("%.1f km"),Metres/1000);
}
FString Duration(double Seconds)
{
    const int32 Minutes=FMath::Max(1,FMath::RoundToInt(Seconds/60));
    return Minutes<60?FString::Printf(TEXT("%d min"),Minutes):FString::Printf(TEXT("%d h %02d min"),Minutes/60,Minutes%60);
}
const TCHAR* TurnText(const FDynaRouteManeuver& M)
{
    if(M.Arrive)return TEXT("Arrive at destination");
    switch(M.Turn)
    {
    case -3: case 3: return TEXT("Make a U-turn");
    case -2: return TEXT("Sharp left");
    case -1: return TEXT("Turn left");
    case 1: return TEXT("Turn right");
    case 2: return TEXT("Sharp right");
    default: return TEXT("Continue");
    }
}
const FLinearColor Magenta(.86f,.22f,1.f,1.f);
}

int32 ADynaMobilityWorld::RouteHour() const
{
    const auto* Driver=Cast<ADynaDrivePawn>(UGameplayStatics::GetPlayerPawn(this,0));
    return Driver?FMath::Clamp(FMath::FloorToInt(Driver->TimeOfDay),0,23):14;
}

bool ADynaMobilityWorld::Replan(const FVector2D& From,FString& Error)
{
    if(!Planner.IsBuilt())Planner.Build(Segments,RoadSpeeds);
    FDynaRoute Next;
    if(!Planner.Plan(From,Waypoint,RouteHour(),RoadNames,Next,Error))return false;
    Route=MoveTemp(Next); RouteHint=0; RouteAlong=0; RouteOff=0; OffRouteSeconds=0; ++RouteVersion;
    RouteAlong=Route.Project(From,RouteHint,RouteSlack);
    return true;
}

FString ADynaMobilityWorld::SetWaypoint(double X,double Y)
{
    auto Fail=[](const FString& Error)
    {
        auto Data=MakeShared<FJsonObject>(); Data->SetStringField(TEXT("error"),Error);
        FString Json; FJsonSerializer::Serialize(Data,TJsonWriterFactory<>::Create(&Json)); return Json;
    };
    if(!FMath::IsFinite(X) || !FMath::IsFinite(Y) || FMath::Abs(X)>100000 || FMath::Abs(Y)>100000)return Fail(TEXT("Choose a point on the Beirut road map."));
    const APawn* Player=UGameplayStatics::GetPlayerPawn(this,0);
    if(!Player)return Fail(TEXT("Start driving to plan a GPS route."));
    if(Segments.IsEmpty())return Fail(TEXT("Road network not loaded."));
    const FVector2D Previous=Waypoint;
    Waypoint=FVector2D(X,Y);
    FString Error;
    if(!Replan(Local(Player->GetActorLocation()),Error)) { Waypoint=Previous; return Fail(Error); }
    RouteActive=true; ArrivedSeconds=0; Replans=0; ReplanCooldown=0;
    RouteMessage=TEXT("Route set");
    UpdateRoute(0);
    return RouteStatus();
}

void ADynaMobilityWorld::ClearWaypoint()
{
    RouteActive=false; Route.Reset(); RouteHint=0; RouteAlong=RouteOff=OffRouteSeconds=ArrivedSeconds=RouteSlack=0;
    RouteMessage.Reset(); ++RouteVersion;
    if(Minimap) { Minimap->RoutePoints.Reset(); Minimap->RouteHasGoal=false; }
}

void ADynaMobilityWorld::UpdateRoute(double Dt)
{
    if(!RouteActive || !Route.IsValid())return;
    if(ArrivedSeconds>0) { ArrivedSeconds-=Dt; if(ArrivedSeconds<=0)ClearWaypoint(); return; }
    const APawn* Player=UGameplayStatics::GetPlayerPawn(this,0);
    if(!Player)return;
    const FVector2D P=Local(Player->GetActorLocation());
    RouteAlong=Route.Project(P,RouteHint,RouteOff);
    RouteSlack=FMath::Min(RouteSlack,RouteOff);
    ReplanCooldown=FMath::Max(0.0,ReplanCooldown-Dt);
    if(FVector2D::Distance(P,Route.Points.Last())<25 || (Route.Length-RouteAlong<20 && RouteOff<25))
    { RouteMessage=TEXT("You have arrived"); ArrivedSeconds=6; return; }
    // GTA-style recalculation: off the line for a moment, then plan again from the car.
    OffRouteSeconds=RouteOff>RouteSlack+30?OffRouteSeconds+Dt:0;
    if(OffRouteSeconds>1.5 && ReplanCooldown<=0)
    {
        ReplanCooldown=3; FString Error;
        if(Replan(P,Error)) { ++Replans; RouteMessage=TEXT("Route recalculated"); }
        else RouteMessage=Error;
    }
}

double ADynaMobilityWorld::RemainingSeconds() const
{
    // Travel time still ahead at the planning hour's speeds, from the car's leg onward.
    double Seconds=0;
    for(int32 I=FMath::Max(0,RouteHint);I<Route.Roads.Num();++I)
    {
        const double Start=FMath::Max(Route.Along[I],RouteAlong),Length=Route.Along[I+1]-Start;
        if(Length>0)Seconds+=Length/(Planner.SpeedKmh(Route.Roads[I],Route.Hour)/3.6);
    }
    return Seconds;
}

FString ADynaMobilityWorld::RouteStatus() const
{
    auto Data=MakeShared<FJsonObject>();
    Data->SetBoolField(TEXT("active"),RouteActive);
    Data->SetNumberField(TEXT("version"),RouteVersion);
    if(RouteActive && Route.IsValid())
    {
        const double Remaining=FMath::Max(0.0,Route.Length-RouteAlong);
        const double Seconds=RemainingSeconds();
        auto Pair=[](double A,double B){ return TArray<TSharedPtr<FJsonValue>>{MakeShared<FJsonValueNumber>(A),MakeShared<FJsonValueNumber>(B)}; };
        Data->SetArrayField(TEXT("waypoint"),Pair(Waypoint.X,Waypoint.Y));
        Data->SetArrayField(TEXT("destination"),Pair(Route.Points.Last().X,Route.Points.Last().Y));
        Data->SetStringField(TEXT("destination_road"),Route.DestinationRoad);
        Data->SetStringField(TEXT("message"),RouteMessage);
        Data->SetBoolField(TEXT("arrived"),ArrivedSeconds>0);
        Data->SetNumberField(TEXT("hour"),Route.Hour);
        Data->SetNumberField(TEXT("length_m"),Route.Length);
        Data->SetNumberField(TEXT("total_s"),Route.Seconds);
        Data->SetNumberField(TEXT("progress_m"),RouteAlong);
        Data->SetNumberField(TEXT("remaining_m"),Remaining);
        Data->SetNumberField(TEXT("eta_s"),Seconds);
        Data->SetNumberField(TEXT("average_kmh"),Route.Seconds>0?Route.Length/Route.Seconds*3.6:0);
        Data->SetNumberField(TEXT("off_route_m"),RouteOff);
        Data->SetNumberField(TEXT("recalculations"),Replans);
        Data->SetNumberField(TEXT("turns"),Route.Maneuvers.Num()-1);
        if(Route.SlowRoad!=INDEX_NONE)
        {
            auto Slow=MakeShared<FJsonObject>();
            Slow->SetStringField(TEXT("road"),FDynaRoutePlanner::RoadLabel(RoadNames,Route.SlowRoad));
            Slow->SetNumberField(TEXT("kmh"),Route.SlowKmh);
            Data->SetObjectField(TEXT("slowest"),Slow);
        }
        TArray<TSharedPtr<FJsonValue>> Steps;
        for(const FDynaRouteManeuver& M:Route.Maneuvers)
        {
            if(M.AlongM<RouteAlong+2 && !M.Arrive)continue;
            auto Step=MakeShared<FJsonObject>();
            Step->SetStringField(TEXT("instruction"),TurnText(M));
            Step->SetNumberField(TEXT("turn"),M.Turn);
            Step->SetBoolField(TEXT("arrive"),M.Arrive);
            Step->SetStringField(TEXT("road"),M.Road);
            Step->SetNumberField(TEXT("distance_m"),FMath::Max(0.0,M.AlongM-RouteAlong));
            Steps.Add(MakeShared<FJsonValueObject>(Step));
        }
        Data->SetArrayField(TEXT("steps"),Steps);
        TArray<TSharedPtr<FJsonValue>> Points;
        for(const FVector2D& P:Route.Points)Points.Add(MakeShared<FJsonValueArray>(Pair(FMath::RoundToDouble(P.X*10)/10,FMath::RoundToDouble(P.Y*10)/10)));
        Data->SetArrayField(TEXT("points"),Points);
    }
    Data->SetNumberField(TEXT("network_nodes"),Planner.NodeCount());
    Data->SetNumberField(TEXT("junction_links"),Planner.JunctionLinks());
    Data->SetStringField(TEXT("basis"),TEXT("Fastest path on exported roads at the in-game hour, using Tari'ak 2015-2019 hourly median speeds. One-way rules and turn bans are not modelled."));
    FString Json;
    FJsonSerializer::Serialize(Data,TJsonWriterFactory<>::Create(&Json));
    return Json;
}

void ADynaMobilityWorld::DrawRouteCard(AHUD* HUD,float X,float Y,float Height) const
{
    UFont* Small=GEngine->GetSmallFont();
    UFont* Medium=GEngine->GetMediumFont();
    const float W=CardWidth;
    HUD->DrawRect(FLinearColor(.018f,.026f,.032f,.95f),X,Y,W,Height);
    HUD->DrawRect(Magenta,X,Y,4,Height);
    const float L=X+16;
    HUD->DrawText(TEXT("GPS ROUTE"),Magenta,L,Y+6,Small,.85f);
    if(ArrivedSeconds>0)
    {
        HUD->DrawText(TEXT("You have arrived"),FLinearColor::White,L,Y+30,Medium,1.f);
        HUD->DrawText(Route.DestinationRoad,FLinearColor(.82f,.87f,.86f),L,Y+60,Small,1.f);
        return;
    }
    // Next instruction with a drawn turn arrow.
    const FDynaRouteManeuver* Next=nullptr; const FDynaRouteManeuver* After=nullptr;
    for(const FDynaRouteManeuver& M:Route.Maneuvers)
    {
        if(M.AlongM<RouteAlong+2 && !M.Arrive)continue;
        if(!Next)Next=&M; else { After=&M; break; }
    }
    const float IconX=L+22,IconY=Y+52;
    if(Next && Next->Arrive)
    {
        HUD->DrawRect(FLinearColor(.08f,0,.1f,1),IconX-11,IconY-11,22,22);
        HUD->DrawRect(Magenta,IconX-9,IconY-9,18,18);
        HUD->DrawRect(FLinearColor::White,IconX-3,IconY-3,6,6);
    }
    else if(Next)
    {
        static const double Angles[4]={0,90,135,180};
        const double A=FMath::DegreesToRadians(Angles[FMath::Min(FMath::Abs(Next->Turn),3)]*(Next->Turn<0?-1:1));
        const FVector2D Base(IconX,IconY+18),Knee(IconX,IconY),Dir(FMath::Sin(A),-FMath::Cos(A));
        const FVector2D Tip=Knee+Dir*16,Side(-Dir.Y,Dir.X);
        auto Line=[&](FVector2D P,FVector2D Q){ HUD->DrawLine(P.X,P.Y,Q.X,Q.Y,FLinearColor::White,4.f); };
        Line(Base,Knee); Line(Knee,Tip); Line(Tip,Tip-Dir*8+Side*7); Line(Tip,Tip-Dir*8-Side*7);
    }
    const float TX=L+56;
    if(Next)
    {
        HUD->DrawText(Distance(Next->AlongM-RouteAlong),FLinearColor::White,TX,Y+26,Medium,1.15f);
        HUD->DrawText(TurnText(*Next),FLinearColor(.9f,.94f,.95f),TX,Y+54,Small,1.f);
        FString Base=Next->Road,Onto=Next->Arrive?Base:TEXT("onto ")+Base; float TW=0,TH=0;
        HUD->GetTextSize(Onto,TW,TH,Small,.95f);
        while(Base.Len()>4 && TX+TW>X+W-10)
        { Base.LeftChopInline(1); Onto=(Next->Arrive?Base:TEXT("onto ")+Base)+TEXT("..."); HUD->GetTextSize(Onto,TW,TH,Small,.95f); }
        HUD->DrawText(Onto,FLinearColor(.82f,.87f,.86f),TX,Y+72,Small,.95f);
    }
    float Row=Y+100;
    if(After)
    {
        HUD->DrawText(FString::Printf(TEXT("Then: %s - %s"),TurnText(*After),*After->Road).Left(48),FLinearColor(.7f,.76f,.76f),L,Row,Small,.85f);
        Row+=20;
    }
    HUD->DrawRect(FLinearColor(1,1,1,.12f),L,Row,W-32,1); Row+=8;
    // Remaining distance and time, progress, and the hour's traffic basis.
    const double Seconds=RemainingSeconds();
    const double Remaining=FMath::Max(0.0,Route.Length-RouteAlong);
    HUD->DrawText(FString::Printf(TEXT("%s   |   %s"),*Distance(Remaining),*Duration(Seconds)),FLinearColor::White,L,Row,Medium,.9f);
    Row+=28;
    const float Bar=W-32,Done=Route.Length>0?float(FMath::Clamp(RouteAlong/Route.Length,0.0,1.0)):0.f;
    HUD->DrawRect(FLinearColor(1,1,1,.15f),L,Row,Bar,5);
    HUD->DrawRect(Magenta,L,Row,Bar*Done,5);
    Row+=12;
    HUD->DrawText(FString::Printf(TEXT("To %s"),*Route.DestinationRoad).Left(44),FLinearColor(.82f,.87f,.86f),L,Row,Small,.9f); Row+=18;
    HUD->DrawText(FString::Printf(TEXT("Traffic at %02d:00 - avg %.0f km/h on route"),Route.Hour,Route.Seconds>0?Route.Length/Route.Seconds*3.6:0),
        FLinearColor(.75f,.82f,.8f),L,Row,Small,.85f); Row+=17;
    if(Route.SlowRoad!=INDEX_NONE)
    {
        HUD->DrawText(FString::Printf(TEXT("Slowest: %s, %.0f km/h"),*FDynaRoutePlanner::RoadLabel(RoadNames,Route.SlowRoad),Route.SlowKmh).Left(46),
            FLinearColor(.95f,.78f,.5f),L,Row,Small,.85f); Row+=17;
    }
    const bool Off=OffRouteSeconds>0;
    const FString Status=Off?TEXT("Off route - recalculating..."):RouteMessage==TEXT("Route recalculated")?FString::Printf(TEXT("Recalculated %d time%s"),Replans,Replans==1?TEXT(""):TEXT("s")):FString(TEXT("Tari'ak hourly speeds - one-way rules not modelled"));
    if(Row+14<Y+Height)HUD->DrawText(Status,Off?FLinearColor(1,.7f,.45f):FLinearColor(.6f,.66f,.66f),L,Row,Small,.75f);
}
