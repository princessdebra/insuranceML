import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { correctDocumentField, uploadDocument, BASE_URL } from "@/lib/api";

const DOCUMENT_TYPES: { value: "police_abstract" | "id_document" | "garage_quote" | "other"; label: string }[] = [
  { value: "police_abstract", label: "Police Abstract" },
  { value: "id_document", label: "ID / Driving Licence" },
  { value: "garage_quote", label: "Garage Quote" },
  { value: "other", label: "Other" },
];

type DocumentRecord = {
  id: number;
  party: string;
  document_type: string;
  filename: string;
  parsed_fields: Record<string, unknown>;
  corrected_fields: Record<string, unknown> | null;
  corrected_by: string | null;
  corrected_at: string | null;
  extraction_confidence: number;
};

/**
 * Shows uploaded supporting documents (police abstract / ID / garage quote)
 * with their OCR-extracted data, and lets the party who uploaded a document
 * correct fields OCR got wrong. Reused on both the member's and assessor's
 * own claim-detail views -- each only gets an edit control on documents
 * belonging to their own party; the real access check happens server-side.
 */
export default function DocumentsPanel({
  documents,
  viewerParty,
  correctorId,
  onCorrected,
  claimId,
}: {
  documents: DocumentRecord[];
  viewerParty: "member" | "assessor";
  correctorId: string;
  onCorrected?: () => void;
  /** When provided, shows an "Add a Document" upload form -- e.g. a police
   * abstract that arrives after the claim was already filed. Omit to show
   * a read-only view (documents were already all captured at intake). */
  claimId?: string;
}) {
  const [editingId, setEditingId] = useState<number | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [pendingType, setPendingType] = useState<"police_abstract" | "id_document" | "garage_quote" | "other">("police_abstract");
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");

  const handleUpload = async () => {
    if (!pendingFile || !claimId) return;
    setUploading(true);
    setUploadError("");
    try {
      const res = await uploadDocument({
        claimId,
        party: viewerParty,
        documentType: pendingType,
        uploaderId: correctorId,
        file: pendingFile,
      });
      if (res.success) {
        setPendingFile(null);
        onCorrected?.();
      } else {
        setUploadError(res.detail || "Could not upload document.");
      }
    } catch (e) {
      setUploadError("Could not upload document — check your connection.");
    } finally {
      setUploading(false);
    }
  };

  const startEdit = (doc: DocumentRecord) => {
    const current = doc.corrected_fields || doc.parsed_fields || {};
    const asStrings: Record<string, string> = {};
    Object.entries(current).forEach(([k, v]) => {
      asStrings[k] = Array.isArray(v) ? v.join(", ") : v === null || v === undefined ? "" : String(v);
    });
    setDraft(asStrings);
    setEditingId(doc.id);
    setError("");
  };

  const cancelEdit = () => {
    setEditingId(null);
    setDraft({});
    setError("");
  };

  const saveEdit = async (doc: DocumentRecord) => {
    setSaving(true);
    setError("");
    try {
      const original = doc.corrected_fields || doc.parsed_fields || {};
      const correctedFields: Record<string, unknown> = {};
      Object.entries(draft).forEach(([k, v]) => {
        const wasArray = Array.isArray(original[k]);
        correctedFields[k] = wasArray
          ? v.split(",").map((s) => s.trim()).filter(Boolean)
          : v;
      });
      const res = await correctDocumentField({ documentId: doc.id, correctedFields, correctedBy: correctorId });
      if (res.success) {
        setEditingId(null);
        setDraft({});
        onCorrected?.();
      } else {
        setError(res.detail || "Could not save correction.");
      }
    } catch (e) {
      setError("Could not save correction — check your connection.");
    } finally {
      setSaving(false);
    }
  };

  const uploadForm = claimId ? (
    <div className="p-4 border rounded-xl bg-muted/10 space-y-3">
      <p className="text-[10px] font-black uppercase text-muted-foreground">Add a Document</p>
      <p className="text-[11px] text-muted-foreground">
        Got a police OB number or other document after filing? Upload it here — we'll update the claim automatically.
      </p>
      <div className="flex flex-wrap gap-2">
        {DOCUMENT_TYPES.map((t) => (
          <button
            key={t.value}
            type="button"
            onClick={() => setPendingType(t.value)}
            className={`text-[10px] font-bold uppercase px-2.5 py-1.5 rounded-lg border transition-colors ${
              pendingType === t.value
                ? "bg-primary text-primary-foreground border-primary"
                : "border-border text-muted-foreground hover:border-primary/40"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      <Input
        type="file"
        accept="image/*"
        disabled={uploading}
        onChange={(e) => setPendingFile(e.target.files?.[0] || null)}
      />
      {pendingFile && <p className="text-[10px] text-emerald-600">{pendingFile.name} selected</p>}
      {uploadError && <p className="text-xs text-destructive font-semibold">{uploadError}</p>}
      <Button size="sm" disabled={!pendingFile || uploading} onClick={handleUpload}>
        {uploading ? "Uploading..." : "Upload Document"}
      </Button>
    </div>
  ) : null;

  if (!documents?.length) {
    return (
      <div className="space-y-4">
        <div className="text-center py-8 text-xs italic text-muted-foreground bg-muted/20 rounded-xl border border-dashed">
          No supporting documents uploaded for this claim.
        </div>
        {uploadForm}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {documents.map((doc) => {
        const isMine = doc.party === viewerParty;
        const isEditing = editingId === doc.id;
        const displayed = doc.corrected_fields || doc.parsed_fields || {};
        const populatedFields = Object.entries(displayed).filter(
          ([, v]) => v !== null && v !== "" && !(Array.isArray(v) && v.length === 0)
        );
        const confidence = doc.extraction_confidence ?? 0;

        return (
          <div key={doc.id} className="p-4 border rounded-xl bg-muted/10 space-y-3">
            <div className="flex justify-between items-center flex-wrap gap-2">
              <div className="flex items-center gap-2">
                <Badge variant="outline" className="uppercase text-[9px] font-black">{doc.party}</Badge>
                <p className="text-xs font-black uppercase tracking-wide">
                  {doc.document_type?.replace(/_/g, " ")}
                </p>
              </div>
              <div className="flex items-center gap-2">
                {doc.corrected_fields && (
                  <Badge variant="outline" className="text-[9px] font-bold uppercase border-primary/40 text-primary">
                    Corrected
                  </Badge>
                )}
                <Badge className={confidence >= 60 ? "bg-emerald-600" : confidence > 0 ? "bg-amber-500" : "bg-muted-foreground/40"}>
                  {confidence}% confidence
                </Badge>
                <a
                  href={`${BASE_URL}/api/analysis/documents/${doc.id}/file`}
                  target="_blank"
                  rel="noopener noreferrer"
                  title="View original document"
                  className="text-muted-foreground hover:text-primary transition-colors"
                >
                  <span className="material-symbols-outlined text-[18px] align-middle">visibility</span>
                </a>
              </div>
            </div>
            <p className="text-[10px] text-muted-foreground font-mono">{doc.filename}</p>

            {isEditing ? (
              <div className="space-y-3 pt-2 border-t border-dashed">
                {Object.entries(draft).map(([k, v]) => (
                  <div key={k}>
                    <label className="text-[9px] font-black uppercase text-muted-foreground block mb-1">
                      {k.replace(/_/g, " ")}
                    </label>
                    <Input
                      value={v}
                      className="text-xs h-9"
                      onChange={(e) => setDraft((d) => ({ ...d, [k]: e.target.value }))}
                    />
                  </div>
                ))}
                {error && <p className="text-[11px] text-destructive font-semibold">{error}</p>}
                <div className="flex gap-2 pt-1">
                  <Button size="sm" className="text-xs" disabled={saving} onClick={() => saveEdit(doc)}>
                    {saving ? "Saving..." : "Save Corrections"}
                  </Button>
                  <Button size="sm" variant="outline" className="text-xs" disabled={saving} onClick={cancelEdit}>
                    Cancel
                  </Button>
                </div>
              </div>
            ) : (
              <>
                {populatedFields.length > 0 ? (
                  <div className="grid grid-cols-2 gap-2 pt-2 border-t border-dashed">
                    {populatedFields.map(([k, v]) => (
                      <div key={k} className="text-xs">
                        <span className="text-muted-foreground uppercase text-[9px] font-black block">{k.replace(/_/g, " ")}</span>
                        <span className="font-semibold text-foreground/90">
                          {Array.isArray(v) ? v.join(", ") : String(v)}
                        </span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-[11px] italic text-muted-foreground pt-2 border-t border-dashed">
                    No structured fields extracted — document may be illegible or extraction failed.
                  </p>
                )}
                {doc.corrected_fields && (
                  <p className="text-[10px] text-muted-foreground italic">
                    Corrected by {doc.corrected_by} on {doc.corrected_at?.split(" ")[0]}
                  </p>
                )}
                {isMine && (
                  <Button size="sm" variant="outline" className="text-[11px] mt-1" onClick={() => startEdit(doc)}>
                    {doc.corrected_fields ? "Edit Correction" : "This looks wrong — correct it"}
                  </Button>
                )}
              </>
            )}
          </div>
        );
      })}
      {uploadForm}
    </div>
  );
}
