import NextAuth from "next-auth";
import Credentials from "next-auth/providers/credentials";
import bcrypt from "bcryptjs";
import { prisma } from "@/lib/prisma";
import { authConfig } from "@/auth.config";
import type { Role } from "@/generated/prisma/enums";

declare module "next-auth" {
  interface User {
    role: Role;
    organizationId: string | null;
    organizationSlug: string | null;
    vendorId: string | null;
  }
  interface Session {
    user: {
      id: string;
      email: string;
      name?: string | null;
      role: Role;
      organizationId: string | null;
      organizationSlug: string | null;
      vendorId: string | null;
    };
  }
}

declare module "@auth/core/jwt" {
  interface JWT {
    id: string;
    role: Role;
    organizationId: string | null;
    organizationSlug: string | null;
    vendorId: string | null;
  }
}

export const { handlers, auth, signIn, signOut } = NextAuth({
  ...authConfig,
  providers: [
    // Organizer / vendor sign-in: scoped to a single organization, resolved
    // from the org slug in the login form (mirrors the /o/[orgSlug] route
    // structure so tenant context is established before authentication).
    Credentials({
      id: "org-credentials",
      name: "Organization sign in",
      credentials: {
        orgSlug: { label: "Organization", type: "text" },
        email: { label: "Email", type: "email" },
        password: { label: "Password", type: "password" },
      },
      async authorize(creds) {
        const orgSlug = typeof creds?.orgSlug === "string" ? creds.orgSlug : undefined;
        const email = typeof creds?.email === "string" ? creds.email.toLowerCase().trim() : undefined;
        const password = typeof creds?.password === "string" ? creds.password : undefined;
        if (!orgSlug || !email || !password) return null;

        const organization = await prisma.organization.findUnique({ where: { slug: orgSlug } });
        if (!organization) return null;

        const user = await prisma.user.findUnique({
          where: { organizationId_email: { organizationId: organization.id, email } },
          include: { vendor: true },
        });
        if (!user?.hashedPassword) return null;
        if (user.role === "PLATFORM_ADMIN") return null;

        const valid = await bcrypt.compare(password, user.hashedPassword);
        if (!valid) return null;

        return {
          id: user.id,
          email: user.email,
          name: user.name,
          role: user.role,
          organizationId: user.organizationId,
          organizationSlug: organization.slug,
          vendorId: user.vendor?.id ?? null,
        };
      },
    }),
    // Platform admin sign-in: intentionally cross-org, no slug involved.
    Credentials({
      id: "admin-credentials",
      name: "Platform admin sign in",
      credentials: {
        email: { label: "Email", type: "email" },
        password: { label: "Password", type: "password" },
      },
      async authorize(creds) {
        const email = typeof creds?.email === "string" ? creds.email.toLowerCase().trim() : undefined;
        const password = typeof creds?.password === "string" ? creds.password : undefined;
        if (!email || !password) return null;

        const user = await prisma.user.findFirst({
          where: { email, role: "PLATFORM_ADMIN", organizationId: null },
        });
        if (!user?.hashedPassword) return null;

        const valid = await bcrypt.compare(password, user.hashedPassword);
        if (!valid) return null;

        return {
          id: user.id,
          email: user.email,
          name: user.name,
          role: user.role,
          organizationId: null,
          organizationSlug: null,
          vendorId: null,
        };
      },
    }),
  ],
});
