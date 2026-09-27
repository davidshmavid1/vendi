"use client";

import { useActionState } from "react";
import Link from "next/link";
import { signupOrganizer, type SignupOrganizerState } from "@/server/actions/organizations";
import { Card, Field, SubmitButton, FormError } from "@/components/ui";

const initialState: SignupOrganizerState = {};

export default function SignupPage() {
  const [state, formAction, pending] = useActionState(signupOrganizer, initialState);

  return (
    <div className="mx-auto flex w-full max-w-md flex-1 flex-col justify-center px-6 py-16">
      <h1 className="text-2xl font-semibold">Start your market</h1>
      <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
        Create your organizer account — no approval needed.
      </p>
      <Card className="mt-6">
        <form action={formAction} className="flex flex-col gap-4">
          <Field label="Market name" name="organizationName" placeholder="Maple Street Market" />
          <Field label="Market URL" name="slug" placeholder="maple-street" />
          <Field label="Your name" name="name" />
          <Field label="Email" name="email" type="email" />
          <Field label="Password" name="password" type="password" />
          <FormError error={state.error} />
          <SubmitButton pending={pending}>Create market</SubmitButton>
        </form>
      </Card>
      <p className="mt-4 text-center text-sm text-zinc-600 dark:text-zinc-400">
        Already have a market? Use your market&apos;s own login link.
      </p>
      <Link href="/" className="mt-2 text-center text-sm text-zinc-500 hover:underline">
        Back home
      </Link>
    </div>
  );
}
