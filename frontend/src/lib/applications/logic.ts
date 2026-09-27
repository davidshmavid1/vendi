// Pure helpers for the application screens (no React, no fetch), so they
// can be unit tested with node:test. The server re-validates everything.

export type QuestionType = "short_text" | "long_text" | "single_choice" | "acknowledgement";

export type Question = {
  id: string;
  type: QuestionType;
  label: string;
  required: boolean;
  choices: string[];
};

export type Answers = Record<string, string | boolean>;

export type ApplicationStatus = "SUBMITTED" | "APPROVED" | "REJECTED" | "WITHDRAWN";

export type IntakeState = "open" | "not_open_yet" | "closed" | "not_accepting";

export const ANSWER_LIMITS: Record<string, number> = { short_text: 200, long_text: 2000 };

export const STATUS_LABELS: Record<ApplicationStatus, { label: string; tone: "blue" | "green" | "red" | "zinc" }> = {
  SUBMITTED: { label: "Submitted · awaiting review", tone: "blue" },
  APPROVED: { label: "Approved", tone: "green" },
  REJECTED: { label: "Not accepted", tone: "red" },
  WITHDRAWN: { label: "Withdrawn", tone: "zinc" },
};

/** Only same-site relative paths may be used as a post-login destination. */
export function safeNext(next: string | null | undefined, fallback = "/markets"): string {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\")) return fallback;
  return next;
}

export function loginHref(next: string): string {
  return `/account/login?next=${encodeURIComponent(safeNext(next))}`;
}

/** Client-side check mirroring the server's rules, for immediate feedback. */
export function checkAnswers(questions: Question[], answers: Answers): Record<string, string> {
  const problems: Record<string, string> = {};
  for (const q of questions) {
    const value = answers[q.id];
    if (q.type === "acknowledgement") {
      if (q.required && value !== true) problems[q.id] = "Please check this box.";
      continue;
    }
    const text = typeof value === "string" ? value.trim() : "";
    if (!text) {
      if (q.required) problems[q.id] = "This question is required.";
      continue;
    }
    if (q.type === "single_choice" && !q.choices.includes(text)) {
      problems[q.id] = "Choose one of the listed options.";
    } else if (ANSWER_LIMITS[q.type] && text.length > ANSWER_LIMITS[q.type]) {
      problems[q.id] = `Must be at most ${ANSWER_LIMITS[q.type]} characters.`;
    }
  }
  return problems;
}

/** The request body's answers: trimmed text, unchecked boxes and blanks left out. */
export function answersPayload(questions: Question[], answers: Answers): Answers {
  const out: Answers = {};
  for (const q of questions) {
    const value = answers[q.id];
    if (q.type === "acknowledgement") {
      if (value === true) out[q.id] = true;
    } else if (typeof value === "string" && value.trim()) {
      out[q.id] = value.trim();
    }
  }
  return out;
}

/** Field errors from an ``answers_invalid`` response. */
export function answerErrors(details: Record<string, unknown>[] | null | undefined): Record<string, string> {
  const out: Record<string, string> = {};
  for (const d of details ?? []) {
    if (typeof d.question_id === "string" && typeof d.message === "string") out[d.question_id] = d.message;
  }
  return out;
}

export function intakeMessage(state: IntakeState, opensAt: string | null, zone: string): string {
  switch (state) {
    case "open":
      return "Applications are open.";
    case "not_open_yet":
      return opensAt ? `Applications open ${formatDateTime(opensAt, zone)}.` : "Applications aren't open yet.";
    case "closed":
      return "Applications for this date are closed.";
    default:
      return "This date isn't accepting applications on Vendi.";
  }
}

export function formatDateTime(iso: string, zone: string): string {
  return new Intl.DateTimeFormat("en-US", {
    timeZone: zone,
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  }).format(new Date(iso));
}

/** One answer as display text. */
export function answerText(question: Question, value: string | boolean | undefined | null): string {
  if (question.type === "acknowledgement") return value === true ? "Yes (checked)" : "Not checked";
  return typeof value === "string" && value ? value : "No answer";
}
