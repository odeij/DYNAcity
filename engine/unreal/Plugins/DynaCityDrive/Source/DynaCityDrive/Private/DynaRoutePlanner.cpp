#include "DynaRoutePlanner.h"
#include "DynaMobilityWorld.h"
#include "Algo/Reverse.h"
#include "Algo/BinarySearch.h"
#include "Templates/Function.h"

namespace
{
constexpr double CellM = 100;
FIntPoint Cell(const FVector2D& P) { return FIntPoint(FMath::FloorToInt(P.X/CellM), FMath::FloorToInt(P.Y/CellM)); }
FVector2D Closest(const FVector2D& P, const FVector2D& A, const FVector2D& B, double& T)
{
    const FVector2D D=B-A;
    const double L2=D.SizeSquared();
    T=L2>1e-9?FMath::Clamp(FVector2D::DotProduct(P-A,D)/L2,0.0,1.0):0.0;
    return A+D*T;
}
FVector2D PointAt(const FDynaRoute& Route, double Along)
{
    Along=FMath::Clamp(Along,0.0,Route.Length);
    int32 I=Algo::UpperBound(Route.Along,Along)-1;
    I=FMath::Clamp(I,0,Route.Points.Num()-2);
    const double Span=Route.Along[I+1]-Route.Along[I];
    return Span>1e-6?FMath::Lerp(Route.Points[I],Route.Points[I+1],(Along-Route.Along[I])/Span):Route.Points[I];
}
int32 TurnClass(double Degrees)
{
    const double A=FMath::Abs(Degrees);
    const int32 Class=A<30?0:A<115?1:A<165?2:3;
    return Degrees>0?-Class:Class; // counter-clockwise (positive) is a left turn
}
}

FString FDynaRoutePlanner::RoadLabel(const TArray<FString>& RoadNames, int32 Road)
{
    return RoadNames.IsValidIndex(Road) && !RoadNames[Road].IsEmpty()?RoadNames[Road]:FString(TEXT("unnamed road"));
}

double FDynaRoutePlanner::SpeedKmh(int32 Road, int32 Hour) const
{
    if(!RoadSpeeds.IsValidIndex(Road) || RoadSpeeds[Road].Num()!=24)return DefaultKmh;
    const float Value=RoadSpeeds[Road][((Hour%24)+24)%24];
    return FMath::IsFinite(Value) && Value>=3?Value:DefaultKmh;
}

void FDynaRoutePlanner::AddEdge(int32 A, int32 B, double Length, int32 Road)
{
    if(A==B)return;
    Adjacent[A].Add({B,Length,Road});
    Adjacent[B].Add({A,Length,Road});
}

void FDynaRoutePlanner::Build(const TArray<FDynaMapSegment>& Segments, const TArray<TArray<float>>& Speeds)
{
    Nodes.Reset(); Adjacent.Reset(); SegA.Reset(); SegB.Reset(); SegRoad.Reset(); Grid.Reset(); Links=0;
    RoadSpeeds=Speeds; MaxKmh=DefaultKmh;
    for(const auto& Row:RoadSpeeds)for(float V:Row)if(FMath::IsFinite(V))MaxKmh=FMath::Max(MaxKmh,double(V));
    // Junctions are shared vertices in the export; key them at 0.5 m.
    TMap<FIntPoint,int32> Keys;
    auto NodeAt=[&](const FVector2D& P)
    {
        const FIntPoint Key(FMath::RoundToInt(P.X*2),FMath::RoundToInt(P.Y*2));
        if(const int32* Found=Keys.Find(Key))return *Found;
        const int32 Index=Nodes.Add(P); Adjacent.AddDefaulted(); Keys.Add(Key,Index);
        return Index;
    };
    for(int32 I=0;I<Segments.Num();++I)
    {
        const FDynaMapSegment& S=Segments[I];
        const int32 A=NodeAt(S.A),B=NodeAt(S.B);
        SegA.Add(A); SegB.Add(B); SegRoad.Add(S.Road);
        AddEdge(A,B,FVector2D::Distance(S.A,S.B),S.Road);
        const FIntPoint C0=Cell(FVector2D(FMath::Min(S.A.X,S.B.X),FMath::Min(S.A.Y,S.B.Y)));
        const FIntPoint C1=Cell(FVector2D(FMath::Max(S.A.X,S.B.X),FMath::Max(S.A.Y,S.B.Y)));
        for(int32 X=C0.X;X<=C1.X;++X)for(int32 Y=C0.Y;Y<=C1.Y;++Y)Grid.FindOrAdd(FIntPoint(X,Y)).Add(I);
    }
    // Close small export gaps: a dead end within 12 m of another road joins it.
    const int32 Original=Nodes.Num();
    for(int32 N=0;N<Original;++N)
    {
        if(Adjacent[N].Num()!=1)continue;
        const FVector2D P=Nodes[N];
        int32 Best=INDEX_NONE; double BestD=12; FVector2D BestAt;
        const FIntPoint C=Cell(P);
        for(int32 X=C.X-1;X<=C.X+1;++X)for(int32 Y=C.Y-1;Y<=C.Y+1;++Y)
            if(const auto* List=Grid.Find(FIntPoint(X,Y)))for(int32 S:*List)
            {
                if(SegA[S]==N || SegB[S]==N || SegA[S]==Adjacent[N][0].To || SegB[S]==Adjacent[N][0].To)continue;
                double T; const FVector2D At=Closest(P,Nodes[SegA[S]],Nodes[SegB[S]],T);
                const double D=FVector2D::Distance(P,At);
                if(D<BestD) { BestD=D; Best=S; BestAt=At; }
            }
        if(Best==INDEX_NONE)continue;
        const int32 A=SegA[Best],B=SegB[Best];
        AddEdge(N,A,BestD+FVector2D::Distance(BestAt,Nodes[A]),SegRoad[Best]);
        AddEdge(N,B,BestD+FVector2D::Distance(BestAt,Nodes[B]),SegRoad[Best]);
        ++Links;
    }
    Built=true;
}

bool FDynaRoutePlanner::Snap(const FVector2D& P, double MaxDistanceM, int32& Segment, FVector2D& At) const
{
    Segment=INDEX_NONE; double BestD=MaxDistanceM;
    const int32 Reach=FMath::CeilToInt(MaxDistanceM/CellM);
    const FIntPoint C=Cell(P);
    for(int32 X=C.X-Reach;X<=C.X+Reach;++X)for(int32 Y=C.Y-Reach;Y<=C.Y+Reach;++Y)
        if(const auto* List=Grid.Find(FIntPoint(X,Y)))for(int32 S:*List)
        {
            double T; const FVector2D Q=Closest(P,Nodes[SegA[S]],Nodes[SegB[S]],T);
            const double D=FVector2D::Distance(P,Q);
            if(D<BestD) { BestD=D; Segment=S; At=Q; }
        }
    return Segment!=INDEX_NONE;
}

bool FDynaRoutePlanner::Plan(const FVector2D& From, const FVector2D& To, int32 Hour, const TArray<FString>& RoadNames, FDynaRoute& Out, FString& Error) const
{
    Out.Reset(); Out.Hour=((Hour%24)+24)%24;
    if(!Built || Nodes.IsEmpty()) { Error=TEXT("Road network not loaded."); return false; }
    int32 SF,ST; FVector2D PF,PT;
    if(!Snap(From,150,SF,PF)) { Error=TEXT("The car is not near an exported road."); return false; }
    if(!Snap(To,400,ST,PT)) { Error=TEXT("No exported road within 400 m of the chosen point."); return false; }
    const int32 Start=Nodes.Num(),Goal=Nodes.Num()+1,Count=Nodes.Num()+2;
    auto Seconds=[&](double Length,int32 Road){ return Length/(SpeedKmh(Road,Out.Hour)/3.6); };
    TArray<double> Cost; Cost.Init(TNumericLimits<double>::Max(),Count);
    TArray<int32> Prev; Prev.Init(INDEX_NONE,Count);
    TArray<int32> PrevRoad; PrevRoad.Init(INDEX_NONE,Count);
    TArray<bool> Done; Done.Init(false,Count);
    auto Position=[&](int32 N){ return N==Start?PF:N==Goal?PT:Nodes[N]; };
    auto Neighbours=[&](int32 N,TFunctionRef<void(int32,double,int32)> Visit)
    {
        if(N==Start)
        {
            Visit(SegA[SF],FVector2D::Distance(PF,Nodes[SegA[SF]]),SegRoad[SF]);
            Visit(SegB[SF],FVector2D::Distance(PF,Nodes[SegB[SF]]),SegRoad[SF]);
            if(SF==ST)Visit(Goal,FVector2D::Distance(PF,PT),SegRoad[SF]);
            return;
        }
        if(N==Goal)return;
        for(const FEdge& E:Adjacent[N])Visit(E.To,E.Length,E.Road);
        if(N==SegA[ST] || N==SegB[ST])Visit(Goal,FVector2D::Distance(Nodes[N],PT),SegRoad[ST]);
    };
    struct FOpen { double F; int32 Node; };
    auto Less=[](const FOpen& A,const FOpen& B){ return A.F<B.F; };
    TArray<FOpen> Open;
    const double MaxMs=MaxKmh/3.6;
    Cost[Start]=0; Open.HeapPush({FVector2D::Distance(PF,PT)/MaxMs,Start},Less);
    while(!Open.IsEmpty())
    {
        FOpen Top; Open.HeapPop(Top,Less);
        if(Done[Top.Node])continue;
        Done[Top.Node]=true;
        if(Top.Node==Goal)break;
        const double Base=Cost[Top.Node];
        Neighbours(Top.Node,[&](int32 V,double Length,int32 Road)
        {
            const double C=Base+Seconds(Length,Road);
            if(Done[V] || C>=Cost[V])return;
            Cost[V]=C; Prev[V]=Top.Node; PrevRoad[V]=Road;
            Open.HeapPush({C+FVector2D::Distance(Position(V),PT)/MaxMs,V},Less);
        });
    }
    if(!Done[Goal]) { Error=TEXT("That point is on a road not connected to the car's network."); return false; }
    TArray<int32> Chain;
    for(int32 N=Goal;N!=INDEX_NONE;N=Prev[N])Chain.Add(N);
    Algo::Reverse(Chain);
    for(int32 I=0;I<Chain.Num();++I)
    {
        const FVector2D P=Position(Chain[I]);
        const int32 Road=I+1<Chain.Num()?PrevRoad[Chain[I+1]]:INDEX_NONE;
        if(!Out.Points.IsEmpty() && FVector2D::Distance(Out.Points.Last(),P)<.3)
        { if(I+1<Chain.Num())Out.Roads.Last()=Road; continue; }
        Out.Points.Add(P);
        if(I+1<Chain.Num())Out.Roads.Add(Road);
    }
    if(Out.Points.Num()<2) { Out.Points.Add(PT); Out.Roads.SetNum(1); Out.Roads[0]=SegRoad[ST]; }
    Out.Roads.SetNum(Out.Points.Num()-1);
    Out.Along.Add(0);
    TMap<int32,double> RoadLength;
    for(int32 I=1;I<Out.Points.Num();++I)
    {
        const double L=FVector2D::Distance(Out.Points[I-1],Out.Points[I]);
        Out.Along.Add(Out.Along.Last()+L);
        Out.Seconds+=Seconds(L,Out.Roads[I-1]);
        RoadLength.FindOrAdd(Out.Roads[I-1])+=L;
    }
    Out.Length=Out.Along.Last();
    for(const auto& Pair:RoadLength)
        if(Pair.Value>=100 && (Out.SlowRoad==INDEX_NONE || SpeedKmh(Pair.Key,Out.Hour)<Out.SlowKmh))
        { Out.SlowRoad=Pair.Key; Out.SlowKmh=SpeedKmh(Pair.Key,Out.Hour); }
    Out.DestinationRoad=RoadLabel(RoadNames,SegRoad[ST]);
    // Instructions where the route changes road and either the name changes or it bends.
    for(int32 I=1;I+1<Out.Points.Num();++I)
    {
        if(Out.Roads[I]==Out.Roads[I-1])continue;
        const FString Before=RoadLabel(RoadNames,Out.Roads[I-1]),After=RoadLabel(RoadNames,Out.Roads[I]);
        const FVector2D In=Out.Points[I]-PointAt(Out,Out.Along[I]-15),Ahead=PointAt(Out,Out.Along[I]+15)-Out.Points[I];
        if(In.SizeSquared()<1e-4 || Ahead.SizeSquared()<1e-4)continue;
        const double Degrees=FMath::RadiansToDegrees(FMath::Atan2(In.X*Ahead.Y-In.Y*Ahead.X,FVector2D::DotProduct(In,Ahead)));
        const int32 Turn=TurnClass(Degrees);
        if(Turn==0 && Before==After)continue;
        FDynaRouteManeuver M; M.AlongM=Out.Along[I]; M.Turn=Turn; M.Road=After;
        if(!Out.Maneuvers.IsEmpty() && M.AlongM-Out.Maneuvers.Last().AlongM<15)
        {
            FDynaRouteManeuver& Last=Out.Maneuvers.Last();
            Last.Road=After;
            if(FMath::Abs(Turn)>FMath::Abs(Last.Turn))Last.Turn=Turn;
            continue;
        }
        Out.Maneuvers.Add(M);
    }
    FDynaRouteManeuver Arrive; Arrive.AlongM=Out.Length; Arrive.Arrive=true; Arrive.Road=Out.DestinationRoad;
    Out.Maneuvers.Add(Arrive);
    return true;
}

double FDynaRoute::Project(const FVector2D& P, int32& Hint, double& OffRouteM) const
{
    if(Points.Num()<2) { OffRouteM=0; return 0; }
    auto Search=[&](int32 From,int32 To,double& Best,double& BestAlong,int32& BestLeg)
    {
        for(int32 I=FMath::Max(0,From);I<FMath::Min(To,Points.Num()-1);++I)
        {
            double T; const FVector2D Q=Closest(P,Points[I],Points[I+1],T);
            const double D=FVector2D::Distance(P,Q);
            if(D<Best) { Best=D; BestLeg=I; BestAlong=Along[I]+T*(Along[I+1]-Along[I]); }
        }
    };
    double Best=TNumericLimits<double>::Max(),BestAlong=0; int32 BestLeg=FMath::Max(Hint,0);
    Search(Hint-5,Hint+60,Best,BestAlong,BestLeg);
    if(Best>40)Search(0,Points.Num(),Best,BestAlong,BestLeg);
    Hint=BestLeg; OffRouteM=Best;
    return BestAlong;
}
