#include "DynaControlPanel.h"
#include "Engine/Engine.h"
#include "Engine/GameViewportClient.h"
#include "Engine/World.h"
#include "Containers/Ticker.h"
#include "GameFramework/PlayerController.h"
#include "Framework/Application/SlateApplication.h"
#include "GenericPlatform/GenericWindow.h"
#include "Misc/Paths.h"
#include "SWebBrowser.h"
#include "WebBrowserModule.h"
#include "Widgets/SWindow.h"

static TWeakObjectPtr<UDynaControlPanel> Controls;

/** CEF draws into its own SViewport inside the browser; that viewport is what takes keys (a mouse click
 *  focuses it). It only exists once CEF has created the page, which can take seconds while Play loads. */
static TSharedPtr<SWidget> FindCefSurface(const TSharedRef<SWidget>& Root)
{
    if (Root->GetType() == FName(TEXT("SViewport"))) return Root;
    FChildren* Children = Root->GetChildren();
    for (int32 Index = 0; Children && Index < Children->Num(); ++Index)
        if (TSharedPtr<SWidget> Found = FindCefSurface(Children->GetChildAt(Index))) return Found;
    return nullptr;
}

/** True once keyboard focus sits on the CEF surface. With bFreshEvent, focus passes through the game viewport
 *  first: re-setting focus on a widget that holds it sends no event, and CEF drops its own focus whenever the
 *  OS window deactivates, so it needs a new focus event to take keys again. */
static bool FocusBrowserView(const TSharedRef<SWebBrowser>& View, bool bFreshEvent = false)
{
    FSlateApplication& Slate = FSlateApplication::Get();
    const TSharedPtr<SWidget> Surface = FindCefSurface(View);
    const TSharedRef<SWidget> Target = Surface.IsValid() ? Surface.ToSharedRef() : TSharedRef<SWidget>(View);
    if (Slate.GetUserFocusedWidget(0) == Target)
    {
        if (!bFreshEvent) return true;
        Slate.SetAllUserFocusToGameViewport(EFocusCause::SetDirectly);
    }
    Slate.SetAllUserFocus(Target, EFocusCause::Mouse);
    const TSharedPtr<SWidget> Now = Slate.GetUserFocusedWidget(0);
    UE_LOG(LogTemp, Verbose, TEXT("DynaCity menu focus: surface=%s focused=%s"), Surface.IsValid() ? TEXT("yes") : TEXT("no"),
        Now.IsValid() ? *Now->GetTypeAsString() : TEXT("none"));
    return Surface.IsValid() && Now == Target;
}

/** Keep trying for a few seconds until the CEF surface exists and holds focus. */
static void FocusWhenReady(const TSharedRef<SWebBrowser>& View, bool bFreshEvent)
{
    TWeakPtr<SWebBrowser> Weak = View;
    const double Deadline = FPlatformTime::Seconds() + 6.0;
    FTSTicker::GetCoreTicker().AddTicker(FTickerDelegate::CreateLambda([Weak, Deadline, bFreshEvent](float) mutable
    {
        const TSharedPtr<SWebBrowser> Pinned = Weak.Pin();
        if (!Pinned.IsValid()) return false;
        const bool bDone = FocusBrowserView(Pinned.ToSharedRef(), bFreshEvent);
        bFreshEvent = false;
        return !bDone && FPlatformTime::Seconds() < Deadline;
    }), 0.1f);
}

/** Give the in-viewport menu the keyboard: UI-only input aimed at the browser, cursor shown. PIE startup and
 *  the pawn's own GameAndUI switch (applied after the key event that opened the menu) take focus back, so
 *  focus again over the following frames. */
static void FocusOverlay(UGameViewportClient* Viewport, const TSharedRef<SWebBrowser>& View)
{
    if (UWorld* World = Viewport ? Viewport->GetWorld() : nullptr)
        if (APlayerController* PC = World->GetFirstPlayerController())
        {
            FInputModeUIOnly Mode;
            Mode.SetWidgetToFocus(View);
            Mode.SetLockMouseToViewportBehavior(EMouseLockMode::DoNotLock);
            PC->SetInputMode(Mode);
            PC->bShowMouseCursor = true;
        }
    // Closing the separate panel window when Play starts can leave another application active, so keys
    // never reach Unreal. Bring the Play window to the front and give it the OS keyboard focus once.
    if (const TSharedPtr<SWindow> PlayWindow = Viewport ? Viewport->GetWindow() : nullptr)
    {
        PlayWindow->BringToFront(true);
        if (const TSharedPtr<FGenericWindow> Native = PlayWindow->GetNativeWindow()) Native->SetWindowFocus();
    }
    FocusWhenReady(View, false);
}

/** file:/// URL of the bundled UI folder, with a trailing slash. */
static FString UiBaseUrl()
{
    FString Path = FPaths::ConvertRelativePathToFull(FPaths::ProjectContentDir() / TEXT("UI/"));
    Path.ReplaceInline(TEXT("\\"), TEXT("/"));
    Path.ReplaceInline(TEXT(" "), TEXT("%20"));
    if (!Path.EndsWith(TEXT("/"))) Path += TEXT("/");
    return TEXT("file:///") + Path;
}

void UDynaControlPanel::Show(const FString& Page)
{
    // Slate's SWebBrowserView expects its hosting widget module to initialize
    // WebBrowser first. This host uses Slate directly, without WebBrowserWidget.
    IWebBrowserModule::Get();
    Controls = this;
    const FString SafePage = (Page == TEXT("controls") || Page == TEXT("search") || Page == TEXT("layers"))
        ? Page : TEXT("explore");
    if (TSharedPtr<SWindow> Existing = Window.Pin())
    {
        Existing->BringToFront();
        if (TSharedPtr<SWebBrowser> View = Browser.Pin())
        {
            View->ExecuteJavascript(TEXT("window.showPage('") + SafePage + TEXT("');"));
            FocusBrowser(Existing.ToSharedRef(), View.ToSharedRef());
        }
        return;
    }
    FString Path = FPaths::ConvertRelativePathToFull(FPaths::ProjectContentDir() / TEXT("UI/index.html"));
    Path.ReplaceInline(TEXT("\\"), TEXT("/"));
    Path.ReplaceInline(TEXT(" "), TEXT("%20"));
    const FString Url = TEXT("file:///") + Path;
    TSharedRef<SWindow> Panel = SNew(SWindow)
        .Title(FText::FromString(TEXT("Explore Beirut")))
        .ClientSize(FVector2D(960,700)).MinWidth(780).MinHeight(560);
    TSharedRef<SWebBrowser> View = SNew(SWebBrowser)
        .InitialURL(Url + TEXT("#") + SafePage)
        .ShowControls(false).ShowAddressBar(false)
        .OnBeforePopup_Lambda([](FString, FString) { return true; })
        .OnBeforeNavigation_Lambda([Url](const FString& Target, const FWebNavigationRequest&)
            { return !Target.StartsWith(Url); });
    View->BindUObject(TEXT("beirut"), this, true);
    Window = Panel; Browser = View;
    Panel->SetContent(View);
    Panel->SetWidgetToFocusOnActivate(View);
    FSlateApplication::Get().AddWindow(Panel);
    FocusBrowser(Panel, View);
}

void UDynaControlPanel::ShowOverlay(const FString& Page)
{
    IWebBrowserModule::Get();
    Controls = this;
    const FString SafePage = (Page == TEXT("pause") || Page == TEXT("search") || Page == TEXT("settings") || Page == TEXT("controls"))
        ? Page : TEXT("title");
    UGameViewportClient* Viewport = GEngine ? GEngine->GameViewport : nullptr;
    if (!Viewport)
    {
        Show(SafePage == TEXT("search") || SafePage == TEXT("controls") ? SafePage : TEXT("explore"));
        return;
    }
    // The title menu replaces the separate panel window while Play runs.
    if (TSharedPtr<SWindow> Panel = Window.Pin()) Panel->RequestDestroyWindow();
    Window.Reset(); Browser.Reset();
    const FString Base = UiBaseUrl();
    const FString Title = Base + TEXT("title.html");
    if (Overlay.IsValid() && OverlayViewport.Get() == Viewport)
    {
        if (TSharedPtr<SWebBrowser> View = OverlayBrowser.Pin())
        {
            // Reopened from the HUD, or still on the City map page (index.html): land on the requested menu page.
            if (View->GetUrl().StartsWith(Title)) View->ExecuteJavascript(TEXT("window.showMenu('") + SafePage + TEXT("');"));
            else View->LoadURL(Title + TEXT("#") + SafePage);
            FocusOverlay(Viewport, View.ToSharedRef());
        }
        return;
    }
    Overlay.Reset();   // left over from a previous Play session whose viewport is gone
    TSharedRef<SWebBrowser> View = SNew(SWebBrowser)
        .InitialURL(Title + TEXT("#") + SafePage)
        .ShowControls(false).ShowAddressBar(false).ShowErrorMessage(false)
        .BackgroundColor(FColor(7, 9, 15, 255))
        .OnBeforePopup_Lambda([](FString, FString) { return true; })
        .OnBeforeNavigation_Lambda([Base](const FString& Target, const FWebNavigationRequest&)
            { return !(Target.StartsWith(Base + TEXT("title.html")) || Target.StartsWith(Base + TEXT("index.html"))); });
    View->BindUObject(TEXT("beirut"), this, true);
    Overlay = View; OverlayBrowser = View; OverlayViewport = Viewport;
    // Coming back to the editor window (alt-tab, a dialog) leaves Slate focus on the browser while CEF has
    // dropped its own; hand the keyboard back to the menu whenever the application is reactivated.
    if (!ActivationHandle.IsValid())
        ActivationHandle = FSlateApplication::Get().OnApplicationActivationStateChanged().AddWeakLambda(this, [this](bool bActive)
        {
            if (TSharedPtr<SWebBrowser> Pinned = OverlayBrowser.Pin(); bActive && Pinned && OverlayViewport.IsValid())
                FocusWhenReady(Pinned.ToSharedRef(), true);
        });
    // Above the driving HUD (which uses the default Z-order).
    Viewport->AddViewportWidgetContent(View, 100);
    FocusOverlay(Viewport, View);
}

bool UDynaControlPanel::IsOverlayOpen() const
{
    return Overlay.IsValid() && OverlayViewport.IsValid();
}

FString UDynaControlPanel::GetFocusDebug() const
{
    const TSharedPtr<SWidget> Focused = FSlateApplication::Get().GetUserFocusedWidget(0);
    return Focused.IsValid() ? Focused->GetTypeAsString() + TEXT(" ") + Focused->GetReadableLocation() : TEXT("none");
}

void UDynaControlPanel::FocusBrowser(const TSharedRef<SWindow>& Panel, const TSharedRef<SWebBrowser>& View)
{
    // The panel is a separate OS window. Without this, Slate keeps keyboard focus on the Play viewport
    // while the panel is in front, so Tab/Enter/Esc typed into the panel drive the car instead of the page.
    if (TSharedPtr<FGenericWindow> Native = Panel->GetNativeWindow()) Native->SetWindowFocus();
    FSlateApplication::Get().SetAllUserFocus(View, EFocusCause::SetDirectly);
}

void UDynaControlPanel::Close()
{
    if (TSharedPtr<SWindow> Panel = Window.Pin()) Panel->RequestDestroyWindow();
    Window.Reset(); Browser.Reset();
    if (UGameViewportClient* Viewport = OverlayViewport.Get(); Viewport && Overlay.IsValid())
    {
        Viewport->RemoveViewportWidgetContent(Overlay.ToSharedRef());
        FSlateApplication::Get().SetAllUserFocusToGameViewport();
    }
    Overlay.Reset(); OverlayBrowser.Reset(); OverlayViewport.Reset();
}
void UDynaControlPanel::SetState(const FString& Json) { Controls = this; State = Json; }
void UDynaControlPanel::SetResponse(const FString& Json) { Response = Json; }
FString UDynaControlPanel::GetState() const { return State; }
FString UDynaControlPanel::Execute(const FString& Request)
{
    Response = TEXT("{\"error\":\"This action is unavailable in the current session.\"}");
    if (Request.Len() <= 8192) RequestReceived.Broadcast(Request);
    return Response;
}
void UDynaControlPanel::ShowRegisteredPanel(const FString& Page)
{
    if (!Controls.IsValid()) return;
    // During Play the HUD keys open the in-viewport menu: F1/Tab the pause menu, F3 Find a place.
    if (GEngine && GEngine->GameViewport) Controls->ShowOverlay(Page == TEXT("search") ? Page : TEXT("pause"));
    else Controls->Show(Page);
}
