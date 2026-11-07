#include "DynaMinimapComponent.h"
#include "DynaMapMath.h"
#include "DynaMobilityWorld.h"
#include "Async/Async.h"
#include "CanvasItem.h"
#include "Dom/JsonObject.h"
#include "Engine/Canvas.h"
#include "Engine/Engine.h"
#include "Engine/Texture2D.h"
#include "GameFramework/HUD.h"
#include "GameFramework/Pawn.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformTime.h"
#include "HttpModule.h"
#include "IImageWrapper.h"
#include "IImageWrapperModule.h"
#include "Interfaces/IHttpRequest.h"
#include "Interfaces/IHttpResponse.h"
#include "Misc/DateTime.h"
#include "Misc/FileHelper.h"
#include "Misc/Guid.h"
#include "Misc/Paths.h"
#include "Misc/ScopeLock.h"
#include "Modules/ModuleManager.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#if PLATFORM_WINDOWS
#include "Windows/WindowsHWrapper.h"
#else
#include <cstdio>
#endif

struct FDynaTileData
{
    TArray<uint8> Compressed;
    TArray64<uint8> Pixels;
    int32 Width = 0, Height = 0;
    double FreshUntil = 0;
    FString ETag, Modified, CacheControl, Expires;
    bool NoStore = false, MustRevalidate = false;
};

namespace
{
constexpr int32 MaxLoads = 4, MaxMemoryTiles = 96;
constexpr double Week = 7*24*60*60;
double UtcNow() { return double(FDateTime::UtcNow().ToUnixTimestamp()); }
FString CachePath(const FString& Key) { return FPaths::Combine(FPaths::ProjectSavedDir(),TEXT("DynaCity/MapTiles/v1"),Key); }
int32 Provider(const FString& Key) { return Key.StartsWith(TEXT("street"))?0:1; }
bool ParseJson(const FString& Text,TSharedPtr<FJsonObject>& Out)
{ return FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text),Out) && Out.IsValid(); }
const TCHAR* ModeName(EDynaBasemap Mode)
{ return Mode==EDynaBasemap::Street?TEXT("street"):Mode==EDynaBasemap::Satellite?TEXT("satellite"):TEXT("hybrid"); }
bool ParseMode(const FString& Name,EDynaBasemap& Out)
{
    if(Name.Equals(TEXT("street"),ESearchCase::CaseSensitive))Out=EDynaBasemap::Street;
    else if(Name.Equals(TEXT("satellite"),ESearchCase::CaseSensitive))Out=EDynaBasemap::Satellite;
    else if(Name.Equals(TEXT("hybrid"),ESearchCase::CaseSensitive))Out=EDynaBasemap::Hybrid;
    else return false;
    return true;
}

// All disk I/O and image decompression runs in a bounded thread-pool task.
bool Decode(FDynaTileData& Data,IImageWrapperModule* Images)
{
    if(Data.Compressed.IsEmpty() || Data.Compressed.Num()>4*1024*1024)return false;
    const EImageFormat Format=Images->DetectImageFormat(Data.Compressed.GetData(),Data.Compressed.Num());
    if(Format!=EImageFormat::PNG && Format!=EImageFormat::JPEG)return false;
    auto Image=Images->CreateImageWrapper(Format);
    if(!Image.IsValid() || !Image->SetCompressed(Data.Compressed.GetData(),Data.Compressed.Num()))return false;
    Data.Width=Image->GetWidth(); Data.Height=Image->GetHeight();
    if(Data.Width<1 || Data.Height<1 || Data.Width>1024 || Data.Height>1024)return false;
    return Image->GetRaw(ERGBFormat::BGRA,8,Data.Pixels) && Data.Pixels.Num()==int64(Data.Width)*Data.Height*4;
}
void ReadCache(const FString& Key,FDynaTileData& Data,IImageWrapperModule* Images)
{
    FString Text; TSharedPtr<FJsonObject> Meta;
    if(!FFileHelper::LoadFileToString(Text,*(CachePath(Key)+TEXT(".json"))) || !ParseJson(Text,Meta))return;
    Meta->TryGetNumberField(TEXT("fresh_until"),Data.FreshUntil);
    Meta->TryGetStringField(TEXT("etag"),Data.ETag);
    Meta->TryGetStringField(TEXT("modified"),Data.Modified);
    Meta->TryGetStringField(TEXT("cache_control"),Data.CacheControl);
    Meta->TryGetStringField(TEXT("expires"),Data.Expires);
    Meta->TryGetBoolField(TEXT("must_revalidate"),Data.MustRevalidate);
    const FString File=CachePath(Key)+TEXT(".tile");
    if(IFileManager::Get().FileSize(*File)>4*1024*1024 || !FFileHelper::LoadFileToArray(Data.Compressed,*File) || !Decode(Data,Images))
    { Data.Compressed.Reset(); Data.Pixels.Reset(); Data.FreshUntil=0; Data.ETag.Reset(); Data.Modified.Reset(); }
}
void WriteCache(const FString& Key,const FDynaTileData& Data)
{
    // Serialize writers from multiple PIE worlds. A full cache does not evict tiles
    // younger than seven days; skip new disk entries rather than churn OSM tiles.
    static FCriticalSection CacheLock;
    FScopeLock Lock(&CacheLock);
    const FString Base=CachePath(Key);
    if(Data.NoStore)
    {
        IFileManager::Get().Delete(*(Base+TEXT(".tile")));
        IFileManager::Get().Delete(*(Base+TEXT(".json")));
        return;
    }
    if(Data.Compressed.IsEmpty())return;
    const FString Directory=FPaths::GetPath(Base);
    IFileManager::Get().MakeDirectory(*Directory,true);
    constexpr int64 DiskBudget=256ll*1024*1024;
    TArray<FString> Files; IFileManager::Get().FindFiles(Files,*(Directory/TEXT("*.tile")),true,false);
    struct FDiskEntry { FString Path; int64 Bytes; FDateTime Modified; };
    TArray<FDiskEntry> Entries; int64 Bytes=0;
    for(const FString& File:Files)
    {
        const FString Path=Directory/File;
        const int64 Length=FMath::Max(int64(0),IFileManager::Get().FileSize(*Path));
        Bytes+=Length; Entries.Add({Path,Length,IFileManager::Get().GetTimeStamp(*Path)});
    }
    const int64 OldSize=FMath::Max(int64(0),IFileManager::Get().FileSize(*(Base+TEXT(".tile"))));
    Entries.Sort([](const FDiskEntry& A,const FDiskEntry& B) { return A.Modified<B.Modified; });
    for(const auto& Entry:Entries)
    {
        if(Bytes-OldSize+Data.Compressed.Num()<=DiskBudget)break;
        if(Entry.Path==Base+TEXT(".tile") || (FDateTime::UtcNow()-Entry.Modified).GetTotalSeconds()<Week)continue;
        if(IFileManager::Get().Delete(*Entry.Path))
        {
            Bytes-=Entry.Bytes;
            IFileManager::Get().Delete(*FPaths::ChangeExtension(Entry.Path,TEXT("json")));
        }
    }
    if(Bytes-OldSize+Data.Compressed.Num()>DiskBudget)return;
    // Write payload before metadata so an interrupted first write cannot appear fresh.
    const FString Temp=Base+TEXT(".tmp");
    if(!FFileHelper::SaveArrayToFile(Data.Compressed,*Temp) || !IFileManager::Get().Move(*(Base+TEXT(".tile")),*Temp,true))return;
    auto Meta=MakeShared<FJsonObject>();
    Meta->SetNumberField(TEXT("fresh_until"),Data.FreshUntil);
    Meta->SetStringField(TEXT("etag"),Data.ETag); Meta->SetStringField(TEXT("modified"),Data.Modified);
    Meta->SetStringField(TEXT("cache_control"),Data.CacheControl); Meta->SetStringField(TEXT("expires"),Data.Expires);
    Meta->SetBoolField(TEXT("must_revalidate"),Data.MustRevalidate);
    FString Text; FJsonSerializer::Serialize(Meta,TJsonWriterFactory<>::Create(&Text));
    FFileHelper::SaveStringToFile(Text,*(Base+TEXT(".json")));
}
void ReadHeaders(FDynaTileData& Data,const FHttpResponsePtr& Response,bool NotModified)
{
    auto Header=[&](const TCHAR* Name,FString& Value)
    { const FString New=Response->GetHeader(Name); if(!New.IsEmpty() || !NotModified)Value=New; };
    Header(TEXT("ETag"),Data.ETag); Header(TEXT("Last-Modified"),Data.Modified);
    Header(TEXT("Cache-Control"),Data.CacheControl); Header(TEXT("Expires"),Data.Expires);
    double Lifetime=Week;
    const FString CC=Data.CacheControl.ToLower();
    TArray<FString> Directives; CC.ParseIntoArray(Directives,TEXT(","),true);
    bool HasMaxAge=false, NoCache=false;
    Data.NoStore=false; Data.MustRevalidate=false;
    for(FString Directive:Directives)
    {
        Directive.TrimStartAndEndInline();
        if(Directive==TEXT("no-store"))Data.NoStore=true;
        if(Directive==TEXT("no-cache"))NoCache=true;
        if(Directive==TEXT("must-revalidate"))Data.MustRevalidate=true;
        if(Directive.StartsWith(TEXT("max-age=")))
        { Lifetime=FMath::Max(0.0,FCString::Atod(*Directive.Mid(8).Replace(TEXT("\""),TEXT("")))); HasMaxAge=true; }
    }
    FDateTime Date;
    double ResponseDate=UtcNow();
    if(FDateTime::ParseHttpDate(Response->GetHeader(TEXT("Date")),Date))ResponseDate=double(Date.ToUnixTimestamp());
    if(!HasMaxAge && FDateTime::ParseHttpDate(Data.Expires,Date))Lifetime=FMath::Max(0.0,double(Date.ToUnixTimestamp())-ResponseDate);
    const double Age=FMath::Max(FMath::Max(0.0,UtcNow()-ResponseDate),FCString::Atod(*Response->GetHeader(TEXT("Age"))));
    Data.FreshUntil=UtcNow()+FMath::Max(0.0,Lifetime-Age);
    if(NoCache || Data.NoStore)Data.FreshUntil=0;
    Data.MustRevalidate=Data.MustRevalidate || NoCache || Data.NoStore;
}
bool ClipRoad(FVector2D& A,FVector2D& B,double Size)
{
    // Inset by half the stroke width so even line caps cannot bleed out.
    const FVector2D Offset(1,1); A-=Offset; B-=Offset; Size-=2;
    const FVector2D D=B-A; double Low=0,High=1;
    const double P[4]={-D.X,D.X,-D.Y,D.Y},Q[4]={A.X,Size-A.X,A.Y,Size-A.Y};
    for(int I=0;I<4;++I)
    {
        if(FMath::Abs(P[I])<1e-9) { if(Q[I]<0)return false; }
        else { const double T=Q[I]/P[I]; if(P[I]<0)Low=FMath::Max(Low,T);else High=FMath::Min(High,T); }
    }
    if(Low>High)return false;
    B=A+D*High+Offset; A+=D*Low+Offset; return true;
}
}

UDynaMinimapComponent::UDynaMinimapComponent() { PrimaryComponentTick.bCanEverTick=false; }
void UDynaMinimapComponent::ConfigureGeography(const FString& Json)
{
    LoadPreferences();
    Initialized=false; Georeferenced=false; CrsDeclared=false;
    UtmOrigin=FVector::ZeroVector; Centre=LastPlayer=FVector2D::ZeroVector;
    Heading=ActualHeading=MapRotation=LastDraw=0; Range=180; ClampImageryRange();
    DrawnTiles=MissingTiles=0; Visible.Reset();
    ReferenceCrs=TEXT("EPSG:32636"); GeographyError=TEXT("Invalid map JSON");
    TSharedPtr<FJsonObject> Data;
    if(!ParseJson(Json,Data))return;
    if(Data->HasField(TEXT("map_reference")))
    {
        const TSharedPtr<FJsonObject>* Reference;
        GeographyError=TEXT("map_reference must be an object");
        if(!Data->TryGetObjectField(TEXT("map_reference"),Reference) || !Reference->IsValid())return;
        if((*Reference)->HasField(TEXT("crs")))
        {
            CrsDeclared=true;
            GeographyError=TEXT("map_reference.crs must be EPSG:32636");
            if(!(*Reference)->TryGetStringField(TEXT("crs"),ReferenceCrs)) { ReferenceCrs.Reset(); return; }
            ReferenceCrs=ReferenceCrs.TrimStartAndEnd().ToUpper();
            if(ReferenceCrs!=TEXT("EPSG:32636"))return;
        }
    }
    const TArray<TSharedPtr<FJsonValue>>* Origin;
    GeographyError=TEXT("Invalid EPSG:32636 origin_utm_m");
    if(!Data->TryGetArrayField(TEXT("origin_utm_m"),Origin) || Origin->Num()<2)return;
    double E=0,N=0,Z=0;
    if(!(*Origin)[0]->TryGetNumber(E) || !(*Origin)[1]->TryGetNumber(N))return;
    if(Origin->Num()>2 && !(*Origin)[2]->TryGetNumber(Z))return;
    // Fail visibly to local roads rather than silently guessing a geographic origin.
    if(!FMath::IsFinite(E) || !FMath::IsFinite(N) || !FMath::IsFinite(Z) || E<100000 || E>900000 || N<0 || N>9500000)return;
    UtmOrigin=FVector(E,N,Z); Georeferenced=true; GeographyError.Reset();
}
TSharedRef<FJsonObject> UDynaMinimapComponent::StatusObject() const
{
    // Inspection only: never advances smoothing, fetches tiles, or changes traffic.
    auto Data=MakeShared<FJsonObject>();
    Data->SetBoolField(TEXT("georeferenced"),Georeferenced);
    Data->SetBoolField(TEXT("initialized"),Initialized);
    Data->SetStringField(TEXT("geography_error"),GeographyError);
    Data->SetStringField(TEXT("crs"),ReferenceCrs);
    Data->SetBoolField(TEXT("crs_declared"),CrsDeclared);
    Data->SetStringField(TEXT("mode"),ModeName(Mode));
    Data->SetBoolField(TEXT("heading_up"),HeadingUp);
    Data->SetStringField(TEXT("preferences_path"),PreferencesPath());
    Data->SetStringField(TEXT("preferences_error"),PreferencesError);
    Data->SetBoolField(TEXT("preferences_loaded"),PreferencesLoaded);
    const bool Satellite=Mode!=EDynaBasemap::Street;
    Data->SetNumberField(TEXT("imagery_meters_per_pixel"),2*Range/HudSize);
    Data->SetNumberField(TEXT("imagery_source_resolution_m"),Satellite?10:0);
    Data->SetNumberField(TEXT("imagery_effective_max_resolution_m"),Satellite?10:0);
    Data->SetNumberField(TEXT("imagery_max_zoom"),Satellite?14:19);
    Data->SetBoolField(TEXT("overzoom"),Satellite && 2*Range/HudSize<10);
    Data->SetBoolField(TEXT("regional_overview"),Satellite);
    Data->SetNumberField(TEXT("heading_deg"),FRotator::ClampAxis(FMath::RadiansToDegrees(Heading)));
    Data->SetNumberField(TEXT("actual_heading_deg"),FRotator::ClampAxis(FMath::RadiansToDegrees(ActualHeading)));
    Data->SetNumberField(TEXT("map_rotation_deg"),FRotator::ClampAxis(FMath::RadiansToDegrees(MapRotation)));
    Data->SetNumberField(TEXT("half_range_m"),Range);
    Data->SetNumberField(TEXT("draw_age_s"),Initialized?FMath::Max(0.0,FPlatformTime::Seconds()-LastDraw):-1);
    Data->SetNumberField(TEXT("tiles"),Textures.Num());
    Data->SetNumberField(TEXT("tile_entries"),Tiles.Num());
    Data->SetNumberField(TEXT("visible_tiles"),Visible.Num());
    Data->SetNumberField(TEXT("drawn_tiles"),DrawnTiles);
    Data->SetNumberField(TEXT("missing_tiles"),MissingTiles);
    Data->SetNumberField(TEXT("pending"),ActiveLoads);
    Data->SetNumberField(TEXT("http_handles"),Requests.Num());
    Data->SetNumberField(TEXT("max_concurrent_loads"),MaxLoads);
    Data->SetNumberField(TEXT("memory_tile_limit"),MaxMemoryTiles);
    int32 Errors=0,VisibleErrors=0;
    for(const auto& Pair:Tiles)if(Pair.Value.Failed) { ++Errors; if(Visible.Contains(Pair.Key))++VisibleErrors; }
    Data->SetNumberField(TEXT("errors"),Errors);
    Data->SetNumberField(TEXT("visible_errors"),VisibleErrors);
    Data->SetNumberField(TEXT("failed_loads_total"),double(FailedLoads));
    auto Array=[](std::initializer_list<double> Values)
    {
        TArray<TSharedPtr<FJsonValue>> Out;
        for(double Value:Values)Out.Add(MakeShared<FJsonValueNumber>(Value));
        return Out;
    };
    if(Georeferenced)
    {
        Data->SetArrayField(TEXT("origin_utm_m"),Array({UtmOrigin.X,UtmOrigin.Y,UtmOrigin.Z}));
        const auto LL=DynaMapMath::Utm36ToLonLat(UtmOrigin.X,UtmOrigin.Y);
        Data->SetArrayField(TEXT("origin_lonlat"),Array({LL.X,LL.Y}));
    }
    else
    {
        Data->SetField(TEXT("origin_utm_m"),MakeShared<FJsonValueNull>());
        Data->SetField(TEXT("origin_lonlat"),MakeShared<FJsonValueNull>());
    }
    auto Center=MakeShared<FJsonObject>();
    Center->SetStringField(TEXT("crs"),Georeferenced?TEXT("EPSG:3857"):TEXT("local_m"));
    if(Initialized)Center->SetArrayField(TEXT("position_m"),Array({Centre.X,Centre.Y}));
    else Center->SetField(TEXT("position_m"),MakeShared<FJsonValueNull>());
    Data->SetObjectField(TEXT("center"),Center);
    if(Initialized)Data->SetArrayField(TEXT("player_local_m"),Array({LastPlayer.X,LastPlayer.Y}));
    else Data->SetField(TEXT("player_local_m"),MakeShared<FJsonValueNull>());
    return Data;
}
FString UDynaMinimapComponent::Status() const
{
    FString Json;
    FJsonSerializer::Serialize(StatusObject(),TJsonWriterFactory<>::Create(&Json));
    return Json;
}
FVector2D UDynaMinimapComponent::Mercator(const FVector2D& Local) const
{
    if(!Georeferenced)return Local;
    const auto P=DynaMapMath::Utm36ToMercator(UtmOrigin.X+Local.X,UtmOrigin.Y+Local.Y);
    return FVector2D(P.X,P.Y);
}
FString UDynaMinimapComponent::PreferencesPath() const
{
    return PreferencesPathOverride.IsEmpty()
        ?FPaths::ConvertRelativePathToFull(FPaths::Combine(FPaths::ProjectSavedDir(),TEXT("DynaCity/map-preferences.json")))
        :PreferencesPathOverride;
}
void UDynaMinimapComponent::ClampImageryRange()
{
    if(Mode!=EDynaBasemap::Street)Range=FMath::Max(Range,HudSize*5);
}
void UDynaMinimapComponent::UpdateRange(double Speed,double Dt,bool Snap)
{
    const double Target=Mode==EDynaBasemap::Street?FMath::Clamp(180+Speed*9,180.0,420.0):HudSize*5;
    Range=Snap?Target:Range+(Target-Range)*DynaMapMath::FollowAlpha(Dt,2.5);
    ClampImageryRange();
}
void UDynaMinimapComponent::LoadPreferences()
{
    // Do not overwrite an explicit successful SetPreferences or reload on geography changes.
    if(PreferencesLoaded)return;
    PreferencesLoaded=true; Mode=EDynaBasemap::Street; HeadingUp=true; PreferencesError.Reset();
    const FString Path=PreferencesPath();
    if(!IFileManager::Get().FileExists(*Path) && !IFileManager::Get().DirectoryExists(*Path))return;
    FString Text,Name; TSharedPtr<FJsonObject> Data; double Version=0; bool Up=true; EDynaBasemap Parsed;
    if(!FFileHelper::LoadFileToString(Text,*Path))
        PreferencesError=TEXT("Cannot read preferences: ")+Path;
    else if(!ParseJson(Text,Data) || !Data->HasTypedField<EJson::Number>(TEXT("version"))
        || !Data->HasTypedField<EJson::String>(TEXT("mode")) || !Data->HasTypedField<EJson::Boolean>(TEXT("heading_up"))
        || !Data->TryGetNumberField(TEXT("version"),Version) || Version!=1
        || !Data->TryGetStringField(TEXT("mode"),Name) || !ParseMode(Name,Parsed)
        || !Data->TryGetBoolField(TEXT("heading_up"),Up))
        PreferencesError=TEXT("Invalid preferences: expected version 1, mode street/satellite/hybrid and boolean heading_up: ")+Path;
    else { Mode=Parsed; HeadingUp=Up; ClampImageryRange(); }
}
bool UDynaMinimapComponent::SetPreferences(const FString& Name,bool bHeadingUp)
{
    check(IsInGameThread());
    EDynaBasemap Parsed;
    if(!ParseMode(Name,Parsed))
    { PreferencesError=TEXT("Unknown preferences mode: ")+Name; return false; }
    const FString Path=PreferencesPath(),Directory=FPaths::GetPath(Path);
    auto Data=MakeShared<FJsonObject>();
    Data->SetNumberField(TEXT("version"),1); Data->SetStringField(TEXT("mode"),Name); Data->SetBoolField(TEXT("heading_up"),bHeadingUp);
    FString Text; FJsonSerializer::Serialize(Data,TJsonWriterFactory<>::Create(&Text));
    const FString Temp=Path+TEXT(".")+FGuid::NewGuid().ToString(EGuidFormats::Digits)+TEXT(".tmp");
    if(!IFileManager::Get().MakeDirectory(*Directory,true)
        || !FFileHelper::SaveStringToFile(Text,*Temp,FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
    {
        IFileManager::Get().Delete(*Temp);
        PreferencesError=TEXT("Cannot write preferences: ")+Path; return false;
    }
    // Replace in the same directory without deleting the old file first. A failed
    // replacement leaves both the previous preferences and live state untouched.
#if PLATFORM_WINDOWS
    const bool Replaced=::MoveFileExW(*Temp,*Path,MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)!=0;
#else
    const bool Replaced=std::rename(TCHAR_TO_UTF8(*Temp),TCHAR_TO_UTF8(*Path))==0;
#endif
    if(!Replaced)
    {
        IFileManager::Get().Delete(*Temp);
        PreferencesError=TEXT("Cannot atomically replace preferences: ")+Path; return false;
    }
    Mode=Parsed; HeadingUp=bHeadingUp; PreferencesLoaded=true; PreferencesError.Reset(); ClampImageryRange();
    return true;
}
void UDynaMinimapComponent::CycleMode() { SetPreferences(ModeName(EDynaBasemap((int32(Mode)+1)%3)),HeadingUp); }
void UDynaMinimapComponent::ToggleOrientation() { SetPreferences(ModeName(Mode),!HeadingUp); }
void UDynaMinimapComponent::EndPlay(const EEndPlayReason::Type Reason)
{
    Stopped=true;
    for(auto& Pair:Requests) { Pair.Value->OnProcessRequestComplete().Unbind(); Pair.Value->CancelRequest(); }
    Requests.Reset(); Textures.Reset(); Tiles.Reset();
    Super::EndPlay(Reason);
}
void UDynaMinimapComponent::InstallTile(const FString& Key,const FDynaTileData& Data)
{
    check(IsInGameThread());
    if(Data.Pixels.IsEmpty() || Stopped)return;
    UTexture2D* Texture=UTexture2D::CreateTransient(Data.Width,Data.Height,PF_B8G8R8A8);
    if(!Texture)return;
    Texture->SRGB=true; Texture->NeverStream=true; Texture->Filter=TF_Bilinear;
    Texture->AddressX=TA_Clamp; Texture->AddressY=TA_Clamp;
    void* Pixels=Texture->GetPlatformData()->Mips[0].BulkData.Lock(LOCK_READ_WRITE);
    FMemory::Memcpy(Pixels,Data.Pixels.GetData(),Data.Pixels.Num());
    Texture->GetPlatformData()->Mips[0].BulkData.Unlock();
    Textures.Add(Key,Texture); // GC-visible before enqueuing its render resource.
    Texture->UpdateResource();
}
void UDynaMinimapComponent::FinishTile(const FString& Key,TSharedPtr<FDynaTileData,ESPMode::ThreadSafe> Data,bool Success)
{
    check(IsInGameThread());
    if(Stopped)return;
    Requests.Remove(Key);
    FTileState* State=Tiles.Find(Key);
    if(!State)return;
    if(State->Pending) { State->Pending=false; --ActiveLoads; }
    State->Failed=!Success;
    if(!Success)++FailedLoads;
    State->RetryAt=FPlatformTime::Seconds()+(Success?30:120);
    if(Success && Data.IsValid())
    {
        if(!Data->Pixels.IsEmpty())InstallTile(Key,*Data);
        State->FreshUntil=Data->FreshUntil;
        State->MustRevalidate=Data->MustRevalidate;
    }
}
void UDynaMinimapComponent::LoadTile(const FString& Key,const FString& Url)
{
    FTileState& State=Tiles.FindOrAdd(Key);
    if(State.Pending || ActiveLoads>=MaxLoads)return;
    State.Pending=true; ++ActiveLoads;
    const TWeakObjectPtr<UDynaMinimapComponent> Weak(this);
    const TWeakObjectPtr<AActor> Actor(GetOwner());
    IImageWrapperModule* Images=&FModuleManager::LoadModuleChecked<IImageWrapperModule>(TEXT("ImageWrapper"));
    Async(EAsyncExecution::ThreadPool,[Weak,Actor,Key,Url,Images]()
    {
        auto Data=MakeShared<FDynaTileData,ESPMode::ThreadSafe>();
        ReadCache(Key,*Data,Images);
        AsyncTask(ENamedThreads::GameThread,[Weak,Actor,Key,Url,Data]()
        {
            if(!Actor.IsValid() || !Weak.IsValid() || Weak->Stopped)return;
            if(!Weak->Visible.Contains(Key))
            {
                Weak->FinishTile(Key,nullptr,true);
                Weak->Tiles.FindChecked(Key).RetryAt=0;
                return;
            }
            if(!Data->Pixels.IsEmpty() && Data->FreshUntil>UtcNow()) { Weak->FinishTile(Key,Data,true); return; }
            if(!Data->Pixels.IsEmpty() && !Data->MustRevalidate)Weak->InstallTile(Key,*Data);
            Weak->FetchTile(Key,Url,Data);
        });
    });
}
void UDynaMinimapComponent::FetchTile(const FString& Key,const FString& Url,TSharedPtr<FDynaTileData,ESPMode::ThreadSafe> Cached)
{
    const int32 Source=Provider(Key);
    if(ProviderRetryAt[Source]>FPlatformTime::Seconds()) { FinishTile(Key,nullptr,false); return; }
    auto Request=FHttpModule::Get().CreateRequest();
    Requests.Add(Key,Request);
    Request->SetURL(Url); Request->SetVerb(TEXT("GET")); Request->SetTimeout(15);
    Request->SetHeader(TEXT("User-Agent"),TEXT("DynaCITYDrive/1.0 (native Unreal geographic minimap)"));
    Request->SetHeader(TEXT("Accept"),TEXT("image/png,image/jpeg"));
    if(!Cached->ETag.IsEmpty())Request->SetHeader(TEXT("If-None-Match"),Cached->ETag);
    if(!Cached->Modified.IsEmpty())Request->SetHeader(TEXT("If-Modified-Since"),Cached->Modified);
    const TWeakObjectPtr<UDynaMinimapComponent> Weak(this);
    const TWeakObjectPtr<AActor> Actor(GetOwner());
    IImageWrapperModule* Images=&FModuleManager::LoadModuleChecked<IImageWrapperModule>(TEXT("ImageWrapper"));
    Request->OnProcessRequestComplete().BindLambda([Weak,Actor,Key,Cached,Images,Source](FHttpRequestPtr,FHttpResponsePtr Response,bool Ok)
    {
        // Explicitly marshal even if the HTTP backend changes its completion thread.
        AsyncTask(ENamedThreads::GameThread,[Weak,Actor,Key,Cached,Images,Source,Response,Ok]()
        {
            if(!Actor.IsValid() || !Weak.IsValid() || Weak->Stopped)return;
            const int32 Code=Response.IsValid()?Response->GetResponseCode():0;
            if(!Ok || !Response.IsValid() || (Code!=200 && Code!=304))
            {
                double Delay=Code==403 || Code==404?3600:120;
                if(Response.IsValid())
                {
                    const FString Retry=Response->GetHeader(TEXT("Retry-After"));
                    FDateTime Date;
                    Delay=FMath::Max(Delay,FDateTime::ParseHttpDate(Retry,Date)?double(Date.ToUnixTimestamp())-UtcNow():FCString::Atod(*Retry));
                }
                Weak->ProviderRetryAt[Source]=FPlatformTime::Seconds()+FMath::Clamp(Delay,120.0,86400.0);
                Weak->FinishTile(Key,nullptr,false); return;
            }
            auto Data=MakeShared<FDynaTileData,ESPMode::ThreadSafe>();
            if(Code==304)*Data=MoveTemp(*Cached);
            else Data->Compressed=Response->GetContent();
            ReadHeaders(*Data,Response,Code==304);
            Async(EAsyncExecution::ThreadPool,[Weak,Actor,Key,Data,Images]()
            {
                const bool Valid=!Data->Pixels.IsEmpty() || Decode(*Data,Images);
                if(Valid)WriteCache(Key,*Data);
                AsyncTask(ENamedThreads::GameThread,[Weak,Actor,Key,Data,Valid]()
                {
                    if(Actor.IsValid() && Weak.IsValid() && !Weak->Stopped)Weak->FinishTile(Key,Data,Valid);
                });
            });
        });
    });
    if(!Request->ProcessRequest())FinishTile(Key,nullptr,false);
}
void UDynaMinimapComponent::TrimCache()
{
    while(Tiles.Num()>MaxMemoryTiles)
    {
        FString Oldest; double Time=TNumericLimits<double>::Max();
        for(const auto& Pair:Tiles)
            if(!Pair.Value.Pending && !Visible.Contains(Pair.Key) && Pair.Value.LastUsed<Time) { Time=Pair.Value.LastUsed; Oldest=Pair.Key; }
        if(Oldest.IsEmpty())break;
        Textures.Remove(Oldest); Tiles.Remove(Oldest);
    }
}

void UDynaMinimapComponent::Draw(AHUD* HUD,UCanvas* Canvas,const APawn* Player,float X,float Y,float Size,
    const TArray<FDynaMapSegment>& Roads,const TMap<FIntPoint,TArray<int32>>& Grid)
{
    if(!HUD || !Canvas || !Player || Stopped || Size<=0)return;
    HudSize=Size; ClampImageryRange();
    const ADynaMobilityWorld* Mobility=Cast<ADynaMobilityWorld>(GetOwner());
    if(!Mobility)return;
    const double Now=FPlatformTime::Seconds();
    const double Dt=Initialized?FMath::Clamp(Now-LastDraw,0.0,.1):0;
    LastDraw=Now;
    const FVector2D Local=Mobility->Local(Player->GetActorLocation()), Target=Mercator(Local);
    const FVector2D Ahead=Mercator(Mobility->Local(Player->GetActorLocation()+Player->GetActorForwardVector()*100));
    const double TargetHeading=FMath::Atan2(Ahead.X-Target.X,Ahead.Y-Target.Y);
    ActualHeading=TargetHeading;
    const FVector2D Velocity=Mobility->Local(Player->GetActorLocation()+Player->GetVelocity())-Local;
    const bool Satellite=Mode!=EDynaBasemap::Street;

    // Detect displacement, not distance to the lagged centre: normal follow never snaps.
    const bool Snap=!Initialized || FVector2D::Distance(Local,LastPlayer)>100;
    UpdateRange(Velocity.Size(),Dt,Snap);
    if(Snap) { Centre=Target; Heading=TargetHeading; Initialized=true; }
    else
    {
        Centre+=(Target-Centre)*DynaMapMath::FollowAlpha(Dt,9);
        Heading+=DynaMapMath::AngleDelta(Heading,TargetHeading)*DynaMapMath::FollowAlpha(Dt,10);

    }
    ClampImageryRange(); // Also clamp after smoothing and viewport resizing, not just mode entry.
    LastPlayer=Local;
    Heading=FMath::UnwindRadians(Heading);
    if(Snap)MapRotation=HeadingUp?Heading:0;
    else MapRotation+=DynaMapMath::AngleDelta(MapRotation,HeadingUp?Heading:0)*DynaMapMath::FollowAlpha(Dt,10);
    MapRotation=FMath::UnwindRadians(MapRotation);
    const double Rotation=MapRotation, C=FMath::Cos(Rotation), S=FMath::Sin(Rotation);
    const auto LL=DynaMapMath::Utm36ToLonLat(UtmOrigin.X+Local.X,UtmOrigin.Y+Local.Y);
    const double MercScale=Georeferenced?1/FMath::Cos(LL.Y*DynaMapMath::Pi/180):1;
    const double Half=Range*MercScale, Pixels=Size/(2*Half);
    auto Project=[&](const FVector2D& P)
    {
        const FVector2D D=P-Centre;
        return FVector2D(Size*.5+(C*D.X-S*D.Y)*Pixels,Size*.5-(S*D.X+C*D.Y)*Pixels);
    };
    HUD->DrawRect(FLinearColor(.018f,.026f,.032f,.95f),X-5,Y-28,Size+10,Size+99);
    HUD->DrawRect(FLinearColor(.10f,.14f,.15f,1),X,Y,Size,Size);
    Visible.Reset();
    int32 Missing=0,MissingBase=0,Failed=0,Stale=0;
    DrawnTiles=0;
    if(Georeferenced)
    {
        struct FVisibleTile { FString Key,Url; std::vector<DynaMapMath::Vertex> Polygon; double Distance; int32 Layer; };
        TArray<FVisibleTile> Wanted;
        const int32 LayerCount=Mode==EDynaBasemap::Hybrid?3:1;
        for(int32 Layer=0;Layer<LayerCount;++Layer)
        {
        const int32 MaxZoom=Layer==1?18:Satellite?14:19;
        // Ceil selects enough source pixels for the HUD; satellite remains source-limited.
        const int32 Zoom=DynaMapMath::RasterZoom(Pixels,MaxZoom);
        const int32 Count=1<<Zoom;
        const double World=2*DynaMapMath::Pi*DynaMapMath::Radius, TileSpan=World/Count;
        const double Radius=Half*FMath::Sqrt(2.0);
        const int32 MinX=FMath::Clamp(FMath::FloorToInt((Centre.X-Radius+World*.5)/TileSpan),0,Count-1);
        const int32 MaxX=FMath::Clamp(FMath::FloorToInt((Centre.X+Radius+World*.5)/TileSpan),0,Count-1);
        const int32 MinY=FMath::Clamp(FMath::FloorToInt((World*.5-Centre.Y-Radius)/TileSpan),0,Count-1);
        const int32 MaxY=FMath::Clamp(FMath::FloorToInt((World*.5-Centre.Y+Radius)/TileSpan),0,Count-1);

        for(int32 TY=MinY;TY<=MaxY;++TY)for(int32 TX=MinX;TX<=MaxX;++TX)
        {
            const double Left=TX*TileSpan-World*.5,Top=World*.5-TY*TileSpan;
            std::vector<DynaMapMath::Vertex> Polygon;
            for(const FVector2D UV:{FVector2D(0,0),FVector2D(1,0),FVector2D(1,1),FVector2D(0,1)})
            {
                const FVector2D P=Project(FVector2D(Left+UV.X*TileSpan,Top-UV.Y*TileSpan));
                Polygon.push_back({{P.X,P.Y},{UV.X,UV.Y}});
            }
            Polygon=DynaMapMath::ClipTile(std::move(Polygon),Size);
            if(Polygon.size()<3)continue; // Never fetch bounding-box corners outside the rotated viewport.
            const TCHAR* Source=Layer==1?TEXT("eox_streets"):Layer==2?TEXT("eox_labels"):Satellite?TEXT("eox2016"):TEXT("street");
            const FString Key=FString::Printf(TEXT("%s_%d_%d_%d"),Source,Zoom,TX,TY);
            const TCHAR* EoxLayer=Layer==1?TEXT("streets_3857"):Layer==2?TEXT("overlay_bright_3857"):TEXT("s2cloudless_3857");
            const FString Url=Satellite
                ?FString::Printf(TEXT("https://tiles.maps.eox.at/wmts/1.0.0/%s/default/g/%d/%d/%d.%s"),EoxLayer,Zoom,TY,TX,Layer==0?TEXT("jpg"):TEXT("png"))
                :FString::Printf(TEXT("https://tile.openstreetmap.org/%d/%d/%d.png"),Zoom,TX,TY);
            Wanted.Add({Key,Url,MoveTemp(Polygon),FVector2D::DistSquared(Centre,FVector2D(Left+TileSpan*.5,Top-TileSpan*.5)),Layer});
            Visible.Add(Key);
        }
        }
        // Base imagery first, then transparent cartography, then labels. All layers
        // share the same clipped visibility, request budget and bounded cache.
        Wanted.Sort([](const FVisibleTile& A,const FVisibleTile& B) { return A.Layer==B.Layer?A.Distance<B.Distance:A.Layer<B.Layer; });
        for(const auto& Tile:Wanted)
        {
            FTileState& State=Tiles.FindOrAdd(Tile.Key); State.LastUsed=Now;
            if(!State.Pending && Now>=State.RetryAt && (State.FreshUntil<=UtcNow() || !Textures.Contains(Tile.Key)) && ActiveLoads<MaxLoads)
                LoadTile(Tile.Key,Tile.Url);
            if(State.Failed)++Failed;
            if(State.FreshUntil<=UtcNow() && Textures.Contains(Tile.Key))++Stale;
            const bool NeedsValidation=State.MustRevalidate && State.FreshUntil<=UtcNow() && (State.Failed || State.RetryAt<=Now);
            UTexture2D* Texture=NeedsValidation?nullptr:Textures.FindRef(Tile.Key);
            if(!Texture) { ++Missing; if(Tile.Layer==0)++MissingBase; continue; }
            TArray<FCanvasUVTri> Triangles;
            auto Position=[&](const DynaMapMath::Vertex& V) { return FVector2D(X+V.Position.X,Y+V.Position.Y); };
            auto UV=[](const DynaMapMath::Vertex& V) { return FVector2D(V.UV.X,V.UV.Y); };
            for(size_t I=1;I+1<Tile.Polygon.size();++I)
            {
                FCanvasUVTri Tri;
                Tri.V0_Pos=Position(Tile.Polygon[0]); Tri.V0_UV=UV(Tile.Polygon[0]); Tri.V0_Color=FLinearColor::White;
                Tri.V1_Pos=Position(Tile.Polygon[I]); Tri.V1_UV=UV(Tile.Polygon[I]); Tri.V1_Color=FLinearColor::White;
                Tri.V2_Pos=Position(Tile.Polygon[I+1]); Tri.V2_UV=UV(Tile.Polygon[I+1]); Tri.V2_Color=FLinearColor::White;
                Triangles.Add(Tri);
            }
            FCanvasTriangleItem Item(FVector2D::ZeroVector,FVector2D::ZeroVector,FVector2D::ZeroVector,Texture->GetResource());
            Item.TriangleList=MoveTemp(Triangles);
            Item.BlendMode=Tile.Layer==0?SE_BLEND_Opaque:SE_BLEND_Translucent;
            Canvas->DrawItem(Item);
            ++DrawnTiles;
        }
    }
    MissingTiles=Missing;
    if(MissingBase>0 || !Georeferenced)
    {
        TSet<int32> Nearby;
        const double Radius=Range*1.6;
        for(int GX=FMath::FloorToInt((Local.X-Radius)/200);GX<=FMath::FloorToInt((Local.X+Radius)/200);++GX)
            for(int GY=FMath::FloorToInt((Local.Y-Radius)/200);GY<=FMath::FloorToInt((Local.Y+Radius)/200);++GY)
                if(const auto* Cell=Grid.Find(FIntPoint(GX,GY)))for(int I:*Cell)Nearby.Add(I);
        for(int I:Nearby)
        {
            FVector2D A=Project(Mercator(Roads[I].A)),B=Project(Mercator(Roads[I].B));
            if(ClipRoad(A,B,Size))HUD->DrawLine(X+A.X,Y+A.Y,X+B.X,Y+B.Y,FLinearColor(.88f,.91f,.81f,.9f),1.5f);
        }
    }
    // GPS route: dark casing under a bright magenta line, then the waypoint blip, pinned to
    // the map edge (toward the destination) when it is off the map.
    if(RoutePoints.Num()>=2)
    {
        // Inset by half the casing width so the thick line never crosses the map border.
        const FVector2D Inset(4,4);
        TArray<TPair<FVector2D,FVector2D>> Drawn;
        for(int32 I=1;I<RoutePoints.Num();++I)
        {
            FVector2D A=Project(Mercator(RoutePoints[I-1]))-Inset,B=Project(Mercator(RoutePoints[I]))-Inset;
            if(!ClipRoad(A,B,Size-8))continue;
            A+=Inset; B+=Inset;
            HUD->DrawLine(X+A.X,Y+A.Y,X+B.X,Y+B.Y,FLinearColor(.16f,.02f,.2f,.9f),7.f);
            Drawn.Add({A,B});
        }
        for(const auto& Line:Drawn)
            HUD->DrawLine(X+Line.Key.X,Y+Line.Key.Y,X+Line.Value.X,Y+Line.Value.Y,FLinearColor(.86f,.22f,1.f,1.f),4.f);
    }
    if(RouteHasGoal)
    {
        FVector2D G=Project(Mercator(RouteGoal));
        const FVector2D Mid(Size*.5,Size*.5);
        const bool Inside=G.X>=10 && G.Y>=10 && G.X<=Size-10 && G.Y<=Size-10;
        if(!Inside)
        {
            const FVector2D D=G-Mid;
            const double Scale=(Size*.5-10)/FMath::Max(FMath::Abs(D.X),FMath::Abs(D.Y));
            G=Mid+D*Scale;
        }
        const float R=Inside?8.f:6.f;
        HUD->DrawRect(FLinearColor(.08f,.0f,.1f,1),X+G.X-R-2,Y+G.Y-R-2,2*R+4,2*R+4);
        HUD->DrawRect(FLinearColor(.86f,.22f,1.f,1.f),X+G.X-R,Y+G.Y-R,2*R,2*R);
        HUD->DrawRect(FLinearColor::White,X+G.X-2,Y+G.Y-2,4,4);
    }
    // Marker follows the actual vehicle position within the smoothed camera.
    const FVector2D PlayerAt=Project(Target)+FVector2D(X,Y);
    const double ArrowAngle=Heading-Rotation;
    const FVector2D Dir(FMath::Sin(ArrowAngle),-FMath::Cos(ArrowAngle)),Side(-Dir.Y,Dir.X);
    const FVector2D Tip=PlayerAt+Dir*10,A=PlayerAt-Dir*7+Side*6,B=PlayerAt-Dir*7-Side*6;
    auto Edge=[&](FVector2D P,FVector2D Q) { HUD->DrawLine(P.X,P.Y,Q.X,Q.Y,FLinearColor(.42f,1,.73f),2); };
    Edge(Tip,A); Edge(Tip,B); Edge(A,B);
    const FVector2D North(-S,-C), NPos=FVector2D(X+Size*.5,Y+Size*.5)+North*(Size*.5-14);
    HUD->DrawRect(FLinearColor(.02f,.03f,.04f,.85f),NPos.X-7,NPos.Y-6,16,17);
    HUD->DrawText(TEXT("N"),FLinearColor::White,NPos.X-4,NPos.Y-6,GEngine->GetSmallFont(),.85f);
    const TCHAR* Name=Mode==EDynaBasemap::Street?TEXT("STREET"):Mode==EDynaBasemap::Satellite?TEXT("SATELLITE 2016"):TEXT("HYBRID 2016");
    HUD->DrawText(FString::Printf(TEXT("%s  |  %s"),Name,HeadingUp?TEXT("Heading up"):TEXT("North up")),FLinearColor(.82f,.94f,.88f),X+3,Y-23,GEngine->GetSmallFont(),.85f);
    FString State=!Georeferenced?TEXT("Invalid / missing geography - local roads")
        :Failed?TEXT("Offline / unavailable - cache + roads")
        :MissingBase?TEXT("Loading basemap - local road fallback")
        :Missing?TEXT("Loading cartographic overlays")
        :Stale?TEXT("Cached imagery - refreshing"):Satellite?TEXT("Regional overview - 10 m/pixel source"):TEXT("Street basemap");
    HUD->DrawText(FString::Printf(TEXT("%.0f m across  |  M: map  B: rotate"),Range*2),FLinearColor(.82f,.87f,.86f),X+2,Y+Size+4,GEngine->GetSmallFont(),.75f);
    if(!PreferencesError.IsEmpty())State=TEXT("Preferences error - inspect minimap Status");
    HUD->DrawText(State,FLinearColor(.8f,.85f,.83f),X+2,Y+Size+19,GEngine->GetSmallFont(),.7f);
    // Attribution is always outside the clip, on an opaque panel; never fades with tiles.
    HUD->DrawText(TEXT("\u00a9 OpenStreetMap contributors"),FLinearColor::White,X+2,Y+Size+34,GEngine->GetSmallFont(),.8f);
    if(Satellite)
    {
        HUD->DrawText(Mode==EDynaBasemap::Hybrid?TEXT("2016 imagery / cartography: EOX, MapServer"):TEXT("\u00a9 EOX / Sentinel-2 cloudless 2016"),FLinearColor::White,X+2,Y+Size+49,GEngine->GetSmallFont(),.75f);
        HUD->DrawText(TEXT("Modified Copernicus Sentinel data 2016 | CC BY 4.0"),FLinearColor::White,X+2,Y+Size+62,GEngine->GetSmallFont(),.7f);
    }
    TrimCache();
}
