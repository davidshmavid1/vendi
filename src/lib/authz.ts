import "server-only";
import { redirect, notFound } from "next/navigation";
import { auth } from "@/auth";
import { prisma } from "@/lib/prisma";
import { ForbiddenError } from "@/lib/errors";
import type { Role } from "@/generated/prisma/enums";
import type { OrganizationModel } from "@/generated/prisma/models";

/**
 * This module is the ONE place session data is turned into a query filter.
 * Every Server Component / Server Action / Route Handler that touches the
 * database must obtain an AuthContext (or AdminContext) from here first, and
 * merge tenantWhere()/vendorRowWhere() into every Prisma call it makes. No
 * route handler should read `auth()` or hit `prisma` directly to decide what
 * a user can see — that decision lives here so it can't drift between call
 * sites.
 */

export type AuthContext = {
  userId: string;
  role: Extract<Role, "ORGANIZER" | "VENDOR">;
  organizationId: string;
  vendorId: string | null;
};

export type AdminContext = {
  userId: string;
  role: "PLATFORM_ADMIN";
};

/**
 * Resolves the org from its slug and binds the current session to it. This
 * is the outermost tenant-isolation gate: if the signed-in user's session
 * was not issued for this exact organization, we redirect rather than let
 * any query run. PLATFORM_ADMIN never gets a context here — admins reach
 * org data only through requireAdminContext + an explicit organizationId,
 * never through the tenant-scoped path.
 */
export async function requireOrgContext(orgSlug: string): Promise<{
  ctx: AuthContext;
  organization: OrganizationModel;
}> {
  const organization = await prisma.organization.findUnique({ where: { slug: orgSlug } });
  if (!organization) notFound();

  const session = await auth();
  if (!session?.user) redirect(`/o/${orgSlug}/login`);

  if (session.user.role === "PLATFORM_ADMIN") {
    // Admins browse orgs from /admin, not via the tenant path.
    redirect(`/admin/organizations/${organization.id}`);
  }

  if (session.user.organizationId !== organization.id) {
    // Session belongs to a different tenant than the URL is asking for.
    // This is the non-negotiable check: never let it fall through.
    redirect(`/o/${orgSlug}/login`);
  }

  return {
    ctx: {
      userId: session.user.id,
      role: session.user.role as "ORGANIZER" | "VENDOR",
      organizationId: organization.id,
      vendorId: session.user.vendorId,
    },
    organization,
  };
}

export function requireRole(ctx: AuthContext, allowed: Array<AuthContext["role"]>) {
  if (!allowed.includes(ctx.role)) {
    throw new ForbiddenError(`Role ${ctx.role} cannot perform this action`);
  }
}

export function requireVendor(ctx: AuthContext): asserts ctx is AuthContext & { vendorId: string } {
  if (ctx.role !== "VENDOR" || !ctx.vendorId) {
    throw new ForbiddenError("This action requires a vendor account");
  }
}

export async function requireAdminContext(): Promise<AdminContext> {
  const session = await auth();
  if (!session?.user || session.user.role !== "PLATFORM_ADMIN") {
    redirect("/admin/login");
  }
  return { userId: session.user.id, role: "PLATFORM_ADMIN" };
}

/** Base filter every query in a tenant-scoped context must include. */
export function tenantWhere(ctx: AuthContext) {
  return { organizationId: ctx.organizationId };
}

/**
 * Row-level filter on top of the tenant filter: an ORGANIZER sees every row
 * in their org, a VENDOR only ever sees rows tied to their own vendorId.
 */
export function vendorRowWhere(ctx: AuthContext) {
  if (ctx.role === "VENDOR") {
    requireVendor(ctx);
    return { organizationId: ctx.organizationId, vendorId: ctx.vendorId };
  }
  return { organizationId: ctx.organizationId };
}
