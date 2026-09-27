"use client";

import Link from "next/link";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Card } from "@/components/ui";
import { AccountGate, buttonClass, DjangoPage, inputClass, Notice } from "@/components/DjangoPage";
import { apiGet, apiSend, type ApiError } from "@/lib/django/client";
import { useAccount } from "@/lib/django/useAccount";
import {
  ANSWER_LIMITS,
  answerErrors,
  answersPayload,
  checkAnswers,
  formatDateTime,
  intakeMessage,
  type Answers,
  type Question,
} from "@/lib/applications/logic";
import type { MyBusiness, Page, PublicIntake, VendorApplication } from "@/lib/applications/types";
import { formatOccurrence } from "@/lib/discovery/logic";

type Load<T> = { status: "loading" } | { status: "ready"; data: T } | { status: "failed"; error: ApiError; httpStatus: number };

export function ApplyClient({ marketId, occurrenceId }: { marketId: number; occurrenceId: number }) {
  const { state: account, reload: reloadAccount } = useAccount();
  const [intake, setIntake] = useState<Load<PublicIntake>>({ status: "loading" });
  const [businesses, setBusinesses] = useState<Load<MyBusiness[]>>({ status: "loading" });
  // Existing application per owned business, for this date.
  const [existing, setExisting] = useState<Record<number, VendorApplication | null>>({});
  const [businessId, setBusinessId] = useState<number | null>(null);
  const [answers, setAnswers] = useState<Answers>({});
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<ApiError | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState<VendorApplication | null>(null);

  const loadIntake = useCallback(async () => {
    setIntake({ status: "loading" });
    const result = await apiGet<PublicIntake>(`/public/occurrences/${occurrenceId}/application`);
    if (result.ok && result.data.occurrence.market_id !== marketId) {
      setIntake({ status: "failed", error: { code: "not_found", message: "Event date not found." }, httpStatus: 404 });
    } else {
      setIntake(result.ok ? { status: "ready", data: result.data } : { status: "failed", error: result.error, httpStatus: result.status });
    }
  }, [marketId, occurrenceId]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial load
    void loadIntake();
  }, [loadIntake]);

  const signedIn = account.status === "signed_in";
  useEffect(() => {
    if (!signedIn) return;
    let cancelled = false;
    (async () => {
      const result = await apiGet<Page<MyBusiness>>("/vendors?limit=100");
      if (cancelled) return;
      if (!result.ok) {
        setBusinesses({ status: "failed", error: result.error, httpStatus: result.status });
        return;
      }
      const owned = result.data.items.filter((m) => m.role === "OWNER");
      const found: Record<number, VendorApplication | null> = {};
      await Promise.all(
        owned.map(async (m) => {
          const apps = await apiGet<Page<VendorApplication>>(
            `/vendors/${m.business.id}/applications?occurrence_id=${occurrenceId}`,
          );
          found[m.business.id] = apps.ok ? (apps.data.items[0] ?? null) : null;
        }),
      );
      if (cancelled) return;
      setExisting(found);
      setBusinesses({ status: "ready", data: result.data.items });
      if (owned.length === 1) setBusinessId(owned[0].business.id);
    })();
    return () => {
      cancelled = true;
    };
  }, [signedIn, occurrenceId]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting || intake.status !== "ready" || businessId === null) return;
    const questions = intake.data.questions;
    const problems = checkAnswers(questions, answers);
    setFieldErrors(problems);
    setFormError(null);
    if (Object.keys(problems).length > 0) {
      setFormError({ code: "answers_invalid", message: "Some answers need attention." });
      return;
    }
    setSubmitting(true);
    const result = await apiSend<VendorApplication>("POST", `/vendors/${businessId}/applications`, {
      occurrence_id: occurrenceId,
      questions_version: intake.data.questions_version,
      answers: answersPayload(questions, answers),
    });
    setSubmitting(false);
    if (result.ok) {
      setSubmitted(result.data);
      setExisting((e) => ({ ...e, [businessId]: result.data }));
      return;
    }
    if (result.error.code === "answers_invalid") setFieldErrors(answerErrors(result.error.details));
    setFormError(result.error);
  }

  const occurrence = intake.status === "ready" ? intake.data.occurrence : null;

  return (
    <DjangoPage account={account}>
      <Link href={`/markets/${marketId}`} className="text-sm text-zinc-600 underline dark:text-zinc-400">
        ← Back to the market
      </Link>
      {intake.status === "loading" && <p className="mt-6 text-sm text-zinc-500">Loading…</p>}
      {intake.status === "failed" && (
        <div className="mt-6">
          <Notice tone="red">
            {intake.httpStatus === 404 ? "This market date isn't available." : intake.error.message}{" "}
            {intake.httpStatus !== 404 && (
              <button type="button" className="underline" onClick={loadIntake}>
                Try again
              </button>
            )}
          </Notice>
        </div>
      )}
      {intake.status === "ready" && occurrence && (
        <>
          <h1 className="mt-4 text-2xl font-semibold tracking-tight">Apply to {intake.data.market_name}</h1>
          <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
            {formatOccurrence(occurrence)} · Organized by {intake.data.organizer_name}
          </p>

          {submitted ? (
            <div className="mt-6">
              <Notice tone="green">
                <p className="font-medium">Application submitted.</p>
                <p className="mt-1">
                  The organizer will review it. Approval confirms you&apos;re accepted for this date; it doesn&apos;t
                  reserve a stall or take payment.
                </p>
                <Link
                  href={`/vendor/businesses/${submitted.vendor_business_id}/applications/${submitted.id}`}
                  className="mt-2 inline-block underline"
                >
                  View your application
                </Link>
              </Notice>
            </div>
          ) : intake.data.state !== "open" ? (
            <div className="mt-6">
              <Notice tone="amber">{intakeMessage(intake.data.state, intake.data.opens_at, occurrence.timezone)}</Notice>
            </div>
          ) : (
            <div className="mt-6">
              <AccountGate account={account} retry={reloadAccount}>
                {account.status === "signed_in" && !account.account.email_verified ? (
                  <Notice tone="amber">Confirm your email address before applying.</Notice>
                ) : businesses.status === "loading" ? (
                  <p className="text-sm text-zinc-500">Loading your businesses…</p>
                ) : businesses.status === "failed" ? (
                  <Notice tone="red">{businesses.error.message}</Notice>
                ) : (
                  <ApplicationForm
                    intake={intake.data}
                    businesses={businesses.data}
                    existing={existing}
                    businessId={businessId}
                    onBusiness={(id) => {
                      setBusinessId(id);
                      setFormError(null);
                    }}
                    answers={answers}
                    onAnswer={(id, value) => setAnswers((a) => ({ ...a, [id]: value }))}
                    fieldErrors={fieldErrors}
                    formError={formError}
                    submitting={submitting}
                    onSubmit={submit}
                    onReload={() => {
                      setFormError(null);
                      setFieldErrors({});
                      void loadIntake();
                    }}
                    returnTo={`/markets/${marketId}/dates/${occurrenceId}/apply`}
                  />
                )}
              </AccountGate>
            </div>
          )}
        </>
      )}
    </DjangoPage>
  );
}

function ApplicationForm(props: {
  intake: PublicIntake;
  businesses: MyBusiness[];
  existing: Record<number, VendorApplication | null>;
  businessId: number | null;
  onBusiness: (id: number) => void;
  answers: Answers;
  onAnswer: (id: string, value: string | boolean) => void;
  fieldErrors: Record<string, string>;
  formError: ApiError | null;
  submitting: boolean;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onReload: () => void;
  returnTo: string;
}) {
  const { intake, businesses, existing, businessId, formError } = props;
  const owned = businesses.filter((m) => m.role === "OWNER");

  if (businesses.length === 0) {
    return (
      <Notice>
        You need a vendor business profile to apply.{" "}
        <Link href={`/vendor/businesses/new?next=${encodeURIComponent(props.returnTo)}`} className="font-medium underline">
          Create your business
        </Link>
        . It&apos;s your own profile and isn&apos;t linked to this organizer.
      </Notice>
    );
  }
  if (owned.length === 0) {
    return (
      <Notice tone="amber">
        Only a business&apos;s owner can apply. You&apos;re a member of{" "}
        {businesses.map((m) => m.business.name).join(", ")}; ask its owner to apply, or{" "}
        <Link href={`/vendor/businesses/new?next=${encodeURIComponent(props.returnTo)}`} className="underline">
          create your own business
        </Link>
        .
      </Notice>
    );
  }

  const already = businessId !== null ? existing[businessId] : null;
  const closesAt = intake.closes_at ?? intake.occurrence.starts_at;

  return (
    <form onSubmit={props.onSubmit} className="flex flex-col gap-6" noValidate>
      <Card>
        <fieldset>
          <legend className="text-sm font-semibold">Apply as</legend>
          <div className="mt-3 flex flex-col gap-2">
            {owned.map((m) => (
              <label key={m.business.id} className="flex items-center gap-2 text-sm">
                <input
                  type="radio"
                  name="business"
                  value={m.business.id}
                  checked={businessId === m.business.id}
                  onChange={() => props.onBusiness(m.business.id)}
                />
                <span>{m.business.name}</span>
                {existing[m.business.id] && <span className="text-xs text-zinc-500">(already applied)</span>}
              </label>
            ))}
          </div>
        </fieldset>
      </Card>

      {already ? (
        <Notice tone="amber">
          {already.vendor_snapshot.name} has already applied to this date.{" "}
          <Link href={`/vendor/businesses/${already.vendor_business_id}/applications/${already.id}`} className="underline">
            View that application
          </Link>
        </Notice>
      ) : (
        <>
          {intake.instructions && (
            <Card>
              <h2 className="text-sm font-semibold">From the organizer</h2>
              <p className="mt-2 whitespace-pre-line text-sm">{intake.instructions}</p>
            </Card>
          )}
          {intake.questions.length > 0 && (
            <Card>
              <div className="flex flex-col gap-5">
                {intake.questions.map((q) => (
                  <QuestionField
                    key={q.id}
                    question={q}
                    value={props.answers[q.id]}
                    error={props.fieldErrors[q.id]}
                    onChange={(v) => props.onAnswer(q.id, v)}
                  />
                ))}
              </div>
            </Card>
          )}
          <p className="text-xs text-zinc-500">
            Applications close {formatDateTime(closesAt, intake.occurrence.timezone)}. Approval confirms you&apos;re
            accepted for this date; it doesn&apos;t reserve a stall or take payment.
          </p>
          {formError && <FormProblem error={formError} onReload={props.onReload} />}
          <div>
            <button type="submit" disabled={props.submitting || businessId === null} className={buttonClass}>
              {props.submitting ? "Submitting…" : "Submit application"}
            </button>
            {businessId === null && <p className="mt-2 text-xs text-zinc-500">Choose a business to apply as.</p>}
          </div>
        </>
      )}
    </form>
  );
}

function FormProblem({ error, onReload }: { error: ApiError; onReload: () => void }) {
  const message =
    error.code === "participation_restricted"
      ? "You can't apply to this organizer's markets right now."
      : error.code === "applications_closed"
        ? "Applications for this date just closed."
        : error.message;
  return (
    <Notice tone="red">
      {message}
      {(error.code === "questions_changed" || error.code === "applications_closed") && (
        <>
          {" "}
          <button type="button" className="underline" onClick={onReload}>
            Reload the form
          </button>
        </>
      )}
      {error.code === "application_exists" && typeof error.details?.[0]?.application_id === "number" && (
        <> It&apos;s listed under My applications.</>
      )}
    </Notice>
  );
}

function QuestionField({
  question,
  value,
  error,
  onChange,
}: {
  question: Question;
  value: string | boolean | undefined;
  error?: string;
  onChange: (value: string | boolean) => void;
}) {
  const id = `q-${question.id}`;
  const describedBy = error ? `${id}-error` : undefined;
  const label = (
    <>
      {question.label}
      {question.required ? <span className="text-red-600"> *</span> : <span className="text-zinc-500"> (optional)</span>}
    </>
  );
  const errorText = error && (
    <p id={`${id}-error`} className="text-xs text-red-600 dark:text-red-400">
      {error}
    </p>
  );

  if (question.type === "acknowledgement") {
    return (
      <div className="flex flex-col gap-1">
        <label className="flex items-start gap-2 text-sm">
          <input
            id={id}
            type="checkbox"
            checked={value === true}
            onChange={(e) => onChange(e.target.checked)}
            aria-invalid={Boolean(error)}
            aria-describedby={describedBy}
            className="mt-0.5"
          />
          <span>{label}</span>
        </label>
        {errorText}
      </div>
    );
  }
  if (question.type === "single_choice") {
    return (
      <fieldset className="flex flex-col gap-1" aria-invalid={Boolean(error)} aria-describedby={describedBy}>
        <legend className="text-sm font-medium">{label}</legend>
        <div className="mt-1 flex flex-wrap gap-4">
          {question.choices.map((choice) => (
            <label key={choice} className="flex items-center gap-2 text-sm">
              <input type="radio" name={id} value={choice} checked={value === choice} onChange={() => onChange(choice)} />
              {choice}
            </label>
          ))}
        </div>
        {errorText}
      </fieldset>
    );
  }
  const limit = ANSWER_LIMITS[question.type];
  const text = typeof value === "string" ? value : "";
  const common = {
    id,
    value: text,
    maxLength: limit,
    "aria-invalid": Boolean(error),
    "aria-describedby": describedBy,
    className: inputClass,
  };
  return (
    <div className="flex flex-col gap-1 text-sm">
      <label htmlFor={id} className="font-medium">
        {label}
      </label>
      {question.type === "long_text" ? (
        <textarea {...common} rows={4} onChange={(e) => onChange(e.target.value)} />
      ) : (
        <input {...common} type="text" onChange={(e) => onChange(e.target.value)} />
      )}
      <span className="text-xs text-zinc-500">
        {text.length}/{limit}
      </span>
      {errorText}
    </div>
  );
}
