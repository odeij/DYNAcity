#include "DynaLocationPanel.h"
#include "Framework/Application/SlateApplication.h"
#include "Widgets/SWindow.h"
#include "Widgets/Layout/SBorder.h"
#include "Widgets/Layout/SScrollBox.h"
#include "Widgets/SBoxPanel.h"
#include "Widgets/Input/SButton.h"
#include "Widgets/Input/SEditableTextBox.h"
#include "Widgets/Text/STextBlock.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Dom/JsonObject.h"

static TWeakObjectPtr<UDynaLocationPanel> RegisteredPanel;

class SDynaLocationSearch : public SCompoundWidget
{
public:
    SLATE_BEGIN_ARGS(SDynaLocationSearch) {} SLATE_END_ARGS()
    void Construct(const FArguments&, UDynaLocationPanel* InOwner)
    {
        Owner = InOwner;
        ChildSlot[SNew(SBorder).Padding(20)[SNew(SVerticalBox)
            + SVerticalBox::Slot().AutoHeight().Padding(0,0,0,10)
              [SNew(STextBlock).Text(FText::FromString(TEXT("Find a place in Beirut")))]
            + SVerticalBox::Slot().AutoHeight()
              [SNew(SHorizontalBox)
                + SHorizontalBox::Slot().FillWidth(1)
                  [SAssignNew(Input, SEditableTextBox)
                    .HintText(FText::FromString(TEXT("Place name, map link, or latitude, longitude")))
                    .OnTextCommitted_Lambda([this](const FText&, ETextCommit::Type Type)
                        { if (Type == ETextCommit::OnEnter) Search(); })]
                + SHorizontalBox::Slot().AutoWidth().Padding(8,0)
                  [SNew(SButton).Text(FText::FromString(TEXT("Search")))
                    .OnClicked_Lambda([this]() { Search(); return FReply::Handled(); })]]
            + SVerticalBox::Slot().AutoHeight().Padding(0,12)
              [SAssignNew(Status, STextBlock).AutoWrapText(true)
                .Text(FText::FromString(TEXT("Search downloaded OSM places. Select a result to go there.")))]
            + SVerticalBox::Slot().FillHeight(1)[SAssignNew(Results, SScrollBox)]
            + SVerticalBox::Slot().AutoHeight().Padding(0,12,0,0)
              [SNew(STextBlock).AutoWrapText(true).Text(FText::FromString(
                TEXT("Place names: OpenStreetMap contributors (ODbL). Driving uses a nearby source road; coverage varies.")))]]];
    }
    void Search()
    {
        Results->ClearChildren();
        if (Owner.IsValid()) Owner->QueryRequested.Broadcast(Input->GetText().ToString());
    }
    void SetStatus(const FString& Message) { Status->SetText(FText::FromString(Message)); }
    void SetResults(const FString& Json)
    {
        Results->ClearChildren();
        TArray<TSharedPtr<FJsonValue>> Rows;
        if (!FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Json), Rows)) return;
        for (int32 I = 0; I < Rows.Num(); ++I)
        {
            const TSharedPtr<FJsonObject> Row = Rows[I]->AsObject();
            if (!Row) continue;
            FString Label, Detail;
            if (!Row->TryGetStringField(TEXT("name"), Label)) continue;
            Row->TryGetStringField(TEXT("detail"), Detail);
            Results->AddSlot().Padding(0,4)[SNew(SButton)
                .OnClicked_Lambda([this,I]() { if (Owner.IsValid()) Owner->ResultSelected.Broadcast(I); return FReply::Handled(); })
                [SNew(SVerticalBox)
                    + SVerticalBox::Slot().AutoHeight()[SNew(STextBlock).AutoWrapText(true).Text(FText::FromString(Label))]
                    + SVerticalBox::Slot().AutoHeight()[SNew(STextBlock).AutoWrapText(true).Text(FText::FromString(Detail))]]];
        }
    }
    TWeakPtr<SWindow> Window;
    TSharedPtr<SEditableTextBox> Input;
private:
    TWeakObjectPtr<UDynaLocationPanel> Owner;
    TSharedPtr<STextBlock> Status;
    TSharedPtr<SScrollBox> Results;
};

void UDynaLocationPanel::Show()
{
    RegisteredPanel = this;
    if (TSharedPtr<SDynaLocationSearch> Existing = Widget.Pin())
    {
        if (TSharedPtr<SWindow> Window = Existing->Window.Pin()) Window->BringToFront();
        FSlateApplication::Get().SetKeyboardFocus(Existing->Input);
        return;
    }
    TSharedRef<SWindow> Window = SNew(SWindow).Title(FText::FromString(TEXT("Explore Beirut - location search")))
        .ClientSize(FVector2D(640,480)).SupportsMaximize(false).SupportsMinimize(false);
    TSharedRef<SDynaLocationSearch> Content = SNew(SDynaLocationSearch, this);
    Content->Window = Window;
    Widget = Content;
    Window->SetContent(Content);
    FSlateApplication::Get().AddWindow(Window);
    FSlateApplication::Get().SetKeyboardFocus(Content->Input);
}

void UDynaLocationPanel::SetResults(const FString& Json)
{
    if (TSharedPtr<SDynaLocationSearch> Content = Widget.Pin()) Content->SetResults(Json);
}
void UDynaLocationPanel::SetStatus(const FString& Message)
{
    RegisteredPanel = this;
    if (TSharedPtr<SDynaLocationSearch> Content = Widget.Pin()) Content->SetStatus(Message);
}
void UDynaLocationPanel::ShowRegisteredPanel()
{
    if (RegisteredPanel.IsValid()) RegisteredPanel->Show();
}
