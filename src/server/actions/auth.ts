"use server";

import { AuthError } from "next-auth";
import { signIn, signOut } from "@/auth";

export type LoginState = { error?: string };

/** Redirects to the org landing page, which routes further by session role
 * once signed in (we don't know ORGANIZER vs VENDOR until authorize() runs,
 * so we can't pick the final dashboard URL before calling signIn). */
export async function loginOrg(
  orgSlug: string,
  _prevState: LoginState,
  formData: FormData,
): Promise<LoginState> {
  const email = formData.get("email");
  const password = formData.get("password");
  if (typeof email !== "string" || typeof password !== "string" || !email || !password) {
    return { error: "Email and password are required" };
  }

  try {
    await signIn("org-credentials", {
      orgSlug,
      email,
      password,
      redirectTo: `/o/${orgSlug}`,
    });
  } catch (error) {
    if (error instanceof AuthError) {
      return { error: "Invalid email or password" };
    }
    throw error;
  }

  return {};
}

export async function loginAdmin(
  _prevState: LoginState,
  formData: FormData,
): Promise<LoginState> {
  const email = formData.get("email");
  const password = formData.get("password");
  if (typeof email !== "string" || typeof password !== "string" || !email || !password) {
    return { error: "Email and password are required" };
  }

  try {
    await signIn("admin-credentials", { email, password, redirectTo: "/admin" });
  } catch (error) {
    if (error instanceof AuthError) {
      return { error: "Invalid email or password" };
    }
    throw error;
  }

  return {};
}

export async function logout() {
  await signOut({ redirectTo: "/" });
}
