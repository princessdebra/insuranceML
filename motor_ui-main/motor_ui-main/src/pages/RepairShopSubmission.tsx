import { useState, useEffect, useRef } from "react";
import { useNavigate, useParams } from "react-router-dom";
import AssessorLayout from "@/layouts/AssessorLayout";
import { submitRepairShopEstimate } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Avatar } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Calendar } from "@/components/ui/calendar";
import { format } from "date-fns";

// --- DYNAMIC RESPONSE VARIATIONS ---
const RESPONSES = {
  GREETING: (id: string, shop: string) => [
    `Workshop Portal Active. I'll help you submit the repair estimate for Claim ${id}. Workshop ID: ${shop}.`,
    `System Link Established. Documenting the repair quotation for ${id}. (Shop: ${shop})`,
    `Repair Shop Interface Online. Ready to process the financial estimate for Claim ${id}.`,
    `Workshop Diagnostic Mode. Initiating the estimate submission for ${id}. Authenticated as ${shop}.`,
    `Estimate Submission Portal for ${id} is now open. Workshop ID ${shop} is currently active.`
  ],
  ASK_ESTIMATE: [
    "Please provide a detailed estimate narrative. Include parts required, labor details, and diagnostic findings.",
    "Narrative required: Please describe the repair scope, including labor breakdown and necessary spare parts:",
    "Technical Findings: Provide a summary of the diagnostic results and the estimated work required:",
    "Scope of Work: Enter the repair details, labor hours, and specific parts involved in this quote:",
    "Please type the technical estimate narrative including a list of parts and labor intensity:"
  ],
  ASK_COST: [
    "Got the narrative. What is the total quotation amount (KES) for this repair?",
    "Understood. What is the total financial estimate (KES) for this entire repair job?",
    "Details logged. Please enter the total repair cost in KES (inclusive of all taxes):",
    "Quotation Phase: Input the final estimated amount in Kenyan Shillings:",
    "Narrative captured. Please provide the total KES value for this quotation:"
  ],
  ASK_DATE: [
    "When was this estimate generated?",
    "Please specify the date this quotation was issued:",
    "Record Date: When was this repair estimate officially created?",
    "To finalize the timeline, select the date this estimate was generated:",
    "Log Date: Indicate the generation date for this financial record:"
  ],
  ASK_PHOTOS: [
    "Requirement: Technical Documentation. Please upload the photos of the vehicle in the workshop.",
    "Evidence Acquisition: Attach photos of the vehicle currently at your workshop facility:",
    "Workshop Documentation: Please upload visual evidence of the damage from your garage site:",
    "Technical Requirement: Upload photos showing the vehicle's state and repair areas at the shop:",
    "Visual Verification: Please provide workshop photos documenting the vehicle and repair scope:"
  ],
  SUBMITTING: [
    "Processing technical financial quote...",
    "Transmitting workshop estimate to the claims management engine...",
    "Syncing quotation data and visual assets with the central server...",
    "Verifying financial parameters and uploading workshop evidence...",
    "Finalizing submission. Encrypting workshop data for claim review..."
  ]
};

type Message = {
  sender: "bot" | "user";
  text?: string;
  component?: React.ReactNode;
};

export default function RepairShopChatbot() {
  const { claimId: urlClaimId } = useParams();
  const navigate = useNavigate();
  const scrollRef = useRef<HTMLDivElement>(null);
  
  const claimId = urlClaimId || "CLM-2026-000007";
  const shopId = "SH-1234";

  const [messages, setMessages] = useState<Message[]>([]);
  const [step, setStep] = useState("START");
  const [inputValue, setInputValue] = useState("");
  const [isTyping, setIsTyping] = useState(false);

  const [formData, setFormData] = useState({
    claim_id: claimId,
    shop_id: shopId,
    repair_estimate: "",
    total_cost: "",
    estimate_date: format(new Date(), "yyyy-MM-dd"),
  });
  const [photos, setPhotos] = useState<File[]>([]);

  const getRand = (arr: string[]) => arr[Math.floor(Math.random() * arr.length)];

  // --- STREAMING ENGINE ---
  const addBotMessage = async (text?: string, component?: React.ReactNode) => {
    setIsTyping(true);
    // Simulate realistic "thinking"
    await new Promise(resolve => setTimeout(resolve, 800 + Math.random() * 800));
    setIsTyping(false);

    if (!text) {
      setMessages((prev) => [...prev, { sender: "bot", component }]);
      return;
    }

    // Initialize streaming message container
    let currentText = "";
    setMessages((prev) => [...prev, { sender: "bot", text: "" }]);

    for (let i = 0; i < text.length; i++) {
      currentText += text[i];
      setMessages((prev) => {
        const updated = [...prev];
        updated[updated.length - 1] = { ...updated[updated.length - 1], text: currentText };
        return updated;
      });
      await new Promise(resolve => setTimeout(resolve, 15)); // Streaming speed
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
      await addBotMessage(getRand(RESPONSES.GREETING(claimId, shopId)));
      await addBotMessage(getRand(RESPONSES.ASK_ESTIMATE));
      setStep("ASK_ESTIMATE");
    };
    startWorkflow();
  }, [claimId]);

  useEffect(() => {
    scrollRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isTyping, photos]);

  const handleSend = async () => {
    if (!inputValue) return;
    const val = inputValue;
    setInputValue("");
    addUserMessage(val);

    if (step === "ASK_ESTIMATE") {
      setFormData(prev => ({ ...prev, repair_estimate: val }));
      setStep("ASK_COST");
      await addBotMessage(getRand(RESPONSES.ASK_COST));
    } else if (step === "ASK_COST") {
      setFormData(prev => ({ ...prev, total_cost: val }));
      setStep("ASK_DATE");
      await addBotMessage(getRand(RESPONSES.ASK_DATE), 
        <div className="mt-3 bg-card p-2 rounded-xl border shadow-lg w-fit">
          <Calendar 
            mode="single"
            onSelect={(date) => date && handleDateSelect(date)}
            disabled={(date) => date > new Date()}
          />
        </div>
      );
    }
  };

  const handleDateSelect = async (date: Date) => {
    const formatted = format(date, "yyyy-MM-dd");
    addUserMessage(`Estimate Date: ${format(date, "PPP")}`);
    setFormData(prev => ({ ...prev, estimate_date: formatted }));
    
    setStep("ASK_PHOTOS");
    await addBotMessage(getRand(RESPONSES.ASK_PHOTOS));
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
    
    // Move photos to user chat history
    addUserMessage(undefined, (
      <div className="space-y-2">
        <p className="text-[10px] font-black opacity-70 uppercase tracking-widest">Documentation Submitted:</p>
        <PhotoGrid files={photos} />
      </div>
    ));

    setStep("PROCESSING");
    await addBotMessage(getRand(RESPONSES.SUBMITTING));
    
    try {
      const result = await submitRepairShopEstimate({
        ...formData,
        total_cost: Number(formData.total_cost),
        photos: photos
      });

      if (result.success) {
        await addBotMessage(
          result.message,
          <div className="mt-4 space-y-4">
            <div className="bg-card border border-border rounded-2xl overflow-hidden shadow-xl">
              <div className="bg-muted/50 p-4 border-b flex justify-between items-center">
                <p className="text-[10px] font-black uppercase text-muted-foreground tracking-widest">Financial Record</p>
                <Badge variant="outline" className="font-mono text-[10px]">{result.reference_number}</Badge>
              </div>
              <div className="p-5 space-y-4">
                 <div className="flex justify-between items-center border-b border-dashed pb-2">
                    <span className="text-xs text-muted-foreground">Total Quotation</span>
                    <span className="text-sm font-black text-primary">{result.submission_details.estimated_cost}</span>
                 </div>
                 <div className="flex justify-between items-center border-b border-dashed pb-2">
                    <span className="text-xs text-muted-foreground">Assets Uploaded</span>
                    <span className="text-sm font-bold">{result.submission_details.photos_uploaded} Images</span>
                 </div>
                 <div className="flex justify-between items-center">
                    <span className="text-xs text-muted-foreground">Claim Status</span>
                    <span className="text-[10px] font-black text-emerald-600 bg-emerald-50 px-2 py-0.5 rounded-full uppercase">{result.status}</span>
                 </div>
              </div>
            </div>

            <div className="bg-primary/5 border border-primary/10 p-4 rounded-xl space-y-2">
               <p className="text-[10px] font-black text-primary uppercase tracking-widest">Next Processing Steps:</p>
               {result.next_steps.map((s: string, i: number) => (
                 <p key={i} className="text-[11px] text-foreground/80 leading-relaxed">• {s}</p>
               ))}
            </div>

            <div className="flex flex-col gap-2">
               <Button className="w-full bg-primary font-bold py-6 shadow-lg shadow-primary/20" onClick={() => navigate(`/assessor/claim/${claimId}`)}>
                  Go to Claim Details
               </Button>
               <Button variant="ghost" className="w-full text-xs font-bold" onClick={() => navigate("/assessor/dashboard")}>
                  Return to Dashboard
               </Button>
            </div>
          </div>
        );
        setStep("FINISHED");
      }
    } catch (e) {
      await addBotMessage("Submission failed. Ensure the server is reachable and files are valid.");
    }
  };

  return (
    <AssessorLayout>
      <div className="flex flex-col h-[calc(100vh-64px)] bg-background">
        <div className="px-6 py-3 border-b bg-card flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className={`size-2 rounded-full animate-pulse ${step === "FINISHED" ? 'bg-emerald-500' : 'bg-primary'}`}></div>
            <span className="text-xs font-bold uppercase tracking-widest text-muted-foreground">Repair Shop Assistant</span>
          </div>
          <Badge variant="outline" className="font-mono text-[10px]">{claimId}</Badge>
        </div>

        <ScrollArea className="flex-1 p-4 md:p-8">
          <div className="max-w-2xl mx-auto space-y-6">
            {messages.map((m, i) => (
              <div key={i} className={`flex ${m.sender === "bot" ? "justify-start" : "justify-end"} gap-3`}>
                {m.sender === "bot" && (
                  <Avatar className="size-8 bg-primary text-primary-foreground flex items-center justify-center shadow-sm">
                    <span className="material-symbols-outlined text-sm">home_repair_service</span>
                  </Avatar>
                )}
                <div className={`max-w-[85%] rounded-2xl px-5 py-3 shadow-sm ${
                  m.sender === "bot" ? "bg-card text-foreground border border-primary/10 rounded-tl-none" : "bg-primary text-primary-foreground rounded-tr-none shadow-primary/20"
                }`}>
                  {m.text && <p className="text-sm leading-relaxed whitespace-pre-wrap">{m.text}</p>}
                  {m.component}
                </div>
              </div>
            ))}

            {step === "ASK_PHOTOS" && (
              <div className="flex justify-start gap-3">
                <Avatar className="size-8 bg-primary text-primary-foreground flex items-center justify-center">
                   <span className="material-symbols-outlined text-sm">home_repair_service</span>
                </Avatar>
                <div className="bg-card border rounded-2xl p-5 w-full max-w-[85%] shadow-lg border-primary/10">
                  <p className="text-xs font-black uppercase text-primary mb-4 tracking-widest">Evidence Acquisition</p>
                  <Input type="file" multiple accept="image/*" className="mb-4 text-xs cursor-pointer" onChange={(e) => setPhotos(Array.from(e.target.files || []))} />
                  
                  {photos.length > 0 && <PhotoGrid files={photos} />}
                  
                  <Button className="w-full bg-primary font-bold py-6 mt-4" disabled={photos.length === 0 || isTyping} onClick={triggerSubmission}>
                    {isTyping ? "Transmitting..." : "Finalize & Submit Estimate"}
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
              placeholder={step === "ASK_COST" ? "Enter quotation amount..." : step === "ASK_ESTIMATE" ? "Describe repairs..." : "System locked..."}
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