import Link from "next/link";
import { requireAdminContext } from "@/lib/authz";
import { prisma } from "@/lib/prisma";
import { Card, Badge } from "@/components/ui";
import { logout } from "@/server/actions/auth";

export default async function AdminHomePage() {
  await requireAdminContext();

  const organizations = await prisma.organization.findMany({
    orderBy: { createdAt: "desc" },
    include: {
      _count: { select: { events: true, vendors: true, bookings: true } },
    },
  });

  return (
    <div className="mx-auto w-full max-w-4xl flex-1 px-6 py-12">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Organizations</h1>
        <form action={logout}>
          <button className="text-sm text-zinc-500 hover:underline">Sign out</button>
        </form>
      </div>
      <div className="mt-6 flex flex-col gap-3">
        {organizations.map((org) => (
          <Link key={org.id} href={`/admin/organizations/${org.id}`}>
            <Card className="flex items-center justify-between transition-colors hover:border-zinc-400 dark:hover:border-zinc-600">
              <div>
                <p className="font-medium">{org.name}</p>
                <p className="text-sm text-zinc-500">
                  /{org.slug} · {org._count.events} events · {org._count.vendors} vendors ·{" "}
                  {org._count.bookings} bookings
                </p>
              </div>
              <div className="flex items-center gap-2">
                <Badge tone={org.subscriptionStatus === "ACTIVE" ? "green" : "yellow"}>
                  {org.subscriptionStatus}
                </Badge>
                <Badge tone={org.stripeOnboardingComplete ? "green" : "yellow"}>
                  {org.stripeOnboardingComplete ? "Stripe connected" : "Stripe pending"}
                </Badge>
              </div>
            </Card>
          </Link>
        ))}
        {organizations.length === 0 && <p className="text-zinc-500">No organizations yet.</p>}
      </div>
    </div>
  );
}
