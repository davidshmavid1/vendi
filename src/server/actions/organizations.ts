"use server";

import { z } from "zod";
import { AuthError } from "next-auth";
import { prisma } from "@/lib/prisma";
import { hashPassword } from "@/lib/password";
import { signIn } from "@/auth";

const signupSchema = z.object({
  organizationName: z.string().min(2).max(100),
  slug: z
    .string()
    .min(3)
    .max(50)
    .regex(/^[a-z0-9-]+$/, "Use lowercase letters, numbers, and hyphens only"),
  name: z.string().min(1).max(100),
  email: z.string().email(),
  password: z.string().min(8).max(100),
});

export type SignupOrganizerState = { error?: string };

/** Organizer self-serve signup: creates the Organization and its first
 * ORGANIZER user, then signs them straight in. No platform admin involved. */
export async function signupOrganizer(
  _prevState: SignupOrganizerState,
  formData: FormData,
): Promise<SignupOrganizerState> {
  const parsed = signupSchema.safeParse({
    organizationName: formData.get("organizationName"),
    slug: formData.get("slug"),
    name: formData.get("name"),
    email: formData.get("email"),
    password: formData.get("password"),
  });
  if (!parsed.success) {
    return { error: parsed.error.issues[0]?.message ?? "Invalid input" };
  }

  const { organizationName, slug, name, email, password } = parsed.data;
  const normalizedEmail = email.toLowerCase().trim();

  const existingOrg = await prisma.organization.findUnique({ where: { slug } });
  if (existingOrg) {
    return { error: "That organization URL is already taken" };
  }

  const hashedPassword = await hashPassword(password);

  await prisma.organization.create({
    data: {
      name: organizationName,
      slug,
      users: {
        create: { name, email: normalizedEmail, hashedPassword, role: "ORGANIZER" },
      },
    },
  });

  try {
    await signIn("org-credentials", {
      orgSlug: slug,
      email: normalizedEmail,
      password,
      redirectTo: `/o/${slug}/dashboard`,
    });
  } catch (error) {
    if (error instanceof AuthError) {
      return { error: "Account created, but sign-in failed — please log in." };
    }
    throw error;
  }

  return {};
}
