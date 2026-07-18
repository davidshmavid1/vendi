import type { NextAuthConfig } from "next-auth";
import { NextResponse } from "next/server";

// Edge-safe half of the Auth.js config: no Prisma, no bcrypt. Middleware runs
// on the Edge runtime and can only see the JWT — it does coarse route/role
// gating here. Precise organizationId + row-level scoping happens in
// src/lib/authz.ts, which runs in Server Components/Actions (Node runtime)
// and always hits the database.
export const authConfig = {
  pages: {
    signIn: "/login",
  },
  session: { strategy: "jwt" },
  providers: [],
  callbacks: {
    // These two are pure field-copying (no Prisma/bcrypt) so they're safe to
    // run on the Edge runtime. They live here — not just in src/auth.ts — so
    // middleware's `auth.user` actually has role/organizationSlug/vendorId
    // on it; otherwise middleware would fall back to Auth.js's default
    // session shape (no custom fields) and every authorized() check below
    // would see `undefined` and bounce a just-signed-in user back to login.
    jwt({ token, user }) {
      if (user) {
        token.id = user.id!;
        token.role = user.role;
        token.organizationId = user.organizationId;
        token.organizationSlug = user.organizationSlug;
        token.vendorId = user.vendorId;
      }
      return token;
    },
    session({ session, token }) {
      session.user.id = token.id;
      session.user.role = token.role;
      session.user.organizationId = token.organizationId;
      session.user.organizationSlug = token.organizationSlug;
      session.user.vendorId = token.vendorId;
      return session;
    },
    authorized({ auth, request }) {
      const { pathname } = request.nextUrl;
      const role = auth?.user?.role;
      const organizationSlug = auth?.user?.organizationSlug;

      // Only these sub-areas require a signed-in session; the org landing
      // page, org-scoped login, and vendor signup stay public.
      const organizerArea = pathname.match(/^\/o\/([^/]+)\/dashboard(\/.*)?$/);
      const vendorArea = pathname.match(/^\/o\/([^/]+)\/vendor\/(dashboard|events|book)(\/.*)?$/);

      if (organizerArea) {
        const slug = organizerArea[1];
        if (!auth?.user || role !== "ORGANIZER" || organizationSlug !== slug) {
          return NextResponse.redirect(new URL(`/o/${slug}/login`, request.nextUrl));
        }
        return true;
      }

      if (vendorArea) {
        const slug = vendorArea[1];
        if (!auth?.user || role !== "VENDOR" || organizationSlug !== slug) {
          return NextResponse.redirect(new URL(`/o/${slug}/login`, request.nextUrl));
        }
        return true;
      }

      if (pathname.startsWith("/admin") && pathname !== "/admin/login") {
        if (role !== "PLATFORM_ADMIN") {
          return NextResponse.redirect(new URL("/admin/login", request.nextUrl));
        }
        return true;
      }

      return true;
    },
  },
} satisfies NextAuthConfig;
