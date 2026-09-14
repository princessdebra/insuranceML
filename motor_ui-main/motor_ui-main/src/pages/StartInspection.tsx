import { useState, useEffect, useRef } from "react";
import { useNavigate, useParams } from "react-router-dom";
import AssessorLayout from "@/layouts/AssessorLayout";
import { submitAssessorReport, assessNarrative } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Avatar } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Calendar } from "@/components/ui/calendar";
import { format } from "date-fns";

// --- DYNAMIC RESPONSE VARIATIONS ---
const RESPONSES = {
  GREETING: (id: string) => [
    `Field Assessment Mode Activated. Documenting inspection for Claim ${id}. When is this inspection being conducted?`,
    `Diagnostic session initialized for ${id}. Please confirm the inspection date on the calendar:`,
    `Assessor portal active. I'm ready to record your findings for Claim ${id}. Select the date of the visit:`,
    `Initializing digital forensics for ${id}. To start the report, please indicate when the site visit occurred:`,
    `Digital assessment log started for ${id}. Kindly provide the date of the physical inspection:`
  ],
  ASK_COST: [
    "Understood. Based on evaluation, what is the total estimated repair cost in KES?",
    "Valuation phase: Please enter the total projected cost for repairs (KES):",
    "Analysis noted. What is your professional estimate for the total repair value in KES?",
    "Understood. Provide the ballpark repair cost figure in Kenyan Shillings:",
    "Got it. What's the total financial impact for repairs based on your assessment?"
  ],
  ASK_REPORT: [
    "Please provide the technical damage forensics (impact points, structural integrity).",
    "Describe your technical findings. Focus on impact zones and structural damage:",
    "Reporting phase: Please summarize the forensics and specific damage observed:",
    "Forensic details required: Enter your assessment of the structural and mechanical damage:",
    "Technical summary needed: Describe the nature of the damage and integrity of the vehicle:"
  ],
  ANALYZING_REPORT: [
    "Reviewing your findings for completeness...",
    "Checking whether this has enough technical detail...",
  ],
  ASK_CRUSH_DEPTH: [
    "What is the measured crush/deformation depth in mm? (Enter your best on-site measurement, or 'unknown')",
    "Please provide the measured crush depth in millimeters, or type 'unknown' if not measured:",
  ],
  ASK_APPROACH_ANGLE: [
    "Based on the damage pattern, what was the approximate impact angle?",
    "Which best describes the direction of impact based on your findings?",
  ],
  ASK_THIRD_PARTY_CONFIRM: [
    "Did you independently confirm the third-party vehicle's details on-site (registration, make/model)? Type them, or 'skip' if not applicable.",
    "Please note any third-party vehicle details you personally verified at the scene, or type 'skip':",
  ],
  ASK_PHOTOS: [
    "Requirement: Technical Evidence. Please upload the inspection photos below.",
    "Documentation required: Please attach the visual evidence assets from the field site:",
    "Final requirement: Upload all technical photos documenting the damage forensics:",
    "Evidence acquisition: Please provide the field photos to support your valuation:",
    "Visual verification needed: Upload your assessment photos to wrap up this report:"
  ],
  SUBMITTING: [
    "Synchronizing assessment data with the claims engine...",
    "Transmitting technical forensics to the head office...",
    "Uploading digital assets and finalized valuation...",
    "Encrypting report data and pushing to the central database...",
    "Finalizing diagnostic transmission. Please wait..."
  ]
};

type Message = {
  sender: "bot" | "user";
  text?: string;
  component?: React.ReactNode;
};

export default function StartInspectionChatbot() {
  const { claimId } = useParams();
  const navigate = useNavigate();
  const scrollRef = useRef<HTMLDivElement>(null);
  const assessorId = localStorage.getItem("assessorId") || "";

  const [messages, setMessages] = useState<Message[]>([]);
  const [step, setStep] = useState("START");
  const [inputValue, setInputValue] = useState("");
  const [isTyping, setIsTyping] = useState(false);

  const [formData, setFormData] = useState({
    claim_id: claimId || "",
    assessor_id: assessorId,
    damage_report: "",
    estimated_cost: "",
    // toISOString() converts to UTC before formatting -- for a timezone
    // where local time and UTC fall on different calendar days at the
    // moment this runs, that silently produces yesterday's or tomorrow's
    // date instead of today's. format() uses the Date object's local
    // components directly, so it always matches what the assessor's own
    // clock says "today" is.
    inspection_date: format(new Date(), "yyyy-MM-dd"),
    crush_depth_mm: "",
    approach_angle_deg: "",
    third_party_vehicle_confirmed: "",
  });

  const [photos, setPhotos] = useState<File[]>([]);
  const [garageQuote, setGarageQuote] = useState<File | null>(null);
  const [idDocument, setIdDocument] = useState<File | null>(null);
  const narrativeClarifyRoundRef = useRef(0);

  const getRand = (arr: string[]) => arr[Math.floor(Math.random() * arr.length)];

  // --- STREAMING ENGINE ---
  const addBotMessage = async (text?: string, component?: React.ReactNode) => {
    setIsTyping(true);
    // Realistic thinking delay
    await new Promise(resolve => setTimeout(resolve, 800 + Math.random() * 800));
    setIsTyping(false);

    if (!text) {
      setMessages((prev) => [...prev, { sender: "bot", component }]);
      return;
    }

    // Initialize streaming message
    let currentText = "";
    setMessages((prev) => [...prev, { sender: "bot", text: "" }]);

    for (let i = 0; i < text.length; i++) {
      currentText += text[i];
      setMessages((prev) => {
        const updated = [...prev];
        updated[updated.length - 1] = { ...updated[updated.length - 1], text: currentText };
        return updated;
      });
      await new Promise(resolve => setTimeout(resolve, 15)); 
    }

    if (component) {
      setMessages((prev) => {
        const updated = [...prev];
        updated[updated.length - 1] = { ...updated[updated.length - 1], component };
        return updated;
      });
    }
  };

  const addUserMessage = (text?: string, component?: React.ReactNode) => {
    setMessages((prev) => [...prev, { sender: "user", text, component }]);
  };

  // 1. Initial Greeting
  useEffect(() => {
    const startWorkflow = async () => {
      await addBotMessage(getRand(RESPONSES.GREETING(claimId || "N/A")), 
        <div className="mt-3 bg-card p-2 rounded-xl border shadow-lg w-fit">
          <Calendar
            mode="single"
            // No `selected` here on purpose: pre-selecting today made the
            // calendar's own "click an already-selected day to deselect it"
            // behavior fire on the very first click, which the `date &&`
            // guard below then silently swallowed -- exactly the "clicking
            // 8 does nothing" bug. The assessor is meant to actively pick a
            // date; react-day-picker already marks today distinctly on its
            // own without needing it pre-selected.
            onSelect={(date) => date && handleDateSelect(date)}
            disabled={(date) => date > new Date()}
            className="rounded-md"
          />
        </div>
      );
      setStep("ASK_DATE");
    };
    startWorkflow();
  }, [claimId]);

  useEffect(() => {
    scrollRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isTyping, photos]);

  const handleDateSelect = async (date: Date) => {
    const formatted = format(date, "yyyy-MM-dd");
    addUserMessage(`Inspection Date: ${format(date, "PPP")}`);
    setFormData(prev => ({ ...prev, inspection_date: formatted }));
    setStep("ASK_COST");
    await addBotMessage(getRand(RESPONSES.ASK_COST));
  };

  // --- STRUCTURED FOLLOW-UP STEPS ---
  // Crush depth / approach angle / third-party confirmation are asked as
  // deliberate on-site measurements rather than left to narrative inference
  // -- these feed the physics engine as direct, authoritative overrides
  // instead of being guessed from the free-text report the same way the
  // member's narrative used to be.

  const askCrushDepth = async () => {
    setStep("ASK_CRUSH_DEPTH");
    await addBotMessage(getRand(RESPONSES.ASK_CRUSH_DEPTH));
  };

  const askApproachAngle = async () => {
    setStep("ASK_APPROACH_ANGLE");
    await addBotMessage(getRand(RESPONSES.ASK_APPROACH_ANGLE),
      <div className="flex flex-wrap gap-2 mt-3">
        {[
          { label: "Rear-end (0°)", value: "0" },
          { label: "Angled (45°)", value: "45" },
          { label: "T-bone (90°)", value: "90" },
          { label: "Angled head-on (135°)", value: "135" },
          { label: "Head-on (180°)", value: "180" },
          { label: "Unknown", value: "" },
        ].map((opt) => (
          <Button key={opt.label} variant="secondary" className="rounded-full font-bold" onClick={() => handleApproachAngleSelect(opt.label, opt.value)}>
            {opt.label}
          </Button>
        ))}
      </div>
    );
  };

  const handleApproachAngleSelect = async (label: string, value: string) => {
    addUserMessage(label);
    setFormData(prev => ({ ...prev, approach_angle_deg: value }));
    setStep("ASK_THIRD_PARTY_CONFIRM");
    await addBotMessage(getRand(RESPONSES.ASK_THIRD_PARTY_CONFIRM));
  };

  const handleSend = async () => {
    if (!inputValue) return;
    const val = inputValue;
    setInputValue("");
    addUserMessage(val);

    if (step === "ASK_COST") {
      setFormData(prev => ({ ...prev, estimated_cost: val }));
      setStep("ASK_REPORT");
      await addBotMessage(getRand(RESPONSES.ASK_REPORT));
    } else if (step === "ASK_REPORT") {
      // Agentic step: same sufficiency check used on the member's narrative
      // -- a one-line assessor report used to sail straight through even
      // though it's treated as the MORE authoritative source for physics
      // and narrative intelligence whenever it's present.
      const combinedReport = formData.damage_report
        ? `${formData.damage_report}\n\n${val}`
        : val;
      setFormData(prev => ({ ...prev, damage_report: combinedReport }));

      await addBotMessage(getRand(RESPONSES.ANALYZING_REPORT));
      let sufficient = true;
      let clarifyingQuestion: string | null = null;
      try {
        const assessment = await assessNarrative({
          narrative: combinedReport,
          claim_type: "motor",
          round: narrativeClarifyRoundRef.current,
        });
        sufficient = assessment.sufficient;
        clarifyingQuestion = assessment.clarifying_question;
      } catch (err) {
        console.error("Report assessment failed, proceeding anyway", err);
      }

      if (!sufficient && clarifyingQuestion) {
        narrativeClarifyRoundRef.current += 1;
        await addBotMessage(clarifyingQuestion);
      } else {
        askCrushDepth();
      }
    } else if (step === "ASK_CRUSH_DEPTH") {
      setFormData(prev => ({ ...prev, crush_depth_mm: val.toLowerCase() === "unknown" ? "" : val }));
      askApproachAngle();
    } else if (step === "ASK_THIRD_PARTY_CONFIRM") {
      setFormData(prev => ({ ...prev, third_party_vehicle_confirmed: val.toLowerCase() === "skip" ? "" : val }));
      setStep("ASK_PHOTOS");
      await addBotMessage(getRand(RESPONSES.ASK_PHOTOS));
    }
  };

  const PhotoGrid = ({ files }: { files: File[] }) => (
    <div className="grid grid-cols-3 gap-2 mt-2">
      {files.map((p, idx) => (
        <div key={idx} className="aspect-square bg-muted rounded-lg border overflow-hidden relative shadow-sm">
          <img src={URL.createObjectURL(p)} className="object-cover w-full h-full" alt="evidence" />
        </div>
      ))}
    </div>
  );

  const triggerSubmission = async () => {
    if (photos.length === 0) return;
    
    // Move photos to user chat history immediately
    addUserMessage(undefined, (
      <div className="space-y-2">
        <p className="text-[10px] font-bold opacity-70 uppercase tracking-widest">Evidence Uploaded:</p>
        <PhotoGrid files={photos} />
      </div>
    ));

    // Hide UI and start bot reaction
    setStep("PROCESSING");
    await addBotMessage(getRand(RESPONSES.SUBMITTING));
    
    try {
      const result = await submitAssessorReport({
        claim_id: formData.claim_id,
        assessor_id: formData.assessor_id,
        damage_report: formData.damage_report,
        estimated_cost: Number(formData.estimated_cost),
        inspection_date: formData.inspection_date,
        crush_depth_mm: formData.crush_depth_mm ? Number(formData.crush_depth_mm) : undefined,
        approach_angle_deg: formData.approach_angle_deg ? Number(formData.approach_angle_deg) : undefined,
        third_party_vehicle_confirmed: formData.third_party_vehicle_confirmed || undefined,
        photos: photos,
        garage_quote: garageQuote || undefined,
        id_document: idDocument || undefined,
      });

      if (result.success) {
        await addBotMessage(
          result.message,
          <div className="mt-4 space-y-4">
            <div className="bg-primary text-primary-foreground rounded-2xl p-5 shadow-xl relative overflow-hidden">
               <span className="material-symbols-outlined absolute -right-6 -bottom-6 text-[120px] opacity-10">fact_check</span>
               <p className="text-[10px] uppercase font-black opacity-70 mb-1">Assessor Reference</p>
               <h3 className="text-xl font-black mb-4">{result.reference_number}</h3>
               
               <div className="space-y-3 bg-white/10 p-4 rounded-xl">
                  <div className="flex justify-between text-[11px] border-b border-white/20 pb-2">
                    <span>Valuation</span><span className="font-bold">{result.submission_details.estimated_cost}</span>
                  </div>
                  <div className="flex justify-between text-[11px]">
                    <span>Evidence</span><span className="font-bold">{result.submission_details.photos_uploaded} Assets</span>
                  </div>
               </div>
            </div>
            
            <div className="flex flex-col gap-2">
               <Button className="w-full bg-primary font-bold py-6" onClick={() => navigate(`/assessor/claim/${claimId}`)}>
                 View Intelligence Report
               </Button>
               <Button variant="outline" className="w-full" onClick={() => navigate("/assessor/dashboard")}>
                 Return to Dashboard
               </Button>
            </div>
          </div>
        );
        setStep("FINISHED");
      }
    } catch (e) {
      await addBotMessage("Transmission error. Please check connection.");
    }
  };

  return (
    <AssessorLayout>
      <div className="flex flex-col h-[calc(100vh-64px)] bg-background">
        <div className="px-6 py-3 border-b bg-card flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className={`size-2 rounded-full animate-pulse ${step === "FINISHED" ? 'bg-emerald-500' : 'bg-primary'}`}></div>
            <span className="text-xs font-bold uppercase tracking-widest text-muted-foreground">Diagnostic Assistant</span>
          </div>
          <Badge variant="outline" className="font-mono text-[10px]">AUTH: {assessorId}</Badge>
        </div>

        <ScrollArea className="flex-1 p-4 md:p-8">
          <div className="max-w-2xl mx-auto space-y-6">
            {messages.map((m, i) => (
              <div key={i} className={`flex ${m.sender === "bot" ? "justify-start" : "justify-end"} gap-3`}>
                {m.sender === "bot" && (
                  <Avatar className="size-8 bg-primary text-primary-foreground flex items-center justify-center shadow-sm">
                    <span className="material-symbols-outlined text-sm">engineering</span>
                  </Avatar>
                )}
                <div className={`max-w-[85%] rounded-2xl px-5 py-3 shadow-sm ${
                  m.sender === "bot" 
                    ? "bg-card text-foreground border border-primary/10 rounded-tl-none" 
                    : "bg-primary text-primary-foreground rounded-tr-none"
                }`}>
                  {m.text && <p className="text-sm leading-relaxed whitespace-pre-wrap">{m.text}</p>}
                  {m.component}
                </div>
              </div>
            ))}

            {step === "ASK_PHOTOS" && (
              <div className="flex justify-start gap-3">
                <Avatar className="size-8 bg-primary text-primary-foreground flex items-center justify-center">
                   <span className="material-symbols-outlined text-sm">engineering</span>
                </Avatar>
                <div className="bg-card border rounded-2xl p-5 w-full max-w-[85%] shadow-lg border-primary/10">
                  <p className="text-xs font-black uppercase text-primary mb-4 tracking-widest">Evidence Acquisition</p>
                  <Input 
                    type="file" 
                    multiple 
                    accept="image/*" 
                    className="mb-4 text-xs cursor-pointer" 
                    onChange={(e) => setPhotos(Array.from(e.target.files || []))} 
                  />
                  {photos.length > 0 && <div className="mb-4"><PhotoGrid files={photos} /></div>}

                  <div className="border-t pt-4 mt-2 mb-4 space-y-3">
                    <p className="text-[10px] font-black uppercase text-primary tracking-widest">Supporting Documents (optional)</p>
                    <div>
                      <label className="text-[10px] text-muted-foreground mb-1 block">Garage Quote photo</label>
                      <Input type="file" accept="image/*" className="text-xs cursor-pointer" onChange={(e) => setGarageQuote(e.target.files?.[0] || null)} />
                      {garageQuote && <p className="text-[10px] text-emerald-600 mt-1"> {garageQuote.name}</p>}
                    </div>
                    <div>
                      <label className="text-[10px] text-muted-foreground mb-1 block">ID / Licence photo (on-site verification)</label>
                      <Input type="file" accept="image/*" className="text-xs cursor-pointer" onChange={(e) => setIdDocument(e.target.files?.[0] || null)} />
                      {idDocument && <p className="text-[10px] text-emerald-600 mt-1"> {idDocument.name}</p>}
                    </div>
                  </div>

                  <Button className="w-full bg-primary font-bold py-6" disabled={photos.length === 0 || isTyping} onClick={triggerSubmission}>
                    Finalize & Submit
                  </Button>
                </div>
              </div>
            )}

            {isTyping && <div className="p-2 bg-muted w-12 rounded-full text-center text-xs animate-pulse ml-11">...</div>}
            <div ref={scrollRef} />
          </div>
        </ScrollArea>

        <div className="p-4 border-t bg-card">
          <div className="max-w-2xl mx-auto flex gap-3">
            <Input
              className="flex-1 h-12"
              placeholder={["ASK_DATE", "ASK_APPROACH_ANGLE", "ASK_PHOTOS", "FINISHED"].includes(step) ? "Follow interface prompts..." : "Type details here..."}
              type={step === "ASK_COST" ? "number" : "text"}
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleSend()}
              disabled={["ASK_DATE", "ASK_APPROACH_ANGLE", "ASK_PHOTOS", "FINISHED", "PROCESSING"].includes(step)}
            />
            <Button className="size-12 rounded-full shadow-lg" onClick={handleSend} disabled={!inputValue || ["ASK_DATE", "ASK_APPROACH_ANGLE", "ASK_PHOTOS", "FINISHED"].includes(step)}>
              <span className="material-symbols-outlined">send</span>
            </Button>
          </div>
        </div>
      </div>
    </AssessorLayout>
  );
}