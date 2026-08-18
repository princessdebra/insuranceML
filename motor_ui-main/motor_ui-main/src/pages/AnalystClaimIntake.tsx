import ClaimChatbot from "./ClaimChatbot";

/**
 * Claims analyst filing a claim on a member's behalf (e.g. taken over the
 * phone). Reuses the member intake wizard in full -- same AI narrative
 * checks, OCR document handling, physics-relevant structured questions --
 * with analystMode adding a member-lookup step at the front instead of
 * assuming the filer IS the member.
 */
export default function AnalystClaimIntake() {
  return <ClaimChatbot analystMode />;
}
