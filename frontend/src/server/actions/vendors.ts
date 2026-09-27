"use server";

import { z } from "zod";
import { AuthError } from "next-auth";
import { prisma } from "@/lib/prisma";
import { hashPassword } from "@/lib/password";
import { signIn } from "@/auth";

const vendorSignupSchema = z.object({
  orgSlug: z.string().min(1),
  businessName: z.string().min(2).max(150),
  name: z.string().min(1).max(100),
  email: z.string().email(),
  password: z.string().min(8).max(100),
});

export type VendorSignupState = { error?: string };

/** Vendor self-serve signup within a single organizer's market. */
export async function signupVendor(
  _prevState: VendorSignupState,
  formData: FormData,
): Promise<VendorSignupState> {
  const parsed = vendorSignupSchema.safeParse({
    orgSlug: formData.get("orgSlug"),
    businessName: formData.get("businessName"),
    name: formData.get("name"),
    email: formData.get("email"),
    password: formData.get("password"),
  });
  if (!parsed.success) {
    return { error: parsed.error.issues[0]?.message ?? "Invalid input" };
  }

  const { orgSlug, businessName, name, email, password } = parsed.data;
  const normalizedEmail = email.toLowerCase().trim();

  const organization = await prisma.organization.findUnique({ where: { slug: orgSlug } });
  if (!organization) {
    return { error: "Market not found" };
  }

  const existingUser = await prisma.user.findUnique({
    where: { organizationId_email: { organizationId: organization.id, email: normalizedEmail } },
  });
  if (existingUser) {
    return { error: "An account with this email already exists for this market" };
  }

  const hashedPassword = await hashPassword(password);

  await prisma.user.create({
    data: {
      organizationId: organization.id,
      email: normalizedEmail,
      name,
      hashedPassword,
      role: "VENDOR",
      vendor: { create: { organizationId: organization.id, businessName } },
    },
  });

  try {
    await signIn("org-credentials", {
      orgSlug,
      email: normalizedEmail,
      password,
      redirectTo: `/o/${orgSlug}/vendor/dashboard`,
    });
  } catch (error) {
    if (error instanceof AuthError) {
      return { error: "Account created, but sign-in failed — please log in." };
    }
    throw error;
  }

  return {};
}
