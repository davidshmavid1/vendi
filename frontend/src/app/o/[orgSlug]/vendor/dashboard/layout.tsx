import { requireOrgContext, requireRole } from "@/lib/authz";
import { logout } from "@/server/actions/auth";

export default async function VendorDashboardLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ orgSlug: string }>;
}) {
  const { orgSlug } = await params;
  const { ctx } = await requireOrgContext(orgSlug);
  requireRole(ctx, ["VENDOR"]);

  return (
    <div className="flex flex-1 flex-col">
      <nav className="flex items-center justify-between border-b border-zinc-200 px-6 py-3 dark:border-zinc-800">
        <span className="text-sm font-medium">Vendor dashboard</span>
        <form action={logout}>
          <button className="text-sm text-zinc-500 hover:underline">Sign out</button>
        </form>
      </nav>
      <div className="flex flex-1 flex-col">{children}</div>
    </div>
  );
}
