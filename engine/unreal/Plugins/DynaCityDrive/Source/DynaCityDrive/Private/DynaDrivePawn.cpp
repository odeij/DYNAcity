#include "DynaDrivePawn.h"
#include "DriveDynamics.h"
#include "DynaMobilityWorld.h"
#include "DynaVehicleLights.h"
#include "DynaAmbientSky.h"
#include "DynaMinimapComponent.h"
#include "Components/SpotLightComponent.h"
#include "Components/PointLightComponent.h"
#include "Engine/PointLight.h"
#include "Components/DirectionalLightComponent.h"
#include "Components/SkyLightComponent.h"
#include "Engine/DirectionalLight.h"
#include "Engine/SkyLight.h"
#include "DynaLocationPanel.h"
#include "DynaControlPanel.h"
#include "Framework/Application/SlateApplication.h"
#include "Camera/CameraComponent.h"
#include "Components/BoxComponent.h"
#include "Components/InputComponent.h"
#include "Components/StaticMeshComponent.h"
#include "GameFramework/PlayerController.h"
#include "GameFramework/SpringArmComponent.h"
#include "Engine/Canvas.h"
#include "Engine/Engine.h"
#include "Engine/StaticMesh.h"
#include "Engine/World.h"
#include "Engine/GameViewportClient.h"
#include "Containers/Ticker.h"
#include "Widgets/Input/SButton.h"
#include "Widgets/SOverlay.h"
#include "Widgets/Layout/SBorder.h"
#include "Widgets/Layout/SWrapBox.h"
#include "Widgets/Text/STextBlock.h"
#include "Styling/CoreStyle.h"
#include "EngineUtils.h"
#include "InputCoreTypes.h"
#include "Modules/ModuleManager.h"
#include "Dom/JsonObject.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"

IMPLEMENT_MODULE(FDefaultModuleImpl, DynaCityDrive)

ADynaDrivePawn::ADynaDrivePawn()
{
    PrimaryActorTick.bCanEverTick = true;
    VehicleLights=CreateDefaultSubobject<UDynaVehicleLights>(TEXT("VehicleLights"));
    Chassis = CreateDefaultSubobject<UBoxComponent>(TEXT("Chassis"));
    SetRootComponent(Chassis);
    Chassis->InitBoxExtent(FVector(218, 90, 55));
    Chassis->SetCollisionProfileName(TEXT("Pawn"));
    Chassis->SetCollisionResponseToChannel(ECC_Camera, ECR_Ignore);
    Chassis->SetCanEverAffectNavigation(false);
    Body = CreateDefaultSubobject<UStaticMeshComponent>(TEXT("Body"));
    Body->SetupAttachment(Chassis);
    Body->SetRelativeLocation(FVector(0, 0, -75));
    Body->SetCollisionEnabled(ECollisionEnabled::NoCollision);
    Body->SetCanEverAffectNavigation(false);
    CameraArm = CreateDefaultSubobject<USpringArmComponent>(TEXT("CameraArm"));
    CameraArm->SetupAttachment(Chassis);
    CameraArm->TargetArmLength = 650;
    CameraArm->SetRelativeLocation(FVector(0, 0, 100));
    CameraArm->SetRelativeRotation(FRotator(-12, 0, 0));
    CameraArm->bEnableCameraLag = true;
    CameraArm->CameraLagSpeed = 7;
    CameraArm->bDoCollisionTest = true;
    CameraArm->bInheritRoll = false;
    CameraArm->bInheritPitch = false;
    Camera = CreateDefaultSubobject<UCameraComponent>(TEXT("Camera"));
    Camera->SetupAttachment(CameraArm, USpringArmComponent::SocketName);
    Camera->SetFieldOfView(75);
    Cockpit = CreateDefaultSubobject<UStaticMeshComponent>(TEXT("Cockpit"));
    Cockpit->SetupAttachment(Chassis);
    Cockpit->SetRelativeLocation(FVector(0,0,-75));
    // Finished LHD source assets are converted once. Never mirror the cabin.
    Cockpit->SetRelativeScale3D(FVector(1,1,1));
    Cockpit->SetCollisionEnabled(ECollisionEnabled::NoCollision);
    Cockpit->SetCanEverAffectNavigation(false);
    Cockpit->SetVisibility(false);
    LeftHeadlight=CreateDefaultSubobject<USpotLightComponent>(TEXT("LeftHeadlight"));
    RightHeadlight=CreateDefaultSubobject<USpotLightComponent>(TEXT("RightHeadlight"));
    int I=0;
    for(USpotLightComponent* Light:{LeftHeadlight.Get(),RightHeadlight.Get()}) {
        Light->SetupAttachment(Chassis);Light->SetRelativeLocation(FVector(215,I++?58:-58,-7));
        Light->SetRelativeRotation(FRotator(-4,0,0));Light->SetIntensity(35000);
        Light->SetAttenuationRadius(6500);Light->SetInnerConeAngle(15);Light->SetOuterConeAngle(33);
        Light->SetCastShadows(false);Light->SetVisibility(false);
    }
}

void ADynaDrivePawn::CollectStarts()
{
    Starts.Reset();
    for (TActorIterator<ADynaDriveStart> It(GetWorld()); It; ++It) Starts.Add(*It);
    Starts.Sort([](const TWeakObjectPtr<ADynaDriveStart>& A, const TWeakObjectPtr<ADynaDriveStart>& B)
    { return A->Area < B->Area; });
}

void ADynaDrivePawn::EndPlay(const EEndPlayReason::Type Reason)
{
    if(IsValid(AmbientSky))AmbientSky->Destroy();
    Super::EndPlay(Reason);
}

void ADynaDrivePawn::BeginPlay()
{
    Super::BeginPlay();
    CollectStarts();
    double Best = TNumericLimits<double>::Max();
    for (int32 I = 0; I < Starts.Num(); ++I)
    {
        const double Distance = FVector::DistSquared(Starts[I]->GetActorLocation(), GetActorLocation());
        if (Distance < Best) { Best = Distance; CurrentStart = I; }
    }
    if (!Starts.IsEmpty()) UseStart(CurrentStart);
    else
    {
        ResetTransform = GetActorTransform();
        Heading = GetActorRotation().Yaw;
        SurfaceStatus = TEXT("No source drive starts configured");
    }
    if (APlayerController* PC = Cast<APlayerController>(GetController()))
    {
        PC->bShowMouseCursor = false;
        PC->SetInputMode(FInputModeGameOnly());
    }
}

void ADynaDrivePawn::SetupPlayerInputComponent(UInputComponent* Input)
{
    Super::SetupPlayerInputComponent(Input);
    // Driving keys are ignored while the menu is open: the panel window can sit in front of the viewport
    // and typing there (R, N, F, C, L...) must not reset, teleport or toggle the car. Tab/F1/F3 stay live.
    auto BindDriving = [this, Input](const FKey& Key, void (ADynaDrivePawn::*Action)())
    {
        FInputKeyBinding Binding(FInputChord(Key), IE_Pressed);
        Binding.KeyDelegate.GetDelegateForManualSet().BindLambda([this, Action]()
        {
            const APlayerController* PC = Cast<APlayerController>(GetController());
            if (!PC || !PC->bShowMouseCursor) (this->*Action)();
        });
        Input->KeyBindings.Add(MoveTemp(Binding));
    };
    BindDriving(EKeys::R, &ADynaDrivePawn::ResetToStart);
    BindDriving(EKeys::N, &ADynaDrivePawn::NextArea);
    BindDriving(EKeys::U, &ADynaDrivePawn::RecoverVehicle);
    BindDriving(EKeys::F, &ADynaDrivePawn::ToggleFlyMode);
    BindDriving(EKeys::C, &ADynaDrivePawn::CycleCamera);
    BindDriving(EKeys::L, &ADynaDrivePawn::ToggleNight);
    BindDriving(EKeys::Z, &ADynaDrivePawn::ToggleLeftSignal);
    BindDriving(EKeys::X, &ADynaDrivePawn::ToggleRightSignal);
    BindDriving(EKeys::H, &ADynaDrivePawn::ToggleHazards);
    BindDriving(EKeys::M, &ADynaDrivePawn::CycleMinimap);
    BindDriving(EKeys::B, &ADynaDrivePawn::ToggleMinimapOrientation);
    Input->BindKey(EKeys::Tab, IE_Pressed, this, &ADynaDrivePawn::ToggleCursor);
    Input->BindKey(EKeys::F3, IE_Pressed, this, &ADynaDrivePawn::OpenLocationSearch);
    Input->BindKey(EKeys::F1, IE_Pressed, this, &ADynaDrivePawn::OpenControls);
}

void ADynaDrivePawn::CycleMinimap()
{
    if(Mobility && Mobility->Minimap)Mobility->Minimap->CycleMode();
}

void ADynaDrivePawn::ToggleMinimapOrientation()
{
    if(Mobility && Mobility->Minimap)Mobility->Minimap->ToggleOrientation();
}

void ADynaDrivePawn::OpenControls()
{
    SpeedCmS = 0; ReleaseDriveInputs();
    if (APlayerController* PC = Cast<APlayerController>(GetController()))
    {
        PC->FlushPressedKeys();   // a key released while the panel had focus must not stay "down"
        PC->bShowMouseCursor = true;
        PC->SetInputMode(FInputModeGameAndUI());
    }
    UDynaControlPanel::ShowRegisteredPanel(TEXT("controls"));
    // In the editor F1 is also the level viewport's wireframe shortcut, which the Play viewport then shows.
    // Restore lit view after the editor has handled the key.
    if (GIsEditor)
        FTSTicker::GetCoreTicker().AddTicker(FTickerDelegate::CreateWeakLambda(this, [this](float)
        {
            if (UWorld* World = GetWorld(); World && GEngine) GEngine->Exec(World, TEXT("viewmode lit"));
            return false;
        }), 0.05f);
}

void ADynaDrivePawn::ResumeDriving()
{
    if (APlayerController* PC = Cast<APlayerController>(GetController()))
    {
        PC->FlushPressedKeys();   // drop keys held or released while the menu had focus (no stuck W/Space)
        PC->bShowMouseCursor = false;
        PC->SetInputMode(FInputModeGameOnly());
        FSlateApplication::Get().SetAllUserFocusToGameViewport();
    }
}

void ADynaDrivePawn::OpenLocationSearch()
{
    SpeedCmS = 0;
    ReleaseDriveInputs();
    if (APlayerController* PC = Cast<APlayerController>(GetController()))
    {
        PC->FlushPressedKeys();
        PC->bShowMouseCursor = true;
        PC->SetInputMode(FInputModeGameAndUI());
    }
    UDynaControlPanel::ShowRegisteredPanel(TEXT("search"));
}

bool ADynaDrivePawn::TryLocation(const FVector& SourcePosition, float Yaw, const FString& PlaceName)
{
    if (SourcePosition.ContainsNaN() || !FMath::IsFinite(Yaw)) return false;
    FVector Position; FRotator Rotation;
    // Validate all wheel supports and body clearance before moving the player.
    if (!SampleSurface(SourcePosition, Yaw, Position, Rotation, true)) return false;
    SafePositions.Reset();
    ResetTransform = FTransform(FRotator(0,Yaw,0), SourcePosition);
    CurrentArea = PlaceName; CurrentRoad = TEXT("Nearby mapped road");
    ResetToStart();
    return true;
}

void ADynaDrivePawn::SetDriveInputs(float Throttle, float Steering, bool Brake)
{
    ExternalInputs = true;
    if (!FMath::IsFinite(Throttle) || !FMath::IsFinite(Steering))
    {
        ExternalThrottle = ExternalSteering = 0; ExternalBrake = true; return;
    }
    ExternalThrottle = FMath::Clamp(Throttle, -1.0f, 1.0f);
    ExternalSteering = FMath::Clamp(Steering, -1.0f, 1.0f);
    ExternalBrake = Brake;
}

void ADynaDrivePawn::ReleaseDriveInputs()
{
    ExternalInputs = false; ExternalThrottle = 0; ExternalSteering = 0; ExternalBrake = false;
    ExternalFlight = false; ExternalFlyInput = FVector::ZeroVector; ExternalFlyBoost = false;
    FlyVelocity = FVector::ZeroVector;
}

void ADynaDrivePawn::ToggleFlyMode() { SetFlyMode(!FlyMode); }

void ADynaDrivePawn::SetFlyMode(bool Enabled)
{
    if (FlyMode == Enabled) return;
    ReleaseDriveInputs(); SpeedCmS = 0; BlockedReason.Reset();
    Chassis->SetCollisionEnabled(ECollisionEnabled::NoCollision);
    if (Enabled)
    {
        FlightReturnTransform = GetActorTransform();
        FlightReturnInitialSurface = !SurfaceReady && WaitingInitialSurface;
        const FVector Eye = Camera->GetComponentLocation();
        FlyYaw = Camera->GetComponentRotation().Yaw;
        FlyPitch = FMath::Clamp(Camera->GetComponentRotation().Pitch, -85.0f, 85.0f);
        SetActorLocationAndRotation(Eye, FRotator(0, FlyYaw, 0), false, nullptr, ETeleportType::TeleportPhysics);
        FlyMode = true;
        SurfaceStatus = TEXT("Free flight - F returns to the parked car");
    }
    else
    {
        FlyMode = false;
        SetActorTransform(FlightReturnTransform, false, nullptr, ETeleportType::TeleportPhysics);
        Heading = FlightReturnTransform.Rotator().Yaw;
        WaitingInitialSurface = FlightReturnInitialSurface;
        SurfaceReady = false; SurfaceWaitSeconds = 0;
        SurfaceStatus = TEXT("Reloading parked car surface...");
    }
    UpdateCamera(0);
}

void ADynaDrivePawn::SetFlyInputs(float Forward, float Right, float Up, float Yaw, float Pitch, bool Boost)
{
    ExternalFlight = true;
    if (!FMath::IsFinite(Forward) || !FMath::IsFinite(Right) || !FMath::IsFinite(Up) ||
        !FMath::IsFinite(Yaw) || !FMath::IsFinite(Pitch))
    {
        ExternalFlyInput = FVector::ZeroVector; ExternalFlyBoost = false; return;
    }
    ExternalFlyInput = FVector(FMath::Clamp(Forward,-1.0f,1.0f), FMath::Clamp(Right,-1.0f,1.0f), FMath::Clamp(Up,-1.0f,1.0f));
    FlyYaw = FRotator::NormalizeAxis(Yaw); FlyPitch = FMath::Clamp(Pitch,-85.0f,85.0f);
    ExternalFlyBoost = Boost;
}

void ADynaDrivePawn::TickFlight(float Dt)
{
    FVector Input = ExternalFlight ? ExternalFlyInput : FVector::ZeroVector;
    bool Boost = ExternalFlight && ExternalFlyBoost;
    if (APlayerController* PC = Cast<APlayerController>(GetController()))
    {
        if (PC->bShowMouseCursor) { Input = FVector::ZeroVector; Boost = false; }
        else if (!ExternalFlight)
        {
            float X, Y; PC->GetInputMouseDelta(X,Y);
            FlyYaw = FRotator::NormalizeAxis(FlyYaw + X);
            FlyPitch = FMath::Clamp(FlyPitch - Y,-85.0f,85.0f);
            Input.X = float(PC->IsInputKeyDown(EKeys::W)) - float(PC->IsInputKeyDown(EKeys::S));
            Input.Y = float(PC->IsInputKeyDown(EKeys::D)) - float(PC->IsInputKeyDown(EKeys::A));
            Input.Z = float(PC->IsInputKeyDown(EKeys::E)) - float(PC->IsInputKeyDown(EKeys::Q));
            Boost = PC->IsInputKeyDown(EKeys::LeftShift) || PC->IsInputKeyDown(EKeys::RightShift);
        }
    }
    const FRotator View(FlyPitch,FlyYaw,0);
    const FVector Direction = View.Vector()*Input.X + FRotationMatrix(View).GetScaledAxis(EAxis::Y)*Input.Y + FVector::UpVector*Input.Z;
    FlyVelocity = Direction.GetClampedToMaxSize(1.0) * (Boost ? 18000.0 : 4500.0);
    SetActorLocationAndRotation(GetActorLocation() + FlyVelocity*FMath::Clamp(Dt,0.0f,0.1f),
        FRotator(0,FlyYaw,0),false,nullptr,ETeleportType::TeleportPhysics);
    UpdateCamera(Dt);
}

void ADynaDrivePawn::RememberSafePosition()
{
    if (!SurfaceReady || FlyMode) return;
    if (!SafePositions.IsEmpty() && FVector::DistSquared(SafePositions.Last().GetLocation(),GetActorLocation()) < FMath::Square(500.0)) return;
    SafePositions.Add(GetActorTransform());
    if (SafePositions.Num() > 32) SafePositions.RemoveAt(0);
}

void ADynaDrivePawn::RecoverVehicle()
{
    if (FlyMode) SetFlyMode(false);
    for (int32 I=SafePositions.Num()-1; I>=0; --I)
    {
        const FTransform Candidate = SafePositions[I];
        if (FVector::DistSquared(Candidate.GetLocation(),GetActorLocation()) < FMath::Square(250.0)) continue;
        FVector Position; FRotator Rotation;
        if (!SampleSurface(Candidate.GetLocation(),Candidate.Rotator().Yaw,Position,Rotation,false)) continue;
        ReleaseDriveInputs(); SpeedCmS = 0;
        SetActorLocationAndRotation(Position,Rotation,false,nullptr,ETeleportType::TeleportPhysics);
        Heading = Rotation.Yaw; SurfaceReady = true; WaitingInitialSurface = false;
        Chassis->SetCollisionEnabled(ECollisionEnabled::QueryAndPhysics);
        SafePositions.SetNum(I+1);
        SurfaceStatus = TEXT("Recovered to a recent clear position");
        CameraArm->bEnableCameraLag = false;
        return;
    }
    ResetToStart();
}

void ADynaDrivePawn::UseStart(int32 Index)
{
    if (!Starts.IsValidIndex(Index) || !Starts[Index].IsValid()) return;
    CurrentStart = Index;
    ADynaDriveStart* Start = Starts[Index].Get();
    SafePositions.Reset();
    ResetTransform = Start->GetActorTransform();
    CurrentArea = Start->Area; CurrentRoad = Start->RoadName;
    Body->SetStaticMesh(Start->VehicleMesh);
    ResetToStart();
}

void ADynaDrivePawn::ResetToStart()
{
    FlyMode = false; WaitingInitialSurface = true; BlockedReason.Reset();
    SpeedCmS = 0; SurfaceReady = false; SurfaceWaitSeconds = 0;
    ReleaseDriveInputs();
    Chassis->SetCollisionEnabled(ECollisionEnabled::NoCollision);
    SetActorTransform(ResetTransform, false, nullptr, ETeleportType::TeleportPhysics);
    Heading = ResetTransform.Rotator().Yaw;
    SurfaceStatus = TEXT("Loading source road surface...");
    // Disable camera lag for the teleport so it does not fly across the city.
    CameraArm->bEnableCameraLag = false;
}

void ADynaDrivePawn::NextArea()
{
    if (!Starts.IsEmpty()) UseStart((CurrentStart + 1) % Starts.Num());
}

bool ADynaDrivePawn::SampleSurface(const FVector& Centre, float Yaw, FVector& Position, FRotator& Rotation, bool Initial) const
{
    const FRotator Flat(0, Yaw, 0);
    const FVector Forward = Flat.Vector();
    const FVector Right = FRotationMatrix(Flat).GetScaledAxis(EAxis::Y);
    FCollisionQueryParams Query(SCENE_QUERY_STAT(DynaDriveGround), false, this);
    FVector Average = FVector::ZeroVector, Normal = FVector::ZeroVector;
    double Low = TNumericLimits<double>::Max(), High = -TNumericLimits<double>::Max();
    for (double X : {-135.0, 135.0}) for (double Y : {-70.0, 70.0})
    {
        const FVector Probe = Centre + Forward * X + Right * Y;
        FHitResult Hit;
        const double Above = Initial ? 300 : 60;
        const double Below = Initial ? 900 : 300;
        if (!GetWorld()->LineTraceSingleByChannel(Hit, Probe + FVector(0, 0, Above),
            Probe - FVector(0, 0, Below), ECC_Visibility, Query))
        { BlockedReason = TEXT("Missing wheel support"); return false; }
        if (Hit.ImpactNormal.Z < 0.7) { BlockedReason = TEXT("Surface too steep"); return false; }
        Average += Hit.ImpactPoint; Normal += Hit.ImpactNormal;
        Low = FMath::Min(Low, Hit.ImpactPoint.Z); High = FMath::Max(High, Hit.ImpactPoint.Z);
    }
    Average /= 4; Normal.Normalize();
    if (High - Low > 150) { BlockedReason = TEXT("Uneven wheel support"); return false; }
    // Initial placement is validated against the source candidate elevation.
    if ((Initial && FMath::Abs(Average.Z - (Centre.Z - 120)) > 35) ||
        (!Initial && FMath::Abs(Average.Z - (Centre.Z - 75)) > 45))
    { BlockedReason = TEXT("Ground elevation changed too far"); return false; }
    const FVector Tangent = FVector::VectorPlaneProject(Forward, Normal).GetSafeNormal();
    Rotation = FRotationMatrix::MakeFromXZ(Tangent, Normal).Rotator();
    Position = FVector(Centre.X, Centre.Y, Average.Z + 75);
    // Translation sweeps do not cover rotation. Check each destination pose too.
    const auto Blocked = [&](const FVector& Test) {
        return GetWorld()->OverlapBlockingTestByChannel(Test, Rotation.Quaternion(), ECC_Pawn,
            FCollisionShape::MakeBox(Chassis->GetScaledBoxExtent()), Query);
    };
    if (Blocked(Position))
    {
        // Only small road lips qualify; buildings still block the raised pose.
        if (Initial || Blocked(Position + FVector(0,0,25)))
        { BlockedReason = TEXT("Chassis clearance blocked"); return false; }
        Position.Z += 25;
    }
    BlockedReason.Reset();
    return true;
}

bool ADynaDrivePawn::MoveGrounded(const FVector& Position, const FRotator& Rotation, FHitResult& Hit)
{
    const FTransform Before = GetActorTransform();
    SetActorLocationAndRotation(Position,Rotation,true,&Hit,ETeleportType::None);
    if (!Hit.bBlockingHit) return true;
    SetActorTransform(Before,false,nullptr,ETeleportType::TeleportPhysics);
    // A step is bounded to 25 cm and must clear all three swept segments.
    const FVector Lift(0,0,25);
    SetActorLocation(Before.GetLocation()+Lift,true,&Hit);
    if (!Hit.bBlockingHit)
    {
        SetActorLocationAndRotation(Position+Lift,Rotation,true,&Hit);
        if (!Hit.bBlockingHit)
        {
            SetActorLocation(Position,true,&Hit);
            if (!Hit.bBlockingHit) return true;
        }
    }
    SetActorTransform(Before,false,nullptr,ETeleportType::TeleportPhysics);
    BlockedReason = TEXT("Obstacle blocks swept chassis");
    return false;
}

void ADynaDrivePawn::Step(float Dt, float Throttle, float Steering, bool Brake)
{
    const float NextSpeed = static_cast<float>(DynaDrive::SpeedStep(SpeedCmS, Throttle, Brake, Dt, GetActorForwardVector().Z));
    const float NextHeading = Heading + static_cast<float>(DynaDrive::YawStepDegrees(NextSpeed, Steering, Dt));
    // If turning would wedge the body, retain the old heading to allow escape.
    for (float CandidateHeading : {NextHeading, Heading})
    {
        const FVector Destination = GetActorLocation() + FRotator(0,CandidateHeading,0).Vector()*NextSpeed*Dt;
        FVector Position; FRotator Rotation; FHitResult Hit;
        if (SampleSurface(Destination,CandidateHeading,Position,Rotation,false) && MoveGrounded(Position,Rotation,Hit))
        {
            SpeedCmS = NextSpeed; Heading = Rotation.Yaw;
            SurfaceStatus = TEXT("Source surface available"); BlockedReason.Reset();
            RememberSafePosition(); return;
        }
    }
    SpeedCmS = 0;
    SurfaceStatus = TEXT("Blocked - reverse, U unsticks, F flies, R resets");
}

void ADynaDrivePawn::Tick(float DeltaSeconds)
{
    Super::Tick(DeltaSeconds);
    VehicleLights->Reset();
    if(!FlyMode && Body->GetStaticMesh())VehicleLights->AddVehicle(Body->GetComponentTransform(),Body->GetStaticMesh()->GetBoundingBox(),
        TurnSignal | (BrakeLightsOn?8:0),GetWorld()->GetTimeSeconds(),NightMode);
    VehicleLights->Flush();
    const double TickTime = FPlatformTime::Seconds();
    if (LastTickWallTime > 0) TickWallIntervalMs = (TickTime - LastTickWallTime) * 1000.0;
    LastTickWallTime = TickTime;
    StreetLightRefreshSeconds += DeltaSeconds;
    if (StreetLightRefreshSeconds >= 1.0f)
    {
        StreetLightRefreshSeconds = 0;
        UpdateStreetLights();
    }
    if (FlyMode)
    {
        TickFlight(DeltaSeconds); return;
    }
    if (!Body->GetStaticMesh())
    {
        SpeedCmS = 0; SurfaceReady = false;
        SurfaceStatus = TEXT("Vehicle mesh missing - prepare the driving setup before Play");
        return;
    }
    if (!SurfaceReady)
    {
        SurfaceWaitSeconds += DeltaSeconds;
        FVector Position; FRotator Rotation;
        if (SampleSurface(GetActorLocation(), Heading, Position, Rotation, WaitingInitialSurface))
        {
            SetActorLocationAndRotation(Position, Rotation, false, nullptr, ETeleportType::TeleportPhysics);
            Chassis->SetCollisionEnabled(ECollisionEnabled::QueryAndPhysics);
            SurfaceReady = true; SurfaceStatus = TEXT("Source surface available");
            RememberSafePosition();
        }
        else if (SurfaceWaitSeconds > 15) SurfaceStatus = TEXT("No safe loaded surface - F flies, U recovers, N changes area");
        UpdateCamera(DeltaSeconds); return;
    }
    float Throttle = ExternalThrottle, Steering = ExternalSteering; bool Brake = ExternalBrake;
    if (!ExternalInputs)
    {
        Throttle = Steering = 0; Brake = true;
        if (APlayerController* PC = Cast<APlayerController>(GetController()); PC && !PC->bShowMouseCursor)
        {
            Throttle = float(PC->IsInputKeyDown(EKeys::W) || PC->IsInputKeyDown(EKeys::Up)) - float(PC->IsInputKeyDown(EKeys::S) || PC->IsInputKeyDown(EKeys::Down));
            Steering = float(PC->IsInputKeyDown(EKeys::D) || PC->IsInputKeyDown(EKeys::Right)) - float(PC->IsInputKeyDown(EKeys::A) || PC->IsInputKeyDown(EKeys::Left));
            Brake = PC->IsInputKeyDown(EKeys::SpaceBar);
        }
    }
    // Bound each simulation step to avoid tunnelling after a slow frame.
    BrakeLightsOn=Brake || (SpeedCmS*Throttle<0);
    const float Time = FMath::Clamp(DeltaSeconds, 0.0f, 0.1f);
    const int32 Count = FMath::Max(1, FMath::CeilToInt(Time * 60));
    for (int32 I = 0; I < Count; ++I) Step(Time / Count, Throttle, Steering, Brake);
    UpdateCamera(DeltaSeconds);
}

void ADynaDrivePawn::CycleCamera()
{
    CameraView = (CameraView + 1) % 4;
    CameraYaw = 0; CameraPitch = -12;
}

ADynaMobilityWorld* ADynaDrivePawn::CreateMobilityWorld()
{
    if(!IsValid(Mobility))Mobility=GetWorld()->SpawnActor<ADynaMobilityWorld>();
    return Mobility;
}

void ADynaDrivePawn::UpdateStreetLights()
{
    if (!GetWorld()) return;
    for (TActorIterator<APointLight> It(GetWorld()); It; ++It)
    {
        if (!It->ActorHasTag(TEXT("DYNACITY_PILOT_LIGHT")) &&
            !It->ActorHasTag(TEXT("DYNACITY_STREET_LIGHT"))) continue;
        if (auto* Light = It->GetLightComponent())
        {
            const float Target = NightMode ? 1600.0f : 0.0f;
            if (!FMath::IsNearlyEqual(Light->Intensity, Target)) Light->SetIntensity(Target);
        }
    }
}

void ADynaDrivePawn::ToggleNight() { SetNightMode(!NightMode); }

void ADynaDrivePawn::SetNightMode(bool Enabled)
{
    SetTimeOfDay(Enabled?22.f:14.f);
}

void ADynaDrivePawn::SetTimeOfDay(float Hour)
{
    if(!FMath::IsFinite(Hour))return;
    TimeOfDay=FMath::Clamp(Hour,0.f,24.f);
    const float Elevation=FMath::Sin((TimeOfDay-6.f)/12.f*PI);
    const float Day=FMath::Clamp(Elevation*3.f,0.f,1.f);
    const bool Enabled=Elevation<=.08f;
    NightMode=Enabled;
    UpdateStreetLights();
    for(TActorIterator<ADirectionalLight> It(GetWorld());It;++It) {
        auto* Light=It->FindComponentByClass<UDirectionalLightComponent>();
        if(!DayIntensities.Contains(Light))DayIntensities.Add(Light,Light->Intensity);
        // Below the horizon this light becomes the moon: opposite the sun, cool blue, a fraction of daylight.
        const bool MoonUp=Elevation<0.f;
        Light->SetIntensity(FMath::Lerp(DayIntensities[Light]*MoonFraction,DayIntensities[Light],Day));
        It->SetActorRotation(FRotator(MoonUp?Elevation*65.f:-Elevation*65.f,TimeOfDay*15.f-120.f+(MoonUp?180.f:0.f),0));
        Light->SetLightColor(MoonUp?FLinearColor(.55f,.7f,1.f)
            :FMath::Lerp(FLinearColor(1.f,.55f,.3f),FLinearColor(1.f,.96f,.9f),FMath::Clamp(Elevation*2.f,0.f,1.f)));
    }
    for(TActorIterator<ASkyLight> It(GetWorld());It;++It) {
        auto* Light=It->GetLightComponent();
        if(!DayIntensities.Contains(Light))DayIntensities.Add(Light,Light->Intensity);
        // Night floor so shadows and streets stay readable under moon and starlight.
        Light->SetIntensity(FMath::Lerp(DayIntensities[Light]*NightSkyFraction,DayIntensities[Light],Day));
        Light->SetLightColor(FMath::Lerp(FLinearColor(.45f,.6f,1.f),FLinearColor::White,Day));
    }
    LeftHeadlight->SetVisibility(Enabled);RightHeadlight->SetVisibility(Enabled);
    if(AmbientSky)AmbientSky->SetHour(TimeOfDay);
}

void ADynaDrivePawn::ConfigureAmbientEffects(UMaterialInterface* CloudMaterial,const TArray<UMaterialInterface*>& LampMaterials,FVector Up)
{
    VehicleLights->Configure(LampMaterials);
    if(Mobility)Mobility->ConfigureLights(LampMaterials);
    if(CloudMaterial) {
        if(!IsValid(AmbientSky)) {
            AmbientSky=GetWorld()->SpawnActor<ADynaAmbientSky>();
            AmbientSky->SetOwner(this);
        }
        AmbientSky->Configure(CloudMaterial,Up);
        AmbientSky->SetClouds(CloudsEnabled);
    }
    SetTimeOfDay(TimeOfDay);
}
void ADynaDrivePawn::SetCloudsEnabled(bool Enabled)
{ CloudsEnabled=Enabled;if(AmbientSky)AmbientSky->SetClouds(Enabled); }
void ADynaDrivePawn::SetTurnSignal(int32 Direction)
{ if(Direction>=0 && Direction<=4 && Direction!=3)TurnSignal=Direction; }
void ADynaDrivePawn::ToggleLeftSignal() { SetTurnSignal(TurnSignal==2?0:2); }
void ADynaDrivePawn::ToggleRightSignal() { SetTurnSignal(TurnSignal==1?0:1); }
void ADynaDrivePawn::ToggleHazards() { SetTurnSignal(TurnSignal==4?0:4); }

void ADynaDrivePawn::UpdateCamera(float Dt)
{
    if (FlyMode)
    {
        CameraArm->TargetArmLength = 0;
        CameraArm->SetRelativeLocation(FVector::ZeroVector);
        CameraArm->SetRelativeRotation(FRotator(FlyPitch, 0, 0));
        CameraArm->bEnableCameraLag = false; CameraArm->bDoCollisionTest = false;
        Body->SetVisibility(false); Cockpit->SetVisibility(false);
        Camera->SetFieldOfView(80);
        return;
    }
    if (APlayerController* PC = Cast<APlayerController>(GetController()); PC && !PC->bShowMouseCursor && PC->IsInputKeyDown(EKeys::RightMouseButton))
    {
        float X, Y; PC->GetInputMouseDelta(X, Y);
        CameraYaw = FMath::Clamp(CameraYaw + X, -140.0f, 140.0f);
        CameraPitch = FMath::Clamp(CameraPitch - Y, -45.0f, 5.0f);
    }
    else CameraYaw = FMath::FInterpTo(CameraYaw, 0.0f, Dt, 3.0f);
    const bool Inside=CameraView==3;
    CameraArm->TargetArmLength = CameraView == 0 ? 650 : CameraView == 2 ? 1100 : 0;
    CameraArm->SetRelativeLocation(Inside?DriverEyeCm:CameraView==1?FVector(180,0,90):FVector(0,0,110));
    CameraArm->SetRelativeRotation(FRotator(CameraView==2?-45:Inside?-4:CameraView==1?-5:CameraPitch,CameraYaw,0));
    CameraArm->bEnableCameraLag = SurfaceReady && (CameraView==0||CameraView==2);
    CameraArm->bDoCollisionTest=!Inside;
    Body->SetVisibility(!Inside);
    Cockpit->SetVisibility(Inside);
    Camera->SetFieldOfView(Inside?88:75);
}

void ADynaDrivePawn::ToggleCursor()
{
    if (APlayerController* PC = Cast<APlayerController>(GetController()))
    {
        if (PC->bShowMouseCursor) ResumeDriving();
        else OpenControls();
    }
}

FString ADynaDrivePawn::GetDriveStatus() const
{
    TSharedRef<FJsonObject> Data = MakeShared<FJsonObject>();
    Data->SetNumberField(TEXT("speed_kmh"), SpeedCmS * .036);
    Data->SetNumberField(TEXT("x_cm"), GetActorLocation().X);
    Data->SetNumberField(TEXT("y_cm"), GetActorLocation().Y);
    Data->SetNumberField(TEXT("z_cm"), GetActorLocation().Z);
    Data->SetNumberField(TEXT("yaw_degrees"), GetActorRotation().Yaw);
    Data->SetStringField(TEXT("start_area"), CurrentArea);
    Data->SetStringField(TEXT("start_road"), CurrentRoad);
    Data->SetStringField(TEXT("surface_status"), SurfaceStatus);
    Data->SetBoolField(TEXT("surface_ready"), SurfaceReady);
    Data->SetBoolField(TEXT("fly_mode"), FlyMode);
    Data->SetBoolField(TEXT("recovery_available"), !SafePositions.IsEmpty());
    Data->SetStringField(TEXT("blocked_reason"), BlockedReason);
    Data->SetNumberField(TEXT("fly_speed_cm_s"), FlyVelocity.Size());
    Data->SetNumberField(TEXT("camera_view"), CameraView);
    Data->SetBoolField(TEXT("night_mode"), NightMode);
    Data->SetNumberField(TEXT("time_of_day"),TimeOfDay);
    Data->SetBoolField(TEXT("clouds_enabled"),CloudsEnabled);
    Data->SetNumberField(TEXT("turn_signal"),TurnSignal);
    Data->SetBoolField(TEXT("cockpit_available"), Cockpit->GetStaticMesh()!=nullptr);
    Data->SetNumberField(TEXT("driver_eye_y_cm"),DriverEyeCm.Y);
    Data->SetNumberField(TEXT("render_frame_ms"),GetWorld()->GetDeltaSeconds()*1000.0);
    Data->SetNumberField(TEXT("game_tick_wall_ms"), TickWallIntervalMs);
    Data->SetStringField(TEXT("mobility"),Mobility?Mobility->Status():TEXT("{}"));
    Data->SetStringField(TEXT("movement_model"), FlyMode ? TEXT("free-flight exploration; F returns to parked car") : TEXT("arcade bicycle steering; swept chassis; bounded curb steps; source-surface probes"));
    FString Result;
    const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Result);
    FJsonSerializer::Serialize(Data, Writer);
    return Result;
}

ADynaDriveGameMode::ADynaDriveGameMode()
{
    DefaultPawnClass = ADynaDrivePawn::StaticClass();
    HUDClass = ADynaDriveHUD::StaticClass();
}

AActor* ADynaDriveGameMode::ChoosePlayerStart_Implementation(AController* Player)
{
    for (TActorIterator<ADynaDriveStart> It(GetWorld()); It; ++It) if (It->Preferred) return *It;
    return Super::ChoosePlayerStart_Implementation(Player);
}

void ADynaDriveHUD::BeginPlay()
{
    Super::BeginPlay();
    if (GetWorld() && GetWorld()->GetGameViewport())
    {
        TSharedRef<SWrapBox> Shortcuts = SNew(SWrapBox)
            .UseAllottedSize(true).InnerSlotPadding(FVector2D(20, 8));
        for (const TCHAR* Shortcut : {
            TEXT("W / S  Accelerate / reverse"), TEXT("A / D  Steer"),
            TEXT("Space  Brake"), TEXT("U  Unstick car"), TEXT("R  Reset car"), TEXT("N  Next area"),
            TEXT("F  Fly / return to car"), TEXT("Fly: WASD + mouse"),
            TEXT("Fly: E / Q  Up / down"), TEXT("Fly: Shift  Faster"),
            TEXT("C  Camera / cockpit"), TEXT("L  Day / night"),
            TEXT("M  Map style"), TEXT("B  Map orientation"),
            TEXT("F1 / Tab  Menu / return to driving"), TEXT("F3  Location search")})
        {
            Shortcuts->AddSlot()
            [SNew(STextBlock).Text(FText::FromString(Shortcut))
                .Font(FCoreStyle::GetDefaultFontStyle("Regular", 11))
                .ColorAndOpacity(FLinearColor(.88f, .94f, .96f))];
        }
        ControlsButton = SNew(SOverlay).Visibility(EVisibility::SelfHitTestInvisible)
            + SOverlay::Slot().HAlign(HAlign_Fill).VAlign(VAlign_Bottom).Padding(20, 0, 20, 16)
            [SAssignNew(ControlsGuide, SBorder).Visibility(EVisibility::HitTestInvisible)
                .BorderImage(FCoreStyle::Get().GetBrush("WhiteBrush"))
                .BorderBackgroundColor(FLinearColor(.025f, .04f, .055f, .94f))
                .Padding(12)
                [Shortcuts]]
            + SOverlay::Slot().HAlign(HAlign_Right).VAlign(VAlign_Top).Padding(20)
            [SNew(SButton).Text(FText::FromString(TEXT("Menu & controls  [F1]")))
                .OnClicked_Lambda([this]()
                {
                    if (APlayerController* PC = GetOwningPlayerController())
                        if (ADynaDrivePawn* Car = Cast<ADynaDrivePawn>(PC->GetPawn())) Car->OpenControls();
                    return FReply::Handled();
                })];
        GetWorld()->GetGameViewport()->AddViewportWidgetContent(ControlsButton.ToSharedRef(), 20);
    }
}

void ADynaDriveHUD::EndPlay(const EEndPlayReason::Type Reason)
{
    if (ControlsButton.IsValid() && GetWorld() && GetWorld()->GetGameViewport())
        GetWorld()->GetGameViewport()->RemoveViewportWidgetContent(ControlsButton.ToSharedRef());
    ControlsButton.Reset();
    ControlsGuide.Reset();
    Super::EndPlay(Reason);
}

void ADynaDriveHUD::DrawHUD()
{
    Super::DrawHUD();
    const APlayerController* PC = GetOwningPlayerController();
    const ADynaDrivePawn* Car = PC ? Cast<ADynaDrivePawn>(PC->GetPawn()) : nullptr;
    if (!Canvas || !Car) return;
    const float Width = FMath::Min(420.0f, Canvas->ClipX - 40);
    DrawRect(FLinearColor(.025f, .04f, .055f, .78f), 20, 20, Width, 82);
    UFont* Font = GEngine->GetSmallFont();
    DrawText(TEXT("DynaCITY  |  Explore Beirut"), FLinearColor(.7f, .9f, .9f), 32, 28, Font, 1.0f);
    DrawText(FString::Printf(TEXT("%s  |  %d km/h"), Car->FlyMode ? TEXT("FREE FLIGHT - F returns to car") : *Car->CurrentArea,
        FMath::RoundToInt(Car->GetVelocity().Size() * .036f)),
        FLinearColor::White, 32, 52, Font, 1.05f);
    DrawText(Car->SurfaceStatus, FLinearColor(.9f, .8f, .55f), 32, 79, Font, .85f);
    if (Car->Mobility)
    {
        const FString Street = Car->Mobility->StreetAt(Car->GetActorLocation());
        const FString Label = Street.IsEmpty() ? TEXT("Unnamed road") : Street;
        float TextW = 0, TextH = 0;
        GetTextSize(Label, TextW, TextH, Font, 1.5f);
        const float BoxW = FMath::Min(Canvas->ClipX - 40, TextW + 40);
        const float BoxX = (Canvas->ClipX - BoxW) * .5f;
        DrawRect(FLinearColor(.025f, .04f, .055f, .78f), BoxX, 20, BoxW, TextH + 16);
        DrawText(Label, Street.IsEmpty() ? FLinearColor(.7f, .75f, .78f) : FLinearColor::White,
            BoxX + (BoxW - TextW) * .5f, 28, Font, 1.5f);
    }
    const float MapSize=FMath::Clamp(Canvas->ClipY*.28f,220.f,280.f);
    // Slate reports logical units; Canvas uses viewport pixels. Keep the map
    // above the wrapping shortcut strip even in a narrow or DPI-scaled viewport.
    float ControlsHeight = 82.f;
    if (ControlsButton.IsValid() && ControlsGuide.IsValid() && GetWorld() && GetWorld()->GetGameViewport())
    {
        FVector2D ViewportSize;
        GetWorld()->GetGameViewport()->GetViewportSize(ViewportSize);
        const FGeometry& Geometry = ControlsButton->GetCachedGeometry();
        if (Geometry.GetLocalSize().Y > 0)
            ControlsHeight = FMath::Max(ControlsHeight,
                (ControlsGuide->GetCachedGeometry().GetLocalSize().Y + 32.f) * ViewportSize.Y / Geometry.GetLocalSize().Y);
    }
    if(Car->Mobility)Car->Mobility->DrawMinimap(this,Car,24,Canvas->ClipY-MapSize-ControlsHeight,MapSize,Canvas);
}
