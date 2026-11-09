#include "DynaAmbientSky.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/StaticMesh.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Components/VolumetricCloudComponent.h"

ADynaAmbientSky::ADynaAmbientSky()
{
    PrimaryActorTick.bCanEverTick=true;PrimaryActorTick.TickInterval=.2f;
    Clouds=CreateDefaultSubobject<UStaticMeshComponent>(TEXT("CloudShell"));SetRootComponent(Clouds);
    Clouds->SetMobility(EComponentMobility::Movable);Clouds->SetCollisionEnabled(ECollisionEnabled::NoCollision);
    Clouds->SetCanEverAffectNavigation(false);Clouds->SetCastShadow(false);
    Volumetric=CreateDefaultSubobject<UVolumetricCloudComponent>(TEXT("VolumetricCloud"));Volumetric->SetupAttachment(Clouds);
}
void ADynaAmbientSky::Configure(UMaterialInterface* Material,FVector Up)
{
    Clouds->SetStaticMesh(LoadObject<UStaticMesh>(nullptr,TEXT("/Engine/BasicShapes/Sphere.Sphere")));
    SetActorRotation(FRotationMatrix::MakeFromZ(Up.GetSafeNormal()).Rotator());
    SetActorScale3D(FVector(8000));
    CloudMaterial=UMaterialInstanceDynamic::Create(Material,this);Clouds->SetMaterial(0,CloudMaterial);
    // Stock engine cloud material, lit by the atmosphere sun/moon light and sky; the dome stays as the fallback.
    UMaterialInterface* Stock=bUseVolumetricClouds?LoadObject<UMaterialInterface>(nullptr,TEXT("/Engine/EngineSky/VolumetricClouds/m_SimpleVolumetricCloud_Inst.m_SimpleVolumetricCloud_Inst")):nullptr;
    VolumetricActive=Stock!=nullptr;
    if(VolumetricActive)Volumetric->SetMaterial(Stock);
    Clouds->SetVisibility(!VolumetricActive);
}
void ADynaAmbientSky::Tick(float DeltaSeconds)
{
    Super::Tick(DeltaSeconds);
    if(GetOwner())SetActorLocation(GetOwner()->GetActorLocation());
}
void ADynaAmbientSky::SetHour(float Hour)
{
    if(!CloudMaterial || VolumetricActive)return;
    const float Day=FMath::Clamp(FMath::Sin((Hour-6)/12*PI)*3.f,0.f,1.f);
    const FLinearColor DayColour=Hour>16?FLinearColor(.85f,.57f,.36f):FLinearColor(.85f,.89f,.94f);
    CloudMaterial->SetVectorParameterValue(TEXT("CloudColor"),FMath::Lerp(FLinearColor(.012f,.018f,.03f),DayColour,Day));
}
void ADynaAmbientSky::SetClouds(bool Enabled) { Clouds->SetVisibility(Enabled && !VolumetricActive);Volumetric->SetVisibility(Enabled && VolumetricActive); }
