import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import AdminLayout from "@/layouts/AdminLayout";
import { getBusinessRulesConfig, updateBusinessRulesConfig, BusinessRuleConfigField } from "@/lib/api";

export default function AdminSettings() {
  const navigate = useNavigate();
  const [fields, setFields] = useState<BusinessRuleConfigField[]>([]);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [savedKey, setSavedKey] = useState<string | null>(null);

  useEffect(() => {
    const adminId = localStorage.getItem("adminId");
    if (!adminId) {
      navigate("/admin/login");
      return;
    }
  }, [navigate]);

  const load = () => {
    setLoading(true);
    getBusinessRulesConfig()
      .then((res) => {
        if (res.success) {
          setFields(res.fields);
          setDraft(Object.fromEntries(res.fields.map((f) => [f.key, String(f.value)])));
        } else {
          setError("Couldn't load business rule settings.");
        }
        setLoading(false);
      })
      .catch(() => {
        setError("Couldn't load business rule settings.");
        setLoading(false);
      });
  };

  useEffect(load, []);

  const groups = Array.from(new Set(fields.map((f) => f.group)));

  const saveField = async (field: BusinessRuleConfigField) => {
    const adminId = localStorage.getItem("adminId") || "ADMIN";
    const raw = draft[field.key];
    const num = Number(raw);
    if (raw === "" || Number.isNaN(num)) {
      setError(`"${field.label}" must be a number.`);
      return;
    }
    setError("");
    setSaving(field.key);
    try {
      const res = await updateBusinessRulesConfig({ [field.key]: num }, adminId);
      if (res.success) {
        setFields((prev) => prev.map((f) => (f.key === field.key ? { ...f, value: num, is_overridden: num !== f.default } : f)));
        setSavedKey(field.key);
        setTimeout(() => setSavedKey((k) => (k === field.key ? null : k)), 1800);
      } else {
        setError(res.detail || "Save failed.");
      }
    } catch {
      setError("Save failed -- check your connection and try again.");
    } finally {
      setSaving(null);
    }
  };

  const resetField = async (field: BusinessRuleConfigField) => {
    const adminId = localStorage.getItem("adminId") || "ADMIN";
    setSaving(field.key);
    try {
      const res = await updateBusinessRulesConfig({ [field.key]: null }, adminId);
      if (res.success) {
        setFields((prev) => prev.map((f) => (f.key === field.key ? { ...f, value: f.default, is_overridden: false } : f)));
        setDraft((prev) => ({ ...prev, [field.key]: String(field.default) }));
      }
    } finally {
      setSaving(null);
    }
  };

  return (
    <AdminLayout>
      <div className="p-8 max-w-5xl mx-auto w-full">
        <div className="mb-8 flex flex-col sm:flex-row sm:items-center justify-between gap-4 rounded-3xl bg-gradient-to-br from-primary/10 via-primary/5 to-transparent border border-primary/10 px-7 py-6">
          <div>
            <h2 className="text-3xl font-black text-foreground">Business Rule Settings</h2>
            <p className="text-muted-foreground mt-1 text-sm">
              Tune the thresholds that drive automatic fraud/risk flagging -- changes apply to every claim analyzed from now on.
            </p>
          </div>
          <div className="flex items-center gap-2 bg-card border border-border rounded-full px-4 py-2 shadow-sm shrink-0">
            <span className="material-symbols-outlined text-[18px] text-primary">tune</span>
            <span className="text-sm font-semibold text-foreground">{fields.length} configurable thresholds</span>
          </div>
        </div>

        {error && (
          <div className="mb-6 px-5 py-3 rounded-2xl border border-destructive/20 bg-destructive/5 text-destructive text-sm font-semibold flex items-center gap-2">
            <span className="material-symbols-outlined text-[18px]">error</span>
            {error}
          </div>
        )}

        {loading ? (
          <div className="bg-card p-10 rounded-2xl border border-border shadow-sm text-center text-muted-foreground animate-pulse">
            Loading settings...
          </div>
        ) : (
          <div className="space-y-6">
            {groups.map((group) => (
              <div key={group} className="bg-card rounded-2xl border border-border shadow-sm overflow-hidden">
                <div className="px-6 py-4 border-b border-border flex items-center gap-3">
                  <span className="material-symbols-outlined text-primary bg-primary/10 p-2 rounded-xl text-[20px]">
                    {group === "Timing" ? "schedule" : group === "Cost" ? "payments" : "photo_camera"}
                  </span>
                  <h3 className="text-sm font-black uppercase tracking-widest text-foreground">{group}</h3>
                </div>
                <div className="p-6 space-y-5">
                  {fields
                    .filter((f) => f.group === group)
                    .map((field) => (
                      <div key={field.key} className="flex flex-col sm:flex-row sm:items-center gap-3 sm:gap-6 pb-5 last:pb-0 border-b last:border-b-0 border-border/60">
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 flex-wrap">
                            <p className="text-sm font-bold text-foreground">{field.label}</p>
                            {field.is_overridden && (
                              <span className="text-[9px] font-black uppercase px-1.5 py-0.5 rounded-full bg-primary/10 text-primary">Customized</span>
                            )}
                          </div>
                          <p className="text-xs text-muted-foreground mt-0.5">{field.help}</p>
                        </div>
                        <div className="flex items-center gap-2 shrink-0">
                          <div className="relative">
                            <input
                              type="number"
                              step="any"
                              value={draft[field.key] ?? ""}
                              onChange={(e) => setDraft((prev) => ({ ...prev, [field.key]: e.target.value }))}
                              className="w-28 pl-3 pr-14 py-2 border border-border rounded-full text-sm bg-background text-foreground focus:ring-2 focus:ring-primary/50 outline-none tabular-nums"
                            />
                            <span className="absolute right-3.5 top-1/2 -translate-y-1/2 text-[10px] font-bold uppercase text-muted-foreground pointer-events-none">
                              {field.unit}
                            </span>
                          </div>
                          <button
                            onClick={() => saveField(field)}
                            disabled={saving === field.key || draft[field.key] === String(field.value)}
                            className="text-xs font-bold bg-primary text-primary-foreground px-3.5 py-2 rounded-full hover:bg-primary/90 transition-all disabled:opacity-40 disabled:cursor-not-allowed"
                          >
                            {saving === field.key ? "Saving..." : savedKey === field.key ? "Saved ✓" : "Save"}
                          </button>
                          {field.is_overridden && (
                            <button
                              onClick={() => resetField(field)}
                              disabled={saving === field.key}
                              title={`Reset to default (${field.default} ${field.unit})`}
                              className="size-9 flex items-center justify-center rounded-full border border-border text-muted-foreground hover:text-destructive hover:border-destructive/30 transition-colors disabled:opacity-40"
                            >
                              <span className="material-symbols-outlined text-[18px]">restart_alt</span>
                            </button>
                          )}
                        </div>
                      </div>
                    ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </AdminLayout>
  );
}
