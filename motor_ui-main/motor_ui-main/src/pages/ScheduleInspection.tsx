import { useState, useEffect, useRef } from "react";
import { useNavigate, useParams } from "react-router-dom";
import AssessorLayout from "@/layouts/AssessorLayout";
import { scheduleInspection } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Avatar } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Calendar } from "@/components/ui/calendar";
import { format } from "date-fns";

// --- DYNAMIC RESPONSE VARIATIONS ---
const RESPONSES = {
  GREETING: (claimId: string) => [
    `Hello Assessor. I'll help you schedule the field inspection for Claim ${claimId}. When would you like to perform the inspection?`,
    `Greetings! Ready to book the field visit for Claim ${claimId}. Please select a preferred date from the calendar:`,
    `Systems online. I'm ready to coordinate the inspection for ${claimId}. Which date works best for your schedule?`,
    `Assessor, let's finalize the logistics for Claim ${claimId}. Please indicate your availability on the calendar below:`,
    `Good day. I'm your workflow assistant. To initiate the assessment for ${claimId}, pick an inspection date:`
  ],
  ASK_LOCATION: [
    "Understood. Where will the inspection take place? (e.g., AutoExpress Workshop, Thika Rd)",
    "Got it. Please provide the specific location or workshop address for this visit:",
    "Where should I record the inspection location?",
    "Please enter the site address or the name of the repair center:",
    "Understood. And what is the destination for this field assessment?"
  ],
  ASK_NOTES: [
    "Got it. Any specific notes or instructions for this inspection? (Type 'none' to skip)",
    "Noted. Are there any special instructions for the member or our records? (Or type 'none')",
    "Understood. Do you have any additional observations or notes to add? (Type 'none' if empty)",
    "Would you like to include any specific field notes for this assignment? (Type 'none' to bypass)",
    "Is there anything else the claims team should know? (Type 'none' if not applicable)"
  ],
  SCHEDULING: [
    "Updating assignment status and notifying the member... ⏳",
    "Syncing schedule with the claims management system... 📡",
    "Transmitting inspection data to the dispatch team... ⚙️",
    "Finalizing the calendar entry and triggering notifications... ✍️",
    "Registering the inspection details in the central database... 📂"
  ],
  CONFIRM_PROMPT: [
    "Please confirm the inspection schedule:",
    "Kindly review and confirm these appointment details:",
    "Does this schedule look correct to you?",
    "Please verify the following inspection parameters:",
    "Check the details below and confirm to finalize the booking:"
  ]
};

type Message = {
  sender: "bot" | "user";
  text?: string;
  component?: React.ReactNode;
};

export default function ScheduleInspectionChatbot() {
  const { claimId } = useParams();
  const navigate = useNavigate();
  const scrollRef = useRef<HTMLDivElement>(null);
  
  const assessorId = localStorage.getItem("assessorId") || "ASS001";

  const [messages, setMessages] = useState<Message[]>([]);
  const [step, setStep] = useState("START");
  const [inputValue, setInputValue] = useState("");
  const [isTyping, setIsTyping] = useState(false);

  const [formData, setFormData] = useState({
    assignment_id: "ASG-90D48EC0", 
    inspection_date: "",
    location: "",
    notes: "",
  });

  const getRand = (arr: string[]) => arr[Math.floor(Math.random() * arr.length)];

  // --- STREAMING ENGINE ---
  const addBotMessage = async (text?: string, component?: React.ReactNode) => {
    setIsTyping(true);
    
    // Realistic thinking delay
    await new Promise(resolve => setTimeout(resolve, 800 + Math.random() * 1000));
    
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

  const addUserMessage = (text: string) => {
    setMessages((prev) => [...prev, { sender: "user", text }]);
  };

  // 1. Initial Greeting
  useEffect(() => {
    const startWorkflow = async () => {
      await addBotMessage(getRand(RESPONSES.GREETING(claimId || "N/A")), 
        <div className="mt-3 bg-card p-2 rounded-xl border shadow-lg w-fit">
          <Calendar 
            mode="single"
            onSelect={(date) => date && handleDateSelect(date)}
            disabled={(date) => date < new Date(new Date().setHours(0,0,0,0))}
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
  }, [messages, isTyping]);

  // --- HANDLERS ---

  const handleDateSelect = async (date: Date) => {
    const formattedDate = format(date, "yyyy-MM-dd 09:00:00"); 
    addUserMessage(`Scheduled for ${format(date, "PPP")}`);
    setFormData(prev => ({ ...prev, inspection_date: formattedDate }));
    
    setStep("ASK_LOCATION");
    await addBotMessage(getRand(RESPONSES.ASK_LOCATION));
  };

  const handleSend = async () => {
    if (!inputValue) return;
    const val = inputValue;
    setInputValue("");
    addUserMessage(val);

    if (step === "ASK_LOCATION") {
      setFormData(prev => ({ ...prev, location: val }));
      setStep("ASK_NOTES");
      await addBotMessage(getRand(RESPONSES.ASK_NOTES));
    } else if (step === "ASK_NOTES") {
      const finalNotes = val.toLowerCase() === 'none' ? "" : val;
      const updatedData = { ...formData, notes: finalNotes };
      setFormData(updatedData);
      setStep("CONFIRMATION");
      showConfirmation(updatedData);
    }
  };

  const showConfirmation = async (data: any) => {
    await addBotMessage(getRand(RESPONSES.CONFIRM_PROMPT), 
      <div className="mt-3 space-y-4">
        <div className="bg-card border-2 border-primary/20 rounded-xl p-4 text-xs space-y-2 shadow-sm">
          <div className="flex justify-between border-b pb-2">
            <span className="text-muted-foreground">Claim ID</span>
            <span className="font-bold">{claimId}</span>
          </div>
          <div className="flex justify-between border-b pb-2 pt-1">
            <span className="text-muted-foreground">Date</span>
            <span className="font-bold">{data.inspection_date.split(' ')[0]}</span>
          </div>
          <div className="flex justify-between border-b pb-2 pt-1">
            <span className="text-muted-foreground">Location</span>
            <span className="font-bold">{data.location}</span>
          </div>
          {data.notes && (
            <div className="pt-1">
              <span className="text-muted-foreground">Notes:</span>
              <p className="italic mt-1">"{data.notes}"</p>
            </div>
          )}
        </div>
        <div className="flex gap-2">
          <Button className="flex-1 bg-primary font-bold" onClick={() => triggerScheduleAPI(data)}>
            Confirm Schedule
          </Button>
          <Button variant="outline" onClick={() => window.location.reload()}>
            Reset
          </Button>
        </div>
      </div>
    );
  };

  const triggerScheduleAPI = async (data: any) => {
    addUserMessage("Confirmed. Schedule the inspection.");
    await addBotMessage(getRand(RESPONSES.SCHEDULING));
    
    try {
      const result = await scheduleInspection({
        assignment_id: data.assignment_id,
        inspection_date: data.inspection_date,
        notes: data.notes ? `${data.location} - ${data.notes}` : data.location,
      });

      if (result.success) {
        await addBotMessage(
          result.message,
          <div className="mt-4 space-y-4">
            <div className="bg-emerald-600 text-white rounded-2xl p-5 shadow-xl relative overflow-hidden">
              <span className="material-symbols-outlined absolute -right-4 -top-4 text-8xl opacity-10">calendar_today</span>
              <p className="text-[10px] font-black uppercase opacity-70 mb-1">Status: {result.status || 'Scheduled'}</p>
              <h3 className="text-xl font-black mb-4">Inspection Confirmed</h3>
              <p className="text-xs">The assignment has been moved to 'In Progress'.</p>
            </div>
            
            <div className="flex flex-col gap-2">
              <Button className="w-full bg-primary font-bold py-6" onClick={() => navigate(`/assessor/claim/${claimId}`)}>
                View Claim Details
              </Button>
              <Button variant="ghost" className="w-full text-xs font-bold" onClick={() => navigate("/assessor/dashboard")}>
                Return to Dashboard
              </Button>
            </div>
          </div>
        );
        setStep("FINISHED");
      } else {
        await addBotMessage(`❌ Error: ${result.message}`);
        setStep("ASK_NOTES");
      }
    } catch (e) {
      await addBotMessage("I couldn't reach the server. Please check your connection and try again.");
    }
  };

  return (
    <AssessorLayout>
      <div className="flex flex-col h-[calc(100vh-64px)] bg-background">
        <div className="px-6 py-3 border-b bg-card flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className={`size-2 rounded-full animate-pulse ${step === "FINISHED" ? 'bg-emerald-500' : 'bg-primary'}`}></div>
            <span className="text-xs font-bold uppercase tracking-widest text-muted-foreground">Workflow Assistant</span>
          </div>
          <Badge variant="outline" className="font-mono text-[10px]">ASSESSOR: {assessorId}</Badge>
        </div>

        <ScrollArea className="flex-1 p-4 md:p-8">
          <div className="max-w-2xl mx-auto space-y-6">
            {messages.map((m, i) => (
              <div key={i} className={`flex ${m.sender === "bot" ? "justify-start" : "justify-end"} gap-3`}>
                {m.sender === "bot" && (
                  <Avatar className="size-8 bg-primary text-primary-foreground flex items-center justify-center">
                    <span className="material-symbols-outlined text-sm">support_agent</span>
                  </Avatar>
                )}
                <div className={`max-w-[85%] rounded-2xl px-5 py-3 shadow-sm ${
                  m.sender === "bot" 
                    ? "bg-card text-foreground border border-primary/10 rounded-tl-none" 
                    : "bg-primary text-primary-foreground rounded-tr-none shadow-primary/20"
                }`}>
                  {m.text && <p className="text-sm leading-relaxed whitespace-pre-wrap">{m.text}</p>}
                  {m.component}
                </div>
              </div>
            ))}
            {isTyping && <div className="p-2 bg-muted w-12 rounded-full text-center text-xs animate-pulse ml-11">...</div>}
            <div ref={scrollRef} />
          </div>
        </ScrollArea>

        <div className="p-4 border-t bg-card">
          <div className="max-w-2xl mx-auto flex gap-3">
            <Input
              className="flex-1 h-12"
              placeholder={["ASK_DATE", "CONFIRMATION", "FINISHED"].includes(step) ? "Follow the prompts above..." : "Type details here..."}
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleSend()}
              disabled={["ASK_DATE", "CONFIRMATION", "FINISHED", "PROCESSING"].includes(step)}
            />
            <Button 
              className="size-12 rounded-full shadow-lg" 
              onClick={handleSend} 
              disabled={["ASK_DATE", "CONFIRMATION", "FINISHED", "PROCESSING"].includes(step) || !inputValue}
            >
              <span className="material-symbols-outlined">send</span>
            </Button>
          </div>
          <p className="text-[9px] text-center text-muted-foreground mt-3 uppercase tracking-[0.2em] font-black opacity-60">
            Internal Operations Console
          </p>
        </div>
      </div>
    </AssessorLayout>
  );
}