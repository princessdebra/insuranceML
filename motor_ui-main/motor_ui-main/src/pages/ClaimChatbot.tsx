import { useState, useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import MemberLayout from "@/layouts/MemberLayout";
import AnalystLayout from "@/layouts/AnalystLayout";
import { checkCoverage, getMemberPolicies, createClaim, submitMemberClaim, assessNarrative, ensureOllamaReady, uploadDocument, searchMembers, ExtractedIntakeFacts, MemberSearchResult } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Avatar } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Calendar } from "@/components/ui/calendar";
import { format } from "date-fns";

// --- DYNAMIC RESPONSE VARIATIONS ---
const RESPONSES = {
  GREETING: (name: string) => [
    `Hello ${name}! I'm your AI Claims Assistant. Let's start with your coverage verification. What type of claim is this?`,
    `Hi ${name}! I'm here to help you file your claim quickly. To get started, what's the nature of the incident?`,
    `Greetings, ${name}. I'll guide you through the claims process step-by-step. Which category does this claim fall under?`,
    `Welcome, ${name}. I'm Xenova AI. Let's look into your policy. What type of claim are we initiating today?`,
    `Hey ${name}! Ready to assist with your claim. First, please select the claim type below:`
  ],
  // Analyst-mode phrasing: the person typing is the analyst on the phone
  // with the member, not the member themselves -- "Welcome, Brian" reads as
  // if the bot thinks it's talking to Brian directly, which it isn't.
  ANALYST_GREETING: (name: string) => [
    `Let's get ${name}'s claim started. I'll guide you through coverage verification first. What type of claim is this?`,
    `Filing for ${name}. To get started, what's the nature of the incident they've described?`,
    `I'll walk you through ${name}'s claim step-by-step. Which category does this fall under?`,
    `Let's look into ${name}'s policy. What type of claim are we initiating today?`,
    `Ready to log ${name}'s claim. First, please select the claim type below:`
  ],
  ASK_DATE: [
    "Please select the date of the incident from the calendar:",
    "When did this happen? Please pick the date below:",
    "To process this, I'll need the exact date of the incident:",
    "Could you indicate the date of the occurrence on this calendar?",
    "Got it. Now, please select the incident date:"
  ],
  ASK_DRIVER: [
    "Who was driving the vehicle?",
    "May I know who was behind the wheel at the time?",
    "Who was the operator of the vehicle during the incident?",
    "Please specify the driver at the time of the event:"
  ],
  ASK_DRIVER_NAME: [
    "Please enter the full name of the driver:",
    "I'll need the driver's full legal name, please:",
    "Who exactly was driving? Please type their full name:",
    "What is the name of the person who was driving?",
    "Kindly provide the driver's name for our records:"
  ],
  ASK_LOCATION: [
    "In which area or street did the incident happen?",
    "Where exactly did this occur? (Street, Area, or Landmark)",
    "Could you provide the location of the incident?",
    "I need the incident location. Where did it take place?",
    "Please specify the street or neighborhood where this happened:"
  ],
  ASK_DESCRIPTION: [
    "Please provide a very brief description of the incident for the coverage check.",
    "Briefly describe what happened so I can check your coverage.",
    "Give me a short summary of the event (e.g., 'Rear-end collision').",
    "What's the high-level summary of the incident?",
    "In a few words, tell me what happened during the incident."
  ],
  ANALYZING_COVERAGE: [
    "Analyzing your policy rules and verifying driver authority... 🛡️",
    "Checking your policy limits and coverage eligibility... 🔍",
    "Validating incident data against your active policy... ⚙️",
    "Running a real-time coverage verification. One moment... 🤖",
    "Cross-referencing your claim details with our underwriting rules... 📋"
  ],
  FETCHING_POLICY: [
    "Syncing policy IDs for claim registration... 📂",
    "Retrieving your policy details from the secure vault... 🔐",
    "Connecting to the policy management system... 🌐",
    "Fetching your specific policy parameters for this claim...",
    "Accessing your insurance records to finalize the setup..."
  ],
  CREATING_CLAIM: [
    "Registering claim and assigning field assessor... ⏳",
    "Creating your official claim record in the system... ✍️",
    "Finalizing claim registration and notifying our assessment team...",
    "Generating your unique claim ID and dispatching an assessor...",
    "Submitting your details to the claims department. Please wait..."
  ],
  ASK_NARRATIVE: [
    "Please provide a detailed narrative of the accident for the assessor.",
    "I need a detailed account of how the accident happened. What are the specifics?",
    "Could you describe the event in detail for our assessment team?",
    "Tell me the full story. How did the incident unfold?",
    "Please type a full description of the events leading up to the damage:"
  ],
  ANALYZING_NARRATIVE: [
    "Let me make sure I've got the full picture... 🤔",
    "Reviewing what you've told me so far... 🔍",
    "One moment, checking if I have enough detail for the assessor... 🧠",
    "Just double-checking your account for completeness...",
  ],
  ASK_THIRD_PARTY: [
    "Was another vehicle, pedestrian, or object involved in this incident?",
    "Was there a third party involved — another car, a pedestrian, or something you collided with?",
  ],
  ASK_THIRD_PARTY_DETAILS: [
    "Please provide the other vehicle's registration number and type/model if you know it (or write 'unknown').",
    "What details do you have on the other party — registration plate, vehicle type? Write 'unknown' if you don't have this.",
  ],
  ASK_THIRD_PARTY_FLED: [
    "Did the other party leave the scene before you could exchange details?",
    "Was the other driver still there when you exchanged information, or did they leave?",
  ],
  ASK_POLICE: [
    "Was this incident reported to the police?",
    "Did you file a report with the police about this incident?",
  ],
  ASK_OB_NUMBER: [
    "Please provide the police OB/Abstract number.",
    "What's the OB (Occurrence Book) or Abstract number from the police report?",
  ],
  ASK_WITNESSES: [
    "Were there any witnesses to the incident?",
    "Did anyone else see what happened?",
  ],
  ASK_WITNESS_DETAILS: [
    "Please provide the witness's name and phone number if available.",
    "What contact details do you have for the witness?",
  ],
  ASK_INJURIES: [
    "Was anyone injured in the incident?",
    "Did the incident result in any injuries to you, your passengers, or anyone else?",
  ],
  ASK_INJURY_DETAILS: [
    "Please briefly describe the injuries.",
    "Could you give a short description of the injuries sustained?",
  ],
  ASK_COST: [
    "What is your total estimated claim or incident-related cost in KES?",
    "How much do you estimate the overall costs or expenses will be (KES)?",
    "Please provide a rough estimate of the total incident costs in KES:",
    "What's the ballpark figure for the total expenses in KES?",
    "Kindly input the estimated claim or loss value in Kenyan Shillings:"
  ],
  ASK_PHOTOS: [
    "Finally, please upload photos of the damage to complete the submission.",
    "Almost done! Please upload visual evidence of the damage.",
    "I'll need some photos of the incident to wrap this up.",
    "Please attach photos showing the damage for our assessors.",
    "To finish, kindly upload any photos you have of the scene and damage."
  ],
  SUBMITTING: [
    "Uploading evidence and analyzing submission... 🚀",
    "Transmitting your claim file to the headquarters... 📡",
    "Finalizing your submission and archiving the evidence...",
    "Syncing your photos and narrative with the claim record...",
    "Processing your final claim report. This won't take long..."
  ]
};

type Message = {
  sender: "bot" | "user";
  text?: string;
  component?: React.ReactNode;
};

export default function ClaimChatbot({ analystMode = false }: { analystMode?: boolean }) {
  const navigate = useNavigate();
  const scrollRef = useRef<HTMLDivElement>(null);

  const selfMemberId = localStorage.getItem("memberId") || "";
  const selfMemberName = localStorage.getItem("memberName") || "Member";

  // In analyst mode there's no logged-in member -- the caller's identity is
  // resolved via search partway through the conversation instead of coming
  // from this session's own login.
  const [resolvedMember, setResolvedMember] = useState<{ id: string; name: string } | null>(
    analystMode ? null : { id: selfMemberId, name: selfMemberName }
  );
  const memberId = resolvedMember?.id || "";
  const memberName = resolvedMember?.name || "Member";

  const [memberQuery, setMemberQuery] = useState("");
  const [memberResults, setMemberResults] = useState<MemberSearchResult[]>([]);
  const [searchingMembers, setSearchingMembers] = useState(false);

  const [messages, setMessages] = useState<Message[]>([]);
  const [step, setStep] = useState("START");
  const [inputValue, setInputValue] = useState("");
  const [isTyping, setIsTyping] = useState(false);

  // const [formData, setFormData] = useState({
  //   member_id: memberId,
  //   claim_type: "",
  //   incident_date: "",
  //   incident_location: "",
  //   driver_name: "",
  //   brief_description: "",
  //   coverage_check_id: "", 
  //   policy_id: "",
  //   claim_id: "", 
  //   narrative: "", 
  //   estimated_cost: "", 
  // });

  // 1. ADD THIS REF to track the latest data without closure issues
  const formDataRef = useRef({
    member_id: memberId,
    claim_type: "",
    incident_date: "",
    incident_location: "",
    driver_name: "",
    brief_description: "",
    coverage_check_id: "", 
    policy_id: "",
    claim_id: "",
    narrative: "",
    estimated_cost: "",
    third_party_involved: "",
    third_party_details: "",
    third_party_fled: "",
    police_reported: "",
    police_ob_number: "",
    witnesses_present: "",
    witness_details: "",
    injuries_reported: "",
    injury_details: "",
  });

  const [formData, setFormData] = useState(formDataRef.current);
  const [photos, setPhotos] = useState<File[]>([]);
  const [policeAbstract, setPoliceAbstract] = useState<File | null>(null);
  const [uploadingAbstract, setUploadingAbstract] = useState(false);
  const [idDocument, setIdDocument] = useState<File | null>(null);

  // Agentic narrative intake: how many clarifying follow-ups we've asked
  // this claim, so the loop can't run forever if the claimant keeps
  // giving vague answers.
  const narrativeClarifyRoundRef = useRef(0);

  // Facts the narrative-sufficiency check already found explicit answers
  // for (third party, police, witnesses, injuries) -- lets the structured
  // follow-up questions confirm what was already said instead of asking
  // blind, which reads as not having listened to a detailed narrative.
  const narrativeGuessesRef = useRef<ExtractedIntakeFacts>({});

  // 2. Helper to update both state (for UI) and Ref (for API calls)
  const updateForm = (updates: Partial<typeof formDataRef.current>) => {
    const newData = { ...formDataRef.current, ...updates };
    formDataRef.current = newData;
    setFormData(newData);
  };

  // Helper to pick a random variation
  const getRand = (arr: string[]) => arr[Math.floor(Math.random() * arr.length)];

  // --- STREAMING ENGINE ---
  const addBotMessage = async (text?: string, component?: React.ReactNode) => {
    setIsTyping(true);
    
    // Simulate thinking time (random between 1s and 2s)
    await new Promise(resolve => setTimeout(resolve, 1000 + Math.random() * 1000));
    
    setIsTyping(false);

    if (!text) {
      setMessages((prev) => [...prev, { sender: "bot", component }]);
      return;
    }

    // Initialize streaming message
    let currentText = "";
    setMessages((prev) => [...prev, { sender: "bot", text: "" }]);

    // Stream text character by character
    for (let i = 0; i < text.length; i++) {
      currentText += text[i];
      setMessages((prev) => {
        const updated = [...prev];
        updated[updated.length - 1] = { ...updated[updated.length - 1], text: currentText };
        return updated;
      });
      await new Promise(resolve => setTimeout(resolve, 15)); // Typing speed
    }

    // If there is a component, add it after text streaming finishes
    if (component) {
      setMessages((prev) => {
        const updated = [...prev];
        updated[updated.length - 1] = { ...updated[updated.length - 1], component };
        return updated;
      });
    }
  };

  const addUserMessage = (text: string, component?: React.ReactNode) => {
    setMessages((prev) => [...prev, { sender: "user", text, component }]);
  };

  // As soon as a member starts filing a claim, make sure the self-hosted AI
  // stack (Ollama, reached over the SSH tunnel to the devserver) is actually
  // up -- fire-and-forget, since the tunnel/container occasionally die
  // silently between claims and everything downstream (coverage check,
  // narrative analysis, physics) depends on it. Never blocks the greeting;
  // if recovery is needed it happens in the background while the claimant
  // is still answering the first couple of questions.
  useEffect(() => {
    ensureOllamaReady()
      .then((res) => {
        if (res.status !== "ready") {
          console.info(`AI systems check: ${res.status}`, res.actions_taken);
        }
      })
      .catch((err) => console.warn("AI systems readiness check failed", err));
  }, []);

  // 1. Initial Greeting -- in analyst mode this only runs once a caller has
  // been identified via search (see startMemberSearch/handleMemberSelected
  // below); a name override avoids reading a stale `memberName` closure
  // from before the just-selected member's state update lands.
  const startChat = async (firstNameOverride?: string) => {
    const firstName = (firstNameOverride || memberName).split(" ")[0];
    await addBotMessage(getRand(analystMode ? RESPONSES.ANALYST_GREETING(firstName) : RESPONSES.GREETING(firstName)),
      <div className="flex flex-wrap gap-2 mt-3">
        {["Motor", "Domestic", "Marine"].map(type => (
          <Button key={type} className="rounded-full font-bold" variant="secondary" onClick={() => handleClaimTypeSelect(type)}>
            {type}
          </Button>
        ))}
      </div>
    );
    setStep("ASK_TYPE");
  };

  const runMemberSearch = async (query: string) => {
    setMemberQuery(query);
    if (query.trim().length < 2) {
      setMemberResults([]);
      return;
    }
    setSearchingMembers(true);
    try {
      const res = await searchMembers(query.trim());
      setMemberResults(res.results || []);
    } catch (err) {
      console.error("Member search failed", err);
    } finally {
      setSearchingMembers(false);
    }
  };

  const handleMemberSelected = async (m: MemberSearchResult) => {
    setResolvedMember({ id: m.member_id, name: m.name });
    updateForm({ member_id: m.member_id });
    setMemberResults([]);
    setMemberQuery("");
    addUserMessage(`Filing for ${m.name} (${m.member_id})`);
    startChat(m.name);
  };

  const startMemberSearch = async () => {
    setStep("MEMBER_SEARCH");
    await addBotMessage("Who are you filing this claim for? Search by the caller's name, phone number, or member ID.");
  };

  useEffect(() => {
    if (analystMode) {
      startMemberSearch();
    } else {
      startChat();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isTyping, photos]);

  // --- LOGIC HANDLERS ---

  const handleClaimTypeSelect = async (type: string) => {
    addUserMessage(type);
    updateForm({ claim_type: type.toLowerCase() }); // Use helper
    setStep("ASK_DATE");
    await addBotMessage(getRand(RESPONSES.ASK_DATE),
      <div className="mt-1 bg-card p-2 rounded-xl border shadow-lg w-fit">
        <Calendar mode="single" onSelect={(date) => date && handleDateSelect(date)} disabled={(date) => date > new Date()} />
      </div>
    );
  };

  const handleDateSelect = async (date: Date) => {
    const formattedDate = format(date, "yyyy-MM-dd");
    addUserMessage(format(date, "PPP"));
    updateForm({ incident_date: formattedDate }); // Use helper
    setStep("ASK_DRIVER");
    await addBotMessage(getRand(RESPONSES.ASK_DRIVER),
      <div className="flex gap-2 mt-3">
        <Button variant="secondary" className="rounded-full font-bold" onClick={() => handleDriverSelect("ME")}>
          {analystMode ? `It was ${memberName.split(" ")[0]}` : "It was me"}
        </Button>
        <Button variant="secondary" className="rounded-full font-bold" onClick={() => handleDriverSelect("OTHER")}>Someone else</Button>
      </div>
    );
  };

  const handleDriverSelect = async (choice: "ME" | "OTHER") => {
    if (choice === "ME") {
      addUserMessage(analystMode ? `${memberName} was driving` : "I was driving");
      updateForm({ driver_name: memberName }); // Use helper
      askLocation();
    } else {
      addUserMessage(`Someone else was driving`);
      setStep("ASK_DRIVER_NAME");
      await addBotMessage(getRand(RESPONSES.ASK_DRIVER_NAME));
    }
  };

  const askLocation = async () => {
    setStep("ASK_LOCATION");
    await addBotMessage(getRand(RESPONSES.ASK_LOCATION));
  };

  // --- STRUCTURED FOLLOW-UP QUESTIONS ---
  // Asked once the narrative is deemed sufficient, before cost/photos. These
  // capture facts the fraud-detection models need as deliberate, confirmed
  // answers rather than hoping the claimant happens to mention them in prose.
  //
  // Each has a plain "ask blind" version (below) and a "confirm what the
  // narrative already said" version -- proceedPastNarrative/proceedToPolice/
  // proceedToWitnesses/proceedToInjuries pick between them using
  // narrativeGuessesRef, so a narrative that already clearly answered a
  // question gets a one-click confirmation instead of being asked as if
  // nothing was said.

  const proceedPastNarrative = () => {
    const facts = narrativeGuessesRef.current;
    if (facts.third_party_involved === "Yes") confirmThirdPartyYes(facts.third_party_summary || "");
    else if (facts.third_party_involved === "No") confirmThirdPartyNo();
    else askThirdParty();
  };

  const confirmThirdPartyYes = async (summary: string) => {
    setStep("CONFIRM_THIRD_PARTY");
    await addBotMessage(
      summary
        ? `Sounds like another party was involved — ${summary}. Is that right?`
        : "Sounds like another party was involved in this incident — is that right?",
      <div className="flex gap-2 mt-3">
        <Button variant="secondary" className="rounded-full font-bold" onClick={() => {
          addUserMessage("Yes, that's right");
          updateForm({ third_party_involved: "Yes", third_party_details: summary || "" });
          askThirdPartyFled();
        }}>Yes, that's right</Button>
        <Button variant="outline" className="rounded-full font-bold" onClick={() => {
          addUserMessage("Not quite — let me explain");
          askThirdParty();
        }}>Not quite, let me explain</Button>
      </div>
    );
  };

  const confirmThirdPartyNo = async () => {
    setStep("CONFIRM_THIRD_PARTY");
    await addBotMessage(
      "Sounds like this was a single-vehicle incident, no other party involved — is that right?",
      <div className="flex gap-2 mt-3">
        <Button variant="secondary" className="rounded-full font-bold" onClick={() => {
          addUserMessage("Yes, that's right");
          updateForm({ third_party_involved: "No", third_party_details: "N/A", third_party_fled: "N/A" });
          proceedToPolice();
        }}>Yes, that's right</Button>
        <Button variant="outline" className="rounded-full font-bold" onClick={() => {
          addUserMessage("Actually, let me explain");
          askThirdParty();
        }}>Actually, let me explain</Button>
      </div>
    );
  };

  const proceedToPolice = () => {
    const facts = narrativeGuessesRef.current;
    if (facts.police_reported === "Yes") confirmPoliceYes();
    else if (facts.police_reported === "No") confirmPoliceNo();
    else askPolice();
  };

  const confirmPoliceYes = async () => {
    setStep("CONFIRM_POLICE");
    await addBotMessage(
      "Sounds like this was reported to police — is that right?",
      <div className="flex gap-2 mt-3">
        <Button variant="secondary" className="rounded-full font-bold" onClick={() => {
          addUserMessage("Yes, that's right");
          updateForm({ police_reported: "Yes" });
          askPoliceAbstractUpload();
        }}>Yes, that's right</Button>
        <Button variant="outline" className="rounded-full font-bold" onClick={() => {
          addUserMessage("Not quite — let me clarify");
          askPolice();
        }}>Not quite, let me clarify</Button>
      </div>
    );
  };

  const confirmPoliceNo = async () => {
    setStep("CONFIRM_POLICE");
    await addBotMessage(
      "Sounds like this wasn't reported to police — is that right?",
      <div className="flex gap-2 mt-3">
        <Button variant="secondary" className="rounded-full font-bold" onClick={() => {
          addUserMessage("Yes, that's right");
          updateForm({ police_reported: "No", police_ob_number: "N/A" });
          proceedToWitnesses();
        }}>Yes, that's right</Button>
        <Button variant="outline" className="rounded-full font-bold" onClick={() => {
          addUserMessage("Actually, it was reported");
          askPolice();
        }}>Actually, it was reported</Button>
      </div>
    );
  };

  const proceedToWitnesses = () => {
    const facts = narrativeGuessesRef.current;
    if (facts.witnesses_present === "Yes") confirmWitnessesYes();
    else if (facts.witnesses_present === "No") confirmWitnessesNo();
    else askWitnesses();
  };

  const confirmWitnessesYes = async () => {
    setStep("CONFIRM_WITNESSES");
    await addBotMessage(
      "Sounds like there were witnesses — is that right?",
      <div className="flex gap-2 mt-3">
        <Button variant="secondary" className="rounded-full font-bold" onClick={() => {
          addUserMessage("Yes, that's right");
          updateForm({ witnesses_present: "Yes" });
          setStep("ASK_WITNESS_DETAILS");
          addBotMessage(getRand(RESPONSES.ASK_WITNESS_DETAILS));
        }}>Yes, that's right</Button>
        <Button variant="outline" className="rounded-full font-bold" onClick={() => {
          addUserMessage("Not quite — let me clarify");
          askWitnesses();
        }}>Not quite, let me clarify</Button>
      </div>
    );
  };

  const confirmWitnessesNo = async () => {
    setStep("CONFIRM_WITNESSES");
    await addBotMessage(
      "Sounds like there were no witnesses — is that right?",
      <div className="flex gap-2 mt-3">
        <Button variant="secondary" className="rounded-full font-bold" onClick={() => {
          addUserMessage("Yes, that's right");
          updateForm({ witnesses_present: "No", witness_details: "N/A" });
          proceedToInjuries();
        }}>Yes, that's right</Button>
        <Button variant="outline" className="rounded-full font-bold" onClick={() => {
          addUserMessage("Actually, there were witnesses");
          askWitnesses();
        }}>Actually, there were witnesses</Button>
      </div>
    );
  };

  const proceedToInjuries = () => {
    const facts = narrativeGuessesRef.current;
    if (facts.injuries_reported === "Yes") confirmInjuriesYes();
    else if (facts.injuries_reported === "No") confirmInjuriesNo();
    else askInjuries();
  };

  const confirmInjuriesYes = async () => {
    setStep("CONFIRM_INJURIES");
    await addBotMessage(
      "Sounds like there were injuries — is that right?",
      <div className="flex gap-2 mt-3">
        <Button variant="secondary" className="rounded-full font-bold" onClick={() => {
          addUserMessage("Yes, that's right");
          updateForm({ injuries_reported: "Yes" });
          setStep("ASK_INJURY_DETAILS");
          addBotMessage(getRand(RESPONSES.ASK_INJURY_DETAILS));
        }}>Yes, that's right</Button>
        <Button variant="outline" className="rounded-full font-bold" onClick={() => {
          addUserMessage("Not quite — let me clarify");
          askInjuries();
        }}>Not quite, let me clarify</Button>
      </div>
    );
  };

  const confirmInjuriesNo = async () => {
    setStep("CONFIRM_INJURIES");
    await addBotMessage(
      "Sounds like there were no injuries — is that right?",
      <div className="flex gap-2 mt-3">
        <Button variant="secondary" className="rounded-full font-bold" onClick={() => {
          addUserMessage("Yes, that's right");
          updateForm({ injuries_reported: "No", injury_details: "N/A" });
          setStep("ASK_DETAILED_COST");
          addBotMessage(getRand(RESPONSES.ASK_COST));
        }}>Yes, that's right</Button>
        <Button variant="outline" className="rounded-full font-bold" onClick={() => {
          addUserMessage("Actually, there were injuries");
          askInjuries();
        }}>Actually, there were injuries</Button>
      </div>
    );
  };

  const askThirdParty = async () => {
    setStep("ASK_THIRD_PARTY");
    await addBotMessage(getRand(RESPONSES.ASK_THIRD_PARTY),
      <div className="flex gap-2 mt-3">
        <Button variant="secondary" className="rounded-full font-bold" onClick={() => handleThirdPartySelect(true)}>Yes, another party was involved</Button>
        <Button variant="secondary" className="rounded-full font-bold" onClick={() => handleThirdPartySelect(false)}>No, single-vehicle incident</Button>
      </div>
    );
  };

  const handleThirdPartySelect = async (involved: boolean) => {
    addUserMessage(involved ? "Yes, another party was involved" : "No, single-vehicle incident");
    if (involved) {
      updateForm({ third_party_involved: "Yes" });
      setStep("ASK_THIRD_PARTY_DETAILS");
      await addBotMessage(getRand(RESPONSES.ASK_THIRD_PARTY_DETAILS));
    } else {
      updateForm({ third_party_involved: "No", third_party_details: "N/A", third_party_fled: "N/A" });
      proceedToPolice();
    }
  };

  const askThirdPartyFled = async () => {
    setStep("ASK_THIRD_PARTY_FLED");
    await addBotMessage(getRand(RESPONSES.ASK_THIRD_PARTY_FLED),
      <div className="flex gap-2 mt-3">
        <Button variant="secondary" className="rounded-full font-bold" onClick={() => handleThirdPartyFledSelect(true)}>Yes, they left</Button>
        <Button variant="secondary" className="rounded-full font-bold" onClick={() => handleThirdPartyFledSelect(false)}>No, we exchanged details</Button>
      </div>
    );
  };

  const handleThirdPartyFledSelect = async (fled: boolean) => {
    addUserMessage(fled ? "Yes, they left the scene" : "No, we exchanged details");
    updateForm({ third_party_fled: fled ? "Yes" : "No" });
    proceedToPolice();
  };

  const askPolice = async () => {
    setStep("ASK_POLICE");
    await addBotMessage(getRand(RESPONSES.ASK_POLICE),
      <div className="flex gap-2 mt-3">
        <Button variant="secondary" className="rounded-full font-bold" onClick={() => handlePoliceSelect(true)}>Yes</Button>
        <Button variant="secondary" className="rounded-full font-bold" onClick={() => handlePoliceSelect(false)}>No</Button>
      </div>
    );
  };

  const handlePoliceSelect = async (reported: boolean) => {
    addUserMessage(reported ? "Yes, it was reported" : "No, not reported");
    if (reported) {
      updateForm({ police_reported: "Yes" });
      askPoliceAbstractUpload();
    } else {
      updateForm({ police_reported: "No", police_ob_number: "N/A" });
      proceedToWitnesses();
    }
  };

  // A photo of the police abstract usually already contains the OB number
  // -- ask for the photo here (right when police involvement is confirmed)
  // and read it via OCR, instead of asking the claimant to separately type
  // a number that's sitting right there in the document they have.
  //
  // Doesn't apply in analyst mode: the analyst is on a phone call with the
  // caller, not holding a physical document to photograph, so this would
  // ask for something they can't possibly have in the moment -- go
  // straight to asking the caller for the OB number verbally instead.
  const askPoliceAbstractUpload = async () => {
    if (analystMode) {
      askObNumberTyped();
      return;
    }
    setStep("ASK_POLICE_ABSTRACT_UPLOAD");
    await addBotMessage("Do you have a photo of the police abstract? Upload it and I'll pull the OB number from it.");
  };

  const askObNumberTyped = async () => {
    setStep("ASK_OB_NUMBER");
    await addBotMessage(getRand(RESPONSES.ASK_OB_NUMBER));
  };

  const skipPoliceAbstractUpload = () => {
    addUserMessage("I don't have a photo — I'll type it in");
    setPoliceAbstract(null);
    askObNumberTyped();
  };

  const handlePoliceAbstractUpload = async () => {
    if (!policeAbstract) return;
    const file = policeAbstract;
    addUserMessage(`Uploading ${file.name}`);
    setPoliceAbstract(null);
    setStep("PROCESSING");
    setUploadingAbstract(true);
    await addBotMessage(getRand(["Reading the document... 🔍", "Extracting details from the abstract... 🔎"]));
    try {
      const res = await uploadDocument({
        claimId: formDataRef.current.claim_id,
        party: "member",
        documentType: "police_abstract",
        uploaderId: formDataRef.current.member_id,
        file,
      });
      setUploadingAbstract(false);
      const obNumber = (res.parsed_fields?.ob_number as string) || "";
      if (res.success && obNumber) {
        setStep("CONFIRM_OB_NUMBER");
        await addBotMessage(
          `I read the OB number as: ${obNumber} — is that correct?`,
          <div className="flex gap-2 mt-3">
            <Button variant="secondary" className="rounded-full font-bold" onClick={() => {
              addUserMessage("Yes, that's correct");
              updateForm({ police_ob_number: obNumber });
              proceedToWitnesses();
            }}>Yes, that's correct</Button>
            <Button variant="outline" className="rounded-full font-bold" onClick={() => {
              addUserMessage("No, let me type it");
              askObNumberTyped();
            }}>No, let me type it</Button>
          </div>
        );
      } else {
        await addBotMessage("I couldn't clearly read the OB number from that photo — could you type it in?");
        askObNumberTyped();
      }
    } catch (err) {
      setUploadingAbstract(false);
      await addBotMessage("Upload failed — could you just type the OB number instead?");
      askObNumberTyped();
    }
  };

  const askWitnesses = async () => {
    setStep("ASK_WITNESSES");
    await addBotMessage(getRand(RESPONSES.ASK_WITNESSES),
      <div className="flex gap-2 mt-3">
        <Button variant="secondary" className="rounded-full font-bold" onClick={() => handleWitnessesSelect(true)}>Yes</Button>
        <Button variant="secondary" className="rounded-full font-bold" onClick={() => handleWitnessesSelect(false)}>No</Button>
      </div>
    );
  };

  const handleWitnessesSelect = async (present: boolean) => {
    addUserMessage(present ? "Yes, there were witnesses" : "No witnesses");
    if (present) {
      updateForm({ witnesses_present: "Yes" });
      setStep("ASK_WITNESS_DETAILS");
      await addBotMessage(getRand(RESPONSES.ASK_WITNESS_DETAILS));
    } else {
      updateForm({ witnesses_present: "No", witness_details: "N/A" });
      proceedToInjuries();
    }
  };

  const askInjuries = async () => {
    setStep("ASK_INJURIES");
    await addBotMessage(getRand(RESPONSES.ASK_INJURIES),
      <div className="flex gap-2 mt-3">
        <Button variant="secondary" className="rounded-full font-bold" onClick={() => handleInjuriesSelect(true)}>Yes</Button>
        <Button variant="secondary" className="rounded-full font-bold" onClick={() => handleInjuriesSelect(false)}>No</Button>
      </div>
    );
  };

  const handleInjuriesSelect = async (injured: boolean) => {
    addUserMessage(injured ? "Yes, there were injuries" : "No injuries");
    if (injured) {
      updateForm({ injuries_reported: "Yes" });
      setStep("ASK_INJURY_DETAILS");
      await addBotMessage(getRand(RESPONSES.ASK_INJURY_DETAILS));
    } else {
      updateForm({ injuries_reported: "No", injury_details: "N/A" });
      setStep("ASK_DETAILED_COST");
      await addBotMessage(getRand(RESPONSES.ASK_COST));
    }
  };

  const handleSend = async () => {
    if (!inputValue) return;
    const val = inputValue;
    setInputValue("");
    addUserMessage(val);

    if (step === "ASK_DRIVER_NAME") {
      updateForm({ driver_name: val });
      askLocation();
    } else if (step === "ASK_LOCATION") {
      updateForm({ incident_location: val });
      setStep("ASK_DESCRIPTION");
      await addBotMessage(getRand(RESPONSES.ASK_DESCRIPTION));
    } else if (step === "ASK_DESCRIPTION") {
      updateForm({ brief_description: val }); // CRITICAL: This updates the ref immediately
      setStep("PROCESSING");
      triggerCoverageCheck(); // No need to pass data, it will use formDataRef
    } else if (step === "ASK_NARRATIVE") {
      // Agentic step: append what the claimant just said to the narrative
      // built up so far, then ask the LLM whether it's actually detailed
      // enough -- if not, ask ONE natural follow-up targeting exactly what's
      // missing, rather than always moving straight to the next form field.
      const combinedNarrative = formDataRef.current.narrative
        ? `${formDataRef.current.narrative}\n\n${val}`
        : val;
      updateForm({ narrative: combinedNarrative });

      await addBotMessage(getRand(RESPONSES.ANALYZING_NARRATIVE));
      let sufficient = true;
      let clarifyingQuestion: string | null = null;
      try {
        const assessment = await assessNarrative({
          narrative: combinedNarrative,
          claim_type: formDataRef.current.claim_type,
          round: narrativeClarifyRoundRef.current,
        });
        sufficient = assessment.sufficient;
        clarifyingQuestion = assessment.clarifying_question;
        narrativeGuessesRef.current = assessment.extracted_facts || {};
      } catch (err) {
        console.error("Narrative assessment failed, proceeding anyway", err);
      }

      if (!sufficient && clarifyingQuestion) {
        narrativeClarifyRoundRef.current += 1;
        // Stay on ASK_NARRATIVE -- the next answer gets appended above.
        await addBotMessage(clarifyingQuestion);
      } else {
        proceedPastNarrative();
      }
    } else if (step === "ASK_THIRD_PARTY_DETAILS") {
      updateForm({ third_party_details: val });
      askThirdPartyFled();
    } else if (step === "ASK_OB_NUMBER") {
      updateForm({ police_ob_number: val });
      proceedToWitnesses();
    } else if (step === "ASK_WITNESS_DETAILS") {
      updateForm({ witness_details: val });
      proceedToInjuries();
    } else if (step === "ASK_INJURY_DETAILS") {
      updateForm({ injury_details: val });
      setStep("ASK_DETAILED_COST");
      await addBotMessage(getRand(RESPONSES.ASK_COST));
    } else if (step === "ASK_DETAILED_COST") {
      updateForm({ estimated_cost: val });
      setStep("ASK_PHOTOS");
      await addBotMessage(
        analystMode
          ? "Last step. If the caller has emailed or sent photos you have on hand, you can attach them now — otherwise skip ahead, the assessor will capture photos during the on-site inspection."
          : getRand(RESPONSES.ASK_PHOTOS)
      );
    }
  };

  // --- API STEPS ---

  const triggerCoverageCheck = async () => {
    await addBotMessage(getRand(RESPONSES.ANALYZING_COVERAGE));
    // The shared AI server this runs against can occasionally take a couple
    // of minutes under load -- without this, a long wait with no feedback
    // looks identical to the page being stuck.
    const stillLoadingTimer = setTimeout(() => {
      addBotMessage("Still loading...");
    }, 15000);
    try {
      // Use the Ref value to ensure the description is present
      const result = await checkCoverage(formDataRef.current);
      if (result.success && result.coverage_decision === "COVERED") {
        updateForm({ coverage_check_id: result.check_id });
        await addBotMessage(
          result.message,
          <div className="mt-3 space-y-4">
            <div className="bg-emerald-50 border border-emerald-200 rounded-xl p-4 text-[11px] text-emerald-900">
               <div className="flex justify-between items-center mb-3">
                 <Badge className="bg-emerald-600">COVERAGE CONFIRMED</Badge>
                 <span className="font-mono opacity-60">{result.check_id}</span>
               </div>
               <div className="space-y-1 mb-4 italic">
                  {result.reasons.map((r: string, i: number) => <p key={i}>• {r}</p>)}
               </div>
               <div className="grid grid-cols-2 gap-2 pt-3 border-t border-emerald-200">
                  <div><p className="opacity-60 uppercase text-[9px] font-black">Policy</p><p className="font-bold">{result.policy_number}</p></div>
                  <div><p className="opacity-60 uppercase text-[9px] font-black">Excess</p><p className="font-bold">KES {result.applicable_excess.toLocaleString()}</p></div>
               </div>
            </div>
            <Button 
              className="w-full bg-primary font-bold py-6 shadow-lg shadow-primary/20" 
              onClick={() => fetchPolicyAndConfirm(result.policy_number, result.check_id)}
            >
              Proceed to Claim Creation
            </Button>
          </div>
        );
      } else {
        await addBotMessage(`❌ Coverage Denied`,
          <div className="mt-2 bg-destructive/5 border border-destructive/20 rounded-lg p-3 text-xs text-destructive">
            <p className="font-bold mb-2">{result.message}</p>
          </div>
        );
      }
    } catch (e) {
      await addBotMessage("The coverage server is unreachable.");
    } finally {
      clearTimeout(stillLoadingTimer);
    }
  };

  const fetchPolicyAndConfirm = async (policyNum: string, covId: string) => {
    addUserMessage("I want to proceed with the claim.");
    await addBotMessage(getRand(RESPONSES.FETCHING_POLICY));
    try {
      const res = await getMemberPolicies(memberId);
      const match = res.data.find((p: any) => p.policy_number === policyNum) || res.data[0];
      updateForm({ policy_id: match.policy_id });

      // Using Ref values here ensures the UI shows the correct confirmed details
      const current = formDataRef.current;
      await addBotMessage("Confirm registration details:", 
        <div className="mt-3 space-y-4">
          <div className="bg-card border-2 border-primary/10 rounded-xl p-4 text-xs space-y-2">
            <p><strong>Incident Date:</strong> {current.incident_date}</p>
            <p><strong>Location:</strong> {current.incident_location}</p>
            <p className="italic opacity-70">"{current.brief_description}"</p>
          </div>
          <Button className="w-full h-12 bg-primary font-bold" onClick={() => executeCreateClaim(covId, match.policy_id)}>
            Confirm & Create Claim
          </Button>
        </div>
      );
    } catch (e) { await addBotMessage("Policy retrieval error."); }
  };

  const executeCreateClaim = async (covId: string, polId: string) => {
    addUserMessage("Confirm. Create the claim.");
    await addBotMessage(getRand(RESPONSES.CREATING_CLAIM));
    try {
      // Construct payload directly from the Ref to avoid stale state/blanks
      const current = formDataRef.current;
      const claimPayload = {
        coverage_check_id: covId,
        member_id: current.member_id,
        policy_id: polId,
        incident_date: current.incident_date,
        incident_location: current.incident_location,
        brief_description: current.brief_description,
        claim_type: current.claim_type,
        filed_by_analyst_id: analystMode ? (localStorage.getItem("analystId") || undefined) : undefined,
      };

      const result = await createClaim(claimPayload);
      if (result.success) {
        updateForm({ claim_id: result.claim_id });
        await addBotMessage(
          result.message,
          <div className="mt-3 space-y-4">
            <div className="bg-primary text-primary-foreground rounded-xl p-4 shadow-xl">
              <div className="flex justify-between items-start mb-4">
                <div><p className="text-[9px] uppercase font-black opacity-60">Claim ID</p><p className="text-lg font-black">{result.claim_id}</p></div>
                <Badge variant="secondary" className="bg-white/20 text-white border-none">ACTIVE</Badge>
              </div>
              <div className="flex items-center gap-3 bg-white/10 p-3 rounded-lg">
                <Avatar className="size-8 bg-white/20 text-xs flex items-center justify-center font-bold">{result.assessor_assignment.assessor_name[0]}</Avatar>
                <div><p className="text-[10px] opacity-70">Assigned Assessor</p><p className="text-xs font-bold">{result.assessor_assignment.assessor_name}</p></div>
              </div>
            </div>
            <Button className="w-full bg-primary py-6 font-bold" onClick={() => { addUserMessage("Moving to final step."); setStep("ASK_NARRATIVE"); addBotMessage(getRand(RESPONSES.ASK_NARRATIVE)); }}>
              Proceed to Final Submission
            </Button>
          </div>
        );
      }
    } catch (e) { await addBotMessage("Claim registration failed."); }
  };

  const handleFinalSubmit = async (selectedPhotos: File[]) => {
    // A member self-filing must attach photos; an analyst filing over the
    // phone has none to attach in the moment, so this guard would otherwise
    // silently block every phone-filed submission.
    if (selectedPhotos.length === 0 && !analystMode) return;

    // 1. Show the user's action on the right side
    addUserMessage(
      selectedPhotos.length > 0 ? `Uploading ${selectedPhotos.length} photos and submitting report.` : "Submitting report — no photos attached yet.",
        <div className="grid grid-cols-2 gap-2 mt-2">
            {selectedPhotos.map((f, i) => (
                <div key={i} className="text-[9px] bg-white/10 p-1 rounded border border-white/20 overflow-hidden text-ellipsis whitespace-nowrap">
                   📸 {f.name}
                </div>
            ))}
        </div>
    );

    // 2. Clear photos and hide the upload UI immediately
    setStep("FINALIZING");
    setPhotos([]);
    const finalIdDocument = idDocument;
    setIdDocument(null);

    // 3. Bot responds
    await addBotMessage(getRand(RESPONSES.SUBMITTING));

    try {
      const result = await submitMemberClaim({
        claim_id: formData.claim_id,
        member_id: formData.member_id,
        narrative: formData.narrative,
        estimated_cost: Number(formData.estimated_cost),
        location: formData.incident_location,
        incident_date: formData.incident_date,
        photos: selectedPhotos,
        third_party_involved: formData.third_party_involved,
        third_party_details: formData.third_party_details,
        third_party_fled: formData.third_party_fled,
        police_reported: formData.police_reported,
        police_ob_number: formData.police_ob_number,
        witnesses_present: formData.witnesses_present,
        witness_details: formData.witness_details,
        injuries_reported: formData.injuries_reported,
        injury_details: formData.injury_details,
        id_document: finalIdDocument || undefined,
      });

      if (result.success) {
        await addBotMessage(
          result.message,
          <div className="mt-4 space-y-4">
            <div className="bg-emerald-600 text-white rounded-2xl p-5 shadow-2xl">
               <p className="text-[10px] uppercase font-black opacity-70">Reference Number</p>
               <h3 className="text-2xl font-black mb-6">{result.reference_number}</h3>
            </div>
            <Button variant="outline" className="w-full py-6 font-bold" onClick={() => navigate("/member/dashboard")}>
              Return to Dashboard
            </Button>
          </div>
        );
        setStep("FINISHED");
      }
    } catch (e) { await addBotMessage("Submission error."); }
  };

  const Layout = analystMode ? AnalystLayout : MemberLayout;

  return (
    <Layout>
      <div className="flex flex-col h-[calc(100vh-64px)] bg-background">
        <div className="px-6 py-3 border-b bg-card flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className={`size-2 rounded-full animate-pulse ${step === "FINISHED" ? 'bg-emerald-500' : 'bg-primary'}`}></div>
            <span className="text-xs font-bold uppercase tracking-widest text-muted-foreground">
              {analystMode ? `Filing for ${memberName !== "Member" ? memberName : "caller"}` : "Xenova Agentic AI Claims Handler"}
            </span>
          </div>
        </div>

        <ScrollArea className="flex-1 p-4 md:p-8">
          <div className="max-w-2xl mx-auto space-y-6">
            {messages.map((m, i) => (
              <div key={i} className={`flex ${m.sender === "bot" ? "justify-start" : "justify-end"} gap-3`}>
                {m.sender === "bot" && (
                  <Avatar className="size-8 bg-primary text-primary-foreground flex items-center justify-center">
                    <span className="material-symbols-outlined text-sm">psychology</span>
                  </Avatar>
                )}
                <div className={`max-w-[85%] rounded-2xl px-5 py-3 shadow-sm ${
                  m.sender === "bot" ? "bg-card text-foreground border border-primary/10" : "bg-primary text-primary-foreground shadow-primary/20"
                }`}>
                  {m.text && <p className="text-sm leading-relaxed whitespace-pre-wrap">{m.text}</p>}
                  {m.component}
                </div>
              </div>
            ))}

            {step === "ASK_PHOTOS" && (
              <div className="flex justify-start gap-3">
                <Avatar className="size-8 bg-primary text-primary-foreground flex items-center justify-center"><span className="material-symbols-outlined text-sm">psychology</span></Avatar>
                <div className="bg-card border rounded-2xl p-5 w-full max-w-[85%] shadow-lg">
                  <label className="text-[10px] text-muted-foreground mb-1 block">
                    Damage photos {analystMode ? "(optional — assessor will capture these on-site)" : ""}
                  </label>
                  <Input type="file" multiple accept="image/*" className="mb-4" onChange={(e) => setPhotos(Array.from(e.target.files || []))} />
                  {photos.length > 0 && (
                      <div className="grid grid-cols-4 gap-2 mb-4">
                          {photos.map((f, i) => <div key={i} className="aspect-square bg-muted rounded border text-[8px] p-1 flex items-center justify-center text-center overflow-hidden">{f.name}</div>)}
                      </div>
                  )}

                  <div className="border-t pt-4 mt-2 mb-2 space-y-3">
                    <p className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground">Supporting Documents (optional)</p>
                    <div>
                      <label className="text-[10px] text-muted-foreground mb-1 block">National ID / Driving Licence photo</label>
                      <Input type="file" accept="image/*" className="text-xs" onChange={(e) => setIdDocument(e.target.files?.[0] || null)} />
                      {idDocument && <p className="text-[10px] text-emerald-600 mt-1">✓ {idDocument.name}</p>}
                    </div>
                  </div>

                  <Button className="w-full bg-primary font-bold py-6" disabled={(!analystMode && photos.length === 0) || isTyping} onClick={() => handleFinalSubmit(photos)}>
                    Submit Claim
                  </Button>
                </div>
              </div>
            )}

            {step === "ASK_POLICE_ABSTRACT_UPLOAD" && (
              <div className="flex justify-start gap-3">
                <Avatar className="size-8 bg-primary text-primary-foreground flex items-center justify-center"><span className="material-symbols-outlined text-sm">psychology</span></Avatar>
                <div className="bg-card border rounded-2xl p-5 w-full max-w-[85%] shadow-lg">
                  <Input
                    type="file"
                    accept="image/*"
                    className="mb-3"
                    disabled={uploadingAbstract}
                    onChange={(e) => setPoliceAbstract(e.target.files?.[0] || null)}
                  />
                  {policeAbstract && <p className="text-[10px] text-emerald-600 mb-3">✓ {policeAbstract.name}</p>}
                  <div className="flex gap-2">
                    <Button
                      className="flex-1 bg-primary font-bold"
                      disabled={!policeAbstract || uploadingAbstract}
                      onClick={handlePoliceAbstractUpload}
                    >
                      Upload & Continue
                    </Button>
                    <Button variant="outline" disabled={uploadingAbstract} onClick={skipPoliceAbstractUpload}>
                      I don't have a photo
                    </Button>
                  </div>
                </div>
              </div>
            )}

            {step === "MEMBER_SEARCH" && (
              <div className="flex justify-start gap-3">
                <Avatar className="size-8 bg-primary text-primary-foreground flex items-center justify-center"><span className="material-symbols-outlined text-sm">support_agent</span></Avatar>
                <div className="bg-card border rounded-2xl p-5 w-full max-w-[85%] shadow-lg">
                  <div className="relative mb-3">
                    <span className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground text-[18px]">search</span>
                    <Input
                      className="pl-10"
                      placeholder="Name, phone number, or member ID..."
                      value={memberQuery}
                      onChange={(e) => runMemberSearch(e.target.value)}
                      autoFocus
                    />
                  </div>
                  {searchingMembers && (
                    <p className="text-xs text-muted-foreground italic px-1">Searching...</p>
                  )}
                  {!searchingMembers && memberQuery.trim().length >= 2 && memberResults.length === 0 && (
                    <p className="text-xs text-muted-foreground italic px-1">No members found matching "{memberQuery}".</p>
                  )}
                  <div className="space-y-2 mt-2">
                    {memberResults.map((m) => (
                      <button
                        key={m.member_id}
                        onClick={() => handleMemberSelected(m)}
                        className="w-full flex items-center gap-3 p-3 rounded-xl border border-border hover:border-primary hover:bg-primary/5 transition-colors text-left"
                      >
                        <div className="size-9 rounded-full bg-primary/10 flex items-center justify-center text-primary font-bold text-xs shrink-0">
                          {m.name?.split(" ").map((n) => n[0]).join("").slice(0, 2)}
                        </div>
                        <div className="flex-1 min-w-0">
                          <p className="text-sm font-bold text-foreground truncate">{m.name}</p>
                          <p className="text-xs text-muted-foreground">{m.member_id} • {m.phone}</p>
                        </div>
                        <span className="material-symbols-outlined text-muted-foreground text-[18px]">chevron_right</span>
                      </button>
                    ))}
                  </div>
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
              placeholder="Type here..."
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleSend()}
              disabled={["ASK_TYPE", "ASK_DATE", "ASK_DRIVER", "ASK_PHOTOS", "ASK_POLICE_ABSTRACT_UPLOAD", "MEMBER_SEARCH", "FINISHED", "PROCESSING", "FINALIZING"].includes(step)}
            />
            <Button className="size-12 rounded-full" onClick={handleSend} disabled={!inputValue}>
              <span className="material-symbols-outlined">send</span>
            </Button>
          </div>
        </div>
      </div>
    </Layout>
  );
}