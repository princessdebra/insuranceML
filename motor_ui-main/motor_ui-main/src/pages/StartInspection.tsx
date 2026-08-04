import { useState, useEffect, useRef } from "react";
import { useNavigate, useParams } from "react-router-dom";
import AssessorLayout from "@/layouts/AssessorLayout";
import { submitAssessorReport } from "@/lib/api";
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
  ASK_PHOTOS: [
    "Requirement: Technical Evidence. Please upload the inspection photos below.",
    "Documentation required: Please attach the visual evidence assets from the field site:",
    "Final requirement: Upload all technical photos documenting the damage forensics:",
    "Evidence acquisition: Please provide the field photos to support your valuation:",
    "Visual verification needed: Upload your assessment photos to wrap up this report:"
  ],
  SUBMITTING: [
    "Synchronizing assessment data with the claims engine... 🚀",
    "Transmitting technical forensics to the head office... 📡",
    "Uploading digital assets and finalized valuation... ⚙️",
    "Encrypting report data and pushing to the central database... 📂",
    "Finalizing diagnostic transmission. Please wait... ⏳"
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
    inspection_date: new Date().toISOString().split("T")[0],
  });
  
  const [photos, setPhotos] = useState<File[]>([]);

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
            selected={new Date(formData.inspection_date)}
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
      setFormData(prev => ({ ...prev, damage_report: val }));
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
        ...formData,
        estimated_cost: Number(formData.estimated_cost),
        photos: photos
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
              placeholder={["ASK_DATE", "ASK_PHOTOS", "FINISHED"].includes(step) ? "Follow interface prompts..." : "Type details here..."}
              type={step === "ASK_COST" ? "number" : "text"}
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleSend()}
              disabled={["ASK_DATE", "ASK_PHOTOS", "FINISHED", "PROCESSING"].includes(step)}
            />
            <Button className="size-12 rounded-full shadow-lg" onClick={handleSend} disabled={!inputValue || ["ASK_DATE", "ASK_PHOTOS", "FINISHED"].includes(step)}>
              <span className="material-symbols-outlined">send</span>
            </Button>
          </div>
        </div>
      </div>
    </AssessorLayout>
  );
}