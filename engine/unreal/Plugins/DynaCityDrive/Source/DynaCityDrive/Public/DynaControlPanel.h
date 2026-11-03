#pragma once
#include "CoreMinimal.h"
#include "UObject/Object.h"
#include "DynaControlPanel.generated.h"

class SWindow;
class SWebBrowser;
class SWidget;
DECLARE_DYNAMIC_MULTICAST_DELEGATE_OneParam(FDynaControlRequest, const FString&, Request);

/** Local Bootstrap control surface, hosted by Unreal's built-in browser widget. */
UCLASS(BlueprintType)
class DYNACITYDRIVE_API UDynaControlPanel : public UObject
{
    GENERATED_BODY()
public:
    UPROPERTY(BlueprintAssignable, Category="Beirut|Controls") FDynaControlRequest RequestReceived;
    UFUNCTION(BlueprintCallable, Category="Beirut|Controls") void Show(const FString& Page = TEXT("explore"));
    /** Full-viewport title/pause menu inside the Play window (UI/title.html); falls back to Show() without one. */
    UFUNCTION(BlueprintCallable, Category="Beirut|Controls") void ShowOverlay(const FString& Page = TEXT("title"));
    UFUNCTION(BlueprintCallable, Category="Beirut|Controls") bool IsOverlayOpen() const;
    /** Type and source location of the widget holding user 0's keyboard focus (diagnostics). */
    UFUNCTION(BlueprintCallable, Category="Beirut|Controls") FString GetFocusDebug() const;
    UFUNCTION(BlueprintCallable, Category="Beirut|Controls") void Close();
    UFUNCTION(BlueprintCallable, Category="Beirut|Controls") void SetState(const FString& Json);
    UFUNCTION(BlueprintCallable, Category="Beirut|Controls") void SetResponse(const FString& Json);
    UFUNCTION(BlueprintCallable, Category="Beirut|Controls") FString Execute(const FString& Request);
    UFUNCTION(BlueprintCallable, Category="Beirut|Controls") FString GetState() const;
    UFUNCTION(BlueprintCallable, Category="Beirut|Controls") static void ShowRegisteredPanel(const FString& Page = TEXT("explore"));
private:
    static void FocusBrowser(const TSharedRef<SWindow>& Panel, const TSharedRef<SWebBrowser>& View);
    FString State = TEXT("{}");
    FString Response;
    TWeakPtr<SWindow> Window;
    TWeakPtr<SWebBrowser> Browser;
    TSharedPtr<SWidget> Overlay;
    TWeakPtr<SWebBrowser> OverlayBrowser;
    TWeakObjectPtr<class UGameViewportClient> OverlayViewport;
    FDelegateHandle ActivationHandle;
};
