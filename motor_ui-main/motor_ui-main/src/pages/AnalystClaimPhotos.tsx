import { useEffect, useState } from "react";
import { useNavigate, useParams, Link } from "react-router-dom";
import AnalystLayout from "@/layouts/AnalystLayout";
import { getClaimPhotos, notifyMemberToAddPhotos, ClaimPhoto } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import PhotosPanel from "@/components/PhotosPanel";

export default function AnalystClaimPhotos() {
  const { claimId } = useParams();
  const navigate = useNavigate();
  const [photos, setPhotos] = useState<ClaimPhoto[]>([]);
  const [loading, setLoading] = useState(true);
  const [notifyState, setNotifyState] = useState<"idle" | "sending" | "sent" | "failed">("idle");
  const [notifyMessage, setNotifyMessage] = useState("");

  const handleNotify = async () => {
    if (!claimId) return;
    setNotifyState("sending");
    try {
      const analystId = localStorage.getItem("analystId") || "";
      const res = await notifyMemberToAddPhotos(claimId, analystId);
      if (res.success) {
        setNotifyState("sent");
        setNotifyMessage(`Sent to ${res.sent_to}`);
      } else {
        setNotifyState("failed");
        setNotifyMessage(res.reason || "Could not send email.");
      }
    } catch (e) {
      setNotifyState("failed");
      setNotifyMessage("Could not send email — check your connection.");
    }
  };

  useEffect(() => {
    const analystId = localStorage.getItem("analystId");
    if (!analystId || !claimId) {
      navigate("/analyst/login");
      return;
    }
    getClaimPhotos(claimId)
      .then((res) => {
        if (res.success) setPhotos(res.photos);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, [claimId, navigate]);

  if (loading) {
    return (
      <AnalystLayout>
        <div className="flex items-center justify-center h-full min-h-[400px]">
          <div className="flex flex-col items-center gap-2">
            <div className="size-8 border-4 border-primary border-t-transparent rounded-full animate-spin"></div>
            <p className="text-muted-foreground font-medium">Loading photos...</p>
          </div>
        </div>
      </AnalystLayout>
    );
  }

  return (
    <AnalystLayout>
      <div className="p-6 md:p-10 space-y-8 max-w-4xl mx-auto">
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div>
            <Link to="/analyst/dashboard" className="text-xs text-muted-foreground hover:text-primary flex items-center gap-1 mb-2">
              <span className="material-symbols-outlined text-[16px]">chevron_left</span>
              Back to Dashboard
            </Link>
            <p className="text-[10px] font-black uppercase tracking-widest text-muted-foreground">Claim</p>
            <h1 className="text-2xl font-black text-foreground">{claimId}</h1>
          </div>
        </div>

        <div className="p-5 rounded-2xl border border-primary/20 bg-primary/5 flex items-center justify-between flex-wrap gap-3">
          <div>
            <p className="text-sm font-bold text-foreground">Ask the member to add their own photos</p>
            <p className="text-xs text-muted-foreground mt-0.5">
              Emails a link straight to this claim's photo upload page.
              {notifyState === "sent" && <span className="text-emerald-600 font-semibold"> {notifyMessage}</span>}
              {notifyState === "failed" && <span className="text-destructive font-semibold"> {notifyMessage}</span>}
            </p>
          </div>
          <Button size="sm" disabled={notifyState === "sending"} onClick={handleNotify} className="font-bold shrink-0">
            <span className="material-symbols-outlined text-[16px] mr-1.5">mail</span>
            {notifyState === "sending" ? "Sending..." : "Email Member"}
          </Button>
        </div>

        <Card className="overflow-hidden border-none shadow-sm">
          <CardHeader className="bg-primary/5 border-b border-primary/10">
            <CardTitle className="text-sm font-black uppercase tracking-widest flex items-center gap-2 text-primary">
              <span className="material-symbols-outlined">photo_library</span>
              Claim Photos
            </CardTitle>
          </CardHeader>
          <CardContent className="pt-6">
            <p className="text-xs text-muted-foreground mb-4">
              If the caller has since emailed or sent photos of the damage, attach them here.
            </p>
            <PhotosPanel
              claimId={claimId || ""}
              uploaderType="analyst"
              uploaderId={localStorage.getItem("analystId") || ""}
              initialPhotos={photos}
            />
          </CardContent>
        </Card>
      </div>
    </AnalystLayout>
  );
}
