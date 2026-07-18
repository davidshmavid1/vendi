import Link from "next/link";
import { requireOrgContext, requireRole } from "@/lib/authz";
import { logout } from "@/server/actions/auth";

export default async function OrganizerDashboardLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ orgSlug: string }>;
}) {
  const { orgSlug } = await params;
  const { ctx } = await requireOrgContext(orgSlug);
  requireRole(ctx, ["ORGANIZER"]);

  const base = `/o/${orgSlug}/dashboard`;

  return (
    <div className="flex flex-1 flex-col">
      <nav className="flex items-center justify-between border-b border-zinc-200 px-6 py-3 dark:border-zinc-800">
        <div className="flex gap-5 text-sm font-medium">
          <Link href={base}>Overview</Link>
          <Link href={`${base}/events`}>Events</Link>
          <Link href={`${base}/settings/stripe`}>Stripe</Link>
        </div>
        <form action={logout}>
          <button className="text-sm text-zinc-500 hover:underline">Sign out</button>
        </form>
      </nav>
      <div className="flex flex-1 flex-col">{children}</div>
    </div>
  );
}
