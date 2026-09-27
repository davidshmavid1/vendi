import Link from "next/link";
import { redirect, notFound } from "next/navigation";
import { auth } from "@/auth";
import { prisma } from "@/lib/prisma";
import { Card } from "@/components/ui";

export default async function OrgLandingPage({
  params,
}: {
  params: Promise<{ orgSlug: string }>;
}) {
  const { orgSlug } = await params;
  const organization = await prisma.organization.findUnique({ where: { slug: orgSlug } });
  if (!organization) notFound();

  const session = await auth();
  if (session?.user && session.user.organizationId === organization.id) {
    if (session.user.role === "ORGANIZER") redirect(`/o/${orgSlug}/dashboard`);
    if (session.user.role === "VENDOR") redirect(`/o/${orgSlug}/vendor/dashboard`);
  }

  const openEvents = await prisma.event.findMany({
    where: { organizationId: organization.id, status: "APPLICATIONS_OPEN" },
    orderBy: { startDate: "asc" },
  });

  return (
    <div className="mx-auto w-full max-w-2xl flex-1 px-6 py-12">
      <h1 className="text-2xl font-semibold">{organization.name}</h1>
      <p className="mt-1 text-sm text-zinc-500">Vendor applications open for:</p>

      <div className="mt-4 flex flex-col gap-3">
        {openEvents.map((event) => (
          <Card key={event.id}>
            <p className="font-medium">{event.name}</p>
            <p className="text-sm text-zinc-500">
              {event.startDate.toLocaleDateString()} – {event.endDate.toLocaleDateString()}
            </p>
          </Card>
        ))}
        {openEvents.length === 0 && (
          <p className="text-sm text-zinc-500">No open events right now.</p>
        )}
      </div>

      <div className="mt-8 flex gap-4">
        <Link
          href={`/o/${orgSlug}/vendor/signup`}
          className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white dark:bg-white dark:text-zinc-900"
        >
          Apply as a vendor
        </Link>
        <Link
          href={`/o/${orgSlug}/login`}
          className="rounded-md border border-zinc-300 px-4 py-2 text-sm font-medium dark:border-zinc-700"
        >
          Sign in
        </Link>
      </div>
    </div>
  );
}
