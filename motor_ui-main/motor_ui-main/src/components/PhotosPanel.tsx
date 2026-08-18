import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getClaimPhotos, addClaimPhotos, ClaimPhoto, BASE_URL } from "@/lib/api";

/**
 * View photos already on a claim and add more -- the original submission
 * only supports one round of photo evidence; this covers everything after
 * that (a member who didn't have photos ready at filing time, or an
 * analyst adding photos a caller emailed in after a phone-filed claim).
 * Reused on both the member's and the analyst's claim views.
 */
export default function PhotosPanel({
  claimId,
  uploaderType,
  uploaderId,
  initialPhotos,
  partyFilter,
}: {
  claimId: string;
  uploaderType: "member" | "analyst";
  uploaderId: string;
  initialPhotos: ClaimPhoto[];
  partyFilter?: string;
}) {
  const [photos, setPhotos] = useState<ClaimPhoto[]>(initialPhotos);
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");

  const refetch = async () => {
    const res = await getClaimPhotos(claimId, partyFilter);
    if (res.success) setPhotos(res.photos);
  };

  const handleUpload = async () => {
    if (pendingFiles.length === 0) return;
    setUploading(true);
    setError("");
    try {
      const res = await addClaimPhotos({ claimId, uploaderType, uploaderId, photos: pendingFiles });
      if (res.success) {
        setPendingFiles([]);
        await refetch();
      } else {
        setError(res.detail || "Could not add photos.");
      }
    } catch (e) {
      setError("Could not add photos — check your connection.");
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="space-y-4">
      {photos.length > 0 ? (
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
          {photos.map((p) => (
            <a
              key={p.id}
              href={`${BASE_URL}/api/analysis/photos/${p.id}/file`}
              target="_blank"
              rel="noopener noreferrer"
              className="group relative aspect-square rounded-xl overflow-hidden border border-border block"
            >
              <img
                src={`${BASE_URL}/api/analysis/photos/${p.id}/file`}
                alt={p.filename}
                className="w-full h-full object-cover group-hover:opacity-80 transition-opacity"
              />
              <span className="absolute bottom-1 left-1 text-[8px] font-bold uppercase bg-black/60 text-white px-1.5 py-0.5 rounded">
                {p.party}
              </span>
            </a>
          ))}
        </div>
      ) : (
        <div className="text-center py-8 text-xs italic text-muted-foreground bg-muted/20 rounded-xl border border-dashed">
          No photos uploaded for this claim yet.
        </div>
      )}

      <div className="p-4 border rounded-xl bg-muted/10 space-y-3">
        <p className="text-[10px] font-black uppercase text-muted-foreground">Add More Photos</p>
        <Input
          type="file"
          multiple
          accept="image/*"
          disabled={uploading}
          onChange={(e) => setPendingFiles(Array.from(e.target.files || []))}
        />
        {pendingFiles.length > 0 && (
          <p className="text-[10px] text-emerald-600">{pendingFiles.length} file(s) selected</p>
        )}
        {error && <p className="text-xs text-destructive font-semibold">{error}</p>}
        <Button size="sm" disabled={pendingFiles.length === 0 || uploading} onClick={handleUpload}>
          {uploading ? "Uploading..." : "Upload Photos"}
        </Button>
      </div>
    </div>
  );
}
