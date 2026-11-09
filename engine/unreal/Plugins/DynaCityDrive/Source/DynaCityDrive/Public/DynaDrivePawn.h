#pragma once
#include "CoreMinimal.h"
#include "GameFramework/Pawn.h"
#include "GameFramework/PlayerStart.h"
#include "GameFramework/GameModeBase.h"
#include "GameFramework/HUD.h"
#include "DynaDrivePawn.generated.h"

class UBoxComponent;
class UStaticMeshComponent;
class USpringArmComponent;
class UCameraComponent;
class UStaticMesh;
class ADynaMobilityWorld;
class USpotLightComponent;
class ULightComponentBase;
class UDynaVehicleLights;
class ADynaAmbientSky;
class UMaterialInterface;

UCLASS()
class DYNACITYDRIVE_API ADynaDriveStart : public APlayerStart
{
    GENERATED_BODY()
public:
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Beirut") FString Area;
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Beirut") FString RoadName;
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Beirut") int32 RoadId = -1;
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Beirut") bool Preferred = false;
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Beirut") TObjectPtr<UStaticMesh> VehicleMesh;
};

UCLASS()
class DYNACITYDRIVE_API ADynaDrivePawn : public APawn
{
    GENERATED_BODY()
public:
    ADynaDrivePawn();
    virtual void BeginPlay() override;
    virtual void EndPlay(const EEndPlayReason::Type Reason) override;
    virtual void Tick(float DeltaSeconds) override;
    virtual void SetupPlayerInputComponent(UInputComponent* Input) override;
    virtual FVector GetVelocity() const override { return FlyMode ? FlyVelocity : GetActorForwardVector() * SpeedCmS; }

    UFUNCTION(BlueprintCallable, Category="Beirut|Driving") void ResetToStart();
    UFUNCTION(BlueprintCallable, Category="Beirut|Driving") void NextArea();
    UFUNCTION(BlueprintCallable, Category="Beirut|Driving") void RecoverVehicle();
    UFUNCTION(BlueprintCallable, Category="Beirut|Driving") void ToggleFlyMode();
    UFUNCTION(BlueprintCallable, Category="Beirut|Driving") void SetFlyMode(bool Enabled);
    UFUNCTION(BlueprintCallable, Category="Beirut|Driving") void SetFlyInputs(float Forward, float Right, float Up, float Yaw, float Pitch, bool Boost);
    UFUNCTION(BlueprintCallable, Category="Beirut|Driving") void CycleCamera();
    UFUNCTION(BlueprintCallable, Category="Beirut|Driving") void ToggleNight();
    UFUNCTION(BlueprintCallable, Category="Beirut|Driving") void CycleMinimap();
    UFUNCTION(BlueprintCallable, Category="Beirut|Driving") void ToggleMinimapOrientation();
    UFUNCTION(BlueprintCallable, Category="Beirut|Driving") void SetNightMode(bool Enabled);
    UFUNCTION(BlueprintCallable, Category="Beirut|Atmosphere") void SetTimeOfDay(float Hour);
    UFUNCTION(BlueprintCallable, Category="Beirut|Atmosphere") void SetCloudsEnabled(bool Enabled);
    UFUNCTION(BlueprintCallable, Category="Beirut|Atmosphere") void ConfigureAmbientEffects(UMaterialInterface* CloudMaterial, const TArray<UMaterialInterface*>& LampMaterials, FVector Up);
    UFUNCTION(BlueprintCallable, Category="Beirut|Driving") void SetTurnSignal(int32 Direction);
    void ToggleLeftSignal();
    void ToggleRightSignal();
    void ToggleHazards();
    UPROPERTY(VisibleAnywhere, Category="Beirut|Atmosphere") TObjectPtr<UDynaVehicleLights> VehicleLights;
    UPROPERTY(Transient) TObjectPtr<ADynaAmbientSky> AmbientSky;
    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category="Beirut|Atmosphere") float TimeOfDay = 14;
    /** Moonlight strength as a fraction of the daytime sun intensity. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Beirut|Atmosphere") float MoonFraction = .06f;
    /** Night skylight strength as a fraction of the daytime skylight intensity. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Beirut|Atmosphere") float NightSkyFraction = .12f;
    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category="Beirut|Atmosphere") bool CloudsEnabled = true;
    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category="Beirut|Atmosphere") int32 TurnSignal = 0;
    UFUNCTION(BlueprintCallable, Category="Beirut|Driving") ADynaMobilityWorld* CreateMobilityWorld();
    UFUNCTION(BlueprintCallable, Category="Beirut|Driving") void ToggleCursor();
    UFUNCTION(BlueprintCallable, Category="Beirut|Driving") void OpenLocationSearch();
    UFUNCTION(BlueprintCallable, Category="Beirut|Driving") void OpenControls();
    UFUNCTION(BlueprintCallable, Category="Beirut|Driving") void ResumeDriving();
    UFUNCTION(BlueprintCallable, Category="Beirut|Driving") bool TryLocation(const FVector& SourcePosition, float Yaw, const FString& PlaceName);
    // Also usable by a gamepad/touch interface and deterministic verification.
    UFUNCTION(BlueprintCallable, Category="Beirut|Driving") void SetDriveInputs(float Throttle, float Steering, bool Brake);
    UFUNCTION(BlueprintCallable, Category="Beirut|Driving") void ReleaseDriveInputs();
    UFUNCTION(BlueprintPure, Category="Beirut|Driving") FString GetDriveStatus() const;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category="Beirut|Driving") TObjectPtr<UBoxComponent> Chassis;
    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category="Beirut|Driving") TObjectPtr<UStaticMeshComponent> Body;
    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category="Beirut|Driving") TObjectPtr<USpringArmComponent> CameraArm;
    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category="Beirut|Driving") TObjectPtr<UCameraComponent> Camera;
    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category="Beirut|Driving") TObjectPtr<UStaticMeshComponent> Cockpit;
    // Supplied by the approved source vehicle's authored steering/seat geometry.
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Beirut|Driving") FVector DriverEyeCm = FVector(-22,-34,43);
    UPROPERTY(VisibleAnywhere, Category="Beirut") TObjectPtr<USpotLightComponent> LeftHeadlight;
    UPROPERTY(VisibleAnywhere, Category="Beirut") TObjectPtr<USpotLightComponent> RightHeadlight;
    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category="Beirut") TObjectPtr<ADynaMobilityWorld> Mobility;
    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category="Beirut") bool NightMode = false;
    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category="Beirut|Driving") float SpeedCmS = 0;
    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category="Beirut|Driving") FString CurrentArea;
    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category="Beirut|Driving") FString CurrentRoad;
    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category="Beirut|Driving") FString SurfaceStatus;
    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category="Beirut|Driving") bool SurfaceReady = false;
    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category="Beirut|Driving") bool FlyMode = false;

private:
    bool SampleSurface(const FVector& Centre, float Yaw, FVector& Position, FRotator& Rotation, bool Initial) const;
    void Step(float Dt, float Throttle, float Steering, bool Brake);
    void TickFlight(float Dt);
    void UpdateStreetLights();
    void RememberSafePosition();
    bool MoveGrounded(const FVector& Position, const FRotator& Rotation, FHitResult& Hit);
    friend class FDynaDriveTraversalTest;
    void UpdateCamera(float Dt);
    void CollectStarts();
    void UseStart(int32 Index);
    TArray<TWeakObjectPtr<ADynaDriveStart>> Starts;
    FTransform ResetTransform;
    FTransform FlightReturnTransform;
    TArray<FTransform> SafePositions;
    FVector FlyVelocity = FVector::ZeroVector;
    FVector ExternalFlyInput = FVector::ZeroVector;
    float FlyYaw = 0;
    float FlyPitch = -15;
    bool ExternalFlight = false;
    bool ExternalFlyBoost = false;
    bool WaitingInitialSurface = true;
        bool FlightReturnInitialSurface = true;
    mutable FString BlockedReason;
    float Heading = 0;
    double LastTickWallTime = 0;
    double TickWallIntervalMs = 0;
    float SurfaceWaitSeconds = 0;
    float StreetLightRefreshSeconds = 0;
    int32 CurrentStart = 0;
    int32 CameraView = 0;
    float CameraYaw = 0;
    float CameraPitch = -12;
    bool ExternalInputs = false;
    float ExternalThrottle = 0;
    float ExternalSteering = 0;
    bool ExternalBrake = false;
    bool BrakeLightsOn = false;
    TMap<TWeakObjectPtr<ULightComponentBase>, float> DayIntensities;
};

UCLASS()
class DYNACITYDRIVE_API ADynaDriveGameMode : public AGameModeBase
{
    GENERATED_BODY()
public:
    ADynaDriveGameMode();
    virtual AActor* ChoosePlayerStart_Implementation(AController* Player) override;
};

UCLASS()
class DYNACITYDRIVE_API ADynaDriveHUD : public AHUD
{
    GENERATED_BODY()
public:
    virtual void BeginPlay() override;
    virtual void EndPlay(const EEndPlayReason::Type EndPlayReason) override;
    virtual void DrawHUD() override;
private:
    TSharedPtr<class SWidget> ControlsButton;
    TSharedPtr<class SWidget> ControlsGuide;
};
