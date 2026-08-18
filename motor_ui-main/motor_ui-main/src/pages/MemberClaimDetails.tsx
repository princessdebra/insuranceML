import { useEffect, useState } from "react";
import { useNavigate, useParams, useSearchParams, Link } from "react-router-dom";
import MemberLayout from "@/layouts/MemberLayout";
import { getMemberClaimDetails } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import DocumentsPanel from "@/components/DocumentsPanel";
import PhotosPanel from "@/components/PhotosPanel";

export default function MemberClaimDetails() {
  const { claimId } = useParams();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  const refetch = () => {
    const memberId = localStorage.getItem("memberId");
    if (!memberId || !claimId) return;
    getMemberClaimDetails(claimId, memberId)
      .then((d) => {
        setData(d);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  };

  useEffect(() => {
    // A notification email links here with ?member_id=... so the member
    // doesn't need to already be logged in on this device -- same trust
    // level as the login screen itself (member-ID lookup, no password),
    // not a downgrade from it.
    const linkedMemberId = searchParams.get("member_id");
    if (linkedMemberId && !localStorage.getItem("memberId")) {
      localStorage.setItem("memberId", linkedMemberId);
    }

    const memberId = localStorage.getItem("memberId");
    if (!memberId || !claimId) {
      navigate("/member/login");
      return;
    }
    refetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [claimId, navigate]);

  if (loading) {
    return (
      <MemberLayout>
        <div className="flex items-center justify-center h-full min-h-[400px]">
          <div className="flex flex-col items-center gap-2">
            <div className="size-8 border-4 border-primary border-t-transparent rounded-full animate-spin"></div>
            <p className="text-muted-foreground font-medium">Loading your claim...</p>
          </div>
        </div>
      </MemberLayout>
    );
  }

  if (!data?.success) {
    return (
      <MemberLayout>
        <div className="flex items-center justify-center h-full p-8 text-center">
          <div className="max-w-md space-y-4">
            <span className="material-symbols-outlined text-destructive text-6xl">error</span>
            <h2 className="text-2xl font-bold">Couldn't load this claim</h2>
            <Link to="/member/dashboard" className="inline-block px-6 py-2 bg-primary text-primary-foreground rounded-lg font-bold">Return to Dashboard</Link>
          </div>
        </div>
      </MemberLayout>
    );
  }

  const claim = data.claim_details || {};

  return (
    <MemberLayout>
      <div className="p-6 md:p-10 space-y-8 max-w-4xl mx-auto">
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div>
            <p className="text-[10px] font-black uppercase tracking-widest text-muted-foreground">Claim</p>
            <h1 className="text-2xl font-black text-foreground">{claimId}</h1>
          </div>
          <Badge variant="outline" className="capitalize font-bold">{claim.risk_level ? `${claim.risk_level} priority` : "Under review"}</Badge>
        </div>

        <Card className="overflow-hidden border-none shadow-sm">
          <CardHeader className="bg-primary/5 border-b border-primary/10">
            <CardTitle className="text-sm font-black uppercase tracking-widest flex items-center gap-2 text-primary">
              <span className="material-symbols-outlined">summarize</span>
              Claim Summary
            </CardTitle>
          </CardHeader>
          <CardContent className="pt-6 grid grid-cols-2 gap-4 text-sm">
            <div>
              <p className="text-[10px] font-black uppercase text-muted-foreground">Location</p>
              <p className="font-semibold">{claim.location || "—"}</p>
            </div>
            <div>
              <p className="text-[10px] font-black uppercase text-muted-foreground">Estimated Cost</p>
              <p className="font-semibold">{claim.estimated_cost ? `KES ${Number(claim.estimated_cost).toLocaleString()}` : "—"}</p>
            </div>
            <div className="col-span-2">
              <p className="text-[10px] font-black uppercase text-muted-foreground">Narrative</p>
              <p className="font-medium text-foreground/90 leading-relaxed">{claim.narrative || "—"}</p>
            </div>
          </CardContent>
        </Card>

        <Card className="overflow-hidden border-none shadow-sm">
          <CardHeader className="bg-primary/5 border-b border-primary/10">
            <CardTitle className="text-sm font-black uppercase tracking-widest flex items-center gap-2 text-primary">
              <span className="material-symbols-outlined">photo_library</span>
              Damage Photos
            </CardTitle>
          </CardHeader>
          <CardContent className="pt-6">
            <p className="text-xs text-muted-foreground mb-4">
              Didn't have photos ready when you filed, or want to add more? Upload them here.
            </p>
            <PhotosPanel
              claimId={claimId || ""}
              uploaderType="member"
              uploaderId={localStorage.getItem("memberId") || ""}
              initialPhotos={data.photos || []}
              partyFilter="member"
            />
          </CardContent>
        </Card>

        <Card className="overflow-hidden border-none shadow-sm">
          <CardHeader className="bg-primary/5 border-b border-primary/10">
            <CardTitle className="text-sm font-black uppercase tracking-widest flex items-center gap-2 text-primary">
              <span className="material-symbols-outlined">document_scanner</span>
              Your Supporting Documents
            </CardTitle>
          </CardHeader>
          <CardContent className="pt-6">
            <p className="text-xs text-muted-foreground mb-4">
              Here's what we read from any police abstract, ID, or other documents uploaded for this claim.
              If something looks wrong, correct it below.
            </p>
            <DocumentsPanel
              documents={data.documents || []}
              viewerParty="member"
              correctorId={localStorage.getItem("memberId") || ""}
              onCorrected={refetch}
              claimId={claimId}
            />
          </CardContent>
        </Card>
      </div>
    </MemberLayout>
  );
}
