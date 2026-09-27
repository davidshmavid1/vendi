import { Badge, Card } from "@/components/ui";
import { answerText, formatDateTime, STATUS_LABELS } from "@/lib/applications/logic";
import { CATEGORIES, type VendorApplication, type OrganizerApplication } from "@/lib/applications/types";

/** Read-only application details: what was asked (the questions as they
 *  were at submission) and the business details reviewers saw then. */
export function ApplicationDetails({ application }: { application: VendorApplication | OrganizerApplication }) {
  const { occurrence, vendor_snapshot: v } = application;
  const zone = occurrence.timezone;
  const status = STATUS_LABELS[application.status];
  return (
    <div className="flex flex-col gap-6">
      <Card>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <p className="font-medium">{occurrence.market_name}</p>
            <p className="text-sm text-zinc-600 dark:text-zinc-400">
              {formatDateTime(occurrence.starts_at, zone)}
              {occurrence.status === "CANCELLED" && " · this date was cancelled"}
            </p>
          </div>
          <Badge tone={status.tone}>{status.label}</Badge>
        </div>
        <dl className="mt-4 grid gap-1 text-sm sm:grid-cols-[10rem_1fr]">
          <dt className="text-zinc-500">Submitted</dt>
          <dd>{formatDateTime(application.submitted_at, zone)}</dd>
          {application.decided_at && (
            <>
              <dt className="text-zinc-500">Decided</dt>
              <dd>{formatDateTime(application.decided_at, zone)}</dd>
            </>
          )}
          {application.withdrawn_at && (
            <>
              <dt className="text-zinc-500">Withdrawn</dt>
              <dd>{formatDateTime(application.withdrawn_at, zone)}</dd>
            </>
          )}
        </dl>
        {application.decision_message && (
          <div className="mt-4 rounded-md bg-zinc-100 p-3 text-sm dark:bg-zinc-800">
            <p className="text-xs font-medium text-zinc-500">Message from the organizer</p>
            <p className="mt-1 whitespace-pre-line">{application.decision_message}</p>
          </div>
        )}
        {application.status === "APPROVED" && (
          <p className="mt-4 text-xs text-zinc-500">
            Approval means the vendor is accepted for this date. It doesn&apos;t reserve a stall or take payment.
          </p>
        )}
      </Card>

      <Card>
        <h2 className="text-sm font-semibold">Business details at submission</h2>
        <dl className="mt-3 grid gap-1 text-sm sm:grid-cols-[10rem_1fr]">
          <dt className="text-zinc-500">Name</dt>
          <dd>{v.name}</dd>
          <dt className="text-zinc-500">Category</dt>
          <dd>{CATEGORIES[v.category] ?? v.category}</dd>
          <dt className="text-zinc-500">Contact email</dt>
          <dd className="break-all">{v.contact_email}</dd>
          {v.phone && (
            <>
              <dt className="text-zinc-500">Phone</dt>
              <dd>{v.phone}</dd>
            </>
          )}
          {v.website && (
            <>
              <dt className="text-zinc-500">Website</dt>
              <dd className="break-all">{v.website}</dd>
            </>
          )}
          {(v.city || v.region) && (
            <>
              <dt className="text-zinc-500">Location</dt>
              <dd>{[v.city, v.region].filter(Boolean).join(", ")}</dd>
            </>
          )}
        </dl>
        {v.description && <p className="mt-3 whitespace-pre-line text-sm">{v.description}</p>}
      </Card>

      <Card>
        <h2 className="text-sm font-semibold">Answers</h2>
        {application.questions.length === 0 ? (
          <p className="mt-2 text-sm text-zinc-500">No questions were asked.</p>
        ) : (
          <dl className="mt-3 flex flex-col gap-3 text-sm">
            {application.questions.map((q) => (
              <div key={q.id}>
                <dt className="font-medium">{q.label}</dt>
                <dd className="mt-0.5 whitespace-pre-line text-zinc-700 dark:text-zinc-300">
                  {answerText(q, application.answers[q.id])}
                </dd>
              </div>
            ))}
          </dl>
        )}
      </Card>
    </div>
  );
}
